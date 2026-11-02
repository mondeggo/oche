from pathlib import Path

from fastapi.templating import Jinja2Templates

from app import __version__
from app.config import load_config
from app.i18n import LANGUAGES, catalogue, translate
from app.navigation import HOME_ROUTES


def _integration_enabled(runtime, name):
    try:
        return bool(getattr(runtime, name).config.enabled)
    except AttributeError:
        return False


def _profiles(runtime):
    try:
        return runtime.profiles.summary()
    except AttributeError:
        return {"active_profile": None, "profiles": []}


def navigation_context(request):
    config = load_config()
    ui = getattr(request.app.state, "ui", None)
    runtime = getattr(request.app.state, "runtime", None)
    path = request.url.path
    service = request.query_params.get("service")
    page = (
        "home"
        if path in HOME_ROUTES
        else "cameras"
        if path == "/integrations/cameras"
        else "play"
        if path == "/play"
        else "local"
        if path == "/local"
        else "panels"
        if path.startswith("/panels/")
        else (service if service in {"cameras", "system", "updates"} else "autodarts")
        if path == "/supervisor"
        else "settings"
    )
    pages = {
        name: config.get(f"show_page_{name}", True) for name in ("autodarts", "cameras", "system", "updates")
    }
    language = ui.config.language if ui else "en"
    return {
        "_": lambda message, **values: translate(message, language, **values),
        "ui_language": language,
        "ui_languages": LANGUAGES,
        "translations": catalogue(language),
        "app_version": __version__,
        "nav_panels": config.get("panels", []),
        "nav_visibility": {
            "play": config.get("show_play_in_navbar", True),
            "system": pages["autodarts"] or pages["system"] or pages["updates"],
            "pages": pages,
            "panels": config.get("show_panels_in_navbar", True),
        },
        "nav_integrations": {
            name: _integration_enabled(runtime, name) for name in ("wled", "caller")
        },
        "nav_profiles": _profiles(runtime),
        "ui_theme": ui.config.theme if ui else "dark",
        "sidebar_page": page,
        "sidebar_collapsed": page != "settings" and config.get(f"autohide_navbar_on_{page}", False),
    }


templates = Jinja2Templates(
    directory=str(Path(__file__).parent / "templates"), context_processors=[navigation_context]
)
