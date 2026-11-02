from pathlib import Path
from typing import Literal
from urllib.parse import urlsplit

from pydantic import BaseModel, ConfigDict, Field, field_validator
from pydantic_settings import (
    BaseSettings,
    PydanticBaseSettingsSource,
    SettingsConfigDict,
    YamlConfigSettingsSource,
)


class UIConfig(BaseModel):
    model_config = ConfigDict(extra="forbid")
    theme: Literal["dark", "light"] = "dark"
    language: Literal["en", "fr", "de", "pt-PT"] = "en"


class UIUpdate(UIConfig):
    profile_id: str | None = None


class Settings(BaseSettings):
    model_config = SettingsConfigDict(
        env_prefix="OCHE_CORE_",
        env_file=".env",
        env_ignore_empty=True,
        yaml_file="config/config.yaml",
        yaml_file_encoding="utf-8",
        extra="ignore",
    )

    @classmethod
    def settings_customise_sources(
        cls,
        settings_cls: type[BaseSettings],
        init_settings: PydanticBaseSettingsSource,
        env_settings: PydanticBaseSettingsSource,
        dotenv_settings: PydanticBaseSettingsSource,
        file_secret_settings: PydanticBaseSettingsSource,
    ) -> tuple[PydanticBaseSettingsSource, ...]:
        return (
            init_settings,
            env_settings,
            dotenv_settings,
            YamlConfigSettingsSource(settings_cls),
            file_secret_settings,
        )

    data_dir: Path = Path("data")
    api_base_url: str = "https://api.autodarts.com"
    client_id: str = ""
    board_id: str = ""
    ui_theme: Literal["dark", "light"] | None = None
    local_autodarts_url: str = "http://127.0.0.1:3180"
    ui_language: Literal["en", "fr", "de", "pt-PT"] | None = None
    request_timeout: float = Field(default=10, gt=0)
    reconcile_interval: float = Field(default=30, gt=0)

    @field_validator("local_autodarts_url")
    @classmethod
    def valid_local_url(cls, value: str) -> str:
        parts = urlsplit(value)
        if (
            parts.scheme not in {"http", "https"}
            or not parts.hostname
            or parts.username
            or parts.password
            or parts.query
            or parts.fragment
            or parts.path not in {"", "/"}
        ):
            raise ValueError("The local detector requires an HTTP or HTTPS origin without credentials")
        try:
            _ = parts.port
        except ValueError:
            raise ValueError("The local detector port is invalid") from None
        return value.rstrip("/")

    @field_validator("api_base_url")
    @classmethod
    def valid_api_url(cls, value: str) -> str:
        parts = urlsplit(value)
        if (
            parts.scheme != "https"
            or not parts.hostname
            or parts.username
            or parts.password
            or parts.query
            or parts.fragment
            or parts.path not in {"", "/"}
        ):
            raise ValueError("The cloud API requires an HTTPS origin without credentials")
        return value.rstrip("/")
