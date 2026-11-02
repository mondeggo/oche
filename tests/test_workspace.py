"""Integration boundaries introduced by the single-application application."""

import re
from html.parser import HTMLParser
from unittest.mock import patch

import httpx
import pytest
from fastapi.testclient import TestClient

import oche_runtime as store
from app.core.config import Settings
from app.main import create_app
from app.navigation import HOME_ROUTES


class Document(HTMLParser):
    def __init__(self):
        super().__init__()
        self.ids = []
        self.assets = set()

    def handle_starttag(self, tag, attrs):
        attrs = dict(attrs)
        if "id" in attrs:
            self.ids.append(attrs["id"])
        for key in ("href", "src"):
            if attrs.get(key, "").startswith("/static/"):
                self.assets.add(attrs[key].split("#")[0])


@pytest.fixture
def application(tmp_path):
    def offline(request):
        raise AssertionError(f"Unconfigured Oche made an external request: {request.url}")

    return create_app(
        Settings(_env_file=None, data_dir=tmp_path / "core"), transport=httpx.MockTransport(offline)
    )


def test_workspace_assets_and_ids_are_valid_and_core_has_no_second_application(application):
    with TestClient(application) as client:
        page = client.get("/")
        assert page.status_code == 200
        document = Document()
        document.feed(page.text)
        assert len(document.ids) == len(set(document.ids)), "Duplicate IDs break integration controls"
        for asset in document.assets:
            assert client.get(asset).status_code == 200, asset
        assert "app-profile" in document.ids
        assert not hasattr(application.state.runtime.settings, "port")
        assert not hasattr(application.state.runtime.settings, "host")
        assert client.get("/healthz").json()["ok"] is True
        assert client.get("/api/status").json()["auth"]["state"] == "disconnected"
    assert application.state.runtime.tasks == []


def test_sidebar_reaches_every_page_and_marks_the_current_one(application, tmp_path):
    pages = {
        "/integrations/autodarts/board": "Autodarts",
        "/integrations/cameras": "Camera Enhancer",
        "/supervisor?service=system": "Status",
        "/supervisor?service=updates": "Updates",
        "/config": "Settings",
    }
    with (
        TestClient(application) as client,
        patch("app.config.CONFIG_FILE", tmp_path / "settings.json"),
    ):
        for path, label in pages.items():
            page = client.get(path)
            assert page.status_code == 200, path
            document = Document()
            document.feed(page.text)
            assert len(document.ids) == len(set(document.ids)), path
            sidebar = page.text[page.text.index('<aside id="app-sidebar"') : page.text.index("</aside>")]
            current = re.findall(r'<a [^>]*data-label="([^"]+)"[^>]*aria-current="?page', sidebar)
            assert current == [label], path
            for target in pages:
                assert f'href="{target}"' in sidebar, (path, target)
            assert 'href="/integrations"' in sidebar
            assert 'id="sidebar-board"' not in sidebar
            integrations = sidebar[sidebar.index('href="/integrations"') : sidebar.index('href="/profiles"')]
            # Integrations add their pages under Integrations, not System.
            assert 'id="nav-link-cameras"' in integrations and 'id="nav-link-autodarts"' in integrations
            system = sidebar[sidebar.index('id="sidebar-system"') :]
            assert 'id="nav-link-autodarts"' not in system
            assert '<option value="default" selected>Default</option>' in sidebar
            for status in ("autodarts", "cloud"):
                assert f'data-health="{status}"' in sidebar
        assert 'href="/integrations/cameras"' in client.get("/").text.split('id="core-main"')[1]
        # Camera tools and each System page retain their own collapse preferences.
        assert client.post("/config/data", json={"autohide_navbar_on_cameras": True}).status_code == 200
        collapsed = {
            path: 'data-sidebar-default="1"' in client.get(path).text
            for path in ("/integrations/autodarts/board", "/integrations/cameras", "/supervisor?service=system")
        }
        assert collapsed == {
            "/integrations/autodarts/board": False,
            "/integrations/cameras": True,
            "/supervisor?service=system": False,
        }
        # Pages can be hidden one at a time; a section hides with its last page.
        assert client.post("/config/data", json={"show_page_cameras": False}).status_code == 200
        page = client.get("/config").text
        assert re.search(r'id="nav-link-cameras"[^>]*\shidden', page)
        assert 'href="/integrations"' in page
        assert not re.search(r'id="sidebar-system"[^>]*\shidden', page)
        assert (
            client.post(
                "/config/data", json={"show_page_system": False, "show_page_updates": False}
            ).status_code
            == 200
        )
        assert re.search(r'id="sidebar-system"[^>]*\shidden', client.get("/config").text)
        moved = client.get("/autodarts/cameras", follow_redirects=False)
        assert moved.status_code == 307
        assert moved.headers["location"] == "/integrations/cameras"


def test_camera_card_and_sidebar_share_persisted_visibility(application, tmp_path):
    with (
        TestClient(application) as client,
        patch("app.config.CONFIG_FILE", tmp_path / "settings.json"),
    ):
        before = client.get("/config/data").json()
        for enabled in (False, True):
            assert client.post("/config/data", json={"show_page_cameras": enabled}).status_code == 200
            page = client.get("/").text
            assert re.search(rf'id="cameras-toggle"[^>]*aria-checked="{str(enabled).lower()}"', page)
            assert bool(re.search(r'id="nav-link-cameras"[^>]*\shidden', page)) != enabled
            assert bool(re.search(r'id="cameras-settings"[^>]*\shidden', page)) != enabled
            saved = client.get("/config/data").json()
            assert saved == {**before, "show_page_cameras": enabled}
            # A fresh page uses the same saved setting as the Integrations card.
            assert (
                bool(re.search(r'id="nav-link-cameras"[^>]*\shidden', client.get("/supervisor").text))
                != enabled
            )


def test_direct_workspace_urls_refresh_and_camera_aliases_remain_available(application):
    with TestClient(application) as client:
        for path in HOME_ROUTES:
            response = client.get(path)
            assert response.status_code == 200, path
            assert 'id="core-main"' in response.text
            assert 'href="/integrations/cameras"' in response.text
            assert 'href="/integrations/caller"' in response.text
            assert response.headers["Cache-Control"] == "no-cache"
        assert "Camera Enhancer" in client.get("/integrations/cameras").text
        for legacy in ("/supervisor?service=cameras", "/autodarts/cameras"):
            response = client.get(legacy, follow_redirects=False)
            assert response.status_code == 307
            assert response.headers["location"] == "/integrations/cameras"


def test_appearance_and_connection_persist_in_the_same_runtime(application):
    with TestClient(application) as client:
        assert client.patch("/api/ui", json={"theme": "dark"}).status_code == 200
        assert 'data-theme="dark"' in client.get("/").text
        assert 'data-theme="dark"' in client.get("/config").text
        assert client.put("/api/config", json={"client_id": "oche-test"}).status_code == 200
        assert client.get("/api/config").json()["client_id"] == "oche-test"


def test_restart_requires_launcher_and_serializes_with_updates(application, tmp_path):
    with TestClient(application) as client, patch.object(store, "DATA", tmp_path):
        with patch.dict("os.environ", {"OCHE_LAUNCHER": ""}):
            assert client.post("/system/restart").status_code == 409
        with (
            patch.dict("os.environ", {"OCHE_LAUNCHER": "1"}),
            patch.object(store, "ROOT", tmp_path / "modules"),
        ):
            assert client.post("/system/restart", headers={"Origin": "https://other.test"}).status_code == 403
            assert client.post("/system/restart").status_code == 202
            assert (tmp_path / "state/restart-oche").exists()
            store.write_state("oche", {"pending": True, "active": "2.0.1"})
            assert client.post("/system/restart").status_code == 409
            # Oche is listed first, above the modules it updates.
            assert [m["name"] for m in client.get("/updates/status").json()["modules"]] == [
                "oche",
                "autodarts",
            ]


def test_core_configuration_reads_the_oche_environment(tmp_path, monkeypatch):
    monkeypatch.setenv("OCHE_CORE_CLIENT_ID", "current-client")
    assert Settings(_env_file=None, data_dir=tmp_path).client_id == "current-client"
    monkeypatch.delenv("OCHE_CORE_CLIENT_ID")
    assert Settings(_env_file=None, data_dir=tmp_path).client_id == ""
