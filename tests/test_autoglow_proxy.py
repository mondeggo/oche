import socket
import threading
import time
import unittest
from unittest.mock import patch

from fastapi import FastAPI, Request, WebSocket, WebSocketDisconnect
from fastapi.responses import HTMLResponse, JSONResponse, Response
from fastapi.testclient import TestClient
from starlette.websockets import WebSocketDisconnect as ClientDisconnect
import uvicorn

from app.main import app
from app.routers.autoglow_proxy import rewrite_asset


class FakeAutoGlow:
    """Real loopback HTTP/WebSocket upstream; never starts AutoGlow or its hardware."""

    def __init__(self):
        self.requests = []
        self.websockets = []
        self.disconnected = threading.Event()
        upstream = FastAPI()

        @upstream.api_route('/{path:path}', methods=['GET', 'HEAD', 'POST', 'PUT', 'PATCH', 'DELETE', 'OPTIONS'])
        async def http(request: Request, path: str):
            body = await request.body()
            self.requests.append({'path': request.url.path, 'query': request.scope['query_string'],
                                  'method': request.method, 'body': body, 'headers': dict(request.headers)})
            if path == '':
                return HTMLResponse('''<!doctype html><html><head>
                    <link rel="stylesheet" href="/css/theme.css"></head><body>
                    <h1>Lighting controls</h1><output id="api-result"></output>
                    <output id="socket-result"></output><script src="/app.js"></script>
                    </body></html>''')
            if path == 'app.js':
                return Response('''fetch('/api/status?check=1')
                    .then(response => response.json())
                    .then(data => document.querySelector('#api-result').textContent = data.state);
                    const socket = new WebSocket(`${location.protocol === 'https:' ? 'wss' : 'ws'}://${location.host}/ws/events`);
                    socket.onmessage = event => { document.querySelector('#socket-result').textContent = event.data; };
                    ''', media_type='application/javascript')
            if path == 'css/theme.css':
                return Response('body { background: white; } .logo { background-image: url("/css/logo.bin"); }',
                                media_type='text/css')
            if path == 'api/status':
                return JSONResponse({'state': 'ready', 'unchanged_path': '/api/private'})
            if path == 'api/echo':
                return Response(body, status_code=202, media_type=request.headers.get('content-type'),
                                headers={'x-upstream': 'echo', 'cache-control': 'no-store'})
            if path == 'redirect':
                return Response(status_code=307, headers={'location': '/api/status?next=1'})
            if path == 'external-redirect':
                return Response(status_code=302, headers={'location': 'https://example.com/notice'})
            if path == 'binary':
                return Response(b'\x00\xff\x80unmodified /api/status', media_type='application/octet-stream')
            if path == 'api/fail':
                return JSONResponse({'error': 'upstream failure'}, status_code=409)
            return JSONResponse({'path': '/' + path})

        @upstream.websocket('/ws/{path:path}')
        async def websocket(client: WebSocket, path: str):
            self.websockets.append({'path': client.url.path, 'query': client.scope['query_string'],
                                    'headers': dict(client.headers)})
            await client.accept()
            await client.send_text('lighting connected')
            try:
                while True:
                    message = await client.receive()
                    if message['type'] == 'websocket.disconnect':
                        break
                    if message.get('text') == 'close':
                        await client.close(code=1000)
                        break
                    if message.get('text') is not None:
                        await client.send_text(message['text'])
                    elif message.get('bytes') is not None:
                        await client.send_bytes(message['bytes'])
            except WebSocketDisconnect:
                pass
            finally:
                self.disconnected.set()

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
            raise RuntimeError('Fake AutoGlow upstream did not start.')

    def close(self):
        self.server.should_exit = True
        self.thread.join(timeout=5)
        self.listener.close()
        if self.thread.is_alive():
            raise RuntimeError('Fake AutoGlow upstream did not stop.')


class AutoGlowProxyTests(unittest.TestCase):
    def setUp(self):
        self.upstream = FakeAutoGlow()
        self.addCleanup(self.upstream.close)
        port = patch('app.services.autoglow.PORT', self.upstream.port)
        port.start()
        self.addCleanup(port.stop)
        self.client = TestClient(app, base_url='https://oche.test')
        self.addCleanup(self.client.close)

    def test_forwards_http_query_body_status_and_binary_without_touching_json(self):
        response = self.client.post('/autoglow/ui/api/echo?channel=1&channel=2&name=a%2Fb',
            content=b'\x00\xffbutton=on', headers={'content-type': 'application/octet-stream',
                                                 'origin': 'https://oche.test'})
        self.assertEqual(response.status_code, 202)
        self.assertEqual(response.content, b'\x00\xffbutton=on')
        self.assertEqual(response.headers['content-type'], 'application/octet-stream')
        self.assertEqual(response.headers['x-upstream'], 'echo')
        self.assertEqual(response.headers['cache-control'], 'no-store')
        received = self.upstream.requests[-1]
        self.assertEqual(received['method'], 'POST')
        self.assertEqual(received['path'], '/api/echo')
        self.assertEqual(received['query'], b'channel=1&channel=2&name=a%2Fb')
        self.assertEqual(received['body'], response.content)
        self.assertEqual(self.client.get('/autoglow/ui/binary').content, b'\x00\xff\x80unmodified /api/status')
        self.assertEqual(self.client.get('/autoglow/ui/api/status').json()['unchanged_path'], '/api/private')
        failure = self.client.get('/autoglow/ui/api/fail')
        self.assertEqual(failure.status_code, 409)
        self.assertEqual(failure.json(), {'error': 'upstream failure'})

    def test_rebases_ui_assets_api_websockets_and_local_redirects(self):
        page = self.client.get('/autoglow/ui/')
        self.assertEqual(page.status_code, 200)
        self.assertIn('href="/autoglow/ui/css/theme.css"', page.text)
        self.assertIn('src="/autoglow/ui/app.js"', page.text)
        script = self.client.get('/autoglow/ui/app.js')
        self.assertIn("fetch('/autoglow/ui/api/status?check=1')", script.text)
        self.assertIn('${location.host}/autoglow/ui/ws/events', script.text)
        stylesheet = self.client.get('/autoglow/ui/css/theme.css')
        self.assertIn('url("/autoglow/ui/css/logo.bin")', stylesheet.text)
        redirect = self.client.get('/autoglow/ui/redirect', follow_redirects=False)
        self.assertEqual(redirect.status_code, 307)
        self.assertEqual(redirect.headers['location'], '/autoglow/ui/api/status?next=1')
        external = self.client.get('/autoglow/ui/external-redirect', follow_redirects=False)
        self.assertEqual(external.status_code, 302)
        self.assertEqual(external.headers['location'], 'https://example.com/notice')

    def test_keeps_upstream_fixed_rejects_encoded_prefix_and_preserves_asset_syntax(self):
        for path in ('/%61utoglow/ui/api/test', '/autoglow%2Fui/api/test'):
            with self.subTest(path=path):
                self.assertEqual(self.client.get(path).status_code, 400)
                with self.assertRaises(ClientDisconnect) as rejected:
                    with self.client.websocket_connect('wss://oche.test' + path,
                                                       headers={'origin': 'https://oche.test'}):
                        pass
                self.assertEqual(rejected.exception.code, 1008)
        self.assertEqual(self.upstream.requests, [])
        self.assertEqual(self.upstream.websockets, [])
        response = self.client.get('/autoglow/ui//outside.example:9999/api/test')
        self.assertEqual(response.status_code, 200)
        self.assertEqual(self.upstream.requests[-1]['path'], '//outside.example:9999/api/test')
        self.assertEqual(self.upstream.requests[-1]['headers']['host'], f'127.0.0.1:{self.upstream.port}')
        source = br'''const icon = '<svg xmlns="http://www.w3.org/2000/svg"><path d="M0 0" /></svg>';
            const pattern = /\/api\/[^/]+/g;
            const escaped = text.replace(/"/g, '&quot;');
            const external = 'https://example.com/api/devices';
            const api = '/api/devices'; const page = '/settings';'''
        expected = source.replace(b"'/api/devices'", b"'/autoglow/ui/api/devices'")
        expected = expected.replace(b"'/settings'", b"'/autoglow/ui/settings'")
        self.assertEqual(rewrite_asset(source), expected)

    def test_websocket_relays_text_binary_query_and_both_disconnect_directions(self):
        with self.client.websocket_connect('wss://oche.test/autoglow/ui/ws/events?channel=2',
                                           headers={'origin': 'https://oche.test'}) as client:
            self.assertEqual(client.receive_text(), 'lighting connected')
            client.send_text('turn on')
            self.assertEqual(client.receive_text(), 'turn on')
            client.send_bytes(b'\x00\xff')
            self.assertEqual(client.receive_bytes(), b'\x00\xff')
            self.assertEqual(self.upstream.websockets[-1]['path'], '/ws/events')
            self.assertEqual(self.upstream.websockets[-1]['query'], b'channel=2')
        self.assertTrue(self.upstream.disconnected.wait(2))
        self.upstream.disconnected.clear()
        with self.client.websocket_connect('wss://oche.test/autoglow/ui/ws/events',
                                           headers={'origin': 'https://oche.test'}) as client:
            self.assertEqual(client.receive_text(), 'lighting connected')
            client.send_text('close')
            with self.assertRaises(ClientDisconnect) as closed:
                client.receive_text()
            self.assertEqual(closed.exception.code, 1000)
        self.assertTrue(self.upstream.disconnected.wait(2))

    def test_rejects_cross_origin_changes_and_reports_unavailable_upstream(self):
        response = self.client.post('/autoglow/ui/api/echo', content=b'change',
                                    headers={'origin': 'https://another.example'})
        self.assertEqual(response.status_code, 403)
        self.assertEqual(self.upstream.requests, [])
        for origin in ('https://another.example', 'null'):
            with self.subTest(origin=origin), self.assertRaises(ClientDisconnect):
                with self.client.websocket_connect('wss://oche.test/autoglow/ui/ws/events', headers={'origin': origin}):
                    pass
        self.assertEqual(self.upstream.websockets, [])
        with socket.socket() as unused:
            unused.bind(('127.0.0.1', 0))
            with patch('app.services.autoglow.PORT', unused.getsockname()[1]):
                self.assertIn(self.client.get('/autoglow/ui/').status_code, (502, 503))
        self.assertEqual(self.client.get('/autoglow/ui/api/status').status_code, 200)


if __name__ == '__main__':
    unittest.main()
