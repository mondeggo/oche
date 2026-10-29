from fastapi import APIRouter, Request

from app.config import load_config, save_config
from app.services import system_metrics
from app.services.metrics_history import HistoryConfig

router = APIRouter(prefix="/system", tags=["system"])


@router.get("/status")
async def status():
    return system_metrics.get_status()


@router.get("/history")
async def history(request: Request):
    return request.app.state.metrics_history.status()


@router.put("/history")
async def configure_history(config: HistoryConfig, request: Request):
    saved = load_config()
    saved["metrics_history"] = config.model_dump()
    save_config(saved)
    request.app.state.metrics_history.configure(config)
    return request.app.state.metrics_history.status()
