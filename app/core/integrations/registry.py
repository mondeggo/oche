"""Integrations register what they add to Oche; the rest of Oche reads this registry.

An integration package defines ``integration = register(Integration(...))`` in its
``__init__``. Oche imports the packages through ``load()`` and never names one
directly: the sidebar, Updates, Play, the engine switch and app start-up only use
the entries below. Switching an integration off hides what it adds; its files and
settings stay in place.
"""

import importlib
import pkgutil
from collections.abc import Callable
from dataclasses import dataclass, field
from pathlib import Path

from app import config as settings


@dataclass(frozen=True)
class PlayButton:
    """A button the sidebar shows below Play."""

    id: str
    label: str
    href: str
    icon: str  # Static URL of a single-colour SVG, drawn in the button's colour.
    page: str  # Sidebar page key, so the button shows as current.
    new_tab_href: str | None = None  # Direct URL used when the new-tab setting is on.
    new_tab_setting: str | None = None  # Settings switch controlling new_tab_href.


@dataclass(frozen=True)
class Page:
    """A management page linked under Integrations in the sidebar."""

    id: str
    label: str
    href: str
    page: str
    setting: str | None = None  # A settings.json switch that can also hide the link.


@dataclass(frozen=True)
class Release:
    """How Updates finds, checks and unpacks the integration's program."""

    description: str
    discover: Callable  # (download, architecture) -> release dict
    prepare: Callable  # (archive, content, version, extract) -> None
    format: str
    entry: str  # File that must exist in a prepared release.


@dataclass(frozen=True)
class HomeView:
    """A page of the Home workspace, such as an account's details."""

    id: str
    href: str
    title: str
    description: str
    template: str


@dataclass(frozen=True)
class Health:
    """A status dot in the sidebar. Its module exports ``health(id, status, tr)``.

    ``status`` is the shared /api/status reply (or null when it failed); the
    function returns ``[tone, text, detail]`` with tone good, warn, bad or neutral.
    Engine indicators combine this event status with their process and detection
    readiness. Only enabled integrations contribute indicators.
    """

    id: str
    label: str
    href: str
    module: str
    order: int


@dataclass(frozen=True)
class Engine:
    """A vision engine Play can choose. Only one engine reads the cameras at a time."""

    label: str
    scores: bool  # Whether Play can score darts from it yet.
    note: str = ""  # Shown when it cannot score.
    # async (http, url) -> bool: start an engine that sleeps between games, such as
    # OcheCore's standby. Play calls it only while a game reads this engine.
    wake: Callable | None = None
    # async (http, url) -> bool: when a game starts, drop darts the engine still lists
    # from before the game but that are not on the board. Runs beside the game.
    prepare: Callable | None = None


@dataclass
class Integration:
    id: str
    name: str
    description: str
    enabled_by_default: bool = False
    order: int = 100  # Position in the sidebar, Updates and Play; ties sort by id.
    play: tuple[PlayButton, ...] = ()
    pages: tuple[Page, ...] = ()
    release: Release | None = None
    process: object | None = None  # Module with get_status(), start() and stop().
    # Port the process serves; Updates checks it after an update. An engine serves
    # the board API there, on this machine, for Play to score from.
    port: int | None = None
    engine: Engine | None = None
    routers: tuple = ()
    templates: Path | None = None  # Loaded as "<id>/<name>.html".
    static: Path | None = None  # Served at /static/integrations/<id>/.
    cards: tuple[str, ...] = ()  # Templates included on the Integrations page.
    scripts: tuple[str, ...] = ()  # Module scripts the Home workspace loads.
    # Workspace slots: "notices" above every view and "account" at the top of Integrations.
    home: dict[str, tuple[str, ...]] = field(default_factory=dict)
    views: tuple[HomeView, ...] = ()
    health: tuple[Health, ...] = ()
    # Builds a game source for the runtime: (runtime) -> object with run(),
    # pause(), game_state(), status(), ready, normalizer and close().
    source: Callable | None = None
    shutdown: tuple[Callable, ...] = field(default_factory=tuple)  # Async callables run at stop.

    @property
    def enabled(self) -> bool:
        return enabled(self.id)


REGISTRY: dict[str, Integration] = {}


def register(integration: Integration) -> Integration:
    REGISTRY[integration.id] = integration
    return integration


def load() -> dict[str, Integration]:
    """Import every integration package once, so each one registers itself."""
    package = importlib.import_module("app.core.integrations")
    for module in pkgutil.iter_modules(package.__path__):
        if module.ispkg:
            importlib.import_module(f"{package.__name__}.{module.name}")
    return REGISTRY


def integrations() -> list[Integration]:
    # Import order varies, so list integrations in a fixed order.
    return sorted(load().values(), key=lambda item: (item.order, item.id))


def get(name: str) -> Integration | None:
    return load().get(name)


def enabled(name: str) -> bool:
    integration = load().get(name)
    if integration is None:
        return False
    saved = settings.load_config().get("integrations", {}).get(name, {})
    return bool(saved.get("enabled", integration.enabled_by_default))


def set_enabled(name: str, value: bool) -> None:
    if name not in load():
        raise ValueError("Unknown integration")
    config = settings.load_config()
    saved = dict(config.get("integrations", {}))
    saved[name] = {**saved.get(name, {}), "enabled": bool(value)}
    config["integrations"] = saved
    settings.save_config(config)


def active() -> list[Integration]:
    return [integration for integration in integrations() if integration.enabled]
