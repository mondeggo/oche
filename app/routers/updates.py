from fastapi import APIRouter, HTTPException
from app.services.updates import manager

router = APIRouter(prefix="/updates", tags=["updates"])


@router.get("/status")
def status():
    return manager.status()


@router.post("/check", status_code=202)
def check():
    return submit("check")


@router.post("/{name}/{action}", status_code=202)
def submit(action: str, name: str = None):
    try:
        manager.submit(action, name)
    except ValueError as error:
        raise HTTPException(status_code=409, detail=str(error)) from error
    return {"accepted": True}


@router.post("/update-all", status_code=202)
def update_all():
    return submit("update-all")
