"""OcheCore: the offline Rust board service, managed like Autodarts."""

from pathlib import Path

from app.core.integrations.registry import Engine, Health, Integration, Page, Release, register

from . import process, release
from .routes import pages, router
from .wake import clear_stale_visit, hold, wake

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
    # OcheCore serves the Autodarts board API (/api/state, /api/events) on its own port.
    # OcheCore closes its cameras after a standby time; a game wakes them, resets a
    # visit left from before the game and waits for a lit, empty board.
    engine=Engine(label="OcheCore", scores=True, wake=wake, prepare=clear_stale_visit, hold=hold),
    health=(Health("ochecore", "OcheCore", "/integrations/ochecore", "/static/js/health.mjs", 20),),
    routers=(router, pages),
    templates=HERE / "templates",
))
