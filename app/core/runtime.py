import asyncio

import httpx

from app.core.api.ui import UISettings
from app.core.autodarts.auth import DeviceAuth
from app.core.autodarts.cloud import CloudConnection
from app.core.autodarts.detection import LocalDetection
from app.core.autodarts.errors import ConnectionProblem
from app.core.config import ConnectionConfig, Settings
from app.core.events import EventBus, RawEventRecorder
from app.core.integrations.caller.service import Caller
from app.core.integrations.sources import integration_view
from app.core.integrations.wled.service import WLED
from app.core.integrations.wled2.service import WLED2
from app.core.integrations.wled2.service import Layout as WLED2Layout
from app.core.local import LocalGames, LocalOptions
from app.core.profiles import Profiles
from app.core.storage import write_private_json


class Runtime:
    def __init__(self, settings: Settings, http: httpx.AsyncClient, ui: UISettings | None = None):
        self.settings = settings
        self.http = http
        self.debug = RawEventRecorder(settings.data_dir / "debug")
        self.bus = EventBus(recorder=self.debug)
        self.config = settings.connection()
        self.tasks: list[asyncio.Task] = []
        self.lock = asyncio.Lock()
        self.local = LocalGames(settings.data_dir, http, self.bus, settings.local_autodarts_url)
        self.detection = LocalDetection(http, settings.local_autodarts_url)
        self.cloud_task: asyncio.Task | None = None
        self._create_connections()
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
        self.wled2 = WLED2(settings.data_dir / "wled2.json", http, self.bus, self.game_state, self.wled)
        self.profiles.wled2 = self.wled2

    def game_state(self) -> dict:
        if self.local.active:
            return self.local.game_state()
        return self.cloud.game_state()

    def start_cloud(self) -> None:
        if self.local.active:
            self.cloud.state = "paused_for_local_game"
            return
        self.cloud.normalizer.resync()
        self.tasks = [task for task in self.tasks if not task.done()]
        self.cloud_task = asyncio.create_task(self.cloud.run(), name="autodarts-cloud")
        self.tasks.append(self.cloud_task)

    async def start_local(self, options: LocalOptions) -> dict:
        async with self.lock, self.local.lock:
            if self.local.active:
                raise ConnectionProblem("End the current local game before starting another.")
            if self.cloud_task and not self.cloud_task.done():
                self.cloud_task.cancel()
                await asyncio.gather(self.cloud_task, return_exceptions=True)
            try:
                self.local.start(options)
            except Exception:
                self.start_cloud()
                raise
            self.cloud.state = "paused_for_local_game"
            return self.local.status()

    async def stop_local(self) -> dict:
        async with self.lock, self.local.lock:
            if self.local.active:
                self.local.stop()
                self.start_cloud()
            return self.local.status()

    async def configure_wled2(self, layout: WLED2Layout) -> None:
        previous = self.wled.config.model_copy(deep=True)
        switching = layout.enabled and previous.enabled
        if switching:
            await self.profiles.configure_wled(previous.model_copy(update={"enabled": False}))
        try:
            await self.wled2.configure(layout)
        except (Exception, asyncio.CancelledError):
            if switching:
                restore = asyncio.create_task(self.profiles.configure_wled(previous))
                while not restore.done():
                    try:
                        await asyncio.shield(restore)
                    except asyncio.CancelledError:
                        pass
                restore.result()
            raise

    def _create_connections(self) -> None:
        self.auth = DeviceAuth(
            self.http,
            self.settings.api_base_url,
            self.config.client_id,
            self.settings.data_dir / "tokens.json",
        )
        self.cloud = CloudConnection(
            self.http, self.auth, self.config.board_id, self.bus, self.settings.reconcile_interval
        )

    def start(self) -> None:
        self.tasks = [
            asyncio.create_task(self.local.run(), name="autodarts-local"),
            asyncio.create_task(self.local.run_bots(), name="local-bots"),
        ]
        self.start_cloud()
        self.wled.start()
        self.wled2.start()
        self.caller.start()

    async def close(self, *, stop_debug: bool = True) -> None:
        for task in self.tasks:
            task.cancel()
        await asyncio.gather(*self.tasks, return_exceptions=True)
        self.local.reset_detector()
        self.tasks.clear()
        await self.wled.close()
        await self.wled2.close()
        await self.caller.close()
        await self.auth.close()
        if stop_debug:
            await self.debug.stop()

    async def configure(self, config: ConnectionConfig) -> None:
        async with self.lock:
            write_private_json(self.settings.data_dir / "connection.json", config.model_dump())
            await self.close(stop_debug=False)
            if config.client_id != self.config.client_id:
                await self.auth.forget()
            self.config = config
            self._create_connections()
            self.bus.clear()
            self.start()

    async def logout(self) -> None:
        async with self.lock:
            await self.close(stop_debug=False)
            await self.auth.forget()
            self.bus.clear()
            self._create_connections()
            self.start()

    def status(self) -> dict:
        return {
            "auth": self.auth.status(),
            "cloud": self.cloud.status(),
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
                "normalized": self.cloud.normalizer.emitted + self.local.normalizer.emitted,
                "invalid_match_states": self.cloud.normalizer.invalid_states,
                "debug": self.debug.status(),
            },
        }
