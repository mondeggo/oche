import os
import unittest
from unittest.mock import patch

from app.server import main


class ServerTests(unittest.TestCase):
    def test_configured_port_and_legacy_default(self):
        for environment, expected in (({}, 8180), ({"OCHE_PORT": "80"}, 80),
                                      ({"OCHE_PORT": "9090"}, 9090)):
            with self.subTest(environment=environment):
                with patch.dict(os.environ, environment, clear=True), patch("app.server.uvicorn.run") as run:
                    main()
                    run.assert_called_once_with("app.main:app", host="0.0.0.0", port=expected)

    def test_invalid_port_does_not_start_server(self):
        for port in ("0", "65536", "abc", ""):
            with self.subTest(port=port):
                with patch.dict(os.environ, {"OCHE_PORT": port}), patch("app.server.uvicorn.run") as run:
                    with self.assertRaises(ValueError):
                        main()
                    run.assert_not_called()
