from fastapi import APIRouter, HTTPException, Request
from app.templating import templates

from app.config import load_config
from app.services import autoglow

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
