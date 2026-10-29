"""Read application versions without importing or executing module code."""

from pathlib import Path
import re
import tomllib


def is_revision(value):
    return bool(value and re.fullmatch(r"[0-9a-fA-F]{40,64}", value))


def read_text(path):
    try:
        return path.read_text().strip()
    except OSError:
        return None


def application_version(source):
    source = Path(source)
    version = read_text(source / "VERSION")
    if version and not is_revision(version):
        return version
    try:
        value = tomllib.loads((source / "pyproject.toml").read_text())["project"]["version"]
        if isinstance(value, str) and not is_revision(value):
            return value
    except (OSError, ValueError, KeyError, TypeError):
        pass
    return None


def metadata(source, identifier=None):
    source = Path(source)
    return {
        "version": application_version(source)
        or (identifier if identifier and not is_revision(identifier) else None),
        "revision": read_text(source / "REVISION") or (identifier if is_revision(identifier) else None),
    }
