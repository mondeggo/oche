import json
from pathlib import Path
import tempfile
import unittest
from unittest.mock import Mock, patch

from app.services import autoglow


class AutoGlowTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.data = Path(self.temp.name)
        self.processes = [Mock(status="stopped")]
        for name, value in [("DATA_DIR", self.data), ("_processes", self.processes)]:
            patcher = patch.object(autoglow, name, value)
            patcher.start()
            self.addCleanup(patcher.stop)

    def test_missing_source_does_not_launch(self):
        with patch.object(autoglow, "SOURCE", self.data):
            self.assertFalse(autoglow.start())
        for process in self.processes:
            process.start.assert_not_called()

    def test_restart_clears_old_connection_state(self):
        folder = self.data / "autoglow"
        folder.mkdir()
        status = folder / ".sync_status.json"
        status.write_text('{"local": true}')
        with patch.object(autoglow, "installed", return_value=True):
            autoglow.start()
        self.assertFalse(status.exists())
        for process in self.processes:
            process.start.assert_called_once()

    def test_failed_start_stops_server(self):
        self.processes[0].start.side_effect = RuntimeError("failure")
        with patch.object(autoglow, "installed", return_value=True):
            with self.assertRaises(RuntimeError):
                autoglow.start()
        for process in self.processes:
            process.stop.assert_called_once()

    def test_connection_requires_server_running(self):
        folder = self.data / "autoglow"
        folder.mkdir()
        (folder / ".sync_status.json").write_text(json.dumps({"online": True}))
        for index, process in enumerate(self.processes):
            process.name = str(index)
            process.status = "running"
        self.assertTrue(autoglow.get_status()["autodarts_connected"])
        self.processes[0].status = "stopped"
        self.assertFalse(autoglow.get_status()["autodarts_connected"])
        self.assertEqual(autoglow.get_status()["status"], "stopped")

    def test_server_controls(self):
        for action in ("start", "stop", "restart"):
            process = self.processes[0]
            process.reset_mock()
            autoglow.control_process("web", action)
            self.assertEqual(process.start.call_count, int(action in ("start", "restart")))
            self.assertEqual(process.stop.call_count, int(action in ("stop", "restart")))

    def test_current_source_layout_is_installed(self):
        for name in ("server.py", "core/engine.py", "web/index.html"):
            file = self.data / name
            file.parent.mkdir(parents=True, exist_ok=True)
            file.touch()
        with patch.object(autoglow, "SOURCE", self.data):
            self.assertTrue(autoglow.installed())
