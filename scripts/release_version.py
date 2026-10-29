"""Detect a project-version change across a push so CI publishes each version once."""

import os
import re
import subprocess
import tomllib
from pathlib import Path

# X.Y.Z with an optional PEP 440 pre-release, for example 0.2.0, 0.3.0a1 or 1.0.0rc2.
VERSION = re.compile(r"(0|[1-9]\d*)\.(0|[1-9]\d*)\.(0|[1-9]\d*)((a|b|rc)(0|[1-9]\d*))?")


def check(version: str) -> str:
    if not VERSION.fullmatch(version):
        raise ValueError("Release versions must use X.Y.Z or X.Y.Z(a|b|rc)N, for example 0.3.0a1")
    return version


def detect(before: str | None, manual: bool = False) -> tuple[str, bool]:
    current = check(tomllib.loads(Path("pyproject.toml").read_text())["project"]["version"])
    previous = None
    if before and before != "0" * 40:
        subprocess.run(["git", "cat-file", "-e", f"{before}^{{commit}}"], check=True)
        files = subprocess.check_output(
            ["git", "ls-tree", "--name-only", before, "--", "pyproject.toml"], text=True
        )
        if files.strip():
            content = subprocess.check_output(["git", "show", f"{before}:pyproject.toml"], text=True)
            previous = tomllib.loads(content)["project"]["version"]
    return current, manual or previous != current


if __name__ == "__main__":
    version, changed = detect(os.environ.get("BEFORE_SHA"), os.environ.get("EVENT_NAME") == "workflow_dispatch")
    with Path(os.environ["GITHUB_OUTPUT"]).open("a") as output:
        output.write(f"changed={str(changed).lower()}\nversion={version}\ntag=v{version}\n")
    print(f"Project version: {version}; publish={changed}")
