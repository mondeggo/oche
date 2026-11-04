# Oche

Oche runs your darts board from one web app. It installs and runs the local
Autodarts board software as its detection engine, opens Autodarts Play, and connects your Autodarts account so game events
can drive lighting and a caller.

- **Play**: choose a vision engine (Autodarts, OcheCore or manual darts), then a
  game mode, and play X01 (301/501) on this device. If the chosen engine is stopped,
  use its Start button under Local detection. Only manual darts are offered
  until a vision engine is switched on and installed; OcheCore is listed but cannot
  score until its Autodarts-compatible API exists. Automatic offline scoring with the
  Autodarts v2 detector is not verified; see [local games](docs/LOCAL_GAMES.md).
- **Play on Autodarts**: Autodarts Play inside Oche, over local HTTPS. The Autodarts
  integration adds it below Play while it is switched on.
- **Vision engines** (Integrations): Autodarts, on by default, and OcheCore, an
  offline Rust detector for two to four cameras. Switch them on or off on their
  cards; each has its own page under Integrations to install, start and stop it and
  read its logs. One engine reads the cameras at a time, so starting one stops the
  other. OcheCore's own page opens in a new tab on port 5180. Updates list only the
  integrations that are switched on. Each engine card also offers **Clear cache**
  and **Uninstall**, both with confirmation warnings. Clear cache stops the engine
  and deletes its board settings, calibration and logs; the installed binary stays.
  Uninstall stops the engine and removes all installed versions and rollback history,
  keeping board settings and calibration for reinstallation. Neither action resets
  Oche profiles or the Autodarts account.
- **Integrations**: connect your Autodarts account and turn on Visual WLED, WLED
  and Caller. Each integration can follow Autodarts Play, local play,
  or both. Camera Enhancer provides optional previews, focus and exposure controls.
  The Camera Enhancer switch controls sidebar visibility independently of detection.
- **Profiles**: save integration settings, theme and language as profiles and
  switch between them from the sidebar.
- **Languages**: English, French, German and European Portuguese, selected beside
  the theme button. Translation files are bundled for offline use.
- **Panels**: add your own web pages to the sidebar.
- **System**: status and in-app updates. Device history defaults to 60 minutes sampled every 30 seconds;
  retention and sampling are configurable, and history can be turned off.

Integration pages have direct addresses such as `/integrations/caller` and
`/integrations/cameras`. `/events` shows cloud and local game events, with source
filters and raw local detector monitoring for debugging without a game.

It runs as one Docker container on a Linux board computer (AMD64 or ARM64, such as
a Raspberry Pi), with a single web port and no extra services.

## Install

On the board computer, run:

```sh
curl -fsSL https://raw.githubusercontent.com/mondeggo/oche/main/scripts/install.sh | bash
```

The installer asks for one choice:

1. **Easy** (default): installs to `~/oche`, starts on boot, and gives Oche the
   whole `/dev` directory so cameras and USB devices work even when plugged in
   later.
2. **Detailed**: choose the folder and startup behaviour, and pick individual
   cameras.

It installs Docker if it is missing, pulls `mondeggo/oche:latest`, downloads the
official Autodarts release into `data/modules/autodarts` and starts the container.
Easy installation always includes Autodarts; Detailed asks, with Yes as the default.
Without it, Oche still runs: install it later from **System › Updates** or with
`./oche.sh autodarts`. Linux with systemd, `curl` and `sudo` are required.

## First steps

1. Open `http://<board-computer-ip>` in a browser on your network.
2. **Integrations**: connect your Autodarts account. The sidebar status dots show
   the Autodarts process, local detector readiness and the cloud connection;
   hover them for the cause.
3. **Integrations › Autodarts**: set up the board and its cameras in the setup terminal.
4. **Settings › HTTPS**: turn on HTTPS so Autodarts Play can open inside Oche.
   Local HTTPS works straight away with a browser warning; a domain you own gives a
   trusted certificate.
5. Turn on the integrations you use and adjust them on their pages.

## Manage

Run the helper from the installation folder:

```sh
cd ~/oche
./oche.sh start     # pull the image if possible and start
./oche.sh stop      # stop and remove the container, keep data
./oche.sh restart   # pull and recreate
./oche.sh update    # same as restart
./oche.sh pull      # download the image only
./oche.sh status    # container state
./oche.sh logs      # follow the logs
./oche.sh cameras   # choose cameras, all of /dev, or none
./oche.sh autodarts # install Autodarts if it is missing
```

Camera choices are written to `docker-compose.override.yml`. The helper leaves an
override that you wrote yourself untouched.

## Updates

- **Application updates** appear on **System › Updates**. They replace the app code
  in place and roll back if the new version fails to start. They are published as
  GitHub releases of this repository.
- **Autodarts updates** also appear on **System › Updates**, from the official
  Autodarts stable releases. Autodarts is not part of the image, so image updates
  keep the installed version.
- **Image updates** (`./oche.sh update`) bring a newer Python runtime, dependencies
  and launcher.

## Data and backup

Everything you configure lives in `~/oche/data`:

```text
data/
├── core/          # account, tokens, profiles, lighting, voices and theme
├── autodarts/     # board configuration
├── ochecore/      # OcheCore settings and calibration (board.toml)
├── https/         # certificates and renewal credentials
├── logs/
├── modules/       # installed releases of Oche and Autodarts
├── state/         # launcher requests
└── settings.json
```

Back it up after setting up your board. It holds credentials, so keep backups
private. The container runs as UID/GID 1000 and needs write access to it.

## Configuration

Set these in `~/oche/.env`; [.env.example](.env.example) lists them.

| Variable | Default | Purpose |
|---|---|---|
| `OCHE_IMAGE` | `mondeggo/oche:latest` | Image to run |
| `OCHE_RESTART_POLICY` | `unless-stopped` | Start on boot (`no` to turn off) |
| `OCHE_PORT` | `80` | HTTP port |
| `OCHE_HTTPS_PORT` | `443` | HTTPS port |
| `OCHE_UPDATE_FEED` | latest GitHub release | Application update manifest; empty turns updates off |
| `OCHE_ACME_DNS_URL` | `https://auth.acme-dns.io` | acme-dns server for domain certificates |
| `OCHE_CORE_CLIENT_ID`, `OCHE_CORE_BOARD_ID` | empty | Lock the account and board at startup |
| `OCHE_CORE_LOCAL_AUTODARTS_URL` | `http://127.0.0.1:3180` | Local detector origin; can point to a separate LAN board |

Run `./oche.sh restart` after changing `.env`.

## Development

Docker is the default development environment. The startup message also shows
`--no-docker` for running locally with Python 3.12+ and [uv](https://docs.astral.sh/uv/).

```sh
scripts/dev.sh                          # build and start, http://localhost:8180
scripts/dev.sh up --build -d            # background
scripts/dev.sh logs -f
scripts/dev.sh down                     # keep development data
scripts/dev.sh --no-docker              # local Python with reload
scripts/dev.sh --no-docker --launcher   # local Python with restart/update controls
```

On Windows, `scripts\dev.bat [--launcher]` runs Python locally with uv.
Autodarts has no Windows build; use the Linux container to test detection.

`app/`, `launcher.py` and `oche_runtime.py` are bind-mounted. Python changes in
`app/` reload automatically; restart the container after editing the launcher or
runtime contract. Rebuild after dependency changes. Development uses a separate
`oche-dev` Compose project and named data volume, leaving production data alone.
`OCHE_DEV_PORT` and `OCHE_DEV_HTTPS_PORT` override ports 8180 and 8443.
The helper also loads the repository's camera override when present.

The launcher enables restart, installation, update health checks and rollback.
The release feed stays enabled (override `OCHE_UPDATE_FEED` for a test feed).
Test packages must match the image runtime fingerprint; see [releases](docs/RELEASES.md)
for exporting compatible packages.
Install Autodarts from the UI or with:

```sh
scripts/dev.sh run --rm --entrypoint python oche -m app.services.install_module autodarts
```

Installing an Oche update switches from the checkout to the installed release;
reload is disabled for that release. To return to mounted sources while preserving
other settings and modules:

```sh
scripts/dev.sh stop
scripts/dev.sh run --rm --entrypoint python oche -c 'import oche_runtime as s; s.write_state("oche", {})'
scripts/dev.sh up -d
```

Docker definitions live in `docker/`. Production builds the application release
archive in a separate stage and extracts that artifact into the image's bundled
release directory. There is no `/app/app` checkout in the production image; the
launcher seeds and runs the release from persistent storage. The archive is built
from the current checkout, using the same format as the published update assets.

To test a local production image:

```sh
docker build -f docker/Dockerfile -t oche:local .
OCHE_IMAGE=oche:local docker compose --project-directory . -f docker/compose.yml up -d
```

Installed systems keep `docker-compose.yml` beside `oche.sh` for compatibility.

### Checks

```sh
uv run pytest -q
node --test tests/test_frontend.mjs tests/test_integrations_frontend.mjs
uv run python scripts/check_translations.py
uv run ruff check app/core app/main.py app/templating.py launcher.py oche_runtime.py scripts/package_updates.py scripts/release_version.py
for script in scripts/*.sh; do bash -n "$script"; done
```

If the system temporary folder is restricted, add `--basetemp=.test-tmp-run` to
pytest. Commits follow Conventional Commits.

More detail: [architecture](docs/ARCHITECTURE.md), [releases](docs/RELEASES.md) and
[validation](docs/VALIDATION.md).
