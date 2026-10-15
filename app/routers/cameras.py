"""Oche's camera tools use only the managed Autodarts instance."""

from fastapi import APIRouter, HTTPException, Request
from fastapi.responses import Response
import asyncio
from typing import Annotated, Literal

from fastapi import Path
from pydantic import BaseModel, ConfigDict, Field, StrictInt, model_validator

from app.services import cameras
from app.services import camera_controls, camera_vision
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


CameraIndex = Annotated[int, Path(ge=0, le=2)]


class ControlValues(BaseModel):
    model_config = ConfigDict(extra='forbid')
    device: str = Field(min_length=1, max_length=4096)
    values: dict[str, StrictInt] = Field(default_factory=dict, max_length=32)


class ProfileRequest(BaseModel):
    model_config = ConfigDict(extra='forbid')
    device: str = Field(min_length=1, max_length=4096)
    name: str = Field(min_length=1, max_length=64, pattern=r'^[\w .-]+$')


class FocusROI(BaseModel):
    model_config = ConfigDict(extra='forbid', allow_inf_nan=False)
    x: float = Field(ge=0, le=1)
    y: float = Field(ge=0, le=1)
    width: float = Field(gt=0, le=1)
    height: float = Field(gt=0, le=1)

    @model_validator(mode='after')
    def inside_image(self):
        if self.x + self.width > 1.000001 or self.y + self.height > 1.000001:
            raise ValueError('The selected area must stay inside the image.')
        return self


class CalibrationRequest(BaseModel):
    model_config = ConfigDict(extra='forbid')
    device: str = Field(min_length=1, max_length=4096)
    roi: FocusROI


async def _control_call(awaitable):
    try:
        return await awaitable
    except cameras.CameraError as error:
        raise HTTPException(status_code=error.status_code, detail=error.detail) from error


@router.get('/controls/{index}')
async def controls(index: CameraIndex):
    return await _control_call(camera_controls.get_controls(index))


@router.patch('/controls/{index}')
async def set_controls(index: CameraIndex, payload: ControlValues):
    return await _control_call(camera_controls.update(index, payload.device, payload.values))


@router.post('/controls/{index}/defaults')
async def control_defaults(index: CameraIndex, payload: ControlValues):
    return await _control_call(camera_controls.update(index, payload.device, defaults=True))


@router.post('/controls/recover')
async def control_recovery():
    return await _control_call(camera_controls.recover())


@router.get('/profiles')
async def profiles():
    try:
        return camera_controls.profiles()
    except cameras.CameraError as error:
        raise HTTPException(status_code=error.status_code, detail=error.detail) from error


@router.post('/profiles/{index}')
async def save_profile(index: CameraIndex, payload: ProfileRequest):
    return await _control_call(camera_controls.save_profile(index, payload.device, payload.name))


@router.post('/profiles/{index}/apply')
async def apply_profile(index: CameraIndex, payload: ProfileRequest):
    return await _control_call(camera_controls.apply_profile(index, payload.device, payload.name))


@router.delete('/profiles/{name}')
async def delete_profile(name: str):
    try:
        return camera_controls.delete_profile(name)
    except cameras.CameraError as error:
        raise HTTPException(status_code=error.status_code, detail=error.detail) from error


@router.get('/target/{index}')
async def target(index: CameraIndex, mode: Literal['furthest_double', 't20'] = 'furthest_double'):
    async def detect():
        data = await cameras.get_data()
        body, _ = await cameras.get_frame(index)
        if (await cameras.get_data())['revision'] != data['revision']:
            raise cameras.CameraError(409, 'Camera settings changed during targeting. Try again.')
        result = await asyncio.to_thread(camera_vision.target, body, data['settings']['devices'][index], mode)
        result['revision'] = data['revision']
        return result
    return await _control_call(detect())


@router.get('/calibration')
async def calibration_status():
    return camera_controls.job_status()


@router.post('/calibration/start/{index}')
async def start_calibration(index: CameraIndex, payload: CalibrationRequest):
    return await _control_call(camera_controls.start(index, payload.device, payload.roi.model_dump()))


@router.post('/calibration/cancel')
async def cancel_calibration():
    return await _control_call(camera_controls.cancel())


@router.post('/calibration/apply/{job_id}')
async def apply_calibration(job_id: str):
    return await _control_call(camera_controls.apply_calibration(job_id))
