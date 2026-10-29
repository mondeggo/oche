"""Discover and prepare releases from each application's official publisher."""

import json
from pathlib import Path
import re
import shutil
import tempfile
import uuid

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
    if not (content / "autodarts").is_file():
        roots = list(content.iterdir())
        if len(roots) != 1 or not roots[0].is_dir() or not (roots[0] / "autodarts").is_file():
            raise ValueError("AutoDarts archive is missing its executable")
        # The wrapper directory may itself be named autodarts, the same name
        # as the executable it contains. Move it aside before flattening.
        root = content.parent / ("unpacked-" + uuid.uuid4().hex)
        roots[0].rename(root)
        for child in root.iterdir():
            shutil.move(str(child), str(content / child.name))
        root.rmdir()
    (content / "autodarts").chmod(0o755)
    (content / "VERSION").write_text(version)
