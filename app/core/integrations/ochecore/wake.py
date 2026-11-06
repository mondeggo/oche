"""Wake OcheCore's cameras for a game.

OcheCore closes its cameras after its detection standby time (``standby_seconds``)
and then waits for its start command. Its Autodarts-compatible ``/api/state`` still
reports a running board, so its own ``/api/v1/status`` tells whether it can score.
"""

import httpx

ASLEEP = {"stopped", "standby"}


async def wake(http: httpx.AsyncClient, url: str) -> bool:
    """Send OcheCore's start command when its cameras are stopped or in standby; True if sent."""
    response = await http.get(f"{url}/api/v1/status", timeout=2)
    response.raise_for_status()
    status = response.json()
    if not isinstance(status, dict) or status.get("state") not in ASLEEP:
        return False
    response = await http.post(f"{url}/api/v1/command", json={"command": "start"}, timeout=5)
    response.raise_for_status()
    return True
