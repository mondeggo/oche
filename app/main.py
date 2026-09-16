from contextlib import asynccontextmanager

from fastapi import FastAPI, Request
from fastapi.staticfiles import StaticFiles

from app.config import load_config
from app.routers import autodarts, autoglow, config, panels, system
from app.services import autodarts as autodarts_service
from app.services import autoglow as autoglow_service
from app.services import system_metrics
from app.templating import templates

@asynccontextmanager
async def lifespan(app: FastAPI):
    try:
        autoglow_service.start()
        if load_config().get("autostart_autodarts"):
            autodarts_service.start()
        yield
    finally:
        autoglow_service.stop()


app = FastAPI(title="Oche", lifespan=lifespan)

app.mount("/static", StaticFiles(directory="app/static"), name="static")

app.include_router(autodarts.router)
app.include_router(autoglow.router)
app.include_router(config.router)
app.include_router(panels.router)
app.include_router(system.router)


@app.get("/")
async def index(request: Request):
    return templates.TemplateResponse(
        "index.html",
        {
            "request": request,
            "system": system_metrics.get_status(),
            "autodarts_status": autodarts_service.get_status(),
            "autoglow_status": autoglow_service.get_status(),
        },
    )


@app.get("/supervisor")
async def supervisor(request: Request):
    return await autodarts.page(request)


@app.get("/board")
async def board(request: Request):
    return templates.TemplateResponse(
        "board.html",
        {
            "request": request,
            "status": autodarts_service.get_status(),
            "active_nav": "board",
            "full_width": True,
            "allow_header_autohide": True,
            "autohide_navbar_default": load_config().get(
                "autohide_navbar_on_board", False
            ),
        },
    )


@app.get("/play")
async def play(request: Request):
    return templates.TemplateResponse(
        "play.html",
        {
            "request": request,
            "active_nav": "play",
            "full_width": True,
            "allow_header_autohide": True,
            "autohide_navbar_default": load_config().get(
                "autohide_navbar_on_play", False
            ),
        },
    )


@app.get("/healthz")
async def healthz():
    return {"ok": True}
