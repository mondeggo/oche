from pathlib import Path

from fastapi.templating import Jinja2Templates
from jinja2 import BaseLoader, ChoiceLoader, FileSystemLoader, TemplateNotFound

from app import __version__
from app.config import load_config
from app.core.integrations import registry
from app.i18n import LANGUAGES, catalogue, translate
from app.navigation import HOME_ROUTES


class IntegrationTemplates(BaseLoader):
    """Serve "<integration>/<file>.html" from the integration's own templates folder.

    Integrations register themselves on import, so their folders are looked up
    only when a template is first requested.
    """

    def get_source(self, environment, template):
        name, _, rest = template.partition("/")
        integration = registry.get(name) if rest else None
        if not integration or not integration.templates:
            raise TemplateNotFound(template)
        return FileSystemLoader(str(integration.templates)).get_source(environment, rest)


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
    integration_pages = {
        link.href: link.page for item in registry.integrations() for link in (*item.pages, *item.play)
    }
    page = (
        "home"
        if path in HOME_ROUTES
        else integration_pages[path]
        if path in integration_pages
        else "local"
        if path == "/local"
        else "panels"
        if path.startswith("/panels/")
        else (service if service in {"system", "updates"} else "system")
        if path == "/supervisor"
        else "settings"
    )
    pages = {name: config.get(f"show_page_{name}", True) for name in ("cameras", "system", "updates")}
    switched_on = registry.active()
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
            "system": pages["system"] or pages["updates"],
            "pages": pages,
            "panels": config.get("show_panels_in_navbar", True),
        },
        "nav_integrations": {
            name: _integration_enabled(runtime, name) for name in ("wled", "caller")
        },
        "nav_profiles": _profiles(runtime),
        # What switched-on integrations add: buttons below Play, and pages under
        # Integrations. Every registered entry renders, hidden when off, so the
        # sidebar can show it again without a reload.
        "integrations_registry": registry.integrations(),
        "nav_registry": [
            {
                "id": item.id,
                "enabled": item in switched_on,
                "release": bool(item.release),
                # Buttons below Play also follow the "Play" sidebar switch in Settings.
                "play": [(button, item in switched_on and config.get("show_play_in_navbar", True)) for button in item.play],
                "pages": [
                    (link, item in switched_on and (not link.setting or config.get(link.setting, True)))
                    for link in item.pages
                ],
            }
            for item in registry.integrations()
        ],
        "ui_theme": ui.config.theme if ui else "dark",
        "sidebar_page": page,
        "sidebar_collapsed": page != "settings" and config.get(f"autohide_navbar_on_{page}", False),
    }


templates = Jinja2Templates(
    directory=str(Path(__file__).parent / "templates"), context_processors=[navigation_context]
)
templates.env.loader = ChoiceLoader([templates.env.loader, IntegrationTemplates()])
