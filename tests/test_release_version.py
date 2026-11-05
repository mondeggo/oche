import subprocess
import tomllib
from pathlib import Path

import pytest

from app import __version__
from scripts import release_version


@pytest.mark.parametrize("version", ["3.0.0", "3.0.0a1", "3.1.0b2", "10.2.3rc11"])
def test_release_versions_accept_final_and_pre_releases(version):
    assert release_version.check(version) == version


@pytest.mark.parametrize("version", ["3.0", "v3.0.0", "3.0.0-a1", "03.0.0", "3.0.0a", "3.0.0.dev1"])
def test_release_versions_reject_other_forms(version):
    with pytest.raises(ValueError):
        release_version.check(version)


def test_current_project_version_is_publishable():
    version, changed = release_version.detect(None)
    assert release_version.check(version) == version
    assert changed is True


def test_runtime_version_matches_project():
    project = tomllib.loads(Path("pyproject.toml").read_text(encoding="utf-8"))
    assert __version__ == project["project"]["version"]


def test_unchanged_version_is_not_published_again(tmp_path, monkeypatch):
    project = '[project]\nname = "oche"\nversion = "{}"\n'
    git = ["git", "-c", "user.email=ci@oche.test", "-c", "user.name=CI"]
    monkeypatch.chdir(tmp_path)
    subprocess.run(["git", "init", "-q"], check=True)
    (tmp_path / "pyproject.toml").write_text(project.format("3.0.0a1"))
    subprocess.run([*git, "add", "pyproject.toml"], check=True)
    subprocess.run([*git, "commit", "-qm", "one"], check=True)
    before = subprocess.check_output(["git", "rev-parse", "HEAD"], text=True).strip()
    assert release_version.detect(before) == ("3.0.0a1", False)
    assert release_version.detect(before, manual=True) == ("3.0.0a1", True)
    (tmp_path / "pyproject.toml").write_text(project.format("3.0.0a2"))
    assert release_version.detect(before) == ("3.0.0a2", True)
