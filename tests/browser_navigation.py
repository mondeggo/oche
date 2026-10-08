"""Browser regression checks: python tests/browser_navigation.py.

Requires Playwright and a locally installed Microsoft Edge. Requests are served
by TestClient with simulated service status; no Docker or cameras are required.
"""
import json
import sys
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch
from urllib.parse import urlsplit

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from fastapi.testclient import TestClient
from playwright.sync_api import sync_playwright
from app.main import app


class NavigationTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        config = patch('app.config.CONFIG_FILE', Path(self.temp.name) / 'config.json')
        config.start()
        self.addCleanup(config.stop)
        self.client = TestClient(app)
        self.addCleanup(self.client.close)
        self.playwright = sync_playwright().start()
        self.addCleanup(self.playwright.stop)
        self.browser = self.playwright.chromium.launch(channel='msedge', headless=True)
        self.addCleanup(self.browser.close)
        self.page = self.browser.new_context().new_page()
        self.errors = []
        self.page.on('pageerror', lambda error: self.errors.append(str(error)))
        self.pid = 100
        self.offline = False
        self.board_loads = 0
        self.page.route('**/*', self.route)
        self.terminal_messages = []
        self.terminal_connections = []
        self.page.route_web_socket('**/autodarts/terminal/ws', self.terminal_socket)

    def terminal_socket(self, socket):
        self.terminal_connections.append(socket)
        socket.send(b'Autodarts setup\r\n')
        socket.on_message(lambda message: self.terminal_messages.append(json.loads(message)))

    def route(self, route):
        url = urlsplit(route.request.url)
        if url.port == 3180:
            if route.request.is_navigation_request():
                self.board_loads += 1
            route.fulfill(content_type='text/html', body='<h1>Camera stream</h1>')
        elif url.path == '/autodarts/status':
            if self.offline:
                route.fulfill(status=503, body='Unavailable')
            else:
                route.fulfill(json={'status': 'running', 'pid': self.pid})
        elif url.path == '/ochecore/ui/api/ui':
            route.fulfill(status=503, json={'detail': 'Core UI settings unavailable in this fixture.'})
        elif url.hostname == 'oche.test':
            self.route_app(route)
        else:
            route.fulfill(content_type='text/html', body='<h1>External page</h1>')

    def route_app(self, route):
        response = self.client.request(route.request.method, route.request.url,
            content=route.request.post_data,
            headers={key: value for key, value in route.request.headers.items()
                     if key in ('content-type', 'origin', 'sec-fetch-site')})
        route.fulfill(status=response.status_code, body=response.content, headers=dict(response.headers))

    def view(self):
        element = self.page.locator('.oche-page-frame:not([hidden])')
        if element.count():
            return element.element_handle().content_frame()
        return self.page.main_frame

    def go(self, path):
        toggle = self.view().locator('#nav-toggle')
        if toggle.is_visible() and toggle.get_attribute('aria-expanded') == 'false':
            toggle.click()
        self.view().locator(f'#topbar a[href="{path}"]').first.click()
        origin = urlsplit(self.page.url)
        self.page.wait_for_url(origin.scheme + '://' + origin.netloc + path)
        self.view().locator('#topbar').wait_for()

    def test_updates_tab_has_its_own_retained_view(self):
        self.page.goto('http://oche.test/supervisor')
        self.view().locator('a[href="/supervisor?service=updates"]').click()
        self.page.wait_for_url('http://oche.test/supervisor?service=updates')
        self.view().get_by_role('heading', name='Software updates').wait_for()
        self.view().locator('#update-modules article').first.wait_for()
        self.assertEqual(self.view().locator('#update-modules article').count(), 4)
        self.assertTrue(self.view().locator('#check-updates').is_enabled())
        self.assertTrue(self.view().locator('#update-installation-notice').is_visible())
        with patch('app.routers.updates.manager.submit') as submit:
            with self.page.expect_response('**/updates/check') as response:
                self.view().locator('#check-updates').click()
            self.assertEqual(response.value.status, 202)
            submit.assert_called_once_with('check', None)
        self.view().evaluate('window.updatesMarker = "retained"')
        self.view().locator('a[href="/supervisor?service=autodarts"]').click()
        self.page.wait_for_url('http://oche.test/supervisor?service=autodarts')
        self.view().locator('#toggle-board-btn').wait_for()
        self.assertEqual(self.view().locator('#update-modules').count(), 0)
        self.view().locator('a[href="/supervisor?service=updates"]').click()
        self.page.wait_for_url('http://oche.test/supervisor?service=updates')
        self.view().get_by_role('heading', name='Software updates').wait_for()
        self.assertEqual(self.view().evaluate('window.updatesMarker'), 'retained')
        self.assertEqual(self.errors, [])

    def test_autoglow_embeds_same_origin_with_https_assets_api_and_websocket(self):
        from test_autoglow_proxy import FakeAutoGlow

        upstream = FakeAutoGlow()
        self.addCleanup(upstream.close)
        status = {'installed': True, 'status': 'running', 'web_port': upstream.port,
                  'autodarts_connected': False, 'processes': {'autoglow-web': 'running'},
                  'pids': {'autoglow-web': 123}}
        requests, sockets = [], []
        self.page.on('request', lambda request: requests.append(request.url))

        def socket_connected(socket):
            sockets.append(socket.url)
            socket.send('lighting connected')

        self.page.route_web_socket('**/autoglow/ui/ws/**', socket_connected)
        with patch('app.services.autoglow.PORT', upstream.port), \
             patch('app.services.autoglow.get_status', return_value=status):
            for path, frame_id in (('/autoglow', '#ag-frame'),
                                   ('/supervisor?service=autoglow', '#board-frame')):
                with self.subTest(path=path):
                    self.page.goto('https://oche.test' + path)
                    frame = self.page.locator(frame_id)
                    frame.wait_for(state='visible')
                    self.assertEqual(frame.get_attribute('src'), '/autoglow/ui/')
                    embedded = frame.element_handle().content_frame()
                    embedded.get_by_text('Lighting controls', exact=True).wait_for()
                    embedded.locator('#api-result').filter(has_text='ready').wait_for()
                    embedded.locator('#socket-result').filter(has_text='lighting connected').wait_for()
                    self.assertEqual(embedded.url, 'https://oche.test/autoglow/ui/')
            self.assertEqual(sockets, ['wss://oche.test/autoglow/ui/ws/events'] * 2)
            self.assertTrue(all(url.startswith('https://oche.test/') for url in requests), requests)
            self.assertIn('https://oche.test/autoglow/ui/app.js', requests)
            self.assertIn('https://oche.test/autoglow/ui/api/status?check=1', requests)
            self.page.route('**/autoglow/ui/', lambda route: route.fulfill(status=502, body='Unavailable'))
            self.page.goto('https://oche.test/autoglow')
            self.page.get_by_text('Waiting for AutoGlow 2. Check its service logs if this persists.', exact=True).wait_for()
            self.assertTrue(self.page.locator('#ag-frame').is_hidden())
        self.assertEqual(self.errors, [])

    def test_http_panel_explains_https_requirement_without_blocked_iframe(self):
        panels = self.client.put('/panels/data', json={'panels': [
            {'name': 'Local tool', 'url': 'http://device.test/control', 'pinned': True}
        ]}).json()['panels']
        path = '/panels/' + panels[0]['id']
        self.page.goto('https://oche.test' + path)
        self.page.locator('#panel-https-required').wait_for()
        self.assertEqual(self.page.locator('iframe.panel-frame').count(), 0)
        self.assertEqual(self.page.get_by_role('link', name='Edit panel settings').get_attribute('href'), '/config#panels')
        self.assertEqual(self.page.get_by_role('link', name='Open in a new tab').get_attribute('href'),
                         'http://device.test/control')
        self.page.goto('http://oche.test' + path)
        self.page.locator('iframe.panel-frame').wait_for()
        self.assertEqual(self.page.locator('#panel-https-required').count(), 0)
        self.assertEqual(self.errors, [])

    def test_core_home_theme_and_caller_frame_survive_navigation(self):
        from test_ochecore import FakeOcheCore

        upstream = FakeOcheCore()
        upstream.ui['theme'] = 'light'
        self.addCleanup(upstream.close)
        status = {'installed': True, 'status': 'running', 'web_port': upstream.port,
                  'version': '0.1.2', 'pid': 123, 'processes': {'ochecore': 'running'},
                  'pids': {'ochecore': 123}}
        requests, sockets = [], []
        self.page.on('request', lambda request: requests.append(request.url))

        def audio_connected(socket):
            sockets.append(socket.url)
            socket.send(json.dumps(FakeOcheCore.audio))

        self.page.route_web_socket('**/ochecore/ui/caller/audio', audio_connected)
        self.page.route('**/ochecore/ui/api/ui', self.route_app)
        self.page.emulate_media(color_scheme='dark')
        with patch('app.services.ochecore.PORT', upstream.port), \
             patch('app.services.ochecore.get_status', return_value=status):
            self.page.goto('https://oche.test/')
            frame = self.view().locator('#oc-frame')
            frame.wait_for(state='visible')
            self.assertEqual(frame.get_attribute('src'), '/ochecore/ui/')
            self.assertIn('autoplay', frame.get_attribute('allow'))
            embedded = frame.element_handle().content_frame()
            embedded.locator('#api-result').filter(has_text='saved').wait_for()
            embedded.locator('#socket-result').filter(has_text='audio connected').wait_for()
            self.view().wait_for_function('document.documentElement.dataset.theme === "light"')
            embedded.wait_for_function('document.documentElement.dataset.theme === "light"')
            self.assertFalse(any(request['method'] == 'PATCH' and request['path'] == '/api/ui'
                                 for request in upstream.requests))
            for theme in ('dark', 'light'):
                with self.page.expect_response(lambda response: response.url.endswith('/ochecore/ui/api/ui')
                                                and response.request.method == 'PATCH'):
                    self.view().locator('#theme-toggle').click()
                embedded.wait_for_function('theme => document.documentElement.dataset.theme === theme', arg=theme)
                self.assertEqual(upstream.ui['theme'], theme)
                self.assertEqual(embedded.locator('html').get_attribute('data-embedded'), 'true')
            embedded.evaluate('window.callerMarker = "retained"')
            self.go('/play')
            with self.page.expect_response(lambda response: response.url.endswith('/ochecore/ui/api/ui')
                                            and response.request.method == 'PATCH'):
                self.view().locator('#theme-toggle').click()
            self.go('/')
            embedded.wait_for_function('document.documentElement.dataset.theme === "dark"')
            self.assertEqual(self.view().locator('#oc-frame').element_handle().content_frame()
                             .evaluate('window.callerMarker'), 'retained')
            self.assertEqual(len(sockets), 1)
            # A change from Core's UI or another device reaches the retained Home.
            upstream.ui['theme'] = 'light'
            self.view().wait_for_function('document.documentElement.dataset.theme === "light"', timeout=8000)
            embedded.wait_for_function('document.documentElement.dataset.theme === "light"')

            self.go('/supervisor')
            self.view().locator('a[href="/supervisor?service=ochecore"]').click()
            self.page.wait_for_url('https://oche.test/supervisor?service=ochecore')
            board = self.view().locator('#board-frame')
            board.wait_for(state='visible')
            self.assertEqual(board.get_attribute('src'), '/ochecore/ui/')
            self.assertIn('autoplay', board.get_attribute('allow'))
            board.content_frame.locator('#api-result').filter(has_text='saved').wait_for()
            self.assertFalse(any(url.startswith('http:') for url in requests), requests)
            self.assertTrue(all(urlsplit(url).port != upstream.port for url in requests), requests)
            self.assertIn('https://oche.test/ochecore/ui/static/app.js', requests)

            self.go('/config')
            self.assertEqual(self.view().locator('#cfg-show-ochecore').count(), 0)
            self.assertEqual(self.view().locator('#nav-link-ochecore').count(), 0)
            with self.page.expect_response('**/config/data'):
                self.view().locator('#cfg-autostart-ochecore').uncheck()
            self.assertFalse(self.client.get('/config/data').json()['autostart_ochecore'])
            self.assertEqual(self.view().locator('a[href="/config/system"]').count(), 0)
            self.go('/supervisor')
            self.view().locator('a[href="/supervisor?service=system"]').click()
            self.page.wait_for_url('https://oche.test/supervisor?service=system')
            self.view().locator('#m-cpu').wait_for()
            self.go('/')
            self.assertEqual(self.view().locator('#oc-frame').element_handle().content_frame()
                             .evaluate('window.callerMarker'), 'retained')
        self.assertEqual(self.errors, [])

    def test_theme_follows_system_and_uses_requested_palette(self):
        palettes = {
            'dark': ['rgb(28, 28, 33)', 'rgb(41, 41, 48)', 'rgb(228, 228, 233)',
                     'rgb(165, 165, 177)', 'rgb(68, 68, 79)', 'rgb(119, 119, 131)',
                     'rgb(25, 135, 84)', 'rgb(21, 115, 71)', 'rgb(219, 196, 165)'],
            'light': ['rgb(245, 244, 241)', 'rgb(255, 255, 255)', 'rgb(48, 48, 56)',
                      'rgb(102, 102, 113)', 'rgb(221, 219, 215)', 'rgb(139, 137, 145)',
                      'rgb(25, 135, 84)', 'rgb(21, 115, 71)', 'rgb(101, 78, 56)'],
        }
        for theme, expected in palettes.items():
            with self.subTest(theme=theme):
                self.page.emulate_media(color_scheme=theme)
                self.page.goto('http://oche.test/config/https')
                self.page.locator('#https-local:not([disabled])').wait_for()
                self.assertEqual(self.page.locator('html').get_attribute('data-theme'), theme)
                self.assertIsNone(self.page.evaluate('localStorage.getItem("oche.theme")'))
                actual = self.page.evaluate('''() => {
                    const css = selector => getComputedStyle(document.querySelector(selector));
                    return [css('body').backgroundColor, css('.card').backgroundColor,
                        css('body').color, css('.hint').color, css('.card').borderTopColor,
                        css('#https-domain').borderTopColor, css('#https-local').backgroundColor,
                        css('.brand').color];
                }''')
                self.assertEqual(actual, expected[:7] + expected[8:])
                self.page.locator('#https-local').hover()
                self.page.wait_for_function('color => getComputedStyle(document.querySelector("#https-local")).backgroundColor === color',
                                            arg=expected[7])
                opposite = 'light' if theme == 'dark' else 'dark'
                self.assertEqual(self.page.locator('#theme-toggle').get_attribute('aria-label'),
                                 'Switch to ' + opposite + ' theme')
                self.page.mouse.move(0, 0)
        self.assertEqual(self.errors, [])

    def test_latest_theme_choice_across_tabs_wins_when_core_recovers(self):
        online = False
        settings = {'embedded': True, 'theme': 'dark', 'parent_origin': '', 'error': None}
        saved_themes = []

        def ui_settings(route):
            if not online:
                route.fulfill(status=503, json={'detail': 'Core is stopped.'})
                return
            if route.request.method == 'PATCH':
                settings.update(route.request.post_data_json)
                saved_themes.append(settings['theme'])
            route.fulfill(json=settings)

        self.page.route('**/ochecore/ui/api/ui', ui_settings)
        self.page.emulate_media(color_scheme='dark')
        self.page.goto('http://oche.test/config')
        with self.page.expect_response('**/ochecore/ui/api/ui'):
            self.page.locator('#theme-toggle').click()  # Older choice: light, queued offline.
        other_tab = self.page.context.new_page()
        self.addCleanup(other_tab.close)
        other_tab.on('pageerror', lambda error: self.errors.append(str(error)))
        other_tab.route('**/*', self.route)
        other_tab.route('**/ochecore/ui/api/ui', ui_settings)
        other_tab.goto('http://oche.test/config')
        self.assertEqual(other_tab.locator('html').get_attribute('data-theme'), 'light')
        with other_tab.expect_response('**/ochecore/ui/api/ui'):
            other_tab.locator('#theme-toggle').click()  # Latest choice: dark.
        self.page.wait_for_function('document.documentElement.dataset.theme === "dark"')
        online = True
        with other_tab.expect_response('**/ochecore/ui/api/ui'):
            other_tab.evaluate('window.dispatchEvent(new Event("focus"))')
        with self.page.expect_response('**/ochecore/ui/api/ui'):
            self.page.evaluate('window.dispatchEvent(new Event("focus"))')
        self.assertTrue(saved_themes)
        self.assertEqual(set(saved_themes), {'dark'})
        self.assertEqual(settings['theme'], 'dark')
        self.assertEqual(self.page.locator('html').get_attribute('data-theme'), 'dark')
        self.assertEqual(other_tab.locator('html').get_attribute('data-theme'), 'dark')
        self.assertEqual(self.errors, [])

    def test_theme_ignores_an_old_save_response_after_a_newer_click(self):
        settings = {'embedded': True, 'theme': 'dark', 'parent_origin': '', 'error': None}
        pending = []

        def ui_settings(route):
            if route.request.method == 'PATCH':
                pending.append(route)
            else:
                route.fulfill(json=settings)

        self.page.route('**/ochecore/ui/api/ui', ui_settings)
        self.page.goto('http://oche.test/config')
        self.page.wait_for_function('document.documentElement.dataset.theme === "dark"')
        with self.page.expect_request(lambda request: request.url.endswith('/ochecore/ui/api/ui')
                                       and request.method == 'PATCH'):
            self.page.locator('#theme-toggle').click()
        self.page.locator('#theme-toggle').click()
        self.assertEqual(self.page.locator('html').get_attribute('data-theme'), 'dark')
        self.assertEqual(len(pending), 1)
        with self.page.expect_request(lambda request: request.url.endswith('/ochecore/ui/api/ui')
                                       and request.method == 'PATCH' and request.post_data_json['theme'] == 'dark'):
            pending[0].fulfill(json={**settings, 'theme': 'light'})
        # The old light response must not repaint before the newer dark save returns.
        self.assertEqual(self.page.locator('html').get_attribute('data-theme'), 'dark')
        self.assertEqual(len(pending), 2)
        pending[1].fulfill(json=settings)
        self.assertEqual(self.page.evaluate('localStorage.getItem("oche.theme")'), 'dark')
        self.assertEqual(self.errors, [])

    def test_stale_theme_cache_does_not_override_owner_choice_in_retained_child(self):
        self.page.emulate_media(color_scheme='dark')
        self.page.goto('http://oche.test/config')
        with self.page.expect_response('**/ochecore/ui/api/ui'):
            self.page.locator('#theme-toggle').click()  # Core is offline; light remains queued.
        self.go('/play')
        child = self.view()
        self.assertNotEqual(child, self.page.main_frame)
        for frame in (self.page.main_frame, child):
            frame.evaluate('''() => {
                window.staleThemeCacheSeen = false;
                window.addEventListener('storage', event => {
                    if (event.key === 'oche.theme' && event.newValue === 'dark')
                        window.staleThemeCacheSeen = true;
                });
            }''')
        peer = self.page.context.new_page()
        self.addCleanup(peer.close)
        peer.on('pageerror', lambda error: self.errors.append(str(error)))
        peer.route('**/*', self.route)
        peer.goto('http://oche.test/config')
        peer.evaluate('localStorage.setItem("oche.theme", "dark")')
        for frame in (self.page.main_frame, child):
            frame.wait_for_function('window.staleThemeCacheSeen')
            self.assertEqual(frame.locator('html').get_attribute('data-theme'), 'light')
        self.assertEqual(self.errors, [])

    def test_theme_persists_and_syncs_retained_pages_terminal_and_other_tabs(self):
        def wait_theme(frame, theme):
            frame.wait_for_function('theme => document.documentElement.dataset.theme === theme', arg=theme)

        self.page.emulate_media(color_scheme='dark')
        self.page.goto('http://oche.test/supervisor')
        terminal = self.view().locator('#board-frame').element_handle().content_frame()
        terminal.locator('.xterm-screen').wait_for()
        self.view().evaluate('window.themePageMarker = "retained"')
        terminal.evaluate('window.themeTerminalMarker = "retained"')
        self.go('/config')
        self.view().locator('#theme-toggle').click()
        wait_theme(self.view(), 'light')
        self.assertEqual(self.page.evaluate('localStorage.getItem("oche.theme")'), 'light')
        self.go('/supervisor')
        wait_theme(self.view(), 'light')
        wait_theme(terminal, 'light')
        terminal.wait_for_function('getComputedStyle(document.querySelector(".xterm-viewport")).backgroundColor === "rgb(245, 244, 241)"')
        self.assertEqual(self.view().evaluate('window.themePageMarker'), 'retained')
        self.assertEqual(terminal.evaluate('window.themeTerminalMarker'), 'retained')

        other_tab = self.page.context.new_page()
        self.addCleanup(other_tab.close)
        other_tab.on('pageerror', lambda error: self.errors.append(str(error)))
        other_tab.route('**/*', self.route)
        other_tab.goto('http://oche.test/config')
        wait_theme(other_tab.main_frame, 'light')
        other_tab.locator('#theme-toggle').click()
        wait_theme(self.view(), 'dark')
        wait_theme(terminal, 'dark')
        terminal.wait_for_function('getComputedStyle(document.querySelector(".xterm-viewport")).backgroundColor === "rgb(28, 28, 33)"')
        self.assertEqual(len(self.terminal_connections), 1)
        self.view().locator('#theme-toggle').click()
        wait_theme(other_tab.main_frame, 'light')
        self.page.reload()
        wait_theme(self.view(), 'light')
        self.assertEqual(self.page.evaluate('localStorage.getItem("oche.theme")'), 'light')
        self.assertEqual(self.errors, [])

    def test_terminal_navigation_preserves_session_and_keyboard(self):
        self.page.goto('http://oche.test/supervisor')
        frame = self.view().frame_locator('#board-frame')
        frame.locator('.xterm-screen').wait_for()
        frame.locator('.xterm-helper-textarea').press('ArrowDown')
        self.page.wait_for_timeout(100)
        self.assertTrue(any(m.get('type') == 'input' and m.get('data') == '\x1b[B' for m in self.terminal_messages))
        self.assertTrue(any(m.get('type') == 'resize' for m in self.terminal_messages))
        self.view().evaluate('window.pageMarker = "original"')
        self.view().locator('#board-frame').element_handle().content_frame().evaluate('window.terminalMarker = "retained"')
        self.view().locator('a[href="/supervisor?service=system"]').click()
        self.page.wait_for_url('http://oche.test/supervisor?service=system')
        self.view().locator('#m-cpu').wait_for()
        self.assertIn('active', self.view().locator('#nav-link-autodarts').get_attribute('class').split())
        self.assertEqual(self.view().locator('.supervisor-selector a[href="/supervisor?service=system"]').get_attribute('aria-current'), 'page')
        self.view().evaluate('window.systemMarker = "retained-system"')
        self.view().locator('.supervisor-selector a[href="/supervisor?service=autodarts"]').click()
        self.page.wait_for_url('http://oche.test/supervisor?service=autodarts')
        self.assertEqual(self.view().evaluate('window.pageMarker'), 'original')
        # Legacy links must select the same cached System view, without replacing Autodarts.
        self.page.evaluate('window.ocheNavigation.navigate("/config/system")')
        self.page.wait_for_url('http://oche.test/supervisor?service=system')
        self.assertEqual(self.view().evaluate('window.systemMarker'), 'retained-system')
        self.view().locator('.supervisor-selector a[href="/supervisor?service=autodarts"]').click()
        self.page.wait_for_url('http://oche.test/supervisor?service=autodarts')
        self.go('/config')
        self.go('/play')
        self.go('/supervisor')
        self.assertEqual(self.view().evaluate('window.pageMarker'), 'original')
        self.page.go_back()
        self.page.wait_for_url('http://oche.test/play')
        self.page.go_forward()
        self.page.wait_for_url('http://oche.test/supervisor')
        self.assertEqual(self.view().evaluate('window.pageMarker'), 'original')
        self.view().locator('#toggle-logs-btn').click()
        self.view().locator('#board-frame').wait_for(state='hidden')
        self.view().locator('#toggle-play-btn').click()
        self.view().locator('#play-frame').wait_for(state='visible')
        self.view().locator('#toggle-board-btn').click()
        self.view().locator('#board-frame').wait_for(state='visible')
        self.assertEqual(self.view().locator('#board-frame').element_handle().content_frame().evaluate('window.terminalMarker'), 'retained')
        self.assertEqual(self.board_loads, 0)
        self.assertEqual(len(self.terminal_connections), 1)
        self.assertEqual(self.errors, [])

    def test_responsive_layouts_do_not_overflow(self):
        panels = self.client.put('/panels/data', json={'panels': [
            {'name': 'A long pinned panel name for a small screen',
             'url': 'https://example.com/dashboard', 'pinned': True}
        ]}).json()['panels']
        paths = ('/', '/config', '/config/https', '/supervisor?service=system', '/supervisor', '/supervisor?service=autoglow',
                 '/supervisor?service=ochecore', '/play', '/autoglow', '/panels/' + panels[0]['id'])
        for width, height in ((320, 740), (390, 844), (768, 1024), (1024, 768), (1440, 900)):
            self.page.set_viewport_size({'width': width, 'height': height})
            for path in paths:
                with self.subTest(width=width, path=path):
                    self.page.goto('http://oche.test' + path)
                    self.page.locator('.topbar.nav-ready').wait_for()
                    self.assertLessEqual(self.page.evaluate('document.documentElement.scrollWidth'), width)
                    self.assertTrue(self.page.locator('#theme-toggle').is_visible())
                    theme_toggle = self.page.locator('#theme-toggle').bounding_box()
                    self.assertGreaterEqual(theme_toggle['x'], 0)
                    self.assertLessEqual(theme_toggle['x'] + theme_toggle['width'], width + 1)
                    if width <= 900:
                        self.page.locator('#nav-toggle').click()
                        self.page.locator('.panels-menu summary').click()
                        dropdown = self.page.locator('.panels-dropdown').bounding_box()
                        self.assertGreaterEqual(dropdown['x'], 0)
                        self.assertLessEqual(dropdown['x'] + dropdown['width'], width + 1)
                        self.assertLessEqual(self.page.evaluate('document.documentElement.scrollWidth'), width)
                        self.page.keyboard.press('Escape')
                    if path in ('/', '/play', '/autoglow') or path.startswith('/panels/'):
                        box = self.page.locator('main').bounding_box()
                        self.assertAlmostEqual(box['y'] + box['height'], height, delta=1)
        self.assertEqual(self.errors, [])

    def test_mobile_menu_survives_cached_navigation_and_rotation(self):
        self.page.set_viewport_size({'width': 390, 'height': 844})
        self.page.goto('http://oche.test/supervisor')
        self.page.locator('#board-frame').content_frame.locator('.xterm-screen').wait_for()
        self.page.evaluate('window.originalMenuToggle = document.querySelector("#nav-toggle")')
        self.go('/config')
        self.go('/supervisor')
        # Wait for the retained page's navbar to be refreshed before using it.
        self.view().wait_for_function('document.querySelector("#nav-toggle") !== window.originalMenuToggle')
        self.view().locator('#nav-toggle').click()
        self.view().locator('#main-nav').wait_for(state='visible')
        self.page.set_viewport_size({'width': 1024, 'height': 768})
        self.view().locator('#nav-toggle').wait_for(state='hidden')
        self.view().locator('#main-nav').wait_for(state='visible')
        self.page.set_viewport_size({'width': 390, 'height': 844})
        self.view().locator('#main-nav').wait_for(state='hidden')
        self.go('/config')
        self.assertEqual(len(self.terminal_connections), 1)
        self.assertEqual(self.errors, [])

    def test_touch_terminal_keys_and_resize(self):
        self.page.close()
        self.page = self.browser.new_page(viewport={'width': 390, 'height': 844},
                                          is_mobile=True, has_touch=True)
        self.page.on('pageerror', lambda error: self.errors.append(str(error)))
        self.page.route('**/*', self.route)
        self.page.route_web_socket('**/autodarts/terminal/ws', self.terminal_socket)
        self.page.goto('http://oche.test/supervisor')
        terminal = self.page.locator('#board-frame').element_handle().content_frame()
        terminal.get_by_text('Connected', exact=False).wait_for()
        self.assertFalse(terminal.evaluate('document.activeElement.classList.contains("xterm-helper-textarea")'))
        terminal.locator('#terminal-touch-keys').wait_for(state='hidden')
        terminal.get_by_role('button', name='Keys', exact=True).tap()
        terminal.get_by_role('button', name='Down arrow', exact=True).tap()
        terminal.get_by_role('button', name='Enter', exact=True).tap()
        self.page.wait_for_timeout(100)
        self.assertTrue(any(m.get('data') == '\x1b[B' for m in self.terminal_messages))
        self.assertTrue(any(m.get('data') == '\r' for m in self.terminal_messages))
        self.terminal_connections[-1].send(b'\x1b[?1hApplication mode\r\n')
        terminal.get_by_text('Application mode', exact=True).wait_for()
        terminal.get_by_role('button', name='Down arrow', exact=True).tap()
        self.page.wait_for_timeout(100)
        self.assertTrue(any(m.get('data') == '\x1bOB' for m in self.terminal_messages))
        terminal.get_by_role('button', name='Keys', exact=True).tap()
        terminal.locator('#terminal-touch-keys').wait_for(state='hidden')
        self.assertLessEqual(terminal.evaluate('document.documentElement.scrollWidth'), terminal.evaluate('innerWidth'))
        self.assertLessEqual(terminal.evaluate('document.documentElement.scrollHeight'), terminal.evaluate('innerHeight'))
        before = [m['cols'] for m in self.terminal_messages if m.get('type') == 'resize'][-1]
        self.page.set_viewport_size({'width': 844, 'height': 390})
        self.page.wait_for_timeout(200)
        after = [m['cols'] for m in self.terminal_messages if m.get('type') == 'resize'][-1]
        self.assertGreater(after, before)
        terminal.get_by_role('button', name='Keyboard', exact=True).tap()
        self.assertTrue(terminal.evaluate('document.activeElement.classList.contains("xterm-helper-textarea")'))
        self.assertEqual(len(self.terminal_connections), 1)
        self.assertEqual(self.errors, [])

    def test_terminal_links_open_in_new_tabs(self):
        self.browser.contexts[0].route('https://example.com/**', lambda route: route.fulfill(body='Login page'))
        self.page.goto('http://oche.test/supervisor')
        frame = self.view().frame_locator('#board-frame')
        frame.locator('.xterm-screen').wait_for()
        frame.locator('#terminal-status').get_by_text('Connected', exact=False).wait_for()
        for label, output, url in (
            ('https://example.com/login', b'https://example.com/login', 'https://example.com/login'),
            ('Sign in', b'\x1b]8;;https://example.com/device\x07Sign in\x1b]8;;\x07', 'https://example.com/device'),
        ):
            with self.subTest(label=label):
                self.terminal_connections[-1].send(b'\x1b[?1000h\x1b[?1006h\x1b[2J\x1b[H' + output + b'\r\n')
                link = frame.get_by_text(label, exact=True)
                link.hover()
                frame.locator('#terminal[title="' + url + '"]').wait_for()
                # The real TUI repaints in response to mouse input.
                socket = self.terminal_connections[-1]
                def repaint_on_mouse(message):
                    event = json.loads(message)
                    if event.get('type') == 'input' and event.get('data', '').startswith('\x1b[<'):
                        socket.send(b'\x1b[2J\x1b[H' + output + b'\r\n')
                socket.on_message(repaint_on_mouse)
                with self.page.context.expect_page(timeout=3000) as opened:
                    self.page.mouse.down()
                    self.page.wait_for_timeout(150)
                    self.page.mouse.up()
                tab = opened.value
                tab.wait_for_url(url)
                self.assertTrue(tab.evaluate('window.opener === null'))
                tab.close()
                self.assertEqual(self.page.url, 'http://oche.test/supervisor')
        self.assertEqual(len(self.terminal_connections), 1)
        self.assertEqual(self.errors, [])

    def test_split_styled_terminal_url_without_cached_hover(self):
        url = 'https://auth.autodarts.com/link'
        # Exercise clicks even when the linkifier has not resolved a hover.
        self.page.route('**/addon-web-links.js', lambda route: route.fulfill(
            content_type='application/javascript',
            body='window.WebLinksAddon = {WebLinksAddon: class {activate() {} dispose() {}}};'))
        self.page.context.route(url, lambda route: route.fulfill(body='Sign in'))
        self.page.goto('http://oche.test/supervisor')
        frame = self.view().frame_locator('#board-frame')
        frame.locator('#terminal-status').get_by_text('Connected', exact=False).wait_for()
        output = (b'\x1b[?1000h\x1b[?1006h\x1b[2J\x1b[H' + b' ' * 33 +
                  b'\x1b[4:5m\x1b[38;2;144;205;244mh\x1b[7mt\x1b[27m'
                  b'tps://auth.autodarts.com/link\x1b[0m' + b' ' * 34)
        for fragment in ('h', 't', 'tps://auth.autodarts.com/link'):
            with self.subTest(fragment=fragment):
                self.terminal_connections[-1].send(output)
                span = frame.locator('.xterm-rows span').get_by_text(fragment, exact=True)
                span.wait_for()
                rect = span.bounding_box()
                # Click immediately after a repaint, without linkifier hover state.
                self.page.mouse.move(0, 0)
                self.page.mouse.move(rect['x'] + rect['width'] / 2, rect['y'] + rect['height'] / 2)
                self.terminal_connections[-1].send(output)
                self.page.wait_for_timeout(80)
                with self.page.context.expect_page(timeout=3000) as opened:
                    self.page.mouse.down()
                    self.page.mouse.up()
                tab = opened.value
                tab.wait_for_url(url)
                self.assertTrue(tab.evaluate('window.opener === null'))
                tab.close()
        self.assertEqual(self.errors, [])

    def test_copy_terminal_code_with_mouse_reporting_and_http_clipboard(self):
        self.page.goto('http://oche.test/supervisor')
        frame = self.view().frame_locator('#board-frame')
        frame.locator('#terminal-status').get_by_text('Connected', exact=False).wait_for()
        terminal_frame = self.view().locator('#board-frame').element_handle().content_frame()
        terminal_frame.evaluate('''() => {
            Object.defineProperty(navigator, 'clipboard', {value: undefined, configurable: true});
            document.execCommand = command => {
                window.copiedText = document.activeElement.value;
                return command === 'copy';
            };
        }''')
        self.terminal_connections[-1].send(b'\x1b[?1000h\x1b[?1006h\x1b[2J\x1b[HABCD-1234\r\n')
        code = frame.get_by_text('ABCD-1234', exact=True)
        code.wait_for()
        rect = code.bounding_box()
        self.page.keyboard.down('Shift')
        self.page.mouse.move(rect['x'] + 1, rect['y'] + rect['height'] / 2)
        self.page.mouse.down()
        self.page.mouse.move(rect['x'] + rect['width'] - 1, rect['y'] + rect['height'] / 2, steps=8)
        self.page.mouse.up()
        self.page.keyboard.up('Shift')
        frame.locator('.xterm-selection div').first.wait_for()
        self.page.keyboard.press('Control+Shift+C')
        frame.get_by_text('Copied.', exact=True).wait_for()
        self.assertEqual(terminal_frame.evaluate('window.copiedText'), 'ABCD-1234')
        self.assertFalse(any(m.get('data') == '\x03' for m in self.terminal_messages))
        # A repaint must not erase the selection before right-click Copy.
        self.terminal_connections[-1].send(b'\x1b[2J\x1b[HUpdated screen\r\n')
        frame.get_by_text('Updated screen', exact=True).wait_for()
        frame.locator('.xterm-screen').click(button='right')
        frame.get_by_role('menuitem', name='Copy', exact=True).click()
        frame.get_by_text('Copied.', exact=True).wait_for()
        self.assertEqual(terminal_frame.evaluate('window.copiedText'), 'ABCD-1234')
        # HTTPS clipboard path uses the same selected text.
        terminal_frame.evaluate('''() => {
            Object.defineProperty(navigator, 'clipboard', {value: {
                writeText: async text => { window.apiCopiedText = text; }
            }, configurable: true});
        }''')
        frame.locator('.xterm-screen').click(button='right')
        frame.get_by_role('menuitem', name='Copy', exact=True).click()
        terminal_frame.wait_for_function('window.apiCopiedText === "ABCD-1234"')
        self.assertEqual(self.errors, [])

    def test_terminal_reconnect(self):
        self.page.goto('http://oche.test/supervisor')
        frame = self.view().frame_locator('#board-frame')
        frame.locator('.xterm-screen').wait_for()
        frame.locator('#terminal-status').get_by_text('Connected', exact=False).wait_for()
        self.terminal_connections[-1].close()
        frame.get_by_text('Disconnected. Reconnect to open board setup.').wait_for()
        frame.locator('#terminal-reconnect').click()
        frame.locator('#terminal-status').get_by_text('Connected', exact=False).wait_for()
        self.assertEqual(len(self.terminal_connections), 2)
        self.assertEqual(self.errors, [])

    def test_clear_logs_stays_cleared_after_reload(self):
        from app.services import autodarts, autoglow
        for service, process, toggle in (
            ('autodarts', autodarts._process, '#toggle-logs-btn'),
            ('autoglow', autoglow._processes[0], '#toggle-play-btn'),
        ):
            with self.subTest(service=service):
                log = Path(self.temp.name) / (process.name + '.log')
                log.write_text('first line\nsecond line\n')
                with patch.object(process, '_log_file', log), \
                     patch('app.routers.' + service + '.LOG_DIR', Path(self.temp.name)):
                    self.page.goto('http://oche.test/supervisor?service=' + service)
                    self.view().locator(toggle).click()
                    self.view().evaluate('refreshLogs()')
                    self.assertIn('first line', self.view().locator('#ad-logs').inner_text())
                    with self.page.expect_response('**/' + service + '/logs/clear'):
                        self.view().locator('#clear-terminal').click()
                    self.view().wait_for_function('!clearingLogs')
                    self.assertEqual(log.read_text(), '')
                    self.page.reload()
                    self.view().locator(toggle).click()
                    self.view().evaluate('refreshLogs()')
                    self.assertEqual(self.view().locator('#ad-logs').inner_text(), '(no logs yet)')
                    log.write_text('new line\n')
                    self.view().evaluate('refreshLogs()')
                    self.assertEqual(self.view().locator('#ad-logs').inner_text(), 'new line\n')
                    response = self.client.get('/' + service + '/logs/export')
                    self.assertEqual(response.text.splitlines(), ['new line'])
        self.assertEqual(self.errors, [])

    def test_headless_page_refreshes_navigation_settings(self):
        self.page.goto('http://oche.test/config')
        self.go('/supervisor')
        self.view().evaluate('window.pageMarker = "retained"')
        self.go('/config')
        with self.page.expect_response('**/config/data'):
            self.view().locator('#cfg-show-play').uncheck()
        self.go('/supervisor')
        self.view().locator('#nav-link-play').wait_for(state='hidden')
        self.assertEqual(self.view().evaluate('window.pageMarker'), 'retained')
        self.assertEqual(self.errors, [])

    def test_https_setting_reports_failure_and_switches_the_whole_tab(self):
        data = {'enabled': False, 'running': False, 'port': 8443,
                'http_port': 8180, 'error': None}
        fail = True

        def https_request(route):
            if route.request.method == 'POST':
                if fail:
                    route.fulfill(status=503, json={'detail': 'HTTPS port is busy.'})
                    return
                data['enabled'] = data['running'] = route.request.post_data_json['enabled']
                data['mode'] = route.request.post_data_json['mode']
            route.fulfill(json=data)

        self.page.route('**/config/https/status', https_request)
        self.page.goto('http://oche.test/play')
        self.go('/config')  # Settings is inside Oche's retained navigation frame.
        self.view().get_by_role('link', name='Configure HTTPS', exact=True).click()
        self.page.wait_for_url('http://oche.test/config/https')
        self.view().locator('#https-local:not([disabled])').wait_for()
        self.assertEqual(self.view().locator('#cfg-https').count(), 0)
        self.view().locator('#https-local').click()
        self.view().get_by_text('HTTPS port is busy.', exact=True).wait_for()
        self.assertEqual(self.view().locator('#https-state').inner_text(), 'Off')
        self.assertTrue(self.view().locator('#https-open').is_hidden())
        fail = False
        self.view().locator('#https-local').click()
        self.view().locator('#https-open').wait_for()
        self.assertEqual(self.view().locator('#https-open').get_attribute('href'),
                         'https://oche.test:8443/config/https')
        self.view().locator('#https-open').click()
        self.page.wait_for_url('https://oche.test:8443/config/https')
        self.page.get_by_text('Local HTTPS is active. Accept the certificate warning when you first connect in each browser.', exact=True).wait_for()
        self.assertEqual(data['mode'], 'local')
        self.assertEqual(len(self.page.context.pages), 1)
        self.page.locator('#https-disable').click()
        self.page.wait_for_url('http://oche.test:8180/config/https')
        self.page.get_by_text('HTTPS is off. Choose a method below to enable it.', exact=True).wait_for()
        self.assertEqual(self.errors, [])

    def test_acmedns_setup_from_http_prepares_records_then_activates_domain_https(self):
        data = {'enabled': False, 'running': False, 'mode': 'local', 'port': 443,
                'http_port': 8180, 'error': None, 'local_ip': '192.168.1.24',
                'local_ips': ['192.168.1.24'], 'acme_dns': {
                    'available': True, 'busy': False, 'prepared': False, 'domain': '', 'email': ''}}
        submitted = []
        self.page.route('**/config/https/status', lambda route: route.fulfill(json=data))

        def prepare(route):
            submitted.append(route.request.post_data_json)
            data['acme_dns'].update(busy=False, prepared=True, domain='oche.example.com',
                local_ip='192.168.1.24', email='owner@example.com', cname_name='_acme-challenge.oche.example.com',
                cname_target='12345678-1234-1234-1234-123456789012.auth.acme-dns.io')
            route.fulfill(status=202, json={'started': True})

        def issue(route):
            submitted.append(route.request.post_data_json)
            data['acme_dns'].update(busy=True, phase='issuing')
            route.fulfill(status=202, json={'started': True})

        self.page.route('**/config/https/prepare', prepare)
        self.page.route('**/config/https/certificate', issue)
        self.page.goto('http://oche.test/config/https')
        self.page.locator('#https-domain-fields:not([disabled])').wait_for()
        self.page.locator('#https-domain').fill('oche.example.com')
        self.page.locator('#https-email').fill('owner@example.com')
        self.assertEqual(self.page.locator('#https-token').count(), 0)
        self.assertTrue(self.page.locator('#https-domain-submit').is_hidden())
        self.page.locator('#https-prepare').click()
        self.assertEqual(submitted, [])  # Terms must be explicitly accepted.
        self.page.locator('#https-terms').check()
        self.page.locator('#https-prepare').click()
        self.page.locator('#https-dns-step').wait_for()
        self.assertEqual(len(submitted), 1)
        self.assertFalse(data['enabled'])  # Preparation does not turn on local HTTPS.
        self.assertEqual(self.page.locator('#https-record-name').inner_text(), 'oche.example.com')
        self.assertEqual(self.page.locator('#https-record-ip').inner_text(), '192.168.1.24')
        self.assertEqual(self.page.locator('#https-cname-target').inner_text(), data['acme_dns']['cname_target'])
        for width in (320, 390, 768, 1280):
            self.page.set_viewport_size({'width': width, 'height': 900})
            self.assertLessEqual(self.page.evaluate('document.documentElement.scrollWidth'), width)
        self.page.screenshot(path=str(Path(tempfile.gettempdir()) / 'oche-acmedns-desktop.png'), full_page=True)
        self.page.set_viewport_size({'width': 390, 'height': 844})
        self.page.screenshot(path=str(Path(tempfile.gettempdir()) / 'oche-acmedns-mobile.png'), full_page=True)
        self.page.locator('#https-domain').fill('different.example.com')
        self.assertTrue(self.page.locator('#https-domain-submit').is_hidden())
        self.page.locator('#https-domain').fill('oche.example.com')
        self.page.locator('#https-domain-submit').click()
        self.page.get_by_text('Checking DNS and requesting your certificate.', exact=False).wait_for()
        self.assertEqual(len(submitted), 2)
        self.assertEqual(submitted[0]['domain'], 'oche.example.com')
        data.update(enabled=True, running=True, mode='letsencrypt', domain='oche.example.com')
        data['acme_dns'].update(busy=False, last_success='2026-10-04T10:00:00+00:00')
        self.page.locator('#https-domain-open').wait_for(timeout=8000)
        self.assertEqual(self.page.locator('#https-domain-open').get_attribute('href'), 'https://oche.example.com/play')
        self.assertEqual(self.page.locator('#https-domain-open').get_attribute('target'), '_top')
        self.assertEqual(self.page.locator('#https-state').inner_text(), 'Domain HTTPS')
        self.assertEqual(self.errors, [])


if __name__ == '__main__':
    unittest.main()
