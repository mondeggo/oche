# syntax=docker/dockerfile:1
FROM python:3.12-slim

ARG TARGETARCH
ARG AUTODARTS_VERSION
ARG AUTOGLOW_VERSION

# Runtime libs Autodarts' bundled browser/vision stack needs, plus udev for
# serial/camera device enumeration.
RUN apt-get update && apt-get install -y --no-install-recommends \
  curl ca-certificates \
  libgl1 libglib2.0-0 libusb-1.0-0 \
  udev libcap2-bin \
  && rm -rf /var/lib/apt/lists/*

# Fetch the pre-compiled Autodarts binary for the container's architecture,
# mirroring what get.autodarts.io does minus the systemd/sudo setup it
# normally performs on a host — Oche supervises the binary as a subprocess
# instead (see app/services/autodarts.py).
RUN set -eux; \
  case "${TARGETARCH:-amd64}" in \
  amd64) AD_ARCH="amd64" ;; \
  arm64) AD_ARCH="arm64" ;; \
  *) echo "Unsupported architecture: ${TARGETARCH}"; exit 1 ;; \
  esac; \
  VERSION="${AUTODARTS_VERSION:-}"; \
  if [ -z "$VERSION" ]; then \
  VERSION=$(curl -fsSL "https://get.autodarts.io/detection/latest/linux/${AD_ARCH}/RELEASES.json" \
  | python -c 'import json, sys; print(json.load(sys.stdin)["currentVersion"].removeprefix("v"))'); \
  fi; \
  echo "$VERSION" | grep -Eq '^[0-9]+\.[0-9]+\.[0-9]+$'; \
  mkdir -p /opt/autodarts; \
  curl -fsSL "https://get.autodarts.io/detection/latest/linux/${AD_ARCH}/autodarts${VERSION}.linux-${AD_ARCH}.tar.gz" \
  | tar -xz -C /opt/autodarts; \
  chmod +x /opt/autodarts/autodarts

# CI pins the SHA; local builds default to the repository's default branch.
# Credentials are mounted only for this step and never saved in an image layer.
RUN --mount=type=secret,id=autoglow_token,required=true set -eu; \
  AG_TOKEN=$(cat /run/secrets/autoglow_token); \
  test -n "$AG_TOKEN" || { echo "AutoGlow build token is empty." >&2; exit 1; }; \
  AG_VERSION="${AUTOGLOW_VERSION:-}"; \
  if [ -z "$AG_VERSION" ]; then \
    curl -fsSL --retry 3 -H "Authorization: Bearer $AG_TOKEN" \
      "https://api.github.com/repos/mondeggo/AutoGlow2/commits/HEAD" -o /tmp/autoglow-ref.json; \
    AG_VERSION=$(python -c 'import json; print(json.load(open("/tmp/autoglow-ref.json"))["sha"])'); \
  fi; \
  echo "$AG_VERSION" | grep -Eq '^[0-9a-f]{40}$'; \
  mkdir -p /opt/autoglow; \
  curl -fsSL --retry 3 -H "Authorization: Bearer $AG_TOKEN" \
  "https://api.github.com/repos/mondeggo/AutoGlow2/tarball/${AG_VERSION}" \
  -o /tmp/autoglow.tar.gz; \
  tar -xzf /tmp/autoglow.tar.gz --strip-components=1 -C /opt/autoglow; \
  echo "$AG_VERSION" > /opt/autoglow/REVISION; \
  rm -f /tmp/autoglow.tar.gz /tmp/autoglow-ref.json

WORKDIR /app

COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt -r /opt/autoglow/requirements.txt

COPY app ./app

RUN groupadd --gid 1000 oche \
  && useradd --uid 1000 --gid 1000 --create-home --shell /usr/sbin/nologin oche \
  && mkdir -p /app/data/autodarts /home/oche/.config \
  && ln -s /app/data/autodarts /home/oche/.config/autodarts \
  && chown -R oche:oche /home/oche/.config \
  && chown -R oche:oche /app /opt/autodarts
# Host networking uses the host's privileged-port rules. Allow the non-root
# Python server to bind OCHE_PORT=80 without running the application as root.
RUN setcap 'cap_net_bind_service=+ep' "$(readlink -f /usr/local/bin/python)"
USER oche

EXPOSE 80 8180 3180 8080

CMD ["python", "-m", "app.server"]
