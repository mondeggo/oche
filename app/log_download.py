import os
from pathlib import Path

from fastapi.responses import StreamingResponse


def log_chunks(path: Path):
    try:
        source = path.open("rb")
    except FileNotFoundError:
        return
    with source:
        # Do not chase a growing file indefinitely. Truncation simply ends the
        # stream; a mutable file cannot safely advertise a fixed Content-Length.
        remaining = os.fstat(source.fileno()).st_size
        while remaining > 0:
            chunk = source.read(min(65536, remaining))
            if not chunk:
                break
            remaining -= len(chunk)
            yield chunk


def log_download(path: Path):
    return StreamingResponse(log_chunks(path), media_type="text/plain", headers={
        "Content-Disposition": f'attachment; filename="{path.name}"',
        "Cache-Control": "no-store",
        "X-Content-Type-Options": "nosniff",
    })
