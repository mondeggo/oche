"""Autodarts: the board software, its setup terminal, Camera Enhancer, the cloud account
and Play on Autodarts."""

from pathlib import Path

from app.core.integrations.registry import (
    Engine,
    Health,
    HomeView,
    Integration,
    Page,
    PlayButton,
    Release,
    register,
)

from . import camera_controls, process, release
from .account.routes import router as account_router
from .account.source import AutodartsAccount
from .camera_routes import router as camera_router
from .routes import pages, router

HERE = Path(__file__).parent
STATIC = "/static/integrations/autodarts"

integration = register(Integration(
    id="autodarts",
    name="Autodarts",
    description="Board detection and calibration from Autodarts. Play on Autodarts needs it.",
    # The installer adds Autodarts by default, so it starts switched on.
    enabled_by_default=True,
    play=(PlayButton("play", "Play on Autodarts", "/play", f"{STATIC}/logo.svg", "play"),),
    pages=(
        Page("autodarts", "Autodarts", "/integrations/autodarts/board", "autodarts"),
        Page("cameras", "Camera Enhancer", "/integrations/cameras", "cameras", setting="show_page_cameras"),
    ),
    release=Release(
        description="Board detection & calibration",
        discover=release.discover_autodarts,
        prepare=release.prepare_autodarts,
        format="autodarts-archive",
        entry="autodarts",
    ),
    process=process,
    port=3180,
    engine=Engine(label="Autodarts", scores=True),
    routers=(router, camera_router, pages, account_router),
    templates=HERE / "templates",
    static=HERE / "static",
    cards=("autodarts/cameras_card.html",),
    scripts=(f"{STATIC}/account.js", f"{STATIC}/cameras-card.js"),
    home={
        "notices": ("autodarts/home_notice.html",),
        "account": ("autodarts/home_account.html",),
    },
    views=(HomeView(
        "autodarts", "/integrations/autodarts", "Autodarts account",
        "Choose your board and review the cloud connection.", "autodarts/home_view.html",
    ),),
    health=(
        Health("autodarts", "Autodarts", "/integrations/autodarts/board", f"{STATIC}/health.mjs", 10),
    ),
    # Autodarts Play reaches Oche as the "cloud" game source.
    source=AutodartsAccount,
    shutdown=(camera_controls.shutdown,),
))
