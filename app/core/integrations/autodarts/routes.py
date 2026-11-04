from fastapi import APIRouter, HTTPException, Request, WebSocket
from fastapi.responses import PlainTextResponse

from app.config import LOG_DIR, load_config
from app.core.integrations.autodarts import process as autodarts
from app.core.integrations.autodarts.terminal import AutodartsTerminal
from app.log_download import log_download
from app.services import engines
from app.services.setup_terminal import serve_terminal
from app.templating import templates

router = APIRouter(prefix="/autodarts", tags=["autodarts"])
# Pages the integration adds outside its /autodarts API.
pages = APIRouter(include_in_schema=False)


@router.get("/terminal")
async def terminal_page(request: Request):
    return templates.TemplateResponse(request=request, name="terminal.html", context={
        "service": "autodarts", "label": "Autodarts",
    })


@router.websocket("/terminal/ws")
async def terminal_socket(websocket: WebSocket):
    await serve_terminal(websocket, autodarts, AutodartsTerminal, "Autodarts")


@pages.get("/integrations/autodarts/board")
async def page(request: Request):
    service = "autodarts"
    config = load_config()
    return templates.TemplateResponse(request=request, name="autodarts/board.html", context={
            "request": request,
            "service": service,
            "status": autodarts.get_status(),
            "config": config,
            "active_nav": "autodarts",
        },
    )


@pages.get("/integrations/cameras")
async def camera_enhancer(request: Request):
    return templates.TemplateResponse(
        request=request, name="autodarts/cameras.html", context={"active_nav": "cameras", "service": "cameras"},
    )


@pages.get("/play")
async def play(request: Request):
    return templates.TemplateResponse(
        request=request, name="autodarts/play.html", context={"request": request, "active_nav": "play", "full_width": True},
    )


@router.get("/status")
def status():
    return autodarts.get_status()


@router.post("/start")
def start():
    # Starting Autodarts makes it the active engine and stops OcheCore.
    try:
        engines.use("autodarts")
        ok = autodarts.get_status()["status"] == "running"
    except (RuntimeError, ValueError) as e:
        raise HTTPException(status_code=409, detail=str(e)) from e
    return {"ok": ok, **autodarts.get_status()}


@router.post("/stop")
def stop():
    ok = autodarts.stop()
    return {"ok": ok, **autodarts.get_status()}


@router.post("/restart")
def restart():
    try:
        autodarts.stop()
        engines.use("autodarts")
        ok = autodarts.get_status()["status"] == "running"
    except (RuntimeError, ValueError) as e:
        raise HTTPException(status_code=409, detail=str(e)) from e
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
