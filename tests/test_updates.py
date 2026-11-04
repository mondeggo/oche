"""Release validation must finish before the launcher switches the application."""

import dataclasses
import hashlib
import io
import tarfile
from unittest.mock import patch

import pytest

import oche_runtime as store
from app.core.integrations import registry
from app.services.updates import UpdateManager, extract


@pytest.fixture
def manager(tmp_path, monkeypatch):
    for key, value in {
        "ROOT": tmp_path / "modules",
        "DATA": tmp_path,
        "BUNDLES": tmp_path / "bundles",
        "RUNTIME": "test-runtime",
    }.items():
        monkeypatch.setattr(store, key, value)
    bundle = store.BUNDLES / "oche"
    bundle.mkdir(parents=True)
    (bundle / "VERSION").write_text("2.0.0")
    (bundle / "app/core").mkdir(parents=True)
    (bundle / "app/core/runtime.py").write_text("# bundled")
    store.initialize()
    return UpdateManager()


def release(tmp_path, manager, *, integrated=True, checksum=True, runtime="test-runtime"):
    stream = io.BytesIO()
    members = {"VERSION": "2.0.1", "RUNTIME": runtime, "app/server.py": "# candidate"}
    if integrated:
        members["app/core/runtime.py"] = "# integrated"
    with tarfile.open(fileobj=stream, mode="w:gz") as archive:
        for name, content in members.items():
            payload = content.encode()
            info = tarfile.TarInfo(name)
            info.size = len(payload)
            archive.addfile(info, io.BytesIO(payload))
    blob = stream.getvalue()
    digest = hashlib.sha256(blob).hexdigest()
    manager.available = {
        "oche": {
            "version": "2.0.1",
            "runtime": runtime,
            "url": "https://releases.test/oche-test.tar.gz",
            "sha256": digest if checksum else "0" * 64,
        }
    }

    def download(url, destination, limit):
        destination.write_bytes(blob)
        return digest

    return patch("app.services.updates.download", side_effect=download)


def test_complete_application_is_staged_atomically_before_restart(manager, tmp_path):
    with release(tmp_path, manager):
        manager.install("oche", refresh=False)
    state = store.read_state("oche")
    assert state["active"] == "2.0.1" and state["previous"] == "2.0.0" and state["pending"]
    assert (store.location("oche", "unused") / "app/core/runtime.py").is_file()
    assert (store.DATA / "state/restart-oche").is_file()
    # An interruption never promotes an unverified candidate on the next container start.
    store.initialize()
    assert store.read_state("oche")["active"] == "2.0.0"


@pytest.mark.parametrize(
    "options,reason",
    [
        ({"integrated": False}, "integrated darts"),
        ({"checksum": False}, "SHA-256"),
        ({"runtime": "v1-runtime"}, "Release incompatible with this runtime"),
    ],
)
def test_invalid_release_preserves_active_application(manager, tmp_path, options, reason):
    with release(tmp_path, manager, **options), pytest.raises(ValueError, match=reason):
        manager.install("oche", refresh=False)
    assert store.read_state("oche")["active"] == "2.0.0"
    assert not (store.DATA / "state/restart-oche").exists()


@pytest.mark.parametrize(
    "name,kind",
    [
        ("../outside", tarfile.REGTYPE),
        ("/outside", tarfile.REGTYPE),
        ("link", tarfile.SYMTYPE),
        ("hardlink", tarfile.LNKTYPE),
    ],
)
def test_archive_cannot_escape_its_staging_directory(tmp_path, name, kind):
    path = tmp_path / "unsafe.tar.gz"
    with tarfile.open(path, "w:gz") as archive:
        member = tarfile.TarInfo(name)
        member.type, member.linkname = kind, "../../outside"
        archive.addfile(member, io.BytesIO())
    with pytest.raises(ValueError, match="Unsafe"):
        extract(path, tmp_path / "content")


def offline_autodarts(entry):
    """The Autodarts integration, with release discovery answered by the test."""
    autodarts = registry.get("autodarts")
    release = dataclasses.replace(autodarts.release, discover=lambda *_: entry)
    return patch.dict(registry.REGISTRY, {"autodarts": dataclasses.replace(autodarts, release=release)})


def autodarts_release(version="2.1.0"):
    stream = io.BytesIO()
    with tarfile.open(fileobj=stream, mode="w:gz") as archive:
        info = tarfile.TarInfo("autodarts/autodarts")
        payload = b"#!/bin/sh\n"
        info.size, info.mode = len(payload), 0o755
        archive.addfile(info, io.BytesIO(payload))
    blob = stream.getvalue()
    digest = hashlib.sha256(blob).hexdigest()
    entry = {
        "version": version,
        "display_version": version,
        "format": "autodarts-archive",
        "url": f"https://releases.test/autodarts_{version}.tar.gz",
        "sha256": digest,
    }

    def download(url, destination, limit):
        destination.write_bytes(blob)
        return digest

    return entry, patch("app.services.updates.download", side_effect=download)


def test_missing_autodarts_installs_without_starting_and_offers_install(manager):
    entry, downloads = autodarts_release()
    manager.available = {"autodarts": entry}
    module = next(m for m in manager.status()["modules"] if m["name"] == "autodarts")
    # A skipped install is offered on its row but never counted as an update.
    assert not module["present"] and module["installable"] and not module["has_update"]
    with downloads, offline_autodarts(entry), \
            patch("app.core.integrations.autodarts.process.start") as start:
        assert manager.install_missing("autodarts") == "2.1.0"
        assert manager.install_missing("autodarts") is None
    start.assert_not_called()
    assert store.read_state("autodarts") == {"active": "2.1.0", "previous": None, "pending": False}
    assert (store.location("autodarts", "unused") / "autodarts").is_file()
    module = next(m for m in manager.status()["modules"] if m["name"] == "autodarts")
    assert module["present"] and not module["installable"] and module["installed"] == ["2.1.0"]


def test_native_module_releases_survive_an_image_upgrade(manager, monkeypatch):
    entry, downloads = autodarts_release()
    with downloads, offline_autodarts(entry):
        manager.install_missing("autodarts")
    monkeypatch.setattr(store, "RUNTIME", "next-runtime")
    store.initialize()
    assert store.read_state("autodarts")["active"] == "2.1.0"
    assert (store.location("autodarts", "unused") / "autodarts").is_file()


def test_runtime_copies_from_older_images_are_flattened(manager):
    for runtime in ("old-runtime", "test-runtime"):
        release = store.ROOT / "autodarts/versions/2.0.2" / runtime
        release.mkdir(parents=True)
        (release / "RUNTIME").write_text(runtime)
        (release / "autodarts").write_text(runtime)
    store.write_state("autodarts", {"active": "2.0.2", "previous": None, "pending": False, "runtime": "old-runtime"})
    store.initialize()
    executable = store.location("autodarts", "unused") / "autodarts"
    # The copy for the current runtime wins, and the others are removed.
    assert executable.read_text() == "test-runtime"
    assert sorted(p.name for p in (store.ROOT / "autodarts/versions").iterdir()) == ["2.0.2"]


def test_interrupted_native_update_rolls_back_without_a_bundle(manager):
    store.write_state("autodarts", {"active": "2.1.0", "previous": "2.0.2", "pending": True})
    store.initialize()
    state = store.read_state("autodarts")
    assert state["active"] == "2.0.2" and not state["pending"]


@pytest.mark.parametrize("name", ["../oche", "Oche", "", "a" * 41, None])
def test_module_names_are_validated(name):
    with pytest.raises(ValueError):
        store.read_state(name)


def test_downloads_identify_oche(tmp_path):
    # The Autodarts release server answers Python's default User-Agent with HTTP 403.
    from app import __version__
    from app.services import updates

    with patch.object(updates, "build_opener") as opener:
        opener.return_value.open.return_value.__enter__.return_value.read.side_effect = [b"data", b""]
        updates.download("https://releases.test/file", tmp_path / "file", 100)
    assert ("User-Agent", f"Oche/{__version__}") in opener.return_value.addheaders


@pytest.mark.parametrize(
    "installed,autostart,expected_stop",
    [
        (False, True, False),  # A first install keeps the engine running.
        (False, False, True),  # Unless Oche would not start it on boot.
        (True, True, True),  # Updating a stopped Autodarts leaves it stopped.
    ],
)
def test_first_autodarts_install_keeps_running(manager, installed, autostart, expected_stop):
    for version in ("2.0.2", "2.1.0"):
        release = store.release_path("autodarts", version)
        release.mkdir(parents=True)
        (release / "autodarts").write_text("#!/bin/sh\n")
    if installed:
        store.write_state("autodarts", {"active": "2.0.2", "previous": None, "pending": False})
    statuses = iter(["stopped"] + ["running"] * 10)
    response = patch("urllib.request.urlopen")
    with response as urlopen, \
            patch("app.core.integrations.autodarts.process.get_status", side_effect=lambda: {"status": next(statuses)}), \
            patch("app.core.integrations.autodarts.process.start"), patch("app.core.integrations.autodarts.process.stop") as stop, \
            patch("app.config.load_config", return_value={"autostart_autodarts": autostart}), \
            patch("app.services.updates.time.sleep"):
        urlopen.return_value.__enter__.return_value.status = 200
        manager.activate("autodarts", "2.1.0")
    # stop() runs once before starting the candidate, and again only to leave it stopped.
    assert stop.call_count == (2 if expected_stop else 1)
    assert store.read_state("autodarts")["active"] == "2.1.0" and not store.read_state("autodarts")["pending"]


def test_active_release_with_missing_files_can_be_installed_again(manager):
    # An older image's state can name a release whose files never reached this layout.
    store.write_state("autodarts", {"active": "2.0.2", "previous": None, "pending": False})
    entry, downloads = autodarts_release()
    manager.available = {"autodarts": entry}
    module = next(m for m in manager.status()["modules"] if m["name"] == "autodarts")
    assert not module["present"] and module["installable"]
    with downloads, offline_autodarts(entry):
        assert manager.install_missing("autodarts") == "2.1.0"
    assert (store.location("autodarts", "unused") / "autodarts").is_file()


@pytest.mark.parametrize("name,installing", [("autodarts", True), ("oche", False)])
def test_update_job_distinguishes_first_install(manager, monkeypatch, name, installing):
    monkeypatch.setenv("OCHE_LAUNCHER", "1")
    with patch("app.services.updates.threading.Thread"):
        manager.submit("update", name)
    try:
        assert manager.job["installing"] is installing
    finally:
        manager.lock.release()


def test_integration_install_is_a_serialized_job_without_starting_engines(manager, monkeypatch):
    monkeypatch.setenv("OCHE_LAUNCHER", "1")
    registry.set_enabled("autodarts", False)
    entry, downloads = autodarts_release()
    with patch("app.services.updates.threading.Thread"), downloads, offline_autodarts(entry), \
            patch.object(registry.get("autodarts").process, "start") as start:
        manager.submit("install", "autodarts")
        assert manager.job["installing"] and manager.job["status"] == "running"
        with pytest.raises(ValueError, match="Another update"):
            manager.submit("install", "autodarts")
        manager._run("install", "autodarts")
        assert manager.job["status"] == "complete"
        assert store.read_state("autodarts")["active"] == "2.1.0"
        start.assert_not_called()
    assert not manager.lock.locked()


def test_failed_integration_install_reports_error_and_can_be_retried(manager, monkeypatch):
    monkeypatch.setenv("OCHE_LAUNCHER", "1")
    with patch("app.services.updates.threading.Thread"), \
            patch.object(manager, "install_missing", side_effect=ValueError("Download failed")):
        manager.submit("install", "ochecore")
        manager._run("install", "ochecore")
        assert manager.job["status"] == "error" and manager.job["message"] == "Download failed"
        assert not manager.lock.locked()
        manager.submit("install", "ochecore")
        manager._run("install", "ochecore")
        assert not store.read_state("ochecore").get("active")
