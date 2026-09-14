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
            "allow_header_autohide": True,
            "autohide_navbar_default": load_config().get("autohide_navbar_on_autoglow", False),
        },
    )


@router.get("/status")
async def status():
    return autoglow.get_status()


@router.post("/test")
async def test():
    try:
        autoglow.test_animation()
    except RuntimeError as e:
        raise HTTPException(status_code=400, detail=str(e))
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))
    return {"ok": True}
