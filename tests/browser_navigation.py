"""Browser regression checks: python tests/browser_navigation.py.

Requires Playwright and a locally installed Microsoft Edge. Requests are served
by TestClient and a fake Board Manager; no Docker or cameras are required.
"""
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
        self.view().locator(f'#topbar a[href="{path}"]').first.click()
        self.page.wait_for_url('http://oche.test' + path)
        self.view().locator('#topbar').wait_for()

    def board(self):
        view = self.view()
        view.locator('#board-frame').wait_for(state='visible')
        frame = view.locator('#board-frame').element_handle().content_frame()
        frame.wait_for_selector('h1')
        return frame

    def test_navigation_preserves_camera_documents(self):
        self.page.goto('http://oche.test/autodarts')
        board = self.board()
        board.evaluate('window.streamMarker = "original"')
        self.go('/config')
        self.go('/play')
        self.go('/autodarts')
        self.assertEqual(self.board().evaluate('window.streamMarker'), 'original')
        self.assertEqual(self.board_loads, 1)
        self.assertEqual(self.page.title(), 'Oche - Autodarts')
        self.page.go_back()
        self.page.wait_for_url('http://oche.test/play')
        self.page.go_forward()
        self.page.wait_for_url('http://oche.test/autodarts')
        self.assertEqual(self.board().evaluate('window.streamMarker'), 'original')
        self.go('/supervisor')
        other = self.board()
        other.evaluate('window.streamMarker = "autodarts"')
        self.view().locator('#toggle-logs-btn').click()
        self.view().locator('#toggle-play-btn').click()
        self.view().locator('#toggle-board-btn').click()
        self.go('/config')
        self.go('/supervisor')
        self.assertEqual(self.board().evaluate('window.streamMarker'), 'autodarts')
        self.assertEqual(self.board_loads, 2)
        self.assertEqual(self.errors, [])

    def test_poll_failure_preserves_stream_but_restart_reloads(self):
        for path in ('/autodarts', '/supervisor'):
            with self.subTest(path=path):
                self.page.goto('http://oche.test' + path)
                board = self.board()
                board.evaluate('window.streamMarker = "alive"')
                count = self.board_loads
                self.offline = True
                self.view().evaluate('pollBoardStatus()')
                self.assertEqual(board.evaluate('window.streamMarker'), 'alive')
                self.offline = False
                self.view().evaluate('pollBoardStatus()')
                self.assertEqual(self.board().evaluate('window.streamMarker'), 'alive')
                self.assertEqual(self.board_loads, count)
                self.pid += 1
                self.view().evaluate('pollBoardStatus()')
                self.assertIsNone(self.board().evaluate('window.streamMarker'))
                self.assertEqual(self.board_loads, count + 1)
        self.assertEqual(self.errors, [])

    def test_board_entered_from_settings_retains_stream_and_refreshes_nav(self):
        self.page.goto('http://oche.test/config')
        self.go('/autodarts')
        self.board().evaluate('window.streamMarker = "retained"')
        self.go('/config')
        with self.page.expect_response('**/config/data'):
            self.view().locator('#cfg-show-play').uncheck()
        self.go('/autodarts')
        self.view().locator('#nav-link-play').wait_for(state='hidden')
        self.assertEqual(self.board().evaluate('window.streamMarker'), 'retained')
        self.assertEqual(self.board_loads, 1)
        self.assertEqual(self.errors, [])


if __name__ == '__main__':
    unittest.main()
