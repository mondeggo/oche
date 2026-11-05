from fastapi import APIRouter, HTTPException, Request, WebSocket
from fastapi.responses import PlainTextResponse

from app.config import LOG_DIR, load_config
from app.core.integrations.local_url import local_http_url
from app.log_download import log_download
from app.services import engines
from app.services.setup_terminal import serve_terminal
from app.templating import templates

from . import process
from .terminal import OcheCoreTerminal

router = APIRouter(prefix="/ochecore", tags=["ochecore"])
pages = APIRouter(include_in_schema=False)


@router.get("/terminal")
async def terminal_page(request: Request):
    return templates.TemplateResponse(request=request, name="terminal.html", context={
        "service": "ochecore", "label": "OcheCore",
    })


@router.websocket("/terminal/ws")
async def terminal_socket(websocket: WebSocket):
    await serve_terminal(websocket, process, OcheCoreTerminal, "OcheCore")


@pages.get("/integrations/ochecore")
async def page(request: Request):
    return templates.TemplateResponse(request=request, name="ochecore/manage.html", context={
        "service": "ochecore",
        "status": process.get_status(),
        "config": load_config(),
        "local_url": local_http_url(request.url.hostname, process.PORT),
        "active_nav": "ochecore",
    })


@router.get("/status")
def status():
    return process.get_status()


def _use():
    # Starting OcheCore makes it the active engine, so Autodarts releases the cameras.
    try:
        engines.use("ochecore")
    except RuntimeError as error:
        raise HTTPException(status_code=409, detail=str(error)) from error
    state = process.get_status()
    return {"ok": state["status"] == "running", **state}


@router.post("/start")
def start():
    return _use()


@router.post("/restart")
def restart():
    process.stop()
    return _use()


@router.post("/stop")
def stop():
    return {"ok": process.stop(), **process.get_status()}


@router.get("/logs", response_class=PlainTextResponse)
def logs():
    return process.logs()


@router.get("/logs/export")
def export_logs():
    return log_download(LOG_DIR / "ochecore.log")


@router.post("/logs/clear")
def clear_logs():
    try:
        process.clear_logs()
    except OSError as error:
        raise HTTPException(status_code=500, detail="Failed to clear OcheCore logs.") from error
    return {"ok": True}
