#!/usr/bin/env bash
# Oche Docker management helper
set -euo pipefail

SCRIPT_DIR="$(cd -- "$(dirname -- "${BASH_SOURCE[0]}")" && pwd)"
cd "$SCRIPT_DIR"

usage() { echo "Usage: $0 {start|restart|pull|stop|build|push}"; }
[[ $# -eq 1 ]] || { usage >&2; exit 1; }
case "$1" in
    start|restart|pull|stop|build|push) ;;
    -h|--help) usage; exit 0 ;;
    *) usage >&2; exit 1 ;;
esac

command -v docker >/dev/null || { echo "Docker is required." >&2; exit 1; }
docker info >/dev/null
docker compose version >/dev/null || { echo "Docker Compose v2 is required." >&2; exit 1; }
COMPOSE_CMD=(docker compose --project-directory "$SCRIPT_DIR" -f "$SCRIPT_DIR/docker-compose.yml")
if [[ -f "$SCRIPT_DIR/docker-compose.override.yml" ]]; then
    COMPOSE_CMD+=(-f "$SCRIPT_DIR/docker-compose.override.yml")
fi
compose() { "${COMPOSE_CMD[@]}" "$@"; }

# Resolve interpolation through Compose itself, including .env and overrides.
IMAGE=$(compose config --images oche)
[[ -n "$IMAGE" && "$IMAGE" != *$'\n'* ]] || { echo "Expected one image for the oche service." >&2; exit 1; }

build_image() {
    if [[ ! -f Dockerfile || ! -f requirements.txt || ! -d app ]]; then
        echo "Local builds need an Oche source checkout (Dockerfile, requirements.txt, and app/). This installation uses a published image." >&2
        return 1
    fi
    docker build -t "$IMAGE" .
}

ensure_image() {
    if ! docker pull "$IMAGE"; then
        echo "Pull failed; checking for a local image or source checkout." >&2
        if docker image inspect "$IMAGE" >/dev/null 2>&1; then
            echo "Using existing local image: $IMAGE"
        else
            build_image
        fi
    fi
}

case "$1" in
    start) ensure_image; compose up -d --no-build --pull never oche ;;
    restart) ensure_image; compose up -d --no-build --pull never --force-recreate oche ;;
    pull) ensure_image ;;
    stop) compose down ;;
    build) build_image ;;
    push)
        docker image inspect "$IMAGE" >/dev/null || { echo "Build or pull $IMAGE before pushing." >&2; exit 1; }
        docker push "$IMAGE"
        ;;
esac
