# Docker Deployment — Cloud-Only Slim Image

**Status**: 📋 Planned
**Created**: 2026-04-13
**Updated**: 2026-04-13
**Priority**: Medium (new deployment target, no feature impact)
**Target environment**: Raspberry Pi 5 home server (linux/arm64), single-user

## Overview

Ship thestill as a Docker image suitable for home-server deployment on a Raspberry Pi 5.
The image is cloud-transcription-only by default: no `torch`, no `openai-whisper`, no
`whisperx`, no `pyannote.audio`. Two multi-stage targets are published from one
Dockerfile:

- **`:slim`** — Dalston-only. No `ffmpeg`. Smallest possible image (~200–250 MB).
- **`:full`** — Adds a static `ffmpeg` binary (~80 MB) copied from
  `mwader/static-ffmpeg`, enabling Google Cloud Speech and ElevenLabs providers.

SQLite persistence is retained; Postgres migration is explicitly **out of scope** for
this work. A future spec can revisit Postgres if multi-user hosting becomes a real
requirement.

Local Whisper / Parakeet transcription remains installable via a
`local-transcription` optional extra for anyone who wants to build their own image.
Neither published image includes it.

## Goals

1. `pip install .` succeeds without `torch` or any local-transcription dependency.
2. `thestill server` starts cleanly without local-transcription packages installed.
3. A single `Dockerfile` produces both `:slim` and `:full` via multi-stage targets.
4. All mutable state (SQLite DB, audio, transcripts, summaries) lives under one
   mounted directory.
5. Existing laptop data can be imported into the deployed container via `rsync`.
6. The compose file is pre-wired for a future sibling `dalston` container on a shared
   Docker network.
7. No code regression for existing non-Docker users.

## Non-goals

- Postgres migration.
- Kubernetes manifests.
- CI/CD workflows or registry publishing.
- Multi-arch builds from laptop (build is performed on the Pi).
- GPU support.
- Background worker / scheduler sidecar. Pipeline commands are run manually via
  `docker exec` or through the web UI.

## Background findings

### The three import blockers

Cloud providers do not need `torch`, but three top-level imports currently force it
into the import graph on every `thestill` invocation:

1. **[thestill/utils/device.py:24](../thestill/utils/device.py#L24)** — `import torch`
   at module top level. [thestill/core/transcriber.py:30](../thestill/core/transcriber.py#L30)
   imports `device.py`, and `transcriber.py` is the base class for *every* concrete
   transcriber, including `GoogleCloudTranscriber` and `DalstonTranscriber`. Merely
   instantiating a cloud transcriber triggers `torch` loading.

2. **[thestill/cli.py:37](../thestill/cli.py#L37)** — unconditional
   `from .core.whisper_transcriber import WhisperTranscriber, WhisperXTranscriber`.
   Runs on every CLI invocation. Transitively pulls `whisper`, `whisperx`, `torch`.
   The `transcribe` command at [cli.py:1458](../thestill/cli.py#L1458) and
   [cli.py:1470](../thestill/cli.py#L1470) references both classes by bare name, so
   simply deleting the top-level import yields `NameError`. Fix requires lazy imports
   at each usage site, mirroring the pattern already used for `ParakeetTranscriber` at
   [cli.py:1453](../thestill/cli.py#L1453).

3. **[thestill/core/parakeet_transcriber.py:26](../thestill/core/parakeet_transcriber.py#L26)** —
   top-level `import torch`. (`librosa` is already lazy-imported at
   [parakeet_transcriber.py:104](../thestill/core/parakeet_transcriber.py#L104), so
   no change needed there.) This file is currently only imported on demand from
   [task_handlers.py:567](../thestill/core/task_handlers.py#L567), so the blocker is
   latent — but fragile against future regressions.

[thestill/core/whisper_transcriber.py](../thestill/core/whisper_transcriber.py) has
similar top-level imports but will become unreachable in cloud-only mode once A2 is
done. Hardening is still recommended as cheap insurance.

### Provider dispatch is already lazy

[thestill/core/task_handlers.py:567-645](../thestill/core/task_handlers.py#L567)
already performs lazy, conditional imports per configured transcription provider.
Only the three blockers above prevent cloud-only installs from working today.

### Frontend is a gitignored build artifact

[thestill/web/app.py:267-289](../thestill/web/app.py#L267) serves a React SPA from
`thestill/web/static/`, which is the Vite build output of
[thestill/web/frontend/](../thestill/web/frontend/) per
[vite.config.ts:17](../thestill/web/frontend/vite.config.ts#L17)
(`outDir: '../static'`). [.gitignore:186](../.gitignore#L186) excludes
`thestill/web/static/`.

Hatchling (the build backend per [pyproject.toml:1-3](../pyproject.toml#L1-L3))
respects `.gitignore` by default when collecting files for the wheel. Consequence:
`pip install .` inside a Docker build produces a wheel **without** the frontend.
The runtime image then hits the `static_directory_not_found` branch in
[app.py:289](../thestill/web/app.py#L289), and the web UI breaks even though the API
routes still work.

This requires both a Node build stage in the Dockerfile **and** a `force-include`
rule in `pyproject.toml` so Hatchling packages the Vite output into the wheel when
it exists.

### Git dependency in pyproject.toml

[pyproject.toml:61](../pyproject.toml#L61) declares
`dalston-sdk @ git+https://github.com/ssarunic/dalston.git#subdirectory=sdk`. The
Docker builder stage must have `git` installed and network access for pip to resolve
this.

### Manual `thestill transcribe` CLI vs batch flow

[cli.py:1662](../thestill/cli.py#L1662) in the `thestill transcribe` command fails
if no downsampled audio exists on disk. This path cannot work in the `:slim` image
(no `ffmpeg` → no downsampling → no downsampled audio). However, the batch /
web-UI flow via [batch_processor.py:236](../thestill/services/batch_processor.py#L236)
correctly honors Dalston's URL-based flow and skips download/downsample entirely
(see commit `3a64b8b`). The `:slim` image is therefore usable for the intended
workflow; only the manual `thestill transcribe` subcommand is unavailable, which is
a documentation note, not a code change.

### `requirements.txt` drift risk

[requirements.txt](../requirements.txt) still pins
`openai-whisper==20250625`, `transformers>=4.30.0`, and `librosa>=0.10.0`. No tool
in the repo consumes this file; `pyproject.toml` is the source of truth. The file
will drift after the extras split and should be deleted.

## Execution plan

The plan is gated: each phase must pass its verification step before the next
begins. No Docker work starts until Phase A and Phase B are green.

### Phase A — Import refactor

Goal: `thestill server` starts with no local-transcription dependencies installed.

| # | File | Change |
|---|---|---|
| A1 | [thestill/utils/device.py](../thestill/utils/device.py) | Move `import torch` into the body of `resolve_device()`. Wrap in `try/except ImportError` and return `"cpu"` when torch is absent. Audit the rest of the file for any other torch references and move them into the same function. |
| A2a | [thestill/cli.py:37](../thestill/cli.py#L37) | Delete the top-level `from .core.whisper_transcriber import ...` line. |
| A2b | [thestill/cli.py:1458](../thestill/cli.py#L1458) | Add `from .core.whisper_transcriber import WhisperXTranscriber` inside the `enable_diarization` branch, immediately before the class is used. |
| A2c | [thestill/cli.py:1470](../thestill/cli.py#L1470) | Add `from .core.whisper_transcriber import WhisperTranscriber` inside the `else` branch, immediately before the class is used. |
| A3 | [thestill/core/parakeet_transcriber.py:26](../thestill/core/parakeet_transcriber.py#L26) | Move `import torch` into the class methods that use it (`load_model`, `transcribe`). `librosa` at line 104 is already lazy — leave alone. |
| A4 | [thestill/core/whisper_transcriber.py](../thestill/core/whisper_transcriber.py) | Move top-level `import torch`, `import whisper`, `import whisperx`, `from pyannote.audio import ...` into the methods that use them. Unreachable in cloud-only mode after A2, but prevents future regressions. |

**A5 — Verification gate:**

```bash
./venv/bin/pip uninstall -y torch openai-whisper whisperx pyannote.audio
./venv/bin/thestill --help          # must succeed
./venv/bin/thestill status          # must succeed
./venv/bin/thestill server          # must start, /health must return 200
```

If any command fails, there is a fourth blocker; find and fix before proceeding.

### Phase B — Packaging

Goal: `pip install .` installs the cloud-only dependency set; a separate extra
installs local transcription; the wheel packages the built frontend.

| # | File | Change |
|---|---|---|
| B1 | [pyproject.toml](../pyproject.toml) | Move `openai-whisper`, `whisperx`, `pyannote.audio`, `librosa` (and any explicit `torch` pin) from `[project] dependencies` into `[project.optional-dependencies] local-transcription`. |
| B2 | [pyproject.toml](../pyproject.toml) | Add `[tool.hatch.build.targets.wheel.force-include]` rule mapping `"thestill/web/static" = "thestill/web/static"` so Hatchling packages the gitignored Vite output when it exists at build time. |
| B3 | [requirements.txt](../requirements.txt) | **Delete** the file. Confirmed unused by any tool in the repo; drift risk only. |

**Dependencies staying in the main `dependencies` list** (cloud path needs them):
`pydub` (pure-Python, tiny), `google-cloud-speech`, `elevenlabs`, `dalston-sdk`,
`openai`, `anthropic`, `google-genai`, `mistralai`, FastAPI, uvicorn, pydantic,
click, feedparser, yt-dlp, structlog, and all current non-ML dependencies.

**B4 — Verification gate:**

```bash
rm -rf venv && python3.12 -m venv venv
./venv/bin/pip install -e .
./venv/bin/python -c "import torch"    # must fail: ModuleNotFoundError
./venv/bin/thestill server             # must start

# Wheel-packaging check (requires a local Vite build first)
cd thestill/web/frontend && npm ci && npm run build && cd -
./venv/bin/pip install build && ./venv/bin/python -m build --wheel
unzip -l dist/thestill-*.whl | grep 'web/static'   # must show index.html and assets/
```

### Phase C — Docker artifacts

Goal: `docker build --target slim` and `docker build --target full` produce
working images from a single Dockerfile.

#### C1 — `Dockerfile` (repo root)

Four build stages plus two output targets:

```dockerfile
# syntax=docker/dockerfile:1.7

ARG UID=1000
ARG GID=1000

# ---------- Stage 1: build the React SPA ----------
FROM node:20-slim AS frontend-builder
WORKDIR /src/thestill/web/frontend
COPY thestill/web/frontend/package.json thestill/web/frontend/package-lock.json ./
RUN npm ci
COPY thestill/web/frontend/ ./
RUN npm run build
# Vite writes to /src/thestill/web/static per outDir: '../static'

# ---------- Stage 2: static ffmpeg source (for :full only) ----------
FROM mwader/static-ffmpeg:7.0 AS ffmpeg-src

# ---------- Stage 3: build the Python wheel ----------
FROM python:3.12-slim AS python-builder
ENV PIP_DISABLE_PIP_VERSION_CHECK=1 PIP_NO_CACHE_DIR=1
WORKDIR /build
RUN apt-get update \
 && apt-get install -y --no-install-recommends build-essential git \
 && rm -rf /var/lib/apt/lists/*
COPY pyproject.toml README.md ./
COPY thestill ./thestill
# Inject the built frontend so Hatchling's force-include picks it up
COPY --from=frontend-builder /src/thestill/web/static ./thestill/web/static
RUN pip wheel --wheel-dir /wheels .

# ---------- Stage 4: runtime base ----------
FROM python:3.12-slim AS base
ARG UID
ARG GID
ENV PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1 \
    STORAGE_PATH=/data \
    DATABASE_PATH=/data/podcasts.db \
    LOG_FORMAT=json \
    LOG_LEVEL=INFO
RUN groupadd -g ${GID} thestill \
 && useradd  -u ${UID} -g ${GID} -d /app -s /usr/sbin/nologin thestill \
 && mkdir -p /app /data \
 && chown -R thestill:thestill /app /data
WORKDIR /app
COPY --from=python-builder /wheels /wheels
RUN pip install --no-cache-dir /wheels/*.whl && rm -rf /wheels
USER thestill
VOLUME ["/data"]
EXPOSE 8000
HEALTHCHECK --interval=30s --timeout=5s --start-period=20s --retries=3 \
  CMD python -c "import urllib.request,sys; \
    sys.exit(0 if urllib.request.urlopen('http://localhost:8000/health').status==200 else 1)" \
  || exit 1
CMD ["thestill", "server", "--host", "0.0.0.0", "--port", "8000"]

# ---------- Target: slim (Dalston-only, no ffmpeg) ----------
FROM base AS slim

# ---------- Target: full (adds static ffmpeg) ----------
FROM base AS full
USER root
COPY --from=ffmpeg-src /ffmpeg  /usr/local/bin/ffmpeg
COPY --from=ffmpeg-src /ffprobe /usr/local/bin/ffprobe
USER thestill
```

**Rationale:**

- Separate `python-builder` stage keeps gcc, build headers, and git out of the
  runtime image. Runtime has only the installed wheels.
- `frontend-builder` runs `npm ci` against the committed `package-lock.json` for
  reproducible SPA builds. The Vite output is copied into the python-builder's
  source tree **before** `pip wheel` runs, so Hatchling's `force-include` (Phase B2)
  packages it into the wheel.
- `ARG UID/GID` let the Pi operator match the container user to a host UID at
  build time, making bind-mount permissions sane without a chmod dance.
- `LOG_FORMAT=json` default matches production; override via env for debugging.
- `HEALTHCHECK` hits the existing `/health` endpoint at
  [thestill/web/routes/health.py:30](../thestill/web/routes/health.py#L30).
  Status code check only; response body format is irrelevant.
- `:slim` is literally `FROM base AS slim` with no additions. `:full` adds exactly
  two files: the `ffmpeg` and `ffprobe` binaries.
- Multi-arch: all base images (`python:3.12-slim`, `node:20-slim`,
  `mwader/static-ffmpeg:7.0`) publish `linux/arm64` tags. No `platform:` override
  needed.

#### C2 — `.dockerignore` (repo root)

```
# VCS
.git
.github

# Python artifacts
venv
.venv
__pycache__
*.pyc
*.pyo
.pytest_cache
.mypy_cache
.ruff_cache

# Host-side data (never ship into the image)
data/
evaluation/

# Docs and tests (not needed in the image)
docs/
specs/
tests/
*.md
!README.md

# Frontend build inputs and artifacts — the frontend-builder stage
# copies what it needs directly from thestill/web/frontend/
thestill/web/frontend/node_modules
thestill/web/frontend/dist
thestill/web/static/

# Secrets
.env
.env.*
!.env.example

# Editor/OS
.DS_Store
.vscode
.idea
```

**Non-obvious entries:**

- `!README.md` negation is required because `*.md` excludes it, but the Dockerfile
  does `COPY pyproject.toml README.md ./` — build would fail without the negation.
- `thestill/web/frontend/node_modules` (~170 MB on disk) must not bloat the build
  context; the Node stage runs its own `npm ci`.
- `thestill/web/static/` is excluded even though the Python builder needs it,
  because it flows in from the `frontend-builder` stage via `COPY --from=`, not
  from the build context.
- `data/` and `evaluation/` exclude multi-GB of podcast audio that is not image
  content.

#### C3 — `docker-compose.yml` (repo root)

```yaml
networks:
  thestill-net:
    driver: bridge

services:
  thestill:
    build:
      context: .
      target: slim          # change to "full" when Google / ElevenLabs are needed
      args:
        UID: "${UID:-1000}"
        GID: "${GID:-1000}"
    image: thestill:slim
    container_name: thestill
    restart: unless-stopped
    ports:
      - "8000:8000"
    env_file:
      - .env
    environment:
      - DALSTON_BASE_URL=${DALSTON_BASE_URL:-}
    volumes:
      - /srv/thestill/data:/data
    networks:
      - thestill-net
    healthcheck:
      test:
        - "CMD"
        - "python"
        - "-c"
        - "import urllib.request,sys; sys.exit(0 if urllib.request.urlopen('http://localhost:8000/health').status==200 else 1)"
      interval: 30s
      timeout: 5s
      retries: 3
      start_period: 20s
```

**Notes:**

- Host path `/srv/thestill/data` is a deliberate fixed location on the Pi, not a
  laptop-relative `./data`. Matches the "don't bind-mount my laptop disk" constraint.
- `UID` / `GID` are taken from the operator's shell env at build time so the
  in-container user owns the host directory cleanly.
- `DALSTON_BASE_URL` is plumbed through from `.env` with an empty default, meaning
  "use hosted Dalston" when unset. When a sibling `dalston` service is added to
  this compose file on the shared `thestill-net` network, the operator sets it to
  `http://dalston:<port>` in `.env`. Docker's embedded DNS resolves the name.
- `thestill-net` is declared even though only one service uses it today; this
  avoids a disruptive recreate later when a Dalston sibling is added.

**Pre-build verification (Phase C1a):** Confirm the config key name for the Dalston
base URL. If [thestill/utils/config.py](../thestill/utils/config.py) does not
already expose a `DALSTON_BASE_URL` surface, either rename the compose env var to
match the existing key or add a tiny config surface (~5 LOC) in the same pass.
This is the only piece of the plan that may require a config-code change beyond
the import refactor.

### Phase D — Volume and data strategy

#### D1 — Single volume, single mount

All mutable state lives under `/data` inside the container:

- `/data/podcasts.db`
- `/data/original_audio/`, `/data/downsampled_audio/`
- `/data/raw_transcripts/`, `/data/clean_transcripts/`
- `/data/summaries/`, `/data/digests/`
- `/data/podcast_facts/`, `/data/episode_facts/`
- `/data/feeds.json`, `/data/debug_feeds/`, `/data/pending_operations/`

This works because all paths derive from `STORAGE_PATH` via
[thestill/utils/path_manager.py](../thestill/utils/path_manager.py); no code
changes required.

#### D2 — Data import from laptop

First-time setup on the Pi:

```bash
# On the Pi
sudo mkdir -p /srv/thestill/data
sudo chown -R 1000:1000 /srv/thestill/data   # or your Pi user's UID/GID
```

Import existing laptop data:

```bash
# From the laptop
rsync -avz --progress ./data/ pi@rpi.local:/srv/thestill/data/
```

Start the container:

```bash
# On the Pi
cd ~/thestill
UID=$(id -u) GID=$(id -g) docker compose build
docker compose up -d
```

First-run schema bootstrap is skipped automatically because the existing
`podcasts.db` already has the schema. Repositories open the existing file.

#### D3 — Backups

`/srv/thestill/data` is a plain host directory. Back up with:

```bash
# Live (SQLite will handle WAL correctly)
sqlite3 /srv/thestill/data/podcasts.db ".backup /tmp/podcasts.db.backup"
tar czf thestill-backup-$(date +%F).tgz -C /srv/thestill data
```

#### D4 — Storage medium

**Strong recommendation: USB SSD, not SD card.** SQLite WAL writes plus podcast
audio downloads will measurably wear an SD card over months, and random-read
performance affects transcript cleaning and digest generation. An entry-level USB
SSD mounted at `/srv/thestill` solves both problems. Document this as a
prerequisite in `docs/docker.md`.

### Phase E — Configuration & secrets

#### E1 — `.env.example` additions

Add a documented "Docker deployment" section:

```env
# ---------- Docker deployment ----------
# Override these only if you need non-default behaviour
# STORAGE_PATH=/data        # set by the container
# DATABASE_PATH=/data/podcasts.db   # set by the container
# LOG_FORMAT=json           # set by the container

# Dalston transcription provider
# Leave unset to use hosted Dalston; set to http://dalston:<port>
# when running a sibling container on the thestill-net Docker network
DALSTON_BASE_URL=

# Host UID/GID for data directory ownership (used at build time)
# UID=1000
# GID=1000
```

The compose file reads `DALSTON_BASE_URL` via the `environment:` block; the Pi
operator sets it in `.env` if and when they stand up a sibling Dalston container.

### Phase F — Verification

1. **F1 — Phase A gate.** `thestill --help`, `thestill status`, `thestill server`
   all succeed with `torch` / `openai-whisper` / `whisperx` / `pyannote.audio`
   uninstalled from the local venv.
2. **F2 — Phase B gate.** Fresh venv, `pip install -e .`, `import torch` fails,
   `thestill server` starts.
3. **F3 — Wheel content check.**

   ```bash
   unzip -l dist/thestill-*.whl | grep 'web/static'
   ```

   Must show at least `thestill/web/static/index.html` and
   `thestill/web/static/assets/*`.
4. **F4 — Build both images on the Pi.**

   ```bash
   docker build --target slim -t thestill:slim .
   docker build --target full -t thestill:full .
   docker images thestill   # record sizes
   ```

5. **F5 — Run `:slim` via compose.**

   ```bash
   UID=$(id -u) GID=$(id -g) docker compose up -d
   docker compose ps               # must show (healthy)
   curl -sS http://localhost:8000/health          # must return 200
   curl -sS http://localhost:8000/ | head -5      # must return SPA HTML
   docker exec thestill thestill status           # must succeed
   ```

6. **F6 — Dalston env-var smoke test.** Unset `DALSTON_BASE_URL`, confirm hosted
   default is used. Set it to a dummy URL, confirm the container picks it up at
   startup.
7. **F7 — End-to-end transcription.** Using a real RSS feed, run inside the
   container:

   ```bash
   docker exec -it thestill thestill add "https://example.com/feed.rss"
   docker exec -it thestill thestill refresh
   docker exec -it thestill thestill digest --ready-only
   ```

   Confirm an episode completes from discovery through transcription and summary.
8. **F8 — `:full` ffmpeg smoke test.**

   ```bash
   docker run --rm thestill:full ffmpeg -version
   ```

   Must print the static ffmpeg version.

## Files touched

**Modified:**

- [thestill/utils/device.py](../thestill/utils/device.py) — lazy torch
- [thestill/cli.py](../thestill/cli.py) — delete L37, add lazy imports at L1458 and L1470
- [thestill/core/parakeet_transcriber.py](../thestill/core/parakeet_transcriber.py) — lazy torch
- [thestill/core/whisper_transcriber.py](../thestill/core/whisper_transcriber.py) — lazy torch/whisper/whisperx/pyannote
- [pyproject.toml](../pyproject.toml) — extras split + Hatchling force-include
- [.env.example](../.env.example) — Docker deployment section
- [thestill/utils/config.py](../thestill/utils/config.py) — **only if** `DALSTON_BASE_URL` is not already a config surface (TBD, Phase C1a)

**Created:**

- `Dockerfile`
- `.dockerignore`
- `docker-compose.yml`
- `docs/docker.md` — build, import, run, Dalston-sibling pattern, backup/restore

**Deleted:**

- [requirements.txt](../requirements.txt)

## Open questions

1. **Dalston config key name.** Resolve in Phase C1a by grepping
   [thestill/utils/config.py](../thestill/utils/config.py) and wherever the Dalston
   SDK is constructed. If the current surface uses a different env var, rename in
   the compose file rather than adding a new one.
2. **A4 (whisper_transcriber.py hardening).** Recommended to include even though
   A2 makes it unreachable in cloud-only mode; prevents future regressions. Costs
   ~5 minutes.
3. **Runtime install strategy.** Plan uses wheel-based install for a clean
   runtime image with no source tree under `/app`. Alternative would be editable
   install + source copy; rejected because it leaves build-time source in the
   runtime image without benefit.
4. **Python version.** `python:3.12-slim` on both builder and runtime stages.
   RPi5 host Python version is irrelevant inside Docker.

## Image size budget (estimates, to be verified in F4)

| Target | Expected size | Components |
|---|---|---|
| `:slim` | ~200–250 MB | `python:3.12-slim` base (~50 MB) + cloud-only pip deps (~150–200 MB) |
| `:full` | ~280–330 MB | `:slim` + static ffmpeg/ffprobe binaries (~80 MB) |

Neither image includes `torch`, `openai-whisper`, `whisperx`, or `pyannote.audio`.
Anyone wanting local transcription builds their own image with
`pip install ".[local-transcription]"` inside a custom Dockerfile.

## Rollback plan

All changes are additive except the `requirements.txt` deletion and the
`pyproject.toml` dependency move. To revert:

1. Restore `requirements.txt` from git.
2. Restore `pyproject.toml` from git (moves the 4 packages back into main deps).
3. Revert the 4 Python files with the lazy imports (they are strict improvements
   but cleanly revertable).
4. Delete `Dockerfile`, `.dockerignore`, `docker-compose.yml`, `docs/docker.md`.

No database schema changes, no breaking API changes, no config renames.
