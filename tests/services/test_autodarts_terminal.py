import asyncio
import json
import unittest
from threading import BoundedSemaphore
from types import SimpleNamespace
from unittest.mock import patch

from fastapi.testclient import TestClient
from starlette.websockets import WebSocketDisconnect

from app.main import app


class FakeTerminal:
    def __init__(self):
        self.output = asyncio.Queue()
        self.output.put_nowait(b"setup ready")
        self.size = None
        self.closed = False

    async def read(self):
        return await self.output.get()

    async def write(self, data):
        self.output.put_nowait(data.encode())

    def resize(self, rows, cols):
        self.size = (rows, cols)

    async def close(self):
        self.closed = True


class TerminalSocketTests(unittest.TestCase):
    service = "autodarts"
    label = "Autodarts"
    factory = "AutodartsTerminal"
    process = "autodarts"

    def route(self, suffix):
        return f"app.core.integrations.{self.service}.routes.{suffix}"

    def setUp(self):
        self.client = TestClient(app)
        self.addCleanup(self.client.close)
        self.terminal = FakeTerminal()
        for patcher in (
            patch('app.services.setup_terminal.os', SimpleNamespace(name='posix')),
            patch(self.route(f'{self.process}.get_status'), return_value={'status': 'running'}),
            patch(self.route(self.factory), return_value=self.terminal),
            patch('app.services.setup_terminal.terminal_slots', BoundedSemaphore(4)),
        ):
            patcher.start()
            self.addCleanup(patcher.stop)

    def connect(self, origin='http://testserver'):
        return self.client.websocket_connect(f'/{self.service}/terminal/ws', headers={'origin': origin})

    def test_input_output_resize_and_disconnect_cleanup(self):
        with self.connect() as socket:
            self.assertEqual(socket.receive_bytes(), b'setup ready')
            socket.send_json({'type': 'resize', 'rows': 40, 'cols': 120})
            socket.send_json({'type': 'input', 'data': '\x1b[B'})
            self.assertEqual(socket.receive_bytes(), b'\x1b[B')
            self.assertEqual(self.terminal.size, (40, 120))
        self.assertTrue(self.terminal.closed)

    def test_rejects_other_origins(self):
        for origin in ('https://other.example', 'http://[', 'null'):
            with self.subTest(origin=origin), self.assertRaises(WebSocketDisconnect):
                with self.connect(origin):
                    pass

    def test_session_limit_does_not_spawn_another_process(self):
        with patch('app.services.setup_terminal.terminal_slots', BoundedSemaphore(0)), \
             patch(self.route(self.factory)) as terminal:
            with self.connect() as socket:
                self.assertIn('sessions are in use', socket.receive_json()['error'])
            terminal.assert_not_called()

    def test_binary_frame_closes_cleanly(self):
        with self.connect() as socket:
            self.assertEqual(socket.receive_bytes(), b'setup ready')
            socket.send_bytes(b'not the input protocol')
            self.assertIn('error', socket.receive_json())
        self.assertTrue(self.terminal.closed)

    def test_failed_start_releases_session_slot(self):
        slots = BoundedSemaphore(1)
        with patch('app.services.setup_terminal.terminal_slots', slots), \
             patch(self.route(self.factory), side_effect=OSError):
            with self.connect() as socket:
                self.assertIn('error', socket.receive_json())
        self.assertTrue(slots.acquire(blocking=False))

    def test_stopped_daemon_does_not_start_a_terminal(self):
        with patch(self.route(f'{self.process}.get_status'), return_value={'status': 'stopped'}), \
             patch(self.route(self.factory)) as terminal:
            with self.connect() as socket:
                self.assertIn(f'Start {self.label}', socket.receive_json()['error'])
            terminal.assert_not_called()

    def test_invalid_input_closes_client(self):
        for message in ('not json', json.dumps({'type': 'resize', 'rows': -1, 'cols': 80})):
            self.terminal = FakeTerminal()
            with self.subTest(message=message), patch(self.route(self.factory), return_value=self.terminal):
                with self.connect() as socket:
                    # Consume queued startup output before checking the error.
                    socket.send_text(message)
                    while True:
                        response = socket.receive()
                        if response.get('text'):
                            self.assertIn('error', json.loads(response['text']))
                            break
                self.assertTrue(self.terminal.closed)


class OcheCoreTerminalSocketTests(TerminalSocketTests):
    service = "ochecore"
    label = "OcheCore"
    factory = "OcheCoreTerminal"
    process = "process"
