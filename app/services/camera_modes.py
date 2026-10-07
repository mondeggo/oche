"""Read camera capture capabilities without starting or reconfiguring capture.

Linux V4L2 ENUM_FMT / ENUM_FRAMESIZES / ENUM_FRAMEINTERVALS use fixed-size
structures on both supported Linux architectures. Only enumeration ioctls are
issued; an already running Autodarts capture keeps ownership of its stream.
"""

from copy import deepcopy
from fractions import Fraction
import os
from pathlib import Path
import re
import stat
import struct
import sys
import threading
import time

ENUM_FMT = 0xC0405602
ENUM_SIZES = 0xC02C564A
ENUM_INTERVALS = 0xC034564B
_cache = {}
_lock = threading.Lock()


def _interval_rates(buffer):
    kind = struct.unpack_from('I', buffer, 16)[0]
    if kind == 1:
        numerator, denominator = struct.unpack_from('II', buffer, 20)
        return [denominator / numerator] if numerator and denominator else []
    if kind not in (2, 3):
        return []
    n1, d1, n2, d2, ns, ds = struct.unpack_from('6I', buffer, 20)
    if not all((n1, d1, n2, d2)):
        return []
    low, high = Fraction(n1, d1), Fraction(n2, d2)
    step = Fraction(ns, ds) if ns and ds else None
    if high < low or (kind == 3 and step is None):
        return []
    candidates = {low, high, *(Fraction(1, fps) for fps in range(1, 241))}
    return [float(1 / value) for value in candidates if low <= value <= high
            and (kind == 2 or ((value - low) / step).denominator == 1)]


def _frame_sizes(buffer):
    kind = struct.unpack_from('I', buffer, 8)[0]
    if kind == 1:
        return [struct.unpack_from('II', buffer, 12)]
    if kind not in (2, 3):
        return []
    min_w, max_w, step_w, min_h, max_h, step_h = struct.unpack_from('6I', buffer, 12)
    if kind == 2:
        step_w = step_h = 1
    if not step_w or not step_h:
        return []
    # Continuous ranges cannot be listed exhaustively. Offer common sizes only
    # when they satisfy the device's advertised range and step, plus endpoints.
    candidates = {(min_w, min_h), (max_w, max_h), (640, 480), (800, 600),
                  (1280, 720), (1280, 960), (1920, 1080), (2560, 1440), (3840, 2160)}
    return [(w, h) for w, h in candidates if min_w <= w <= max_w and min_h <= h <= max_h
            and (w - min_w) % step_w == 0 and (h - min_h) % step_h == 0]


def _read_modes(fd, ioctl):
    modes = {}
    formats = set()
    for capture_type in (1, 9):  # ordinary and multi-planar video capture
        for index in range(16):
            buffer = bytearray(64)
            struct.pack_into('II', buffer, 0, index, capture_type)
            try:
                ioctl(fd, ENUM_FMT, buffer)
            except OSError:
                break
            formats.add(struct.unpack_from('I', buffer, 44)[0])
    for pixel_format in formats:
        format_modes = {}
        for index in range(64):
            buffer = bytearray(44)
            struct.pack_into('II', buffer, 0, index, pixel_format)
            try:
                ioctl(fd, ENUM_SIZES, buffer)
            except OSError:
                break
            for width, height in _frame_sizes(buffer):
                if not (16 <= width <= 8192 and 16 <= height <= 8192 and width * height <= 16777216):
                    continue
                rates = format_modes.setdefault((width, height), set())
                for interval_index in range(64):
                    intervals = bytearray(52)
                    struct.pack_into('4I', intervals, 0, interval_index, pixel_format, width, height)
                    try:
                        ioctl(fd, ENUM_INTERVALS, intervals)
                    except OSError:
                        break
                    for fps in _interval_rates(intervals):
                        # Frame-interval quantization can encode 30 FPS as
                        # 30.00003. Normalize before comparing pixel formats.
                        nearest = round(fps)
                        rate = nearest if abs(fps - nearest) <= 0.001 else round(fps, 6)
                        if 1 <= rate <= 240:
                            rates.add(rate)
                    if struct.unpack_from('I', intervals, 16)[0] != 1:
                        break
            if struct.unpack_from('I', buffer, 8)[0] != 1:
                break
        # Autodarts does not expose a verified mapping from its camera IDs to
        # V4L2 pixel formats. A rate is safe to offer only if every format that
        # advertises this size supports it. An unreadable interval list stays
        # empty, so another format cannot supply guessed rates for it.
        for size, rates in format_modes.items():
            if size in modes:
                modes[size].intersection_update(rates)
            else:
                modes[size] = set(rates)
    return [{'width': w, 'height': h, 'fps': sorted(rates)} for (w, h), rates in sorted(modes.items())]


def enumerate_modes(path):
    """Return verified modes for an exposed local video node, or [] if unknown."""
    if not sys.platform.startswith('linux'):
        return []
    import fcntl
    try:
        resolved = str(Path(path).resolve())
        if not re.fullmatch(r'/dev/video\d+', resolved):
            return []
        info = os.stat(resolved)
        if not stat.S_ISCHR(info.st_mode):
            return []
        key = (resolved, info.st_rdev, info.st_ino)
        with _lock:
            cached = _cache.get(key)
            if cached and time.monotonic() - cached[0] < 15:
                return deepcopy(cached[1])
            fd = os.open(resolved, os.O_RDONLY | os.O_NONBLOCK | os.O_CLOEXEC | os.O_NOFOLLOW)
            try:
                result = _read_modes(fd, fcntl.ioctl)
            finally:
                os.close(fd)
            if len(_cache) > 32:
                _cache.clear()
            _cache[key] = (time.monotonic(), result)
            return deepcopy(result)
    except (OSError, ValueError, TypeError):
        return []
