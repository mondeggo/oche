import asyncio

import httpx
import pytest
from fastapi.testclient import TestClient

from app.core.autodarts.detection import LocalDetection
from app.main import create_app
from app.core.integrations.autodarts import process as autodarts


@pytest.mark.parametrize("status", ["Throw", "Takeout", "Calibrating", "Starting", "Stopped"])
async def test_detection_health_uses_pipeline_state_without_cloud_or_scored_throws(status):
    running = status != "Stopped"

    def respond(request):
        assert str(request.url) == "http://127.0.0.1:3180/api/state"
        return httpx.Response(200, json={"running": running, "status": status, "connected": False})

    async with httpx.AsyncClient(transport=httpx.MockTransport(respond)) as http:
        detector = LocalDetection(http)
        await detector.check({"status": "running", "pid": 42})
        health = detector.status()
        assert health["running"] == running
        assert health["detecting"] == (status in {"Throw", "Takeout"})
        assert (
            health["state"]
            == {
                "Throw": "detecting",
                "Takeout": "detecting",
                "Calibrating": "calibrating",
                "Starting": "starting",
                "Stopped": "stopped",
            }[status]
        )


async def test_health_cache_deduplicates_polls_and_process_stop_clears_green_immediately():
    requests = []

    def respond(request):
        requests.append(request)
        return httpx.Response(200, json={"running": True, "status": "Throw"})

    async with httpx.AsyncClient(transport=httpx.MockTransport(respond)) as http:
        detector = LocalDetection(http)
        process = {"status": "running", "pid": 42}
        await asyncio.gather(detector.check(process), detector.check(process))
        assert len(requests) == 1
        assert detector.status()["detecting"]
        await detector.check({"status": "stopped", "pid": None})
        assert not detector.status()["detecting"]
        await detector.check({"status": "running", "pid": 43})
        assert len(requests) == 2


@pytest.mark.parametrize(
    "payload", [None, {}, {"running": "true", "status": "Throw"}, {"running": True, "status": None}]
)
async def test_malformed_health_response_never_reports_detecting(payload):
    async with httpx.AsyncClient(
        transport=httpx.MockTransport(lambda request: httpx.Response(200, json=payload))
    ) as http:
        detector = LocalDetection(http)
        await detector.check({"status": "running", "pid": 42})
        assert detector.status()["state"] == "unavailable"
        assert not detector.status()["detecting"]


async def test_api_failure_clears_previous_detection_health():
    failed = False

    def respond(request):
        if failed:
            raise httpx.ConnectError("Unavailable", request=request)
        return httpx.Response(200, json={"running": True, "status": "Throw"})

    async with httpx.AsyncClient(transport=httpx.MockTransport(respond)) as http:
        detector = LocalDetection(http)
        await detector.check({"status": "running", "pid": 42})
        assert detector.status()["detecting"]
        failed = True
        detector.checked_at = None
        await detector.check({"status": "running", "pid": 42})
        assert detector.status()["running"]
        assert not detector.status()["detecting"]
        assert detector.status()["state"] == "unavailable"


def test_status_checks_detection_without_an_active_local_game(settings, monkeypatch):
    monkeypatch.setattr(autodarts, "get_status", lambda: {"status": "running", "pid": 42})
    app = create_app(
        settings,
        transport=httpx.MockTransport(
            lambda request: httpx.Response(200, json={"running": True, "status": "Throw", "connected": False})
        ),
    )
    with TestClient(app) as client:
        status = client.get("/api/status").json()
        assert not status["local"]["active"]
        assert status["local_detection"]["detecting"]
        assert status["cloud"]["state"] == "unconfigured"


def test_manual_game_does_not_turn_detection_status_green(settings, monkeypatch):
    def unexpected(request):
        pytest.fail(f"Stopped detector should not be contacted: {request.url}")

    monkeypatch.setattr(autodarts, "get_status", lambda: {"status": "stopped", "pid": None})
    with TestClient(create_app(settings, transport=httpx.MockTransport(unexpected))) as client:
        assert client.post("/api/local/start", json={"input": "manual"}).status_code == 200
        status = client.get("/api/status").json()
        assert status["local"]["active"]
        assert status["local_detection"]["state"] == "stopped"
        assert not status["local_detection"]["detecting"]
