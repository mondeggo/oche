from fastapi import APIRouter, HTTPException, Request
from fastapi.responses import PlainTextResponse
from app.templating import templates

from app.config import load_config
from app.services import autodarts

router = APIRouter(prefix="/autodarts", tags=["autodarts"])


@router.get("")
async def page(request: Request):
    return templates.TemplateResponse(
        "autodarts.html",
        {
            "request": request,
            "status": autodarts.get_status(),
            "active_nav": "autodarts",
            "allow_header_autohide": True,
            "autohide_navbar_default": load_config().get("autohide_navbar_on_autodarts", False),
        },
    )


@router.get("/status")
async def status():
    return autodarts.get_status()


@router.post("/start")
async def start():
    try:
        ok = autodarts.start()
    except RuntimeError as e:
        raise HTTPException(status_code=409, detail=str(e))
    return {"ok": ok, **autodarts.get_status()}


@router.post("/stop")
async def stop():
    ok = autodarts.stop()
    return {"ok": ok, **autodarts.get_status()}


@router.post("/restart")
async def restart():
    try:
        ok = autodarts.restart()
    except RuntimeError as e:
        raise HTTPException(status_code=409, detail=str(e))
    return {"ok": ok, **autodarts.get_status()}


@router.get("/logs", response_class=PlainTextResponse)
async def logs():
    return autodarts.logs()
