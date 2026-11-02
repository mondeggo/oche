"""Resolve replaceable application code without moving user data."""
import os
from pathlib import Path


def source(name, fallback):
    # Development runs Oche from the working tree, but still uses installed modules.
    if name == "oche" and not os.environ.get("OCHE_LAUNCHER"):
        return Path(fallback)
    from oche_runtime import location
    return location(name, fallback)
