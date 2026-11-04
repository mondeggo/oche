"""The OcheCore setup client, connected to the supervised daemon."""

from app.services.setup_terminal import SetupTerminal

from . import process


class OcheCoreTerminal(SetupTerminal):
    def __init__(self):
        super().__init__([
            str(process.binary()), "--config", str(process.CONFIG),
            "--host", "127.0.0.1", "--port", str(process.PORT), "remote",
        ])
