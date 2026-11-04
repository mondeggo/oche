#!/usr/bin/env bash
# Oche Docker management helper
set -euo pipefail

SCRIPT_DIR="$(cd -- "$(dirname -- "${BASH_SOURCE[0]}")" && pwd)"
# Repository scripts live in scripts/; installed helpers live beside Compose.
if [[ ! -f "$SCRIPT_DIR/docker-compose.yml" ]]; then
    SCRIPT_DIR="$(cd -- "$SCRIPT_DIR/.." && pwd)"
fi
cd "$SCRIPT_DIR"

usage() {
    cat <<EOF
Usage: $0 {start|stop|restart|update|pull|status|logs|cameras|autodarts}
       $0 -h | --help

Manage Oche using the Compose files and .env in the project directory.

  start    Pull the configured image and start Oche.
  stop     Remove containers, keeping persistent data.
  restart  Pull the configured image and recreate Oche.
  update   Download and apply an update (same as restart).
  pull     Download the image without restarting Oche.
  status   Show the container state.
  logs     Follow the Oche logs (Ctrl+C to stop).
  cameras  Select individual cameras, all /dev devices, or none; recreate Oche.
  autodarts  Install Autodarts board detection into data/modules if it is missing.
             Later Autodarts updates come from System > Updates in Oche.
  -h, --help  Show this help without requiring Docker.

If pulling fails, start/restart/update/pull reuse an existing local image
or report an error. Docker Compose v2 is required for management commands.
EOF
}
[[ $# -eq 1 ]] || { usage >&2; exit 1; }
case "$1" in
    start|restart|update|pull|stop|status|logs|cameras|autodarts) ;;
    -h|--help) usage; exit 0 ;;
    *) usage >&2; exit 1 ;;
esac

command -v docker >/dev/null || { echo "Docker is required." >&2; exit 1; }
docker info >/dev/null
docker compose version >/dev/null || { echo "Docker Compose v2 is required." >&2; exit 1; }
if [[ "$1" == cameras ]]; then
    if [[ ! -f "$SCRIPT_DIR/scripts/install.sh" ]]; then
        echo "Camera setup is missing. Rerun the installer to restore it." >&2
        exit 1
    fi
    source "$SCRIPT_DIR/scripts/install.sh"
    reconfigure_cameras
fi
COMPOSE_FILE="$SCRIPT_DIR/docker-compose.yml"
if [[ -f "$SCRIPT_DIR/docker/compose.yml" ]]; then
    COMPOSE_FILE="$SCRIPT_DIR/docker/compose.yml"
fi
COMPOSE_CMD=(docker compose --project-directory "$SCRIPT_DIR" -f "$COMPOSE_FILE")
if [[ -f "$SCRIPT_DIR/docker-compose.override.yml" ]]; then
    COMPOSE_CMD+=(-f "$SCRIPT_DIR/docker-compose.override.yml")
fi
compose() { "${COMPOSE_CMD[@]}" "$@"; }

# Resolve interpolation through Compose itself, including .env and overrides.
IMAGE=$(compose config --images oche)
[[ -n "$IMAGE" && "$IMAGE" != *$'\n'* ]] || { echo "Expected one image for the oche service." >&2; exit 1; }

ensure_image() {
    if ! docker pull "$IMAGE"; then
        echo "Pull failed; checking for an existing local image." >&2
        if docker image inspect "$IMAGE" >/dev/null 2>&1; then
            echo "Using existing local image: $IMAGE"
        else
            echo "No local image available for $IMAGE. Check registry access and retry." >&2
            return 1
        fi
    fi
}

case "$1" in
    start) ensure_image; compose up -d --no-build --pull never oche ;;
    restart|update) ensure_image; compose up -d --no-build --pull never --force-recreate oche ;;
    pull) ensure_image ;;
    stop) compose down ;;
    status) compose ps oche ;;
    logs) compose logs -f --tail 200 oche ;;
    cameras)
        compose config --quiet
        compose up -d --no-build --pull never --force-recreate oche
        ;;
    autodarts)
        ensure_image
        # The image's updater verifies the release and saves it as the container user.
        compose run --rm --entrypoint python oche -m app.services.install_module autodarts
        ;;
esac
