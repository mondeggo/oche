# Oche

An all-in-one Docker app with one-line installation for a hassle-free darts setup. Manage Autodarts and AutoGlow/WLED lighting, play online, and keep your tools together in one web interface.

- **Board & Autodarts:** set up your board and manage detection.
- **AutoGlow/WLED:** manage your board lighting.
- **Play:** access online Autodarts games inside Oche.
- **Panels:** add website URLs in Settings to open your own tools from the Panels menu.

## Install

The image is Linux-based (AMD64 and ARM64, including 64-bit Raspberry Pi OS). It can also run on Windows with [Docker Desktop and WSL 2](https://docs.docker.com/desktop/features/wsl/); camera and USB access need extra setup there.

**The installer and commands below are for Linux-based systems.** Use a system with systemd and `curl`.

If `curl` is missing, install it on Debian, Ubuntu, or Raspberry Pi OS:

```bash
sudo apt update
sudo apt install -y curl
```

Then run the installer in a terminal as your normal user:

```bash
curl -fsSL https://raw.githubusercontent.com/mondeggo/oche/main/scripts/install.sh | bash
```

The installer first offers two modes. Docker is installed if needed.

- **Easy (default):** installs in `~/oche`, enables startup on boot, and mounts
  the full host `/dev` directory with device permissions for cameras, serial
  controllers, and devices connected later. Uses `mondeggo/oche:latest` unless
  an image is already configured.
- **Detailed:** lets you choose the installation folder, startup preference,
  and camera access.

Detailed camera setup probes `/dev/video*` with `v4l2-ctl` using administrator access.
It selects capture nodes and excludes metadata nodes and Raspberry Pi video
processors. On apt-based systems, it installs `v4l-utils` if needed. Select camera numbers, or
choose `all` to mount the entire host `/dev` directory (including non-camera
devices) and grant device access, including devices connected later. Compose
already mounts `/dev`; choose `all` for hot-plug support. Numbered selections
grant access to those cameras. `none` adds no device permissions; it does not
remove the base `/dev` mount.

Open **http://localhost**. From another device, use your Oche machine's IP address instead of `localhost`.

Compose uses host networking so Autodarts can access the host network directly.
Oche listens on port `80`; set `OCHE_PORT=8180` in `.env` to use another port,
then recreate the container. Autodarts still uses port `3180`. These ports must
be available on the host. Existing Compose files without `OCHE_PORT` retain
port `8180`.

For USB lighting controllers, choose `all` during device setup, or configure an
explicit `devices` mapping and the device's host group ID in `group_add` in
`docker-compose.override.yml`. Mounting `/dev` alone does not grant the non-root
application device access. Your settings and board data stay in the installation's `data/` folder.

If an existing Autodarts configuration is found at
`~/.config/autodarts/config.toml`, the installer reuses it by default. This is a
read/write mount of that directory, so changes in Oche also update the existing
configuration. Stop the host's Autodarts service before starting Oche to free
the cameras and port `3180`.

To disable reuse and use Oche's separate configuration, add this to `.env` in
the installation folder, then run `./oche.sh restart`:

```dotenv
OCHE_REUSE_AUTODARTS_CONFIG=false
```

Set it to `true` (the default) to reuse the detected host configuration again.
The host directory stays mounted but is unused when the flag is `false`. Existing
installations need to rerun the updated installer once to generate the
configurable mount. If no existing setup is detected, Oche already uses
`data/autodarts` by default.

Compose mounts the Linux Docker host's `/etc/localtime` read-only so Oche uses
the host's timezone. Development inherits this mount. With Docker Desktop, the
host is its Linux VM, whose timezone may differ from Windows. Existing installs
keep their Compose file: add the `/etc/localtime` mount from this repository and
run `docker compose up -d` to apply it.

## Manage Oche

The installer includes `oche.sh` to start, stop, restart, and update the container.
In a repository checkout, use `bash scripts/oche.sh <command>` instead;
the installer places this helper at the installation root for convenience.
It uses the image configured
in `.env`/Compose and includes `docker-compose.override.yml` for camera mappings.
Run it from the installation folder:

```bash
./oche.sh -h            # Show all commands and help (also --help)
./oche.sh start         # Pull the latest image and start
./oche.sh stop          # Remove containers, keeping persistent data
./oche.sh update        # Pull the latest image and recreate the container
./oche.sh restart       # Pull the latest image and recreate the container
./oche.sh pull          # Download the image without restarting
./oche.sh cameras       # Change camera/device access and recreate the container
```

Use `./oche.sh cameras` to switch between individual camera numbers, `all`
(full `/dev` access, including hot-plug devices), and `none` (no added camera
permissions). It reruns camera detection and applies the selection without
downloading a new image. Camera probing may request administrator access.
Custom override files are left untouched; edit those manually. Older
installations need to rerun the updated installer once to install this command.

GitHub Actions builds and publishes the images. If pulling fails, the helper
uses an existing local image or reports an error if none is available.
Docker Compose v2 and permission to access Docker are required. If the installer
added you to the Docker group, log out and back in before running these commands.
If `./oche.sh` cannot run because the file lacks execute permission, use
`bash oche.sh start` (or another command) from the folder containing the script.

## Development

Local image builds need a GitHub token with **Contents: read** access to the
private `mondeggo/AutoGlow2` repository. Copy `.env.example` to `.env` at the
repository root and fill in `AUTOGLOW_TOKEN`:

```dotenv
AUTOGLOW_TOKEN=your_github_token
```

Compose automatically reads `.env`, which is ignored by Git and excluded from
the Docker build context. You can also set the token in your shell environment;
shell values take precedence over `.env`. Compose passes it as a temporary
BuildKit secret; it is not included in the running container.

With Docker running and the token set, open a terminal in your local copy of this repository and run:

```bash
bash scripts/dev.sh
```

On Windows, run `.\scripts\dev.bat` instead. Docker Desktop must have host networking
enabled; device mounts refer to its Linux VM. No local Python installation is needed to launch development.

This builds and launches a local image, shows logs, and reloads Python changes automatically. Open **http://localhost:8180**; refresh the browser after editing templates or styles. Press **Ctrl+C** to stop.


## Supervisor

Open **Supervisor** to start, stop, or restart Autodarts and AutoGlow 2 and
view their logs. AutoGlow runs its configuration server and lighting engine together, with one
Start/Stop/Restart control and combined logs. Configuration, presets, flows, and
backups are stored in `data/autoglow/`. Update AutoGlow through the Oche image. **Autodarts** in the navigation
opens the full board interface; **AutoGlow 2** opens its lighting configuration.
