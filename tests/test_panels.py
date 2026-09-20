import re
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch
from uuid import uuid4

from fastapi.testclient import TestClient

from app.config import load_config, save_config
from app.main import app


class PanelTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        config_patch = patch("app.config.CONFIG_FILE", Path(self.temp.name) / "config.json")
        config_patch.start()
        self.addCleanup(config_patch.stop)
        self.client = TestClient(app)
        self.addCleanup(self.client.close)

    def save_panels(self, panels):
        return self.client.put("/panels/data", json={"panels": panels})

    def test_create_edit_delete_and_navigation(self):
        config = load_config()
        config["autostart_autodarts"] = True
        save_config(config)
        response = self.save_panels([{"name": "Lighting", "url": "http://wled.local"}])
        self.assertEqual(response.status_code, 200)
        panel = response.json()["panels"][0]
        self.assertTrue(load_config()["autostart_autodarts"])
        path = f'/panels/{panel["id"]}'
        for page in ("/config", "/play", "/autodarts", "/supervisor", "/autoglow"):
            response = self.client.get(page)
            self.assertEqual(response.status_code, 200)
            self.assertIn(path, response.text)
        response = self.client.get(path)
        self.assertIn('src="http://wled.local/"', response.text)
        self.assertIn('title="Lighting"', response.text)
        panel.update(name="New name", url="https://example.com/dashboard")
        self.assertEqual(self.save_panels([panel]).status_code, 200)
        self.assertIn('title="New name"', self.client.get(path).text)
        self.assertEqual(self.save_panels([]).status_code, 200)
        self.assertEqual(self.client.get(path).status_code, 404)
        self.assertNotIn(path, self.client.get("/play").text)

    def test_invalid_input_does_not_replace_saved_panels(self):
        panel = self.save_panels([{"name": "Valid", "url": "https://example.com"}]).json()["panels"][0]
        for url in ("javascript:alert(1)", "data:text/html,test", "file:///tmp/test", "/relative", "https://"):
            with self.subTest(url=url):
                self.assertEqual(self.save_panels([{"name": "Bad", "url": url}]).status_code, 422)
        self.assertEqual(self.save_panels([{"name": "   ", "url": "https://example.com"}]).status_code, 422)
        self.assertEqual(self.save_panels([panel, panel]).status_code, 422)
        self.assertEqual(load_config()["panels"], [panel])

    def test_old_config_and_general_settings_preserve_panels(self):
        save_config({"lang": "en"})
        self.assertEqual(load_config()["panels"], [])
        panel = self.save_panels([{"name": "Tool", "url": "http://192.168.1.10"}]).json()["panels"][0]
        response = self.client.post("/config/data", json={
            "autostart_autodarts": False,
            "autohide_navbar_on_board": True,
            "autohide_navbar_on_play": False,
            "autohide_navbar_on_autodarts": False,
            "autohide_navbar_on_autoglow": False,
            "autohide_navbar_on_panels": False,
            "show_play_in_navbar": True,
            "show_board_in_navbar": True,
            "show_autodarts_in_navbar": True,
            "show_autoglow_in_navbar": True,
            "show_panels_in_navbar": True,
        })
        self.assertEqual(response.status_code, 200)
        self.assertEqual(load_config()["panels"], [panel])
        self.assertEqual(self.client.get(f"/panels/{uuid4()}").status_code, 404)

    def test_panel_names_are_escaped(self):
        panel = self.save_panels([{"name": "<script>alert(1)</script>", "url": "https://example.com"}]).json()["panels"][0]
        response = self.client.get(f'/panels/{panel["id"]}')
        self.assertNotIn("<script>alert(1)</script>", response.text)
        self.assertIn("&lt;script&gt;", response.text)

    def test_new_tab_preference_is_saved_and_used_in_navigation(self):
        response = self.save_panels([{
            "name": "Google", "url": "https://www.google.com", "open_in_new_tab": True,
        }])
        self.assertEqual(response.status_code, 200)
        panel = response.json()["panels"][0]
        self.assertTrue(load_config()["panels"][0]["open_in_new_tab"])
        page = self.client.get("/config").text
        self.assertIn('href="https://www.google.com/"', page)
        self.assertIn('>Google</a', page)
        self.assertNotIn(f'href="/panels/{panel["id"]}"', page)
        self.assertNotIn("Your panels", page)
        panel["open_in_new_tab"] = False
        self.assertEqual(self.save_panels([panel]).status_code, 200)
        page = self.client.get("/config").text
        self.assertIn(f'href="/panels/{panel["id"]}"', page)
        # The dropdown no longer has a separate "open in a new tab" shortcut
        # arrow - only the panel's own name link, following its own setting.
        self.assertNotIn("panel-external", page)

    def test_nav_items_are_shown_by_default_and_hidden_via_config(self):
        # Nav links are always rendered (so client-side JS can toggle them
        # live without a reload) and hidden via the `hidden` attribute.
        def hidden_ids(page):
            return {
                nav_id for nav_id in ("nav-link-play", "nav-link-board", "nav-link-autodarts", "nav-link-autoglow")
                if re.search(rf'id="{nav_id}"[^>]*\bhidden\b', page)
            }

        page = self.client.get("/").text
        self.assertEqual(hidden_ids(page), set())

        response = self.client.post("/config/data", json={
            "autostart_autodarts": False,
            "autohide_navbar_on_board": False,
            "autohide_navbar_on_play": False,
            "autohide_navbar_on_autodarts": False,
            "autohide_navbar_on_autoglow": False,
            "autohide_navbar_on_panels": False,
            "show_play_in_navbar": False,
            "show_board_in_navbar": False,
            "show_autodarts_in_navbar": True,
            "show_autoglow_in_navbar": True,
            "show_panels_in_navbar": True,
        })
        self.assertEqual(response.status_code, 200)

        page = self.client.get("/").text
        self.assertEqual(hidden_ids(page), {"nav-link-play", "nav-link-board"})
        # Hiding a nav item doesn't take down its page.
        self.assertEqual(self.client.get("/play").status_code, 200)
        self.assertEqual(self.client.get("/autodarts").status_code, 200)


if __name__ == "__main__":
    unittest.main()
