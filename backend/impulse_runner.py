import asyncio
import uuid
import os
from pathlib import Path
from typing import Dict, List, Optional
import datetime
import time
import signal
import shutil

APP_ROOT = Path(__file__).resolve().parent.parent
SCRIPTS_DIR = APP_ROOT / "scripts"
RUNNER_DIR = APP_ROOT / "runner"
LOGS_DIR = RUNNER_DIR / "logs"
RESULTS_DIR = RUNNER_DIR / "results"

BASH_DRIVER = SCRIPTS_DIR / "impulse_bash.sh"

class RunStatus:
    def __init__(self, cmd: List[str]):
        self.id: str = datetime.datetime.utcnow().strftime("%Y%m%d-%H%M%S-") + uuid.uuid4().hex[:6]
        self.cmd = cmd
        self.process: Optional[asyncio.subprocess.Process] = None
        self.start_ts = datetime.datetime.utcnow().isoformat() + "Z"
        self.end_ts: Optional[str] = None
        self.return_code: Optional[int] = None
        self.tail_tasks: List[asyncio.Task] = []
        self.stage: str = "RUNNING"
        self.log_dir: Path | None = None

runs: Dict[str, RunStatus] = {}
sio = None


async def _tail_file(run: RunStatus, logfile: Path) -> None:
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
        for t in run.tail_tasks:
            t.cancel()


async def _read_driver_stdout(run: RunStatus) -> None:
    assert run.process.stdout is not None
    while True:
        line = await run.process.stdout.readline()
        if not line:
            break
        decoded = line.decode(errors="replace").rstrip("\n")
        await sio.emit(
            "log",
            {
                "run_id": run.id,
                "file": "driver",
                "ts": datetime.datetime.utcnow().timestamp(),
                "line": decoded,
            },
            room=run.id,
        )


async def _wait_for_exit(run: RunStatus) -> None:
    await run.process.wait()
    run.return_code = run.process.returncode
    run.end_ts = datetime.datetime.utcnow().isoformat() + "Z"
    await sio.emit(
        "complete",
        {"run_id": run.id, "outcome": "success" if run.return_code == 0 else "failure", "rc": run.return_code},
        room=run.id,
    )
    for t in run.tail_tasks:
        t.cancel()


async def start_run(args: List[str]) -> str:
    cmd = ["/bin/bash", str(BASH_DRIVER)] + args
    run = RunStatus(cmd)
    run.log_dir = log_dir_for(run.id)
    runs[run.id] = run

    env = os.environ.copy()
    env["LOGS_DIR"] = str(run.log_dir)

    proc = await asyncio.create_subprocess_exec(
        *cmd,
        stdout=asyncio.subprocess.PIPE,
        stderr=asyncio.subprocess.STDOUT,
        cwd=str(APP_ROOT),
        env=env,
        start_new_session=True,
    )
    run.process = proc

    asyncio.create_task(_read_driver_stdout(run))
    asyncio.create_task(_watch_new_logs(run))
    asyncio.create_task(_wait_for_exit(run))

    return run.id


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

def log_dir_for(run_id: str) -> Path:
    d = LOGS_DIR / run_id
    d.mkdir(parents=True, exist_ok=True)
    return d

def get_active_run_id() -> Optional[str]:
    active = [r for r in runs.values() if r.return_code is None]
    if not active:
        return None
    return sorted(active, key=lambda r: r.start_ts)[-1].id

def _terminate_run(run: RunStatus):
    if run.process and run.process.returncode is None:
        pgid = os.getpgid(run.process.pid)
        try:
            os.killpg(pgid, signal.SIGTERM)
        except ProcessLookupError:
            pass
        time.sleep(3)
        try:
            os.killpg(pgid, signal.SIGKILL)
        except ProcessLookupError:
            pass
    if run.log_dir and run.log_dir.exists():
        try:
            shutil.rmtree(run.log_dir)
        except:
            pass

async def cancel_run(run_id: str):
    if run_id not in runs:
        raise KeyError("run not found")
    _terminate_run(runs[run_id])
