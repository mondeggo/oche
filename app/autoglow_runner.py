import asyncio
import importlib
import os
from pathlib import Path
import shutil
import sys

from app.config import DATA_DIR


def main():
    source = Path(os.environ.get("OCHE_AUTOGLOW_SOURCE", "/opt/autoglow"))
    data = DATA_DIR / "autoglow"
    data.mkdir(parents=True, exist_ok=True)
    sys.path.insert(0, str(source))
    web = importlib.import_module("web_server")
    web.PROJECT_DIR = web.BASE_DIR = str(data)
    web.CONFIG_FILE = str(data / "config.json")
    web.PROFILES_DIR = str(data / "profiles")
    if sys.argv[1] == "web":
        # Seed bundled profiles once; never overwrite user edits on restart/update.
        for profile in (source / "profiles").rglob("*.json"):
            relative = profile.relative_to(source / "profiles")
            if len(relative.parts) == 1:
                relative = Path("ws281x") / relative
            target = data / "profiles" / relative
            if not target.exists():
                target.parent.mkdir(parents=True, exist_ok=True)
                shutil.copy2(profile, target)
        web.init_config()
        sys.argv = [sys.argv[0], "--port", os.environ.get("OCHE_AUTOGLOW_PORT", "8080")]
        web.main()
    else:
        if not (data / "config.json").exists():
            web.init_config()
        listener = importlib.import_module("autodarts_wled_mini")
        listener.PROJECT_DIR = str(data)
        listener.CONFIG_FILE = web.CONFIG_FILE
        asyncio.run(listener.autodarts_logger())


if __name__ == "__main__":
    main()
