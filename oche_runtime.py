"""Stable update storage contract shared by the launcher and application.

Changes to this file require a runtime image upgrade.
"""
import json
import os
from pathlib import Path
import re
import shutil
import uuid

MODULES = ("autodarts", "autoglow", "ochecore", "oche")
DATA = Path(os.environ.get("OCHE_DATA_DIR", "/app/data"))
ROOT = DATA / "modules"
BUNDLES = Path(os.environ.get("OCHE_BUNDLES_DIR", "/opt/oche-bundles"))
RUNTIME_FILE = Path("/opt/oche-runtime-id")
RUNTIME = os.environ.get("OCHE_RUNTIME_ID", RUNTIME_FILE.read_text().strip() if RUNTIME_FILE.exists() else "oche-runtime-1")


def valid_version(value):
    if not isinstance(value, str) or not re.fullmatch(r"[A-Za-z0-9][A-Za-z0-9._-]{0,99}", value):
        raise ValueError("Invalid release version")
    return value


def read_state(name):
    if name not in MODULES:
        raise ValueError("Unknown module")
    path = ROOT / name / "state.json"
    return json.loads(path.read_text()) if path.exists() else {}


def write_state(name, state):
    path = ROOT / name / "state.json"
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary = path.with_name(uuid.uuid4().hex + ".tmp")
    with temporary.open("w") as output:
        json.dump(state, output)
        output.flush()
        os.fsync(output.fileno())
    os.replace(temporary, path)


def location(name, fallback):
    state = read_state(name)
    if not state.get("active"):
        return Path(fallback)
    return release_path(name, state["active"])


def release_path(name, version):
    return ROOT / name / "versions" / valid_version(version) / RUNTIME


def initialize():
    for name in MODULES:
        source = BUNDLES / name
        if not source.is_dir():
            continue
        state = read_state(name)
        # A pending activation never becomes trusted just because we restarted.
        if state.get("pending"):
            state["active"] = state.get("previous")
            state["pending"] = False
            state["error"] = "Interrupted update rolled back"
            write_state(name, state)
        if state.get("active") and state.get("runtime") == RUNTIME and location(name, source).is_dir():
            continue
        identity = source / "REVISION" if (source / "REVISION").is_file() else source / "VERSION"
        version = valid_version(identity.read_text().strip())
        target = release_path(name, version)
        target.parent.mkdir(parents=True, exist_ok=True)
        if not target.exists():
            stage = target.parent / (".seed-" + uuid.uuid4().hex)
            shutil.copytree(source, stage, symlinks=False)
            os.replace(stage, target)
        write_state(name, {"active": version, "previous": None, "pending": False, "runtime": RUNTIME})
