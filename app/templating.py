from fastapi.templating import Jinja2Templates

from app.config import load_config


def navigation_context(request):
    config = load_config()
    return {
        "nav_panels": config.get("panels", []),
        "nav_visibility": {
            "play": config.get("show_play_in_navbar", True),
            "board": config.get("show_board_in_navbar", True),
            "autodarts": config.get("show_autodarts_in_navbar", True),
            "autoglow": config.get("show_autoglow_in_navbar", True),
            "panels": config.get("show_panels_in_navbar", True),
        },
    }


templates = Jinja2Templates(
    directory="app/templates", context_processors=[navigation_context]
)
