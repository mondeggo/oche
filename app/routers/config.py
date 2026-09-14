from fastapi import APIRouter, Request
from app.templating import templates
from pydantic import BaseModel

from app.config import load_config, save_config

router = APIRouter(prefix="/config", tags=["config"])


class ConfigUpdate(BaseModel):
    autostart_autodarts: bool
    autohide_navbar_on_board: bool
    autohide_navbar_on_play: bool
    autohide_navbar_on_autodarts: bool
    autohide_navbar_on_autoglow: bool
    autohide_navbar_on_panels: bool
    show_play_in_navbar: bool
    show_board_in_navbar: bool
    show_autodarts_in_navbar: bool
    show_autoglow_in_navbar: bool
    show_panels_in_navbar: bool


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
    config.update(update.model_dump())
    save_config(config)
    return config
