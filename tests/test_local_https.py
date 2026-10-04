import os
from pathlib import Path
import shutil
import socket
import ssl
import tempfile
import unittest
from unittest.mock import patch

from fastapi.testclient import TestClient
import httpx

from app.config import load_config, save_config
from app.main import app
from app.services.local_https import certificate_names, ensure_certificate

OPENSSL = shutil.which("openssl")
if not OPENSSL and Path(r"C:\Program Files\Git\usr\bin\openssl.exe").exists():
    OPENSSL = r"C:\Program Files\Git\usr\bin\openssl.exe"


def free_port():
    with socket.socket() as sock:
        sock.bind(("127.0.0.1", 0))
        return sock.getsockname()[1]


@unittest.skipUnless(OPENSSL, "OpenSSL is required for certificate integration tests")
class LocalHTTPSTests(unittest.TestCase):
    def setUp(self):
        temporary = tempfile.TemporaryDirectory()
        self.addCleanup(temporary.cleanup)
        self.data = Path(temporary.name)
        self.port = free_port()
        for target, value in (
            ("app.config.CONFIG_FILE", self.data / "config.json"),
            ("app.services.local_https.DATA_DIR", self.data),
            ("app.services.dns_certificate.DATA_DIR", self.data),
        ):
            patcher = patch(target, value)
            patcher.start()
            self.addCleanup(patcher.stop)
        for patcher in (
            patch.dict(os.environ, {"OCHE_HTTPS_PORT": str(self.port)}),
            patch("app.services.local_https.shutil.which", return_value=OPENSSL),
            patch("app.main.autodarts_service.stop"),
            patch("app.main.autoglow_service.stop"),
        ):
            patcher.start()
            self.addCleanup(patcher.stop)
        save_config({"autostart_autodarts": False, "autostart_autoglow": False})

    def test_toggle_serves_tls_disables_from_https_and_reuses_certificate_after_restart(self):
        with TestClient(app, base_url="http://127.0.0.1:8180") as client:
            self.assertFalse(client.get("/config/https/status").json()["enabled"])
            self.assertFalse((self.data / "https").exists())
            response = client.post("/config/https", json={"enabled": True})
            self.assertEqual(response.status_code, 200, response.text)
            self.assertTrue(response.json()["running"])
            self.assertEqual(response.json()["http_port"], 8180)
            certificate = (self.data / "https/local.pem").read_bytes()
            context = ssl.create_default_context(cafile=str(self.data / "https/local.pem"))
            with httpx.Client(base_url=f"https://127.0.0.1:{self.port}", verify=context, trust_env=False) as secure:
                self.assertEqual(secure.get("/healthz").json(), {"ok": True})
                self.assertEqual(secure.post("/config/https", json={"enabled": False},
                    headers={"Origin": f"https://127.0.0.1:{self.port}"}).status_code, 200)
            self.assertFalse(load_config()["https_enabled"])
            self.assertEqual(client.get("/healthz").status_code, 200)
            self.assertEqual(client.post("/config/https", json={"enabled": True}).status_code, 200)
            self.assertEqual((self.data / "https/local.pem").read_bytes(), certificate)
        with TestClient(app) as client:
            self.assertTrue(client.get("/config/https/status").json()["running"])
            self.assertEqual((self.data / "https/local.pem").read_bytes(), certificate)

    def test_busy_port_reports_failure_and_keeps_http_available(self):
        with socket.socket() as occupied:
            occupied.bind(("0.0.0.0", self.port))
            occupied.listen()
            with TestClient(app) as client:
                response = client.post("/config/https", json={"enabled": True})
                self.assertEqual(response.status_code, 503)
                self.assertIn(str(self.port), response.json()["detail"])
                self.assertFalse(load_config()["https_enabled"])
                self.assertFalse((self.data / "https").exists())
                self.assertEqual(client.get("/healthz").status_code, 200)
        with TestClient(app) as client:
            self.assertEqual(client.post("/config/https", json={"enabled": True}).status_code, 200)

    def test_startup_failure_is_visible_and_retry_recovers(self):
        config = load_config()
        config["https_enabled"] = True
        save_config(config)
        with patch("app.services.local_https.ensure_certificate", side_effect=RuntimeError("Certificate failure")):
            with TestClient(app) as client:
                status = client.get("/config/https/status").json()
                self.assertTrue(status["enabled"])
                self.assertFalse(status["running"])
                self.assertEqual(status["error"], "Certificate failure")
                self.assertEqual(client.get("/healthz").status_code, 200)
        with TestClient(app) as client:
            self.assertTrue(client.get("/config/https/status").json()["running"])

    def test_browser_origin_and_other_settings(self):
        with TestClient(app) as client:
            response = client.post("/config/https", json={"enabled": True},
                                   headers={"Origin": "https://another.example"})
            self.assertEqual(response.status_code, 403)
            self.assertFalse((self.data / "https").exists())
            client.post("/config/data", json={"show_play_in_navbar": False})
            client.post("/config/https", json={"enabled": True})
            self.assertFalse(load_config()["show_play_in_navbar"])
            client.post("/config/data", json={"show_autoglow_in_navbar": False})
            self.assertTrue(load_config()["https_enabled"])

    def test_invalid_port_and_generation_failure_are_recoverable(self):
        with TestClient(app) as client:
            for port in ("", "abc", "0", "65536"):
                with patch.dict(os.environ, {"OCHE_HTTPS_PORT": port}):
                    self.assertEqual(client.post("/config/https", json={"enabled": True}).status_code, 503)
                    self.assertFalse(client.get("/config/https/status").json()["running"])
            with patch("app.services.local_https.shutil.which", return_value=None):
                self.assertEqual(client.post("/config/https", json={"enabled": True}).status_code, 503)
            self.assertEqual(client.post("/config/https", json={"enabled": True}).status_code, 200)

    def test_invalid_certificate_is_replaced_and_request_name_is_sanitized(self):
        names = certificate_names("oche.local\nDNS:evil.example")
        self.assertNotIn("evil.example", names)
        self.assertIn("IP:127.0.0.1", names)
        bundle = ensure_certificate("192.168.10.20")
        old = bundle.read_bytes()
        with patch("app.services.local_https.ssl.SSLContext.load_cert_chain", side_effect=[ssl.SSLError(), None]):
            ensure_certificate("192.168.10.20")
        self.assertNotEqual(bundle.read_bytes(), old)
        ssl.SSLContext(ssl.PROTOCOL_TLS_SERVER).load_cert_chain(bundle)


if __name__ == "__main__":
    unittest.main()
