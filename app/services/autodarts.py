import os
from pathlib import Path

from app.config import AUTODARTS_DIR
from app.services.process_manager import ManagedProcess, registry

# The Autodarts board-manager binary, installed into the image at build time
# (see Dockerfile) rather than via the host `get.autodarts.io` installer.
AUTODARTS_BIN = Path("/opt/autodarts/autodarts")
HOST_CONFIG_DIR = Path("/app/host-autodarts")
CONFIG_LINK = Path("/home/oche/.config/autodarts")


def configure_config_source() -> None:
    reuse = os.environ.get("OCHE_REUSE_AUTODARTS_CONFIG", "true").strip().lower()
    if reuse not in ("true", "false"):
        raise RuntimeError("OCHE_REUSE_AUTODARTS_CONFIG must be true or false.")
    target = (HOST_CONFIG_DIR if reuse == "true" and
              (HOST_CONFIG_DIR / "config.toml").is_file() else AUTODARTS_DIR)
    # Only manage the symlink supplied by our image; leave native installs alone.
    if CONFIG_LINK.is_symlink() and CONFIG_LINK.readlink() != target:
        CONFIG_LINK.unlink()
        CONFIG_LINK.symlink_to(target, target_is_directory=True)

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
    configure_config_source()
    return _process.start()


def stop() -> bool:
    return _process.stop()


def restart() -> bool:
    configure_config_source()
    return _process.restart()


def logs(lines: int = 200) -> str:
    return _process.tail_log(lines)
