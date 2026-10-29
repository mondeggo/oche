import json

import httpx
import pytest
from fastapi.testclient import TestClient

from app.i18n import catalogue, translate
from app.main import create_app
from scripts.check_translations import check


def test_all_catalogues_cover_marked_messages_and_preserve_placeholders():
    assert check() == []
    assert len(catalogue("fr")) > 1100
    assert translate("New unknown message", "de") == "New unknown message"
    assert translate("{0} wins!", "de", **{"0": "Zoë"}) == "Zoë gewinnt!"


@pytest.mark.parametrize(
    "language,label",
    [
        ("en", "Play on Oche"),
        ("fr", "Jouer sur Oche"),
        ("de", "Auf Oche spielen"),
        ("pt-PT", "Jogar no Oche"),
    ],
)
def test_language_selection_renders_local_pages_and_scoreboard(settings, language, label):
    with TestClient(create_app(settings)) as client:
        response = client.patch("/api/ui", json={"language": language, "profile_id": "default"})
        assert response.status_code == 200
        assert response.json()["language"] == language
        for path in ("/", "/local", "/config", "/supervisor", "/autodarts/terminal"):
            page = client.get(path)
            assert page.status_code == 200, path
            assert f'lang="{language}"' in page.text
            assert 'id="oche-translations"' in page.text
            if path != "/autodarts/terminal":
                assert label in page.text
                assert 'id="language-select"' in page.text
        assert f'lang="{language}"' in client.get("/scoreboard").text
    with TestClient(create_app(settings)) as client:
        assert client.get("/api/ui").json()["language"] == language


def test_profile_theme_and_language_clone_switch_export_import_and_delete(settings):
    with TestClient(create_app(settings)) as client:
        client.patch("/api/ui", json={"theme": "light", "language": "fr"})
        cloned = client.post("/api/profiles", json={"name": "German", "source": "current"}).json()[
            "active_profile"
        ]
        assert client.get("/api/ui").json()["language"] == "fr"
        client.patch("/api/ui", json={"theme": "dark", "language": "de"})
        client.put("/api/profile", json={"id": "default"})
        assert client.get("/api/ui").json()["theme"] == "light"
        assert client.get("/api/ui").json()["language"] == "fr"
        client.put("/api/profile", json={"id": cloned})
        assert client.get("/api/ui").json()["language"] == "de"
        exported = client.get(f"/api/profiles/{cloned}/export").json()
        assert exported["appearance"] == {"theme": "dark", "language": "de"}
        imported = client.post("/api/profiles/import", json={"profile": exported}).json()["active_profile"]
        assert client.get("/api/ui").json()["language"] == "de"
        client.delete(f"/api/profiles/{imported}")
        ui = client.get("/api/ui").json()
        expected = client.get(f"/api/profiles/{ui['profile_id']}/export").json()["appearance"]
        assert ui["language"] == expected["language"]


def test_old_profiles_migrate_from_existing_appearance_and_ignore_stale_cache(settings):
    with TestClient(create_app(settings)) as client:
        client.post("/api/profiles", json={"name": "Second"})
    path = settings.data_dir / "profiles.json"
    data = json.loads(path.read_text(encoding="utf-8"))
    data.pop("appearances")
    path.write_text(json.dumps(data), encoding="utf-8")
    (settings.data_dir / "ui.json").write_text('{"theme":"light","language":"pt-PT"}', encoding="utf-8")
    settings.ui_theme = None
    with TestClient(create_app(settings)) as client:
        assert client.get("/api/ui").json()["language"] == "pt-PT"
        assert client.get("/api/ui").json()["theme"] == "light"
        client.patch("/api/ui", json={"language": "de"})
        client.put("/api/profile", json={"id": "default"})
        assert client.get("/api/ui").json()["language"] == "pt-PT"
    (settings.data_dir / "ui.json").write_text('{"theme":"dark","language":"en"}', encoding="utf-8")
    with TestClient(create_app(settings)) as client:
        assert client.get("/api/ui").json()["language"] == "pt-PT"


def test_stale_profile_appearance_request_is_rejected(settings):
    with TestClient(create_app(settings)) as client:
        client.post("/api/profiles", json={"name": "Second"})
        response = client.patch("/api/ui", json={"theme": "light", "language": "fr", "profile_id": "default"})
        assert response.status_code == 409
        assert client.get("/api/ui").json()["language"] == "en"
        assert client.patch("/api/ui", json={"language": "es"}).status_code == 422


def test_appearance_save_failure_rolls_back_profile_selection(settings, monkeypatch):
    from app.core.autodarts.errors import ConnectionProblem

    app = create_app(settings)
    with TestClient(app) as client:
        client.patch("/api/ui", json={"language": "fr"})
        client.post("/api/profiles", json={"name": "English", "source": "blank"})
        before = (settings.data_dir / "profiles.json").read_bytes()

        async def fail(update):
            raise ConnectionProblem("Disk full")

        monkeypatch.setattr(app.state.ui, "configure", fail)
        response = client.put("/api/profile", json={"id": "default"})
        assert response.status_code == 409
        assert client.get("/api/ui").json()["language"] == "en"
        assert (settings.data_dir / "profiles.json").read_bytes() == before


def test_translating_does_not_change_profile_names_or_runtime_identifiers(settings):
    def offline(request):
        raise AssertionError(f"Translation contacted external service: {request.url}")

    with TestClient(create_app(settings, transport=httpx.MockTransport(offline))) as client:
        result = client.post("/api/profiles", json={"name": "Settings"}).json()
        client.patch("/api/ui", json={"language": "de"})
        page = client.get("/local").text
        assert f'value="{result["active_profile"]}" selected>Settings</option>' in page
        client.post("/api/local/start", json={"input": "manual", "players": ["Settings"]})
        assert client.get("/api/game").json()["player"]["name"] == "Settings"
        assert client.get("/api/game").json()["source"] == "local"
