"""Wake OcheCore's cameras for a game and tell when the board is ready for it.

OcheCore closes its cameras after its detection standby time (``standby_seconds``)
and then waits for its start command. Its Autodarts-compatible ``/api/state`` still
reports a running board ready for a throw, also while the board is too dark or not
empty, so its own ``/api/v1/status`` tells whether it can score.
"""

import asyncio

import httpx

ASLEEP = {"stopped", "standby"}


async def status(http: httpx.AsyncClient, url: str) -> dict:
    response = await http.get(f"{url}/api/v1/status", timeout=2)
    response.raise_for_status()
    data = response.json()
    if not isinstance(data, dict):
        raise ValueError("Invalid OcheCore status")
    return data


async def command(http: httpx.AsyncClient, url: str, name: str) -> None:
    response = await http.post(f"{url}/api/v1/command", json={"command": name}, timeout=5)
    response.raise_for_status()


async def wake(http: httpx.AsyncClient, url: str) -> bool:
    """Send OcheCore's start command when its cameras are stopped or in standby; True if sent."""
    if (await status(http, url)).get("state") not in ASLEEP:
        return False
    await command(http, url, "start")
    return True


async def clear_stale_visit(http: httpx.AsyncClient, url: str, *, seconds: float = 6) -> bool:
    """Reset OcheCore's visit when it lists darts the board does not hold; True if sent.

    OcheCore keeps its visit through standby and restarts, and a dart it could not
    place can stay listed after the board is empty. A new game would wait for that
    dart to be cleared and score nothing. Darts still on the board are kept: they
    need a takeout, and a reset would make OcheCore ignore the next throws.
    """
    loop = asyncio.get_running_loop()
    deadline = loop.time() + seconds
    # The cameras count the darts on the board a moment after they open.
    while True:
        current = await status(http, url)
        if current.get("state") == "running" and type(current.get("physical_darts")) is int:
            break
        if loop.time() >= deadline:
            return False
        await asyncio.sleep(0.25)
    if current["physical_darts"] or not current.get("throws"):
        return False
    await command(http, url, "reset")
    return True


def hold_reason(snapshot: dict) -> str | None:
    """What players must fix before the first dart, or None when OcheCore can score.

    Follows OcheCore's board activity: cameras that cannot see the board read TOO
    DARK, and a board that was not empty when the cameras started reads CLEAR THE
    BOARD. Nothing counts until every camera sees an empty board.
    """
    # A sleeping board is woken by the game; demo and control modes have no scene.
    if snapshot.get("state") != "running" or snapshot.get("mode") != "live":
        return None
    assigned = [
        camera.get("slot") for camera in snapshot.get("config", {}).get("cameras", []) if camera.get("device")
    ]
    cameras = {camera.get("slot"): camera.get("state") for camera in snapshot.get("cameras", [])}
    if any(cameras.get(slot) == "error" for slot in assigned):
        return "An OcheCore camera has an error. Check OcheCore's page in Integrations."
    if any(cameras.get(slot) != "streaming" for slot in assigned):
        return "Waiting for OcheCore's cameras to start."
    if not snapshot.get("capabilities", {}).get("vision"):
        return "Calibrate OcheCore before playing."
    if snapshot.get("blind_cameras"):
        return "Too dark. Turn on the light so OcheCore's cameras can see the board."
    scene = snapshot.get("scene")
    if scene == "empty":
        return None
    if scene == "unknown":
        return "Waiting for OcheCore to see the board."
    return "Clear the board. OcheCore sees something on it."


async def hold(http: httpx.AsyncClient, url: str) -> str | None:
    return hold_reason(await status(http, url))
