"""Find and unpack Autodarts releases from the official Autodarts release server."""

import json
import re
import tempfile
from pathlib import Path

from app.services.release_metadata import flatten_single_folder

AD_FEED = "https://releases.autodarts.com/headless/downloads/latest.stable.json"
AD_BASE = "https://releases.autodarts.com/headless/downloads/"


def discover_autodarts(download, architecture):
    with tempfile.TemporaryDirectory(prefix="oche-autodarts-release-") as folder:
        path = Path(folder) / "release.json"
        download(AD_FEED, path, 1024 * 1024)
        entry = json.loads(path.read_text())["platforms"]["linux-" + architecture]
        version = entry["version"]
        if not re.fullmatch(r"2\.\d+\.\d+", version):
            raise ValueError("Unsupported AutoDarts stable version")
        filename = f"autodarts_{version}_linux-{architecture}.tar.gz"
        asset = next((f for f in entry["files"] if f["name"] == filename and f["kind"] == "archive"), None)
        if not asset or asset["url"] != AD_BASE + filename:
            raise ValueError("AutoDarts archive is not on its official release server")
        digest = asset.get("sha256")
        if not digest:
            # The official feed currently has no checksums. Bind discovery to
            # bytes delivered by official HTTPS; never invent a publisher hash.
            digest = download(asset["url"], path, 128 * 1024 * 1024)
            if path.stat().st_size != asset["size"]:
                raise ValueError("AutoDarts archive size differs from its release metadata")
            verification = "SHA-256 pinned from official HTTPS download"
        else:
            verification = "Publisher SHA-256"
        if not re.fullmatch(r"[a-f0-9]{64}", digest):
            raise ValueError("Invalid AutoDarts SHA-256")
        return {
            "version": version,
            "display_version": version,
            "format": "autodarts-archive",
            "url": asset["url"],
            "sha256": digest,
            "source": "AutoDarts official stable releases",
            "verification": verification,
        }


def prepare_autodarts(archive, content, version, extract):
    extract(archive, content)
    flatten_single_folder(content, "autodarts", "AutoDarts")
    (content / "autodarts").chmod(0o755)
    (content / "VERSION").write_text(version)
