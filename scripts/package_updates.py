"""Export verified, architecture-specific bundles from a prepared runtime image."""
import argparse
import hashlib
import importlib.metadata
import json
from pathlib import Path
import platform
import tarfile


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--runtime-only", action="store_true")
    parser.add_argument("--output", default="/output")
    parser.add_argument("--base-url")
    args = parser.parse_args()
    runtime_file = Path("/opt/oche-runtime-id")
    if args.runtime_only:
        # Shared dependencies cannot be changed by application updates. OcheCore
        # carries its isolated environment; the shared runtime is fingerprinted.
        packages = sorted(f"{d.metadata['Name'].lower()}=={d.version}" for d in importlib.metadata.distributions())
        contract = "oche-runtime-1\n" + platform.python_version() + "\n" + "\n".join(packages)
        runtime_file.write_text(hashlib.sha256(contract.encode()).hexdigest())
        for source in Path("/opt/oche-bundles").iterdir():
            (source / "RUNTIME").write_text(runtime_file.read_text())
        return
    if not args.base_url or not args.base_url.startswith("https://"):
        parser.error("--base-url must be the HTTPS release asset directory")
    output = Path(args.output)
    output.mkdir(parents=True, exist_ok=True)
    arch = {"x86_64": "amd64", "aarch64": "arm64"}[platform.machine()]
    entries = {}
    for source in sorted(Path("/opt/oche-bundles").iterdir()):
        display_version = (source / "VERSION").read_text().strip()
        revision_file = source / "REVISION"
        revision = revision_file.read_text().strip() if revision_file.exists() else None
        version = revision or display_version
        name = f"{source.name}-{version}-linux-{arch}.tar.gz"
        archive = output / name
        with tarfile.open(archive, "w:gz", dereference=True) as bundle:
            for child in sorted(source.iterdir()):
                bundle.add(child, arcname=child.name)
        with archive.open("rb") as stream:
            digest = hashlib.file_digest(stream, "sha256").hexdigest()
        entries[source.name] = {"version": version, "display_version": display_version,
                                "revision": revision, "sha256": digest,
                                "url": args.base_url.rstrip("/") + "/" + name,
                                "runtime": runtime_file.read_text().strip()}
    (output / f"updates-{arch}.json").write_text(json.dumps({"schema": 1, "platforms": {"linux-" + arch: entries}}, indent=2))


if __name__ == "__main__":
    main()
