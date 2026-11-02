from fastapi import APIRouter, HTTPException
from pydantic import BaseModel, ConfigDict
from starlette.concurrency import run_in_threadpool

from app.core.integrations import registry
from app.services import engines

router = APIRouter(tags=["integrations"])


class Switch(BaseModel):
    model_config = ConfigDict(extra="forbid")
    enabled: bool


async def _run(action, name):
    try:
        return await run_in_threadpool(action, name)
    except ValueError as error:
        raise HTTPException(status_code=404, detail=str(error)) from error
    except RuntimeError as error:
        raise HTTPException(status_code=409, detail=str(error)) from error


@router.get("/api/engines")
def engine_status():
    return engines.status()


@router.post("/api/engines/{name}/use")
async def use_engine(name: str):
    return await _run(engines.use, name)


@router.post("/api/engines/{name}/stop")
async def stop_engine(name: str):
    return await _run(engines.stop, name)


def integration_status() -> list[dict]:
    return [
        {"id": item.id, "name": item.name, "description": item.description, "enabled": item.enabled}
        for item in registry.integrations()
    ]


@router.get("/api/integrations")
def list_integrations():
    return integration_status()


@router.put("/api/integrations/{name}")
async def switch_integration(name: str, switch: Switch):
    """Switching an integration on only shows it; switching it off stops its engine."""
    def apply(_):
        registry.set_enabled(name, switch.enabled)
        if not switch.enabled:
            engines.switch_off(name)
        return integration_status()
    return await _run(apply, name)
