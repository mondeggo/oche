"""Local detector health, independent of games and cloud connectivity."""

import asyncio
import time
from urllib.parse import urlsplit

import httpx

BOARD_URL = "http://127.0.0.1:3180"


class LocalDetection:
    def __init__(self, http: httpx.AsyncClient, url: str = BOARD_URL):
        self.http = http
        self.url = url.rstrip("/")
        self.managed = urlsplit(self.url).hostname in {"127.0.0.1", "localhost", "::1"}
        self.lock = asyncio.Lock()
        self.checked_at: float | None = None
        self.process_key: tuple | None = None
        self.current = self.result("unavailable", False, False, "Detection has not been checked.")

    @staticmethod
    def result(state: str, running: bool, detecting: bool, reason: str) -> dict:
        return {"state": state, "running": running, "detecting": detecting, "reason": reason}

    @classmethod
    def classify(cls, data: object) -> dict:
        if (
            not isinstance(data, dict)
            or type(data.get("running")) is not bool
            or not isinstance(data.get("status"), str)
        ):
            raise ValueError("Invalid local detector state")
        if not data["running"]:
            return cls.result("stopped", False, False, "Local Autodarts detection is stopped.")
        status = data["status"].strip().lower().replace("_", " ").replace("-", " ")
        if status in {
            "throw",
            "ready",
            "ready for throw",
            "takeout",
            "takeout in progress",
            "removing darts",
        }:
            return cls.result("detecting", True, True, "Local Autodarts is running and detecting darts.")
        if status == "calibrating":
            return cls.result("calibrating", True, False, "Local Autodarts is calibrating.")
        if status in {"starting", "started"}:
            return cls.result("starting", True, False, "Local Autodarts is starting detection.")
        return cls.result("waiting", True, False, "Local Autodarts is running but detection is not ready.")

    async def check(self, process: dict) -> None:
        async with self.lock:
            key = (process.get("status"), process.get("pid")) if self.managed else (self.url,)
            if self.managed and process.get("status") != "running":
                state = "unavailable" if process.get("status") == "nofile" else "stopped"
                self.current = self.result(
                    state, False, False, process.get("unavailable_reason") or "Local Autodarts is stopped."
                )
                self.process_key, self.checked_at = key, None
                return
            if (
                key == self.process_key
                and self.checked_at is not None
                and time.monotonic() - self.checked_at < 2
            ):
                return
            self.process_key = key
            try:
                response = await self.http.get(f"{self.url}/api/state", timeout=2)
                response.raise_for_status()
                self.current = self.classify(response.json())
            except (httpx.HTTPError, ValueError):
                self.current = self.result(
                    "unavailable",
                    self.managed,
                    False,
                    "Autodarts is running, but its local detection state is unavailable."
                    if self.managed
                    else "Cannot read local detector readiness. Check the vision engine's page in Integrations.",
                )
            self.checked_at = time.monotonic()

    def status(self) -> dict:
        return dict(self.current)
