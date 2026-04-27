# syntax=docker/dockerfile:1.7

# Multi-stage build producing two targets from a single Dockerfile:
#   - slim: Dalston-only, no ffmpeg (~200-250 MB)
#   - full: slim + static ffmpeg/ffprobe (~280-330 MB)
#
# Neither image includes torch, openai-whisper, whisperx, or pyannote.audio.
# For local transcription, build your own image with
# `pip install ".[local-transcription]"`.

ARG THESTILL_UID=1000
ARG THESTILL_GID=1000

# ---------- Stage 1: build the React SPA ----------
# Spec #25 item 5.1: every base image is digest-pinned. Tags are mutable —
# Docker Hub's ``:slim`` resolves to whatever was last pushed under that
# tag, which means CI builds drift silently and a compromised maintainer
# upload would land in our image without warning. Dependabot's docker
# ecosystem watches these digests and opens a PR weekly when a new
# version is published.
FROM node:22-slim@sha256:d415caac2f1f77b98caaf9415c5f807e14bc8d7bdea62561ea2fef4fbd08a73c AS frontend-builder
WORKDIR /src/thestill/web/frontend
COPY thestill/web/frontend/package.json thestill/web/frontend/package-lock.json ./
RUN npm ci
COPY thestill/web/frontend/ ./
RUN npm run build
# Vite writes to /src/thestill/web/static per outDir: '../static'

# ---------- Stage 2: static ffmpeg source (for :full only) ----------
FROM mwader/static-ffmpeg:8.1@sha256:6fb848850b647688910ccf88112a96afe5ee46bcd54ee41daf875cac98b02bff AS ffmpeg-src

# ---------- Stage 3: build the Python wheel ----------
FROM python:3.12-slim@sha256:46cb7cc2877e60fbd5e21a9ae6115c30ace7a077b9f8772da879e4590c18c2e3 AS python-builder
ENV PIP_DISABLE_PIP_VERSION_CHECK=1 PIP_NO_CACHE_DIR=1
WORKDIR /build
RUN apt-get update \
 && apt-get install -y --no-install-recommends build-essential git \
 && rm -rf /var/lib/apt/lists/*
COPY pyproject.toml README.md uv.lock ./
COPY thestill ./thestill
# Inject the built SPA so Hatchling's artifacts whitelist picks it up.
COPY --from=frontend-builder /src/thestill/web/static ./thestill/web/static
# Spec #25 item 3.8: build wheels from the committed lockfile, not from
# pyproject.toml's `>=` floors. ``uv export`` dumps the locked versions
# (with sha256 hashes) as a pip-compatible requirements.txt; ``pip wheel``
# refuses to build any wheel whose hash doesn't match — closing the
# supply-chain ambush window between Dependabot bumps. The project wheel
# itself is built last with ``--no-deps`` so pip doesn't re-resolve.
RUN pip install --no-cache-dir uv \
 && uv export --frozen --no-emit-project --format requirements-txt > /tmp/reqs.txt \
 && pip wheel --wheel-dir /wheels --require-hashes -r /tmp/reqs.txt \
 && pip wheel --wheel-dir /wheels --no-deps .

# ---------- Stage 4: runtime base ----------
FROM python:3.12-slim@sha256:46cb7cc2877e60fbd5e21a9ae6115c30ace7a077b9f8772da879e4590c18c2e3 AS base
# Re-declare with defaults so `docker build` works without --build-arg.
# Top-level ARGs before the first FROM don't propagate into stages.
ARG THESTILL_UID=1000
ARG THESTILL_GID=1000
ENV PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1 \
    STORAGE_PATH=/data \
    DATABASE_PATH=/data/podcasts.db \
    LOG_FORMAT=json \
    LOG_LEVEL=INFO
# On macOS, `id -g` returns 20 (staff), which collides with the `dialout`
# group baked into python:3.12-slim. Reuse any existing group with that GID
# instead of failing, then create the `thestill` user inside it so the later
# `USER thestill` directive resolves.
RUN set -eux; \
    if ! getent group ${THESTILL_GID} >/dev/null; then \
        groupadd -g ${THESTILL_GID} thestill; \
    fi; \
    useradd -u ${THESTILL_UID} -g ${THESTILL_GID} -d /app -s /usr/sbin/nologin thestill; \
    mkdir -p /app /data; \
    chown -R ${THESTILL_UID}:${THESTILL_GID} /app /data
WORKDIR /app
COPY --from=python-builder /wheels /wheels
# Install the pre-built wheels directly with --no-deps. The builder stage's
# `pip wheel .` already resolved and built the complete dependency closure
# into /wheels, so we can install them as-is without letting pip re-resolve.
# Dep resolution is what triggers git (thestill's METADATA still lists
# `dalston-sdk @ git+https://...` as a direct URL), and this runtime stage
# deliberately has no git binary to keep the slim image small.
RUN pip install --no-cache-dir --no-deps /wheels/*.whl \
 && rm -rf /wheels
USER thestill
VOLUME ["/data"]
EXPOSE 8000
HEALTHCHECK --interval=30s --timeout=5s --start-period=20s --retries=3 \
  CMD ["python", "-c", "import urllib.request; urllib.request.urlopen('http://localhost:8000/health', timeout=3)"]
CMD ["thestill", "server", "--host", "0.0.0.0", "--port", "8000"]

# ---------- Target: slim (Dalston-only, no ffmpeg) ----------
FROM base AS slim

# ---------- Target: full (adds static ffmpeg) ----------
FROM base AS full
USER root
COPY --from=ffmpeg-src /ffmpeg  /usr/local/bin/ffmpeg
COPY --from=ffmpeg-src /ffprobe /usr/local/bin/ffprobe
USER thestill
