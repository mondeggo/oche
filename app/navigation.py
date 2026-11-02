"""Public routes backed by the retained Home workspace."""

from app.core.integrations import registry

# Home's own pages. Integrations add theirs as views; see home_routes().
HOME_ROUTES = {
    "/": "overview",
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
