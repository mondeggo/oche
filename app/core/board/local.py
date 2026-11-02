"""Read the detector's local state feed without a cloud account or board commands."""

import asyncio
import json
from contextlib import asynccontextmanager
from datetime import UTC, datetime
from urllib.parse import urlsplit, urlunsplit

import httpx
from websockets.asyncio.client import connect
from websockets.exceptions import InvalidHandshake

from app.core.board.detection import BOARD_URL
from app.core.errors import ConnectionProblem


class LocalBoardConnection:
    def __init__(self, http: httpx.AsyncClient, url: str = BOARD_URL):
        self.http, self.url = http, url.rstrip("/")
        parts = urlsplit(self.url)
        self.events_url = urlunsplit(
            ("wss" if parts.scheme == "https" else "ws", parts.netloc, "/api/events", "type=state", "")
        )
        self.transport: str | None = None
        self.received = 0
        self.invalid_messages = 0
        self.last_message_at: str | None = None

    def status(self) -> dict:
        return {
            "url": self.url,
            "transport": self.transport,
            "received": self.received,
            "invalid_messages": self.invalid_messages,
            "last_message_at": self.last_message_at,
        }

    def accept(self, state: object) -> dict:
        if not isinstance(state, dict) or not isinstance(state.get("status"), str):
            raise ConnectionProblem("The local detector returned an invalid board state.")
        self.received += 1
        self.last_message_at = datetime.now(UTC).isoformat()
        return state

    async def read(self) -> dict:
        response = await self.http.get(f"{self.url}/api/state", timeout=2)
        response.raise_for_status()
        return self.accept(response.json())

    @asynccontextmanager
    async def session(self):
        socket = None
        states = None
        try:
            try:
                socket = await connect(
                    self.events_url,
                    open_timeout=2,
                    close_timeout=1,
                    max_size=262144,
                    proxy=None,
                )
            except (OSError, TimeoutError, InvalidHandshake):
                # Some compatible detectors expose only the HTTP state endpoint.
                self.transport = "polling"
            else:
                self.transport = "websocket"
            states = self.states(socket)
            yield states
        finally:
            self.transport = None
            if states:
                await states.aclose()
            if socket:
                await socket.close()

    async def states(self, socket):
        # Subscribe first, then take a baseline. An existing visit never becomes
        # new throws just because this application connected to the detector.
        baseline = await self.read()
        if socket is not None:
            # Events buffered during the HTTP read belong to the baseline too.
            # Do not replay an old visit after a newer empty snapshot.
            while True:
                try:
                    message = await asyncio.wait_for(socket.recv(), timeout=0.01)
                except TimeoutError:
                    break
                state = self.parse_message(message)
                if state is not None:
                    baseline = state
        yield baseline
        while True:
            if socket is None:
                await asyncio.sleep(0.2)
                yield await self.read()
                continue
            try:
                message = await asyncio.wait_for(socket.recv(), timeout=2)
            except TimeoutError:
                # Reconcile cumulative snapshots and allow a stopped game to
                # release its socket even when the detector sends no events.
                yield await self.read()
                continue
            state = self.parse_message(message)
            if state is not None:
                yield state

    def parse_message(self, message: str | bytes) -> dict | None:
        try:
            packet = json.loads(message)
            if not isinstance(packet, dict) or packet.get("type") != "state":
                return None
            return self.accept(packet.get("data"))
        except (ValueError, ConnectionProblem):
            self.invalid_messages += 1
            raise ConnectionProblem("The local detector returned an invalid board state.") from None
