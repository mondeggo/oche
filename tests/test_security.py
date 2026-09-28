import unittest
from unittest.mock import patch

from fastapi.testclient import TestClient

from app.main import app
from app.security import same_origin


class OriginTests(unittest.TestCase):
    def test_origin_validation(self):
        self.assertTrue(same_origin('http://example.com:80', 'http', 'example.com'))
        self.assertTrue(same_origin('https://EXAMPLE.com', 'https', 'example.com:443'))
        for origin in ('null', 'http://[', 'http://example.com:invalid',
                       'https://example.com', 'http://other.example',
                       'http://user@example.com', 'http://example.com/path'):
            with self.subTest(origin=origin):
                self.assertFalse(same_origin(origin, 'http', 'example.com'))

    def test_browser_cannot_clear_logs_from_another_origin(self):
        client = TestClient(app)
        self.addCleanup(client.close)
        for service in ('autodarts', 'autoglow'):
            with patch('app.routers.' + service + '.' + service + '.clear_logs') as clear:
                for headers in ({'origin': 'https://other.example'}, {'origin': 'null'},
                                {'sec-fetch-site': 'cross-site'}, {'origin': 'http://['}):
                    response = client.post('/' + service + '/logs/clear', headers=headers)
                    self.assertEqual(response.status_code, 403)
                clear.assert_not_called()
                self.assertEqual(client.post('/' + service + '/logs/clear',
                                            headers={'origin': 'http://testserver'}).status_code, 200)
                clear.assert_called_once()
