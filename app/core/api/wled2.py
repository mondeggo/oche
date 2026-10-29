"""Lighting studio controls on the shared Oche API."""

from fastapi import APIRouter, Request, Response

from app.core.api.http import check_revision, configuration_revision
from app.core.integrations.wled.service import DeviceAddress, Matrix, Power, Preview
from app.core.integrations.wled2.service import Corners, Layout, Rules, SimulationFrame

router = APIRouter(prefix="/api/wled2")


@router.get("")
async def configuration(request: Request, response: Response):
    runtime = request.app.state.runtime
    async with runtime.lock:
        response.headers["ETag"] = configuration_revision(runtime, request)
        return runtime.wled2.config


@router.put("")
async def configure(layout: Layout, request: Request, response: Response):
    runtime = request.app.state.runtime
    async with runtime.lock:
        check_revision(request, runtime)
        await runtime.configure_wled2(layout)
        response.headers["ETag"] = configuration_revision(runtime, request)
        return runtime.wled2.config


@router.get("/status")
async def status(request: Request):
    wled2 = request.app.state.runtime.wled2
    await wled2.check_connections()
    return wled2.status()


@router.get("/defaults")
async def defaults():
    return {"rules": Rules(), "matrix": Matrix(width=16, height=16)}


@router.post("/discover")
async def discover(request: Request):
    return await request.app.state.runtime.wled2.engine.discover()


@router.post("/probe")
async def probe(address: DeviceAddress, request: Request):
    return await request.app.state.runtime.wled2.engine.probe_address(address)


@router.post("/preview")
async def preview(value: Preview, request: Request):
    runtime = request.app.state.runtime
    async with runtime.lock:
        return await runtime.wled2.preview(value)


@router.post("/controllers/{controller_id}/power")
async def power(controller_id: str, value: Power, request: Request):
    runtime = request.app.state.runtime
    async with runtime.lock:
        return await runtime.wled2.engine.power(value.on, controller_id)


@router.post("/controllers/{controller_id}/corners")
async def corners(controller_id: str, value: Corners, request: Request):
    """Show or hide a matrix panel's corner colours until the toggle changes again."""
    runtime = request.app.state.runtime
    async with runtime.lock:
        return await runtime.wled2.show_corners(controller_id, value.target_id, value.on)


@router.post("/simulate")
async def simulate(value: SimulationFrame, request: Request):
    runtime = request.app.state.runtime
    async with runtime.lock:
        return await runtime.wled2.simulate(value)
