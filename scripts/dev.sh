#!/usr/bin/env bash
# Build and launch Oche locally with live logs and reload.
set -euo pipefail

cd -- "$(dirname -- "${BASH_SOURCE[0]}")/.."
command -v docker >/dev/null || { echo "Install and start Docker first." >&2; exit 1; }
mode=${1:-}
if [[ -n "$mode" && "$mode" != "--updates" ]]; then
    echo "Usage: bash scripts/dev.sh [--updates]" >&2
    exit 2
fi

compose=(docker compose -p oche-dev -f docker-compose.yml)
if [[ -f docker-compose.override.yml ]]; then
    compose+=(-f docker-compose.override.yml)
fi

echo "Oche development: http://localhost:8180"
echo "Stop other Oche containers first; standalone OcheCore development can run alongside on port 9180."
echo "Press Ctrl+C to stop; logs appear below."
compose+=(-f docker-compose.build.yml)
if [[ "$mode" == "--updates" ]]; then
    compose+=(-f docker-compose.updates.yml)
    echo "Launcher mode: application updates enabled; source live reload disabled."
fi
exec "${compose[@]}" up --build
