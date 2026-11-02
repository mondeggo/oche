"""Stable update storage contract shared by the launcher and application.

Changes to this file require a runtime image upgrade.

The application decides which modules exist; this contract accepts any valid
module name so new integrations can ship in application updates. Only Oche runs
on the image's Python runtime, so only its releases are stored per runtime. Other
modules are native programs and keep their releases across image upgrades.
"""

import json
import os
import re
import shutil
import uuid
from pathlib import Path

DATA = Path(os.environ.get("OCHE_DATA_DIR", str(Path(__file__).resolve().parent / "data"))).resolve()
ROOT = DATA / "modules"
BUNDLES = Path(os.environ.get("OCHE_BUNDLES_DIR", "/opt/oche-bundles"))
RUNTIME_FILE = Path("/opt/oche-runtime-id")
RUNTIME = os.environ.get(
    "OCHE_RUNTIME_ID", RUNTIME_FILE.read_text().strip() if RUNTIME_FILE.exists() else "oche-runtime-1"
)
RUNTIME_BOUND = frozenset({"oche"})


def valid_module(value):
    if not isinstance(value, str) or not re.fullmatch(r"[a-z][a-z0-9-]{0,39}", value):
        raise ValueError("Unknown module")
    return value


def valid_version(value):
    if not isinstance(value, str) or not re.fullmatch(r"[A-Za-z0-9][A-Za-z0-9._-]{0,99}", value):
        raise ValueError("Invalid release version")
    return value


def read_state(name):
    path = ROOT / valid_module(name) / "state.json"
    return json.loads(path.read_text()) if path.exists() else {}


def write_state(name, state):
    path = ROOT / valid_module(name) / "state.json"
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
    release = ROOT / valid_module(name) / "versions" / valid_version(version)
    return release / RUNTIME if name in RUNTIME_BOUND else release


def _flatten_runtime_copies(name):
    """Move native releases out of the per-runtime folders that older images created."""
    versions = ROOT / name / "versions"
    if name in RUNTIME_BOUND or not versions.is_dir():
        return
    recorded = read_state(name).get("runtime")
    for release in versions.iterdir():
        if release.name.startswith(".") or not release.is_dir():
            continue
        children = list(release.iterdir())
        copies = [
            child for child in children
            if child.is_dir() and (child / "RUNTIME").is_file()
            and (child / "RUNTIME").read_text().strip() == child.name
        ]
        if not copies or len(copies) != len(children):
            continue
        chosen = next(
            (child for preferred in (RUNTIME, recorded) for child in copies if child.name == preferred),
            sorted(copies)[0],
        )
        old = versions / (".runtime-copies-" + uuid.uuid4().hex)
        os.replace(release, old)
        os.replace(old / chosen.name, release)
        shutil.rmtree(old, ignore_errors=True)


def initialize():
    names = {path.name for root in (BUNDLES, ROOT) if root.is_dir() for path in root.iterdir() if path.is_dir()}
    for name in sorted(names):
        try:
            valid_module(name)
        except ValueError:
            continue
        _flatten_runtime_copies(name)
        state = read_state(name)
        # A pending activation never becomes trusted just because we restarted.
        if state.get("pending"):
            state["active"] = state.get("previous")
            state["pending"] = False
            state["error"] = "Interrupted update rolled back"
            write_state(name, state)
        source = BUNDLES / name
        if not source.is_dir():
            continue
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
