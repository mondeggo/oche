"""Persisted settings for the standalone /scoreboard display page."""

import asyncio
from typing import Literal

from pydantic import ConfigDict, Field, ValidationError

from app.core.config import Settings
from app.core.integrations.sources import PlaySources
from app.core.storage import write_private_json


class ScoreboardConfig(PlaySources):
    model_config = ConfigDict(extra="forbid")
    enabled: bool = False
    title: str = Field(default="", max_length=60)
    theme: Literal["dark", "light", "auto"] = "auto"
    show_last_dart: bool = True
    show_checkout: bool = True
    show_sets_legs: bool = True
    show_bots: bool = True


class Scoreboard:
    def __init__(self, settings: Settings):
        self.path = settings.data_dir / "scoreboard.json"
        self.lock = asyncio.Lock()
        self.error: str | None = None
        self.config = ScoreboardConfig()
        try:
            if self.path.exists():
                self.config = ScoreboardConfig.model_validate_json(
                    self.path.read_text(encoding="utf-8")
                )
        except (OSError, ValueError, ValidationError):
            self.error = "Cannot load scoreboard.json. Save settings to replace the invalid file."

    def status(self) -> dict:
        return {**self.config.model_dump(), "error": self.error}

    async def configure(self, update: ScoreboardConfig) -> dict:
        async with self.lock:
            values = {**self.config.model_dump(), **update.model_dump(exclude_unset=True)}
            config = ScoreboardConfig.model_validate(values)
            write_private_json(self.path, config.model_dump())
            self.config, self.error = config, None
            return self.status()
