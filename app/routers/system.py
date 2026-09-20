from fastapi import APIRouter

from app.services import system_metrics

router = APIRouter(prefix="/system", tags=["system"])


@router.get("/status")
async def status():
    return system_metrics.get_status()
