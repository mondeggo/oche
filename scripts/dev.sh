#!/usr/bin/env sh
set -eu
cd "$(dirname "$0")/.."
uv sync --locked
if [ "${1:-}" = "--launcher" ]; then
    exec uv run python launcher.py
fi
exec uv run python -m uvicorn app.main:app --host 127.0.0.1 --port 8180 --reload
