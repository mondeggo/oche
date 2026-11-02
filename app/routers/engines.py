from fastapi import APIRouter, HTTPException
from starlette.concurrency import run_in_threadpool

from app.services import engines

router = APIRouter(prefix="/api/engines", tags=["engines"])


@router.get("")
def engine_status():
    return engines.status()


async def _run(action, name):
    try:
        return await run_in_threadpool(action, name)
    except ValueError as error:
        raise HTTPException(status_code=404, detail=str(error)) from error
    except RuntimeError as error:
        raise HTTPException(status_code=409, detail=str(error)) from error


@router.post("/{name}/use")
async def use_engine(name: str):
    return await _run(engines.use, name)


@router.post("/{name}/stop")
async def stop_engine(name: str):
    return await _run(engines.stop, name)
