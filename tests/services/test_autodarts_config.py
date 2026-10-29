import os
import tempfile
import unittest
from pathlib import Path
from unittest.mock import MagicMock, patch

from app.services import autodarts


class AutodartsConfigTests(unittest.TestCase):
    def test_starts_foreground_daemon_without_terminal(self):
        with patch('app.services.process_manager.subprocess.Popen') as popen, \
             patch.object(autodarts._process, '_proc', None), \
             patch('app.services.process_manager.open', new_callable=unittest.mock.mock_open):
            self.assertTrue(autodarts.start())
            self.assertEqual(popen.call_args.args[0], [str(autodarts.AUTODARTS_BIN), 'run'])

    def test_status_reports_bundled_version(self):
        with tempfile.TemporaryDirectory() as folder:
            binary = Path(folder) / 'autodarts'
            binary.touch()
            (Path(folder) / 'VERSION').write_text('2.0.2\n')
            with patch.object(autodarts, 'AUTODARTS_BIN', binary):
                status = autodarts.get_status()
            self.assertEqual(status['version'], '2.0.2')
            self.assertTrue(status['headless'])
            self.assertTrue(status['installed'])
