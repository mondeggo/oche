#!/usr/bin/env sh
set -eu
ROOT_DIR=$(CDPATH= cd -- "$(dirname -- "$0")/.." && pwd)
cd "$ROOT_DIR"

usage() {
    echo "Usage: scripts/dev.sh [COMPOSE_ARGS...]"
    echo "       scripts/dev.sh --no-docker [--launcher]"
    echo "Docker is the default; use --no-docker to run locally with uv."
}

case "${1:-}" in
    -h|--help) usage; exit 0 ;;
    --no-docker)
        shift
        case "$#:${1:-}" in
            0:|1:--launcher) ;;
            *) usage >&2; exit 2 ;;
        esac
        echo "Starting local development without Docker."
        uv sync --locked
        if [ "${1:-}" = "--launcher" ]; then
            exec uv run python launcher.py
        fi
        exec uv run python -m uvicorn app.main:app --host 127.0.0.1 --port 8180 --reload
        ;;
    --launcher)
        echo "For a local launcher, use: scripts/dev.sh --no-docker --launcher" >&2
        echo "Docker development already runs through the launcher." >&2
        exit 2
        ;;
esac

echo "Using Docker for development. Run scripts/dev.sh --no-docker to run locally with uv."
if [ "$#" -eq 0 ]; then
    set -- up --build
fi
if [ -f "$ROOT_DIR/docker-compose.override.yml" ]; then
    set -- -f "$ROOT_DIR/docker-compose.override.yml" "$@"
fi
exec docker compose --project-name oche-dev --project-directory "$ROOT_DIR" \
    -f "$ROOT_DIR/docker/compose.yml" -f "$ROOT_DIR/docker/compose.dev.yml" "$@"
