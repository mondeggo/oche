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
        self.page = self.browser.new_page()
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
        elif url.hostname == 'oche.test':
            response = self.client.request(route.request.method,
                url.path + ('?' + url.query if url.query else ''),
                content=route.request.post_data,
                headers={'content-type': route.request.headers.get('content-type', '')})
            route.fulfill(status=response.status_code, body=response.content,
                          headers=dict(response.headers))
        else:
            route.fulfill(content_type='text/html', body='<h1>External page</h1>')

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
        self.page.wait_for_url('http://oche.test' + path)
        self.view().locator('#topbar').wait_for()

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
        paths = ('/', '/config', '/config/https', '/supervisor', '/supervisor?service=autoglow',
                 '/play', '/autoglow', '/panels/' + panels[0]['id'])
        for width, height in ((320, 740), (390, 844), (768, 1024), (1024, 768), (1440, 900)):
            self.page.set_viewport_size({'width': width, 'height': height})
            for path in paths:
                with self.subTest(width=width, path=path):
                    self.page.goto('http://oche.test' + path)
                    self.page.locator('.topbar.nav-ready').wait_for()
                    self.assertLessEqual(self.page.evaluate('document.documentElement.scrollWidth'), width)
                    if width <= 900:
                        self.page.locator('#nav-toggle').click()
                        self.page.locator('.panels-menu summary').click()
                        dropdown = self.page.locator('.panels-dropdown').bounding_box()
                        self.assertGreaterEqual(dropdown['x'], 0)
                        self.assertLessEqual(dropdown['x'] + dropdown['width'], width + 1)
                        self.assertLessEqual(self.page.evaluate('document.documentElement.scrollWidth'), width)
                        self.page.keyboard.press('Escape')
                    if path in ('/play', '/autoglow') or path.startswith('/panels/'):
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
