"""A private PTY for the Autodarts setup client (Linux containers)."""

import asyncio
import errno
import os
import signal
import subprocess

from app.services.autodarts import AUTODARTS_BIN


class AutodartsTerminal:
    def __init__(self):
        # Keep POSIX imports here so the rest of Oche also runs on Windows.
        import pty
        import termios

        self.master, slave = pty.openpty()
        self.closed = False
        try:
            termios.tcsetwinsize(slave, (24, 80))
            os.set_blocking(self.master, False)
            self.process = subprocess.Popen(
                [str(AUTODARTS_BIN), "remote", "-H", "127.0.0.1"],
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
