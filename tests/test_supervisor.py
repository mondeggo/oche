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
        self.assertIn('<iframe', response.text)
        self.assertIn('/autodarts/start', response.text)
        ag = self.client.get('/supervisor?service=autoglow')
        self.assertEqual(ag.status_code, 200)
        self.assertIn('/autoglow/process/web/start', ag.text)
        self.assertIn('/autoglow/process/listener/start', ag.text)
        self.assertIn('Webserver', ag.text)
        self.assertIn('Web Logs', ag.text)
        self.assertIn('Listener Logs', ag.text)
        self.assertLess(response.text.index('class="panels-menu"'), response.text.index('id="nav-link-autodarts"'))
        self.assertLess(response.text.index('id="nav-link-autodarts"'), response.text.index('aria-label="Settings"'))
        self.assertIn('>Supervisor</a', response.text)
        self.assertIn('>Autodarts</a', response.text)
        self.assertEqual(self.client.get('/autodarts').status_code, 200)

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

    def test_autoglow_logs_separate_processes(self):
        logs = {'autoglow-web': 'server output', 'autoglow-listener': 'listener output'}
        with patch('app.routers.autoglow.autoglow.logs', return_value=logs):
            self.assertEqual(self.client.get('/autoglow/logs').json(), logs)
