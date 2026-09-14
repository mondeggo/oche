import shutil
import time
from typing import Optional

import psutil

from app.config import DATA_DIR

# Primes psutil's internal sample so the first real call below doesn't block
# for an interval or report a meaningless 0.0 (see psutil docs on
# non-blocking cpu_percent usage).
psutil.cpu_percent(interval=None)


def _io_snapshot() -> dict:
    disk = psutil.disk_io_counters()
    net = psutil.net_io_counters()
    return {
        "time": time.monotonic(),
        "disk_read": disk.read_bytes if disk else 0,
        "disk_write": disk.write_bytes if disk else 0,
        "net_sent": net.bytes_sent if net else 0,
        "net_recv": net.bytes_recv if net else 0,
    }


# Disk/network counters from psutil are cumulative since boot, not a rate -
# keep the previous sample around so get_status() can diff against it.
_last_io = _io_snapshot()


def _rate_mb_s(prev: int, now: int, dt: float) -> float:
    if dt <= 0:
        return 0.0
    return round(max(now - prev, 0) / dt / (1024 ** 2), 2)


def _cpu_temp_c() -> Optional[float]:
    sensors = getattr(psutil, "sensors_temperatures", None)
    if sensors is None:
        return None
    try:
        temps = sensors()
    except OSError:
        return None
    if not temps:
        return None
    for label in ("cpu_thermal", "coretemp", "k10temp", "cpu-thermal", "soc_thermal"):
        if temps.get(label):
            return temps[label][0].current
    for entries in temps.values():
        if entries:
            return entries[0].current
    return None


def get_status() -> dict:
    global _last_io

    mem = psutil.virtual_memory()
    disk = shutil.disk_usage(DATA_DIR)
    disk_percent = round(disk.used / disk.total * 100, 1) if disk.total else 0.0

    io_now = _io_snapshot()
    dt = io_now["time"] - _last_io["time"]
    disk_read_mb_s = _rate_mb_s(_last_io["disk_read"], io_now["disk_read"], dt)
    disk_write_mb_s = _rate_mb_s(_last_io["disk_write"], io_now["disk_write"], dt)
    net_recv_mb_s = _rate_mb_s(_last_io["net_recv"], io_now["net_recv"], dt)
    net_sent_mb_s = _rate_mb_s(_last_io["net_sent"], io_now["net_sent"], dt)
    _last_io = io_now

    return {
        "cpu_percent": psutil.cpu_percent(interval=None),
        "cpu_count": psutil.cpu_count() or 1,
        "mem_percent": mem.percent,
        "mem_used_gb": round(mem.used / (1024 ** 3), 1),
        "mem_total_gb": round(mem.total / (1024 ** 3), 1),
        "disk_percent": disk_percent,
        "disk_used_gb": round(disk.used / (1024 ** 3), 1),
        "disk_total_gb": round(disk.total / (1024 ** 3), 1),
        "disk_read_mb_s": disk_read_mb_s,
        "disk_write_mb_s": disk_write_mb_s,
        "net_recv_mb_s": net_recv_mb_s,
        "net_sent_mb_s": net_sent_mb_s,
        "cpu_temp_c": _cpu_temp_c(),
        "uptime_s": int(time.time() - psutil.boot_time()),
    }
