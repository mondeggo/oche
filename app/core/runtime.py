import asyncio

import httpx

from app.core.api.ui import UISettings
from app.core.board.detection import BOARD_URL, LocalDetection
from app.core.config import Settings
from app.core.errors import ConnectionProblem
from app.core.events import EventBus, RawEventRecorder
from app.core.integrations import registry
from app.core.integrations.caller.service import Caller
from app.core.integrations.registry import Engine
from app.core.integrations.sources import integration_view
from app.core.integrations.wled.service import WLED
from app.core.integrations.wled2.service import WLED2
from app.core.integrations.wled2.service import Layout as WLED2Layout
from app.core.local import LocalGames, LocalOptions
from app.core.profiles import Profiles
from app.core.tournaments import Tournaments
from app.services import engines


class Runtime:
    def __init__(self, settings: Settings, http: httpx.AsyncClient, ui: UISettings | None = None):
        self.settings = settings
        self.http = http
        self.debug = RawEventRecorder(settings.data_dir / "debug")
        self.bus = EventBus(recorder=self.debug)
        self.tasks: list[asyncio.Task] = []
        self.lock = asyncio.Lock()
        self.local = LocalGames(
            settings.data_dir, http, self.bus, self.board_url, gated=self.engine_gates_first_dart
        )
        self.tournaments = Tournaments(settings.data_dir, self.local)
        self.detection = LocalDetection(http, self.board_url)
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
        self.wled2 = WLED2(self.wled, self.profiles)

    def board_url(self) -> str:
        """The board API local games read: the vision engine Play uses, on its own port.

        A detector set with OCHE_CORE_LOCAL_AUTODARTS_URL, such as one on another
        computer, serves every engine choice instead.
        """
        configured = self.settings.local_autodarts_url
        if configured != BOARD_URL:
            return configured
        engine = registry.get(engines.chosen())
        return f"http://127.0.0.1:{engine.port}" if engine and engine.port else configured

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
            self.tournaments.sync()
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
            status = self.local.status()
        # Outside the locks: the engine may take a moment to answer.
        await self.wake_engine(new_game=True)
        return status

    def local_engine(self) -> tuple[Engine | None, str]:
        """The chosen vision engine and its URL while a game reads it, if Oche runs it.

        Only an engine Oche runs on this machine is driven, never a detector set with
        OCHE_CORE_LOCAL_AUTODARTS_URL.
        """
        if not (self.local.active and self.local.saved.options.input == "autodarts"):
            return None, ""
        item = registry.get(engines.chosen())
        url = self.board_url()
        if not item or not item.engine or not item.port or url != f"http://127.0.0.1:{item.port}":
            return None, url
        return item.engine, url

    def engine_gates_first_dart(self) -> bool:
        engine = self.local_engine()[0]
        return bool(engine and engine.hold)

    async def wake_engine(self, *, new_game: bool = False) -> bool:
        """Wake the chosen vision engine while a game reads it, if the engine can sleep.

        A new game also lets the engine drop a visit left from before it. Best effort:
        a failure leaves detection as it is.
        """
        engine, url = self.local_engine()
        if engine is None:
            return False
        try:
            woke = bool(engine.wake and await engine.wake(self.http, url))
        except (httpx.HTTPError, ValueError):
            woke = False
        if new_game and engine.prepare:
            # The cameras may still be opening; the game starts meanwhile.
            self.tasks = [task for task in self.tasks if not task.done()]
            self.tasks.append(asyncio.create_task(self._prepare_engine(engine.prepare, url), name="engine-prepare"))
        return woke

    async def _prepare_engine(self, prepare, url: str) -> None:
        try:
            await prepare(self.http, url)
        except (httpx.HTTPError, ValueError):
            pass

    async def watch_engine(self) -> None:
        """Ask the engine what to fix while a game waits for its first dart."""
        while True:
            await asyncio.sleep(0.5)
            engine, url = self.local_engine()
            if not (engine and engine.hold) or self.local.armed:
                continue
            try:
                hold = await engine.hold(self.http, url)
            except (httpx.HTTPError, ValueError):
                # Without an answer, detection behaves as it did before engines reported one.
                hold = None
            async with self.local.lock:
                if self.local_engine()[0] is engine and not self.local.armed:
                    try:
                        self.local.engine_checked(hold)
                    except ConnectionProblem as exc:
                        self.local.detector_error = str(exc)

    async def keep_engine_awake(self) -> None:
        # OcheCore may fall into standby during a long pause in a game.
        while True:
            await asyncio.sleep(30)
            await self.wake_engine()

    async def stop_local(self) -> dict:
        async with self.lock, self.local.lock:
            self.tournaments.sync()
            if self.local.active:
                self.local.stop()
                self.start_sources()
            return self.local.status()

    def local_status(self) -> dict:
        self.tournaments.sync()
        return self.local.status()

    async def start_tournament_match(self, match_id: str) -> dict:
        async with self.lock, self.local.lock:
            options = self.tournaments.match_options(match_id)
            tournament = self.tournaments.saved
            game = self.local.saved
            if self.local.active:
                if game.tournament_id == tournament.id and game.tournament_match_id == match_id:
                    return self.local_status()
                if game.tournament_id == tournament.id and game.frame.winner >= 0:
                    self.local.stop()
                else:
                    raise ConnectionProblem("End the current local game before starting another.")
            await self._stop_sources()
            try:
                self.local.start(options, tournament_id=tournament.id, tournament_match_id=match_id)
            except Exception:
                self.start_sources()
                raise
            for source in self.sources.values():
                source.pause()
            status = self.local_status()
        await self.wake_engine(new_game=True)
        return status

    async def cancel_tournament(self) -> dict:
        async with self.lock, self.local.lock:
            self.tournaments.sync()
            saved = self.tournaments.saved
            if (
                saved and saved.state == "active" and self.local.active
                and self.local.saved.tournament_id == saved.id
            ):
                self.local.stop()
                self.start_sources()
            self.tournaments.cancel()
            return self.tournaments.status()

    async def configure_wled2(self, layout: WLED2Layout) -> None:
        await self.wled2.configure(layout)

    def _create_sources(self) -> None:
        self.sources = {item.id: item.source(self) for item in registry.active() if item.source}

    def start(self) -> None:
        self.tasks = [
            asyncio.create_task(self.local.run(), name="autodarts-local"),
            asyncio.create_task(self.local.run_bots(), name="local-bots"),
            asyncio.create_task(self.keep_engine_awake(), name="engine-wake"),
            asyncio.create_task(self.watch_engine(), name="engine-watch"),
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
