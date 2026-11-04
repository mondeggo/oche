import os
import unittest
from pathlib import Path
from unittest.mock import patch

from app.server import main


class ServerTests(unittest.TestCase):
    def test_configured_port_and_default(self):
        for environment, expected in (({}, 8180), ({"OCHE_PORT": "80"}, 80),
                                      ({"OCHE_PORT": "9090"}, 9090)):
            with self.subTest(environment=environment):
                with patch.dict(os.environ, environment, clear=True), patch("app.server.uvicorn.run") as run:
                    main()
                    run.assert_called_once_with(
                        "app.main:app", host="0.0.0.0", port=expected, reload=False, reload_dirs=None
                    )

    def test_development_reload_watches_application_only(self):
        with patch.dict(os.environ, {"OCHE_DEV_RELOAD": "1"}, clear=True), patch("app.server.uvicorn.run") as run:
            main()
            run.assert_called_once_with(
                "app.main:app", host="0.0.0.0", port=8180, reload=True,
                reload_dirs=[str(Path(__file__).resolve().parents[2] / "app")],
            )

    def test_invalid_port_does_not_start_server(self):
        for port in ("0", "65536", "abc", ""):
            with self.subTest(port=port):
                with patch.dict(os.environ, {"OCHE_PORT": port}), patch("app.server.uvicorn.run") as run:
                    with self.assertRaises(ValueError):
                        main()
                    run.assert_not_called()
