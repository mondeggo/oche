"""Detect project-version changes across a push, matching OcheCore releases."""
import os
from pathlib import Path
import re
import subprocess
import tomllib


def detect(before, manual=False):
    current = tomllib.loads(Path("pyproject.toml").read_text())["project"]["version"]
    if not re.fullmatch(r"(0|[1-9]\d*)\.(0|[1-9]\d*)\.(0|[1-9]\d*)", current):
        raise ValueError("Release versions must use X.Y.Z, for example 0.1.1")
    previous = None
    if before and before != "0" * 40:
        subprocess.run(["git", "cat-file", "-e", f"{before}^{{commit}}"], check=True)
        files = subprocess.check_output(["git", "ls-tree", "--name-only", before, "--", "pyproject.toml"], text=True)
        if files.strip():
            content = subprocess.check_output(["git", "show", f"{before}:pyproject.toml"], text=True)
            previous = tomllib.loads(content)["project"]["version"]
    return current, manual or previous != current


if __name__ == "__main__":
    version, changed = detect(os.environ.get("BEFORE_SHA"), os.environ.get("EVENT_NAME") == "workflow_dispatch")
    with Path(os.environ["GITHUB_OUTPUT"]).open("a") as output:
        output.write(f"changed={str(changed).lower()}\nversion={version}\ntag=v{version}\n")
    print(f"Project version: {version}; publish={changed}")
