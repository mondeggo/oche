"""Find and unpack OcheCore releases from its GitHub releases."""

import json
import re
import tempfile
from pathlib import Path

from app.services.release_metadata import flatten_single_folder

OC_FEED = "https://api.github.com/repos/mondeggo/ochecore/releases/latest"
OC_BASE = "https://github.com/mondeggo/ochecore/releases/download/"


def discover_ochecore(download, architecture):
    with tempfile.TemporaryDirectory(prefix="oche-ochecore-release-") as folder:
        path = Path(folder) / "release.json"
        download(OC_FEED, path, 1024 * 1024)
        release = json.loads(path.read_text())
        if release.get("draft") or release.get("prerelease"):
            raise ValueError("The latest OcheCore release is not a stable release")
        match = re.fullmatch(r"v(\d+\.\d+\.\d+)", release.get("tag_name", ""))
        if not match:
            raise ValueError("Unsupported OcheCore release version")
        version = match[1]
        filename = f"ochecore-v{version}-linux-{architecture}.tar.gz"
        assets = {asset["name"]: asset["browser_download_url"] for asset in release.get("assets", [])}
        expected = f"{OC_BASE}v{version}/"
        for name in (filename, filename + ".sha256"):
            # GitHub spells the repository as it was named, so compare without case.
            if not assets.get(name, "").lower().startswith(expected):
                raise ValueError("OcheCore archive is not on its official release page")
        download(assets[filename + ".sha256"], path, 4096)
        digest, _, listed = path.read_text().strip().partition(" ")
        if not re.fullmatch(r"[a-f0-9]{64}", digest) or listed.strip().lstrip("*") != filename:
            raise ValueError("Invalid OcheCore SHA-256")
        return {
            "version": version,
            "display_version": version,
            "format": "ochecore-archive",
            "url": assets[filename],
            "sha256": digest,
            "source": "OcheCore GitHub releases",
            "verification": "Publisher SHA-256",
        }


def prepare_ochecore(archive, content, version, extract):
    extract(archive, content)
    flatten_single_folder(content, "oche-core", "OcheCore")
    (content / "oche-core").chmod(0o755)
    (content / "VERSION").write_text(version)
