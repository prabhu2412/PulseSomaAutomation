"""
FastAPI + Socket.IO entrypoint.

SOMA endpoints:
  POST /tests
  POST /tests/{id}/cancel
  GET  /tests/active
  GET  /tests/{id}
  GET  /tests/{id}/logs
  GET  /tests/{id}/logs/{file}

IMPULSE endpoints:
  POST /impulse/tests
  POST /impulse/tests/{id}/cancel
  GET  /impulse/tests/active
  GET  /impulse/tests/{id}
  GET  /impulse/tests/{id}/logs
  GET  /impulse/tests/{id}/logs/{file}
"""

from pathlib import Path
from typing import List

import socketio
from fastapi import FastAPI, HTTPException, status
from fastapi.responses import FileResponse
from fastapi.middleware.cors import CORSMiddleware
from starlette.staticfiles import StaticFiles
from pydantic import BaseModel

from . import job_runner
from . import impulse_runner


# Socket.IO server
sio = socketio.AsyncServer(async_mode="asgi")
job_runner.sio = sio
impulse_runner.sio = sio

# FastAPI app
app = FastAPI(title="Pipelines")

# CORS
app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_methods=["*"],
    allow_headers=["*"],
)

# ----- Models -----
class StartRequest(BaseModel):
    args: List[str] = []


# ----- SOMA routes -----
@app.post("/tests", status_code=202)
async def start_soma(req: StartRequest):
    run_id = await job_runner.start_run(req.args)
    return {"run_id": run_id}

@app.post("/tests/{run_id}/cancel", status_code=status.HTTP_204_NO_CONTENT)
async def cancel_soma(run_id: str):
    try:
        await job_runner.cancel_run(run_id)
    except KeyError:
        raise HTTPException(status_code=404, detail="run not found")

@app.get("/tests/active")
def active_soma():
    rid = job_runner.get_active_run_id()
    if rid:
        return {"run_id": rid}
    raise HTTPException(status_code=404, detail="no active run")

@app.get("/tests/{run_id}")
def status_soma(run_id: str):
    try:
        return job_runner.get_status(run_id)
    except KeyError:
        raise HTTPException(status_code=404, detail="run not found")

@app.get("/tests/{run_id}/logs")
def list_logs_soma(run_id: str):
    try:
        files = job_runner.list_log_files(run_id)
        return {"files": files}
    except KeyError:
        raise HTTPException(status_code=404, detail="run not found")

@app.get("/tests/{run_id}/logs/{filename}")
def download_log_soma(run_id: str, filename: str):
    # try run-specific dir first, then global logs dir
    p1 = (job_runner.log_dir_for(run_id) / filename)
    if p1.exists():
        return FileResponse(p1, media_type="text/plain")
    p2 = (job_runner.LOGS_DIR / filename)
    if p2.exists():
        return FileResponse(p2, media_type="text/plain")
    raise HTTPException(status_code=404, detail="log not found")


# ----- IMPULSE routes -----
@app.post("/impulse/tests", status_code=202)
async def start_impulse(req: StartRequest):
    run_id = await impulse_runner.start_run(req.args)
    return {"run_id": run_id}

@app.post("/impulse/tests/{run_id}/cancel", status_code=status.HTTP_204_NO_CONTENT)
async def cancel_impulse(run_id: str):
    try:
        await impulse_runner.cancel_run(run_id)
    except KeyError:
        raise HTTPException(status_code=404, detail="run not found")

@app.get("/impulse/tests/active")
def active_impulse():
    rid = impulse_runner.get_active_run_id()
    if rid:
        return {"run_id": rid}
    raise HTTPException(status_code=404, detail="no active run")

@app.get("/impulse/tests/{run_id}")
def status_impulse(run_id: str):
    try:
        return impulse_runner.get_status(run_id)
    except KeyError:
        raise HTTPException(status_code=404, detail="run not found")

@app.get("/impulse/tests/{run_id}/logs")
def list_logs_impulse(run_id: str):
    try:
        files = impulse_runner.list_log_files(run_id)
        return {"files": files}
    except KeyError:
        raise HTTPException(status_code=404, detail="run not found")

@app.get("/impulse/tests/{run_id}/logs/{filename}")
def download_log_impulse(run_id: str, filename: str):
    p1 = (impulse_runner.log_dir_for(run_id) / filename)
    if p1.exists():
        return FileResponse(p1, media_type="text/plain")
    p2 = (impulse_runner.LOGS_DIR / filename)
    if p2.exists():
        return FileResponse(p2, media_type="text/plain")
    raise HTTPException(status_code=404, detail="log not found")


# Serve the React frontend
app.mount(
    "/",
    StaticFiles(directory=Path(__file__).parent.parent / "static", html=True),
    name="static",
)


# ----- Socket.IO handlers -----
@sio.event
async def connect(sid, environ):
    pass

@sio.event
async def join(sid, data):
    rid = data.get("run_id")
    if not rid:
        return

    if rid in job_runner.runs:
        sio.enter_room(sid, rid)
        current = job_runner.runs[rid].stage
        await sio.emit("stage", {"run_id": rid, "stage": current}, room=sid)
        if job_runner.runs[rid].paused:
            await sio.emit("paused", {"run_id": rid, "stage": current}, room=sid)
        return

    if rid in impulse_runner.runs:
        sio.enter_room(sid, rid)
        current = impulse_runner.runs[rid].stage
        await sio.emit("stage", {"run_id": rid, "stage": current}, room=sid)
        return

@sio.event
async def continue_stage(sid, data):
    rid = data.get("run_id")
    if not rid or rid not in job_runner.runs:
        return
    run = job_runner.runs[rid]
    if not run.paused:
        return
    job_runner._resume_process(run)
    await sio.emit("resumed", {"run_id": run.id, "stage": run.pending_stage}, room=run.id)

@sio.event
def disconnect(sid):
    pass


from socketio import ASGIApp
asgi_app = ASGIApp(socketio_server=sio, other_asgi_app=app, socketio_path="socket.io")
