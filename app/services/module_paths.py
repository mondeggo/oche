"""Resolve replaceable application code without moving user data."""
import os
from pathlib import Path


def source(name, fallback):
    # Native development keeps existing source overrides and needs no launcher.
    if not os.environ.get("OCHE_LAUNCHER"):
        return Path(fallback)
    from oche_runtime import location
    return location(name, fallback)
