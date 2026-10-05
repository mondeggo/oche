from contextlib import asynccontextmanager

from fastapi import FastAPI, Request
from fastapi.staticfiles import StaticFiles
from fastapi.responses import JSONResponse, RedirectResponse
from starlette.concurrency import run_in_threadpool

from app.config import load_config
from app.routers import autodarts, autoglow, autoglow_proxy, config, panels, system
from app.services import autodarts as autodarts_service
from app.services import autoglow as autoglow_service
from app.services import system_metrics
from app.services.local_https import LocalHTTPS
from app.templating import templates
from app.security import same_origin

@asynccontextmanager
async def lifespan(app: FastAPI):
    app.state.local_https = LocalHTTPS()
    try:
        config = load_config()
        if config.get("autostart_autoglow"):
            autoglow_service.start()
        if config.get("autostart_autodarts"):
            autodarts_service.start()
        await app.state.local_https.restore(app)
        yield
    finally:
        try:
            await app.state.local_https.close()
        finally:
            try:
                await run_in_threadpool(autodarts_service.stop)
            finally:
                await run_in_threadpool(autoglow_service.stop)


app = FastAPI(title="Oche", lifespan=lifespan)


@app.middleware("http")
async def check_write_origin(request: Request, call_next):
    if request.method not in ("GET", "HEAD", "OPTIONS"):
        origin = request.headers.get("origin")
        if ((origin is not None and not same_origin(origin, request.url.scheme, request.headers.get("host", "")))
                or request.headers.get("sec-fetch-site") == "cross-site"):
            return JSONResponse({"detail": "Cross-origin changes are not allowed."}, status_code=403)
    return await call_next(request)

app.mount("/static", StaticFiles(directory="app/static"), name="static")

app.include_router(autodarts.router)
app.include_router(autoglow.router)
app.include_router(autoglow_proxy.router)
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


@app.get("/board", include_in_schema=False)
@app.get("/autodarts", include_in_schema=False)
async def legacy_board():
    return RedirectResponse("/supervisor?service=autodarts", status_code=308)


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
