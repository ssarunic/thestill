# Configuration

All configuration is done via environment variables, typically stored in a `.env` file.

## Setup

```bash
cp .env.example .env
# Edit .env with your settings
```

## Core Settings

| Variable | Description | Default |
|----------|-------------|---------|
| `STORAGE_PATH` | Base directory for all data | `./data` |
| `DATABASE_URL` | Postgres connection string; when set, the Postgres repositories are used (spec #44) | - (empty = SQLite) |
| `DATABASE_PATH` | SQLite database location; ignored when `DATABASE_URL` is set | `{STORAGE_PATH}/podcasts.db` |

`DATABASE_URL` selects the persistence backend via
`repositories/factory.py`: empty means SQLite at `DATABASE_PATH`, any
Postgres URL switches every repository to Postgres. Production
deployments run Postgres.

## File Storage Backend

Spec #35 — selects where pipeline artefacts (audio, transcripts, summaries,
corpus pages, briefings) are stored. `local` keeps the historical on-disk
layout under `STORAGE_PATH`; `s3` routes them to AWS S3.

| Variable | Description | Default |
|----------|-------------|---------|
| `STORAGE_BACKEND` | `local` or `s3` | `local` |
| `S3_BUCKET` | Bucket name (required when `STORAGE_BACKEND=s3`) | - |
| `S3_REGION` | AWS region; must match compute region | `us-east-1` |
| `S3_PREFIX` | Optional key prefix (e.g. `prod/`) | - |
| `S3_ENDPOINT_URL` | Override for LocalStack / MinIO / S3-compatible stores; leave empty for real AWS | - |
| `S3_KMS_KEY_ID` | Customer-managed KMS key for SSE-KMS; empty = SSE-S3 (AES256) | - |

For an end-to-end walkthrough of deploying with S3 on AWS, see
[storage-backends.md](storage-backends.md).

## Transcription Provider

| Variable | Description | Default |
|----------|-------------|---------|
| `TRANSCRIPTION_PROVIDER` | Provider to use: `whisper`, `parakeet`, `google`, `elevenlabs`, `dalston` | `whisper` |
| `WHISPER_MODEL` | Whisper model size: `tiny`, `base`, `small`, `medium`, `large` | `base` |
| `WHISPER_DEVICE` | Device for inference: `auto`, `cpu`, `cuda` | `auto` |
| `ENABLE_DIARIZATION` | Enable speaker identification | `false` |
| `DIARIZATION_MODEL` | pyannote.audio diarization model | `pyannote/speaker-diarization-3.1` |
| `HUGGINGFACE_TOKEN` | Token for pyannote.audio (Whisper diarization) | - |
| `MIN_SPEAKERS` | Minimum speakers (leave empty for auto) | - |
| `MAX_SPEAKERS` | Maximum speakers (leave empty for auto) | - |

See [transcription-providers.md](transcription-providers.md) for provider-specific setup.

## Google Cloud (for Google transcription)

| Variable | Description | Default |
|----------|-------------|---------|
| `GOOGLE_APP_CREDENTIALS` | Path to service account JSON key | - |
| `GOOGLE_CLOUD_PROJECT_ID` | GCP project ID | - |
| `GOOGLE_STORAGE_BUCKET` | GCS bucket for large files (>10MB) | - |

## ElevenLabs

| Variable | Description | Default |
|----------|-------------|---------|
| `ELEVENLABS_API_KEY` | ElevenLabs API key | - |
| `ELEVENLABS_BASE_URL` | API base URL override (empty = official API) | - |
| `ELEVENLABS_MODEL` | Model: `scribe_v1`, `scribe_v1_experimental` | `scribe_v1` |
| `ELEVENLABS_ASYNC_THRESHOLD_MB` | File size above which transcription goes async via webhook (0 = always sync) | `0` |
| `ELEVENLABS_WEBHOOK_SECRET` | Webhook signature verification | - |
| `ELEVENLABS_WEBHOOK_REQUIRE_METADATA` | Require episode_id in webhook | `true` |

## Dalston

| Variable | Description | Default |
|----------|-------------|---------|
| `DALSTON_BASE_URL` | Dalston server base URL | - |
| `DALSTON_API_KEY` | Dalston API key | - |
| `DALSTON_MODEL` | Model to request | - |

## LLM Providers (for cleaning/summarization)

| Variable | Description | Default |
|----------|-------------|---------|
| `LLM_PROVIDER` | Pipeline LLM: `openai`, `anthropic`, `gemini`, `mistral`, `ollama` | `openai` |
| `OPENAI_API_KEY` | OpenAI API key | - |
| `OPENAI_MODEL` | OpenAI model | `gpt-5.2` |
| `OPENAI_REASONING_EFFORT` | Reasoning effort for GPT-5.x: `none`/`low`/`medium`/`high`/`xhigh` | - (unset) |
| `ANTHROPIC_API_KEY` | Anthropic API key | - |
| `ANTHROPIC_MODEL` | Anthropic model | `claude-sonnet-4-5-20250929` |
| `GEMINI_API_KEY` | Google Gemini API key | - |
| `GEMINI_MODEL` | Gemini model | `gemini-3-pro-preview` |
| `GEMINI_THINKING_LEVEL` | Thinking level: `low`/`high` for Pro, `minimal`/`low`/`medium`/`high` for Flash | - (unset) |
| `MISTRAL_API_KEY` | Mistral AI API key | - |
| `MISTRAL_MODEL` | Mistral model | `mistral-large-latest` |
| `OLLAMA_BASE_URL` | Ollama server URL | `http://localhost:11434` |
| `OLLAMA_MODEL` | Ollama model | `gemma3:4b` |

The API key matching `LLM_PROVIDER` is required at startup; the others
are optional.

## Episode Management

| Variable | Description | Default |
|----------|-------------|---------|
| `MAX_EPISODES_PER_PODCAST` | Limit episodes tracked per podcast | - (no limit) |
| `DELETE_AUDIO_AFTER_PROCESSING` | Delete audio after each stage | `false` |
| `REFRESH_MAX_WORKERS` | Parallel workers for `thestill refresh` (see [spec #19](../specs/19-refresh-performance.md)) | `1` |
| `REFRESH_MAX_PER_HOST` | Cap on concurrent HTTP fetches per host during refresh | `2` |
| `REFRESH_QUARANTINE_PROBE_INTERVAL_SECONDS` | Spec #60: how long a `feed_gone`/`invalid_content` quarantine sits before one automatic re-probe (`auth_required`/`blocked_unsafe` are never auto-probed) | `604800` (weekly) |

### MAX_EPISODES_PER_PODCAST

Prevents database from becoming unmanageable for podcasts with hundreds of episodes:

- Only the N most recent episodes (by `pub_date`) are kept per podcast
- Already-processed episodes are never removed, even if total exceeds limit
- New unprocessed episodes fill available slots up to the limit
- Applied during `thestill refresh` command
- Override per-run: `thestill refresh --max-episodes 10`

**Example**: With limit of 50 and podcast has 200 episodes:

- First refresh: Discovers 50 most recent episodes
- After processing 10: Next refresh keeps those 10 processed + 40 most recent unprocessed
- Result: Always stays at ≤50 total episodes per podcast

### DELETE_AUDIO_AFTER_PROCESSING

Saves disk space by removing intermediate audio files:

- After successful **downsampling**: Deletes the original audio file (MP3/M4A)
- After successful **transcription**: Deletes the downsampled audio file (WAV)
- Database path fields are cleared to indicate files no longer exist
- Episode state is preserved (determined by furthest completed stage)

**Important**: Once deleted, audio files must be re-downloaded to re-process.

## Processing & Concurrency

| Variable | Description | Default |
|----------|-------------|---------|
| `MAX_WORKERS` | Thread pool size for parallel processing | `3` |
| `PARALLEL_JOBS` | Default per-stage task queue capacity | `1` |
| `DOWNLOAD_PARALLEL_JOBS` | Per-stage override for the download stage | - (falls back to `PARALLEL_JOBS`) |
| `DOWNSAMPLE_PARALLEL_JOBS` | Per-stage override for the downsample stage | - (falls back to `PARALLEL_JOBS`) |
| `TRANSCRIBE_PARALLEL_JOBS` | Per-stage override for the transcribe stage | - (falls back to `PARALLEL_JOBS`) |
| `CLEAN_PARALLEL_JOBS` | Per-stage override for the clean stage | - (falls back to `PARALLEL_JOBS`) |
| `SUMMARIZE_PARALLEL_JOBS` | Per-stage override for the summarize stage | - (falls back to `PARALLEL_JOBS`) |
| `EXTRACT_ENTITIES_PARALLEL_JOBS` | Per-stage override for entity extraction | - (falls back to `PARALLEL_JOBS`) |
| `RESOLVE_ENTITIES_PARALLEL_JOBS` | Per-stage override for entity resolution | - (falls back to `PARALLEL_JOBS`) |
| `REINDEX_PARALLEL_JOBS` | Per-stage override for corpus reindexing | - (falls back to `PARALLEL_JOBS`) |
| `REFRESH_FEED_PARALLEL_JOBS` | Per-stage override for queued feed refreshes | `2` |
| `CHUNK_DURATION_MINUTES` | Audio chunk length for chunked transcription | `30` |
| `CLEANUP_DAYS` | Age threshold for cleanup of old artefacts | `30` |
| `DEBUG_CLIP_DURATION` | Clip audio to N seconds for debugging | - (unset = full audio) |

## Refresh Scheduler (spec #48)

Per-feed adaptive (AIMD) refresh intervals, optionally driven by a
background scheduler in the web server. Ships dark — both queue mode and
the scheduler are off by default.

| Variable | Description | Default |
|----------|-------------|---------|
| `REFRESH_DEFAULT_INTERVAL_SECONDS` | Seeded/initial per-feed refresh interval | `3600` |
| `REFRESH_MIN_INTERVAL_SECONDS` | AIMD lower clamp — never poll a feed faster than this | `900` |
| `REFRESH_MAX_INTERVAL_SECONDS` | AIMD upper clamp — back off no slower than this | `86400` |
| `REFRESH_VIA_QUEUE` | Enqueue `REFRESH_FEED` tasks instead of running the inline batch | `false` |
| `REFRESH_SCHEDULER_ENABLED` | Run the background tick that enqueues due feeds | `false` |
| `REFRESH_SCHEDULER_TICK_SECONDS` | How often the scheduler scans for due feeds (granularity, not poll interval) | `60` |

## Queue Auto-Heal, Circuit Breaker & Watchdog (spec #49)

The task worker auto-requeues infra-class failures once the dependency
recovers, pauses a stage via a circuit breaker while a dependency is
down, and frees slots held by wedged handlers.

| Variable | Description | Default |
|----------|-------------|---------|
| `QUEUE_AUTO_HEAL` | Auto-requeue infra-class `failed` tasks | `true` |
| `QUEUE_HEAL_INTERVAL_SECONDS` | How often the heal loop sweeps for healable tasks | `300` |
| `QUEUE_HEAL_COOLDOWN_MINUTES` | Minimum age since last failure before requeue | `10` |
| `QUEUE_MAX_HEAL_ATTEMPTS` | Per-task cap on auto-heal rounds | `2` |
| `QUEUE_CIRCUIT_BREAKER` | Per-stage circuit breaker on repeated infra failures | `true` |
| `QUEUE_CIRCUIT_FAILURE_THRESHOLD` | Infra failures within the window that trip a stage OPEN | `3` |
| `QUEUE_CIRCUIT_WINDOW_SECONDS` | Rolling window over which failures are counted | `120` |
| `QUEUE_CIRCUIT_COOLDOWN_SECONDS` | How long a breaker stays OPEN before a half-open probe | `60` |
| `QUEUE_STAGE_WATCHDOG_SECONDS` | Uniform handler watchdog timeout for every stage; `0` disables everywhere | - (unset = per-stage defaults) |

## Transcript Cleaning (legacy inline path)

These variables configure the legacy inline cleaning that can run during the
transcription step only. The standalone `thestill clean-transcript` pipeline
stage ignores them — it uses the main `LLM_PROVIDER` configuration above.

| Variable | Description | Default |
|----------|-------------|---------|
| `ENABLE_TRANSCRIPT_CLEANING` | Enable legacy inline LLM cleaning during transcription | `false` |
| `CLEANING_PROVIDER` | Provider: `gemini`, `ollama`, `openai`, `anthropic` | `gemini` |
| `CLEANING_MODEL` | Model to use | `gemini-3-flash-preview` |
| `CLEANING_CHUNK_SIZE` | Max tokens per chunk | `20000` |
| `CLEANING_OVERLAP_PCT` | Overlap between chunks | `0.15` |
| `CLEANING_EXTRACT_ENTITIES` | Extract names/terms for consistency | `true` |

See [transcript-cleaning.md](transcript-cleaning.md) for details on the
current cleaning pipeline.

## Eval Judge (spec #53)

| Variable | Description | Default |
|----------|-------------|---------|
| `EVAL_JUDGE_PROVIDER` | LLM-as-judge provider for `thestill eval run`, pinned independently of the pipeline LLM | `` (falls back to pipeline, marked unpinned) |
| `EVAL_JUDGE_MODEL` | Judge model — use a dated snapshot, not a floating alias | `` (provider's configured model) |
| `EVAL_JUDGE_TEMPERATURE` | Judge sampling temperature | `0.0` |

Prefer a judge from a different model family than the one producing the
judged artifacts (self-preference bias). See [evals.md](evals.md).

## Web Server

The bind address and port are CLI flags on `thestill server`, not
environment variables:

```bash
thestill server --host 127.0.0.1 --port 8000   # or -h / -p
```

Both default as shown. The only port-related environment variable is
`WEBHOOK_SERVER_PORT`:

| Variable | Description | Default |
|----------|-------------|---------|
| `WEBHOOK_SERVER_PORT` | Port advertised for transcription webhook callbacks | `8000` |

## Authentication

| Variable | Description | Default |
|----------|-------------|---------|
| `MULTI_USER` | Enable multi-user mode with Google OAuth | `false` |
| `GOOGLE_CLIENT_ID` | Google OAuth 2.0 Client ID | - |
| `GOOGLE_CLIENT_SECRET` | Google OAuth 2.0 Client Secret | - |
| `JWT_SECRET_KEY` | Secret for JWT signing (required in every mode — startup fails if unset; generate with `openssl rand -hex 32`) | - |
| `JWT_ALGORITHM` | JWT signing algorithm | `HS256` |
| `JWT_EXPIRE_DAYS` | JWT token expiration in days | `30` |

### Single-User Mode (Default)

When `MULTI_USER=false`:

- No login required
- All API endpoints are accessible without authentication
- A default user is automatically created for ownership tracking
- Best for personal deployments

### Multi-User Mode

When `MULTI_USER=true`:

- Google OAuth authentication required
- Users must log in to access the application
- Each user's data is isolated
- Requires Google OAuth credentials:

1. Go to [Google Cloud Console](https://console.cloud.google.com/apis/credentials)
2. Create OAuth 2.0 Client ID (type: "Web application")
3. Add authorized redirect URI: `http://localhost:8000/auth/google/callback`
4. Copy Client ID and Secret to `.env`

## Deployment & Web Surface (spec #25)

| Variable | Description | Default |
|----------|-------------|---------|
| `ENVIRONMENT` | Deployment mode: `production` or `development` | `production` |
| `COOKIE_SECURE` | Set the `Secure` flag on auth cookies | `true` |
| `ALLOWED_ORIGINS` | Comma-separated CORS origins | - (empty) |
| `TRUSTED_PROXIES` | Comma-separated proxy IPs whose forwarded headers are honored | - (empty) |
| `PUBLIC_BASE_URL` | Operator-declared external base URL (OAuth callbacks, email links) | - |
| `ENABLE_DOCS` | Expose FastAPI docs endpoints (`/docs`, `/redoc`) | `false` |
| `MAX_AUDIO_BYTES` | Upper bound on downloaded audio size | `2147483648` (2 GiB) |
| `MAX_WEBHOOK_BODY_BYTES` | Upper bound on webhook request bodies | `1048576` (1 MiB) |

Startup guards:

- `COOKIE_SECURE=false` is refused when `ENVIRONMENT=production` —
  switch to `ENVIRONMENT=development` to opt out locally.
- `MULTI_USER=true` requires `PUBLIC_BASE_URL`, so the OAuth redirect
  URI can never be derived from an attacker-controllable `Host` header.

## Rate Limits

In-memory per-client rate limiting on the sensitive web surfaces.

| Variable | Description | Default |
|----------|-------------|---------|
| `RATE_LIMIT_AUTH_MAX` | Auth endpoint requests per window | `10` |
| `RATE_LIMIT_AUTH_WINDOW_SECONDS` | Auth window length | `60` |
| `RATE_LIMIT_WEBHOOK_MAX` | Webhook requests per window | `60` |
| `RATE_LIMIT_WEBHOOK_WINDOW_SECONDS` | Webhook window length | `60` |
| `RATE_LIMIT_MCP_MUTATION_MAX` | MCP mutating tool calls per window | `30` |
| `RATE_LIMIT_MCP_MUTATION_WINDOW_SECONDS` | MCP mutation window length | `60` |

## Briefings & Inbox

| Variable | Description | Default |
|----------|-------------|---------|
| `NARRATION_ENABLED` | Enable text-to-speech narration of briefings | `false` |
| `NARRATION_DEFAULT_DURATION_SECONDS` | Target narration length | `300` |
| `INBOX_SEED_ON_FOLLOW` | Recent episodes seeded into the inbox when following a podcast | `2` |
| `BRIEFING_MIN_INTERVAL_SECONDS` | Minimum gap between briefings per user | `21600` (6h) |
| `BRIEFING_READINESS_GRACE_MINUTES` | How long to wait for in-flight episodes before generating anyway | `60` |

## Briefing Scheduler (spec #50)

Ships dark; flip `BRIEFING_SCHEDULER_ENABLED=true` per deployment.

| Variable | Description | Default |
|----------|-------------|---------|
| `BRIEFING_SCHEDULER_ENABLED` | Run the background tick that generates briefings at each user's scheduled hour | `false` |
| `BRIEFING_SCHEDULER_TICK_SECONDS` | How often the scheduler scans for due schedules (granularity, not cadence) | `60` |
| `BRIEFING_SCHEDULER_MAX_PER_TICK` | Cap on briefings generated per tick | `50` |

## Briefing Email Delivery (spec #51)

Scheduled briefings can be emailed to each user when their slot fires.
Delivery is opt-in per user (an "Email each briefing to me" checkbox on
the briefing schedule in Settings) and disabled globally until an email
provider is configured. The delivery pass runs inside the briefing
scheduler tick, so `BRIEFING_SCHEDULER_ENABLED=true` is required for
sends to happen.

| Variable | Description | Default |
|----------|-------------|---------|
| `EMAIL_PROVIDER` | `smtp`, `ses`, or `none` (delivery off globally) | `none` |
| `EMAIL_FROM` | From address, display form allowed (`Thestill <briefings@example.com>`) | - |
| `SMTP_HOST` | SMTP relay host (required for `smtp`) | - |
| `SMTP_PORT` | SMTP relay port | `587` |
| `SMTP_USERNAME` | SMTP auth username (empty = no auth) | - |
| `SMTP_PASSWORD` | SMTP auth password | - |
| `SMTP_STARTTLS` | Upgrade the connection with STARTTLS | `true` |
| `SES_REGION` | AWS SES region (required for `ses`; uses the ambient AWS credential chain) | - |
| `BRIEFING_EMAIL_MAX_ATTEMPTS` | Send attempts before a delivery parks as `failed` | `3` |
| `BRIEFING_EMAIL_BACKOFF_SECONDS` | First-retry delay, doubled per attempt | `300` |
| `UNSUBSCRIBE_SECRET` | Signs unsubscribe tokens; falls back to `JWT_SECRET_KEY` | - |

The `ses` provider needs `boto3`, which is not in the base install —
install it with `pip install "thestill[ses]"` (already present if the
deployment uses the `[s3]` storage extra).

Also required when a provider is configured:

- `PUBLIC_BASE_URL` — email bodies link back to episodes, the in-app
  briefing, and the unsubscribe page with absolute URLs.
- `UNSUBSCRIBE_SECRET` (recommended) — signs the one-click unsubscribe
  token (`/unsubscribe/briefings?token=…`, honored without login per
  CAN-SPAM/RFC 8058). Unset, it falls back to `JWT_SECRET_KEY`; set it
  explicitly so rotating the auth secret never dead-links the
  unsubscribe URLs in already-delivered email.

A misconfigured provider (e.g. `EMAIL_PROVIDER=smtp` without
`SMTP_HOST`) fails at server startup rather than silently at send time.
Each briefing is emailed at most once — deliveries are tracked in the
`briefing_deliveries` table with bounded retries, and a failed send never
blocks briefing generation.

## Corpus Search (sqlite-vec)

Hybrid lexical + semantic search over transcript chunks lives in the
`chunks`, `chunks_vec`, and `chunks_fts` tables of `podcasts.db`. The
embedding model that produces vectors for both indexing and querying
is configurable.

| Variable | Description | Default |
|----------|-------------|---------|
| `EMBEDDING_MODEL` | sentence-transformers model name (must be in `EMBEDDING_MODEL_DIMS`) | `sentence-transformers/paraphrase-multilingual-MiniLM-L12-v2` |

**Default model** is multilingual (50+ languages including Croatian,
German, French, Polish, Mandarin, Japanese, Arabic) at 384 dim and
~470 MB on disk.

**English-only swap** for ~5–10% better English recall:

```bash
EMBEDDING_MODEL=BAAI/bge-small-en-v1.5
```

Both ship in the `[entities]` optional extra. Adding a model with a
different dimension requires extending `EMBEDDING_MODEL_DIMS` in
`thestill/search/base.py` and re-running the chunks migration against
an empty `chunks` table.

**Backfill the index** for an existing corpus:

```bash
make corpus-backfill         # or: thestill chunks backfill
```

`thestill status` reports current chunk count and the embedding model
in use.

## Entity Enrichment (spec #45)

Wikipedia/Wikidata lookups that enrich resolved entities.

| Variable | Description | Default |
|----------|-------------|---------|
| `ENRICHMENT_REQUEST_DELAY_SEC` | Delay between outbound enrichment requests | `0.5` |
| `ENRICHMENT_WIKIPEDIA_LANG` | Wikipedia language edition | `en` |
| `ENRICHMENT_MAX_AGE_DAYS` | Re-enrich entities older than this | `30` |
| `ENRICHMENT_USER_AGENT` | User-Agent for Wikipedia/Wikidata requests | `thestill-podcast-pipeline/0.1 (https://github.com/sasasarunic/thestill)` |
| `ENRICHMENT_MAX_PER_TASK` | Cap on entities enriched per queue task | `200` |

## Logging

Structured logging via `structlog` — see
[logging-configuration.md](logging-configuration.md) for the full guide.

| Variable | Description | Default |
|----------|-------------|---------|
| `LOG_LEVEL` | `DEBUG`, `INFO`, `WARNING`, `ERROR`, `CRITICAL` | `INFO` |
| `LOG_FORMAT` | `console`, `json`, `ecs`, `gcp`, `cloudwatch`, or `auto` (TTY-detected) | `auto` |
| `LOG_FILE` | Optional file path for log output | - (stderr only) |
| `SERVICE_NAME` | Service name stamped on log records (`gcp` format only) | `thestill` |
| `SERVICE_VERSION` | Service version stamped on log records (`gcp` format only) | `1.0.0` |

## MCP Server

The MCP server logs through the shared logging setup — use `LOG_LEVEL`,
`LOG_FORMAT`, and `LOG_FILE` above.

| Variable | Description | Default |
|----------|-------------|---------|
| `MCP_SESSION_KEY` | Per-session quota key for MCP rate limiting | - (random per-process key) |
| `THESTILL_ENV_FILE` | Absolute path to the `.env` to load — useful for MCP clients like Claude Desktop that launch servers with CWD=`$HOME` | - (walk upward from package/CWD) |

## Security & Misc

| Variable | Description | Default |
|----------|-------------|---------|
| `URL_GUARD_ALLOWLIST` | Comma-separated hostnames exempted from the SSRF URL guard (e.g. a Dalston on `localhost`) | - (empty) |

## Configuration Hierarchy

1. Environment variables (highest priority)
2. `.env` file
3. Code defaults (lowest priority)

## Example .env

```bash
# Storage
STORAGE_PATH=./data

# Transcription
TRANSCRIPTION_PROVIDER=google
GOOGLE_APP_CREDENTIALS=/path/to/credentials.json
GOOGLE_CLOUD_PROJECT_ID=my-project
ENABLE_DIARIZATION=true

# LLM (for cleaning/summarization)
OPENAI_API_KEY=sk-...

# Episode management
MAX_EPISODES_PER_PODCAST=50
DELETE_AUDIO_AFTER_PROCESSING=false

# Cleaning (standalone clean-transcript stage uses LLM_PROVIDER above;
# the CLEANING_* vars only affect legacy inline cleaning during transcription)
LLM_PROVIDER=openai
OPENAI_MODEL=gpt-5.2
```
