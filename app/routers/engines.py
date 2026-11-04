from fastapi import APIRouter, HTTPException, Request
from pydantic import BaseModel, ConfigDict
from starlette.concurrency import run_in_threadpool

from app.core.integrations import registry
from app.services import engines
from app.services.updates import manager

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
    job = dict(manager.job)
    return {**engines.status(), "installation": job if job.get("action") == "install" else None}


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
async def switch_integration(name: str, switch: Switch, request: Request):
    """Enabling a missing vision engine installs it; disabling stops its engine.

    An integration that feeds games, like Autodarts Play, joins or leaves the runtime.
    """
    def apply(_):
        item = registry.get(name)
        if item is None:
            raise ValueError("Unknown integration")
        if switch.enabled and item.engine and item.process and item.release:
            if not item.process.get_status()["installed"]:
                job = manager.job
                # Repeated requests follow an existing install instead of
                # rejecting the switch or starting a second download.
                if not (job.get("action") == "install" and job.get("module") == name
                        and job.get("status") == "running"):
                    try:
                        manager.submit("install", name)
                    except ValueError as error:
                        raise RuntimeError(str(error)) from error
        registry.set_enabled(name, switch.enabled)
        if not switch.enabled:
            engines.switch_off(name)
        return integration_status()
    result = await _run(apply, name)
    item = registry.get(name)
    runtime = getattr(request.app.state, "runtime", None)
    if item and item.source and runtime and (name in runtime.sources) != switch.enabled:
        await runtime.restart()
    return result


class MaintenanceRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")
    confirmed: bool = False


@router.post("/api/engines/{name}/clear-cache")
async def clear_engine_cache(name: str, payload: MaintenanceRequest):
    if not payload.confirmed:
        raise HTTPException(409, "Confirm resetting all engine settings and calibration.")
    return await _run(lambda engine: engines.maintain(engine, "clear-cache"), name)


@router.post("/api/engines/{name}/uninstall")
async def uninstall_engine(name: str, payload: MaintenanceRequest):
    if not payload.confirmed:
        raise HTTPException(409, "Confirm uninstalling the engine and its saved releases.")
    return await _run(lambda engine: engines.maintain(engine, "uninstall"), name)
