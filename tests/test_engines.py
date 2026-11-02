"""Integrations register engines; Play chooses one, and only one reads the cameras."""

import dataclasses
import hashlib
import io
import json
import re
import tarfile
from unittest.mock import patch

import pytest
from fastapi.testclient import TestClient

import oche_runtime as store
from app import config as settings
from app.core.config import Settings
from app.core.integrations import registry
from app.core.integrations.ochecore.release import discover_ochecore
from app.services import engines, updates


class FakeProcess:
    def __init__(self, installed=True):
        self.installed, self.running = installed, False

    def get_status(self):
        status = "running" if self.running else "stopped"
        return {"installed": self.installed, "status": status if self.installed else "nofile"}

    def start(self):
        self.running = True

    def stop(self):
        self.running = False


def fake(name, scores=True, enabled=True):
    return registry.Integration(
        id=name, name=name.title(), description="", enabled_by_default=enabled,
        process=FakeProcess(), port=1, engine=registry.Engine(label=name.title(), scores=scores, note="Not yet."),
    )


@pytest.fixture
def engines_registry(tmp_path, monkeypatch):
    monkeypatch.setattr(settings, "CONFIG_FILE", tmp_path / "settings.json")
    fakes = {"first": fake("first"), "second": fake("second", scores=False, enabled=False)}
    monkeypatch.setattr(registry, "REGISTRY", fakes)
    monkeypatch.setattr(registry, "load", lambda: fakes)
    return fakes


def test_the_first_engine_that_scores_is_chosen_by_default(engines_registry):
    assert engines.chosen() == "first"
    registry.set_enabled("first", False)
    assert engines.chosen() == engines.MANUAL


def test_choosing_an_engine_stops_the_other_and_is_saved(engines_registry):
    registry.set_enabled("second", True)
    first, second = engines_registry["first"].process, engines_registry["second"].process
    first.running = True
    engines.use("second")
    assert second.running and not first.running
    assert json.loads(settings.CONFIG_FILE.read_text())["detection_engine"] == "second"
    # Manual darts stop nothing: Play on Autodarts may still need its engine.
    engines.use("first")
    engines.use(engines.MANUAL)
    assert first.running and engines.chosen() == engines.MANUAL


def test_switched_off_engines_cannot_be_chosen_and_give_up_the_choice(engines_registry):
    with pytest.raises(ValueError):
        engines.use("second")
    engines.use("first")
    registry.set_enabled("first", False)
    engines.switch_off("first")
    assert not engines_registry["first"].process.running
    assert "detection_engine" not in json.loads(settings.CONFIG_FILE.read_text())


@pytest.mark.parametrize("autostart", [True, False])
def test_boot_starts_only_the_chosen_engine(engines_registry, autostart):
    registry.set_enabled("second", True)
    config = settings.load_config()
    config.update(detection_engine="second", autostart_autodarts=autostart)
    settings.save_config(config)
    engines.start_on_boot()
    assert engines_registry["second"].process.running is autostart
    assert not engines_registry["first"].process.running


def test_switches_persist_per_integration(engines_registry):
    assert registry.enabled("first") and not registry.enabled("second")
    registry.set_enabled("second", True)
    assert registry.enabled("second")
    assert json.loads(settings.CONFIG_FILE.read_text())["integrations"]["second"] == {"enabled": True}
    with pytest.raises(ValueError):
        registry.set_enabled("unknown", True)


def test_updates_list_only_switched_on_integrations(tmp_path, monkeypatch):
    monkeypatch.setattr(settings, "CONFIG_FILE", tmp_path / "settings.json")
    assert updates.module_names() == ("oche", "autodarts")
    registry.set_enabled("ochecore", True)
    assert updates.module_names() == ("oche", "autodarts", "ochecore")
    registry.set_enabled("autodarts", False)
    assert updates.module_names() == ("oche", "ochecore")


def app_settings(tmp_path):
    return Settings(_env_file=None, data_dir=tmp_path, client_id="", board_id="", ui_theme="dark")


def test_switching_integrations_changes_the_sidebar_and_play(tmp_path, monkeypatch):
    from app.main import create_app

    monkeypatch.setattr(settings, "CONFIG_FILE", tmp_path / "settings.json")
    with TestClient(create_app(app_settings(tmp_path))) as client:
        page = client.get("/local").text
        # Play comes first; the Autodarts integration adds its button below it.
        assert page.index('href="/local"') < page.index('id="nav-link-play"')
        assert not re.search(r'id="nav-link-play"[^>]*\shidden', page)
        assert re.search(r'id="nav-link-ochecore"[^>]*\shidden', page)
        assert re.search(r'value="ochecore"[^>]*disabled', page)
        assert client.put("/api/integrations/autodarts", json={"enabled": False}).status_code == 200
        page = client.get("/local").text
        assert re.search(r'id="nav-link-play"[^>]*\shidden', page)
        assert re.search(r'id="nav-link-autodarts"[^>]*\shidden', page)
        assert re.search(r'value="autodarts"[^>]*disabled', page) and 'value="manual" checked' in page
        assert "Only manual darts are available" in page
        assert client.put("/api/integrations/glow", json={"enabled": True}).status_code == 404
        assert client.post("/api/engines/autodarts/use", json={}).status_code == 404
        assert client.post("/api/engines/manual/use", json={}).json()["active"] == "manual"


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

    ochecore = registry.get("ochecore")
    offline = dataclasses.replace(ochecore, release=dataclasses.replace(ochecore.release, discover=lambda *_: release))
    with patch("app.services.updates.download", side_effect=download), \
            patch.dict(registry.REGISTRY, {"ochecore": offline}):
        assert updates.UpdateManager().install_missing("ochecore") == "0.1.5"
    installed = store.location("ochecore", "unused")
    assert (installed / "oche-core").is_file() and (installed / "LICENSE").is_file()
    assert (installed / "VERSION").read_text() == "0.1.5"


def test_settings_accept_only_known_integration_sidebar_keys(tmp_path, monkeypatch):
    from app.main import create_app

    monkeypatch.setattr(settings, "CONFIG_FILE", tmp_path / "settings.json")
    with TestClient(create_app(app_settings(tmp_path))) as client:
        assert client.post("/config/data", json={"autohide_navbar_on_ochecore": True}).status_code == 200
        assert client.post("/config/data", json={"show_page_cameras": False}).status_code == 200
        assert client.post("/config/data", json={"autohide_navbar_on_glow": True}).status_code == 422
        assert client.post("/config/data", json={"show_page_cameras": "no"}).status_code == 422
