"""Container entry point; retain port 8180 for existing Compose installations."""

import os

import uvicorn


def main():
    port = int(os.environ.get("OCHE_PORT", "8180"))
    if not 1 <= port <= 65535:
        raise ValueError("OCHE_PORT must be between 1 and 65535")
    uvicorn.run("app.main:app", host="0.0.0.0", port=port)


if __name__ == "__main__":
    main()
