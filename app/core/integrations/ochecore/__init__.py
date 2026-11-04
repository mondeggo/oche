"""OcheCore: the offline Rust board service, managed like Autodarts."""

from pathlib import Path

from app.core.integrations.registry import Engine, Health, Integration, Page, Release, register

from . import process, release
from .routes import pages, router

HERE = Path(__file__).parent

integration = register(Integration(
    id="ochecore",
    name="OcheCore",
    description="Offline board detection with two to four cameras.",
    pages=(Page("ochecore", "OcheCore", "/integrations/ochecore", "ochecore"),),
    release=Release(
        description="Offline board detection",
        discover=release.discover_ochecore,
        prepare=release.prepare_ochecore,
        format="ochecore-archive",
        entry="oche-core",
    ),
    process=process,
    port=process.PORT,
    # Play scores with OcheCore once it serves the Autodarts-compatible board API.
    engine=Engine(label="OcheCore", scores=False, note="OcheCore can't score games yet."),
    health=(Health("ochecore", "OcheCore", "/integrations/ochecore", "/static/js/health.mjs", 20),),
    routers=(router, pages),
    templates=HERE / "templates",
))
