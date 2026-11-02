"""Autodarts and OcheCore share the cameras, so only the active engine runs."""

import hashlib
import io
import json
import tarfile
from unittest.mock import patch

import pytest
from fastapi.testclient import TestClient

import oche_runtime as store
from app import config as settings
from app.services import engines
from app.services.updates import UpdateManager
from app.services.upstream_releases import discover_ochecore


class FakeEngine:
    def __init__(self, installed=True):
        self.installed, self.running = installed, False

    def get_status(self):
        status = "running" if self.running else "stopped"
        return {"installed": self.installed, "status": status if self.installed else "nofile"}

    def start(self):
        self.running = True

    def stop(self):
        self.running = False


@pytest.fixture
def fakes(tmp_path, monkeypatch):
    monkeypatch.setattr(settings, "CONFIG_FILE", tmp_path / "settings.json")
    fakes = {"autodarts": FakeEngine(), "ochecore": FakeEngine()}
    monkeypatch.setattr(engines, "ENGINES", fakes)
    return fakes


def test_autodarts_is_the_default_engine(fakes):
    assert engines.active() == "autodarts"
    assert [e["name"] for e in engines.status()["engines"] if e["active"]] == ["autodarts"]


def test_using_an_engine_stops_the_other_and_is_saved(fakes):
    fakes["autodarts"].running = True
    engines.use("ochecore")
    assert fakes["ochecore"].running and not fakes["autodarts"].running
    assert json.loads(settings.CONFIG_FILE.read_text())["detection_engine"] == "ochecore"
    engines.use("autodarts")
    assert fakes["autodarts"].running and not fakes["ochecore"].running


def test_a_missing_engine_can_be_chosen_but_does_not_start(fakes):
    fakes["ochecore"].installed = False
    fakes["autodarts"].running = True
    engines.use("ochecore")
    assert engines.active() == "ochecore"
    assert not fakes["ochecore"].running and not fakes["autodarts"].running


@pytest.mark.parametrize("autostart", [True, False])
def test_boot_starts_only_the_active_engine(fakes, autostart):
    config = settings.load_config()
    config.update(detection_engine="ochecore", autostart_autodarts=autostart)
    settings.save_config(config)
    engines.start_on_boot()
    assert fakes["ochecore"].running is autostart and not fakes["autodarts"].running


def test_engine_api_switches_and_rejects_unknown_names(fakes, tmp_path):
    from app.core.config import Settings
    from app.main import create_app

    app_settings = Settings(_env_file=None, data_dir=tmp_path, client_id="", board_id="", ui_theme="dark")
    with TestClient(create_app(app_settings)) as client:
        assert client.get("/api/engines").json()["active"] == "autodarts"
        response = client.post("/api/engines/ochecore/use", json={})
        assert response.status_code == 200 and response.json()["active"] == "ochecore"
        assert fakes["ochecore"].running
        assert client.post("/api/engines/ochecore/stop", json={}).json()["active"] == "ochecore"
        assert not fakes["ochecore"].running
        assert client.post("/api/engines/glow/use", json={}).status_code == 404


def ochecore_feed(version="0.1.5", architecture="amd64", **changes):
    name = f"ochecore-v{version}-linux-{architecture}.tar.gz"
    base = f"https://github.com/mondeggo/OcheCore/releases/download/v{version}/"
    release = {
        "tag_name": f"v{version}",
        "draft": False,
        "prerelease": False,
        "assets": [
            {"name": name, "browser_download_url": base + name},
            {"name": name + ".sha256", "browser_download_url": base + name + ".sha256"},
        ],
        **changes,
    }
    files = {"releases/latest": json.dumps(release), ".sha256": f"{'a' * 64}  {name}\n"}

    def download(url, destination, limit):
        destination.write_text(next(body for key, body in files.items() if url.endswith(key)))

    return download


def test_ochecore_release_is_found_with_its_publisher_checksum():
    release = discover_ochecore(ochecore_feed(), "amd64")
    assert release["version"] == "0.1.5" and release["sha256"] == "a" * 64
    assert release["format"] == "ochecore-archive"


@pytest.mark.parametrize(
    "changes,reason",
    [
        ({"prerelease": True}, "stable"),
        ({"tag_name": "nightly"}, "version"),
        ({"assets": [{"name": "ochecore-v0.1.5-linux-amd64.tar.gz",
                      "browser_download_url": "https://elsewhere.test/ochecore.tar.gz"}]}, "official"),
    ],
)
def test_untrusted_ochecore_releases_are_refused(changes, reason):
    with pytest.raises(ValueError, match=reason):
        discover_ochecore(ochecore_feed(**changes), "amd64")


def test_ochecore_installs_into_the_module_store(tmp_path, monkeypatch):
    for key, value in {"ROOT": tmp_path / "modules", "DATA": tmp_path, "BUNDLES": tmp_path / "bundles"}.items():
        monkeypatch.setattr(store, key, value)
    stream = io.BytesIO()
    with tarfile.open(fileobj=stream, mode="w:gz") as archive:
        for name, payload in {"ochecore-v0.1.5-linux-amd64/oche-core": b"#!/bin/sh\n",
                              "ochecore-v0.1.5-linux-amd64/LICENSE": b"AGPL"}.items():
            info = tarfile.TarInfo(name)
            info.size = len(payload)
            archive.addfile(info, io.BytesIO(payload))
    blob = stream.getvalue()
    digest = hashlib.sha256(blob).hexdigest()
    release = {"version": "0.1.5", "display_version": "0.1.5", "format": "ochecore-archive",
               "url": "https://github.com/mondeggo/ochecore/releases/download/v0.1.5/x.tar.gz", "sha256": digest}

    def download(url, destination, limit):
        destination.write_bytes(blob)
        return digest

    with patch("app.services.updates.download", side_effect=download), \
            patch.dict("app.services.updates.UPSTREAM", {"ochecore": lambda *_: release}):
        assert UpdateManager().install_missing("ochecore") == "0.1.5"
    installed = store.location("ochecore", "unused")
    assert (installed / "oche-core").is_file() and (installed / "LICENSE").is_file()
    assert (installed / "VERSION").read_text() == "0.1.5"
