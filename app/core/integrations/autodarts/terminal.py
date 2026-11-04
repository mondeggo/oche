"""The Autodarts setup client, connected to the supervised daemon."""

from app.core.integrations.autodarts.process import AUTODARTS_BIN
from app.services.module_paths import source
from app.services.setup_terminal import SetupTerminal


class AutodartsTerminal(SetupTerminal):
    def __init__(self):
        super().__init__([
            str(source("autodarts", AUTODARTS_BIN.parent) / "autodarts"), "remote", "-H", "127.0.0.1",
        ])
