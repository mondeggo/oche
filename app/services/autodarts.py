import os
from pathlib import Path

from app.config import AUTODARTS_DIR
from app.services.module_paths import source
from app.services.process_manager import ManagedProcess, registry

# The v2 headless binary lives in the module store (data/modules/autodarts). The
# installer adds it, and Updates installs and updates it. This path is only a
# fallback for development machines that provide their own copy.
AUTODARTS_BIN = Path("/opt/autodarts/autodarts")
NOT_INSTALLED = (
    "Autodarts is not installed. Install it from System → Updates, "
    "or run ./oche.sh autodarts in the Oche folder."
)


_process = registry.register(
    ManagedProcess(
        name="autodarts",
        command=[str(AUTODARTS_BIN), "run"],
        cwd=AUTODARTS_DIR,
    )
)


def get_status() -> dict:
    refresh_source()
    version_file = AUTODARTS_BIN.parent / "VERSION"
    installed = AUTODARTS_BIN.exists()
    unavailable_reason = None
    if not installed:
        unavailable_reason = (
            "Native Windows development does not include Autodarts. "
            "Use the Linux Docker image for local detection and camera setup."
            if os.name == "nt" else NOT_INSTALLED
        )
    return {
        "version": version_file.read_text().strip() if version_file.is_file() else None,
        "headless": True,
        "installed": installed,
        "status": _process.status if installed else "nofile",
        "unavailable_reason": unavailable_reason,
        "pid": _process.pid,
    }


def start() -> bool:
    refresh_source()
    return _process.start()


def stop() -> bool:
    return _process.stop()


def restart() -> bool:
    stop()
    return start()


def refresh_source():
    global AUTODARTS_BIN
    AUTODARTS_BIN = source("autodarts", AUTODARTS_BIN.parent) / "autodarts"
    _process.command = [str(AUTODARTS_BIN), "run"]


def logs(lines: int = 200) -> str:
    return _process.tail_log(lines)


def clear_logs() -> None:
    _process.clear_log()
