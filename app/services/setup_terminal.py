"""Private PTYs and the shared WebSocket transport for engine setup clients."""

import asyncio
import errno
import json
import os
import signal
import subprocess
from contextlib import suppress
from threading import BoundedSemaphore

import anyio
from fastapi import WebSocket, WebSocketDisconnect

from app.security import same_origin

terminal_slots = BoundedSemaphore(4)


class SetupTerminal:
    def __init__(self, command):
        # Keep POSIX imports here so the rest of Oche also runs on Windows.
        import pty
        import termios

        self.master, slave = pty.openpty()
        self.closed = False
        try:
            termios.tcsetwinsize(slave, (24, 80))
            os.set_blocking(self.master, False)
            self.process = subprocess.Popen(
                command,
                stdin=slave, stdout=slave, stderr=slave,
                env={**os.environ, "TERM": "xterm-256color", "COLORTERM": "truecolor"},
                start_new_session=True,
            )
        except BaseException:
            os.close(self.master)
            raise
        finally:
            os.close(slave)

    async def read(self) -> bytes:
        while True:
            try:
                return os.read(self.master, 16384)
            except BlockingIOError:
                if self.process.poll() is not None:
                    return b""
                await asyncio.sleep(0.02)
            except OSError as error:
                if error.errno == errno.EIO:  # Slave closed when the client exited.
                    return b""
                raise

    async def write(self, data: str) -> None:
        pending = data.encode("utf-8")
        while pending:
            try:
                written = os.write(self.master, pending)
                pending = pending[written:]
            except BlockingIOError:
                await asyncio.sleep(0.02)

    def resize(self, rows: int, cols: int) -> None:
        import termios

        termios.tcsetwinsize(self.master, (rows, cols))
        # The client runs in its own session and may have no controlling TTY.
        if self.process.poll() is None:
            try:
                os.killpg(self.process.pid, signal.SIGWINCH)
            except ProcessLookupError:
                pass

    async def close(self) -> None:
        if self.closed:
            return
        self.closed = True
        os.close(self.master)
        # Also stop children when the client has already exited.
        try:
            os.killpg(self.process.pid, signal.SIGTERM)
        except ProcessLookupError:
            pass
        if self.process.poll() is None:
            try:
                await asyncio.to_thread(self.process.wait, timeout=2)
            except subprocess.TimeoutExpired:
                try:
                    os.killpg(self.process.pid, signal.SIGKILL)
                except ProcessLookupError:
                    pass
                await asyncio.to_thread(self.process.wait)


async def serve_terminal(websocket: WebSocket, process, factory, label: str):
    scheme = "https" if websocket.url.scheme == "wss" else "http"
    if not same_origin(websocket.headers.get("origin", ""), scheme, websocket.headers.get("host", "")):
        await websocket.close(code=1008)
        return
    await websocket.accept()
    terminal = None
    tasks = []
    acquired = False
    try:
        if os.name != "posix":
            await websocket.send_json({"error": "The setup terminal requires the Linux Oche container."})
            return
        if process.get_status()["status"] != "running":
            await websocket.send_json({"error": f"Start {label} above, then reconnect."})
            return
        acquired = terminal_slots.acquire(blocking=False)
        if not acquired:
            await websocket.send_json({"error": "All four setup sessions are in use. Close another terminal and reconnect."})
            return
        terminal = factory()

        async def output():
            while data := await terminal.read():
                await websocket.send_bytes(data)

        async def input_events():
            while True:
                frame = await websocket.receive()
                if frame["type"] == "websocket.disconnect":
                    return
                raw = frame.get("text")
                if not isinstance(raw, str) or len(raw) > 65536:
                    raise ValueError("Terminal input too large")
                message = json.loads(raw)
                if not isinstance(message, dict):
                    raise ValueError("Invalid terminal message")
                if message.get("type") == "input" and isinstance(message.get("data"), str):
                    await terminal.write(message["data"])
                elif message.get("type") == "resize":
                    rows, cols = message.get("rows"), message.get("cols")
                    if type(rows) is not int or type(cols) is not int or not (2 <= rows <= 200 and 2 <= cols <= 500):
                        raise ValueError("Invalid terminal size")
                    terminal.resize(rows, cols)
                else:
                    raise ValueError("Invalid terminal message")

        tasks = [asyncio.create_task(output()), asyncio.create_task(input_events())]
        done, _ = await asyncio.wait(tasks, return_when=asyncio.FIRST_COMPLETED)
        for task in done:
            task.result()
    except WebSocketDisconnect:
        pass
    except (OSError, ValueError, RecursionError):
        with suppress(WebSocketDisconnect, RuntimeError):
            await websocket.send_json({"error": "The terminal session could not continue. Reconnect to try again."})
    finally:
        # Finish reaping the client even if the WebSocket task is cancelled.
        with anyio.CancelScope(shield=True):
            for task in tasks:
                task.cancel()
            await asyncio.gather(*tasks, return_exceptions=True)
            try:
                if terminal:
                    await terminal.close()
            finally:
                if acquired:
                    terminal_slots.release()
            with suppress(WebSocketDisconnect, RuntimeError):
                await websocket.close()

