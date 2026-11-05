"""Client for the board API that vision engines serve.

Autodarts defines it (``GET /api/state``, ``WS /api/events?type=state``), and any
engine that scores games speaks it, so Play reads every engine through this client.
"""

import re

_WORD = re.compile(r"(?<=[a-z])(?=[A-Z])")


def board_words(value: str) -> str:
    """Name a board status or event the way Autodarts' cloud does.

    Autodarts 2.1.1 and OcheCore send camelCase (``takeoutInProgress``); the cloud
    and older boards send words (``Takeout in progress``). Oche compares words.
    """
    words = _WORD.sub(" ", value.strip()).replace("_", " ").replace("-", " ").lower()
    return words[:1].upper() + words[1:]
