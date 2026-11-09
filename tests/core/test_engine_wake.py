import time

import httpx
import pytest
from fastapi.testclient import TestClient

from app.core.integrations.ochecore.wake import clear_stale_visit, hold_reason, wake
from app.main import create_app


def ochecore(state, calls):
    def handler(request):
        calls.append((request.method, request.url.path))
        if request.url.path == "/api/v1/status":
            return httpx.Response(200, json={"state": state})
        if request.url.path == "/api/v1/command":
            assert request.content == b'{"command":"start"}'
            return httpx.Response(200, json={"state": "running"})
        return httpx.Response(200, json={"connected": True, "running": True, "status": "throw", "throws": []})

    return handler


@pytest.mark.parametrize("state,sent", [("standby", True), ("stopped", True), ("running", False)])
async def test_ochecore_starts_only_when_its_cameras_sleep(state, sent):
    calls = []
    async with httpx.AsyncClient(transport=httpx.MockTransport(ochecore(state, calls))) as http:
        assert await wake(http, "http://127.0.0.1:5180") is sent
    assert (("POST", "/api/v1/command") in calls) is sent


def test_a_vision_game_wakes_ochecore_only_when_it_is_the_chosen_engine(settings, monkeypatch):
    calls = []
    monkeypatch.setattr("app.services.engines.chosen", lambda: "ochecore")
    with TestClient(create_app(settings, transport=httpx.MockTransport(ochecore("standby", calls)))) as client:
        assert client.post("/api/local/start", json={"input": "manual"}).status_code == 200
        assert ("GET", "/api/v1/status") not in calls  # Manual darts need no cameras.
        client.post("/api/local/stop", json={})
        assert client.post("/api/local/start", json={"input": "autodarts"}).status_code == 200
        assert ("POST", "/api/v1/command") in calls
        client.post("/api/local/stop", json={})
    calls.clear()
    monkeypatch.setattr("app.services.engines.chosen", lambda: "autodarts")
    with TestClient(create_app(settings, transport=httpx.MockTransport(ochecore("standby", calls)))) as client:
        assert client.post("/api/local/start", json={"input": "autodarts"}).status_code == 200
        assert ("GET", "/api/v1/status") not in calls


def test_a_detector_on_another_computer_is_never_woken(settings, monkeypatch):
    calls = []
    monkeypatch.setattr("app.services.engines.chosen", lambda: "ochecore")
    remote = settings.model_copy(update={"local_autodarts_url": "http://192.168.1.50:5180"})
    with TestClient(create_app(remote, transport=httpx.MockTransport(ochecore("standby", calls)))) as client:
        assert client.post("/api/local/start", json={"input": "autodarts"}).status_code == 200
        assert ("POST", "/api/v1/command") not in calls


PHANTOM = {"label": "?", "uncertain": True}


def ochecore_status(statuses, calls):
    """Answers each status poll with the next status; the last one repeats."""
    def handler(request):
        calls.append((request.method, request.url.path, request.content))
        if request.url.path == "/api/v1/status":
            return httpx.Response(200, json=statuses.pop(0) if len(statuses) > 1 else statuses[0])
        return httpx.Response(200, json={"state": "running"})

    return handler


@pytest.mark.parametrize(
    "statuses,sent",
    [
        # The dart OcheCore could not place outlived its restart; the board is empty.
        ([{"state": "running", "physical_darts": 0, "throws": [PHANTOM]}], True),
        # The cameras are still opening, then count an empty board.
        (
            [
                {"state": "standby", "physical_darts": None, "throws": [PHANTOM]},
                {"state": "running", "physical_darts": None, "throws": [PHANTOM]},
                {"state": "running", "physical_darts": 0, "throws": [PHANTOM]},
            ],
            True,
        ),
        # Darts left on the board need a takeout, not a reset.
        ([{"state": "running", "physical_darts": 3, "throws": [PHANTOM] * 3}], False),
        ([{"state": "running", "physical_darts": 0, "throws": []}], False),
        ([{"state": "running", "physical_darts": None, "throws": [PHANTOM]}], False),
    ],
)
async def test_a_new_game_resets_only_darts_ochecore_lists_but_the_board_lacks(statuses, sent):
    calls = []
    async with httpx.AsyncClient(transport=httpx.MockTransport(ochecore_status(statuses, calls))) as http:
        assert await clear_stale_visit(http, "http://127.0.0.1:5180", seconds=1) is sent
    assert (("POST", "/api/v1/command", b'{"command":"reset"}') in calls) is sent


def test_starting_a_vision_game_resets_a_stale_ochecore_visit_and_keeping_it_awake_does_not(
    settings, monkeypatch
):
    calls = []
    monkeypatch.setattr("app.services.engines.chosen", lambda: "ochecore")
    status = [{"state": "running", "physical_darts": 0, "throws": [PHANTOM]}]
    reset = ("POST", "/api/v1/command", b'{"command":"reset"}')
    app = create_app(settings, transport=httpx.MockTransport(ochecore_status(status, calls)))
    with TestClient(app) as client:
        assert client.post("/api/local/start", json={"input": "autodarts"}).status_code == 200
        deadline = time.monotonic() + 2
        while reset not in calls and time.monotonic() < deadline:
            time.sleep(0.02)
        assert calls.count(reset) == 1
        calls.clear()
        assert client.portal.call(app.state.runtime.wake_engine) is False
        assert reset not in calls


def live(**changes):
    """A running OcheCore with two calibrated, streaming cameras and an empty, lit board."""
    snapshot = {
        "state": "running",
        "mode": "live",
        "config": {"cameras": [{"slot": 1, "device": "/dev/video0"}, {"slot": 2, "device": "/dev/video2"}]},
        "cameras": [{"slot": 1, "state": "streaming"}, {"slot": 2, "state": "streaming"}],
        "capabilities": {"vision": True},
        "blind_cameras": [],
        "scene": "empty",
        "physical_darts": 0,
        "throws": [],
    }
    return {**snapshot, **changes}


@pytest.mark.parametrize(
    "snapshot,reason",
    [
        (live(), None),
        # Standby and demo boards are handled elsewhere: the game wakes them.
        (live(state="standby"), None),
        (live(mode="demo", scene="unknown"), None),
        (live(cameras=[{"slot": 1, "state": "streaming"}, {"slot": 2, "state": "starting"}]), "cameras to start"),
        (live(cameras=[{"slot": 1, "state": "error"}, {"slot": 2, "state": "streaming"}]), "camera has an error"),
        (live(capabilities={"vision": False}), "Calibrate"),
        (live(blind_cameras=[2], scene="unknown", physical_darts=None), "Too dark"),
        (live(scene="unknown", physical_darts=None), "to see the board"),
        (live(scene="darts_present", physical_darts=None), "Clear the board"),
        (live(scene="partial_takeout", physical_darts=1), "Clear the board"),
    ],
)
def test_ochecore_holds_the_first_dart_until_the_board_is_lit_and_empty(snapshot, reason):
    result = hold_reason(snapshot)
    assert result is None if reason is None else reason in result
