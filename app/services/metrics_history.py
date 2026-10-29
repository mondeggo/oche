"""Bounded device metrics history, sampled independently of open browser pages."""

import asyncio
import math
import time
from collections import deque

import psutil
from pydantic import BaseModel, ConfigDict, Field, model_validator

from app.services.system_metrics import get_status

FIELDS = (
    "cpu_percent",
    "mem_percent",
    "disk_percent",
    "cpu_temp_c",
    "disk_read_mb_s",
    "disk_write_mb_s",
    "net_recv_mb_s",
    "net_sent_mb_s",
)


class HistoryConfig(BaseModel):
    model_config = ConfigDict(extra="forbid")
    enabled: bool = True
    retention_minutes: int = Field(default=60, ge=5, le=1440, strict=True)
    sample_seconds: int = Field(default=30, ge=5, le=300, strict=True)

    @property
    def capacity(self) -> int:
        return math.ceil(self.retention_minutes * 60 / self.sample_seconds)

    @model_validator(mode="after")
    def bounded(self):
        if self.capacity > 5000:
            raise ValueError(
                "Choose a longer sampling interval or shorter retention (maximum 5,000 samples)."
            )
        return self


class MetricsHistory:
    def __init__(self, config: HistoryConfig):
        self.config = config
        self.samples: deque[dict] = deque(maxlen=config.capacity)
        self.changed = asyncio.Event()
        self.task: asyncio.Task | None = None
        self.error: str | None = None

    def configure(self, config: HistoryConfig) -> None:
        self.config = config
        self.samples = deque(self.samples if config.enabled else [], maxlen=config.capacity)
        self.prune()
        self.changed.set()

    def prune(self, now: float | None = None) -> None:
        cutoff = (time.time() if now is None else now) - self.config.retention_minutes * 60
        while self.samples and self.samples[0]["timestamp"] < cutoff:
            self.samples.popleft()

    def record(self, metrics: dict, now: float | None = None) -> None:
        if not self.config.enabled:
            return
        now = time.time() if now is None else now
        self.prune(now)
        self.samples.append({"timestamp": now, **{key: metrics.get(key) for key in FIELDS}})

    def status(self) -> dict:
        now = time.time()
        self.prune(now)
        return {
            "now": now,
            "config": self.config.model_dump(),
            "capacity": self.config.capacity,
            "samples": list(self.samples),
            "error": self.error,
        }

    def start(self) -> None:
        self.task = asyncio.create_task(self.run(), name="device-metrics-history")

    async def close(self) -> None:
        if self.task:
            self.task.cancel()
            await asyncio.gather(self.task, return_exceptions=True)

    async def run(self) -> None:
        while True:
            self.changed.clear()
            if self.config.enabled:
                try:
                    self.record(get_status())
                    self.error = None
                except (OSError, RuntimeError, psutil.Error):
                    self.error = "Device metrics could not be sampled. Retrying automatically."
            try:
                await asyncio.wait_for(
                    self.changed.wait(), timeout=self.config.sample_seconds if self.config.enabled else 3600
                )
            except TimeoutError:
                pass
