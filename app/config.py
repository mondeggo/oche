import json
from pathlib import Path

DATA_DIR = Path(__file__).resolve().parent.parent / "data"
DATA_DIR.mkdir(exist_ok=True)

CONFIG_FILE = DATA_DIR / "oche_config.json"
LOG_DIR = DATA_DIR / "logs"
LOG_DIR.mkdir(exist_ok=True)

AUTODARTS_DIR = DATA_DIR / "autodarts"
AUTODARTS_DIR.mkdir(exist_ok=True)

DEFAULTS = {
    "lang": "en",
    "autostart_autodarts": True,
    "autohide_navbar_on_board": False,
    "autohide_navbar_on_play": False,
    "autohide_navbar_on_autodarts": False,
    "autohide_navbar_on_autoglow": False,
    "autohide_navbar_on_panels": False,
    "show_play_in_navbar": True,
    "show_board_in_navbar": True,
    "show_autodarts_in_navbar": True,
    "show_autoglow_in_navbar": True,
    "show_panels_in_navbar": True,
    "panels": [],
    "autoglow": {
        "esp32_port": None,
        "ws_host": "127.0.0.1",
        "ws_port": 3180,
    },
}


def load_config() -> dict:
    if CONFIG_FILE.exists():
        try:
            with open(CONFIG_FILE, "r") as f:
                data = json.load(f)
            merged = {**DEFAULTS, **data}
            return merged
        except Exception:
            pass
    config = dict(DEFAULTS)
    save_config(config)
    return config


def save_config(config: dict) -> None:
    with open(CONFIG_FILE, "w") as f:
        json.dump(config, f, indent=2)
