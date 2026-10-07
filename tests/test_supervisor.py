import tempfile
from pathlib import Path
import unittest
from unittest.mock import patch
from fastapi.testclient import TestClient
from app.main import app


class SupervisorTests(unittest.TestCase):
    def setUp(self):
        self.client = TestClient(app)
        self.addCleanup(self.client.close)

    def test_supervisor_selects_service_and_keeps_previews(self):
        response = self.client.get('/supervisor')
        self.assertEqual(response.status_code, 200)
        self.assertIn('/supervisor?service=autodarts', response.text)
        self.assertIn('/supervisor?service=autoglow', response.text)
        self.assertIn('/supervisor?service=ochecore', response.text)
        self.assertIn('/supervisor?service=system', response.text)
        self.assertIn('<iframe', response.text)
        self.assertIn('/autodarts/start', response.text)
        self.assertIn('/autodarts/terminal', response.text)
        self.assertIn('Autodarts setup terminal', response.text)
        self.assertIn('id="toggle-board-btn"', response.text)
        self.assertIn("onclick=\"showView('board')\"", response.text)
        self.assertNotIn('title="Autodarts Board"', response.text)
        ag = self.client.get('/supervisor?service=autoglow')
        self.assertEqual(ag.status_code, 200)
        self.assertIn('/autoglow/process/web/start', ag.text)
        self.assertNotIn('/autoglow/process/listener/start', ag.text)
        self.assertIn('AutoGlow 2', ag.text)
        self.assertIn("const boardUrl = '/autoglow/ui/'", ag.text)
        self.assertNotIn('Listener Logs', ag.text)
        self.assertNotIn('id="toggle-logs-btn"', ag.text)
        self.assertIn("onclick=\"showView('board')\"", ag.text)
        toggle_bar = ag.text[ag.text.index('class="embed-toggle"'):ag.text.index('class="embed-body"')]
        self.assertEqual(toggle_bar.count('Logs'), 1)
        self.assertLess(response.text.index('class="panels-menu"'), response.text.index('id="nav-link-autodarts"'))
        self.assertLess(response.text.index('id="nav-link-autodarts"'), response.text.index('>Settings</a>'))
        self.assertIn('>Supervisor</a', response.text)
        self.assertNotIn('id="nav-link-board"', response.text)
        self.assertEqual(self.client.get('/autodarts').status_code, 200)

    def test_ochecore_supervisor_controls_preview_and_logs(self):
        response = self.client.get('/supervisor?service=ochecore')
        self.assertEqual(response.status_code, 200)
        for action in ('start', 'stop', 'restart'):
            self.assertIn('/ochecore/' + action, response.text)
        self.assertIn("const boardUrl = '/ochecore/ui/'", response.text)
        self.assertIn('allow="autoplay"', response.text)
        self.assertIn('oc-autostart-boot', response.text)
        self.assertIn('/ochecore/logs', response.text)

    def test_autoglow_stop_controls_service(self):
        with patch('app.routers.autoglow.autoglow.stop') as stop:
            self.assertEqual(self.client.post('/autoglow/stop').status_code, 200)
            stop.assert_called_once()

    def test_autoglow_restart_and_missing_install(self):
        with patch('app.routers.autoglow.autoglow.installed', return_value=True), patch('app.routers.autoglow.autoglow.restart') as restart:
            self.assertEqual(self.client.post('/autoglow/restart').status_code, 200)
            restart.assert_called_once()
        with patch('app.routers.autoglow.autoglow.installed', return_value=False):
            self.assertEqual(self.client.post('/autoglow/restart').status_code, 503)

    def test_autoglow_combined_logs(self):
        logs = {'autoglow-web': 'server output'}
        with patch('app.routers.autoglow.autoglow.logs', return_value=logs):
            self.assertEqual(self.client.get('/autoglow/logs').json(), logs)

    def test_export_downloads_full_logs_and_handles_missing_file(self):
        for service, filename in (('autodarts', 'autodarts.log'), ('autoglow', 'autoglow-web.log')):
            with self.subTest(service=service), tempfile.TemporaryDirectory() as folder:
                with patch('app.routers.' + service + '.LOG_DIR', Path(folder)):
                    response = self.client.get('/' + service + '/logs/export')
                    self.assertEqual(response.status_code, 200)
                    self.assertEqual(response.text, '')
                    content = ''.join(f'line {i}\n' for i in range(350))
                    (Path(folder) / filename).write_bytes(content.encode())
                    response = self.client.get('/' + service + '/logs/export')
                    self.assertEqual(response.text, content)
                    self.assertIn('attachment;', response.headers['content-disposition'])
                    self.assertIn(filename, response.headers['content-disposition'])

    def test_clear_logs_preserves_open_writer_and_other_service_logs(self):
        from app.services import autodarts, autoglow
        for service, process in (('autodarts', autodarts._process), ('autoglow', autoglow._processes[0])):
            with self.subTest(service=service), tempfile.TemporaryDirectory() as folder:
                log = Path(folder) / 'service.log'
                other = Path(folder) / 'other.log'
                other.write_text('keep this')
                with patch.object(process, '_log_file', log):
                    self.assertEqual(self.client.post('/' + service + '/logs/clear').status_code, 200)
                    with log.open('a') as writer:
                        writer.write('old output\n')
                        writer.flush()
                        self.assertEqual(self.client.post('/' + service + '/logs/clear').status_code, 200)
                        self.assertEqual(log.read_text(), '')
                        writer.write('new output\n')
                        writer.flush()
                        self.assertEqual(process.tail_log(), 'new output\n')
                    self.assertEqual(other.read_text(), 'keep this')

    def test_clear_failure_is_reported(self):
        for service in ('autodarts', 'autoglow'):
            with self.subTest(service=service), patch('app.routers.' + service + '.' + service + '.clear_logs', side_effect=PermissionError):
                response = self.client.post('/' + service + '/logs/clear')
                self.assertEqual(response.status_code, 500)
                self.assertIn('Failed to clear', response.json()['detail'])

    def test_old_board_paths_redirect_to_supervisor(self):
        for path in ('/board', '/autodarts'):
            response = self.client.get(path, follow_redirects=False)
            self.assertEqual(response.status_code, 308)
            self.assertEqual(response.headers['location'], '/supervisor?service=autodarts')
        settings = self.client.get('/config').text
        self.assertNotIn('cfg-show-board', settings)
        self.assertNotIn('Auto-hide navbar on Autodarts', settings)
