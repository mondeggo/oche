import asyncio
import json

import httpx
import pytest
from fastapi.testclient import TestClient

from app.core.errors import ConnectionProblem
from app.core.integrations.caller.service import CallerConfig
from app.core.integrations.caller.voices import VOICES, VoiceLibrary
from app.core.integrations.wled.service import Device, Target, WLEDConfig
from app.core.runtime import Runtime
from app.main import create_app


@pytest.fixture
def profile_app(settings, monkeypatch):
    # Installed voices need no download or host playback during configuration tests.
    monkeypatch.setattr(VoiceLibrary, "installed", lambda self, voice: bool(voice))

    def no_network(request):
        pytest.fail(f"Profile configuration attempted network access: {request.url.path}")

    return create_app(settings, transport=httpx.MockTransport(no_network))


def lighting_config():
    return WLEDConfig(
        devices=[
            Device(
                id="board",
                name="Board",
                url="http://wled.test",
                enabled=False,
                targets=[Target(id="ring", name="Ring")],
            )
        ]
    ).model_dump(mode="json")


def test_profile_file_round_trip_preserves_hardware_and_original(profile_app, settings):
    with TestClient(profile_app) as client:
        client.put("/api/wled", json=lighting_config())
        client.patch("/api/caller", json={"volume": 0.3, "output": "browser"})
        downloaded = client.get("/api/profiles/default/export")
        assert downloaded.status_code == 200
        assert "attachment" in downloaded.headers["content-disposition"]
        file = downloaded.json()
        assert file["format"] == "oche-profile" and file["version"] == 1
        assert "url" not in downloaded.text and "tokens" not in downloaded.text
        assert file["zones"][0]["style"]["phases"]["idle"]["color"] == "#6f00dd"
        file["zones"][0]["style"]["phases"]["idle"]["color"] = "#123456"
        file["caller"]["volume"] = 0.7
        revision = client.get("/api/profiles").headers["etag"]
        response = client.post(
            "/api/profiles/import", json={"profile": file}, headers={"If-Match": revision}
        )
        assert response.status_code == 200
        imported = response.json()["active_profile"]
        assert response.json()["profiles"][-1]["name"] == "Default (2)"
        device = client.get("/api/wled").json()["devices"][0]
        assert device["url"] == "http://wled.test" and device["enabled"] is False
        assert device["targets"][0]["phases"]["idle"]["color"] == "#123456"
        assert client.get("/api/caller").json()["volume"] == 0.7
        assert client.get("/api/profiles/default/export").json() == downloaded.json()
    with TestClient(profile_app) as client:
        assert client.get("/api/profiles").json()["active_profile"] == imported
        assert client.get(f"/api/profiles/{imported}/export").json()["caller"]["volume"] == 0.7


def test_profile_import_matches_names_and_rejects_unmatched_zones(profile_app, settings):
    with TestClient(profile_app) as client:
        client.put("/api/wled", json=lighting_config())
        file = client.get("/api/profiles/default/export").json()
        file["zones"][0].update(device_id="other-device", target_id="other-zone")
        response = client.post("/api/profiles/import", json={"profile": file, "name": "Imported"})
        assert response.status_code == 200
        before = (settings.data_dir / "profiles.json").read_bytes()
        file["zones"][0]["target_name"] = "Missing zone"
        response = client.post("/api/profiles/import", json={"profile": file})
        assert response.status_code == 409 and "Cannot match zone" in response.text
        assert (settings.data_dir / "profiles.json").read_bytes() == before


@pytest.mark.parametrize("invalid", ["version", "address", "mode", "duplicate"])
def test_invalid_profile_import_is_atomic(profile_app, settings, invalid):
    with TestClient(profile_app) as client:
        client.put("/api/wled", json=lighting_config())
        file = client.get("/api/profiles/default/export").json()
        if invalid == "version":
            file["version"] = 2
        elif invalid == "address":
            file["zones"][0]["url"] = "http://other.test"
        elif invalid == "mode":
            file["zones"][0]["mode"] = "pixels"
        else:
            file["zones"].append(file["zones"][0])
        before = (settings.data_dir / "profiles.json").read_bytes()
        response = client.post("/api/profiles/import", json={"profile": file})
        assert response.status_code in {409, 422}
        assert (settings.data_dir / "profiles.json").read_bytes() == before


def test_profile_file_request_guards_and_limits(profile_app):
    with TestClient(profile_app) as client:
        file = client.get("/api/profiles/default/export").json()
        path = "/api/profiles/import"
        assert (
            client.post(path, json={"profile": file}, headers={"If-Match": '"stale"'}).status_code
            == 412
        )
        assert (
            client.post(
                path, json={"profile": file}, headers={"Origin": "https://other.test"}
            ).status_code
            == 403
        )
        assert (
            client.post(
                path, content="invalid", headers={"Content-Type": "application/json"}
            ).status_code
            == 422
        )
        assert (
            client.post(
                path,
                content=" " * (2 * 1024 * 1024 + 1),
                headers={"Content-Type": "application/json"},
            ).status_code
            == 413
        )
        assert client.get("/api/profiles/missing/export").status_code == 409
        for _ in range(11):
            assert client.post(path, json={"profile": file}).status_code == 200
        assert client.post(path, json={"profile": file}).status_code == 409


def test_profiles_restore_lighting_and_all_caller_settings(profile_app, settings):
    original_caller = CallerConfig(
        enabled=True,
        voice=next(iter(VOICES)),
        output="browser",
        volume=0.4,
        darts="segment",
        turn_totals=False,
        checkouts=False,
        players=False,
        include_bots=False,
        local_only=True,
    ).model_dump()
    with TestClient(profile_app) as client:
        assert client.put("/api/wled", json=lighting_config()).status_code == 200
        assert client.put("/api/caller", json=original_caller).status_code == 200
        quiet = client.post("/api/profiles", json={"name": "Quiet"}).json()["active_profile"]
        assert (
            client.patch("/api/caller", json={"enabled": False, "volume": 0.1}).status_code == 200
        )
        wled = client.get("/api/wled").json()
        wled["devices"][0]["targets"][0]["phases"]["ready"]["color"] = "#112233"
        # Device addressing and automation switches remain shared across profiles.
        wled["devices"][0]["name"] = "Renamed board"
        wled["devices"][0]["targets"][0]["segment"] = 2
        wled["enabled"] = True
        assert client.put("/api/wled", json=wled).status_code == 200
        assert client.put("/api/profile", json={"id": "default"}).status_code == 200
        assert client.get("/api/caller").json() == original_caller
        restored = client.get("/api/wled").json()
        assert restored["enabled"] is True
        assert restored["devices"][0]["name"] == "Renamed board"
        assert restored["devices"][0]["targets"][0]["segment"] == 2
        assert restored["devices"][0]["targets"][0]["phases"]["ready"]["color"] == "#00ff00"
        assert client.put("/api/profile", json={"id": quiet}).status_code == 200
        assert client.get("/api/caller").json()["volume"] == 0.1
        assert client.get("/api/caller").json()["enabled"] is False
        assert (
            client.get("/api/wled").json()["devices"][0]["targets"][0]["phases"]["ready"]["color"]
            == "#112233"
        )
    assert (settings.data_dir / "profiles.json").is_file()
    assert not (settings.data_dir / "wled.json").exists()
    assert not (settings.data_dir / "caller.json").exists()
    with TestClient(profile_app) as client:
        assert client.get("/api/profiles").json()["active_profile"] == quiet
        assert client.get("/api/caller").json()["volume"] == 0.1


def test_blank_profile_uses_lighting_defaults_and_keeps_hardware(profile_app):
    with TestClient(profile_app) as client:
        client.put("/api/wled", json=lighting_config())
        client.patch("/api/caller", json={"enabled": True, "voice": next(iter(VOICES))})
        response = client.post("/api/profiles", json={"name": "Blank", "source": "blank"})
        assert response.status_code == 200
        assert client.get("/api/caller").json() == CallerConfig().model_dump()
        device = client.get("/api/wled").json()["devices"][0]
        assert device["url"] == "http://wled.test" and device["enabled"] is False
        target = device["targets"][0]
        assert target["phases"]["idle"] == {
            "color": "#6f00dd",
            "brightness": 64,
            "effect": 2,
        }
        assert target["phases"]["takeout"] == {
            "color": "#e17100",
            "brightness": 191,
            "effect": 0,
        }
        assert target["effects"]["match_win"]["effect"] == 9
        assert target["effects"]["match_win"]["duration"] == 10
        assert target["players"] == {}
        assert target["matrix"]["appearance"]["brightness"] == 128




def test_wled_profile_aliases_share_the_application_selection(profile_app):
    with TestClient(profile_app) as client:
        quiet = client.post("/api/profiles", json={"name": "Quiet"}).json()["active_profile"]
        client.patch("/api/caller", json={"volume": 0.2})
        assert client.put("/api/profile", json={"id": "default"}).status_code == 200
        assert client.get("/api/caller").json()["volume"] == 0.6
        assert client.put("/api/profile", json={"id": quiet}).status_code == 200
        assert client.get("/api/caller").json()["volume"] == 0.2
        assert client.delete(f"/api/profiles/{quiet}").status_code == 200
        assert client.get("/api/caller").json()["volume"] == 0.6
        assert client.get("/api/profiles").json()["active_profile"] == "default"
        assert client.delete("/api/profiles/default").status_code == 409


def test_profile_revision_tracks_integrations_and_switch_invalidates_both_scopes(profile_app):
    with TestClient(profile_app) as client:
        revision = client.get("/api/profiles").headers["etag"]
        initial_caller = client.get("/api/caller").headers["etag"]
        changed = client.patch(
            "/api/caller", json={"volume": 0.2}, headers={"If-Match": initial_caller}
        )
        assert changed.status_code == 200
        assert (
            client.post(
                "/api/profiles", json={"name": "Stale"}, headers={"If-Match": revision}
            ).status_code
            == 412
        )
        second = client.get("/api/profiles").headers["etag"]
        wled_revision = client.get("/api/wled").headers["etag"]
        caller_revision = changed.headers["etag"]
        assert client.get("/api/wled/status").headers["etag"] == wled_revision
        assert client.get("/api/caller/status").headers["etag"] == caller_revision
        created = client.post(
            "/api/profiles", json={"name": "Practice"}, headers={"If-Match": second}
        )
        assert created.status_code == 200
        for path, body, etag in (
            ("/api/caller", {"volume": 0.8}, caller_revision),
            ("/api/wled", {"enabled": True}, wled_revision),
        ):
            assert client.patch(path, json=body, headers={"If-Match": etag}).status_code == 412
        assert (
            client.put(
                "/api/profile", json={"id": "default"}, headers={"If-Match": second}
            ).status_code
            == 412
        )
        profile_id = created.json()["active_profile"]
        assert (
            client.delete(f"/api/profiles/{profile_id}", headers={"If-Match": second}).status_code
            == 412
        )
        assert client.get("/api/caller").json()["volume"] == 0.2


def test_unrelated_caller_and_wled_saves_do_not_conflict(profile_app):
    with TestClient(profile_app) as client:
        wled_revision = client.get("/api/wled").headers["etag"]
        caller_revision = client.get("/api/caller").headers["etag"]
        first = client.patch(
            "/api/caller", json={"volume": 0.2}, headers={"If-Match": caller_revision}
        )
        assert first.status_code == 200
        assert client.get("/api/wled").headers["etag"] == wled_revision
        second = client.patch(
            "/api/wled", json={"enabled": True}, headers={"If-Match": wled_revision}
        )
        assert second.status_code == 200
        assert client.get("/api/caller").headers["etag"] == first.headers["etag"]
        assert (
            client.patch(
                "/api/caller", json={"volume": 0.3}, headers={"If-Match": first.headers["etag"]}
            ).status_code
            == 200
        )
        assert (
            client.patch(
                "/api/caller", json={"volume": 0.9}, headers={"If-Match": caller_revision}
            ).status_code
            == 412
        )
        assert (
            client.patch(
                "/api/wled", json={"enabled": False}, headers={"If-Match": wled_revision}
            ).status_code
            == 412
        )




def test_invalid_voice_does_not_persist_or_change_active_profile(profile_app, settings):
    with TestClient(profile_app) as client:
        before = client.get("/api/profiles")
        assert client.patch("/api/caller", json={"voice": "unknown"}).status_code == 409
        assert client.get("/api/profiles").headers["etag"] == before.headers["etag"]
        assert not (settings.data_dir / "profiles.json").exists()


def test_installation_blocks_switch_before_lighting_or_store_changes(profile_app, settings):
    with TestClient(profile_app) as client:
        client.patch("/api/caller", json={"voice": next(iter(VOICES))})
        quiet = client.post("/api/profiles", json={"name": "Quiet", "source": "blank"}).json()[
            "active_profile"
        ]
        client.put("/api/profile", json={"id": "default"})
        before = (settings.data_dir / "profiles.json").read_bytes()

        async def pretend_installation():
            library = profile_app.state.runtime.caller.library
            library.download["voice_id"] = profile_app.state.runtime.caller.config.voice
            library.task = asyncio.create_task(asyncio.Event().wait())

        client.portal.call(pretend_installation)
        response = client.put("/api/profile", json={"id": quiet})
        assert response.status_code == 409
        assert "installation" in response.json()["detail"]
        assert client.get("/api/profiles").json()["active_profile"] == "default"
        assert client.get("/api/wled").json()["active_profile"] == "default"
        assert (settings.data_dir / "profiles.json").read_bytes() == before


def test_write_failure_keeps_both_integrations_and_revision(profile_app, settings, monkeypatch):
    with TestClient(profile_app) as client:
        client.patch("/api/caller", json={"volume": 0.25})
        before = (settings.data_dir / "profiles.json").read_bytes()
        revision = client.get("/api/profiles").headers["etag"]

        def fail_write(*args):
            raise ConnectionProblem("Cannot write application data.")

        monkeypatch.setattr("app.core.profiles.write_private_json", fail_write)
        response = client.post("/api/profiles", json={"name": "Blank", "source": "blank"})
        assert response.status_code == 409
        assert client.get("/api/profiles").headers["etag"] == revision
        assert client.get("/api/wled").json()["active_profile"] == "default"
        assert client.get("/api/caller").json()["volume"] == 0.25
        assert (settings.data_dir / "profiles.json").read_bytes() == before


def test_apply_failure_restores_the_persisted_profile(profile_app, settings, monkeypatch):
    with TestClient(profile_app) as client:
        client.patch("/api/caller", json={"volume": 0.25})
        before = json.loads((settings.data_dir / "profiles.json").read_text())
        service = profile_app.state.runtime.caller
        configure = service.configure
        calls = 0

        async def fail_once(config, **kwargs):
            nonlocal calls
            calls += 1
            if calls == 1:
                raise ConnectionProblem("Temporary playback failure")
            return await configure(config, **kwargs)

        monkeypatch.setattr(service, "configure", fail_once)
        assert (
            client.post("/api/profiles", json={"name": "Blank", "source": "blank"}).status_code
            == 409
        )
        assert calls == 2
        assert client.get("/api/wled").json()["active_profile"] == "default"
        assert client.get("/api/caller").json()["volume"] == 0.25
        assert client.get("/api/profiles").json()["profiles"] == [
            {"id": "default", "name": "Default"}
        ]
        assert json.loads((settings.data_dir / "profiles.json").read_text()) == before


async def test_cancelled_profile_change_finishes_rollback_before_returning(settings, monkeypatch):
    def no_network(request):
        pytest.fail(f"Profile rollback attempted network access: {request.url.path}")

    async with httpx.AsyncClient(transport=httpx.MockTransport(no_network)) as http:
        runtime = Runtime(settings, http)
        await runtime.profiles.configure_caller(CallerConfig(volume=0.25))
        before = runtime.profiles.path.read_bytes()
        revision = runtime.profiles.revision
        configure = runtime.caller.configure
        applying, rolling_back, release = asyncio.Event(), asyncio.Event(), asyncio.Event()
        calls = 0

        async def pause_apply_and_rollback(config, **kwargs):
            nonlocal calls
            calls += 1
            if calls == 1:
                applying.set()
                await asyncio.Event().wait()
            if calls == 2:
                rolling_back.set()
                await release.wait()
            return await configure(config, **kwargs)

        monkeypatch.setattr(runtime.caller, "configure", pause_apply_and_rollback)
        task = asyncio.create_task(runtime.profiles.create("Interrupted", "blank"))
        try:
            await asyncio.wait_for(applying.wait(), 1)
            assert runtime.wled.config.active_profile != "default"
            task.cancel()
            await asyncio.wait_for(rolling_back.wait(), 1)
            task.cancel()  # A second cancellation must not interrupt restoration.
            await asyncio.sleep(0)
            assert not task.done()
            release.set()
            with pytest.raises(asyncio.CancelledError):
                await task
            assert runtime.profiles.path.read_bytes() == before
            assert runtime.profiles.revision == revision
            assert runtime.wled.config.active_profile == "default"
            assert runtime.caller.config.volume == 0.25
            assert runtime.profiles.summary()["profiles"] == [{"id": "default", "name": "Default"}]
            # The next mutation must use coherent lighting and Caller profile IDs.
            created = await runtime.profiles.create("Next")
            assert created["active_profile"] == runtime.wled.config.active_profile
        finally:
            release.set()
            if not task.done():
                task.cancel()
                await asyncio.gather(task, return_exceptions=True)
            await runtime.close()


@pytest.mark.parametrize("corrupt", ["broken", '{"version":1,"wled":{},"callers":{}}'])
def test_invalid_profile_store_is_not_rebuilt_from_integration_files(
    profile_app, settings, corrupt
):
    path = settings.data_dir / "profiles.json"
    path.write_text(corrupt, encoding="utf-8")
    (settings.data_dir / "caller.json").write_text("{}", encoding="utf-8")
    with pytest.raises(ConnectionProblem, match="Restore a valid backup"):
        with TestClient(profile_app):
            pass
    assert path.read_text(encoding="utf-8") == corrupt
