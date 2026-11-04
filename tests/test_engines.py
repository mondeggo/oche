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


@pytest.mark.parametrize("name", ["autodarts", "ochecore"])
def test_enabling_a_missing_engine_submits_install_and_exposes_progress(tmp_path, name):
    from app.main import create_app

    registry.set_enabled(name, False)
    item = registry.get(name)
    job = {"id": "install-1", "action": "install", "module": name, "status": "running"}
    with TestClient(create_app(app_settings(tmp_path))) as client, \
            patch.object(item.process, "get_status", return_value={"installed": False, "status": "nofile"}), \
            patch.object(item.process, "start") as start, \
            patch.object(updates.manager, "job", {"status": "idle"}), \
            patch.object(updates.manager, "submit") as submit:
        submit.side_effect = lambda *_: setattr(updates.manager, "job", dict(job))
        assert client.put(f"/api/integrations/{name}", json={"enabled": True}).status_code == 200
        submit.assert_called_once_with("install", name)
        assert registry.enabled(name)
        assert client.get("/api/engines").json()["installation"] == job
        # A retry while the same download runs follows it, rather than spawning another.
        assert client.put(f"/api/integrations/{name}", json={"enabled": True}).status_code == 200
        assert submit.call_count == 1
        updates.manager.job["status"] = "error"
        updates.manager.job["message"] = "Download failed"
        assert client.get("/api/engines").json()["installation"]["message"] == "Download failed"
        assert client.put(f"/api/integrations/{name}", json={"enabled": True}).status_code == 200
        assert submit.call_count == 2
        start.assert_not_called()


@pytest.mark.parametrize("name", ["autodarts", "ochecore"])
def test_enabling_an_installed_engine_does_not_download(tmp_path, name):
    from app.main import create_app

    registry.set_enabled(name, False)
    with TestClient(create_app(app_settings(tmp_path))) as client, \
            patch.object(registry.get(name).process, "get_status", return_value={"installed": True}), \
            patch.object(updates.manager, "submit") as submit:
        assert client.put(f"/api/integrations/{name}", json={"enabled": True}).status_code == 200
        submit.assert_not_called()


@pytest.mark.parametrize("reason", ["Another update operation is running", "Updates require the Docker runtime with the stable launcher"])
def test_rejected_install_leaves_the_integration_switched_off(tmp_path, reason):
    from app.main import create_app

    registry.set_enabled("ochecore", False)
    with TestClient(create_app(app_settings(tmp_path))) as client, \
            patch.object(registry.get("ochecore").process, "get_status", return_value={"installed": False}), \
            patch.object(updates.manager, "job", {"status": "idle"}), \
            patch.object(updates.manager, "submit", side_effect=ValueError(reason)):
        response = client.put("/api/integrations/ochecore", json={"enabled": True})
        assert response.status_code == 409 and response.json()["detail"] == reason
        assert not registry.enabled("ochecore")


@pytest.mark.parametrize("name,label", [("autodarts", "Autodarts"), ("ochecore", "OcheCore")])
def test_engine_setup_pages_use_their_own_socket_and_offer_a_web_ui(tmp_path, name, label):
    from app.main import create_app

    with TestClient(create_app(app_settings(tmp_path))) as client:
        terminal = client.get(f"/{name}/terminal")
        assert terminal.status_code == 200
        assert f'data-socket="/{name}/terminal/ws"' in terminal.text
        assert f'data-engine-label="{label}"' in terminal.text
        assert client.get("/static/js/terminal.js").status_code == 200
        assert client.get("/static/css/terminal.css").status_code == 200
        path = "/integrations/autodarts/board" if name == "autodarts" else "/integrations/ochecore"
        page = client.get(path)
        assert page.status_code == 200
        assert "Setup terminal" in page.text and f"Open {label} ↗" in page.text


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


def test_switching_autodarts_off_removes_its_account_and_status_dots(tmp_path, monkeypatch):
    from app.main import create_app

    monkeypatch.setattr(settings, "CONFIG_FILE", tmp_path / "settings.json")
    with TestClient(create_app(app_settings(tmp_path))) as client:
        status = client.get("/api/status").json()
        assert {"auth", "cloud"} <= status.keys()
        home = client.get("/integrations").text
        assert 'id="login"' in home and 'data-view="autodarts"' in home
        assert 'id="health-autodarts"' in home
        assert 'id="health-cloud"' not in home and 'id="health-local"' not in home
        assert client.put("/api/integrations/autodarts", json={"enabled": False}).status_code == 200
        # The runtime drops Autodarts Play as a game source; core keeps local detection.
        status = client.get("/api/status").json()
        assert "auth" not in status and "cloud" not in status and "local_detection" in status
        assert client.get("/api/autodarts/config").status_code == 409
        assert client.get("/api/game").json()["available"] is False
        home = client.get("/integrations").text
        assert 'id="login"' not in home and 'data-view="autodarts"' not in home
        assert "/static/integrations/autodarts/account.js" not in home
        for dot in ("autodarts", "cloud", "local"):
            assert f'id="health-{dot}"' not in home
        assert 'id="health-manual"' in home
        with patch("app.routers.engines.manager.submit"):
            assert client.put("/api/integrations/autodarts", json={"enabled": True}).status_code == 200
        assert "auth" in client.get("/api/status").json()
        assert client.get("/api/autodarts/config").json()["client_id"] == "darts-caller"


def test_manual_indicator_follows_selection_and_only_enabled_engines_register_status(tmp_path):
    from app.main import create_app

    registry.set_enabled("autodarts", False)
    registry.set_enabled("ochecore", False)
    with TestClient(create_app(app_settings(tmp_path))) as client:
        def shown():
            status = client.get("/api/status").json()
            rows = [row["id"] for row in status["health"]]
            page = client.get("/local").text
            assert re.findall(r'data-health="([^"]+)"', page) == rows
            return status, rows

        status, rows = shown()
        assert rows == ["manual"] and status["vision"]["active"] == "manual"
        # Enable both integrations without downloading or running real engines.
        with patch("app.routers.engines.manager.submit"), \
                patch.object(registry.get("autodarts").process, "get_status",
                             return_value={"installed": False, "status": "nofile"}), \
                patch.object(registry.get("ochecore").process, "get_status",
                             return_value={"installed": False, "status": "nofile"}):
            assert client.put("/api/integrations/autodarts", json={"enabled": True}).status_code == 200
            assert client.put("/api/integrations/ochecore", json={"enabled": True}).status_code == 200
            assert client.post("/api/engines/manual/use", json={}).status_code == 200
            assert shown()[1] == ["manual", "autodarts", "ochecore"]
            assert client.post("/api/engines/ochecore/use", json={}).status_code == 200
            status, rows = shown()
            assert rows == ["autodarts", "ochecore"]
            assert status["vision"]["active"] == "ochecore"
            assert client.put("/api/integrations/autodarts", json={"enabled": False}).status_code == 200
            assert shown()[1] == ["ochecore"]
            assert client.put("/api/integrations/ochecore", json={"enabled": False}).status_code == 200
            assert shown()[1] == ["manual"]


def test_engine_reset_stops_engine_and_preserves_releases(engines_registry, tmp_path, monkeypatch):
    monkeypatch.setattr(store, "ROOT", tmp_path / "modules")
    process = engines_registry["first"].process
    process.running = True
    called = []
    process.reset_data = lambda: called.append(process.running)
    release = store.ROOT / "first/versions/1.0/binary"
    release.parent.mkdir(parents=True)
    release.write_text("engine")
    engines.maintain("first", "clear-cache")
    assert called == [False]
    assert release.read_text() == "engine"


def test_uninstall_removes_releases_keeps_settings_and_selects_manual(engines_registry, tmp_path, monkeypatch):
    monkeypatch.setattr(store, "ROOT", tmp_path / "modules")
    process = engines_registry["first"].process
    module = store.ROOT / "first"
    module.mkdir(parents=True)
    (module / "state.json").write_text('{"active":"1.0"}')
    board = tmp_path / "first/board.toml"
    board.parent.mkdir()
    board.write_text("calibration")
    process.running = True
    engines.maintain("first", "uninstall")
    assert not module.exists()
    assert board.read_text() == "calibration"
    assert not process.running
    assert engines.chosen() == "manual"


def test_engine_maintenance_is_blocked_during_updates(engines_registry, tmp_path, monkeypatch):
    monkeypatch.setattr(store, "ROOT", tmp_path / "modules")
    with updates.manager.lock:
        with pytest.raises(RuntimeError, match="current update"):
            engines.maintain("first", "uninstall")
        with pytest.raises(RuntimeError, match="current update"):
            engines.use("first")
    store.write_state("first", {"pending": True})
    with pytest.raises(RuntimeError, match="health check"):
        engines.maintain("first", "uninstall")
    assert not updates.manager.lock.locked()


@pytest.mark.parametrize("name,directory", [("autodarts", "AUTODARTS_DIR"), ("ochecore", "HOME")])
def test_reset_removes_saved_engine_data(name, directory, tmp_path, monkeypatch):
    from importlib import import_module

    process = import_module(f"app.core.integrations.{name}.process")
    home = tmp_path / name
    home.mkdir()
    (home / "calibration.json").write_text("calibration")
    (home / "settings.toml").write_text("settings")
    monkeypatch.setattr(process, directory, home)
    with patch.object(process, "clear_logs") as clear:
        process.reset_data()
        clear.assert_called_once_with()
    assert home.is_dir() and not list(home.iterdir())


def test_engine_maintenance_endpoints_require_confirmation(engines_registry):
    from fastapi import FastAPI

    from app.routers.engines import router

    app = FastAPI()
    app.include_router(router)
    with TestClient(app) as client, patch.object(engines, "maintain", return_value={"engines": []}) as maintain:
        for action in ("clear-cache", "uninstall"):
            assert client.post(f"/api/engines/first/{action}", json={}).status_code == 409
            maintain.assert_not_called()
            assert client.post(f"/api/engines/first/{action}", json={"confirmed": True}).status_code == 200
            maintain.assert_called_once_with("first", action)
            maintain.reset_mock()
