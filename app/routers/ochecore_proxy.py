"""Adapt OcheCore's interface, framing policy and Caller audio for embedding."""

import json
import re

from app.service_proxy import create_service_proxy
from app.services import ochecore

PREFIX = "/ochecore/ui"
_ROOT_URL = re.compile(rb"([\"'`])(/(?:api/|static/|events(?=[/\"'`?#])|caller/audio(?=[\"'`?#])))")


def rewrite_asset(body: bytes) -> bytes:
    body = _ROOT_URL.sub(lambda match: match[1] + PREFIX.encode() + match[2], body)
    for endpoint in (b"/caller/audio", b"/events"):
        body = body.replace(b"${location.host}" + endpoint,
                            b"${location.host}" + PREFIX.encode() + endpoint)
    return body


def embedding_headers(headers):
    result = []
    for key, value in headers:
        if key.lower() == "content-security-policy":
            value = re.sub(r"(?i)frame-ancestors\s+[^;]+", "frame-ancestors 'self'", value)
        elif key.lower() == "x-frame-options":
            value = "SAMEORIGIN"
        result.append((key, value))
    return result


def rewrite_socket(path: str, message: str) -> str:
    # Game events remain exactly as the service emitted them. Only the Caller
    # stream contains browser resource URLs which need the embedding prefix.
    if path != "caller/audio":
        return message
    try:
        item = json.loads(message)
    except ValueError:
        return message
    if not isinstance(item, dict) or item.get("type") != "play" or not isinstance(item.get("clips"), list):
        return message
    changed = False
    for clip in item["clips"]:
        if isinstance(clip, dict) and isinstance(clip.get("url"), str) and clip["url"].startswith("/api/caller/audio/"):
            clip["url"] = PREFIX + clip["url"]
            changed = True
    return json.dumps(item, ensure_ascii=False) if changed else message


router = create_service_proxy(
    PREFIX, lambda: ochecore.PORT, "OcheCore", rewrite_asset,
    adjust_headers=embedding_headers, rewrite_socket=rewrite_socket, rebase_origin=True,
)
