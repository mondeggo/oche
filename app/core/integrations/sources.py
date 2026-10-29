"""Shared source selection for game-driven integrations."""

from pydantic import BaseModel, ConfigDict

from app.core.events import Event


class PlaySources(BaseModel):
    model_config = ConfigDict(extra="forbid")
    run_on_cloud: bool = True
    run_on_local: bool = True


def source_allowed(config: PlaySources, source: str) -> bool:
    if source == "local":
        return config.run_on_local
    return source == "cloud" and config.run_on_cloud


def event_allowed(config: PlaySources, event: Event, game: dict) -> bool:
    current = game.get("source")
    source = event.data.get("play_source", current or "cloud")
    return source_allowed(config, source) and (current is None or current == source)


def integration_view(config: PlaySources, game: dict) -> dict:
    allowed = source_allowed(config, game.get("source", "cloud"))
    if allowed:
        return {**game, "source_enabled": True}
    return {
        **game,
        "source_enabled": False,
        "available": False,
        "phase": "waiting",
        "reason": "This integration is disabled for the selected play source.",
    }
