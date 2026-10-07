"""Camera workspace checks: python tests/browser_cameras.py.

Uses the existing Playwright/Microsoft Edge environment and TestClient. Camera
devices, images, saves and service status are simulated; no hardware is opened.
"""
import copy
from pathlib import Path
import struct
import sys
import tempfile
import unittest
from unittest.mock import patch
from urllib.parse import urlsplit
import zlib

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from fastapi.testclient import TestClient
from playwright.sync_api import Error as PlaywrightError, expect, sync_playwright

from app.main import app


def camera_png(index):
    """Encode a detailed, well-lit test image using only the standard library."""
    width, height = 320, 240

    def chunk(kind, body):
        return (struct.pack('!I', len(body)) + kind + body
                + struct.pack('!I', zlib.crc32(kind + body) & 0xffffffff))

    rows = bytearray()
    for y in range(height):
        rows.append(0)
        for x in range(width):
            value = 50 if (x // (6 + index) + y // (6 + index)) % 2 else 205
            rows.extend((value, value, value))
    return (b'\x89PNG\r\n\x1a\n'
            + chunk(b'IHDR', struct.pack('!2I5B', width, height, 8, 2, 0, 0, 0))
            + chunk(b'IDAT', zlib.compress(rows)) + chunk(b'IEND', b''))


class CameraWorkspaceTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.playwright = sync_playwright().start()
        cls.addClassCleanup(cls.playwright.stop)
        cls.browser = cls.playwright.chromium.launch(channel='msedge', headless=True)
        cls.addClassCleanup(cls.browser.close)
        cls.frames = [camera_png(index) for index in range(3)]

    def setUp(self):
        temporary = tempfile.TemporaryDirectory()
        self.addCleanup(temporary.cleanup)
        config = patch('app.config.CONFIG_FILE', Path(temporary.name) / 'config.json')
        config.start()
        self.addCleanup(config.stop)
        status = patch('app.services.autodarts.get_status', return_value={
            'installed': True, 'status': 'running', 'pid': 100, 'version': 'test',
        })
        status.start()
        self.addCleanup(status.stop)
        self.client = TestClient(app)
        self.addCleanup(self.client.close)
        self.context = self.browser.new_context()
        self.addCleanup(self.context.close)
        self.page = self.context.new_page()
        self.errors = []
        self.page.on('pageerror', lambda error: self.errors.append(str(error)))
        self.requests = []
        self.saves = []
        self.save_failure = None
        self.data_failure = None
        self.hold_data = False
        self.pending_data = []
        self.hold_frames = set()
        self.pending_frames = []
        self.frame_failures = {}
        self.state = {
            'running': True, 'capturing': True, 'opened': True, 'revision': 'initial',
            'settings': {'devices': ['cam-a', 'cam-b', 'cam-c'],
                         'width': 640, 'height': 480, 'fps': 30},
            'devices': [
                {'id': 'cam-a', 'label': 'Left camera', 'modes': [
                    {'width': 640, 'height': 480, 'fps': [30, 60]},
                    {'width': 1280, 'height': 720, 'fps': [30, 60]},
                    {'width': 1920, 'height': 1080, 'fps': [30]},
                ]},
                {'id': 'cam-b', 'label': 'Middle camera', 'modes': [
                    {'width': 640, 'height': 480, 'fps': [30, 60]},
                    {'width': 1280, 'height': 720, 'fps': [30]},
                ]},
                {'id': 'cam-c', 'label': 'Right camera', 'modes': [
                    {'width': 640, 'height': 480, 'fps': [30]},
                    {'width': 1280, 'height': 720, 'fps': [30, 60]},
                ]},
                {'id': 'cam-d', 'label': 'Spare high-speed camera', 'modes': [
                    {'width': 640, 'height': 480, 'fps': [30, 60]},
                    {'width': 1280, 'height': 720, 'fps': [60]},
                ]},
            ],
            'cameras': [{'index': index, 'device': device, 'fps': 29.5 - index}
                        for index, device in enumerate(['cam-a', 'cam-b', 'cam-c'])],
            'warnings': [],
        }
        self.page.route('**/*', self.route)
        self.page.route_web_socket('**/autodarts/terminal/ws', lambda socket: socket.send(b'Ready\r\n'))
        self.addCleanup(self.release_requests)

    def release_requests(self):
        # Complete deliberate network stalls before closing the browser context.
        for route in self.pending_frames + [route for route, _ in self.pending_data]:
            try:
                route.abort()
            except PlaywrightError:
                pass  # AbortController may already have cancelled this request.
        self.pending_frames.clear()
        self.pending_data.clear()

    def tearDown(self):
        self.assertEqual(self.errors, [])
        controls = [path for method, path in self.requests
                    if method == 'POST' and path in (
                        '/autodarts/start', '/autodarts/stop', '/autodarts/restart')]
        self.assertEqual(controls, [], 'Camera tools must not control the service lifecycle.')

    def route(self, route):
        request = route.request
        url = urlsplit(request.url)
        self.requests.append((request.method, url.path))
        if url.path == '/autodarts/cameras/data':
            if self.hold_data:
                self.pending_data.append((route, copy.deepcopy(self.state)))
            elif self.data_failure:
                route.fulfill(status=self.data_failure[0], json={'detail': self.data_failure[1]})
            else:
                route.fulfill(json=copy.deepcopy(self.state))
        elif url.path == '/autodarts/cameras/settings':
            payload = request.post_data_json
            self.saves.append(payload)
            if self.save_failure:
                route.fulfill(status=self.save_failure[0], json={'detail': self.save_failure[1]})
            else:
                self.state['settings'] = {key: payload[key]
                                          for key in ('devices', 'width', 'height', 'fps')}
                self.state['revision'] = 'saved-' + str(len(self.saves))
                route.fulfill(json=copy.deepcopy(self.state))
        elif url.path.startswith('/autodarts/cameras/frame/'):
            index = int(url.path.rsplit('/', 1)[1])
            failure = self.frame_failures.get(index)
            if index in self.hold_frames:
                self.pending_frames.append(route)
            elif failure:
                route.fulfill(status=failure[0], json={'detail': failure[1]})
            else:
                route.fulfill(content_type='image/png', body=self.frames[index])
        elif url.path == '/autodarts/status':
            route.fulfill(json={'status': 'running', 'pid': 100})
        elif url.path == '/ochecore/ui/api/ui':
            route.fulfill(status=503, json={'detail': 'Core UI settings unavailable in this fixture.'})
        elif url.path in ('/autodarts/start', '/autodarts/stop', '/autodarts/restart'):
            route.fulfill(status=503, json={'detail': 'Service control forbidden in this test.'})
        elif url.port == 3180 or url.hostname != 'oche.test':
            route.fulfill(content_type='text/html', body='<h1>Simulated external service</h1>')
        else:
            response = self.client.request(request.method, request.url, content=request.post_data,
                headers={key: value for key, value in request.headers.items()
                         if key in ('content-type', 'origin', 'sec-fetch-site')})
            route.fulfill(status=response.status_code, body=response.content, headers=dict(response.headers))

    def open_workspace(self):
        self.page.goto('https://oche.test/autodarts/cameras')
        expect(self.page.locator('#camera-device-0')).to_be_enabled()
        return self.page

    def request_count(self, prefix):
        return sum(path.startswith(prefix) for _, path in self.requests)

    def option_values(self, target, selector):
        return target.locator(selector + ' option:not(:disabled)').evaluate_all(
            'options => options.map(option => option.value).filter(Boolean)')

    def view(self):
        element = self.page.locator('.oche-page-frame:not([hidden])')
        return element.element_handle().content_frame() if element.count() else self.page.main_frame

    def mock_audio(self):
        # Observe scheduling without requiring speakers or producing sound.
        self.page.add_init_script('''
            window.cameraAudio = {contexts: 0, gain: 0, frequency: 0};
            window.AudioContext = class {
                constructor() { cameraAudio.contexts++; this.currentTime = 0; this.destination = {}; }
                createOscillator() {
                    return {frequency: {setTargetAtTime(value) { cameraAudio.frequency = value; }},
                        connect(node) { return node; }, start() {}};
                }
                createGain() {
                    return {gain: {value: 0, setTargetAtTime(value) { cameraAudio.gain = value; }},
                        connect() { return this; }};
                }
                resume() { return Promise.resolve(); }
            };
        ''')

    def test_supported_modes_and_explicit_apply(self):
        workspace = self.open_workspace()
        self.assertEqual(self.saves, [])
        self.assertEqual(self.option_values(workspace, '#camera-resolution'), ['640x480', '1280x720'])
        self.assertEqual(self.option_values(workspace, '#camera-fps'), ['30'])
        expect(workspace.locator('#camera-apply')).to_be_disabled()

        # The replacement supports 60 FPS at 640x480. At 1280x720 its FPS
        # does not overlap the middle camera, so that resolution is omitted.
        workspace.locator('#camera-device-2').select_option('cam-d')
        self.assertEqual(self.option_values(workspace, '#camera-resolution'), ['640x480'])
        self.assertEqual(self.option_values(workspace, '#camera-fps'), ['30', '60'])
        workspace.locator('#camera-fps').select_option('60')
        expect(workspace.locator('#camera-apply')).to_be_enabled()
        self.assertEqual(self.saves, [], 'Changing controls must not save automatically.')
        with workspace.expect_response('**/autodarts/cameras/settings'):
            workspace.locator('#camera-apply').click()
        self.assertEqual(self.saves, [{
            'revision': 'initial', 'devices': ['cam-a', 'cam-b', 'cam-d'],
            'width': 640, 'height': 480, 'fps': 60,
        }])
        expect(workspace.locator('#camera-apply')).to_be_disabled()

    def test_conflict_keeps_edits_and_does_not_retry(self):
        workspace = self.open_workspace()
        workspace.locator('#camera-resolution').select_option('1280x720')
        self.save_failure = (409, 'Camera settings changed elsewhere. Refresh before applying again.')
        with workspace.expect_response('**/autodarts/cameras/settings'):
            workspace.locator('#camera-apply').click()
        expect(workspace.locator('#camera-settings-status')).to_contain_text('changed elsewhere')
        self.assertEqual(workspace.locator('#camera-resolution').input_value(), '1280x720')
        self.assertEqual(workspace.locator('#camera-fps').input_value(), '30')
        self.assertEqual(len(self.saves), 1)
        # A routine data refresh must not silently discard unsaved choices.
        workspace.locator('#camera-refresh').click()
        expect(workspace.locator('#camera-resolution')).to_have_value('1280x720')
        self.assertEqual(len(self.saves), 1)

    def test_older_refresh_cannot_replace_a_successful_save(self):
        workspace = self.open_workspace()
        self.hold_data = True
        with workspace.expect_request('**/autodarts/cameras/data'):
            workspace.locator('#camera-refresh').click()
        self.assertEqual(len(self.pending_data), 1)
        workspace.locator('#camera-device-2').select_option('cam-d')
        workspace.locator('#camera-fps').select_option('60')
        with workspace.expect_response('**/autodarts/cameras/settings'):
            workspace.locator('#camera-apply').click()
        expect(workspace.locator('#camera-settings-status')).to_have_text('Camera settings applied.')

        # Complete the older request only after Apply has installed the new
        # settings and revision. This must not turn a saved choice into a draft.
        self.hold_data = False
        delayed, previous = self.pending_data.pop()
        with workspace.expect_response('**/autodarts/cameras/data'):
            delayed.fulfill(json=previous)
        expect(workspace.locator('#camera-device-2')).to_have_value('cam-d')
        expect(workspace.locator('#camera-fps')).to_have_value('60')
        expect(workspace.locator('#camera-apply')).to_be_disabled()
        workspace.locator('#camera-fps').select_option('30')
        with workspace.expect_response('**/autodarts/cameras/settings'):
            workspace.locator('#camera-apply').click()
        self.assertEqual(self.saves[1]['revision'], 'saved-1')
        self.assertEqual(self.saves[1]['devices'], ['cam-a', 'cam-b', 'cam-d'])

    def test_external_settings_replace_clean_values_but_preserve_dirty_revision(self):
        workspace = self.open_workspace()
        self.state['settings'].update(devices=['cam-a', 'cam-b', 'cam-d'], fps=60)
        self.state['revision'] = 'external-1'
        with workspace.expect_response('**/autodarts/cameras/data'):
            workspace.locator('#camera-refresh').click()
        expect(workspace.locator('#camera-device-2')).to_have_value('cam-d')
        expect(workspace.locator('#camera-fps')).to_have_value('60')
        expect(workspace.locator('#camera-apply')).to_be_disabled()

        workspace.locator('#camera-fps').select_option('30')
        # Another client changes the configured camera while this user's draft
        # is dirty. Keep both their choice and the revision it was based on.
        self.state['settings'].update(devices=['cam-a', 'cam-b', 'cam-c'],
                                      width=1280, height=720, fps=30)
        self.state['revision'] = 'external-2'
        with workspace.expect_response('**/autodarts/cameras/data'):
            workspace.locator('#camera-refresh').click()
        expect(workspace.locator('#camera-device-2')).to_have_value('cam-d')
        expect(workspace.locator('#camera-resolution')).to_have_value('640x480')
        expect(workspace.locator('#camera-fps')).to_have_value('30')
        self.assertEqual(self.saves, [])
        self.save_failure = (409, 'Camera settings changed elsewhere. Review the latest settings.')
        with workspace.expect_response('**/autodarts/cameras/settings'):
            workspace.locator('#camera-apply').click()
        self.assertEqual(self.saves[0]['revision'], 'external-1')
        self.assertEqual(self.saves[0]['devices'], ['cam-a', 'cam-b', 'cam-d'])
        expect(workspace.locator('#camera-settings-status')).to_contain_text('changed elsewhere')

        with workspace.expect_response('**/autodarts/cameras/data'):
            workspace.locator('#camera-reset-settings').click()
        expect(workspace.locator('#camera-device-2')).to_have_value('cam-c')
        expect(workspace.locator('#camera-resolution')).to_have_value('1280x720')
        expect(workspace.locator('#camera-apply')).to_be_disabled()

    def test_reset_clears_draft_during_pending_refresh_then_adopts_new_revision(self):
        workspace = self.open_workspace()
        self.state['settings'].update(devices=['cam-a', 'cam-b', 'cam-d'], fps=60)
        self.state['revision'] = 'external-pending'
        self.hold_data = True
        with workspace.expect_request('**/autodarts/cameras/data'):
            workspace.locator('#camera-refresh').click()
        self.assertEqual(len(self.pending_data), 1)

        workspace.locator('#camera-resolution').select_option('1280x720')
        expect(workspace.locator('#camera-apply')).to_be_enabled()
        workspace.locator('#camera-reset-settings').click()
        # Reset must work before the in-flight refresh finishes.
        expect(workspace.locator('#camera-device-2')).to_have_value('cam-c')
        expect(workspace.locator('#camera-resolution')).to_have_value('640x480')
        expect(workspace.locator('#camera-fps')).to_have_value('30')
        expect(workspace.locator('#camera-apply')).to_be_disabled()
        self.assertEqual(self.saves, [])

        self.hold_data = False
        delayed, updated = self.pending_data.pop()
        with workspace.expect_response('**/autodarts/cameras/data'):
            delayed.fulfill(json=updated)
        expect(workspace.locator('#camera-device-2')).to_have_value('cam-d')
        expect(workspace.locator('#camera-fps')).to_have_value('60')
        expect(workspace.locator('#camera-apply')).to_be_disabled()
        workspace.locator('#camera-fps').select_option('30')
        with workspace.expect_response('**/autodarts/cameras/settings'):
            workspace.locator('#camera-apply').click()
        self.assertEqual(self.saves[0]['revision'], 'external-pending')
        self.assertEqual(self.saves[0]['devices'], ['cam-a', 'cam-b', 'cam-d'])

    def test_frame_timeout_clears_guidance_and_keeps_audio_muted(self):
        self.mock_audio()
        workspace = self.open_workspace()
        workspace.locator('#camera-audio').check()
        workspace.wait_for_function('cameraAudio.gain > 0')
        self.hold_frames.add(0)
        with workspace.expect_request('**/autodarts/cameras/frame/0'):
            workspace.wait_for_timeout(300)
        expect(workspace.locator('#camera-image-state-0')).to_contain_text('timed out', timeout=7500)
        expect(workspace.locator('#camera-focus-current')).to_have_text('—')
        expect(workspace.locator('#camera-focus-trend')).to_contain_text('No fresh image')
        self.assertEqual(workspace.locator('#camera-focus-meter').get_attribute('value'), '0')
        self.assertEqual(workspace.evaluate('cameraAudio.gain'), 0)
        workspace.locator('#camera-audio').uncheck()
        workspace.locator('#camera-audio').check()
        self.assertEqual(workspace.evaluate('cameraAudio.gain'), 0)
        expect(workspace.locator('#camera-image-1')).to_be_visible()

    def test_metadata_error_cannot_restore_stale_audio_or_focus_guidance(self):
        self.mock_audio()
        workspace = self.open_workspace()
        workspace.locator('#camera-audio').check()
        workspace.wait_for_function('cameraAudio.gain > 0')
        self.data_failure = (503, 'The Autodarts camera API is unavailable. Try again shortly.')
        with workspace.expect_response('**/autodarts/cameras/data'):
            workspace.locator('#camera-refresh').click()
        expect(workspace.locator('#camera-status')).to_contain_text('API is unavailable')
        expect(workspace.locator('#camera-image-0')).to_be_hidden()
        self.assertEqual(workspace.evaluate('cameraAudio.gain'), 0)
        workspace.locator('#camera-audio').uncheck()
        workspace.locator('#camera-audio').check()
        self.assertEqual(workspace.evaluate('cameraAudio.gain'), 0)
        expect(workspace.locator('#camera-focus-current')).to_have_text('—')
        self.assertEqual(workspace.locator('#camera-focus-meter').get_attribute('value'), '0')

    def test_unavailable_camera_and_service_are_explained(self):
        self.frame_failures[1] = (404, 'This camera has no frame yet.')
        workspace = self.open_workspace()
        expect(workspace.locator('#camera-image-0')).to_be_visible()
        expect(workspace.locator('#camera-image-1')).to_be_hidden()
        expect(workspace.locator('#camera-image-state-1')).not_to_have_text('Waiting for an image')
        self.assertEqual(self.saves, [])

        self.data_failure = (503, 'Start Autodarts in Supervisor to use the camera tools.')
        workspace.reload()
        expect(workspace.locator('#camera-status')).to_contain_text('Start Autodarts')
        expect(workspace.locator('#camera-device-0')).to_be_disabled()
        self.assertEqual(self.saves, [])

    def test_pause_selection_zoom_camera_switch_and_reset(self):
        self.mock_audio()
        workspace = self.open_workspace()
        for index in range(3):
            expect(workspace.locator(f'#camera-image-{index}')).to_be_visible()
        expect(workspace.locator('#camera-focus-current')).not_to_have_text('—')
        self.assertEqual(workspace.evaluate('cameraAudio.contexts'), 0)
        workspace.locator('#camera-audio').check()
        workspace.wait_for_function('cameraAudio.gain > 0')
        workspace.locator('#camera-preview-toggle').click()
        expect(workspace.locator('#camera-preview-toggle')).to_have_text('Resume preview')
        self.assertEqual(workspace.evaluate('cameraAudio.gain'), 0)
        workspace.wait_for_timeout(250)
        paused_count = self.request_count('/autodarts/cameras/frame/')
        workspace.wait_for_timeout(1100)
        self.assertEqual(self.request_count('/autodarts/cameras/frame/'), paused_count)

        canvas = workspace.locator('#camera-focus-canvas')
        initial_selection = canvas.evaluate('canvas => canvas.toDataURL()')
        canvas.click(position={'x': 40, 'y': 40})
        self.assertNotEqual(canvas.evaluate('canvas => canvas.toDataURL()'), initial_selection)
        expect(workspace.locator('#camera-focus-best')).to_have_text('—')
        moved_selection = canvas.evaluate('canvas => canvas.toDataURL()')
        canvas.press('ArrowRight')
        self.assertNotEqual(canvas.evaluate('canvas => canvas.toDataURL()'), moved_selection)
        workspace.locator('#camera-roi-reset').click()
        self.assertEqual(canvas.evaluate('canvas => canvas.toDataURL()'), initial_selection)

        zoom = workspace.locator('#camera-zoom-canvas')
        initial_zoom = zoom.evaluate('canvas => canvas.toDataURL()')
        workspace.locator('#camera-zoom').fill('4')
        expect(workspace.locator('#camera-zoom-value')).to_have_text('4×')
        self.assertNotEqual(zoom.evaluate('canvas => canvas.toDataURL()'), initial_zoom)
        workspace.locator('[data-camera="1"]').click()
        expect(workspace.locator('#camera-focus-title')).to_have_text('Focus Camera 2')
        expect(workspace.locator('[data-camera="1"]')).to_have_attribute('aria-pressed', 'true')
        expect(workspace.locator('[data-camera="0"]')).to_have_attribute('aria-pressed', 'false')
        expect(workspace.locator('#camera-focus-best')).to_have_text('—')
        workspace.locator('#camera-preview-toggle').click()
        expect(workspace.locator('#camera-focus-current')).not_to_have_text('—')
        workspace.locator('#camera-preview-toggle').click()
        workspace.locator('#camera-focus-reset').click()
        expect(workspace.locator('#camera-focus-current')).to_have_text('—')
        expect(workspace.locator('#camera-focus-best')).to_have_text('—')
        self.assertEqual(workspace.locator('#camera-focus-meter').get_attribute('value'), '0')
        self.assertEqual(self.saves, [])

    def test_keyboard_and_tap_keep_selection_size_at_image_edges(self):
        # Read the actual rectangle drawn on the visible canvas, rather than
        # exposing private selection state from the application.
        self.page.add_init_script('''
            const strokeRect = CanvasRenderingContext2D.prototype.strokeRect;
            CanvasRenderingContext2D.prototype.strokeRect = function(x, y, width, height) {
                if (this.canvas.id === 'camera-focus-canvas') {
                    window.cameraDrawnROI = {x, y, width, height,
                        imageWidth: this.canvas.width, imageHeight: this.canvas.height};
                }
                return strokeRect.apply(this, arguments);
            };
        ''')
        workspace = self.open_workspace()
        expect(workspace.locator('#camera-image-0')).to_be_visible()
        workspace.locator('#camera-preview-toggle').click()
        initial = workspace.evaluate('cameraDrawnROI')
        canvas = workspace.locator('#camera-focus-canvas')
        canvas.focus()

        def assert_edge(horizontal, vertical):
            selection = workspace.evaluate('cameraDrawnROI')
            self.assertAlmostEqual(selection['width'], initial['width'])
            self.assertAlmostEqual(selection['height'], initial['height'])
            self.assertAlmostEqual(selection['x'], horizontal * (selection['imageWidth'] - initial['width']))
            self.assertAlmostEqual(selection['y'], vertical * (selection['imageHeight'] - initial['height']))

        for horizontal, vertical in ((1, 1), (0, 0)):
            for key in (('ArrowRight' if horizontal else 'ArrowLeft'),
                        ('ArrowDown' if vertical else 'ArrowUp')):
                canvas.evaluate('''(element, key) => {
                    for (let count = 0; count < 60; count++) {
                        element.dispatchEvent(new KeyboardEvent('keydown', {key, bubbles: true}));
                    }
                }''', key)
            assert_edge(horizontal, vertical)

        box = canvas.bounding_box()
        canvas.click(position={'x': box['width'] - 1, 'y': box['height'] - 1})
        assert_edge(1, 1)
        canvas.click(position={'x': 1, 'y': 1})
        assert_edge(0, 0)
        self.assertEqual(self.saves, [])

    def test_hidden_retained_workspace_stops_polling_and_audio_then_resumes(self):
        self.mock_audio()
        self.page.emulate_media(color_scheme='dark')
        self.page.goto('https://oche.test/supervisor')
        self.assertEqual(self.request_count('/autodarts/cameras/frame/'), 0)
        self.page.locator('#toggle-cameras-btn').click()
        camera_element = self.page.locator('#cameras-frame')
        expect(camera_element).to_be_visible()
        self.assertEqual(camera_element.get_attribute('src'), '/autodarts/cameras')
        workspace = camera_element.element_handle().content_frame()
        expect(workspace.locator('#camera-image-0')).to_be_visible()
        workspace.evaluate('window.cameraMarker = "retained"')
        workspace.locator('#camera-audio').check()
        workspace.wait_for_function('cameraAudio.gain > 0')
        self.page.locator('#theme-toggle').click()
        expect(workspace.locator('html')).to_have_attribute('data-theme', 'light')

        self.page.locator('#toggle-board-btn').click()
        workspace.wait_for_function('cameraAudio.gain === 0')
        self.page.wait_for_timeout(250)
        hidden_count = self.request_count('/autodarts/cameras/frame/')
        self.page.wait_for_timeout(1100)
        self.assertEqual(self.request_count('/autodarts/cameras/frame/'), hidden_count)
        with self.page.expect_response('**/autodarts/cameras/frame/0'):
            self.page.locator('#toggle-cameras-btn').click()
        workspace.wait_for_function('cameraAudio.gain > 0')

        self.page.locator('#topbar a[href="/play"]').click()
        self.page.wait_for_url('https://oche.test/play')
        workspace.wait_for_function('cameraAudio.gain === 0')
        self.page.wait_for_timeout(250)
        frames = self.request_count('/autodarts/cameras/frame/')
        polls = self.request_count('/autodarts/cameras/data')
        self.page.wait_for_timeout(5300)
        self.assertEqual(self.request_count('/autodarts/cameras/frame/'), frames)
        self.assertEqual(self.request_count('/autodarts/cameras/data'), polls)
        with self.page.expect_response('**/autodarts/cameras/frame/0'):
            self.view().locator('#topbar a[href="/supervisor"]').click()
        self.page.wait_for_url('https://oche.test/supervisor')
        self.assertEqual(workspace.evaluate('window.cameraMarker'), 'retained')
        workspace.wait_for_function('cameraAudio.gain > 0')
        self.assertEqual(workspace.evaluate('cameraAudio.contexts'), 1)
        self.assertEqual(self.saves, [])

    def test_workspace_uses_both_themes_and_fits_small_mobile_width(self):
        self.page.set_viewport_size({'width': 320, 'height': 780})
        self.page.emulate_media(color_scheme='dark')
        workspace = self.open_workspace()
        for theme, background in (('dark', 'rgb(28, 28, 33)'),
                                  ('light', 'rgb(245, 244, 241)')):
            with self.subTest(theme=theme):
                workspace.emulate_media(color_scheme=theme)
                expect(workspace.locator('html')).to_have_attribute('data-theme', theme)
                self.assertEqual(workspace.evaluate('getComputedStyle(document.body).backgroundColor'),
                                 background)
                self.assertTrue(workspace.evaluate('''() =>
                    document.documentElement.scrollWidth <= document.documentElement.clientWidth
                    && document.body.scrollWidth <= document.documentElement.clientWidth'''))
                for selector in ('#camera-preview-toggle', '#camera-focus-canvas',
                                 '#camera-zoom-canvas', '#camera-device-0', '#camera-resolution'):
                    box = workspace.locator(selector).bounding_box()
                    self.assertGreater(box['width'], 0)
                    self.assertGreaterEqual(box['x'], 0)
                    self.assertLessEqual(box['x'] + box['width'], 320.5)


if __name__ == '__main__':
    unittest.main()
