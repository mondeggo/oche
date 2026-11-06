"""Local game controls share Oche's origin guards and runtime."""

from fastapi import APIRouter, Request

from app.core.local import LocalDart, LocalOptions
from app.services import engines

router = APIRouter(prefix="/api/local")


@router.get("")
async def status(request: Request):
    runtime = request.app.state.runtime
    # Before a game, Play shows whether the chosen vision engine is detecting, as the sidebar does.
    await runtime.detection.check(engines.process_status())
    async with runtime.local.lock:
        return {**runtime.local_status(), "readiness": runtime.detection.status()}


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
        local.throw(dart.segment(), coords=dart.coordinates())
        return request.app.state.runtime.local_status()


@router.post("/undo")
async def undo(request: Request):
    local = request.app.state.runtime.local
    async with local.lock:
        local.undo()
        return request.app.state.runtime.local_status()


@router.post("/next")
async def next_turn(request: Request):
    local = request.app.state.runtime.local
    async with local.lock:
        local.next_turn()
        return request.app.state.runtime.local_status()


@router.post("/football/confirm")
async def confirm_football(dart: LocalDart, request: Request):
    local = request.app.state.runtime.local
    async with local.lock:
        local.confirm_football(dart)
        return request.app.state.runtime.local_status()
