"""Release validation must finish before the launcher switches the application."""

import hashlib
import io
import tarfile
from unittest.mock import patch

import pytest

import oche_runtime as store
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
        ({"runtime": "v1-runtime"}, "different Docker"),
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
