import asyncio
import os
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from app.core.integrations.autodarts.terminal import AutodartsTerminal
from app.core.integrations.ochecore import process as ochecore
from app.core.integrations.ochecore.terminal import OcheCoreTerminal


@unittest.skipUnless(os.name == 'posix', 'PTYs require Linux/macOS')
class TerminalProcessTests(unittest.IsolatedAsyncioTestCase):
    async def test_interactive_client_resize_and_cleanup(self):
        with tempfile.TemporaryDirectory() as folder:
            # The terminal runs the executable named autodarts beside AUTODARTS_BIN.
            client = Path(folder) / 'autodarts'
            client.write_text('''#!/usr/bin/env python3
import os, sys, termios, tty
tty.setraw(0)
print('ARGS=' + repr(sys.argv[1:]), flush=True)
while True:
    data = os.read(0, 1024)
    print('SIZE=' + repr(termios.tcgetwinsize(0)) + ' INPUT=' + repr(data), flush=True)
''')
            client.chmod(0o700)
            with patch('app.core.integrations.autodarts.terminal.AUTODARTS_BIN', client):
                terminal = AutodartsTerminal()
            try:
                output = await asyncio.wait_for(terminal.read(), timeout=3)
                self.assertIn(b"['remote', '-H', '127.0.0.1']", output)
                terminal.resize(40, 120)
                await terminal.write('hello')
                output = await asyncio.wait_for(terminal.read(), timeout=3)
                self.assertIn(b'SIZE=(40, 120)', output)
                self.assertIn(b"INPUT=b'hello'", output)
            finally:
                await terminal.close()
            self.assertIsNotNone(terminal.process.poll())
            await terminal.close()  # Cleanup is safe if called again.

    async def test_ochecore_client_uses_its_daemon_and_saved_config(self):
        with tempfile.TemporaryDirectory() as folder:
            client = Path(folder) / 'oche-core'
            client.write_text('''#!/usr/bin/env python3
import sys, time
print(repr(sys.argv[1:]), flush=True)
time.sleep(30)
''')
            client.chmod(0o700)
            config = Path(folder) / 'board.toml'
            with patch.object(ochecore, 'binary', return_value=client), patch.object(ochecore, 'CONFIG', config):
                terminal = OcheCoreTerminal()
            try:
                output = await asyncio.wait_for(terminal.read(), timeout=3)
                expected = ['--config', str(config), '--host', '127.0.0.1', '--port', '5180', 'remote']
                self.assertIn(repr(expected).encode(), output)
            finally:
                await terminal.close()
            self.assertIsNotNone(terminal.process.poll())
