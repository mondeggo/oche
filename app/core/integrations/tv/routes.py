"""The TV and remote pages reuse Play's page and scripts in a display without the sidebar."""

from fastapi import APIRouter, HTTPException, Request

from app.core.integrations import registry
from app.services import engines
from app.templating import templates

pages = APIRouter(include_in_schema=False)
STYLE = "/static/integrations/tv/tv.css"


def play(request: Request, display: str):
    # Switched off, the integration adds no pages.
    if not registry.enabled("tv"):
        raise HTTPException(404)
    return templates.TemplateResponse(request=request, name="local.html", context={
        "active_nav": "local",
        "vision_engines": engines.status(),
        "display": display,
        "bare": True,
        "page_styles": [STYLE],
    })


@pages.get("/tv")
async def tv(request: Request):
    """Read-only: whatever game is on, or a practice board between games."""
    return play(request, "tv")


@pages.get("/tv/remote")
async def remote(request: Request):
    """Play for a phone or tablet: choose and run games while the TV shows them."""
    return play(request, "remote")


@pages.get("/integrations/tv")
async def manage(request: Request):
    return templates.TemplateResponse(request=request, name="tv/manage.html", context={"active_nav": "tv"})
