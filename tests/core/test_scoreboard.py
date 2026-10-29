import json

from fastapi.testclient import TestClient

from app.main import create_app


def test_scoreboard_settings_persist_and_the_display_page_stamps_theme(settings):
    with TestClient(create_app(settings)) as client:
        assert client.get("/api/scoreboard").json() == {
            "run_on_cloud": True,
            "run_on_local": True,
            "enabled": False,
            "title": "",
            "theme": "auto",
            "show_last_dart": True,
            "show_checkout": True,
            "show_sets_legs": True,
            "show_bots": True,
            "error": None,
        }
        # "auto" follows the saved UI theme (dark by default).
        assert 'data-theme="dark"' in client.get("/scoreboard").text
        saved = client.patch(
            "/api/scoreboard", json={"enabled": True, "title": "Pub Night", "theme": "light"}
        )
        assert saved.status_code == 200
        assert saved.json()["title"] == "Pub Night"
        assert 'data-theme="light"' in client.get("/scoreboard").text
        client.patch("/api/ui", json={"theme": "light"})
        client.patch("/api/scoreboard", json={"theme": "auto"})
        assert 'data-theme="light"' in client.get("/scoreboard").text
        assert client.get("/api/scoreboard").headers["cache-control"] == "no-store"
        assert client.get("/scoreboard").headers["cache-control"] == "no-cache"
    with TestClient(create_app(settings)) as client:
        assert client.get("/api/scoreboard").json()["title"] == "Pub Night"


def test_scoreboard_rejects_unknown_fields_and_bad_values(settings):
    with TestClient(create_app(settings)) as client:
        assert client.patch("/api/scoreboard", json={"theme": "purple"}).status_code == 422
        assert client.patch("/api/scoreboard", json={"unknown": True}).status_code == 422
        assert client.patch("/api/scoreboard", json={"title": "x" * 61}).status_code == 422


def test_invalid_scoreboard_file_can_be_repaired_without_blocking_core(settings):
    settings.data_dir.mkdir(exist_ok=True)
    (settings.data_dir / "scoreboard.json").write_text('{"theme":"invalid"}', encoding="utf-8")
    with TestClient(create_app(settings)) as client:
        assert client.get("/healthz").status_code == 200
        assert "Cannot load" in client.get("/api/scoreboard").json()["error"]
        assert client.patch("/api/scoreboard", json={"title": "Fixed"}).json()["error"] is None
    assert json.loads((settings.data_dir / "scoreboard.json").read_text())["title"] == "Fixed"
