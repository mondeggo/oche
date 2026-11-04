"""Tournament controls use the same local-game lock and origin guards."""

from fastapi import APIRouter, Request

from app.core.tournaments import TournamentOptions

router = APIRouter(prefix="/api/tournament")


@router.get("")
async def status(request: Request):
    runtime = request.app.state.runtime
    async with runtime.local.lock:
        return runtime.tournaments.status()


@router.get("/history")
async def history(request: Request):
    runtime = request.app.state.runtime
    async with runtime.local.lock:
        return runtime.tournaments.listing()


@router.get("/history/{tournament_id}")
async def detail(tournament_id: str, request: Request):
    runtime = request.app.state.runtime
    async with runtime.local.lock:
        return runtime.tournaments.status(tournament_id)


@router.post("")
async def create(options: TournamentOptions, request: Request):
    runtime = request.app.state.runtime
    async with runtime.lock, runtime.local.lock:
        runtime.tournaments.sync()
        game, saved = runtime.local.saved, runtime.tournaments.saved
        if (
            runtime.local.active
            and saved
            and saved.state != "active"
            and game.tournament_id == saved.id
            and game.frame.winner >= 0
        ):
            runtime.local.stop()
            runtime.start_sources()
        runtime.tournaments.create(options)
        return runtime.tournaments.status()


@router.post("/matches/{match_id}/start")
async def start(match_id: str, request: Request):
    return await request.app.state.runtime.start_tournament_match(match_id)


@router.post("/cancel")
async def cancel(request: Request):
    return await request.app.state.runtime.cancel_tournament()
