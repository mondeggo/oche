# Releases

## GitHub Actions

The workflows in `.github/workflows/` publish from `main` of `mondeggo/oche`:

- **Checks** runs the Python and frontend tests, lint and shell syntax on pull requests
  and other branches. Both publish workflows run it first and stop if it fails.
- **Publish Docker image** builds AMD64 and ARM64 images on every push to `main` and
  pushes `mondeggo/oche:latest` plus `mondeggo/oche:v<version>_<run>`. The image does
  not contain Autodarts; the installer and Updates fetch it from the official
  Autodarts releases into `data/modules/autodarts`.
- **Publish application updates** runs when the `pyproject.toml` version changes (or on
  manual dispatch). It exports both architecture bundles, combines `updates.json`,
  builds the wheel and attaches them to the GitHub release `v<version>`.

Versions use `X.Y.Z` or a pre-release such as `0.3.0rc1`. Set the repository secrets
`DOCKERHUB_USERNAME` and `DOCKERHUB_TOKEN`.
Point `OCHE_UPDATE_FEED` at a release's `updates.json`, for example
`https://github.com/mondeggo/oche/releases/latest/download/updates.json`.

## Manual build

Build the container for the target architecture:

```sh
docker build -f docker/Dockerfile --platform linux/amd64 --build-arg OCHE_VERSION=0.2.0 -t oche:local .
```

Use `linux/arm64` for ARM64. The dependency environment is locked by `uv.lock`.

Export the application bundle and manifest from the prepared image:

```sh
mkdir -p output
docker run --rm --user root -v "$PWD/output:/output" --entrypoint python oche:local \
  /opt/oche-tools/package_updates.py \
  --base-url https://YOUR-RELEASE-HOST/oche/v0.2.0
```

Host the archive at the declared HTTPS URL. For multiple architectures, combine
the generated `platforms` entries into one `updates.json`, then set
`OCHE_UPDATE_FEED` to that file's HTTPS address. Publishing is a separate step;
no release host or remote repository is configured automatically.

The release contains all of `app/`, including its internal darts runtime and UI.
`launcher.py` and `oche_runtime.py` stay outside the bundle. The runtime fingerprint
covers Python, installed dependencies and a contract tag in `package_updates.py`;
bump that tag when `oche_runtime.py` or `launcher.py` change, so new application
releases are refused on older images. Application version changes do not alter
that fingerprint.

Only Oche's releases are stored per runtime. Other modules, such as Autodarts, are
native programs: their releases stay in `data/modules/<name>/versions/<version>`
across image upgrades. The first start on a runtime-2 image moves Autodarts out of
the per-runtime folders that earlier images created. Generate compatible application releases from the same runtime
image, or ship an image upgrade when dependencies change.

Before activation, the updater checks HTTPS, archive size and paths, checksum,
entry points, integrated runtime and fingerprint. It stages the release and saves
the active/previous state before requesting restart. Only a healthy new boot is
accepted. Failed startup or interrupted activation restores the previous code.
Saved-data changes need a separate migration/backup strategy.
