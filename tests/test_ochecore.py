import json
from pathlib import Path
import socket
import tempfile
import threading
import time
from types import SimpleNamespace
import unittest
from unittest.mock import AsyncMock, Mock, patch

from fastapi import FastAPI, Request, WebSocket
from fastapi.responses import HTMLResponse, JSONResponse, Response
from fastapi.testclient import TestClient
from starlette.websockets import WebSocketDisconnect
import uvicorn

from app.config import load_config, save_config
from app.main import app
from app.services import ochecore


class FakeOcheCore:
    """Loopback fixture with Core's framing and browser-origin restrictions."""

    audio = {'type': 'play', 'clips': [{'url': '/api/caller/audio/test.mp3'},
                                     {'url': 'https://example.com/audio.mp3'}], 'text': 'Score 180'}

    def __init__(self):
        self.requests = []
        self.sockets = []
        self.ui = {'embedded': True, 'theme': 'dark', 'parent_origin': '', 'error': None}
        upstream = FastAPI()

        @upstream.middleware('http')
        async def guard(request, call_next):
            if request.method not in ('GET', 'HEAD', 'OPTIONS'):
                origin = request.headers.get('origin')
                if origin and origin != f'{request.url.scheme}://{request.headers["host"]}':
                    return JSONResponse({'detail': 'Origin not allowed.'}, status_code=403)
            response = await call_next(request)
            ancestor = "'self'" if self.ui['embedded'] else "'none'"
            response.headers['Content-Security-Policy'] = f"default-src 'self'; connect-src 'self'; frame-ancestors {ancestor}; base-uri 'none'"
            return response

        @upstream.api_route('/{path:path}', methods=['GET', 'POST', 'PATCH'])
        async def http(request: Request, path: str):
            self.requests.append({'path': request.url.path, 'method': request.method,
                                  'origin': request.headers.get('origin'), 'body': await request.body()})
            if path == '':
                return HTMLResponse(f'''<!doctype html><html data-theme="{self.ui['theme']}" data-embedded="{str(self.ui['embedded']).lower()}" data-ui-theme="{self.ui['theme']}"><head>
                    <link rel="stylesheet" href="/static/style.css"></head><body>
                    <h1>OcheCore controls</h1><output id="api-result"></output>
                    <output id="socket-result"></output><script src="/static/app.js"></script>
                    </body></html>''')
            if path == 'static/style.css':
                return Response('body { background: white; }', media_type='text/css')
            if path == 'static/app.js':
                return Response('''window.addEventListener('ochecore:ui-settings', event => {
                        document.documentElement.dataset.theme = event.detail.theme;
                        document.documentElement.dataset.embedded = String(event.detail.embedded);
                    });
                    fetch('/api/config', {method: 'PATCH', headers: {'content-type': 'application/json'},
                    body: JSON.stringify({board_id: 'test'})}).then(response => response.json())
                    .then(data => document.querySelector('#api-result').textContent = data.saved ? 'saved' : 'failed');
                    const socket = new WebSocket(`${location.protocol === 'https:' ? 'wss:' : 'ws:'}//${location.host}/caller/audio`);
                    socket.onmessage = event => { document.querySelector('#socket-result').textContent = 'audio connected'; };
                    ''', media_type='application/javascript')
            if path == 'api/config':
                return JSONResponse({'saved': True})
            if path == 'api/ui':
                if request.method == 'PATCH':
                    self.ui.update(await request.json())
                return JSONResponse(self.ui)
            if path == 'api/caller/audio/test.mp3':
                return Response(b'ID3test audio', media_type='audio/mpeg')
            return JSONResponse({'state': 'ready'})

        @upstream.websocket('/{path:path}')
        async def websocket(client: WebSocket, path: str):
            origin = client.headers.get('origin')
            if origin and origin != f'http://{client.headers["host"]}':
                await client.close(code=1008)
                return
            self.sockets.append({'path': path, 'origin': origin})
            await client.accept()
            await client.send_text(json.dumps(self.audio))
            while True:
                message = await client.receive()
                if message['type'] == 'websocket.disconnect':
                    return
                if message.get('bytes') is not None:
                    await client.send_bytes(message['bytes'])
                elif message.get('text') is not None:
                    await client.send_text(message['text'])

        self.listener = socket.socket()
        self.listener.bind(('127.0.0.1', 0))
        self.port = self.listener.getsockname()[1]
        self.server = uvicorn.Server(uvicorn.Config(upstream, log_level='critical', access_log=False))
        self.thread = threading.Thread(target=self.server.run, kwargs={'sockets': [self.listener]}, daemon=True)
        self.thread.start()
        deadline = time.monotonic() + 5
        while not self.server.started and self.thread.is_alive() and time.monotonic() < deadline:
            time.sleep(0.01)
        if not self.server.started:
            self.close()
            raise RuntimeError('Fake OcheCore did not start.')

    def close(self):
        self.server.should_exit = True
        self.thread.join(timeout=5)
        self.listener.close()
        if self.thread.is_alive():
            raise RuntimeError('Fake OcheCore did not stop.')


class OcheCoreTests(unittest.TestCase):
    def setUp(self):
        temporary = tempfile.TemporaryDirectory()
        self.addCleanup(temporary.cleanup)
        self.data = Path(temporary.name)
        self.source = self.data / 'source'
        self.python = self.source / 'private-python'
        self.process = Mock(name='core-process', status='stopped', pid=None)
        self.process.start.return_value = True
        for target, value in (
            ('app.config.CONFIG_FILE', self.data / 'oche_config.json'),
            ('app.services.ochecore.SOURCE', self.source),
            ('app.services.ochecore.PYTHON', self.python),
            ('app.services.ochecore.DATA', self.data / 'core-data'),
            ('app.services.ochecore._process', self.process),
            ('app.routers.ochecore.LOG_DIR', self.data),
        ):
            patcher = patch(target, value)
            patcher.start()
            self.addCleanup(patcher.stop)
        self.client = TestClient(app)
        self.addCleanup(self.client.close)

    def install_fixture(self):
        for file in (self.python, self.source / 'src/ochecore/main.py',
                     self.source / 'src/ochecore/static/index.html'):
            file.parent.mkdir(parents=True, exist_ok=True)
            file.touch()
        (self.source / 'VERSION').write_text('0.1.2\n')

    def test_start_requires_installation_and_preserves_persistent_core_data(self):
        self.assertFalse(ochecore.start())
        self.process.start.assert_not_called()
        self.install_fixture()
        ochecore.DATA.mkdir()
        saved = ochecore.DATA / 'connection.json'
        saved.write_text('{"board_id":"existing-board"}')
        self.assertTrue(ochecore.start())
        self.process.start.assert_called_once()
        self.assertEqual(self.process.env['OCHECORE_DATA_DIR'], str(ochecore.DATA))
        self.assertEqual(self.process.env['OCHECORE_HOST'], '127.0.0.1')
        self.assertEqual(self.process.env['OCHECORE_PORT'], str(ochecore.PORT))
        self.assertEqual(self.process.env['OCHECORE_UI_ENABLED'].lower(), 'true')
        self.assertEqual(self.process.env['OCHECORE_UI_EMBEDDED'].lower(), 'true')
        self.assertEqual(saved.read_text(), '{"board_id":"existing-board"}')
        self.process.status, self.process.pid = 'running', 321
        status = ochecore.get_status()
        self.assertEqual(status['version'], '0.1.2')
        self.assertEqual(status['processes'], {'ochecore': 'running'})
        self.assertEqual(status['pids'], {'ochecore': 321})
        self.assertTrue(status['installed'])

    def test_lifespan_honors_autostart_and_always_stops_core(self):
        for enabled in (True, False):
            config = load_config()
            config.update(autostart_ochecore=enabled, autostart_autodarts=False, autostart_autoglow=False)
            save_config(config)
            https = SimpleNamespace(restore=AsyncMock(), close=AsyncMock())
            with self.subTest(enabled=enabled), patch('app.main.LocalHTTPS', return_value=https), \
                 patch('app.main.autodarts_service.start'), patch('app.main.autoglow_service.start'), \
                 patch('app.main.autodarts_service.stop'), patch('app.main.autoglow_service.stop'), \
                 patch('app.main.ochecore_service.start') as start, \
                 patch('app.main.ochecore_service.stop') as stop:
                with TestClient(app) as client:
                    self.assertEqual(client.get('/healthz').status_code, 200)
                self.assertEqual(start.call_count, int(enabled))
                stop.assert_called_once()

    def test_settings_persist_and_keep_unrelated_values(self):
        initial = self.client.get('/config/data').json()
        self.assertTrue(initial['autostart_ochecore'])
        self.assertNotIn('show_ochecore_in_navbar', initial)
        self.assertFalse(initial['autohide_navbar_on_ochecore'])
        desired = {'autostart_ochecore': False,
                   'autohide_navbar_on_ochecore': True}
        self.assertEqual(self.client.post('/config/data', json=desired).status_code, 200)
        saved = load_config()
        self.assertTrue(all(saved[key] == value for key, value in desired.items()))
        self.assertEqual(saved['autostart_autoglow'], initial['autostart_autoglow'])
        self.assertNotIn('id="nav-link-ochecore"', self.client.get('/config').text)
        self.assertNotIn('cfg-show-ochecore', self.client.get('/config').text)
        self.assertIn('OcheCore', self.client.get('/supervisor?service=ochecore').text)
        saved['show_ochecore_in_navbar'] = False  # Old installations may still store this setting.
        save_config(saved)
        self.assertNotIn('show_ochecore_in_navbar', self.client.get('/config/data').json())
        self.assertIn('id="oc-frame"', self.client.get('/').text)

    def test_home_is_core_and_system_dashboard_is_linked_from_settings(self):
        home = self.client.get('/')
        self.assertEqual(home.status_code, 200)
        self.assertIn('id="oc-frame"', home.text)
        self.assertNotIn('id="m-cpu"', home.text)
        legacy = self.client.get('/ochecore', follow_redirects=False)
        self.assertEqual(legacy.status_code, 308)
        self.assertEqual(legacy.headers['location'], '/')
        system = self.client.get('/config/system')
        self.assertEqual(system.status_code, 200)
        self.assertIn('id="m-cpu"', system.text)
        self.assertIn('href="/config/system"', self.client.get('/config').text)

    def test_control_routes_logs_and_origin_checks(self):
        self.assertEqual(self.client.post('/ochecore/start').status_code, 503)
        self.process.start.assert_not_called()
        self.install_fixture()
        for action in ('start', 'stop', 'restart'):
            with self.subTest(action=action), patch.object(ochecore, action) as control:
                self.assertEqual(self.client.post('/ochecore/' + action).status_code, 200)
                control.assert_called_once()
        with patch.object(ochecore, 'start', side_effect=RuntimeError('Could not launch core')):
            self.assertEqual(self.client.post('/ochecore/start').status_code, 500)
        self.process.tail_log.return_value = 'core log\n'
        self.assertEqual(self.client.get('/ochecore/logs').json(), {'ochecore': 'core log\n'})
        (self.data / 'ochecore.log').write_bytes(b'full core log\n')
        export = self.client.get('/ochecore/logs/export')
        self.assertEqual(export.text, 'full core log\n')
        self.assertIn('attachment;', export.headers['content-disposition'])
        self.assertEqual(self.client.post('/ochecore/logs/clear', headers={'origin': 'https://outside.example'}).status_code, 403)
        self.process.clear_log.assert_not_called()
        self.assertEqual(self.client.post('/ochecore/logs/clear').status_code, 200)
        self.process.clear_log.assert_called_once()


class OcheCoreProxyTests(unittest.TestCase):
    def setUp(self):
        self.upstream = FakeOcheCore()
        self.addCleanup(self.upstream.close)
        port = patch('app.services.ochecore.PORT', self.upstream.port)
        port.start()
        self.addCleanup(port.stop)
        self.client = TestClient(app, base_url='https://oche.test')
        self.addCleanup(self.client.close)

    def test_https_embedding_assets_and_origin_checked_config_writes(self):
        page = self.client.get('/ochecore/ui/')
        self.assertEqual(page.status_code, 200)
        self.assertIn('src="/ochecore/ui/static/app.js"', page.text)
        self.assertIn("frame-ancestors 'self'", page.headers['content-security-policy'])
        self.assertIn("default-src 'self'; connect-src 'self'", page.headers['content-security-policy'])
        script = self.client.get('/ochecore/ui/static/app.js')
        self.assertIn("fetch('/ochecore/ui/api/config'", script.text)
        self.assertIn('${location.host}/ochecore/ui/caller/audio', script.text)
        response = self.client.patch('/ochecore/ui/api/config', json={'board_id': 'test'},
                                     headers={'origin': 'https://oche.test',
                                              'x-forwarded-proto': 'https',
                                              'x-forwarded-host': 'outside.example'})
        self.assertEqual(response.status_code, 200)
        self.assertEqual(self.upstream.requests[-1]['origin'], f'http://127.0.0.1:{self.upstream.port}')
        self.assertEqual(json.loads(self.upstream.requests[-1]['body']), {'board_id': 'test'})
        self.assertEqual(self.client.patch('/ochecore/ui/api/config', json={},
            headers={'origin': 'https://outside.example'}).status_code, 403)
        self.assertEqual(len([r for r in self.upstream.requests if r['method'] == 'PATCH']), 1)

    def test_caller_audio_urls_are_rebased_while_event_payloads_stay_unchanged(self):
        for path in ('caller/audio', 'events', 'events/raw'):
            with self.subTest(path=path), self.client.websocket_connect('wss://oche.test/ochecore/ui/' + path,
                headers={'origin': 'https://oche.test'}) as client:
                received = client.receive_json()
                expected = '/ochecore/ui/api/caller/audio/test.mp3' if path == 'caller/audio' else '/api/caller/audio/test.mp3'
                self.assertEqual(received['clips'][0]['url'], expected)
                self.assertEqual(received['clips'][1]['url'], 'https://example.com/audio.mp3')
                self.assertEqual(received['text'], 'Score 180')
                self.assertEqual(self.upstream.sockets[-1]['origin'], f'http://127.0.0.1:{self.upstream.port}')
                client.send_bytes(b'\x00\xff')
                self.assertEqual(client.receive_bytes(), b'\x00\xff')
        audio = self.client.get('/ochecore/ui/api/caller/audio/test.mp3')
        self.assertEqual(audio.content, b'ID3test audio')
        self.assertEqual(audio.headers['content-type'], 'audio/mpeg')
        with self.assertRaises(WebSocketDisconnect):
            with self.client.websocket_connect('wss://oche.test/ochecore/ui/events',
                                               headers={'origin': 'https://outside.example'}):
                pass


if __name__ == '__main__':
    unittest.main()
