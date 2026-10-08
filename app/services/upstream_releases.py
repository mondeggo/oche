"""Discover and prepare releases from each application's official publisher."""
from email.parser import BytesParser
import json
import os
from pathlib import Path
import re
import shutil
import subprocess
import sys
import tempfile
import uuid
import zipfile

CORE_FEED = "https://api.github.com/repos/mondeggo/oche-core/releases/latest"
AD_FEED = "https://releases.autodarts.com/headless/downloads/latest.stable.json"
AD_BASE = "https://releases.autodarts.com/headless/downloads/"


def discover_core(download, architecture):
    with tempfile.TemporaryDirectory(prefix="oche-core-release-") as folder:
        path = Path(folder) / "release.json"
        download(CORE_FEED, path, 1024 * 1024)
        data = json.loads(path.read_text())
        tag = data["tag_name"]
        if data.get("draft") or data.get("prerelease") or not re.fullmatch(r"v\d+\.\d+\.\d+", tag):
            raise ValueError("OcheCore did not publish a stable version")
        version = tag[1:]
        filename = f"ochecore-{version}-py3-none-any.whl"
        asset = next((a for a in data["assets"] if a["name"] == filename), None)
        if not asset:
            raise ValueError("OcheCore release is missing its Python wheel")
        digest = asset.get("digest", "") or ""
        if not re.fullmatch(r"sha256:[a-f0-9]{64}", digest):
            sums = next((a for a in data["assets"] if a["name"] == "SHA256SUMS"), None)
            if not sums:
                raise ValueError("OcheCore release is missing a SHA-256 checksum")
            download(sums["browser_download_url"], path, 1024 * 1024)
            matches = [line.split()[0] for line in path.read_text().splitlines()
                       if len(line.split()) == 2 and line.split()[1].lstrip("*") == filename]
            if len(matches) != 1 or not re.fullmatch(r"[a-f0-9]{64}", matches[0]):
                raise ValueError("OcheCore wheel checksum is missing or invalid")
            digest = "sha256:" + matches[0]
        return {"version": version, "display_version": version, "format": "ochecore-wheel",
                "url": asset["browser_download_url"], "filename": filename,
                "sha256": digest[7:], "source": "OcheCore GitHub releases"}


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
        return {"version": version, "display_version": version, "format": "autodarts-archive",
                "url": asset["url"], "sha256": digest, "source": "AutoDarts official stable releases",
                "verification": verification}


def run(command):
    result = subprocess.run(command, capture_output=True, text=True, timeout=600,
                            env={**os.environ, "UV_PYTHON_DOWNLOADS": "never", "UV_NO_CONFIG": "1"})
    if result.returncode:
        raise RuntimeError("Release environment validation failed: " + (result.stderr or result.stdout)[-2500:])
    return result.stdout


def prepare_core(wheel, content, version):
    # Inspect metadata without importing uninstalled application code.
    with zipfile.ZipFile(wheel) as archive:
        names = [n for n in archive.namelist() if n.endswith(".dist-info/METADATA")]
        if len(names) != 1 or archive.getinfo(names[0]).file_size > 1024 * 1024:
            raise ValueError("Invalid OcheCore wheel metadata")
        metadata = BytesParser().parsebytes(archive.read(names[0]))
        if metadata["Name"] != "ochecore" or metadata["Version"] != version:
            raise ValueError("OcheCore wheel identity differs from the release")
    environment = content / ".venv"
    python = environment / ("Scripts/python.exe" if os.name == "nt" else "bin/python")
    run(["uv", "venv", "--relocatable", "--python", sys.executable, str(environment)])
    # Wheels only: dependency incompatibility fails before touching the current
    # installation. No source build scripts or shared-environment changes.
    run(["uv", "pip", "install", "--python", str(python), "--only-binary", ":all:",
         "--index-url", "https://pypi.org/simple", str(wheel)])
    run([str(python), "-I", "-c",
         "import importlib.metadata, importlib.resources, ochecore.main; "
         f"assert importlib.metadata.version('ochecore') == {version!r}; "
         "assert importlib.resources.files('ochecore').joinpath('static/index.html').is_file()"])
    (content / "INSTALL_KIND").write_text("wheel")
    (content / "VERSION").write_text(version)


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
