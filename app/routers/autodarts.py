import asyncio
from contextlib import suppress
import json
import os
from threading import BoundedSemaphore

import anyio
from fastapi import APIRouter, HTTPException, Request, WebSocket, WebSocketDisconnect
from fastapi.responses import PlainTextResponse
from app.templating import templates

from app.config import LOG_DIR, load_config
from app.services import autodarts, autoglow
from app.services.autodarts_terminal import AutodartsTerminal
from app.security import same_origin
from app.log_download import log_download

router = APIRouter(prefix="/autodarts", tags=["autodarts"])
terminal_slots = BoundedSemaphore(4)


@router.get("/terminal")
async def terminal_page(request: Request):
    return templates.TemplateResponse("terminal.html", {"request": request})


@router.websocket("/terminal/ws")
async def terminal_socket(websocket: WebSocket):
    scheme = "https" if websocket.url.scheme == "wss" else "http"
    if not same_origin(websocket.headers.get("origin", ""), scheme, websocket.headers.get("host", "")):
        await websocket.close(code=1008)
        return
    await websocket.accept()
    terminal = None
    tasks = []
    acquired = False
    try:
        if os.name != "posix":
            await websocket.send_json({"error": "The setup terminal requires the Linux Oche container."})
            return
        if autodarts.get_status()["status"] != "running":
            await websocket.send_json({"error": "Start Autodarts in Supervisor, then reconnect."})
            return
        acquired = terminal_slots.acquire(blocking=False)
        if not acquired:
            await websocket.send_json({"error": "All four setup sessions are in use. Close another terminal and reconnect."})
            return
        terminal = AutodartsTerminal()

        async def output():
            while data := await terminal.read():
                await websocket.send_bytes(data)

        async def input_events():
            while True:
                frame = await websocket.receive()
                if frame["type"] == "websocket.disconnect":
                    return
                raw = frame.get("text")
                if not isinstance(raw, str) or len(raw) > 65536:
                    raise ValueError("Terminal input too large")
                message = json.loads(raw)
                if not isinstance(message, dict):
                    raise ValueError("Invalid terminal message")
                if message.get("type") == "input" and isinstance(message.get("data"), str):
                    await terminal.write(message["data"])
                elif message.get("type") == "resize":
                    rows, cols = message.get("rows"), message.get("cols")
                    if type(rows) is not int or type(cols) is not int or not (2 <= rows <= 200 and 2 <= cols <= 500):
                        raise ValueError("Invalid terminal size")
                    terminal.resize(rows, cols)
                else:
                    raise ValueError("Invalid terminal message")

        tasks = [asyncio.create_task(output()), asyncio.create_task(input_events())]
        done, _ = await asyncio.wait(tasks, return_when=asyncio.FIRST_COMPLETED)
        for task in done:
            task.result()
    except WebSocketDisconnect:
        pass
    except (OSError, ValueError, RecursionError):
        with suppress(WebSocketDisconnect, RuntimeError):
            await websocket.send_json({"error": "The terminal session could not continue. Reconnect to try again."})
    finally:
        # Finish reaping the client even if the WebSocket task is cancelled.
        with anyio.CancelScope(shield=True):
            for task in tasks:
                task.cancel()
            await asyncio.gather(*tasks, return_exceptions=True)
            try:
                if terminal:
                    await terminal.close()
            finally:
                if acquired:
                    terminal_slots.release()
            with suppress(WebSocketDisconnect, RuntimeError):
                await websocket.close()


async def page(request: Request):
    service = "autoglow" if request.query_params.get("service") == "autoglow" else "autodarts"
    config = load_config()
    return templates.TemplateResponse(
        "autodarts.html",
        {
            "request": request,
            "service": service,
            "status": autoglow.get_status() if service == "autoglow" else autodarts.get_status(),
            "config": config,
            "active_nav": "autodarts",
            "allow_header_autohide": True,
            "autohide_navbar_default": config.get("autohide_navbar_on_autodarts", False),
        },
    )


@router.get("/status")
def status():
    return autodarts.get_status()


@router.post("/start")
def start():
    try:
        ok = autodarts.start()
    except RuntimeError as e:
        raise HTTPException(status_code=409, detail=str(e))
    return {"ok": ok, **autodarts.get_status()}


@router.post("/stop")
def stop():
    ok = autodarts.stop()
    return {"ok": ok, **autodarts.get_status()}


@router.post("/restart")
def restart():
    try:
        ok = autodarts.restart()
    except RuntimeError as e:
        raise HTTPException(status_code=409, detail=str(e))
    return {"ok": ok, **autodarts.get_status()}


@router.get("/logs", response_class=PlainTextResponse)
def logs():
    return autodarts.logs()


@router.get("/logs/export")
def export_logs():
    path = LOG_DIR / "autodarts.log"
    return log_download(path)


@router.post("/logs/clear")
def clear_logs():
    try:
        autodarts.clear_logs()
    except OSError as e:
        raise HTTPException(status_code=500, detail="Failed to clear Autodarts logs.") from e
    return {"ok": True}
