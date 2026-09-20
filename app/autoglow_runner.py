"""Adapt the bundled AutoGlow server to Oche's persistent storage and lifecycle."""
import asyncio
from contextlib import suppress
import importlib
import importlib.util
import json
import os
from pathlib import Path
import shutil
import sys

from app.config import DATA_DIR


def prepare_server(source: Path, data: Path):
    source, data = source.resolve(), data.resolve()
    data.mkdir(parents=True, exist_ok=True)
    config = data / "config.json"
    # Preserve the pre-upgrade configuration before upstream normalizes its schema.
    original = data / "config.pre-oche-upgrade.json"
    if config.exists() and not original.exists():
        shutil.copy2(config, original)
    for bundled in (source / "presets").glob("*.json"):
        target = data / "presets" / bundled.name
        target.parent.mkdir(parents=True, exist_ok=True)
        if not target.exists():
            shutil.copy2(bundled, target)
    flow = data / "game_flows.json"
    if not flow.exists() and (source / flow.name).exists():
        shutil.copy2(source / flow.name, flow)

    os.chdir(data)  # Upstream game flows use paths relative to the working directory.
    sys.path.insert(0, str(source))
    os.environ["AUTOGLOW_CONFIG"] = str(config)
    os.environ["AUTOGLOW_PORT"] = os.environ.get("OCHE_AUTOGLOW_PORT", "8080")
    storage = importlib.import_module("core.storage")
    storage.PROJECT_ROOT = data
    storage.DEFAULT_CONFIG_PATH = str(config)
    storage.BACKUPS_CONFIG_DIR = data / "backups" / "configs"
    storage.PRESETS_DIR = data / "presets"
    backup = importlib.import_module("core.backup_manager")
    backup.PROJECT_ROOT = data
    backup.BACKUP_DIR = data / "backups"

    spec = importlib.util.spec_from_file_location("oche_autoglow_server", source / "server.py")
    server = importlib.util.module_from_spec(spec)
    # Upstream opens its rotating log during import, beside __file__.
    # Load the bundled code with a writable runtime location, then restore assets.
    server.__file__ = str(data / "server.py")
    sys.modules[spec.name] = server
    spec.loader.exec_module(server)
    server.WEB_DIR = str(source / "web")
    return server


async def publish_status(app, path):
    while True:
        engine = app.get("engine")
        client = engine.autodarts if engine else None
        temporary = path.with_suffix(".tmp")
        temporary.write_text(json.dumps({"online": bool(client and client.is_connected)}))
        temporary.replace(path)
        await asyncio.sleep(1)


def create_app(server, data: Path):
    from aiohttp import web

    app = server.create_app()

    @web.middleware
    async def image_updates_only(request, handler):
        if request.path in {"/api/system/update_check", "/api/system/update_apply", "/api/system/update_zip"}:
            return web.json_response({"status": "error", "message": "Update AutoGlow by updating the Oche Docker image."}, status=409)
        return await handler(request)

    app.middlewares.insert(0, image_updates_only)

    async def status_lifecycle(app):
        path = data / ".sync_status.json"
        task = asyncio.create_task(publish_status(app, path))
        try:
            yield
        finally:
            task.cancel()
            with suppress(asyncio.CancelledError):
                await task
            path.unlink(missing_ok=True)

    app.cleanup_ctx.append(status_lifecycle)
    return app


def main():
    from aiohttp import web

    source = Path(os.environ.get("OCHE_AUTOGLOW_SOURCE", "/opt/autoglow"))
    data = (DATA_DIR / "autoglow").resolve()
    server = prepare_server(source, data)
    web.run_app(create_app(server, data), host="0.0.0.0", port=server.PORT)


if __name__ == "__main__":
    main()
