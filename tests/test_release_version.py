import unittest
from unittest.mock import patch

from scripts.release_version import detect


class ReleaseVersionTests(unittest.TestCase):
    def test_push_version_comparison(self):
        for previous, expected in (("0.1.0", True), ("0.1.1", False)):
            with self.subTest(previous=previous), \
                 patch("scripts.release_version.Path.read_text", return_value='[project]\nversion="0.1.1"'), \
                 patch("scripts.release_version.subprocess.run"), \
                 patch("scripts.release_version.subprocess.check_output", side_effect=["pyproject.toml\n", f'[project]\nversion="{previous}"']):
                self.assertEqual(detect("a" * 40), ("0.1.1", expected))

    def test_initial_push_and_manual_release(self):
        with patch("scripts.release_version.Path.read_text", return_value='[project]\nversion="0.1.1"'):
            self.assertEqual(detect("0" * 40), ("0.1.1", True))
            self.assertEqual(detect(None, manual=True), ("0.1.1", True))

    def test_adding_project_metadata_triggers_release(self):
        with patch("scripts.release_version.Path.read_text", return_value='[project]\nversion="0.1.1"'), \
             patch("scripts.release_version.subprocess.run"), \
             patch("scripts.release_version.subprocess.check_output", return_value=""):
            self.assertEqual(detect("a" * 40), ("0.1.1", True))

    def test_invalid_version_rejected(self):
        with patch("scripts.release_version.Path.read_text", return_value='[project]\nversion="main"'):
            with self.assertRaises(ValueError):
                detect(None)
