from fastapi import APIRouter, HTTPException, Request
from fastapi.responses import RedirectResponse

from app.config import LOG_DIR, load_config
from app.log_download import log_download
from app.services import ochecore
from app.templating import templates

router = APIRouter(prefix="/ochecore", tags=["ochecore"])


@router.get("")
async def legacy_page():
    return RedirectResponse("/", status_code=308)


async def page(request: Request):
    return templates.TemplateResponse("ochecore.html", {
        "request": request,
        "status": ochecore.get_status(),
        "active_nav": "home",
        "full_width": True,
        "allow_header_autohide": True,
        "autohide_navbar_default": load_config().get("autohide_navbar_on_ochecore", False),
    })


@router.get("/status")
def status():
    return ochecore.get_status()


def _start(restart=False):
    if not ochecore.installed():
        raise HTTPException(status_code=503, detail="OcheCore is not installed in this image. Rebuild or update Oche.")
    try:
        ochecore.restart() if restart else ochecore.start()
    except RuntimeError as error:
        raise HTTPException(status_code=500, detail=str(error)) from error
    return ochecore.get_status()


@router.post("/start")
def start():
    return _start()


@router.post("/stop")
def stop():
    ochecore.stop()
    return ochecore.get_status()


@router.post("/restart")
def restart():
    return _start(restart=True)


@router.get("/logs")
def logs():
    return ochecore.logs()


@router.get("/logs/export")
def export_logs():
    return log_download(LOG_DIR / "ochecore.log")


@router.post("/logs/clear")
def clear_logs():
    try:
        ochecore.clear_logs()
    except OSError as error:
        raise HTTPException(status_code=500, detail="Failed to clear OcheCore logs.") from error
    return {"ok": True}
