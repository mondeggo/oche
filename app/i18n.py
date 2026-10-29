"""Bundled interface translations; unknown messages retain their English source."""

import json
import re
from functools import lru_cache
from pathlib import Path

LANGUAGES = {"en": "English", "fr": "Français", "de": "Deutsch", "pt-PT": "Português (PT)"}
LOCALES = Path(__file__).parent / "static" / "locales"


@lru_cache(maxsize=4)
def catalogue(language: str) -> dict[str, str]:
    if language == "en" or language not in LANGUAGES:
        return {}
    return json.loads((LOCALES / f"{language}.json").read_text(encoding="utf-8"))


def translate(message: str, language: str = "en", **values) -> str:
    text = catalogue(language).get(message, message)
    return re.sub(r"\{(\w+)\}", lambda match: str(values.get(match[1], match[0])), text)
