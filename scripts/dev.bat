@echo off
setlocal
cd /d "%~dp0.." || exit /b 1

set "OCHE_DEV_LAUNCHER="
if not "%~2"=="" goto usage
if "%~1"=="" goto prepare
if /i "%~1"=="--launcher" (
    set "OCHE_DEV_LAUNCHER=1"
    goto prepare
)

:usage
echo Usage: scripts\dev.bat [--launcher]
exit /b 2

:prepare
echo Starting native Windows development, without Docker.
echo Autodarts detection is included in the Linux container image, not this Windows process.
echo For container development: docker compose -f docker-compose.yml -f docker-compose.dev.yml up --build
echo.
where uv >nul 2>&1
if errorlevel 1 (
    echo Install uv and make sure it is available on PATH.
    exit /b 1
)

uv sync --locked
if errorlevel 1 exit /b %errorlevel%

if defined OCHE_DEV_LAUNCHER (
    uv run python launcher.py
) else (
    uv run python -m uvicorn app.main:app --host 127.0.0.1 --port 8180 --reload
)
exit /b %errorlevel%
