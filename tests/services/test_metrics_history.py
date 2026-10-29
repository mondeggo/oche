import asyncio
from unittest.mock import patch

import pytest
from fastapi.testclient import TestClient
from pydantic import ValidationError

from app.config import load_config
from app.core.config import Settings
from app.main import create_app
from app.services.metrics_history import FIELDS, HistoryConfig, MetricsHistory


def test_history_is_bounded_by_capacity_and_time_and_off_clears_it(monkeypatch):
    history = MetricsHistory(HistoryConfig(retention_minutes=5, sample_seconds=5))
    for index in range(100):
        history.record({key: index for key in FIELDS}, now=index * 5)
    assert len(history.samples) == 60
    assert history.samples[0]["timestamp"] == 200
    monkeypatch.setattr("app.services.metrics_history.time.time", lambda: 1000)
    assert history.status()["now"] == 1000
    assert history.status()["samples"] == []
    history.record({"cpu_percent": 42}, now=1000)
    history.configure(HistoryConfig(enabled=False))
    history.record({"cpu_percent": 80}, now=1001)
    assert history.status()["samples"] == []


@pytest.mark.parametrize(
    "values",
    [
        {"retention_minutes": 1},
        {"sample_seconds": 1},
        {"retention_minutes": 1440, "sample_seconds": 5},
        {"sample_seconds": 301},
        {"retention_minutes": 1441},
    ],
)
def test_history_rejects_unbounded_or_excessively_frequent_sampling(values):
    with pytest.raises(ValidationError):
        HistoryConfig(**values)


async def test_sampler_runs_without_a_page_and_stops_sampling_when_disabled(monkeypatch):
    sampled = asyncio.Event()
    calls = []

    def metrics():
        calls.append(True)
        sampled.set()
        return {"cpu_percent": 12}

    monkeypatch.setattr("app.services.metrics_history.get_status", metrics)
    history = MetricsHistory(HistoryConfig())
    history.start()
    try:
        await asyncio.wait_for(sampled.wait(), 1)
        assert len(history.samples) == 1
        history.configure(HistoryConfig(enabled=False))
        await asyncio.sleep(0)
        assert len(calls) == 1 and not history.samples
        sampled.clear()
        history.configure(HistoryConfig())
        await asyncio.wait_for(sampled.wait(), 1)
        assert len(calls) == 2
    finally:
        await history.close()
    assert history.task.done()


def test_history_settings_persist_and_sampling_stops_with_the_application(tmp_path):
    settings = Settings(_env_file=None, data_dir=tmp_path / "core")
    with patch("app.config.CONFIG_FILE", tmp_path / "settings.json"):
        with TestClient(create_app(settings)) as client:
            response = client.get("/system/history")
            assert response.headers["Cache-Control"] == "no-store"
            result = response.json()
            assert result["config"] == {"enabled": True, "retention_minutes": 60, "sample_seconds": 30}
            assert result["capacity"] == 120
            value = {"enabled": False, "retention_minutes": 90, "sample_seconds": 60}
            assert client.put("/system/history", json=value).json()["samples"] == []
            assert load_config()["metrics_history"] == value
            assert client.put("/system/history", json={"sample_seconds": 1}).status_code == 422
            assert (
                client.put(
                    "/system/history", json=value, headers={"Origin": "https://other.test"}
                ).status_code
                == 403
            )
            service = client.app.state.metrics_history
        assert service.task.done()
        with TestClient(create_app(settings)) as client:
            assert client.get("/system/history").json()["config"] == value
            assert client.get("/system/history").json()["samples"] == []
