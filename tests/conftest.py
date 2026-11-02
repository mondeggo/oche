import pytest

from app import config


@pytest.fixture(autouse=True)
def isolated_settings(tmp_path, monkeypatch):
    """Keep every test away from the real data/settings.json."""
    monkeypatch.setattr(config, "CONFIG_FILE", tmp_path / "settings.json")
