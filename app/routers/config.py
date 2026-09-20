from typing import Optional

from fastapi import APIRouter, Request
from app.templating import templates
from pydantic import BaseModel

from app.config import load_config, save_config

router = APIRouter(prefix="/config", tags=["config"])


class ConfigUpdate(BaseModel):
    # All optional: callers (the Settings page, and the Supervisor page's
    # inline toggles) only send the fields they're changing.
    autostart_autodarts: Optional[bool] = None
    autostart_autoglow: Optional[bool] = None
    autohide_navbar_on_board: Optional[bool] = None
    autohide_navbar_on_play: Optional[bool] = None
    autohide_navbar_on_autodarts: Optional[bool] = None
    autohide_navbar_on_autoglow: Optional[bool] = None
    autohide_navbar_on_panels: Optional[bool] = None
    show_play_in_navbar: Optional[bool] = None
    show_board_in_navbar: Optional[bool] = None
    show_autodarts_in_navbar: Optional[bool] = None
    show_autoglow_in_navbar: Optional[bool] = None
    show_panels_in_navbar: Optional[bool] = None


@router.get("")
async def page(request: Request):
    return templates.TemplateResponse(
        "config.html",
        {"request": request, "config": load_config(), "active_nav": "config"},
    )


@router.get("/data")
async def get_data():
    return load_config()


@router.post("/data")
async def update_data(update: ConfigUpdate):
    config = load_config()
    config.update(update.model_dump(exclude_none=True))
    save_config(config)
    return config
