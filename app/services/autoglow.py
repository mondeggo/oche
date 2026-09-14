import json
import socket
import time
from typing import Optional

import serial
import serial.tools.list_ports

from app.config import load_config

KNOWN_VID_PIDS = [(0x10C4, 0xEA60), (0x1A86, 0x7523), (0x0403, 0x6001), (0x303A, 0x1001)]


def find_esp32_port() -> Optional[str]:
    for port in serial.tools.list_ports.comports():
        if (port.vid, port.pid) in KNOWN_VID_PIDS:
            return port.device
    return None


def check_autodarts_connection() -> bool:
    cfg = load_config()["autoglow"]
    try:
        with socket.create_connection((cfg["ws_host"], cfg["ws_port"]), timeout=0.5):
            return True
    except OSError:
        return False


def get_status() -> dict:
    port = find_esp32_port()
    return {
        "hardware_found": port is not None,
        "port": port,
        "autodarts_connected": check_autodarts_connection(),
    }


def test_animation(port: Optional[str] = None) -> None:
    """Sends a brief rainbow test animation, then settles to green ('Throw')."""
    port = port or find_esp32_port()
    if not port:
        raise RuntimeError("No ESP32 detected")

    with serial.Serial(port, 115200, timeout=1) as ser:
        time.sleep(1.5)  # let the serial connection settle
        rainbow = {"on": True, "bri": 255, "seg": {"fx": 9, "sx": 128, "ix": 128}}
        ser.write((json.dumps(rainbow) + "\n").encode())
        time.sleep(3)
        throw = {"on": True, "bri": 255, "seg": {"fx": 0, "col": [[0, 255, 0]]}}
        ser.write((json.dumps(throw) + "\n").encode())
