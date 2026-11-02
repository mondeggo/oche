"""Install a module's first release without starting it.

The installer and `./oche.sh autodarts` run this in a one-off container, so the
release is verified by the updater and saved in data/modules as the container user.
"""

import argparse
import sys

from app.services.updates import manager, releases


def main(argv=None):
    parser = argparse.ArgumentParser(description="Install a module that Oche manages.")
    parser.add_argument("module", choices=sorted(releases()))
    args = parser.parse_args(argv)
    try:
        version = manager.install_missing(args.module)
    except Exception as error:
        print(f"Could not install {args.module}: {error}", file=sys.stderr)
        return 1
    if version:
        print(f"Installed {args.module} {version}.")
    else:
        print(f"{args.module} is already installed; Updates keeps it current.")
    return 0


if __name__ == "__main__":
    sys.exit(main())
