import asyncio

import httpx

from app.core.api.ui import UISettings
from app.core.board.detection import LocalDetection
from app.core.config import Settings
from app.core.errors import ConnectionProblem
from app.core.events import EventBus, RawEventRecorder
from app.core.integrations import registry
from app.core.integrations.caller.service import Caller
from app.core.integrations.sources import integration_view
from app.core.integrations.wled.service import WLED
from app.core.integrations.wled2.service import WLED2
from app.core.integrations.wled2.service import Layout as WLED2Layout
from app.core.local import LocalGames, LocalOptions
from app.core.profiles import Profiles


class Runtime:
    def __init__(self, settings: Settings, http: httpx.AsyncClient, ui: UISettings | None = None):
        self.settings = settings
        self.http = http
        self.debug = RawEventRecorder(settings.data_dir / "debug")
        self.bus = EventBus(recorder=self.debug)
        self.tasks: list[asyncio.Task] = []
        self.lock = asyncio.Lock()
        self.local = LocalGames(settings.data_dir, http, self.bus, settings.local_autodarts_url)
        self.detection = LocalDetection(http, settings.local_autodarts_url)
        # Game sources come from switched-on integrations, such as Autodarts Play.
        self.sources: dict = {}
        self.source_tasks: dict[str, asyncio.Task] = {}
        self._create_sources()
        self.wled = WLED(settings.data_dir / "wled.json", http, self.bus, self.game_state)
        self.caller = Caller(
            settings.data_dir,
            http,
            self.bus,
            lambda: integration_view(self.caller.config, self.game_state()),
        )
        self.profiles = Profiles(
            settings.data_dir / "profiles.json", self.wled, self.caller, ui or UISettings(settings)
        )
        self.wled2 = WLED2(settings.data_dir / "wled2.json", self.wled)
        self.wled2.migrate(self.profiles)

    def game_state(self) -> dict:
        if self.local.active:
            return self.local.game_state()
        for source in self.sources.values():
            return source.game_state()
        return self.local.game_state()

    def start_sources(self) -> None:
        if self.local.active:
            for source in self.sources.values():
                source.pause()
            return
        self.tasks = [task for task in self.tasks if not task.done()]
        for name, source in self.sources.items():
            source.normalizer.resync()
            task = asyncio.create_task(source.run(), name=f"{name}-source")
            self.source_tasks[name] = task
            self.tasks.append(task)

    async def _stop_sources(self) -> None:
        tasks = [task for task in self.source_tasks.values() if not task.done()]
        for task in tasks:
            task.cancel()
        await asyncio.gather(*tasks, return_exceptions=True)
        self.source_tasks.clear()

    async def start_local(self, options: LocalOptions) -> dict:
        async with self.lock, self.local.lock:
            if self.local.active:
                raise ConnectionProblem("End the current local game before starting another.")
            await self._stop_sources()
            try:
                self.local.start(options)
            except Exception:
                self.start_sources()
                raise
            for source in self.sources.values():
                source.pause()
            return self.local.status()

    async def stop_local(self) -> dict:
        async with self.lock, self.local.lock:
            if self.local.active:
                self.local.stop()
                self.start_sources()
            return self.local.status()

    async def configure_wled2(self, layout: WLED2Layout) -> None:
        await self.wled2.configure(layout)

    def _create_sources(self) -> None:
        self.sources = {item.id: item.source(self) for item in registry.active() if item.source}

    def start(self) -> None:
        self.tasks = [
            asyncio.create_task(self.local.run(), name="autodarts-local"),
            asyncio.create_task(self.local.run_bots(), name="local-bots"),
        ]
        self.start_sources()
        self.wled.start()
        self.caller.start()

    async def close(self, *, stop_debug: bool = True) -> None:
        for task in self.tasks:
            task.cancel()
        await asyncio.gather(*self.tasks, return_exceptions=True)
        self.local.reset_detector()
        self.tasks.clear()
        await self.wled.close()
        await self.caller.close()
        self.source_tasks.clear()
        for source in self.sources.values():
            await source.close()
        if stop_debug:
            await self.debug.stop()

    async def restart(self, change=None) -> None:
        """Stop, apply a change (such as new account settings), then start fresh sources."""
        async with self.lock:
            await self.close(stop_debug=False)
            if change:
                await change()
            self.bus.clear()
            self._create_sources()
            self.start()

    def status(self) -> dict:
        sources = {}
        for source in self.sources.values():
            sources.update(source.status())
        normalizers = [source.normalizer for source in self.sources.values()]
        return {
            **sources,
            "local_detection": self.detection.status(),
            "local": {
                "active": self.local.active,
                "input": self.local.saved.options.input if self.local.saved else None,
                "detector": self.local.status()["detector"],
                "feed": self.local.status()["feed"],
            },
            "events": {
                "received": self.bus.raw_received,
                "subscribers": len(self.bus.normalized_queues),
                "raw_subscribers": len(self.bus.raw_queues),
                "dropped_deliveries": self.bus.dropped,
                "raw_dropped_deliveries": self.bus.raw_dropped,
                "normalized": sum(item.emitted for item in normalizers) + self.local.normalizer.emitted,
                "invalid_match_states": sum(item.invalid_states for item in normalizers),
                "debug": self.debug.status(),
            },
        }
