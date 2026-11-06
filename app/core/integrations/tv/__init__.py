"""TV view: a full-screen display that follows every local game, and a remote for a phone or tablet."""

from pathlib import Path

from app.core.integrations.registry import Integration, Page, register

from .routes import pages

HERE = Path(__file__).parent

integration = register(Integration(
    id="tv",
    name="TV view",
    description="A full-screen game view for a TV, and a remote to run games from a phone or tablet.",
    enabled_by_default=True,
    order=50,
    pages=(Page("tv", "TV view", "/integrations/tv", "tv"),),
    routers=(pages,),
    templates=HERE / "templates",
    static=HERE / "static",
))
