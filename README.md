# Oche

An all-in-one Docker app with one-line installation for a hassle-free darts setup. Manage Autodarts and AutoGlow/WLED lighting, play online, and keep your tools together in one web interface.

- **Board & Autodarts:** set up your board and manage detection.
- **AutoGlow/WLED:** manage your board lighting.
- **Play:** access online Autodarts games inside Oche.
- **Panels:** add website URLs in Settings to open your own tools from the Panels menu.

## Install

The image is Linux-based (AMD64 and ARM64, including 64-bit Raspberry Pi OS). It can also run on Windows with [Docker Desktop and WSL 2](https://docs.docker.com/desktop/features/wsl/); camera and USB access need extra setup there.

**The installer and commands below are for Linux-based systems.** Use a system with systemd and `curl`, then run this in a terminal as your normal user:

```bash
curl -fsSL https://raw.githubusercontent.com/mondeggo/oche/main/scripts/install.sh | bash
```

Follow the prompts to choose your installation folder, cameras, and startup preference. Docker is installed if needed.

Camera setup probes `/dev/video*` with `v4l2-ctl` using the installer's sudo access.
It selects capture nodes and excludes metadata nodes and Raspberry Pi video
processors. On apt-based systems, it installs `v4l-utils` if needed. Select camera numbers, or
choose `all` to mount the entire host `/dev` directory (including non-camera
devices). The default `none` means no cameras are preselected.

Open **http://localhost:8180**. From another device, use your Oche machine's IP address instead of `localhost`.

For USB lighting controllers, add your serial device using the example in `docker-compose.yml`. Your settings and board data stay in the installation's `data/` folder.

## Update

From your installation folder:

```bash
sudo docker compose pull
sudo docker compose up -d --no-build
```

## Development

With Docker running, run from a source checkout:

```bash
bash dev.sh
```

On Windows, run `.\dev.bat` instead. No local Python installation is needed to launch development.

This builds and launches a local image, shows logs, and reloads Python changes automatically. Open **http://localhost:8180**; refresh the browser after editing templates or styles. Press **Ctrl+C** to stop.

Stop any other Oche container first to free ports `8180` and `3180`. Development uses a separate Docker volume for data and includes your local camera overrides when present.

Application code is in `app/`. To run the checks locally:

```bash
python3 -m pip install -r requirements-dev.txt
python3 -m unittest discover -s tests
```
