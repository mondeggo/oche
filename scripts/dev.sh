#!/usr/bin/env bash
# Build and launch Oche locally with live logs and reload.
set -euo pipefail

cd -- "$(dirname -- "${BASH_SOURCE[0]}")/.."
command -v docker >/dev/null || { echo "Install and start Docker first." >&2; exit 1; }

compose=(docker compose -p oche-dev -f docker-compose.yml)
if [[ -f docker-compose.override.yml ]]; then
    compose+=(-f docker-compose.override.yml)
fi

echo "Oche development: http://localhost:8180"
echo "Stop other Oche containers first. Press Ctrl+C to stop; logs appear below."
exec "${compose[@]}" -f docker-compose.build.yml up --build
