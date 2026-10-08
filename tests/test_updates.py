import hashlib
import io
import json
import os
from pathlib import Path
import tarfile
import tempfile
import unittest
from unittest.mock import patch, Mock
from urllib.error import HTTPError

import oche_runtime as store
from app.services import updates


class UpdateTests(unittest.TestCase):
    def setUp(self):
        temp = tempfile.TemporaryDirectory()
        self.addCleanup(temp.cleanup)
        self.root = Path(temp.name)
        for name, value in (("ROOT", self.root / "modules"), ("DATA", self.root),
                            ("BUNDLES", self.root / "bundles"), ("RUNTIME", "test-runtime")):
            patcher = patch.object(store, name, value)
            patcher.start()
            self.addCleanup(patcher.stop)
        self.manager = updates.UpdateManager()

    def seed(self, name="oche", version="1"):
        path = store.BUNDLES / name
        path.mkdir(parents=True, exist_ok=True)
        (path / "VERSION").write_text(version)
        (path / "RUNTIME").write_text(store.RUNTIME)
        (path / "payload").write_text("bundled")
        store.initialize()
        return store.location(name, "unused")

    def archive(self, members):
        archive = self.root / "bundle.tar.gz"
        with tarfile.open(archive, "w:gz") as bundle:
            for name, content, kind in members:
                entry = tarfile.TarInfo(name)
                entry.type = kind
                entry.size = len(content) if kind == tarfile.REGTYPE else 0
                entry.linkname = "../../outside"
                bundle.addfile(entry, io.BytesIO(content))
        return archive

    def test_seed_preserves_active_and_recovers_interrupted_activation(self):
        target = self.seed()
        self.assertEqual((target / "payload").read_text(), "bundled")
        store.write_state("oche", {"active": "2", "previous": "1", "pending": True, "runtime": store.RUNTIME})
        store.initialize()
        self.assertEqual(store.read_state("oche")["active"], "1")
        self.assertFalse(store.read_state("oche")["pending"])
        self.assertIn("Interrupted", store.read_state("oche")["error"])

    def test_semantic_versions_and_revisions_are_separate_including_legacy_bundles(self):
        from app.services.release_metadata import application_version
        for name, version in (("ochecore", "0.1.2"), ("autoglow", "2.1")):
            source = store.BUNDLES / name
            source.mkdir(parents=True)
            revision = ("a" if name == "ochecore" else "b") * 40
            (source / "VERSION").write_text(revision)  # Old images overwrote VERSION.
            (source / "REVISION").write_text(revision)
            if name == "ochecore":
                (source / "pyproject.toml").write_text('[project]\nversion = "0.1.2"\n')
            else:
                (source / "core").mkdir()
                (source / "core/updater.py").write_text('raise RuntimeError("must not execute")\nCURRENT_VERSION = "2.1"\n')
            self.assertEqual(application_version(source), version)
            store.initialize()
            item = next(item for item in self.manager.status()["modules"] if item["name"] == name)
            self.assertEqual(item["active"], revision)
            self.assertEqual(item["active_version"], version)
            self.assertEqual(item["bundled_version"], version)
            self.assertEqual(item["active_revision"], revision)
            (source / "VERSION").write_text(version)  # New images preserve readable VERSION.
            store.initialize()
            self.assertEqual(store.read_state(name)["active"], revision)

    def test_new_runtime_uses_bundle_without_deleting_old_install(self):
        target = self.seed()
        with patch.object(store, "RUNTIME", "new-runtime"):
            store.initialize()
            self.assertNotEqual(target, store.location("oche", "unused"))
            self.assertTrue(target.exists())

    def test_unsafe_archives_rejected(self):
        for name, kind in (("../outside", tarfile.REGTYPE), ("/outside", tarfile.REGTYPE),
                           ("link", tarfile.SYMTYPE), ("hard", tarfile.LNKTYPE),
                           ("a\\..\\outside", tarfile.REGTYPE)):
            with self.subTest(name=name):
                archive = self.archive([(name, b"bad", kind)])
                with self.assertRaises(ValueError):
                    updates.extract(archive, self.root / "extract")

    def test_checksum_failure_never_activates(self):
        self.seed()
        self.manager.available = {"oche": {"version": "2", "url": "https://example.com/release", "sha256": "a" * 64}}
        with patch.object(self.manager, "check"), patch.object(updates, "download", return_value="b" * 64):
            with self.assertRaisesRegex(ValueError, "SHA-256"):
                self.manager.install("oche")
        self.assertEqual(store.read_state("oche")["active"], "1")
        self.assertFalse(store.release_path("oche", "2").exists())

    def test_missing_feed_explains_release_setup_and_clears_stale_candidates(self):
        self.seed()
        self.manager.available = {"oche": {"version": "2"}}
        error = HTTPError(updates.FEED, 404, "Not Found", {}, None)
        with patch.object(updates, "download", side_effect=error):
            self.manager.lock.acquire()
            self.manager._run("check", None)
        self.assertEqual(self.manager.job["status"], "error")
        self.assertIn("Publish application updates workflow", self.manager.job["message"])
        self.assertEqual(self.manager.available, {})
        self.assertEqual(store.read_state("oche")["active"], "1")

    def test_install_verifies_and_requests_self_restart(self):
        self.seed()
        archive = self.archive([(name, content, tarfile.REGTYPE) for name, content in (
            ("VERSION", b"2"), ("RUNTIME", b"test-runtime"), ("app/server.py", b"pass"))])
        blob = archive.read_bytes()
        digest = hashlib.sha256(blob).hexdigest()
        self.manager.available = {"oche": {"version": "2", "url": "https://example.com/release", "sha256": digest}}
        def download(url, destination, limit):
            destination.write_bytes(blob)
            return digest
        with patch.object(self.manager, "check"), patch.object(updates, "download", side_effect=download):
            self.manager.install("oche")
        state = store.read_state("oche")
        self.assertEqual((state["active"], state["previous"], state["pending"]), ("2", "1", True))
        self.assertTrue((store.DATA / "state/restart-oche").exists())

    def test_process_failure_restores_previous_release(self):
        from app.services import autoglow  # Load the service before patching the namespace.
        self.seed("autoglow")
        store.release_path("autoglow", "2").mkdir(parents=True)
        service = Mock(PORT=8080)
        service.get_status.side_effect = [{"status": "running"}, {"status": "stopped"}]
        with patch("app.services.autoglow", service):
            with self.assertRaisesRegex(RuntimeError, "exited"):
                self.manager.activate("autoglow", "2")
        self.assertEqual(store.read_state("autoglow")["active"], "1")
        self.assertEqual(service.start.call_count, 2)

    def test_manifest_runtime_mismatch_and_https_requirement(self):
        manifest = {"schema": 1, "platforms": {"linux-amd64": {"oche": {
            "version": "2", "runtime": "other", "url": "https://example.com/oche.tar.gz", "sha256": "a" * 64}}}}
        def download(url, destination, limit):
            destination.write_text(json.dumps(manifest))
        with patch.object(updates, "download", side_effect=download), patch.object(updates.platform, "machine", return_value="x86_64"):
            with self.assertRaisesRegex(ValueError, "newer Docker"):
                self.manager.check()
        with self.assertRaises(ValueError):
            updates.https_url("http://example.com/release")

    def test_parallel_updates_and_native_updates_rejected(self):
        with patch.dict(os.environ, {"OCHE_LAUNCHER": ""}):
            with self.assertRaisesRegex(ValueError, "launcher"):
                self.manager.submit("update", "oche")
        with patch.dict(os.environ, {"OCHE_LAUNCHER": "1"}):
            self.manager.lock.acquire()
            with self.assertRaisesRegex(ValueError, "Another"):
                self.manager.submit("check")
            self.manager.lock.release()

    def test_release_checks_allowed_without_launcher(self):
        with patch.dict(os.environ, {"OCHE_LAUNCHER": ""}), \
             patch.object(self.manager, "check") as check, \
             patch.object(updates.threading, "Thread") as thread:
            thread.return_value.start.side_effect = lambda: self.manager._run("check", None)
            self.manager.submit("check")
        check.assert_called_once()
        self.assertEqual(self.manager.job["status"], "complete")

    def test_update_all_skips_current_packages_and_updates_oche_last(self):
        for name in store.MODULES:
            self.seed(name)
        self.manager.available = {name: {"version": "1" if name == "autoglow" else "2"} for name in store.MODULES}
        with patch.object(self.manager, "check") as check, patch.object(self.manager, "install") as install:
            self.manager.lock.acquire()
            self.manager._run("update-all", None)
        check.assert_called_once()
        self.assertEqual([call.args[0] for call in install.call_args_list], ["autodarts", "ochecore", "oche"])
        self.assertTrue(all(call.kwargs == {"refresh": False} for call in install.call_args_list))
        self.assertEqual(self.manager.job["status"], "complete")
        self.assertEqual(self.manager.job["total"], 3)

    def test_update_all_stops_on_failure(self):
        for name in store.MODULES:
            self.seed(name)
        self.manager.available = {name: {"version": "2"} for name in store.MODULES}
        with patch.object(self.manager, "check"), patch.object(self.manager, "install", side_effect=RuntimeError("Health check failed")) as install:
            self.manager.lock.acquire()
            self.manager._run("update-all", None)
        install.assert_called_once_with("autodarts", refresh=False)
        self.assertEqual(self.manager.job["status"], "error")
        self.assertEqual(self.manager.job["module"], "autodarts")

    def test_web_page_and_cross_origin_rejection(self):
        from fastapi.testclient import TestClient
        from app.main import app
        client = TestClient(app)
        self.addCleanup(client.close)
        self.assertIn("Software updates", client.get("/supervisor?service=updates").text)
        self.assertEqual(client.post("/updates/check", headers={"Origin": "https://evil.example"}).status_code, 403)
        self.assertEqual(len(client.get("/updates/status").json()["modules"]), 4)

    def test_launcher_accepts_only_its_own_healthy_boot(self):
        self.run_launcher_health(True)

    def test_launcher_rolls_back_unhealthy_self_update(self):
        self.run_launcher_health(False)

    def run_launcher_health(self, healthy):
        import launcher
        self.seed()
        store.release_path("oche", "2").mkdir(parents=True)
        store.write_state("oche", {"active": "2", "previous": "1", "pending": True, "runtime": store.RUNTIME})
        callbacks = {}
        child = Mock(pid=12345)
        child.poll.return_value = None
        child.wait.return_value = 0
        environment = {}
        def spawn(*args, **kwargs):
            environment.update(kwargs["env"])
            return child
        def response(*args, **kwargs):
            boot_id = environment["OCHE_BOOT_ID"] if healthy else "another-process"
            return io.BytesIO(json.dumps({"ok": True, "boot_id": boot_id}).encode())
        def finish(seconds):
            callbacks[launcher.signal.SIGTERM](launcher.signal.SIGTERM, None)
        with patch.object(store, "initialize"), \
             patch.object(launcher.signal, "signal", side_effect=lambda sig, callback: callbacks.update({sig: callback})), \
             patch.object(launcher.subprocess, "Popen", side_effect=spawn), \
             patch.object(launcher, "urlopen", side_effect=response), \
             patch.object(launcher.time, "sleep", side_effect=finish), \
             patch.object(launcher.time, "monotonic", side_effect=[0, 91]), \
             patch.object(launcher.os, "killpg", create=True):
            launcher.main()
        state = store.read_state("oche")
        self.assertFalse(state["pending"])
        self.assertEqual(state["active"], "2" if healthy else "1")
