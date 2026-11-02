# Oche

Oche runs your darts board from one web app. It installs and runs the local
Autodarts board software as its detection engine, opens Autodarts Play, and connects your Autodarts account so game events
can drive lighting and a caller.

- **Play on Autodarts**: Autodarts Play inside Oche, over local HTTPS.
- **Play on Oche**: choose a game mode and play X01 (301/501) on this device with
  manual darts or a compatible local Autodarts scored-throw API. Automatic offline scoring with the bundled v2 detector is not
  verified; see [local games](docs/LOCAL_GAMES.md).
- **Integrations**: connect your Autodarts account and turn on Visual WLED, WLED
  and Caller. Each integration can follow Autodarts Play, local play,
  or both. Camera Enhancer provides optional previews, focus and exposure controls.
  The Camera Enhancer switch controls sidebar visibility independently of detection.
- **Profiles**: save integration settings, theme and language as profiles and
  switch between them from the sidebar.
- **Languages**: English, French, German and European Portuguese, selected beside
  the theme button. Translation files are bundled for offline use.
- **Panels**: add your own web pages to the sidebar.
- **System**: local Autodarts (setup terminal, logs, start and stop), status and
  in-app updates. Device history defaults to 60 minutes sampled every 30 seconds;
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
3. **System › Autodarts**: set up the board and its cameras in the setup terminal.
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

Python 3.12+ and [uv](https://docs.astral.sh/uv/) are required.

```sh
scripts\dev.bat            # Windows
sh scripts/dev.sh          # Linux/macOS
```

Both install the locked dependencies and serve **http://localhost:8180** with
reload. Add `--launcher` to run through the launcher, which enables restart and
update controls. Autodarts has no Windows build, so the Autodarts page and setup
terminal only work in the Linux container. `dev.bat` runs Python directly on
Windows; it does not start or connect to a Docker container. Its Autodarts status
therefore explains that native detection is unavailable.

To run the container from source with reload:

```sh
docker compose -f docker-compose.yml -f docker-compose.dev.yml up --build
```

It builds `oche:dev` and serves port 8180. The image has no Autodarts; install it
into `./data` once with:

```sh
docker compose -f docker-compose.yml -f docker-compose.dev.yml run --rm   --entrypoint python oche -m app.services.install_module autodarts
```

To try a local image the normal way, build it and point the installer or `.env` at it:

```sh
docker build -t oche:local .
OCHE_IMAGE=oche:local docker compose up -d
```

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
