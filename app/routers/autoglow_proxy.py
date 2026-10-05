"""Adapt AutoGlow's root-relative interface to Oche's service proxy."""

import re

from app.service_proxy import create_service_proxy
from app.services import autoglow

PREFIX = "/autoglow/ui"
# AutoGlow assumes it runs at /. Rewrite its known URL literals, not arbitrary
# slash characters: SVG markup and JavaScript regular expressions use those too.
_ROUTES = (
    "hub", "dashboard", "devices", "wled-devices", "autodarts",
    "autodarts-account", "matrix", "event-matrix", "power", "power-timers",
    "idle", "idle-mode", "simulator", "led-simulator", "settings", "updates",
    "updates-backups", "backups", "app.js", "style.css",
)
_ROOT_URL = re.compile(
    rb"([\"'`])(/(?:(?:api|css|js|ws)/|(?:"
    + b"|".join(re.escape(route.encode()) for route in _ROUTES)
    + rb")(?=[\"'`?#])))"
)


def rewrite_asset(body: bytes) -> bytes:
    body = _ROOT_URL.sub(lambda match: match[1] + PREFIX.encode() + match[2], body)
    return body.replace(b"${location.host}/ws/", b"${location.host}" + PREFIX.encode() + b"/ws/")


router = create_service_proxy(PREFIX, lambda: autoglow.PORT, "AutoGlow", rewrite_asset)
