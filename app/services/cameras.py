"""Camera-only access to the Autodarts process managed by Oche.

The upstream configuration contains credentials. Keep it private and return only
the explicit camera fields below; upstream error bodies are private too.
"""

import asyncio
import hashlib
import json
import math
import os
import re
import tomllib
from urllib.parse import parse_qs, urlsplit

import aiohttp

from app.services import autodarts
from app.services.camera_modes import enumerate_modes


DEFAULT_PORT = 3180
MAX_JSON_BYTES = 2 * 1024 * 1024
MAX_FRAME_BYTES = 12 * 1024 * 1024
REQUEST_TIMEOUT = aiohttp.ClientTimeout(total=4, connect=1, sock_read=3)
_settings_lock = asyncio.Lock()


class CameraError(Exception):
    def __init__(self, status_code: int, detail: str):
        super().__init__(detail)
        self.status_code = status_code
        self.detail = detail


def _api_base() -> str:
    if autodarts.get_status().get("status") != "running":
        raise CameraError(503, "Start Autodarts in Supervisor to use the camera tools.")
    # The running daemon uses this link (or the native configuration directory).
    # Reading its target also respects the user's choice to reuse host settings.
    path = autodarts.CONFIG_LINK / "config.toml"
    if not path.is_file():
        host = autodarts.HOST_CONFIG_DIR / "config.toml"
        reuse = os.environ.get("OCHE_REUSE_AUTODARTS_CONFIG", "true").strip().lower() == "true"
        path = host if reuse and host.is_file() else autodarts.AUTODARTS_DIR / "config.toml"
    try:
        if path.is_file():
            with path.open("rb") as stream:
                raw = stream.read(MAX_JSON_BYTES + 1)
            if len(raw) > MAX_JSON_BYTES:
                raise ValueError("oversized config")
            config = tomllib.loads(raw.decode("utf-8"))
            port = config.get("api", {}).get("port", config.get("host", {}).get("port", DEFAULT_PORT))
        else:
            port = DEFAULT_PORT
        if isinstance(port, bool) or not re.fullmatch(r"[0-9]{1,5}", str(port)):
            raise ValueError("invalid port")
        port = int(port)
        if not 1 <= port <= 65535:
            raise ValueError("invalid port")
    except (OSError, UnicodeError, ValueError, AttributeError, TypeError):
        raise CameraError(503, "The Autodarts API port could not be read from its configuration.") from None
    # Never use a host or URL supplied by the browser or upstream configuration.
    return f"http://127.0.0.1:{port}"


async def _read_limited(response, limit: int) -> bytes:
    if response.content_length is not None and response.content_length > limit:
        raise CameraError(502, "Autodarts returned more camera data than expected.")
    result = bytearray()
    async for chunk in response.content.iter_chunked(65536):
        result.extend(chunk)
        if len(result) > limit:
            raise CameraError(502, "Autodarts returned more camera data than expected.")
    return bytes(result)


async def _json_request(session, base: str, path: str, *, method="GET", payload=None):
    try:
        async with session.request(method, base + path, json=payload, allow_redirects=False) as response:
            if response.status != 200:
                raise CameraError(503, "Autodarts could not provide the camera settings. Try again shortly.")
            if response.content_type != "application/json":
                raise CameraError(502, "Autodarts returned an unexpected camera response.")
            raw = await _read_limited(response, MAX_JSON_BYTES)
        return json.loads(raw)
    except (aiohttp.ClientError, asyncio.TimeoutError, OSError):
        message = ("The save could not be confirmed. Reload the camera settings before trying again."
                   if method == "PATCH" else "The Autodarts camera API is unavailable. Try again shortly.")
        raise CameraError(503, message) from None
    except (ValueError, UnicodeError, RecursionError):
        raise CameraError(502, "Autodarts returned an unexpected camera response.") from None


def _number(value, *, maximum=1000):
    if isinstance(value, bool) or not isinstance(value, (int, float)):
        return None
    if not 0 < value <= maximum or not math.isfinite(value):
        return None
    return value


def _settings(config: object) -> dict:
    cam = config.get("cam") if isinstance(config, dict) else None
    if not isinstance(cam, dict):
        raise CameraError(502, "Autodarts returned invalid camera settings.")
    devices = cam.get("cams")
    width, height = cam.get("width"), cam.get("height")
    fps = _number(cam.get("fps", cam.get("fps_max")))
    if (not isinstance(devices, list) or len(devices) != 3 or
            any(not isinstance(device, str) or len(device) > 4096 for device in devices) or
            type(width) is not int or type(height) is not int or
            not 1 <= width <= 16384 or not 1 <= height <= 16384 or fps is None):
        raise CameraError(502, "Autodarts returned invalid camera settings.")
    return {"devices": list(devices), "width": width, "height": height, "fps": fps}


def _revision(settings: dict) -> str:
    normalized = dict(settings, fps=float(settings["fps"]))
    raw = json.dumps(normalized, sort_keys=True, separators=(",", ":"), ensure_ascii=True)
    return hashlib.sha256(raw.encode()).hexdigest()


def _rates(raw) -> list:
    # Only explicit numeric rates are unambiguous. Do not guess whether an
    # unfamiliar fraction describes FPS or a frame interval in seconds.
    if not isinstance(raw, list):
        return []
    rates = set()
    for value in raw:
        if _number(value, maximum=240.001) is None:
            continue
        rounded = round(value)
        # V4L2 frame intervals can round 30 FPS to 30.00003. The v2 config
        # accepts whole FPS only, so genuine fractional rates stay unavailable.
        if 1 <= rounded <= 240 and abs(value - rounded) <= 0.001:
            rates.add(rounded)
    return sorted(rates)


def _modes(raw) -> list[dict]:
    result = {}
    if not isinstance(raw, list):
        return []
    for mode in raw[:512]:
        if not isinstance(mode, dict):
            continue
        width, height = mode.get("width"), mode.get("height")
        if type(width) is not int or type(height) is not int or not 16 <= width <= 8192 or not 16 <= height <= 8192:
            continue
        rates = _rates(mode.get("framerates", mode.get("fps")))
        result.setdefault((width, height), set()).update(rates)
    return [{"width": width, "height": height, "fps": sorted(rates)}
            for (width, height), rates in sorted(result.items())]


def _native_path(identifier: str) -> str | None:
    if re.fullmatch(r"/dev/video[0-9]+", identifier):
        return identifier
    try:
        query = urlsplit(identifier).query if "://" in identifier else identifier
        fields = parse_qs(query, keep_blank_values=True, max_num_fields=32)
    except ValueError:
        return None
    for key in ("native", "location"):
        native = fields.get(key, [])
        if len(native) == 1 and re.fullmatch(r"/dev/video[0-9]+", native[0]):
            return native[0]
    return None


async def _catalog(raw) -> tuple[list[dict], dict[str, set[str]]]:
    if not isinstance(raw, list):
        raise CameraError(502, "Autodarts returned an invalid camera list.")
    entries, identities = {}, {}
    for index, device in enumerate(raw[:128]):
        if not isinstance(device, dict) or not isinstance(device.get("formats"), list):
            continue
        name = device.get("card")
        name = name[:120] if isinstance(name, str) and name.strip() else "Camera"
        for camera_format in device["formats"][:64]:
            if not isinstance(camera_format, dict):
                continue
            identifier = camera_format.get("path")
            if not isinstance(identifier, str) or not identifier or len(identifier) > 4096:
                continue
            modes = _modes(camera_format.get("resolutions"))
            local_path = _native_path(identifier)
            label = f"{name} ({local_path})" if local_path else f"{name} (camera {index + 1})"
            entry = entries.setdefault(identifier, {"id": identifier, "label": label, "modes": []})
            entry["modes"] = _modes(entry["modes"] + modes)
            identity = identities.setdefault(identifier, set())
            identity.add(f"device:{index}")
            if local_path:
                identity.add(f"node:{local_path}")
    # One bounded read-only capability query per local device, regardless of how
    # many upstream format identifiers refer to it. Unknown format mappings use
    # only rates common to the device's pixel formats. This never starts capture.
    fallback = {}
    for entry in entries.values():
        if entry["modes"] and all(mode["fps"] for mode in entry["modes"]):
            continue
        path = _native_path(entry["id"])
        if path is None:
            continue
        if path not in fallback:
            fallback[path] = _modes(await asyncio.to_thread(enumerate_modes, path))
        # Keep explicit API rates, and fill only missing/unknown capabilities.
        known = {(mode["width"], mode["height"]): mode for mode in entry["modes"]}
        advertised = set(known)
        for mode in fallback[path]:
            key = (mode["width"], mode["height"])
            if advertised and key not in advertised:
                continue
            if key not in known or not known[key]["fps"]:
                known[key] = mode
        entry["modes"] = [known[key] for key in sorted(known)]
    return list(entries.values()), identities


async def _data(session, base: str) -> tuple[dict, dict[str, set[str]]]:
    config, raw_devices, stats, state = await asyncio.gather(*[
        _json_request(session, base, path) for path in
        ("/api/config", "/api/devices", "/api/cams/stats", "/api/cams/state")
    ])
    settings = _settings(config)
    devices, identities = await _catalog(raw_devices)
    if not isinstance(stats, dict) or not isinstance(state, dict):
        raise CameraError(502, "Autodarts returned invalid camera status.")
    resolution = stats.get("resolution")
    capture_resolution = None
    if isinstance(resolution, dict):
        width, height = resolution.get("width"), resolution.get("height")
        if type(width) is int and type(height) is int and 1 <= width <= 16384 and 1 <= height <= 16384:
            capture_resolution = {"width": width, "height": height}
    rates = stats.get("fps", [])
    rates = rates if isinstance(rates, list) else []
    cameras = []
    for index, device in enumerate(settings["devices"]):
        rate = rates[index] if index < len(rates) else None
        rate = rate if (type(rate) in (float, int) and 0 <= rate <= 1000 and math.isfinite(rate)) else None
        cameras.append({"index": index, "device": device, "fps": rate})
    warnings = []
    if not devices:
        warnings.append("No cameras are available to Autodarts. Check the camera connections and device access.")
    elif any(not device["modes"] or any(not mode["fps"] for mode in device["modes"]) for device in devices):
        warnings.append("Some camera capabilities could not be read. Settings can only be saved for confirmed resolutions and frame rates.")
    available = {device["id"] for device in devices}
    if any(device and device not in available for device in settings["devices"]):
        warnings.append("A configured camera is missing from the available camera list.")
    return {"running": True, "capturing": state.get("isRunning") is True,
            "opened": state.get("isOpened") is True, "capture_resolution": capture_resolution, "settings": settings,
            "revision": _revision(settings), "devices": devices, "cameras": cameras,
            "warnings": warnings}, identities


async def get_data() -> dict:
    base = _api_base()
    async with aiohttp.ClientSession(timeout=REQUEST_TIMEOUT, trust_env=False) as session:
        data, _ = await _data(session, base)
        return data


def _validate_payload(payload: dict) -> dict:
    if not isinstance(payload, dict) or set(payload) != {"revision", "devices", "width", "height", "fps"}:
        raise CameraError(422, "Provide the camera selection, resolution, frame rate, and settings revision.")
    if not isinstance(payload["revision"], str) or not re.fullmatch(r"[a-f0-9]{64}", payload["revision"]):
        raise CameraError(422, "Reload the camera settings before saving.")
    devices = payload["devices"]
    if (not isinstance(devices, list) or len(devices) != 3 or
            any(not isinstance(device, str) or not device or len(device) > 4096 for device in devices) or
            len(set(devices)) != 3):
        raise CameraError(422, "Select three different cameras.")
    width, height, fps = payload["width"], payload["height"], payload["fps"]
    if (type(width) is not int or type(height) is not int or type(fps) is not int or
            not 16 <= width <= 8192 or not 16 <= height <= 8192 or not 1 <= fps <= 240):
        raise CameraError(422, "Select a supported resolution and frame rate.")
    return {"devices": list(devices), "width": width, "height": height, "fps": fps}


async def apply_settings(payload: dict) -> dict:
    desired = _validate_payload(payload)
    async with _settings_lock:
        base = _api_base()
        async with aiohttp.ClientSession(timeout=REQUEST_TIMEOUT, trust_env=False) as session:
            current, identities = await _data(session, base)
            if current["revision"] != payload["revision"]:
                raise CameraError(409, "Camera settings changed in another session. Reload them before saving.")
            available = {device["id"]: device for device in current["devices"]}
            if any(device not in available for device in desired["devices"]):
                raise CameraError(422, "A selected camera is no longer available. Reload the camera list.")
            seen = set()
            for identifier in desired["devices"]:
                # A physical camera may have several video nodes; conversely,
                # several rich URI aliases can identify the same video node.
                if seen.intersection(identities[identifier]):
                    raise CameraError(422, "Select three different physical cameras.")
                seen.update(identities[identifier])
            for identifier in desired["devices"]:
                if not any(mode["width"] == desired["width"] and mode["height"] == desired["height"] and
                           any(math.isclose(rate, desired["fps"], rel_tol=0, abs_tol=0.000001) for rate in mode["fps"])
                           for mode in available[identifier]["modes"]):
                    raise CameraError(422, "The selected resolution and frame rate are not confirmed for all three cameras.")
            cam = {"cams": desired["devices"], "width": desired["width"],
                   "height": desired["height"], "fps": desired["fps"]}
            echoed = _settings(await _json_request(session, base, "/api/config", method="PATCH", payload={"cam": cam}))
            persisted = _settings(await _json_request(session, base, "/api/config"))
            if _revision(echoed) != _revision(desired) or _revision(persisted) != _revision(desired):
                raise CameraError(409, "Autodarts did not keep all requested camera settings. Reload the settings to see the current values.")
            # Reading status again also reflects cameras reopening after a save.
            result, _ = await _data(session, base)
            if result["revision"] != _revision(desired):
                raise CameraError(409, "Camera settings changed while saving. Reload them to see the current values.")
            return result


async def get_frame(index: int) -> tuple[bytes, str]:
    if type(index) is not int or not 0 <= index <= 2:
        raise CameraError(422, "Choose camera 1, 2, or 3.")
    base = _api_base()
    try:
        async with aiohttp.ClientSession(timeout=REQUEST_TIMEOUT, trust_env=False) as session:
            async with session.get(base + f"/api/img/cams/{index}", allow_redirects=False) as response:
                if response.status == 404:
                    raise CameraError(404, "No image is available from this camera yet.")
                if response.status != 200 or response.content_type not in ("image/jpeg", "image/png"):
                    raise CameraError(502, "Autodarts did not return a camera image.")
                content_type = response.content_type
                data = await _read_limited(response, MAX_FRAME_BYTES)
        signature = b"\xff\xd8\xff" if content_type == "image/jpeg" else b"\x89PNG\r\n\x1a\n"
        if not data.startswith(signature):
            raise CameraError(502, "Autodarts did not return a camera image.")
        return data, content_type
    except (aiohttp.ClientError, asyncio.TimeoutError, OSError):
        raise CameraError(503, "The camera image is temporarily unavailable. Try again shortly.") from None
