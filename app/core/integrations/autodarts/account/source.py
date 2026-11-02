"""Autodarts Play as a game source for the runtime: cloud sign-in, board and match events."""

from .auth import DeviceAuth
from .cloud import CloudConnection
from .config import load_connection


class AutodartsAccount:
    def __init__(self, runtime):
        settings = runtime.settings
        self.config = load_connection(settings)
        self.auth = DeviceAuth(
            runtime.http, settings.api_base_url, self.config.client_id, settings.data_dir / "tokens.json"
        )
        self.cloud = CloudConnection(
            runtime.http, self.auth, self.config.board_id, runtime.bus, settings.reconcile_interval
        )

    @property
    def normalizer(self):
        return self.cloud.normalizer

    @property
    def ready(self) -> bool:
        return self.cloud.state == "connected"

    def run(self):
        return self.cloud.run()

    def pause(self) -> None:
        self.cloud.state = "paused_for_local_game"

    def game_state(self) -> dict:
        return self.cloud.game_state()

    def status(self) -> dict:
        return {"auth": self.auth.status(), "cloud": self.cloud.status()}

    async def close(self) -> None:
        await self.auth.close()
