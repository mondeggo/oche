# syntax=docker/dockerfile:1
FROM ghcr.io/astral-sh/uv:0.12.12 AS uv
FROM python:3.12-slim AS runtime

RUN apt-get update && apt-get install -y --no-install-recommends \
    curl ca-certificates openssl libgl1 libglib2.0-0 libusb-1.0-0 \
    libasound2 libpulse0 udev libcap2-bin v4l-utils \
    && rm -rf /var/lib/apt/lists/*

# AutoDarts detection is not part of the image. The installer and Updates put
# its official release in data/modules/autodarts, where Oche supervises it.

COPY --from=uv /uv /usr/local/bin/uv
WORKDIR /app
COPY pyproject.toml requirements.txt uv.lock ./
COPY oche_runtime.py launcher.py ./
ENV UV_PYTHON_DOWNLOADS=never
RUN uv sync --locked --no-dev --no-install-project --python /usr/local/bin/python && uv cache clean
ENV PATH="/app/.venv/bin:$PATH"
COPY scripts/package_updates.py /opt/oche-tools/package_updates.py

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

# Development uses mounted sources and no seeded application, so the launcher
# starts the checkout until an update is explicitly installed.
FROM runtime AS development
USER root
RUN mkdir -p /opt/oche-bundles && python /opt/oche-tools/package_updates.py --runtime-only
USER oche
ENV OCHE_BUNDLES_DIR=/opt/oche-dev-bundles OCHE_DEV_RELOAD=1

# Produce the same archive format shipped by the application release workflow.
FROM runtime AS packages
USER root
ARG OCHE_VERSION=0.2.0
COPY app /opt/oche-bundles/oche/app
RUN printf '%s\n' "$OCHE_VERSION" > /opt/oche-bundles/oche/VERSION \
    && python /opt/oche-tools/package_updates.py --runtime-only \
    && python /opt/oche-tools/package_updates.py --base-url https://build.invalid/releases

# Only the packaged release crosses into the production image; no checkout app.
FROM runtime AS production
COPY --from=packages /opt/oche-runtime-id /opt/oche-runtime-id
COPY --from=packages /output/oche-*.tar.gz /opt/oche-release.tar.gz
USER root
RUN mkdir -p /opt/oche-bundles/oche \
    && tar -xzf /opt/oche-release.tar.gz -C /opt/oche-bundles/oche \
    && rm /opt/oche-release.tar.gz
# Support one-off module installation commands before the launcher has seeded data.
ENV PYTHONPATH=/app:/opt/oche-bundles/oche
USER oche
