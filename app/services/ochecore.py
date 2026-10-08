"""Run the bundled OcheCore gateway with its own dependencies and saved settings."""

import os
from pathlib import Path

from app.config import DATA_DIR
from app.services.process_manager import ManagedProcess, registry
from app.services.module_paths import source

SOURCE = Path(os.environ.get("OCHE_OCHECORE_SOURCE", "/opt/ochecore"))
PORT = int(os.environ.get("OCHE_OCHECORE_PORT", "9180"))
DATA = DATA_DIR / "ochecore"
PYTHON = SOURCE / ".venv" / ("Scripts/python.exe" if os.name == "nt" else "bin/python")

_process = registry.register(ManagedProcess(
    name="ochecore",
    command=[str(PYTHON), "-u", "-m", "ochecore.main", "serve"],
    cwd=DATA,
))


def installed() -> bool:
    global SOURCE, PYTHON
    if os.environ.get("OCHE_LAUNCHER") == "1":
        SOURCE = source("ochecore", SOURCE)
        PYTHON = SOURCE / ".venv" / ("Scripts/python.exe" if os.name == "nt" else "bin/python")
        _process.command = [str(PYTHON), "-u", "-m", "ochecore.main", "serve"]
    return all(path.is_file() for path in (
        PYTHON, SOURCE / "src/ochecore/main.py", SOURCE / "src/ochecore/static/index.html",
    ))


def start() -> bool:
    if not installed():
        return False
    DATA.mkdir(parents=True, exist_ok=True, mode=0o700)
    # Bind only on loopback: Oche provides the browser connection, including TLS.
    # Running from the data directory avoids loading the source checkout's
    # development OAuth client; connection settings remain editable in the UI.
    _process.env = {
        **os.environ,
        "OCHECORE_HOST": "127.0.0.1",
        "OCHECORE_PORT": str(PORT),
        "OCHECORE_DATA_DIR": str(DATA.resolve()),
        "OCHECORE_UI_ENABLED": "true",
        "OCHECORE_UI_EMBEDDED": "true",
    }
    return _process.start()


def stop() -> bool:
    return _process.stop()


def restart() -> bool:
    stop()
    return start()


def get_status() -> dict:
    present = installed()
    state = _process.status if present else "nofile"
    version_file = SOURCE / "VERSION"
    return {
        "installed": present,
        "status": state,
        "pid": _process.pid,
        "version": version_file.read_text().strip() if version_file.is_file() else None,
        "web_port": PORT,
        "processes": {"ochecore": state},
        "pids": {"ochecore": _process.pid},
    }


def logs() -> dict:
    return {"ochecore": _process.tail_log()}


def clear_logs() -> None:
    _process.clear_log()
