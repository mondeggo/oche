import json
import os
from pathlib import Path
import sys

from app.config import DATA_DIR
from app.services.process_manager import ManagedProcess, registry

SOURCE = Path(os.environ.get("OCHE_AUTOGLOW_SOURCE", "/opt/autoglow"))
PORT = int(os.environ.get("OCHE_AUTOGLOW_PORT", "8080"))
ROOT = Path(__file__).resolve().parents[2]
_processes = [
    registry.register(ManagedProcess(
        name=f"autoglow-{role}",
        command=[sys.executable, "-u", "-m", "app.autoglow_runner", role],
        cwd=ROOT,
    )) for role in ("web", "listener")
]


def installed() -> bool:
    return all((SOURCE / name).is_file() for name in
               ("web_server.py", "autodarts_wled_mini.py"))


def start() -> bool:
    if not installed():
        return False
    # Discard connection state left by a previous listener.
    if _processes[1].status != "running":
        (DATA_DIR / "autoglow" / ".sync_status.json").unlink(missing_ok=True)
    started = False
    try:
        for process in _processes:
            started = process.start() or started
    except RuntimeError:
        stop()
        raise
    return started


def stop() -> None:
    for process in reversed(_processes):
        process.stop()


def get_status() -> dict:
    running = all(p.status == "running" for p in _processes)
    sync = {}
    if running:
        try:
            sync = json.loads((DATA_DIR / "autoglow" / ".sync_status.json").read_text())
        except (OSError, ValueError):
            pass
    return {
        "installed": installed(),
        "status": "running" if running else "stopped",
        "web_port": PORT,
        "autodarts_connected": bool(sync.get("local") or sync.get("online")),
        "processes": {p.name: p.status for p in _processes},
    }


def logs() -> dict:
    return {p.name: p.tail_log() for p in _processes}
