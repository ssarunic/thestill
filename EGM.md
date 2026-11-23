# thestill Blueprint

## System Overview

thestill turns podcast feeds into transcripts, cleaned and summarised episodes,
a linked entity graph, per-user inboxes and briefings, and a searchable corpus.
Callers reach it through three surfaces that share one service and repository
layer:

- the `thestill` CLI ([`thestill/cli.py`](./thestill/EGM.md));
- the FastAPI web server and React SPA ([`thestill/web`](./thestill/web/EGM.md));
- the MCP server, over stdio (`thestill-mcp`) or the remote `/mcp` HTTP
  connector ([`thestill/mcp`](./thestill/mcp/EGM.md)).

The layering, data flow and task-queue design are described in
[specs/01-architecture.md](./specs/01-architecture.md). These contracts do not
restate them. They record, per directory, the code-level interface, the
invariants and the side effects, cited as `file:symbol`. The stage list itself
lives in `thestill/core/queue_manager.py:TaskStage`
([core](./thestill/core/EGM.md)).

## Subdirectories & System Boundaries

- thestill: [./thestill/EGM.md](./thestill/EGM.md) - the Python package: the
  CLI, structured logging, and the `core`, `services`, `repositories`,
  `models`, `search`, `mcp`, `web`, `utils`, `migrations` and `evals`
  subpackages.
- deploy: [./deploy/EGM.md](./deploy/EGM.md) - deployment topologies; today
  one single-EC2 kit (`thestill-aws`, compose stack with Caddy and pgvector)
  driven by the `v*`-tag deploy workflow.
- evaluation: [./evaluation/EGM.md](./evaluation/EGM.md) - standalone,
  resumable transcript-quality evaluation over 20 fixed episodes; nothing in
  the app imports it.
- scripts: [./scripts/EGM.md](./scripts/EGM.md) - standalone backfills,
  audits, prototypes, market-data builders and a live MCP connector check
  (`check_connector.py`) outside the CLI.

`tests/` (test-only), `docs/` and `specs/` (prose), `data/`, `credentials/`
and `evaluation/outputs*` (gitignored data) have no contract.

## Global Invariants

- One backend switch: Postgres when `DATABASE_URL` is set, otherwise SQLite at
  `config.database_path`. Every repository interface has one implementation
  per backend, and both must behave the same
  ([repositories](./thestill/repositories/EGM.md):
  `factory.py:uses_postgres`).
- Postgres schema changes go into both `postgres_schema.py:SCHEMA_SQL` and a
  new Alembic revision under `thestill/migrations/versions`
  ([migrations](./thestill/migrations/EGM.md)).
- `Config` is built by `utils/config.py:load_config` / `load_env_file`, which
  fail fast on missing provider keys and unsafe production settings
  ([utils](./thestill/utils/EGM.md)). Some modules still read the environment
  directly: `logging.py` (`LOG_LEVEL`, `LOG_FORMAT`, `LOG_FILE`,
  `SERVICE_NAME`, `SERVICE_VERSION`), `utils/url_guard.py:103`
  (`URL_GUARD_ALLOWLIST`), `core/spotify_resolver.py:449-450`
  (`SPOTIFY_CLIENT_ID` / `SPOTIFY_CLIENT_SECRET`),
  `core/entity_resolver.py:240` (`REFINED_DATA_DIR`), `mcp/tools.py:114`
  (`MCP_SESSION_KEY`), `web/middleware/rate_limit.py:61` (rate-limit
  overrides), `web/app.py:967` (`THESTILL_STATIC_DIR`), and the `DALSTON_*` /
  `ELEVENLABS_*` fallbacks in `core/dalston_transcriber.py:81-82, 156-158`
  and `core/elevenlabs_transcriber.py:133, 141`. Alembic's
  `migrations/env.py:46-48` loads `.env` with bare `load_dotenv`, ignoring
  `THESTILL_ENV_FILE`.
- Storage paths stay inside the storage root, and slugs are validated by
  `PathManager` ([utils](./thestill/utils/EGM.md)).
- Outbound fetches of user-supplied URLs go through `utils/url_guard.py` (SSRF
  guard: public addresses only, every redirect hop re-validated).
  This includes the Apple-URL fallback
  (`core/media_source.py:_resolve_apple_podcast_redirect`), which fetches the
  page with `guarded_get` and only for the hosts in `_APPLE_PODCAST_HOSTS`.
- Admin and curation writes (accounts, settings, entity curation, transcript
  edits, import-twin folds) go through audited services acting as an `Actor`
  (`web`, `cli` or `system`); entity and transcript data is never fixed with
  raw SQL ([services](./thestill/services/EGM.md),
  [services/abuse](./thestill/services/abuse/EGM.md)). The CLI and the API
  share `EntityCurationService` and `PodcastCurationService`, which record the
  audit row themselves; `tests/unit/test_curation_writes_through_services.py`
  fails if `cli.py` or a route calls a curation write directly.
- Transcript edits are an append-only op log replayed by
  `core/transcript_edits.py:load_effective_transcript`; transcript files are
  never rewritten by an edit ([core](./thestill/core/EGM.md)).
- Inbox delivery is immutable: no API route deletes inbox rows, existing rows
  are never altered on re-delivery, and automation may only flip unread to
  read ([services](./thestill/services/EGM.md),
  [web/routes](./thestill/web/routes/EGM.md)).
- The web API denies by default: every `/api` router except auth and public
  pages requires a session; remote MCP tool scopes fail closed
  ([web](./thestill/web/EGM.md), [mcp](./thestill/mcp/EGM.md)). The session
  routers also honour read-only mode and are rate-limited
  (`require_session`); `/api/status`, `/api/dashboard` and `/api/admin` are
  mounted with `require_admin` only (`web/app.py:881, 896-898, 901`), so they
  skip `enforce_read_only` (which admins pass anyway) and `user_rate_limit`.
- Logging is structured (`structlog`). Every `LOG_FORMAT` except `gcp` passes
  through `log_safety_processor`, which redacts secrets
  ([thestill](./thestill/EGM.md), [utils](./thestill/utils/EGM.md)).
- Known issue: `LOG_FORMAT=gcp` uses `structlog_gcp.build_processors(...)` as
  the whole processor chain (`logging.py:174-183`); `log_safety_processor` is
  added only in the non-gcp branch (`logging.py:187-205`), so gcp output is
  never redacted.

## Global Side Effects

- Database: SQLite file `data/podcasts.db`, or Postgres with the `vector` and
  `pg_trgm` extensions; schema is created on first use, and Alembic runs on
  web startup when migrate-on-startup is enabled.
- File storage (local `data/` tree or S3): original and downsampled audio,
  raw and cleaned transcripts, summaries and sidecars, facts files,
  narrations, eval runs, entity pages.
- Network: RSS feeds, audio hosts and YouTube (yt-dlp); transcription
  providers (Dalston, ElevenLabs, Google Speech-to-Text, local
  Whisper/WhisperX/Parakeet); LLM providers (OpenAI, Anthropic, Gemini,
  Mistral, Ollama); Wikidata and Wikipedia; Apple, Spotify and YouTube
  link resolution; IndexNow; PostHog; SMTP/SES email; alert webhooks.
- Background threads in the web process: task worker, refresh and briefing
  schedulers, periodic housekeeping runner.
- Production infrastructure (deploy kit only): EC2, EIP, S3, IAM, SSM
  parameters, Route53, CloudWatch.

## System Verification

- Run: `make test` (Python suite via `./venv/bin/pytest`, excluding
  `tests/e2e`).
- Frontend: `cd thestill/web/frontend && npm run test:run && npm run lint`.
- Full checks: `make check`.
