"""The Autodarts connection: OAuth client and board, saved in data/connection.json."""

import json
from uuid import UUID

from pydantic import BaseModel, ConfigDict, Field, field_validator


class ConnectionConfig(BaseModel):
    model_config = ConfigDict(extra="forbid", str_strip_whitespace=True)

    client_id: str = Field(default="darts-caller", max_length=200)
    board_id: str = ""

    @field_validator("client_id")
    @classmethod
    def default_client_id(cls, value: str) -> str:
        return value or "darts-caller"

    @field_validator("board_id")
    @classmethod
    def valid_board(cls, value: str) -> str:
        return str(UUID(value)) if value else ""


def load_connection(settings) -> ConnectionConfig:
    """The saved connection, with values from .env or config.yaml taking precedence."""
    path = settings.data_dir / "connection.json"
    values = {}
    if path.exists():
        saved = json.loads(path.read_text(encoding="utf-8"))
        values = ConnectionConfig.model_validate(saved).model_dump()
    for key in ConnectionConfig.model_fields:
        if value := getattr(settings, key):
            values[key] = value
    return ConnectionConfig(**values)


def locked_fields(settings) -> list[str]:
    return [key for key in ConnectionConfig.model_fields if getattr(settings, key)]
