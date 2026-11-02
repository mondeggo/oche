"""Vision engines that integrations register. They share the cameras, so one runs at a time.

Play chooses the engine (or manual darts); starting an engine from its own page
chooses it too. Only integrations that are switched on offer an engine.
"""

from app import config as settings
from app.core.integrations import registry

MANUAL = "manual"


def available() -> dict:
    """Engines of the integrations that are switched on, by integration id."""
    return {item.id: item for item in registry.active() if item.engine and item.process}


def chosen() -> str:
    """The saved choice, falling back to the first engine that can score, else manual."""
    engines = available()
    name = settings.load_config().get("detection_engine")
    if name == MANUAL or name in engines:
        return name
    return next((key for key, item in engines.items() if item.engine.scores), MANUAL)


def status() -> dict:
    current = chosen()
    entries = []
    for item in registry.integrations():
        if not (item.engine and item.process):
            continue
        entries.append({
            "name": item.id,
            "label": item.engine.label,
            "enabled": item.enabled,
            "scores": item.engine.scores,
            "note": item.engine.note,
            "active": item.id == current,
            "port": item.port,
            **item.process.get_status(),
        })
    return {"active": current, "engines": entries}


def use(name: str) -> dict:
    """Choose an engine: stop the others, then start it if installed. Manual stops nothing."""
    engines = available()
    if name != MANUAL and name not in engines:
        raise ValueError("Unknown or switched-off vision engine")
    config = settings.load_config()
    if config.get("detection_engine") != name:
        config["detection_engine"] = name
        settings.save_config(config)
    if name == MANUAL:
        return status()
    for item in registry.integrations():
        if item.id != name and item.engine and item.process:
            item.process.stop()
    process = engines[name].process
    if process.get_status()["installed"]:
        process.start()
    return status()


def stop(name: str) -> dict:
    item = registry.get(name)
    if not (item and item.engine and item.process):
        raise ValueError("Unknown vision engine")
    item.process.stop()
    return status()


def switch_off(name: str) -> None:
    """An integration switched off stops its engine and gives up the Play choice."""
    item = registry.get(name)
    if item and item.engine and item.process:
        item.process.stop()
    config = settings.load_config()
    if config.get("detection_engine") == name:
        config.pop("detection_engine")
        settings.save_config(config)


def process_status() -> dict:
    """The running state of the chosen engine, for the local detector readiness check."""
    item = available().get(chosen())
    return item.process.get_status() if item else {"status": "stopped", "installed": False}


def start_on_boot() -> None:
    # The saved setting keeps its original name; it starts whichever engine Play uses.
    item = available().get(chosen())
    if item and settings.load_config().get("autostart_autodarts") and item.process.get_status()["installed"]:
        item.process.start()


def stop_all() -> None:
    for item in registry.integrations():
        if item.process:
            item.process.stop()
