# syntax=docker/dockerfile:1
FROM ghcr.io/astral-sh/uv:0.12.12 AS uv
FROM python:3.12-slim

ARG TARGETARCH
ARG AUTODARTS_VERSION
ARG AUTOGLOW_VERSION
ARG OCHECORE_VERSION=18b136dc5be0548e4af85f1c64b925b440b9f16c

# Runtime vision/USB libraries and udev for serial/camera device enumeration.
RUN apt-get update && apt-get install -y --no-install-recommends \
  curl ca-certificates openssl \
  libgl1 libglib2.0-0 libusb-1.0-0 libasound2 libpulse0 \
  udev libcap2-bin \
  && rm -rf /var/lib/apt/lists/*

# Fetch the official v2 headless bundle. Oche supervises `autodarts run`;
# the host installer and its systemd service are not needed in the image.
RUN set -eux; \
  case "${TARGETARCH:-amd64}" in \
  amd64) AD_ARCH="amd64" ;; \
  arm64) AD_ARCH="arm64" ;; \
  *) echo "Unsupported architecture: ${TARGETARCH}"; exit 1 ;; \
  esac; \
  VERSION="${AUTODARTS_VERSION:-}"; \
  if [ -z "$VERSION" ]; then \
  VERSION=$(curl -fsSL --retry 3 "https://releases.autodarts.com/headless/downloads/latest.stable.json" \
  | python -c 'import json, sys; print(json.load(sys.stdin)["platforms"]["linux-" + sys.argv[1]]["version"])' "$AD_ARCH"); \
  fi; \
  echo "$VERSION" | grep -Eq '^2\.[0-9]+\.[0-9]+$'; \
  mkdir -p /opt/autodarts; \
  curl -fsSL --retry 3 "https://releases.autodarts.com/headless/downloads/autodarts_${VERSION}_linux-${AD_ARCH}.tar.gz" -o /tmp/autodarts.tar.gz; \
  tar -xzf /tmp/autodarts.tar.gz --strip-components=1 -C /opt/autodarts; \
  rm /tmp/autodarts.tar.gz; \
  chmod +x /opt/autodarts/autodarts; \
  echo "$VERSION" > /opt/autodarts/VERSION; \
  ln -s /opt/autodarts/autodarts /usr/local/bin/autodarts

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

# OcheCore is public. Reuse the optional build token for authenticated GitHub
# requests without placing it in build arguments, the image, or runtime env.
RUN --mount=type=secret,id=autoglow_token set -eu; \
  CORE_VERSION="${OCHECORE_VERSION:-18b136dc5be0548e4af85f1c64b925b440b9f16c}"; \
  echo "$CORE_VERSION" | grep -Eq '^[0-9a-f]{40}$'; \
  mkdir -p /opt/ochecore; \
  if [ -s /run/secrets/autoglow_token ]; then \
    CORE_TOKEN=$(cat /run/secrets/autoglow_token); \
    curl -fsSL --retry 3 -H "Authorization: Bearer $CORE_TOKEN" \
      "https://api.github.com/repos/mondeggo/oche-core/tarball/${CORE_VERSION}" -o /tmp/ochecore.tar.gz; \
  else \
    curl -fsSL --retry 3 "https://api.github.com/repos/mondeggo/oche-core/tarball/${CORE_VERSION}" -o /tmp/ochecore.tar.gz; \
  fi; \
  tar -xzf /tmp/ochecore.tar.gz --strip-components=1 -C /opt/ochecore; \
  echo "$CORE_VERSION" > /opt/ochecore/REVISION; \
  python -c 'import pathlib, tomllib; p=pathlib.Path("/opt/ochecore"); (p/"VERSION").write_text(tomllib.loads((p/"pyproject.toml").read_text())["project"]["version"] + "\n")'; \
  rm /tmp/ochecore.tar.gz

# Keep OcheCore's pinned FastAPI/Uvicorn/audio dependencies separate from Oche
# and AutoGlow. Python 3.12 is supported by the upstream locked environment.
COPY --from=uv /uv /usr/local/bin/uv
RUN cd /opt/ochecore \
  && UV_PYTHON_DOWNLOADS=never uv sync --frozen --no-dev --no-editable --python /usr/local/bin/python \
  && uv cache clean \
  && /opt/ochecore/.venv/bin/python -m ochecore.main --version

WORKDIR /app

COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt -r /opt/autoglow/requirements.txt

COPY app ./app

RUN groupadd --gid 1000 oche \
  && useradd --uid 1000 --gid 1000 --create-home --shell /usr/sbin/nologin oche \
  && mkdir -p /app/data/autodarts /app/data/ochecore /home/oche/.config \
  && ln -s /app/data/autodarts /home/oche/.config/autodarts \
  && chown -R oche:oche /home/oche/.config \
  && chown -R oche:oche /app /opt/autodarts \
  && chmod 700 /app/data/ochecore
# Host networking uses the host's privileged-port rules. Allow the non-root
# Python server to bind OCHE_PORT=80 without running the application as root.
RUN setcap 'cap_net_bind_service=+ep' "$(readlink -f /usr/local/bin/python)"
USER oche

EXPOSE 80 443 8180 3180 8080

CMD ["python", "-m", "app.server"]
