"""Persistent appearance settings for the Oche workspace."""

import asyncio
import json
import re
from pathlib import Path

from fastapi import APIRouter, Request
from fastapi.responses import HTMLResponse
from pydantic import ValidationError

from app.core.autodarts.errors import ConnectionProblem
from app.core.config import Settings, UIConfig, UIUpdate
from app.core.storage import write_private_json
from app.i18n import catalogue, translate


class UISettings:
    def __init__(self, settings: Settings):
        self.path = settings.data_dir / "ui.json"
        self.lock = asyncio.Lock()
        self.error: str | None = None
        self.config = UIConfig()
        try:
            if self.path.exists():
                self.config = UIConfig.model_validate_json(self.path.read_text(encoding="utf-8"))
        except (OSError, ValueError, ValidationError):
            self.error = "Cannot load ui.json. Save UI settings to replace the invalid file."
        overrides = {
            key: value
            for key in UIConfig.model_fields
            if (value := getattr(settings, f"ui_{key}")) is not None
        }
        self.config = UIConfig.model_validate({**self.config.model_dump(), **overrides})

    def status(self) -> dict:
        return {**self.config.model_dump(), "error": self.error}

    async def configure(self, update: UIConfig) -> dict:
        async with self.lock:
            values = {**self.config.model_dump(), **update.model_dump(exclude_unset=True)}
            config = UIConfig.model_validate(values)
            write_private_json(self.path, config.model_dump())
            self.config, self.error = config, None
            return self.status()


def render_scoreboard_display(
    path: Path, ui_theme: str, scoreboard_theme: str, language: str = "en"
) -> HTMLResponse:
    source = path.read_text(encoding="utf-8")
    theme = ui_theme if scoreboard_theme == "auto" else scoreboard_theme

    def attributes(match: re.Match) -> str:
        opening = re.sub(r'\sdata-theme="[^"]*"', "", match.group())
        opening = re.sub(r'\slang="[^"]*"', "", opening)
        return opening[:-1] + f' data-theme="{theme}" lang="{language}">'

    payload = json.dumps(catalogue(language), ensure_ascii=True).replace("<", "\\u003c")
    source = source.replace(
        "</head>",
        f'<script id="oche-translations" type="application/json">{payload}</script><script src="/static/js/i18n.js"></script></head>',
    )
    source = source.replace("Scoreboard · Oche", translate("Scoreboard · Oche", language))
    return HTMLResponse(re.sub(r"<html\b[^>]*>", attributes, source, count=1, flags=re.IGNORECASE))


router = APIRouter()


@router.get("/api/ui")
async def ui_settings(request: Request):
    runtime = request.app.state.runtime
    return {**request.app.state.ui.status(), "profile_id": runtime.profiles.data.wled.active_profile}


@router.patch("/api/ui")
async def configure_ui(update: UIUpdate, request: Request):
    runtime = request.app.state.runtime
    async with runtime.lock:
        if update.profile_id is not None and update.profile_id != runtime.profiles.data.wled.active_profile:
            raise ConnectionProblem("The active profile changed. Reload appearance settings and try again.")
        values = UIConfig.model_validate(update.model_dump(exclude={"profile_id"}, exclude_unset=True))
        await runtime.profiles.configure_ui(values)
        return {**request.app.state.ui.status(), "profile_id": runtime.profiles.data.wled.active_profile}
