from pathlib import Path

from app.config import AUTODARTS_DIR
from app.services.process_manager import ManagedProcess, registry

# The Autodarts board-manager binary, installed into the image at build time
# (see Dockerfile) rather than via the host `get.autodarts.io` installer.
AUTODARTS_BIN = Path("/opt/autodarts/autodarts")

_process = registry.register(
    ManagedProcess(
        name="autodarts",
        command=[str(AUTODARTS_BIN)],
        cwd=AUTODARTS_DIR,
    )
)


def get_status() -> dict:
    return {
        "installed": AUTODARTS_BIN.exists(),
        "status": _process.status if AUTODARTS_BIN.exists() else "nofile",
        "pid": _process.pid,
    }


def start() -> bool:
    return _process.start()


def stop() -> bool:
    return _process.stop()


def restart() -> bool:
    return _process.restart()


def logs(lines: int = 200) -> str:
    return _process.tail_log(lines)
