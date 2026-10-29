import json
import os
from pathlib import Path

DATA_DIR = Path(os.environ.get("OCHE_DATA_DIR", str(Path(__file__).resolve().parent.parent / "data")))
DATA_DIR.mkdir(parents=True, exist_ok=True)

CONFIG_FILE = DATA_DIR / "settings.json"
LOG_DIR = DATA_DIR / "logs"
LOG_DIR.mkdir(exist_ok=True)

AUTODARTS_DIR = DATA_DIR / "autodarts"
AUTODARTS_DIR.mkdir(exist_ok=True)

DEFAULTS = {
    "https_enabled": False,
    "autostart_autodarts": True,
    "panels": [],
    "autohide_navbar_on_home": False,
    "autohide_navbar_on_play": True,
    "autohide_navbar_on_autodarts": False,
    "autohide_navbar_on_cameras": False,
    "autohide_navbar_on_system": False,
    "autohide_navbar_on_updates": False,
    "autohide_navbar_on_panels": True,
    "show_play_in_navbar": True,
    "show_panels_in_navbar": True,
    "show_page_autodarts": True,
    "show_page_cameras": True,
    "show_page_system": True,
    "show_page_updates": True,
    "metrics_history": {"enabled": True, "retention_minutes": 60, "sample_seconds": 30},
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
