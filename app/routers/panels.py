from uuid import UUID, uuid4

from fastapi import APIRouter, HTTPException, Request
from pydantic import AnyHttpUrl, BaseModel, ConfigDict, Field, field_validator

from app.config import load_config, save_config
from app.templating import templates

router = APIRouter(prefix="/panels", tags=["panels"])


class Panel(BaseModel):
    model_config = ConfigDict(str_strip_whitespace=True)

    id: UUID = Field(default_factory=uuid4)
    name: str = Field(min_length=1, max_length=60)
    url: AnyHttpUrl
    open_in_new_tab: bool = False
    pinned: bool = False


class PanelUpdate(BaseModel):
    panels: list[Panel] = Field(max_length=30)

    @field_validator("panels")
    @classmethod
    def unique_ids(cls, panels):
        if len({panel.id for panel in panels}) != len(panels):
            raise ValueError("Panel IDs must be unique")
        return panels


@router.put("/data")
async def update_panels(update: PanelUpdate):
    config = load_config()
    config["panels"] = update.model_dump(mode="json")["panels"]
    save_config(config)
    return {"panels": config["panels"]}


@router.get("/{panel_id}")
async def page(request: Request, panel_id: UUID):
    config = load_config()
    panel = next(
        (
            panel
            for panel in config.get("panels", [])
            if panel["id"] == str(panel_id)
        ),
        None,
    )
    if panel is None:
        raise HTTPException(status_code=404, detail="Panel not found")
    return templates.TemplateResponse(
        "panel.html",
        {
            "request": request,
            "panel": panel,
            "active_nav": "panels",
            "full_width": True,
            "allow_header_autohide": True,
            "autohide_navbar_default": config.get("autohide_navbar_on_panels", False),
        },
    )
