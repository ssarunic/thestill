# thestill (Python package)

## Overview

The `thestill` package is the whole backend: the `thestill` CLI
(`thestill/cli.py`), structured logging (`thestill/logging.py`), and the
subpackages that implement the podcast pipeline (refresh → download →
downsample → transcribe → clean → summarize → entities), the web server, and
the MCP server. `pyproject.toml` exposes two entry points:
`thestill = "thestill.cli:main"` and `thestill-mcp = "thestill.mcp.server:main"`.

The CLI is a thin shell. `main` builds every repository and service once into
a `CLIContext` held in `ctx.obj`, and each command calls a service or
repository from there. Most admin and curation commands go through the same
audited services the web admin API uses, acting as `Actor.cli()`. The
exceptions write the repository directly, with no `Actor` and no audit row:
`mention drop`, `mention repoint`, `entity-alias-add`,
`set-entity-visibility`, `episode set-guests`, `podcast set-hosts` and
`podcast set-recurring`.

## Interface

### Local files

- `thestill/__init__.py`: `__version__`, `__author__`, `__description__`;
  calls `ensure_ffmpeg_on_path()` on import.
- `thestill/logging.py`: `configure_structlog()`, `get_logger(name)`,
  `get_log_level()`, `get_log_format()`, `_cloudwatch_processor`.
- `thestill/cli.py:CLIContext`: dependency container (config, path manager,
  repositories, services, `abuse`, `analytics_service`, `search_backend`, …).
- `thestill/cli.py:require_config`: decorator that exits 1 when `ctx.obj` is
  unset.
- `thestill/cli.py:main`: root `click` group (`--config/-c`, `--quiet/-q`).

### CLI commands (`thestill/cli.py`)

Every command is a `@main.command` or a subcommand of a `@main.group` in
`thestill/cli.py`; `thestill --help` (and `thestill <group> --help`) is the
authoritative list. The commands people run day to day are documented in
[CLAUDE.md](../CLAUDE.md#cli-commands) and `docs/`, which
`tests/docs/test_cli_drift.py` keeps in step with the code. This contract
covers only the cross-command invariants and side effects below.

### Subpackages

- [core](./core/EGM.md): atomic pipeline processors and the task queue that
  runs them (`TaskStage` chain, `QueueManager` / `PostgresQueueManager`,
  `TaskWorker`, schedulers), plus transcribers, LLM providers, cleaning,
  summaries, entities, embeddings and platform-link matchers.
- [evals](./evals/EGM.md): append-only LLM-as-judge eval runs behind
  `thestill eval`, the entity-linking cutover gate and the search golden set.
- [mcp](./mcp/EGM.md): MCP server (24 tools, `thestill://` resources) over
  stdio (`thestill-mcp`) and the remote HTTP connector; per-tool scopes fail
  closed.
- [migrations](./migrations/EGM.md): Alembic environment and the raw-SQL
  revision chain for the Postgres schema.
- [models](./models/EGM.md): Pydantic and dataclass models shared by every
  layer; no I/O.
- [repositories](./repositories/EGM.md): persistence layer with one SQLite and
  one Postgres implementation per interface; `make_repositories` picks the
  backend from `DATABASE_URL`.
- [search](./search/EGM.md): lexical, semantic and hybrid corpus search
  (`SqliteVecBackend`, `PgVectorBackend`) and the related-episodes builders.
- [services](./services/EGM.md): business logic shared by the CLI, web API
  and MCP server, including abuse prevention and narration.
- [utils](./utils/EGM.md): `Config`, `PathManager`, file storage, SSRF and
  log safety, tokens, DB connection helpers.
- [web](./web/EGM.md): FastAPI app, routes, middleware, remote MCP and OAuth,
  public pages, and the React frontend.

## Invariants

- Every command is wrapped in `@require_config`; a failure while `main` builds
  services prints `Configuration error` and exits 1, so `ctx.obj` stays unset
  [`cli.py:require_config`, `cli.py:main`].
- `main` loads the env file (`load_env_file`, honouring `--config` and
  `THESTILL_ENV_FILE`) before `configure_structlog()` [`cli.py:main`].
- Importing the package puts ffmpeg on `PATH` before any subprocess or pydub
  call [`__init__.py:ensure_ffmpeg_on_path`].
- `--dry-run` returns before any write, download or LLM call on the pipeline
  commands (`download`, `downsample`, `clean-transcript`, `transcribe`,
  `summarize`, `resolve-entities`, `eval run`, `rebuild-entities`, `reindex`,
  `chunks backfill`) [`cli.py` per command], except for two steps of
  `transcribe` that run before its dry-run return (`cli.py:2191`): on
  ElevenLabs the webhook server starts first (`cli.py:1934-1966`), and on
  Google `--cancel-pending` calls `reset_pending_operations()`
  (`cli.py:2010-2025`).
- `refresh --dry-run` fetches the feeds and reports what it would discover,
  but saves nothing and enqueues nothing [`cli.py:refresh`,
  `services/refresh_service.py:RefreshService.refresh`].
- Known issue: `transcribe --dry-run --cancel-pending` on Google still
  cancels running operations and downloads finished ones
  (`cli.py:2010-2025`, `core/google_transcriber.py:reset_pending_operations`).
- Destructive repair commands report only unless `--apply` (or `--write`):
  `clean-aliases`, `repair-anchor-mentions`, `repair-mojibake`,
  `entity prune-orphans`, `episode fold-import-twins`,
  `resolve-summary-citations --write` [`cli.py` per command].
- Pipeline order is enforced by inputs: `transcribe` uses only downloaded
  episodes with downsampled audio; `summarize` only episodes whose clean
  transcript exists; a new raw transcript resets `clean_transcript_path` and
  `summary_path` to empty [`cli.py:transcribe`, `cli.py:summarize`].
- `--max-episodes` on `download` and `downsample` is a global total across
  podcasts [`cli.py:download`, `cli.py:downsample`].
- `refresh` and `repair-images` exit 1 when any podcast failed
  [`cli.py:refresh`, `cli.py:repair_images`].
- `reprocess` only queues work for the web server's worker
  (`queue_reprocess`); it runs nothing in-process [`cli.py:reprocess`].
- Entity curation (`entity …`, `resolution-blacklist add`) goes through
  `EntityCurationService` with `Actor.cli()` and the abuse audit log;
  `AdminActionError` exits 1 [`cli.py:_curate`].
- `entity merge` keeps LOSER's name as an alias of KEEPER when it resembles
  KEEPER's name (`is_alias_of`); a name that does not is reported as skipped,
  and LOSER's own aliases are listed as dropped, not carried over.
  `--no-alias` keeps no name (`keep_name=False`). `--dry-run` reports the
  alias outcome without writing [`cli.py:entity_merge`,
  `services/entity_curation.py:EntityCurationService.merge`].
- Transcript edits go through `TranscriptEditService` with
  `base_seq=last_seq(episode_id)` as an optimistic-concurrency base; edits
  never rewrite transcript files [`cli.py:_run_transcript_action`,
  `cli.py:transcript_rename_speaker`].
- `user` and `admin` commands require `config.multi_user`; refusals surface as
  `AdminActionError` and exit 1 [`cli.py:_account_admin`, `cli.py:_act_on`].
- `settings set` validates the value with `get_spec(key).parse`
  [`cli.py:settings_set`].
- `rebuild-entities` asks for confirmation unless `--podcast-id` or `--yes`,
  and skips episodes that already have a pending `EXTRACT_ENTITIES` task
  [`cli.py:rebuild_entities`].
- `relink-direct` refuses when open tasks exceed `--max-open-tasks` (default
  2000) [`cli.py:relink_direct`].
- `podcast set-hosts`, `podcast set-recurring`, `episode set-guests`,
  `set-entity-visibility`, `entity-alias-add`, `mention drop` and
  `mention repoint` write the entity repository directly, with no `Actor` and
  no audit row [`cli.py:podcast_set_hosts`, `cli.py:podcast_set_recurring`,
  `cli.py:episode_set_guests`, `cli.py:set_entity_visibility`,
  `cli.py:entity_alias_add`, `cli.py:mention_drop`, `cli.py:mention_repoint`].
- `entity get` shows at most 5 recent mentions, in both the text and the
  `--json` output [`cli.py:entity_get`].
- `podcast detect-hosts` with no speaker data points at
  `thestill rebuild-entities --podcast-id <id>` [`cli.py:podcast_detect_hosts`].
- `LOG_FORMAT=auto` resolves to `console` on a TTY, otherwise `json`; for
  `console`, `json`, `ecs` and `cloudwatch` the processor order is fixed and
  includes `log_safety_processor` [`logging.py:get_log_format`,
  `logging.py:configure_structlog`].
- Known issue: `LOG_FORMAT=gcp` replaces the whole chain with
  `structlog_gcp.build_processors(...)` (`logging.py:174-183`), so
  `log_safety_processor` never runs and secrets are not redacted.

## Side Effects

- Global `structlog.configure`; with `LOG_FILE`, a 100 MB × 5
  `RotatingFileHandler` on the root logger [`logging.py:configure_structlog`].
- `main` builds repositories through `make_repositories` (SQLite, or Postgres
  when `DATABASE_URL` is set) and the search backend through
  `make_search_backend` [`cli.py:main`].
- Network: feed fetches (`refresh`, `repair-images`), audio downloads
  (`download`), transcription providers (`transcribe`), LLM calls
  (`clean-transcript`, `summarize`, `facts extract`, `narrate`, `eval`),
  Wikidata/Wikipedia (`resolve-entities`, `enrich-entities`,
  `backfill-entity-types`), IndexNow (`set-podcast-visibility`), alert
  channels and email (`alerts`) [`cli.py` per command].
- Files under the storage root: `original_audio/`, `downsampled_audio/`,
  `raw_transcripts/`, `clean_transcripts/`, `summaries/`, facts files,
  narrations, eval runs, entity pages; `cleanup` deletes old audio
  [`cli.py` per command].
- Task queue: `refresh --queue` (`REFRESH_FEED`), `rebuild-entities` and
  `reindex` (`EXTRACT_ENTITIES`), `relink-direct` and `clean-aliases`
  (resolve-entities), `reprocess`, `transcript refresh` and
  `sync-track backfill` [`cli.py` per command].
- Webhook server: `transcribe` on ElevenLabs starts a background webhook
  server (or reuses a running `thestill server`) and waits until each
  submitted episode's ElevenLabs pending operation is settled by the webhook,
  or its raw transcript changes. It waits on stored state, so it finishes
  whichever process receives the callback [`cli.py:_wait_for_webhook_transcripts`].
- `facts edit` launches `$EDITOR` (default `nano`) [`cli.py:facts_edit`].
- `server` runs `uvicorn.run(create_app(config))`, default host 127.0.0.1;
  `--reload` forces one worker [`cli.py:server`].

## Verification

- Run: `make test` (`./venv/bin/pytest --cov=thestill … --ignore=tests/e2e`).
- Unit tests only: `./venv/bin/pytest tests/unit`.
