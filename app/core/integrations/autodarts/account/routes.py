"""The Autodarts account API: connection settings, boards, sign-in and sign-out."""

from fastapi import APIRouter, HTTPException, Request

from app.core.storage import write_private_json

from .config import ConnectionConfig, load_connection, locked_fields

router = APIRouter(prefix="/api/autodarts", tags=["autodarts"])


def account(runtime):
    source = runtime.sources.get("autodarts")
    if source is None:
        raise HTTPException(409, "Switch on the Autodarts integration to use your account.")
    return source


@router.get("/config")
async def config(request: Request):
    runtime = request.app.state.runtime
    return {
        **account(runtime).config.model_dump(),
        "api_base_url": runtime.settings.api_base_url,
        "locked_fields": locked_fields(runtime.settings),
    }


@router.put("/config")
async def configure(config: ConnectionConfig, request: Request):
    runtime = request.app.state.runtime
    current = account(runtime)
    configured = load_connection(runtime.settings)
    for key in locked_fields(runtime.settings):
        if getattr(config, key) != getattr(configured, key):
            raise HTTPException(409, f"{key} is managed by .env or config/config.yaml. Edit that file.")

    async def save():
        write_private_json(runtime.settings.data_dir / "connection.json", config.model_dump())
        if config.client_id != current.config.client_id:
            await current.auth.forget()

    await runtime.restart(save)
    return {"saved": True}


@router.get("/boards")
async def boards(request: Request):
    runtime = request.app.state.runtime
    async with runtime.lock:
        current = account(runtime)
        return {"boards": await current.cloud.list_boards(), "selected_board_id": current.config.board_id or None}


@router.post("/login")
async def login(request: Request):
    runtime = request.app.state.runtime
    async with runtime.lock:
        return await account(runtime).auth.start_login()


@router.post("/logout")
async def logout(request: Request):
    runtime = request.app.state.runtime
    current = account(runtime)
    await runtime.restart(current.auth.forget)
    return {"disconnected": True}
