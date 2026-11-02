"""Read application versions without importing or executing module code."""

import re
import shutil
import tomllib
import uuid
from pathlib import Path


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


def flatten_single_folder(content, executable, label):
    """Move the archive's single top folder up, so the executable sits in content."""
    if (content / executable).is_file():
        return
    roots = list(content.iterdir())
    if len(roots) != 1 or not roots[0].is_dir() or not (roots[0] / executable).is_file():
        raise ValueError(f"{label} archive is missing its executable")
    # The wrapper directory may itself have the executable's name. Move it
    # aside before flattening.
    root = content.parent / ("unpacked-" + uuid.uuid4().hex)
    roots[0].rename(root)
    for child in root.iterdir():
        shutil.move(str(child), str(content / child.name))
    root.rmdir()
