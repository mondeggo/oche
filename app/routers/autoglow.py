from typing import Literal
from fastapi import APIRouter, HTTPException, Request
from app.templating import templates

from app.config import LOG_DIR, load_config
from app.services import autoglow
from app.log_download import log_download

router = APIRouter(prefix="/autoglow", tags=["autoglow"])


@router.get("")
async def page(request: Request):
    return templates.TemplateResponse(
        "autoglow.html",
        {
            "request": request,
            "status": autoglow.get_status(),
            "active_nav": "autoglow",
            "full_width": True,
            "allow_header_autohide": True,
            "autohide_navbar_default": load_config().get("autohide_navbar_on_autoglow", False),
        },
    )


@router.get("/status")
async def status():
    return autoglow.get_status()


@router.post("/start")
def start():
    if not autoglow.installed():
        raise HTTPException(status_code=503, detail="AutoGlow 2 is not installed in this image.")
    try:
        autoglow.start()
    except RuntimeError as e:
        raise HTTPException(status_code=500, detail=str(e))
    return autoglow.get_status()


@router.get("/logs")
def logs():
    return autoglow.logs()


@router.get("/logs/export")
def export_logs():
    path = LOG_DIR / "autoglow-web.log"
    return log_download(path)


@router.post("/stop")
def stop():
    autoglow.stop()
    return autoglow.get_status()


@router.post("/logs/clear")
def clear_logs():
    try:
        autoglow.clear_logs()
    except OSError as e:
        raise HTTPException(status_code=500, detail="Failed to clear AutoGlow logs.") from e
    return {"ok": True}


@router.post("/restart")
def restart():
    if not autoglow.installed():
        raise HTTPException(status_code=503, detail="AutoGlow 2 is not installed in this image.")
    try:
        autoglow.restart()
    except RuntimeError as e:
        raise HTTPException(status_code=500, detail=str(e))
    return autoglow.get_status()


@router.post("/process/{role}/{action}")
def control_process(role: Literal["web"], action: Literal["start", "stop", "restart"]):
    if action != "stop" and not autoglow.installed():
        raise HTTPException(status_code=503, detail="AutoGlow 2 is not installed in this image.")
    try:
        autoglow.control_process(role, action)
    except RuntimeError as e:
        raise HTTPException(status_code=500, detail=str(e))
    return autoglow.get_status()
