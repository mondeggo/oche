# syntax=docker/dockerfile:1
FROM ghcr.io/astral-sh/uv:0.12.12 AS uv
FROM python:3.12-slim

ARG TARGETARCH
ARG AUTODARTS_VERSION
ARG OCHE_VERSION=0.2.0

RUN apt-get update && apt-get install -y --no-install-recommends \
    curl ca-certificates openssl libgl1 libglib2.0-0 libusb-1.0-0 \
    libasound2 libpulse0 udev libcap2-bin v4l-utils \
    && rm -rf /var/lib/apt/lists/*

# AutoDarts detection remains an external binary managed by Oche.
RUN set -eu; \
    case "${TARGETARCH:-amd64}" in amd64) arch=amd64;; arm64) arch=arm64;; *) exit 1;; esac; \
    version="${AUTODARTS_VERSION:-}"; \
    if [ -z "$version" ]; then \
      version=$(curl -fsSL --retry 3 https://releases.autodarts.com/headless/downloads/latest.stable.json \
        | python -c 'import json,sys; print(json.load(sys.stdin)["platforms"]["linux-"+sys.argv[1]]["version"])' "$arch"); \
    fi; \
    echo "$version" | grep -Eq '^2\.[0-9]+\.[0-9]+$'; \
    mkdir -p /opt/autodarts; \
    curl -fsSL --retry 3 "https://releases.autodarts.com/headless/downloads/autodarts_${version}_linux-${arch}.tar.gz" -o /tmp/autodarts.tar.gz; \
    tar -xzf /tmp/autodarts.tar.gz --strip-components=1 -C /opt/autodarts; \
    rm /tmp/autodarts.tar.gz; \
    chmod +x /opt/autodarts/autodarts; \
    echo "$version" > /opt/autodarts/VERSION

COPY --from=uv /uv /usr/local/bin/uv
WORKDIR /app
COPY pyproject.toml requirements.txt uv.lock ./
COPY app ./app
COPY oche_runtime.py launcher.py ./
ENV UV_PYTHON_DOWNLOADS=never
RUN uv sync --locked --no-dev --no-install-project --python /usr/local/bin/python && uv cache clean
ENV PATH="/app/.venv/bin:$PATH"
COPY scripts/package_updates.py /opt/oche-tools/package_updates.py

# All application code, including app/core, is released and rolled back together.
RUN mkdir -p /opt/oche-bundles/oche /opt/oche-bundles/autodarts \
    && cp -a app /opt/oche-bundles/oche/app \
    && printf '%s\n' "$OCHE_VERSION" > /opt/oche-bundles/oche/VERSION \
    && cp -a /opt/autodarts/. /opt/oche-bundles/autodarts/ \
    && python /opt/oche-tools/package_updates.py --runtime-only

ENV OCHE_DATA_DIR=/app/data OCHE_PORT=8180
RUN groupadd --gid 1000 oche \
    && useradd --uid 1000 --gid 1000 --create-home --shell /usr/sbin/nologin oche \
    && mkdir -p /app/data/core /app/data/autodarts /home/oche/.config \
    && ln -s /app/data/autodarts /home/oche/.config/autodarts \
    && chown -R oche:oche /app /home/oche/.config \
    && chmod 700 /app/data/core \
    && setcap 'cap_net_bind_service=+ep' "$(readlink -f /usr/local/bin/python)"
USER oche
EXPOSE 8180 443
HEALTHCHECK --interval=30s --timeout=5s --start-period=90s \
    CMD python -c 'import os,urllib.request; urllib.request.urlopen("http://127.0.0.1:"+os.environ.get("OCHE_PORT","8180")+"/healthz",timeout=3)' || exit 1
CMD ["python", "/app/launcher.py"]
