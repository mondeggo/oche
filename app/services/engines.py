"""Detection engines. Autodarts and OcheCore read the same cameras, so one runs at a time."""

from app import config as settings
from app.services import autodarts, ochecore

ENGINES = {"autodarts": autodarts, "ochecore": ochecore}
DEFAULT = "autodarts"


def active() -> str:
    name = settings.load_config().get("detection_engine")
    return name if name in ENGINES else DEFAULT


def status() -> dict:
    current = active()
    return {
        "active": current,
        "engines": [{"name": name, "active": name == current, **service.get_status()} for name, service in ENGINES.items()],
    }


def use(name: str) -> dict:
    """Make an engine the active one: stop the other, then start it if it is installed."""
    if name not in ENGINES:
        raise ValueError("Unknown detection engine")
    config = settings.load_config()
    if config.get("detection_engine") != name:
        config["detection_engine"] = name
        settings.save_config(config)
    for other, service in ENGINES.items():
        if other != name:
            service.stop()
    if ENGINES[name].get_status()["installed"]:
        ENGINES[name].start()
    return status()


def stop(name: str) -> dict:
    if name not in ENGINES:
        raise ValueError("Unknown detection engine")
    ENGINES[name].stop()
    return status()


def start_on_boot() -> None:
    # The saved setting keeps its original name; it now starts whichever engine is active.
    service = ENGINES[active()]
    if settings.load_config().get("autostart_autodarts") and service.get_status()["installed"]:
        service.start()


def stop_all() -> None:
    for service in ENGINES.values():
        service.stop()
