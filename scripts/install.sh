#!/usr/bin/env bash
# Remote: curl -fsSL <raw-url>/scripts/install.sh | bash
# Local:  bash scripts/install.sh [OWNER/REPO]
set -euo pipefail

# Subshells keep helper variables and cleanup traps isolated.
install_docker() (
if [[ "$(uname -s)" != "Linux" ]]; then
    echo "This script requires Linux." >&2
    exit 1
fi

sudo_cmd=()
if (( EUID != 0 )); then
    command -v sudo >/dev/null || { echo "Install sudo or run as root." >&2; exit 1; }
    sudo_cmd=(sudo)
fi

command -v curl >/dev/null || { echo "Install curl first, then rerun this script." >&2; exit 1; }
command -v systemctl >/dev/null || { echo "This script requires systemd." >&2; exit 1; }

work_dir=$(mktemp -d)
installer="$work_dir/get-docker.sh"
install_log="$work_dir/install.log"
progress_pid=""
cleanup() {
    if [[ -n "$progress_pid" ]]; then
        kill "$progress_pid" 2>/dev/null || true
        wait "$progress_pid" 2>/dev/null || true
    fi
    rm -f "$installer" "$install_log"
    rmdir "$work_dir"
}
trap cleanup EXIT
trap 'exit 130' INT
trap 'exit 143' TERM

echo "Downloading Docker installer..."
curl -fsSL https://get.docker.com -o "$installer"
# Authenticate before hiding installer output so sudo's prompt stays visible.
if (( EUID != 0 )); then
    sudo -v
fi

show_progress() {
    local elapsed=0 frame=0
    local frames=('|' '/' '-' '\')
    printf '\n\n'
    while true; do
        printf '\033[2A\r\033[2K[%s] Installing Docker packages...\n\r\033[2KElapsed: %02d:%02d — installer running; package output is quiet.\n' \
            "${frames[frame]}" "$((elapsed / 60))" "$((elapsed % 60))"
        sleep 1
        elapsed=$((elapsed + 1))
        frame=$(((frame + 1) % 4))
    done
}

if [[ -t 1 ]]; then
    show_progress &
    progress_pid=$!
else
    echo "Installing Docker packages; waiting for the installer to finish..."
fi

install_status=0
"${sudo_cmd[@]}" sh "$installer" >"$install_log" 2>&1 || install_status=$?
if [[ -n "$progress_pid" ]]; then
    kill "$progress_pid" 2>/dev/null || true
    wait "$progress_pid" 2>/dev/null || true
    progress_pid=""
fi
if (( install_status != 0 )); then
    echo "Docker installation failed (exit $install_status). Installer output:" >&2
    cat "$install_log" >&2
    exit "$install_status"
fi
echo "Docker packages installed. Starting Docker and verifying installation..."

"${sudo_cmd[@]}" systemctl enable --now docker
"${sudo_cmd[@]}" docker run --rm hello-world
"${sudo_cmd[@]}" docker compose version

# Membership in the docker group grants root-level access to the host.
docker_user="${SUDO_USER:-${USER:-}}"
if [[ -n "$docker_user" && "$docker_user" != "root" ]]; then
    "${sudo_cmd[@]}" usermod -aG docker "$docker_user"
    echo "Docker installed. Log out and back in to use Docker without sudo."
else
    echo "Docker installed. Run Docker commands as root or with sudo."
fi
)

# Enumerate nodes directly and probe per-node capabilities using installer sudo.
# Optional roots allow discovery to be checked against fixtures without hardware.
discover_cameras() (
    device_root="${1:-/dev}"
    sys_root="${2:-/sys/class/video4linux}"
    export LC_ALL=C
    if ! command -v v4l2-ctl >/dev/null; then
        echo "Camera probing needs v4l2-ctl. Choose 'all' to mount /dev, or install v4l-utils and retry." >&2
        exit 0
    fi
    shopt -s nullglob
    for device in "$device_root"/video[0-9]*; do
        node="${device##*/}"
        [[ "$node" =~ ^video[0-9]+$ && -e "$device" ]] || continue
        if ! info=$("${sudo_cmd[@]}" v4l2-ctl --device="$device" --info 2>&1); then
            printf 'Cannot probe %s: %s\n' "$device" "$info" >&2
            continue
        fi
        # Global Capabilities include metadata AND capture on both UVC nodes.
        # Device Caps describes only the opened node; use global caps for old drivers.
        caps=$(printf '%s\n' "$info" | awk '
            /^[[:space:]]*Capabilities[[:space:]]*:/ { global=$NF }
            /^[[:space:]]*Device Caps[[:space:]]*:/ { device=$NF }
            END { print (device != "" ? device : global) }
        ')
        if [[ ! "$caps" =~ ^0x[0-9a-fA-F]{1,8}$ ]]; then
            echo "Cannot read capture capabilities for $device; skipping." >&2
            continue
        fi
        # Capture (single/multi-planar), excluding memory-to-memory processors.
        (( (caps & 0x00001001) != 0 && (caps & 0x0000c000) == 0 )) || continue
        driver=$(printf '%s\n' "$info" | awk '/^[[:space:]]*Driver name[[:space:]]*:/ { print $NF; exit }')
        case "$driver" in bcm2835-isp|bcm2835-codec*|rpivid*|rpi-hevc-dec) continue ;; esac
        name=""
        if [[ -r "$sys_root/$node/name" ]]; then
            name=$(cat "$sys_root/$node/name") || name=""
        fi
        if [[ -z "$name" ]]; then
            name=$(printf '%s\n' "$info" | sed -n 's/^[[:space:]]*Card type[[:space:]]*:[[:space:]]*//p' | head -n 1)
        fi
        name="${name//$'\t'/ }"
        name="${name//$'\n'/ }"
        printf '%s\t%s\n' "$device" "${name:-Video device ($node)}"
    done | sort -V
)

configure_cameras() (
override=docker-compose.override.yml
marker='# Generated by Oche camera setup'
if [[ -e "$override" ]] && [[ "$(head -n 1 "$override")" != "$marker" ]]; then
    echo "$override already contains custom settings. Configure its camera devices manually." >&2
    exit 1
fi

mapfile -t cameras < <(discover_cameras)

defaults=()
echo "Detected camera capture nodes (metadata and codec nodes excluded):"
for i in "${!cameras[@]}"; do
    entry="${cameras[i]}"
    device="${entry%%$'\t'*}"
    name="${entry#*$'\t'}"
    recommended=""
    if [[ "${name,,}" == *autodarts* ]]; then
        defaults+=("$((i + 1))")
        recommended=" [recommended]"
    fi
    printf '  %d) %s -> %s%s\n' "$((i + 1))" "$name" "$device" "$recommended"
done
default_choice="${defaults[*]}"
default_choice="${default_choice:-none}"
if (( ${#cameras[@]} == 0 )); then
    echo "No camera capture nodes could be verified. Check probe messages, USB connections, and host camera drivers."
fi
echo "The default is a selection, not a detection result. Generic cameras must be selected by number."
echo "  all) Mount the entire host /dev directory (includes non-camera devices)."

while true; do
    choice=""
    read -r -p "Camera numbers separated by spaces, 'all', or 'none' [${default_choice}]: " choice || choice=""
    choice="${choice:-$default_choice}"
    selected=()
    [[ "$choice" == all ]] && break
    [[ "$choice" == none ]] && break
    read -r -a numbers <<< "$choice"
    valid=true
    for number in "${numbers[@]}"; do
        if [[ ! "$number" =~ ^[1-9][0-9]{0,5}$ ]] || (( number > ${#cameras[@]} )); then
            valid=false
            break
        fi
        device="${cameras[number - 1]%%$'\t'*}"
        if [[ ! "$device" =~ ^/dev/video[0-9]+$ || ! -c "$device" ]]; then
            echo "Device unavailable: $device" >&2
            valid=false
            break
        fi
        [[ " ${selected[*]} " == *" $device "* ]] || selected+=("$device")
    done
    if [[ "$valid" == true ]] && (( ${#selected[@]} > 0 )); then
        break
    fi
    echo "Choose valid numbers from the list, 'all', or 'none'."
done

temp_file=$(mktemp ./camera-config.XXXXXX)
trap 'rm -f "$temp_file"' EXIT
{
    printf '%s\nservices:\n  oche:\n' "$marker"
    if [[ "$choice" == all ]]; then
        printf '    volumes:\n      - "/dev:/dev"\n'
        printf "    device_cgroup_rules:\n      - 'a *:* rwm'\n"
        # Keep the non-root application user, with host device group access.
        # Include standard groups even when their devices are not plugged in yet.
        printf '    group_add:\n'
        {
            find /dev -type c -printf '%G\n' -o -type b -printf '%G\n'
            getent group video dialout render plugdev | cut -d: -f3 || true
        } | sort -un | while read -r gid; do printf '      - "%s"\n' "$gid"; done
    elif (( ${#selected[@]} )); then
        printf '    devices:\n'
        for device in "${selected[@]}"; do
            printf '      - "%s:%s"\n' "$device" "$device"
        done
        # Oche runs as a non-root container user; grant host device group access.
        printf '    group_add:\n'
        for device in "${selected[@]}"; do
            stat -c '%g' "$device"
        done | sort -u | while read -r gid; do printf '      - "%s"\n' "$gid"; done
    else
        printf '    devices: []\n'
    fi
} >"$temp_file"
mv -- "$temp_file" "$override"
echo "Camera mappings saved in $PWD/$override"
echo "Explicit device mappings in the main Compose file remain in effect."
)

main() {
    repo="${1:-${OCHE_REPOSITORY:-mondeggo/oche}}"
    if [[ -n "$repo" && ! "$repo" =~ ^[A-Za-z0-9_.-]+/[A-Za-z0-9_.-]+$ ]]; then
        echo "Usage: bash install.sh [OWNER/REPO]" >&2
        return 1
    fi
    [[ "$(uname -s)" == Linux ]] || { echo "This installer requires Linux." >&2; return 1; }
    command -v curl >/dev/null || { echo "Install curl first." >&2; return 1; }
    command -v systemctl >/dev/null || { echo "This installer requires systemd." >&2; return 1; }
    source_dir=""
    if [[ -n "${BASH_SOURCE[0]:-}" && -f "${BASH_SOURCE[0]}" ]]; then
        source_dir=$(cd -- "$(dirname -- "${BASH_SOURCE[0]}")/.." && pwd -P)
        [[ -f "$source_dir/docker-compose.yml" ]] || source_dir=""
    fi
    # Read prompts from the terminal even when the script arrives through a pipe.
    if ! { exec 3</dev/tty; } 2>/dev/null; then
        echo "Run this installer in an interactive terminal." >&2
        return 1
    fi
    exec 0<&3
    if [[ -z "$source_dir" ]]; then
        export OCHE_IMAGE="${OCHE_IMAGE:-mondeggo/oche:latest}"
    fi
invocation_dir="$PWD"
install_home="$HOME"
if [[ -n "${SUDO_USER:-}" && "$SUDO_USER" != root ]]; then
    install_home=$(getent passwd "$SUDO_USER" | cut -d: -f6)
    [[ -n "$install_home" ]] || { echo "Cannot determine your home directory." >&2; exit 1; }
fi

echo "Where should Oche be installed?"
echo "  1) $install_home/oche (default)"
echo "  2) $invocation_dir/oche"
echo "  3) Custom directory"
while true; do
    location_choice=""
    read -r -p "Choose [1/2/3] (default: 1): " location_choice || location_choice=1
    case "$location_choice" in
        1|"") install_dir="$install_home/oche"; break ;;
        2) install_dir="$invocation_dir/oche"; break ;;
        3)
            read -r -p "Installation directory: " install_dir || exit 1
            if [[ -z "$install_dir" ]]; then
                echo "Enter a directory."
                continue
            fi
            case "$install_dir" in
                '~') install_dir="$install_home" ;;
                '~/'*) install_dir="$install_home/${install_dir:2}" ;;
            esac
            break ;;
        *) echo "Please enter 1, 2, or 3." ;;
    esac
done

mkdir -p -- "$install_dir"
install_dir=$(cd -- "$install_dir" && pwd -P)
if [[ -n "$source_dir" && "$install_dir" != "$source_dir" ]]; then
    # Preserve existing destination configuration and data.
    for item in docker-compose.yml docker-compose.override.yml .env data; do
        if [[ -e "$source_dir/$item" && ! -e "$install_dir/$item" ]]; then
            cp -R -- "$source_dir/$item" "$install_dir/"
        fi
    done
fi
if [[ ! -f "$install_dir/docker-compose.yml" ]]; then
    compose_tmp=$(mktemp "$install_dir/.compose.XXXXXX")
    trap 'rm -f -- "$compose_tmp"' EXIT
    curl -fsSL --retry 3 "https://raw.githubusercontent.com/$repo/main/docker-compose.yml" -o "$compose_tmp"
    mv -- "$compose_tmp" "$install_dir/docker-compose.yml"
    trap - EXIT
fi
project_dir="$install_dir"
cd -- "$install_dir"
# Migrate the original local-image setting while preserving hardware edits.
sed -i 's/^    image: oche:latest[[:space:]]*$/    image: "${OCHE_IMAGE:-oche:latest}"/' docker-compose.yml
echo "App files: $install_dir"
echo "Persistent data (configuration, logs, Autodarts): $install_dir/data"

# Repository installs use its published image; local installs can reuse .env.
image_ref="${OCHE_IMAGE:-}"
if [[ -z "$image_ref" && -f .env ]]; then
    image_ref=$(sed -n 's/^OCHE_IMAGE=//p' .env | tail -n 1)
fi
while [[ ! "$image_ref" =~ ^[a-z0-9._-]+(/[a-z0-9._-]+)+:[a-zA-Z0-9_][a-zA-Z0-9._-]*$ ]]; do
    read -r -p "Published image (mondeggo/oche:latest): " image_ref || exit 1
done

while true; do
    boot_choice=""
    if ! read -r -p "Start Oche on boot? [Y/n] (recommended: Yes): " boot_choice; then
        echo "No input received; using recommended option: Yes."
        boot_choice=y
    fi
    case "$boot_choice" in
        [yY]|[yY][eE][sS]|"") restart_policy=unless-stopped; break ;;
        [nN]|[nN][oO]) restart_policy=no; break ;;
        *) echo "Please enter yes or no." ;;
    esac
done

sudo_cmd=()
if (( EUID != 0 )); then
    command -v sudo >/dev/null || { echo "Install sudo or run as root." >&2; exit 1; }
    sudo_cmd=(sudo)
    sudo -v
fi

if ! command -v docker >/dev/null; then
    echo "Docker is missing. Installing Docker..."
    install_docker
fi

# Sudo allows immediate use before new docker group membership takes effect.
if ! "${sudo_cmd[@]}" docker compose version; then
    echo "Docker Compose is missing. Install the Compose plugin, then rerun this script." >&2
    exit 1
fi

if ! command -v v4l2-ctl >/dev/null && command -v apt-get >/dev/null; then
    echo "Installing v4l-utils to identify camera capture nodes..."
    if ! "${sudo_cmd[@]}" apt-get update || ! "${sudo_cmd[@]}" apt-get install -y v4l-utils; then
        echo "Camera probing installation failed; the 'all' fallback remains available."
    fi
fi
configure_cameras

# Persist the choice for future Compose runs, preserving other .env settings.
env_tmp=$(mktemp "$project_dir/.env.XXXXXX")
trap 'rm -f "$env_tmp"' EXIT
if [[ -f .env ]]; then
    sed '/^[[:space:]]*\(export[[:space:]][[:space:]]*\)\{0,1\}OCHE_RESTART_POLICY[[:space:]]*=/d; /^[[:space:]]*\(export[[:space:]][[:space:]]*\)\{0,1\}OCHE_IMAGE[[:space:]]*=/d' .env >"$env_tmp"
fi
printf '\nOCHE_RESTART_POLICY=%s\n' "$restart_policy" >>"$env_tmp"
printf 'OCHE_IMAGE=%s\n' "$image_ref" >>"$env_tmp"
cat "$env_tmp" > .env
rm -f "$env_tmp"
trap - EXIT
# Ensure an inherited environment variable cannot override this choice.
export OCHE_RESTART_POLICY="$restart_policy"
export OCHE_IMAGE="$image_ref"
"${sudo_cmd[@]}" docker compose config --quiet
"${sudo_cmd[@]}" systemctl enable --now docker
"${sudo_cmd[@]}" docker info >/dev/null

echo "Downloading the published Oche image..."
"${sudo_cmd[@]}" docker compose pull
# The image's oche user has UID/GID 1000. Create writable persistent storage.
if [[ ! -e data ]]; then
    "${sudo_cmd[@]}" install -d -o 1000 -g 1000 -m 0755 data
fi
echo "Starting Oche..."
"${sudo_cmd[@]}" docker compose up -d --no-build
"${sudo_cmd[@]}" docker compose ps
if [[ "$restart_policy" == "unless-stopped" ]]; then
    echo "Start on boot enabled. Manually stopping Oche keeps it stopped across reboots."
else
    echo "Start on boot disabled. Start Oche manually with: sudo docker compose up -d"
fi
echo "Oche containers started. The application may take a moment to initialize."
echo "Oche web UI: http://localhost:8180"
echo "Autodarts board manager: http://localhost:3180"
echo "Camera choices are saved in docker-compose.override.yml."
echo "Configure any serial device in docker-compose.yml."
}

main "$@"
