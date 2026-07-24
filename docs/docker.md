# Docker Deployment

Cloud-transcription-only Docker image for home-server deployment (tested on
Raspberry Pi 5, `linux/arm64`).

## Image variants

Both variants are built from the same `Dockerfile` via multi-stage targets:

| Tag         | Contents                                                  | Use when                                     |
| ----------- | --------------------------------------------------------- | -------------------------------------------- |
| `:slim`     | Cloud deps only, no `ffmpeg`                              | Dalston is your only transcription provider |
| `:full`     | `:slim` + static `ffmpeg` and `ffprobe` (~80 MB more)     | You need Google Cloud Speech or ElevenLabs  |

Neither image includes `torch`, `openai-whisper`, `whisperx`, or
`pyannote.audio`. Local transcription requires building your own image that
installs the `local-transcription` optional-dependencies extra.

## Prerequisites

- Docker Engine + Compose v2 on the host.
- **USB SSD mounted at `/srv/thestill`** is strongly recommended. SQLite WAL
  writes and podcast audio downloads will wear an SD card over months, and
  random-read performance matters for transcript cleaning and briefing
  generation.

## First-time setup

> **Required before first run.** The slim image does not ship Whisper /
> WhisperX / Parakeet, so all transcription runs through Dalston.
> `docker-compose.yml` pins `TRANSCRIPTION_PROVIDER=dalston` and you must
> set `DALSTON_BASE_URL` in `.env` to a reachable Dalston server. Leaving
> it unset falls through to `http://localhost:8000` inside the container,
> which resolves to thestill itself and will fail at the first API call.

```bash
# On the Pi — create the data directory owned by your host user
sudo mkdir -p /srv/thestill/data
sudo chown -R "$(id -u):$(id -g)" /srv/thestill/data

# Clone the repo and copy the example env
git clone https://github.com/ssarunic/thestill.git ~/thestill
cd ~/thestill
cp .env.example .env

# Minimum required edits to .env:
#   - DALSTON_BASE_URL=http://<dalston-host>:<port>
#   - LLM_PROVIDER + matching API key (OPENAI_API_KEY, GEMINI_API_KEY, ...)
# STORAGE_PATH and DATABASE_PATH in .env.example are ignored — compose
# overrides them to /data so state lives on the mounted volume.
```

The committed `docker-compose.yml` mounts `./data:/data`, i.e.
`~/thestill/data` under the repo clone. To put state on the SSD path
created above, edit the `volumes:` entry in `docker-compose.yml` before
the first `docker compose up`:

```yaml
    volumes:
      - /srv/thestill/data:/data
```

The rest of this page assumes the `/srv/thestill/data` mount. If you
skip this edit, substitute `~/thestill/data` wherever
`/srv/thestill/data` appears below (and note that data then lives on
whatever disk holds the repo clone).

## Importing existing data from a laptop

```bash
# On the laptop (one-shot rsync to the Pi)
rsync -avz --progress ./data/ pi@rpi.local:/srv/thestill/data/
```

The SQLite schema is not re-bootstrapped: repositories open the existing
`podcasts.db` as-is.

## Build and run

The default compose target is `:slim`. To change to `:full`, edit
`docker-compose.yml` and set `target: full` under `build:`.

```bash
cd ~/thestill
# Build — THESTILL_UID/THESTILL_GID must be exported so the container user
# matches the host user that owns /srv/thestill/data.
THESTILL_UID=$(id -u) THESTILL_GID=$(id -g) docker compose build
docker compose up -d
docker compose ps          # should show (healthy) after ~20s
```

Verify the server. Compose maps host port `8001` to the container's
internal `8000` (`"8001:8000"` — host 8000 is left free for a local
dalston-gateway):

```bash
curl -sS http://localhost:8001/health          # 200 {"status":"healthy", ...}
curl -sS http://localhost:8001/ | head -5      # SPA HTML
docker exec thestill thestill status
```

## Pipeline operations

With the container running, pipeline commands are invoked via `docker exec`:

```bash
docker exec -it thestill thestill add "https://example.com/feed.rss"
docker exec -it thestill thestill refresh
docker exec -it thestill thestill refresh
```

**Note — slim image:** `thestill transcribe` requires downsampled audio on
disk, which requires `ffmpeg`. Use the `:full` image for this subcommand. The
batch and web-UI flows use Dalston's URL-based pipeline and work on `:slim`
without `ffmpeg`.

## Dalston server (required)

Dalston is self-hosted — there is no managed endpoint and `DALSTON_BASE_URL`
has no working default. You must point the slim image at a reachable
Dalston server. Two common topologies:

**Sibling container on the same host.** The compose file pre-declares a
`thestill-net` bridge network so a Dalston container can be added without
recreating the `thestill` service:

1. Stand up a `dalston` service attached to `thestill-net` (in its own
   compose file, or merge into this one).
2. Set `DALSTON_BASE_URL=http://dalston:<port>` in `.env`. Docker's embedded
   DNS resolves `dalston` to the sibling container's IP on the shared
   network.
3. `docker compose up -d thestill`.

**Dalston on a different host.** Set
`DALSTON_BASE_URL=http://<host-ip-or-dns>:<port>` in `.env`. No network
changes needed on the thestill side.

## Backups

`/srv/thestill/data` is a plain host directory — back up with your tool of
choice. For a consistent snapshot of the SQLite database, use `.backup`:

```bash
sqlite3 /srv/thestill/data/podcasts.db ".backup /tmp/podcasts.db.backup"
tar czf thestill-backup-"$(date +%F)".tgz -C /srv/thestill data
```

## Troubleshooting

**Container starts but `/health` never returns 200.** Check logs with
`docker compose logs -f thestill`. Most commonly: missing or malformed
`.env` values (LLM provider API key, database path, etc.).

**`docker compose build` fails on the frontend stage.** The `node_modules`
step needs HTTPS network access. Retry on a better connection.

**`docker compose build` fails on `pip wheel`.** The `python-builder` stage
needs HTTPS to resolve the `dalston-sdk` git dependency. Ensure the Pi has
network access.

**Permission denied writing to `/data`.** The host `/srv/thestill/data`
directory must be owned by the same UID/GID the container was built with.
Re-run `chown` and rebuild with matching `THESTILL_UID`/`THESTILL_GID`.

## Rebuilding after updates

```bash
cd ~/thestill
git pull
THESTILL_UID=$(id -u) THESTILL_GID=$(id -g) docker compose build
docker compose up -d          # compose will recreate only if the image changed
```
