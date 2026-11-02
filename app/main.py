"""One Oche application and lifecycle, including the darts runtime."""

import os
from contextlib import asynccontextmanager
from pathlib import Path

import httpx
from fastapi import FastAPI, HTTPException, Request
from fastapi.responses import JSONResponse, RedirectResponse
from fastapi.staticfiles import StaticFiles
from starlette.concurrency import run_in_threadpool

from app import __version__
from app.config import DATA_DIR, load_config
from app.core.api.http import create_router as create_core_router
from app.core.api.local import router as local_router
from app.core.api.ui import UISettings
from app.core.api.ui import router as ui_router
from app.core.api.websocket import router as events_router
from app.core.api.wled2 import router as lighting_router
from app.core.autodarts.auth import safe_error
from app.core.autodarts.errors import ConnectionProblem
from app.core.config import Settings
from app.core.integrations.wled.simulator import DEMO_URL
from app.core.integrations.wled.simulator import create_app as create_demo_app
from app.core.runtime import Runtime
from app.navigation import HOME_ROUTES
from app.routers import autodarts, cameras, config, panels, system, updates
from app.security import same_origin
from app.services import autodarts as detection
from app.services import camera_controls, system_metrics
from app.services.local_https import LocalHTTPS
from app.services.metrics_history import HistoryConfig, MetricsHistory
from app.services.updates import manager as update_manager
from app.templating import templates

STATIC = Path(__file__).parent / "static"


def create_app(
    settings: Settings | None = None, transport=None, *, manage_services: bool | None = None
) -> FastAPI:
    if manage_services is None:
        manage_services = settings is None
    settings = settings or Settings(data_dir=DATA_DIR / "core")
    demo = create_demo_app()

    @asynccontextmanager
    async def lifespan(application: FastAPI):
        async with httpx.AsyncClient(
            timeout=settings.request_timeout,
            transport=transport,
            mounts={DEMO_URL: httpx.ASGITransport(app=demo)},
            follow_redirects=False,
            trust_env=False,
        ) as http:
            runtime = Runtime(settings, http, application.state.ui)
            application.state.runtime = runtime
            application.state.local_https = LocalHTTPS()
            history = MetricsHistory(HistoryConfig.model_validate(load_config().get("metrics_history", {})))
            application.state.metrics_history = history
            runtime.start()
            history.start()
            try:
                if manage_services:
                    saved = load_config()
                    if saved.get("autostart_autodarts") and detection.get_status()["installed"]:
                        await run_in_threadpool(detection.start)
                    await application.state.local_https.restore(application)
                    if os.environ.get("OCHE_LAUNCHER") == "1" and os.environ.get("OCHE_UPDATE_FEED"):
                        try:
                            update_manager.submit("check")
                        except ValueError:
                            pass
                yield
            finally:
                try:
                    await history.close()
                    await runtime.close()
                finally:
                    if manage_services:
                        try:
                            await camera_controls.shutdown()
                        finally:
                            try:
                                await application.state.local_https.close()
                            finally:
                                await run_in_threadpool(detection.stop)

    application = FastAPI(title="Oche", version=__version__, lifespan=lifespan, redoc_url=None)
    application.state.ui = UISettings(settings)

    @application.middleware("http")
    async def browser_guard(request: Request, call_next):
        if request.method not in {"GET", "HEAD", "OPTIONS"}:
            origin = request.headers.get("origin")
            if (
                origin is not None
                and not same_origin(origin, request.url.scheme, request.headers.get("host", ""))
            ) or request.headers.get("sec-fetch-site") == "cross-site":
                return JSONResponse({"detail": "Cross-origin changes are not allowed."}, status_code=403)
            if (
                request.url.path.startswith("/api/")
                and request.method in {"POST", "PUT", "PATCH"}
                and not request.headers.get("content-type", "").startswith("application/json")
            ):
                return JSONResponse({"detail": "Use application/json."}, status_code=415)
        response = await call_next(request)
        response.headers["X-Content-Type-Options"] = "nosniff"
        response.headers["Referrer-Policy"] = "same-origin"
        response.headers.setdefault(
            "Content-Security-Policy", "frame-ancestors 'self'; base-uri 'self'; object-src 'none'"
        )
        if request.url.path.startswith(("/api/", "/updates/", "/system/")):
            response.headers["Cache-Control"] = "no-store"
        elif request.url.path in HOME_ROUTES or request.url.path.startswith("/static/"):
            response.headers["Cache-Control"] = "no-cache"
        return response

    application.mount("/static", StaticFiles(directory=STATIC), name="static")
    application.mount("/demo-wled", demo)
    core_router = create_core_router()
    for router in (
        core_router,
        local_router,
        events_router,
        lighting_router,
        ui_router,
        autodarts.router,
        cameras.router,
        config.router,
        panels.router,
        system.router,
        updates.router,
    ):
        application.include_router(router)

    @application.exception_handler(ConnectionProblem)
    async def connection_error(request: Request, exc: ConnectionProblem):
        return JSONResponse({"detail": str(exc)}, status_code=409)

    @application.exception_handler(httpx.HTTPError)
    @application.exception_handler(OSError)
    async def external_error(request: Request, exc: Exception):
        return JSONResponse({"detail": safe_error(exc)}, status_code=502)

    async def index(request: Request):
        return templates.TemplateResponse(
            request=request,
            name="home.html",
            context={
                "core_page": True,
                "home_page": HOME_ROUTES[request.url.path],
                "active_nav": "home",
                "full_width": True,
            },
        )

    for path in HOME_ROUTES:
        application.add_api_route(path, index, methods=["GET"], include_in_schema=False)

    @application.get("/integrations/cameras", include_in_schema=False)
    async def camera_enhancer(request: Request):
        return templates.TemplateResponse(
            request=request,
            name="supervisor_cameras.html",
            context={"active_nav": "cameras", "service": "cameras"},
        )

    @application.get("/local")
    async def local_game(request: Request):
        return templates.TemplateResponse(
            request=request,
            name="local.html",
            context={"active_nav": "local"},
        )

    @application.get("/supervisor")
    async def supervisor(request: Request):
        service = request.query_params.get("service")
        if service == "updates":
            return templates.TemplateResponse(
                request=request,
                name="updates.html",
                context={"request": request, "active_nav": "updates", "service": "updates"},
            )
        if service == "cameras":
            return RedirectResponse("/integrations/cameras", status_code=307)
        if service == "system":
            return templates.TemplateResponse(
                request=request,
                name="system.html",
                context={
                    "request": request,
                    "active_nav": "system",
                    "service": "system",
                    "system": system_metrics.get_status(),
                },
            )
        return await autodarts.page(request)

    @application.get("/play")
    async def play(request: Request):
        return templates.TemplateResponse(
            request=request,
            name="play.html",
            context={"request": request, "active_nav": "play", "full_width": True},
        )

    @application.get("/healthz")
    async def healthz():
        runtime = getattr(application.state, "runtime", None)
        alive = bool(
            runtime
            and runtime.tasks
            and all(
                not task.done() or (not task.cancelled() and task.exception() is None)
                for task in runtime.tasks
            )
        )
        result = {"ok": alive, "status": "ok" if alive else "starting", "version": __version__}
        if os.environ.get("OCHE_BOOT_ID"):
            result["boot_id"] = os.environ["OCHE_BOOT_ID"]
        return JSONResponse(result, status_code=200 if alive else 503)

    @application.post("/system/restart", status_code=202)
    async def restart():
        if os.environ.get("OCHE_LAUNCHER") != "1":
            raise HTTPException(409, "Restart is available when Oche runs through its launcher.")
        if update_manager.lock.locked() or any(
            item.get("pending") for item in update_manager.status()["modules"]
        ):
            raise HTTPException(409, "Wait for the current update to finish before restarting.")
        import oche_runtime as store

        marker = store.DATA / "state/restart-oche"
        marker.parent.mkdir(parents=True, exist_ok=True)
        marker.touch()
        return {"accepted": True}

    return application


app = create_app()
