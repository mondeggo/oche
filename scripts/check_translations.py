"""Check catalogue coverage and interpolation tokens without running the app."""

import ast
import json
import re
from collections import Counter
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
LOCALES = ROOT / "app/static/locales"
MESSAGE = re.compile(r"\b(?:tr|_)\(\s*(\"(?:[^\"\\]|\\.)*\"|'(?:[^'\\]|\\.)*')")
TOKENS = re.compile(r"\{(\w+)\}")


def check() -> list[str]:
    errors = []
    english = json.loads((LOCALES / "en.json").read_text(encoding="utf-8"))
    for language in ("fr", "de", "pt-PT"):
        translated = json.loads((LOCALES / f"{language}.json").read_text(encoding="utf-8"))
        for key in sorted(set(english) - set(translated)):
            errors.append(f"{language}: missing {key!r}")
        for key, value in translated.items():
            if key not in english:
                errors.append(f"{language}: unknown message {key!r}")
            if not isinstance(value, str) or not value.strip():
                errors.append(f"{language}: empty translation for {key!r}")
            elif Counter(TOKENS.findall(key)) != Counter(TOKENS.findall(value)):
                errors.append(f"{language}: interpolation tokens differ for {key!r}")
    paths = [
        *ROOT.glob("app/templates/*.html"),
        *ROOT.glob("app/static/js/*"),
        *ROOT.glob("app/static/core/*"),
        # Integrations keep their own pages and scripts.
        *ROOT.glob("app/core/integrations/*/templates/*.html"),
        *ROOT.glob("app/core/integrations/*/static/*"),
    ]
    for path in paths:
        if path.suffix not in {".html", ".js", ".mjs"}:
            continue
        for match in MESSAGE.finditer(path.read_text(encoding="utf-8")):
            try:
                key = ast.literal_eval(match[1])
            except (SyntaxError, ValueError):
                continue
            if key not in english:
                errors.append(f"{path.relative_to(ROOT)}: message missing from catalogue: {key!r}")
    return errors


if __name__ == "__main__":
    failures = check()
    if failures:
        raise SystemExit("\n".join(failures))
    print("All four language catalogues have matching messages and interpolation tokens.")
