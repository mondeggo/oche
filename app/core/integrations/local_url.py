"""Browser URLs for integration services exposed on the host."""


def local_http_url(hostname: str | None, port: int) -> str:
    """Return the service origin using the hostname the browser used for Oche."""
    host = hostname or "127.0.0.1"
    if ":" in host and not host.startswith("["):
        host = f"[{host}]"
    return f"http://{host}:{port}/"
