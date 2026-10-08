"""Verified release bundles, serialized background jobs and health rollback."""
import hashlib
import json
import os
from pathlib import Path, PurePosixPath
import platform
import re
import shutil
import tarfile
import tempfile
import threading
import time
import uuid
from concurrent.futures import ThreadPoolExecutor
from urllib.parse import urlparse
from urllib.error import HTTPError
from urllib.request import HTTPSHandler, HTTPRedirectHandler, build_opener

import oche_runtime as store
from app.services.release_metadata import metadata, is_revision
from app.services.upstream_releases import discover_core, discover_autodarts, prepare_core, prepare_autodarts

FEED = os.environ.get("OCHE_UPDATE_FEED", "https://github.com/mondeggo/oche/releases/latest/download/updates.json")
MAX_DOWNLOAD = 1024 * 1024 * 1024
MAX_EXPANDED = 3 * MAX_DOWNLOAD


def https_url(url):
    parsed = urlparse(url)
    if parsed.scheme != "https" or not parsed.hostname or parsed.username or parsed.password:
        raise ValueError("Update sources must use HTTPS without embedded credentials")
    return url


class HTTPSRedirect(HTTPRedirectHandler):
    def redirect_request(self, req, fp, code, msg, headers, newurl):
        https_url(newurl)
        return super().redirect_request(req, fp, code, msg, headers, newurl)


def download(url, destination, limit):
    opener = build_opener(HTTPSHandler(), HTTPSRedirect())
    digest = hashlib.sha256()
    total = 0
    with opener.open(https_url(url), timeout=30) as response, destination.open("wb") as output:
        while block := response.read(1024 * 1024):
            total += len(block)
            if total > limit:
                raise ValueError("Release exceeds download size limit")
            output.write(block)
            digest.update(block)
    return digest.hexdigest()


def extract(archive, destination):
    with tarfile.open(archive, "r:gz") as bundle:
        members = bundle.getmembers()
        if len(members) > 100000 or sum(m.size for m in members) > MAX_EXPANDED:
            raise ValueError("Release exceeds extraction size limit")
        for member in members:
            path = PurePosixPath(member.name)
            if (not member.isfile() and not member.isdir()) or path.is_absolute() or ".." in path.parts or "\\" in member.name or ":" in member.name:
                raise ValueError("Unsafe path or link in release archive")
        bundle.extractall(destination, members=members, filter="data")


class UpdateManager:
    def __init__(self):
        self.lock = threading.Lock()
        self.available = {}
        self.check_errors = {}
        self.job = {"status": "idle", "message": ""}

    def status(self):
        modules = []
        for name in store.MODULES:
            state = store.read_state(name)
            bundled = store.BUNDLES / name / "VERSION"
            versions = store.ROOT / name / "versions"
            bundle_info = metadata(bundled.parent)
            active_info = metadata(store.release_path(name, state["active"]), state["active"]) if state.get("active") else bundle_info
            previous_info = metadata(store.release_path(name, state["previous"]), state["previous"]) if state.get("previous") else {}
            release = self.available.get(name, {})
            available_version = release.get("display_version") or (release.get("version") if not is_revision(release.get("version")) else None)
            active_id = state.get("active") or bundle_info.get("revision") or bundle_info.get("version")
            has_update = bool(release and release["version"] != active_id)
            if release.get("format") in ("ochecore-wheel", "autodarts-archive"):
                current = active_info.get("version")
                if current and re.fullmatch(r"\d+(\.\d+)*", current):
                    has_update = tuple(map(int, available_version.split("."))) > tuple(map(int, current.split(".")))
            modules.append({"name": name, **state,
                            "has_update": has_update, "source": release.get("source", "Oche releases"),
                            "discovery_error": self.check_errors.get(name),
                            "verification": release.get("verification", "Publisher SHA-256"),
                            "runtime_compatible": release.get("runtime", store.RUNTIME) == store.RUNTIME,
                            "active_version": active_info.get("version"), "active_revision": active_info.get("revision"),
                            "bundled_version": bundle_info.get("version"), "bundled_revision": bundle_info.get("revision"),
                            "previous_version": previous_info.get("version"), "previous_revision": previous_info.get("revision"),
                            "available_version": available_version, "available_revision": release.get("revision") or (release.get("version") if is_revision(release.get("version")) else None),
                            "bundled": bundled.read_text().strip() if bundled.exists() else None,
                            "installed": sorted(p.name for p in versions.iterdir() if (p / store.RUNTIME).is_dir() and not p.name.startswith(".")) if versions.exists() else [],
                            "available": self.available.get(name, {}).get("version")})
        return {"enabled": os.environ.get("OCHE_LAUNCHER") == "1", "modules": modules,
                "job": dict(self.job), "runtime": store.RUNTIME, "check_errors": dict(self.check_errors),
                "boot_id": os.environ.get("OCHE_BOOT_ID")}

    def submit(self, action, name=None):
        if action != "check" and os.environ.get("OCHE_LAUNCHER") != "1":
            raise ValueError("Updates require the Docker runtime with the stable launcher")
        if action not in ("check", "update", "rollback", "update-all") or (action in ("update", "rollback") and name not in store.MODULES):
            raise ValueError("Unknown update action or module")
        if not self.lock.acquire(blocking=False):
            raise ValueError("Another update operation is running")
        if any(store.read_state(n).get("pending") for n in store.MODULES):
            self.lock.release()
            raise ValueError("Waiting for an activation health check")
        self.job = {"status": "running", "message": action, "module": name, "action": action, "id": uuid.uuid4().hex}
        threading.Thread(target=self._run, args=(action, name), daemon=True).start()

    def _run(self, action, name):
        try:
            if action == "check":
                self.check()
            elif action == "update":
                self.install(name)
            elif action == "update-all":
                self.check()
                candidates = [m["name"] for m in self.status()["modules"] if m["has_update"] and m["runtime_compatible"]]
                # MODULES keeps Oche last: its restart must not interrupt others.
                for index, module in enumerate(candidates, 1):
                    self.job.update(module=module, current=index, total=len(candidates))
                    self.install(module, refresh=False)
            else:
                previous = store.read_state(name).get("previous")
                if not previous:
                    raise ValueError("No previous release is available")
                self.activate(name, previous)
            self.job = {**self.job, "status": "complete", "message": "Operation complete", "module": name, "action": action}
        except Exception as error:
            self.job = {**self.job, "status": "error", "message": str(error), "action": action}
        finally:
            self.lock.release()

    def check(self):
        self.available = {}
        self.check_errors = {}
        architecture = {"x86_64": "amd64", "amd64": "amd64", "aarch64": "arm64", "arm64": "arm64"}.get(platform.machine().lower(), platform.machine().lower())
        # An unavailable Oche feed must not hide independent upstream releases.
        with ThreadPoolExecutor(max_workers=3) as pool:
            jobs = [(pool.submit(self._check_bundle_feed), ("oche", "autoglow")),
                    (pool.submit(discover_core, download, architecture), ("ochecore",)),
                    (pool.submit(discover_autodarts, download, architecture), ("autodarts",))]
            for future, names in jobs:
                try:
                    result = future.result()
                    if len(names) == 1:
                        self.available[names[0]] = result
                    else:
                        self.available.update(result)
                except Exception as error:
                    self.check_errors.update({name: str(error) for name in names})
        if not self.available and self.check_errors:
            raise ValueError("; ".join(dict.fromkeys(self.check_errors.values())))

    def _check_bundle_feed(self):
        temporary_dir = tempfile.TemporaryDirectory(prefix="oche-release-check-")
        temporary = Path(temporary_dir.name) / "manifest.json"
        try:
            try:
                download(FEED, temporary, 1024 * 1024)
            except HTTPError as error:
                if error.code == 404:
                    raise ValueError(
                        "The update feed is not available (HTTP 404). Publish a GitHub release "
                        "using the Publish application updates workflow, including updates.json "
                        "and its application archives. If releases already exist, check that "
                        "OCHE_UPDATE_FEED points to a publicly accessible manifest. "
                        "Installed applications have not been changed."
                    ) from error
                raise
            manifest = json.loads(temporary.read_text())
            architecture = {"x86_64": "amd64", "amd64": "amd64", "aarch64": "arm64", "arm64": "arm64"}.get(platform.machine().lower(), platform.machine().lower())
            if manifest.get("schema") != 1:
                raise ValueError("Unsupported release manifest")
            releases = manifest["platforms"]["linux-" + architecture]
            candidates = {}
            for name, entry in releases.items():
                if name not in ("oche", "autoglow"):
                    continue
                store.valid_version(entry["version"])
                https_url(entry["url"])
                if not re.fullmatch(r"[a-f0-9]{64}", entry["sha256"]):
                    raise ValueError("Missing or invalid release SHA-256")
                if not isinstance(entry.get("runtime"), str) or not entry["runtime"]:
                    raise ValueError("Release runtime metadata is missing")
                candidates[name] = entry
            return candidates
        finally:
            temporary_dir.cleanup()

    def install(self, name, refresh=True):
        if refresh:
            self.check()
        release = self.available.get(name)
        if not release:
            raise ValueError("No compatible release is published for this module")
        if release.get("runtime", store.RUNTIME) != store.RUNTIME:
            raise ValueError("This package requires a different Docker runtime image. Upgrade the container image before installing it.")
        version = release["version"]
        if release.get("format") and not next(m for m in self.status()["modules"] if m["name"] == name)["has_update"]:
            return
        if store.read_state(name).get("active") == version:
            return
        versions = store.ROOT / name / "versions"
        versions.mkdir(parents=True, exist_ok=True)
        target = store.release_path(name, version)
        target.parent.mkdir(parents=True, exist_ok=True)
        if not target.exists():
            stage = versions / (".download-" + uuid.uuid4().hex)
            stage.mkdir()
            try:
                self.job["message"] = "Downloading and verifying " + name
                archive = stage / (release["filename"] if release.get("format") == "ochecore-wheel" else "release.tar.gz")
                if download(release["url"], archive, MAX_DOWNLOAD) != release["sha256"]:
                    raise ValueError("Release SHA-256 verification failed")
                content = stage / "content"
                content.mkdir()
                if release.get("format") == "ochecore-wheel":
                    prepare_core(archive, content, version)
                    (content / "RUNTIME").write_text(store.RUNTIME)
                elif release.get("format") == "autodarts-archive":
                    prepare_autodarts(archive, content, version, extract)
                    (content / "RUNTIME").write_text(store.RUNTIME)
                else:
                    extract(archive, content)
                identity = content / "REVISION" if (content / "REVISION").is_file() else content / "VERSION"
                if identity.read_text().strip() != version:
                    raise ValueError("Bundle version does not match release metadata")
                if release.get("display_version") and (content / "VERSION").read_text().strip() != release["display_version"]:
                    raise ValueError("Application version does not match release metadata")
                if (content / "RUNTIME").read_text().strip() != store.RUNTIME:
                    raise ValueError("Bundle requires a different Docker runtime")
                required = {"oche": "app/server.py", "autodarts": "autodarts",
                            "autoglow": "server.py", "ochecore": ".venv/bin/python"}[name]
                if not (content / required).is_file():
                    raise ValueError("Bundle is missing its application entry point")
                os.replace(content, target)
            finally:
                shutil.rmtree(stage, ignore_errors=True)
        self.activate(name, version)

    def activate(self, name, version):
        version = store.valid_version(version)
        if not store.release_path(name, version).is_dir():
            raise ValueError("Requested release is not installed")
        old = store.read_state(name)
        if old.get("active") == version:
            return
        state = {**old, "active": version, "previous": old.get("active"), "pending": True, "error": None}
        self.job["message"] = "Activating and checking " + name
        if name == "oche":
            store.write_state(name, state)
            request = store.DATA / "state" / "restart-oche"
            request.parent.mkdir(parents=True, exist_ok=True)
            request.touch()
            return
        from app.services import autodarts, autoglow, ochecore
        service = {"autodarts": autodarts, "autoglow": autoglow, "ochecore": ochecore}[name]
        running = service.get_status()["status"] == "running"
        service.stop()
        try:
            store.write_state(name, state)
            service.start()
            port = {"autodarts": 3180, "autoglow": autoglow.PORT, "ochecore": ochecore.PORT}[name]
            from urllib.request import urlopen
            deadline = time.monotonic() + 60
            successes = 0
            while time.monotonic() < deadline:
                if service.get_status()["status"] != "running":
                    raise RuntimeError("Updated process exited before becoming healthy")
                try:
                    with urlopen(f"http://127.0.0.1:{port}/", timeout=2) as response:
                        if response.status < 400:
                            successes += 1
                except Exception:
                    successes = 0
                if successes >= 3:
                    break
                time.sleep(1)
            else:
                raise RuntimeError("Updated process did not pass its HTTP health check")
            if not running:
                service.stop()
            state["pending"] = False
            store.write_state(name, state)
        except Exception:
            service.stop()
            store.write_state(name, old)
            if running:
                service.start()
            raise


manager = UpdateManager()
