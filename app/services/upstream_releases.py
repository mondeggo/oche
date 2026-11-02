"""Discover and prepare releases from each application's official publisher."""

import json
from pathlib import Path
import re
import shutil
import tempfile
import uuid

AD_FEED = "https://releases.autodarts.com/headless/downloads/latest.stable.json"
AD_BASE = "https://releases.autodarts.com/headless/downloads/"
OC_FEED = "https://api.github.com/repos/mondeggo/ochecore/releases/latest"
OC_BASE = "https://github.com/mondeggo/ochecore/releases/download/"


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


def _flatten(content, executable, label):
    """Move the archive's single top folder up, so the executable sits in content."""
    if (content / executable).is_file():
        return
    roots = list(content.iterdir())
    if len(roots) != 1 or not roots[0].is_dir() or not (roots[0] / executable).is_file():
        raise ValueError(f"{label} archive is missing its executable")
    # The wrapper directory may itself have the executable's name. Move it
    # aside before flattening.
    root = content.parent / ("unpacked-" + uuid.uuid4().hex)
    roots[0].rename(root)
    for child in root.iterdir():
        shutil.move(str(child), str(content / child.name))
    root.rmdir()


def prepare_autodarts(archive, content, version, extract):
    extract(archive, content)
    _flatten(content, "autodarts", "AutoDarts")
    (content / "autodarts").chmod(0o755)
    (content / "VERSION").write_text(version)


def prepare_ochecore(archive, content, version, extract):
    extract(archive, content)
    _flatten(content, "oche-core", "OcheCore")
    (content / "oche-core").chmod(0o755)
    (content / "VERSION").write_text(version)
