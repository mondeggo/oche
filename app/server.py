"""Container entry point without the launcher."""

import os
from pathlib import Path

import uvicorn


def main():
    port = int(os.environ.get("OCHE_PORT", "8180"))
    if not 1 <= port <= 65535:
        raise ValueError("OCHE_PORT must be between 1 and 65535")
    reload = os.environ.get("OCHE_DEV_RELOAD") == "1"
    uvicorn.run(
        "app.main:app", host="0.0.0.0", port=port, reload=reload,
        reload_dirs=[str(Path(__file__).resolve().parent)] if reload else None,
    )


if __name__ == "__main__":
    main()
