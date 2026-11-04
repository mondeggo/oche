"""Public routes backed by the retained Home workspace."""

from app.core.integrations import registry

# Home's own pages. Integrations add theirs as views; see home_routes().
HOME_ROUTES = {
    "/integrations": "integrations",
    "/integrations/wled": "wled",
    "/integrations/visual-wled": "wled2",
    "/integrations/caller": "caller",
    "/profiles": "profiles",
    "/events": "events",
}


def home_routes() -> dict[str, str]:
    """Home's pages plus every registered integration view, switched on or not."""
    return {**HOME_ROUTES, **{view.href: view.id for item in registry.integrations() for view in item.views}}


def health_rows(active_engine: str) -> list[dict]:
    """Only enabled integrations contribute indicators; manual is the fallback."""
    rows = [vars(row) for item in registry.active() for row in item.health]
    if active_engine == "manual":
        rows.append({"id": "manual", "label": "Manual", "href": "/local", "module": None, "order": 0})
    return sorted(rows, key=lambda row: row["order"])
