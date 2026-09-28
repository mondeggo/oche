"""Browser-origin checks; these do not replace network access control."""

from urllib.parse import urlsplit


def same_origin(origin: str, scheme: str, host: str) -> bool:
    def identity(url):
        parsed = urlsplit(url)
        if parsed.scheme not in ("http", "https") or not parsed.hostname or parsed.username or parsed.password:
            return None
        if parsed.path not in ("", "/") or parsed.query or parsed.fragment:
            return None
        return parsed.scheme, parsed.hostname, parsed.port or (443 if parsed.scheme == "https" else 80)

    try:
        actual = identity(origin)
        return actual is not None and actual == identity(f"{scheme}://{host}")
    except ValueError:
        return False
