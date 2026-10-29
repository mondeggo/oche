import asyncio
import os
from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch

from app.services.autodarts_terminal import AutodartsTerminal


@unittest.skipUnless(os.name == 'posix', 'PTYs require Linux/macOS')
class TerminalProcessTests(unittest.IsolatedAsyncioTestCase):
    async def test_interactive_client_resize_and_cleanup(self):
        with tempfile.TemporaryDirectory() as folder:
            client = Path(folder) / 'client'
            client.write_text('''#!/usr/bin/env python3
import os, sys, termios, tty
tty.setraw(0)
print('ARGS=' + repr(sys.argv[1:]), flush=True)
while True:
    data = os.read(0, 1024)
    print('SIZE=' + repr(termios.tcgetwinsize(0)) + ' INPUT=' + repr(data), flush=True)
''')
            client.chmod(0o700)
            with patch('app.services.autodarts_terminal.AUTODARTS_BIN', client):
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
