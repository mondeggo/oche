import httpx
import pytest
from fastapi.testclient import TestClient

from app.core.integrations.ochecore.wake import wake
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
