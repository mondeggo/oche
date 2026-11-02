import asyncio

import httpx
import pytest
from fastapi.testclient import TestClient
from test_caller import VOICE, install_fixture

from app.core.events import Event, EventBus
from app.core.integrations.caller.service import Caller, CallerConfig, call_parts
from app.core.integrations.sources import PlaySources, event_allowed, integration_view, source_allowed
from app.core.integrations.wled.service import WLED, Device, Target, WLEDConfig
from app.core.integrations.wled2.service import Layout
from app.main import create_app


def turn(source):
    return Event(
        sequence=1,
        source="core",
        kind="normalized",
        event="turn_end",
        data={"play_source": source, "match_id": "game", "variant": "X01", "score": 180},
    )


@pytest.mark.parametrize("model", [CallerConfig, WLEDConfig, Layout])
def test_source_defaults_preserve_existing_configs(model):
    config = model.model_validate({})
    assert config.run_on_cloud and config.run_on_local
    assert source_allowed(config, "cloud") and source_allowed(config, "local")


@pytest.mark.parametrize("source", ["cloud", "local"])
def test_source_selection_filters_snapshots_and_events(source):
    policy = PlaySources(**{f"run_on_{source}": False})
    game = {"available": True, "source": source, "phase": "ready", "remaining": 301}
    view = integration_view(policy, game)
    assert not view["available"] and not view["source_enabled"]
    assert view["phase"] == "waiting"
    assert game["available"]  # The shared snapshot remains usable by other integrations.
    assert not event_allowed(policy, turn(source), game)
    assert call_parts(turn(source), CallerConfig(**{f"run_on_{source}": False})) == []
    other = "local" if source == "cloud" else "cloud"
    assert call_parts(turn(other), CallerConfig(**{f"run_on_{source}": False})) == [["180"]]


def test_queued_events_from_previous_source_are_rejected():
    assert not event_allowed(PlaySources(), turn("cloud"), {"source": "local"})
    assert not event_allowed(PlaySources(), turn("local"), {"source": "cloud"})
    assert event_allowed(PlaySources(), turn("local"), {"source": "local"})


def test_source_choices_persist_for_every_integration(settings):
    endpoints = {
        "wled": "/api/wled",
        "wled2": "/api/wled2",
        "caller": "/api/caller",
    }
    with TestClient(create_app(settings)) as client:
        for name, path in endpoints.items():
            values = {"run_on_cloud": False, "run_on_local": True}
            if name == "wled2":
                values = {**client.get(path).json(), **values}
            response = getattr(client, "put" if name == "wled2" else "patch")(path, json=values)
            assert response.status_code == 200, response.text
    with TestClient(create_app(settings)) as client:
        for path in endpoints.values():
            config = client.get(path).json()
            assert config["run_on_local"] and not config["run_on_cloud"]
        assert not client.app.state.runtime.wled2.config.compile().run_on_cloud


async def test_wled_rejects_disabled_source_events_and_phase_changes(tmp_path):
    game = {"available": True, "phase": "ready", "source": "local"}
    async with httpx.AsyncClient() as http:
        engine = WLED(
            tmp_path / "wled.json",
            http,
            EventBus(),
            lambda: game,
            initial=WLEDConfig(
                run_on_local=False,
                devices=[
                    Device(
                        id="board",
                        name="Board",
                        url="http://board.local",
                        targets=[Target(id="ring", name="Ring")],
                    )
                ],
            ),
        )
        engine.start()
        assert not engine.view()["available"]
        worker = engine.workers["board"]
        win = Event(
            sequence=1, source="core", event="match_win", kind="normalized",
            data={"play_source": "local", "player": {"is_local": True}}
        )
        worker.accept(win)
        assert not worker.overlays
        engine.config.run_on_local = True
        assert engine.view()["available"]
        worker.accept(win)
        assert worker.overlays
        game["source"] = "cloud"
        worker.overlays.clear()
        worker.accept(win)
        assert not worker.overlays
        await engine.close()


async def test_caller_browser_delivery_respects_current_source(tmp_path):
    install_fixture(tmp_path)
    game = {"available": True, "match_id": "game", "source": "local"}
    async with httpx.AsyncClient() as http:
        bus = EventBus()
        caller = Caller(tmp_path, http, bus, lambda: game)
        await caller.configure(CallerConfig(enabled=True, voice=VOICE, output="browser", run_on_local=False))
        await asyncio.sleep(0)
        with caller.subscribe() as queue:
            bus.publish("core", "turn_end", turn("local").data, kind="normalized")
            bus.publish("core", "turn_end", turn("cloud").data, kind="normalized")
            with pytest.raises(TimeoutError):
                await asyncio.wait_for(queue.get(), 0.12)
            game["source"] = "cloud"
            bus.publish("core", "turn_end", turn("cloud").data, kind="normalized")
            assert (await asyncio.wait_for(queue.get(), 1))["type"] == "play"
        await caller.close()


def test_local_screen_has_no_integration_controls_and_nav_shows_source_status(settings):
    with TestClient(create_app(settings)) as client:
        page = client.get("/local").text
        content = page.split('<main id="main"', 1)[1].split("</main>", 1)[0]
        assert "caller" not in content.lower()
        assert "integrations" not in content.lower()
        assert "Play on Autodarts" in page and "Play on Oche" in page
        assert (
            page.index('data-health="autodarts"')
            < page.index('data-health="local"')
            < page.index('data-health="cloud"')
        )
        client.post("/api/local/start", json={"input": "manual"})
        assert client.get("/api/status").json()["local"]["input"] == "manual"


def test_missing_native_detector_explains_windows_development(monkeypatch, tmp_path):
    from app.services import autodarts

    monkeypatch.setattr(autodarts, "AUTODARTS_BIN", tmp_path / "autodarts")
    monkeypatch.setattr(autodarts, "refresh_source", lambda: None)
    # Query the native environment without changing pathlib's platform globally.
    status = autodarts.get_status()
    assert not status["installed"]
    assert status["unavailable_reason"]
    if __import__("os").name == "nt":
        assert "Native Windows development" in status["unavailable_reason"]
