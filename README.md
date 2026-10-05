# Oche

An all-in-one Docker app with one-line installation for a hassle-free darts setup. Manage Autodarts and AutoGlow/WLED lighting, play online, and keep your tools together in one web interface.

- **Supervisor:** manage headless detection and set up Autodarts in an interactive terminal.
- **AutoGlow/WLED:** manage your board lighting.
- **Play:** access online Autodarts games inside Oche.
- **Panels:** add website URLs in Settings to open your own tools from the Panels menu.

Use the sun or moon button in the header to switch between dark and light themes.
Oche initially follows your device's appearance and remembers your choice in this
browser for each Oche address. The theme also updates open Oche pages and the
setup terminal. Embedded websites use their own appearance settings.

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
then recreate the container. Autodarts v2 uses port `3180` for its API. These ports must
be available on the host. Existing Compose files without `OCHE_PORT` retain
port `8180`.

### HTTPS for embedded Autodarts Play

To use the embedded [Autodarts Play website](https://play.autodarts.com/) on your
local network, open Oche over HTTPS.
HTTP also works at `127.0.0.1` or `localhost` on the device running Oche.

Open **Settings → Local HTTPS → Configure HTTPS** (`/config/https`). The page
shows the active method, address, certificate expiry, and any errors.

**Easy method: local IP address with a browser warning.** Choose
**Enable local HTTPS**. Oche generates a self-signed certificate, saves it in
`data/https/local.pem`, and starts HTTPS immediately. Click **Open HTTPS** to
switch this tab, then accept the browser's
certificate warning with **Advanced → Continue** (wording varies by browser).
Other devices can open `https://<oche-host-ip>` and accept the warning there too.
No domain, browser extension, or certificate installation is needed for this method.

**Trusted domain certificate with acme-dns:**

1. Reserve Oche's local IP in your router's DHCP settings. The setup page suggests
   an IP and lets you correct it, including when Docker reports a VM address.
2. Enter your domain, local IPv4 address, and email, accept the Let's Encrypt
   Subscriber Agreement, and select **Prepare DNS records**. Oche registers with
   acme-dns automatically and keeps the credentials on the server. This works
   from HTTP; you do not need to enable local HTTPS first. Preparation does not
   change the current HTTPS method. Repeating it reuses the saved registration.
3. At your existing DNS provider, add the displayed **A record** pointing to
   Oche's local IP and **CNAME** delegating `_acme-challenge.<domain>` to the unique
   acme-dns address. These records stay unchanged during renewals. In Cloudflare,
   use **DNS only** (gray cloud). This setup uses IPv4; remove any AAAA record for
   this hostname. If your router filters domains resolving to private IPs, add a
   local DNS entry for the domain.
4. Select **Verify and activate domain HTTPS**. Oche checks the public DNS records,
   then uses Certbot with an acme-dns authentication hook to obtain the certificate.
   Successful issuance activates domain HTTPS automatically. Open the **domain
   address** to use Play without certificate warnings; accessing the IP
   still uses the local certificate. A failed request leaves the current method running.

No inbound router ports are needed. Issuance and renewal require outbound access
to the acme-dns API and Let's Encrypt over HTTPS, plus public DNS lookups. No DNS
provider API token or manual service signup is required. The domain can stay with
its current DNS provider. See the [acme-dns documentation](https://github.com/acme-dns/acme-dns#usage).

The default validation service is `https://auth.acme-dns.io`, a free public testing
instance. Its operator can validate certificates for the delegated hostname;
availability and stored registrations are not guaranteed. Under **About the
validation service and renewals**, **Create replacement CNAME** can recover a lost
registration. Update the CNAME in your DNS and verify again; the currently served
certificate remains in use during this process. Administrators can set
`OCHE_ACME_DNS_URL` to another trusted HTTPS acme-dns server before preparing a new
registration. Existing registrations remain bound to their original service.

Oche checks the active domain certificate at startup and every **12 hours**.
Certbot decides when renewal is due using the CA's renewal information or its
expiry rules; checks do not force a fresh certificate each time. Successful
renewals reload TLS without restarting Oche. Failed requests preserve the existing
certificate and show an error with a retry control. Failed automatic checks retry
after **one hour**. Switching to the local method
or disabling HTTPS stops automatic domain renewal checks.

The acme-dns credentials are stored in a private, domain-specific `.credentials.json` file under `data/https/`,
separate from the general config, and is never returned by the settings API.
Certbot accounts, certificates, renewal configuration, and private logs are in
`data/https/acme/`. Keep the entire `data/https/` directory private and back it up
with your persistent data. A rebuilt image is required for the bundled Certbot
and DNS dependencies. Live issuance requires your own domain and the two DNS records.

The settings and certificates survive restarts. Turning HTTPS off keeps the
certificate for reuse; turning it off from HTTPS returns this tab to HTTP.
AutoGlow automatically uses Oche's connection, including HTTPS, for its controls,
live updates, and Supervisor preview. No separate AutoGlow certificate is needed.
Custom panels need their own HTTPS address when Oche uses HTTPS. For sites that
only support HTTP, select **Open in a new tab** in the panel's settings.

Port `443` must be free. To use another port, set `OCHE_HTTPS_PORT=8443` in `.env`
and recreate the container (older Compose files also need the environment entry
from `docker-compose.yml`). Settings shows the resulting address. Startup or
generation failures appear in Settings and leave HTTP available. The local certificate
is reused until it is invalid or within seven days of expiry, when it is replaced
on the next HTTPS start. A replaced certificate needs another browser exception.
Keep `data/https/local.pem` private: it contains the private key as well as the certificate.

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

### Autodarts v2 headless

Images use the official stable v2 headless release (2.0.2 when this integration
was updated), resolved for AMD64 and ARM64 during the image build. Oche runs
`autodarts run` and manages its lifecycle. Updates arrive through the Oche image.

V2 replaces the browser Board Manager on port 3180 with a terminal interface.
Start Autodarts in **Supervisor**, then open its **Autodarts** tab to use the
interactive setup terminal. Use the keyboard to sign in, claim the board, set up
cameras, and calibrate. The terminal reconnects to the existing daemon; closing
the page leaves detection running. A **Reconnect** button opens a new setup session.

On phones and tablets, use the menu icon to reach navigation. Tap **Keys** in the
terminal to reveal arrows, Tab, Esc, and Enter, or the keyboard icon to type. Rotating
the device resizes the terminal without reconnecting. Embedded external pages
(Play, AutoGlow, and custom panels) use their own layouts inside the available space.

You can also open the same interface from a terminal on the Docker host:

```bash
docker exec -it oche autodarts remote -H 127.0.0.1
```

From another machine with the headless client installed, use
`autodarts remote -H <oche-host-ip>`. Oche already starts the daemon; do not
install another Autodarts service inside the container.

The daemon uses `~/.config/autodarts/config.toml`. In the container that directory
links to persistent storage or the reused host configuration. The adjacent
`ad-board.log` remains in that directory. The browser terminal runs the fixed
Autodarts remote client over Oche's WebSocket connection and requires Linux.
Up to four setup clients can connect at once.

Oche has no login or authentication: anyone who can reach its port can control
the board and read logs. Keep access restricted to a trusted network, or use
an authenticated reverse proxy for remote access. Browser-origin checks prevent
cross-site writes but do not authenticate API clients. Reverse proxies must
preserve the public Host and scheme and forward WebSocket connections.

Back up your existing configuration before upgrading. The official migration
reuses sign-in from `~/.config/autodarts`; check camera setup and calibration
afterwards. AutoGlow connectivity with v2 still needs validation on a real board.
See the [official headless guide](https://docs.autodarts.com/getting-started/detection/headless-installation/).

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
backups are stored in `data/autoglow/`. Update AutoGlow through the Oche image.
The **Autodarts** tab inside Supervisor opens board setup; **AutoGlow 2** in the
navigation opens its lighting configuration.
