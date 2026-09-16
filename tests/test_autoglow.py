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
        self.processes = [Mock(status="stopped"), Mock(status="stopped")]
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

    def test_failed_start_stops_both_processes(self):
        self.processes[1].start.side_effect = RuntimeError("failure")
        with patch.object(autoglow, "installed", return_value=True):
            with self.assertRaises(RuntimeError):
                autoglow.start()
        for process in self.processes:
            process.stop.assert_called_once()

    def test_connection_requires_listener_and_web_running(self):
        folder = self.data / "autoglow"
        folder.mkdir()
        (folder / ".sync_status.json").write_text(json.dumps({"online": True}))
        for index, process in enumerate(self.processes):
            process.name = str(index)
            process.status = "running"
        self.assertTrue(autoglow.get_status()["autodarts_connected"])
        self.processes[1].status = "stopped"
        self.assertFalse(autoglow.get_status()["autodarts_connected"])
