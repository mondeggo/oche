import subprocess
import threading
import time
from pathlib import Path
from typing import Optional

from app.config import LOG_DIR


class ManagedProcess:
    """Runs and supervises a single long-lived subprocess (replaces systemctl start/stop/status)."""

    def __init__(self, name: str, command: list[str], cwd: Optional[Path] = None, env: Optional[dict] = None):
        self.name = name
        self.command = command
        self.cwd = cwd
        self.env = env
        self._proc: Optional[subprocess.Popen] = None
        self._log_file = LOG_DIR / f"{name}.log"
        self._lock = threading.Lock()

    @property
    def installed(self) -> bool:
        return True

    @property
    def status(self) -> str:
        with self._lock:
            if self._proc is None:
                return "stopped"
            if self._proc.poll() is None:
                return "running"
            return "stopped"

    @property
    def pid(self) -> Optional[int]:
        with self._lock:
            if self._proc and self._proc.poll() is None:
                return self._proc.pid
            return None

    def start(self) -> bool:
        with self._lock:
            if self._proc and self._proc.poll() is None:
                return False
            log_fp = open(self._log_file, "a")
            try:
                self._proc = subprocess.Popen(
                    self.command,
                    cwd=str(self.cwd) if self.cwd else None,
                    env=self.env,
                    stdout=log_fp,
                    stderr=subprocess.STDOUT,
                )
            except OSError as e:
                log_fp.write(f"Failed to start: {e}\n")
                log_fp.close()
                raise RuntimeError(f"Failed to start {self.name}: {e}") from e
            return True

    def stop(self, timeout: float = 5.0) -> bool:
        with self._lock:
            if not self._proc or self._proc.poll() is not None:
                return False
            self._proc.terminate()
            try:
                self._proc.wait(timeout=timeout)
            except subprocess.TimeoutExpired:
                self._proc.kill()
                self._proc.wait(timeout=timeout)
            return True

    def restart(self) -> bool:
        self.stop()
        time.sleep(0.5)
        return self.start()

    def tail_log(self, lines: int = 200) -> str:
        if not self._log_file.exists():
            return ""
        with open(self._log_file, "r", errors="replace") as f:
            content = f.readlines()
        return "".join(content[-lines:])


class ProcessRegistry:
    """Holds all managed processes so routers can look them up by name."""

    def __init__(self):
        self._processes: dict[str, ManagedProcess] = {}

    def register(self, proc: ManagedProcess) -> ManagedProcess:
        self._processes[proc.name] = proc
        return proc

    def get(self, name: str) -> Optional[ManagedProcess]:
        return self._processes.get(name)


registry = ProcessRegistry()
