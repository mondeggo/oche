"""Oche's camera tools use only the managed Autodarts instance."""

from fastapi import APIRouter, HTTPException, Request
from fastapi.responses import Response
from pydantic import BaseModel, ConfigDict, Field, StrictInt

from app.services import cameras
from app.templating import templates

router = APIRouter(prefix='/autodarts/cameras', tags=['cameras'])


class CameraSettings(BaseModel):
    model_config = ConfigDict(extra='forbid')
    revision: str = Field(min_length=1, max_length=128)
    devices: list[str] = Field(min_length=3, max_length=3)
    width: StrictInt = Field(ge=16, le=8192)
    height: StrictInt = Field(ge=16, le=8192)
    fps: StrictInt = Field(ge=1, le=240)


@router.get('')
async def page(request: Request):
    return templates.TemplateResponse('cameras.html', {'request': request})


@router.get('/data')
async def data():
    try:
        return await cameras.get_data()
    except cameras.CameraError as error:
        raise HTTPException(status_code=error.status_code, detail=error.detail) from error


@router.patch('/settings')
async def settings(payload: CameraSettings):
    try:
        return await cameras.apply_settings(payload.model_dump())
    except cameras.CameraError as error:
        raise HTTPException(status_code=error.status_code, detail=error.detail) from error


@router.get('/frame/{index}')
async def frame(index: int):
    if index not in range(3):
        raise HTTPException(status_code=404, detail='Camera not found.')
    try:
        body, content_type = await cameras.get_frame(index)
        return Response(body, media_type=content_type, headers={'Cache-Control': 'no-store', 'X-Content-Type-Options': 'nosniff'})
    except cameras.CameraError as error:
        raise HTTPException(status_code=error.status_code, detail=error.detail) from error
