"""Local game controls share Oche's origin guards and runtime."""

from fastapi import APIRouter, Request

from app.core.local import LocalDart, LocalOptions

router = APIRouter(prefix="/api/local")


@router.get("")
async def status(request: Request):
    return request.app.state.runtime.local.status()


@router.post("/start")
async def start(options: LocalOptions, request: Request):
    return await request.app.state.runtime.start_local(options)


@router.post("/stop")
async def stop(request: Request):
    return await request.app.state.runtime.stop_local()


@router.post("/throw")
async def throw(dart: LocalDart, request: Request):
    local = request.app.state.runtime.local
    async with local.lock:
        local.throw(dart.segment())
        return local.status()


@router.post("/undo")
async def undo(request: Request):
    local = request.app.state.runtime.local
    async with local.lock:
        local.undo()
        return local.status()


@router.post("/next")
async def next_turn(request: Request):
    local = request.app.state.runtime.local
    async with local.lock:
        local.next_turn()
        return local.status()
