import os
from pathlib import Path

from app.config import AUTODARTS_DIR
from app.services.module_paths import source
from app.services.process_manager import ManagedProcess, registry

# The v2 headless binary, installed into the image at build time.
AUTODARTS_BIN = Path("/opt/autodarts/autodarts")


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
            if os.name == "nt" else
            "The Autodarts binary is missing. Use an Oche container image that includes detection."
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
