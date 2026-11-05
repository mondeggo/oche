"""Oche: your connected darts workspace."""

import tomllib
from importlib.metadata import PackageNotFoundError, version
from pathlib import Path


def _runtime_version() -> str:
    """Use the release, source project or installed package version, in that order."""
    root = Path(__file__).resolve().parent.parent
    version_file = root / "VERSION"
    if version_file.is_file():
        return version_file.read_text(encoding="utf-8").strip()
    project_file = root / "pyproject.toml"
    if project_file.is_file():
        with project_file.open("rb") as source:
            return tomllib.load(source)["project"]["version"]
    try:
        return version("oche")
    except PackageNotFoundError as error:
        raise RuntimeError("Cannot determine the installed Oche version") from error


__version__ = _runtime_version()
