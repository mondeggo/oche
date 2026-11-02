"""OcheCore, the offline Rust board service, supervised like Autodarts.

Its release lives in the module store (data/modules/ochecore) and its settings
and calibration in data/ochecore/board.toml.
"""

import os
from pathlib import Path

from app.config import DATA_DIR
from app.services.module_paths import source
from app.services.process_manager import ManagedProcess, registry

PORT = 5180
HOME = DATA_DIR / "ochecore"
CONFIG = HOME / "board.toml"
# Only for development machines that provide their own copy.
FALLBACK = Path("/opt/ochecore")
NOT_INSTALLED = "OcheCore is not installed. Install it from Integrations or System → Updates."

_process = registry.register(ManagedProcess(name="ochecore", command=[], cwd=HOME))


def binary() -> Path:
    return source("ochecore", FALLBACK) / "oche-core"


def refresh_source():
    HOME.mkdir(parents=True, exist_ok=True)
    # A first start saves default settings that listen on the LAN, like Autodarts
    # on 3180, so its browser page opens from other devices; later starts reuse them.
    _process.command = [
        str(binary()), "--config", str(CONFIG), "run", "--init-bind", "0.0.0.0", "--log", "info",
    ]


def get_status() -> dict:
    refresh_source()
    executable = binary()
    installed = executable.is_file()
    version_file = executable.parent / "VERSION"
    return {
        "version": version_file.read_text().strip() if version_file.is_file() else None,
        "installed": installed,
        "status": _process.status if installed else "nofile",
        "unavailable_reason": None if installed else (
            "OcheCore runs in the Linux Docker image, not native Windows development."
            if os.name == "nt" else NOT_INSTALLED
        ),
        "pid": _process.pid,
        "port": PORT,
    }


def start() -> bool:
    refresh_source()
    return _process.start()


def stop() -> bool:
    return _process.stop()


def restart() -> bool:
    stop()
    return start()


def logs(lines: int = 200) -> str:
    return _process.tail_log(lines)


def clear_logs() -> None:
    _process.clear_log()
