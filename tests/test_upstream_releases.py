import hashlib
import io
import json
from pathlib import Path
import tarfile
import tempfile
import unittest
from unittest.mock import patch
import zipfile

from app.services import upstream_releases as upstream
from app.services.updates import extract


class UpstreamReleaseTests(unittest.TestCase):
    def setUp(self):
        temporary = tempfile.TemporaryDirectory()
        self.addCleanup(temporary.cleanup)
        self.root = Path(temporary.name)

    def test_core_selects_wheel_and_publisher_digest(self):
        release = {"tag_name": "v0.1.3", "assets": [
            {"name": "ochecore-linux-amd64.tar.gz", "digest": "sha256:" + "b" * 64},
            {"name": "ochecore-0.1.3-py3-none-any.whl", "digest": "sha256:" + "a" * 64,
             "browser_download_url": "https://github.com/mondeggo/oche-core/releases/download/v0.1.3/ochecore-0.1.3-py3-none-any.whl"}]}
        def download(url, path, limit):
            path.write_text(json.dumps(release))
        result = upstream.discover_core(download, "amd64")
        self.assertEqual(result["format"], "ochecore-wheel")
        self.assertEqual(result["version"], "0.1.3")
        self.assertEqual(result["sha256"], "a" * 64)
        release["assets"][1].pop("digest")
        with self.assertRaisesRegex(ValueError, "checksum"):
            upstream.discover_core(download, "amd64")

    def test_core_checksum_file_fallback(self):
        filename = "ochecore-0.1.3-py3-none-any.whl"
        release = {"tag_name": "v0.1.3", "assets": [
            {"name": filename, "browser_download_url": "https://example.com/" + filename},
            {"name": "SHA256SUMS", "browser_download_url": "https://example.com/SHA256SUMS"}]}
        def download(url, path, limit):
            path.write_text("a" * 64 + "  " + filename if url.endswith("SHA256SUMS") else json.dumps(release))
        self.assertEqual(upstream.discover_core(download, "arm64")["sha256"], "a" * 64)

    def test_autodarts_pins_official_archive_and_checks_size(self):
        name = "autodarts_2.0.2_linux-amd64.tar.gz"
        blob = b"official archive bytes"
        asset = {"name": name, "kind": "archive", "url": upstream.AD_BASE + name, "size": len(blob)}
        release = {"platforms": {"linux-amd64": {"version": "2.0.2", "files": [asset]}}}
        def download(url, path, limit):
            path.write_bytes(json.dumps(release).encode() if url == upstream.AD_FEED else blob)
            return hashlib.sha256(path.read_bytes()).hexdigest()
        result = upstream.discover_autodarts(download, "amd64")
        self.assertEqual(result["sha256"], hashlib.sha256(blob).hexdigest())
        self.assertIn("pinned", result["verification"])
        asset["size"] += 1
        with self.assertRaisesRegex(ValueError, "size"):
            upstream.discover_autodarts(download, "amd64")
        asset["url"] = "https://example.com/" + name
        with self.assertRaisesRegex(ValueError, "official"):
            upstream.discover_autodarts(download, "amd64")

    def test_autodarts_flattens_same_named_wrapper(self):
        archive = self.root / "release.tar.gz"
        with tarfile.open(archive, "w:gz") as output:
            info = tarfile.TarInfo("autodarts/autodarts")
            info.size = 6
            output.addfile(info, io.BytesIO(b"binary"))
        content = self.root / "content"
        content.mkdir()
        upstream.prepare_autodarts(archive, content, "2.0.2", extract)
        self.assertEqual((content / "autodarts").read_bytes(), b"binary")
        self.assertEqual((content / "VERSION").read_text(), "2.0.2")

    def test_wheel_identity_checked_before_installation(self):
        wheel = self.root / "ochecore-0.1.3-py3-none-any.whl"
        with zipfile.ZipFile(wheel, "w") as output:
            output.writestr("ochecore-0.1.3.dist-info/METADATA", "Name: other\nVersion: 0.1.3\n")
        with patch.object(upstream, "run") as run:
            with self.assertRaisesRegex(ValueError, "identity"):
                upstream.prepare_core(wheel, self.root, "0.1.3")
            run.assert_not_called()
