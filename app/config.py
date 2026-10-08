import json
import os
from pathlib import Path

DATA_DIR = Path(os.environ.get("OCHE_DATA_DIR", str(Path(__file__).resolve().parent.parent / "data")))
DATA_DIR.mkdir(parents=True, exist_ok=True)

CONFIG_FILE = DATA_DIR / "oche_config.json"
LOG_DIR = DATA_DIR / "logs"
LOG_DIR.mkdir(exist_ok=True)

AUTODARTS_DIR = DATA_DIR / "autodarts"
AUTODARTS_DIR.mkdir(exist_ok=True)

DEFAULTS = {
    "lang": "en",
    "https_enabled": False,
    "autostart_autodarts": True,
    "autostart_autoglow": True,
    "autostart_ochecore": True,
    "autohide_navbar_on_play": False,
    "autohide_navbar_on_autodarts": False,
    "autohide_navbar_on_autoglow": False,
    "autohide_navbar_on_ochecore": False,
    "autohide_navbar_on_panels": False,
    "show_play_in_navbar": True,
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
            merged.pop("show_board_in_navbar", None)
            merged.pop("autohide_navbar_on_board", None)
            merged.pop("show_ochecore_in_navbar", None)
            return merged
        except Exception:
            pass
    config = dict(DEFAULTS)
    save_config(config)
    return config


def save_config(config: dict) -> None:
    with open(CONFIG_FILE, "w") as f:
        json.dump(config, f, indent=2)
