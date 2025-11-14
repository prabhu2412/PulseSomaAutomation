""" 

Asynchronous launcher / supervisor for the SOMA load-test pipeline. 

 

• Spawns scripts/soma_bash.sh with user-supplied CLI args 

• Watches for exit code and captures stdout/stderr 

• Starts a tail-F task for every *.log file that appears in runner/logs/ 

• Pushes “stage” and “log” events to the Socket.IO server 

""" 

 

import asyncio 

import uuid 

import os 

from pathlib import Path 

from typing import Dict, List, Optional, Any

import datetime 

import time

import psutil

import shutil

from .email_helper import send_alert_async

import importlib

from . import email_helper

import signal #----

 

# --------------------------------------------------------------------------- # 

# Configuration constants – tweak only if your paths change 

# --------------------------------------------------------------------------- # 

APP_ROOT = Path(__file__).resolve().parent.parent 

SCRIPTS_DIR = APP_ROOT / "scripts" 

RUNNER_DIR = APP_ROOT / "runner" 

LOGS_DIR = RUNNER_DIR / "logs" 

DATAFILES_DIR = RUNNER_DIR / "datafiles" 

RESULTS_DIR = RUNNER_DIR / "results" 

 

BASH_DRIVER = SCRIPTS_DIR / "soma_bash.sh" 

 

# --------------------------------------------------------------------------- # 

# In-memory structures 

# --------------------------------------------------------------------------- # 

class RunStatus: 

    """Holds all state for one running (or finished) pipeline.""" 

    def __init__(self, cmd: List[str]): 

        self.id: str = datetime.datetime.utcnow().strftime("%Y%m%d-%H%M%S-") + uuid.uuid4().hex[:6] 

        self.cmd = cmd 

        self.process: Optional[asyncio.subprocess.Process] = None 

        self.start_ts = datetime.datetime.utcnow().isoformat() + "Z" 

        self.end_ts: Optional[str] = None 

        self.return_code: Optional[int] = None 

        self.tail_tasks: List[asyncio.Task] = [] 

        self.stage: str = "INIT" # updated by bash output parser 

        self.error: Optional[str] = None 

        self.paused: bool = False #--

        self.pending_stage: str | None = None #--

        self.log_dir: Path | None = None

        self.pgid: int | None = None

 

runs: Dict[str, RunStatus] = {} # run_id -> RunStatus 

sio = None # injected from main.py 

 

 

# --------------------------------------------------------------------------- # 

# Helpers 

# --------------------------------------------------------------------------- # 

async def _tail_file(run: RunStatus, logfile: Path) -> None: 

    """ 
    Spawn `tail -F <logfile>` and relay each new line to the browser. 
    """ 

    proc = await asyncio.create_subprocess_exec( 

        "tail", "-F", str(logfile), 

        stdout=asyncio.subprocess.PIPE, 

        stderr=asyncio.subprocess.PIPE, 

    ) 

    try: 

        assert proc.stdout is not None 

        while True: 

            line = await proc.stdout.readline() 

            if not line: 

                break 

            await sio.emit( 

                "log", 

                { 

                    "run_id": run.id, 

                    "file": logfile.name, 

                    "ts": datetime.datetime.utcnow().timestamp(), 

                    "line": line.decode(errors="replace").rstrip("\n"), 

                }, 

                room=run.id, 

            ) 

    except asyncio.CancelledError: 

        proc.kill() 

        raise 

 

 

async def _watch_new_logs(run: RunStatus) -> None: 

    """ 

    Poll LOGS_DIR for new *.log files and start tail tasks as they appear. 

    """ 

    seen: set[str] = set() 
    search_dirs: list[Path] = [run.log_dir] if run.log_dir else []
    search_dirs.append(LOGS_DIR)
    try: 

        while run.process and run.process.returncode is None: 

            for d in search_dirs:
                if not d.exists():
                    continue
                for lf in d.glob("*.log"):
                    if lf.name not in seen: 

                        seen.add(lf.name) 

                        task = asyncio.create_task(_tail_file(run, lf)) 

                        run.tail_tasks.append(task) 

            await asyncio.sleep(1) 

    finally: 

        # cancel any remaining tailers 

        for t in run.tail_tasks: 

            t.cancel() 

 



 

async def _read_driver_stdout(run: RunStatus) -> None: 

    """ 
    Reads the Bash driver stdout line-by-line. 

    If a line indicates a stage start, emit a stage event. 

    """ 

    assert run.process.stdout is not None 

    while True: 

        line = await run.process.stdout.readline() 

        if not line: 

            break 

        decoded = line.decode(errors="replace").rstrip("\n") 

        

        # not so good stage detection: look for '=== <STAGE>' markers 

        if decoded.startswith("=== "): 

            # Expected format: "=== DATAPREP1 START ===" 

            parts = decoded.strip("= ").split() 

            if len(parts) >= 1: 

                stage_name = parts[0] 

                run.stage = stage_name 

                await sio.emit( 
                    "stage", 
                    {"run_id": run.id, "stage": stage_name}, 
                    room=run.id, 

                ) 

                if stage_name not in ("CONFIG", "COMPLETE"):
                    _pause_process(run)
                    run.pending_stage = stage_name
                    await sio.emit( 
                        "paused", 
                        {"run_id": run.id, "stage": stage_name}, 
                        room=run.id, 
                    ) 
                    await send_alert_async( 
                        subject=f"SOMA Pipeline awaiting approval - {stage_name}",
                        body=f"run is currently paused and awaiting approval to next stage"
                    )


        # also pipe stdout to browser as a generic log line 
        await sio.emit( 

            "log", 

            { 

                "run_id": run.id, 

                "file": "driver", # virtual file for driver stdout 

                "ts": datetime.datetime.utcnow().timestamp(), 

                "line": decoded, 

            }, 

            room=run.id, 

        ) 

 


# Public API (called by main.py) 


async def start_run(args: List[str]) -> str: 

    """ 

    Launch soma_bash.sh with the provided CLI args. 

    Returns the run_id. 

    """ 


    cmd = ["/bin/bash", str(BASH_DRIVER)] + args 
    


    run = RunStatus(cmd) 

    run.log_dir = log_dir_for(run.id)

    runs[run.id] = run 

    # truncate prev soma_run.log
    global_log = LOGS_DIR / "soma_run.log"
    global_log.touch()
    (global_log).write_text("")



    if args:
        os.environ["ALERT_EMAILS"] = args[0]
        #update in-memory list coming from ui
        email_helper.ALERT_EMAILS = [e.strip() for e in args[0].split(",") if e.strip()]

    
    
    log_path = run.log_dir / "driver.log"
    env = os.environ.copy()
    env["LOGS_DIR"] = str(run.log_dir)
    pgid_file = (run.log_dir / "run_pgid").as_posix()
    env["RUN_PGID_FILE"] = pgid_file


    # spawn the bash driver 

    proc = await asyncio.create_subprocess_exec( 

        *cmd,

        stdout=asyncio.subprocess.PIPE, 

        stderr=asyncio.subprocess.STDOUT, 

        cwd=str(APP_ROOT), 

        env=env,

        start_new_session=True,

    ) 

    run.process = proc 

    

    # background tasks: driver stdout reader + logfile watcher 

    asyncio.create_task(_read_driver_stdout(run)) 

    asyncio.create_task(_watch_new_logs(run)) 

    

    # detach a waiter to set final status 

    asyncio.create_task(_wait_for_exit(run)) 

    

    return run.id 

 

 

async def _wait_for_exit(run: RunStatus) -> None: 

    await run.process.wait() 

    run.return_code = run.process.returncode 

    run.end_ts = datetime.datetime.utcnow().isoformat() + "Z" 

    outcome = "success" if run.return_code == 0 else "failure" 

    

    await sio.emit( 

        "complete", 

        {"run_id": run.id, "outcome": outcome, "rc": run.return_code}, 

        room=run.id, 

    ) 

#     await send_alert_async( -----------  ending email sender
#         subject=f"SOMA pipeline {outcome.upper()}  (run {run.id})", 
#         body=( 
#             f"Outcome: {outcome}\n" 
#             f"Return code: {run.return_code}\n" 
#             f"Started: {run.start_ts}\n" 
#             f"Finished: {run.end_ts}" 
#     ), 
# ) 


    # await send_alert_async(  ------------- dummy email sender
    #     subject="SOMA AUTOMATION TEST",
    #     body="soma pipeline has been completed"
    # )
 

    # cancel tailers 

    for t in run.tail_tasks: 

        t.cancel() 

 

 

def get_status(run_id: str) -> Dict: 

    if run_id not in runs: 

        raise KeyError("run not found") 

    r = runs[run_id] 

    return { 

        "run_id": run_id, 

        "cmd": r.cmd, 

        "stage": r.stage, 

        "start_ts": r.start_ts, 

        "end_ts": r.end_ts, 

        "return_code": r.return_code, 

    } 

 

 

def list_log_files(run_id: str) -> List[str]: 

    files: set[str] = set() 
    for p in log_dir_for(run_id).glob("*.log"): 
        files.add(p.name) 
    for p in LOGS_DIR.glob("*.log"): 
        files.add(p.name) 
    return sorted(files) 

 



def _pause_process(run: RunStatus):            #---
    if run.process and run.process.pid: 
        os.kill(run.process.pid, signal.SIGSTOP) 
        run.paused = True 

def _resume_process(run: RunStatus):            #---
    if run.process and run.process.pid: 
        os.kill(run.process.pid, signal.SIGCONT) 
        run.paused = False 

 

def get_active_run_id() -> Optional[str]: 
    """Return the newest run_id whose process is still running.""" 
    active = [r for r in runs.values() if r.return_code is None] 
    if not active: 
        return None 
    return sorted(active, key=lambda r: r.start_ts)[-1].id 


def log_dir_for(run_id: str) -> Path: 
    d = LOGS_DIR / run_id 
    d.mkdir(parents=True, exist_ok=True) 
    return d 


#END IT

def _terminate_run(run: RunStatus):
    if run.process and run.process.returncode is None:
        pgid = os.getpgid(run.process.pid)
        # First try TERM
        try:
            os.killpg(pgid, signal.SIGTERM)
        except ProcessLookupError:
            pass
        time.sleep(5)
        try:
            os.killpg(pgid, signal.SIGKILL)
        except ProcessLookupError:
            pass
    if run.log_dir and run.log_dir.exists():
        try:
            shutil.rmtree(run.log_dir)
            print('LOGS DELETED')
        except:
            print('ERROR DELETING LOGS')

    
 
async def cancel_run(run_id: str):
    if run_id not in runs:
        raise KeyError("run not found")
    _terminate_run(runs[run_id])
 

 

 

 