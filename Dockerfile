FROM python:3.12-slim

ARG TARGETARCH
ARG AUTODARTS_VERSION

# Runtime libs Autodarts' bundled browser/vision stack needs, plus udev for
# serial/camera device enumeration.
RUN apt-get update && apt-get install -y --no-install-recommends \
  curl ca-certificates \
  libgl1 libglib2.0-0 libusb-1.0-0 \
  udev \
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

WORKDIR /app

COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt

COPY app ./app

RUN groupadd --gid 1000 oche \
  && useradd --uid 1000 --gid 1000 --create-home --shell /usr/sbin/nologin oche \
  && mkdir -p /app/data \
  && chown -R oche:oche /app /opt/autodarts
USER oche

EXPOSE 8180 3180

CMD ["uvicorn", "app.main:app", "--host", "0.0.0.0", "--port", "8180"]
