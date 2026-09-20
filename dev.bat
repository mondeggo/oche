@echo off
REM Build and launch Oche locally with live logs and reload.
setlocal
cd /d "%~dp0"

where docker >nul 2>&1
if errorlevel 1 (
    echo Install and start Docker first.
    exit /b 1
)

set "COMPOSE_OVERRIDE="
if exist docker-compose.override.yml set "COMPOSE_OVERRIDE=-f docker-compose.override.yml"

echo Oche development: http://localhost:8180
echo Stop other Oche containers first. Press Ctrl+C to stop; logs appear below.
docker compose -p oche-dev -f docker-compose.yml %COMPOSE_OVERRIDE% -f docker-compose.build.yml up --build
exit /b %errorlevel%
