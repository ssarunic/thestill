# utils

## Overview

Shared, mostly stateless helpers used by every layer (CLI, core, services,
repositories, web, MCP). The central piece is `config.py`, which loads `.env`
and env vars into a validated `Config`. That `Config` also builds the shared
`PathManager` and `FileStorage` backend. The rest falls into these groups:

- Path and storage safety.
- SSRF and URL handling.
- Log and prompt safety.
- Database connection helpers, plus the one-off SQLite → Postgres migration
  tools.
- Time, duration and briefing cadence.
- Text, HTML and encoding repair.
- Small classifiers: user agent, public page kind, episode origin, YouTube
  errors.

The pluggable storage backends are in the
[file_storage](./file_storage/EGM.md) subpackage.

## Interface

### Package and subpackage

- `__init__.py` - re-exports `ThestillError`, `html_to_plain_text`,
  `extract_links_from_html`.
- [file_storage](./file_storage/EGM.md) - `FileStorage` ABC, `FileMetadata`,
  `StorageError`, `LocalFileStorage`, `make_storage`, and the S3 backend
  (`file_storage/s3.py:S3FileStorage`).

### Configuration

- `config.py:Config` - pydantic model holding every setting. The environment
  variables behind its fields are documented in
  [docs/configuration.md](../../docs/configuration.md), which
  `tests/docs/test_env_var_drift.py` keeps in step with the code. One
  cross-module rule: `thestill/web/permissions.py:has_permission` grants
  `drift.align` only while `drift_alignment_enabled` is true.

  Its non-serialised `path_manager` and `file_storage` fields are filled in
  `__init__`. `get_parallel_jobs_per_stage()` returns each stage's capacity,
  falling back to `parallel_jobs`.
- `config.py:load_config(env_file=None)` - loads `.env`, reads every env var,
  validates the values, and returns `Config`.
- `config.py:load_env_file(env_file=None)` - the only sanctioned `.env` loader.
  It searches: explicit path, then `THESTILL_ENV_FILE`, then upward from the
  package, then upward from the CWD.
- `config.py:_find_dotenv_from_package` - also used by
  `thestill/migrations/env.py`.
- Standalone env getters (no `Config` needed):
  - Platform links: `is_platform_links_enabled`, `get_platform_links_*`.
  - `get_summary_profiles_enabled`.
  - Refresh scheduler: `get_default_refresh_interval_seconds`,
    `get_refresh_min_interval_seconds`, `get_refresh_max_interval_seconds`,
    `is_refresh_via_queue_enabled`, `is_refresh_scheduler_enabled`,
    `is_refresh_on_open_enabled`, `get_refresh_scheduler_tick_seconds`,
    `get_quarantine_probe_interval_seconds`.
  - Briefing scheduler: `get_briefing_scheduler_tick_seconds`,
    `get_briefing_scheduler_max_per_tick`.
  - `is_migrate_on_startup_enabled`.
  - Queue healing and circuit breaker: `is_queue_auto_heal_enabled`,
    `get_queue_heal_*`, `get_queue_max_heal_attempts`,
    `is_queue_exit_on_degraded_enabled`, `is_queue_circuit_breaker_enabled`,
    `get_circuit_*`.
  - Stage timeouts: `get_stage_watchdog_seconds`,
    `get_stale_timeout_seconds_per_stage`.
  - `get_related_incremental_pool_k`.

### Paths and storage

- `path_manager.py:PathManager(storage_path)` - single source of truth for the
  `data/` layout. Directory getters:
  `original_audio_dir`, `downsampled_audio_dir`, `raw_transcripts_dir`,
  `clean_transcripts_dir`, `summaries_dir`, `evaluations_dir`,
  `podcast_facts_dir`, `episode_facts_dir`, `debug_feeds_dir`,
  `pending_operations_dir`, `external_transcripts_dir`, `briefings_dir`,
  `narrations_dir`, `sync_tracks_dir`, `quote_cards_dir`, `corpus_*_dir`,
  `evaluation_runs_dir`, `external_transcript_dir_for_podcast`.
  Checked file getters (path root-checked; all but `briefing_dir` also
  validate their slug or run id):
  `raw_transcript_file_with_podcast`,
  `clean_transcript_file_with_podcast`, `external_transcript_file`,
  `podcast_facts_file`, `episode_facts_file`, `debug_feed_file`,
  `briefing_dir`, `sync_track_file`, `quote_card_file`, `corpus_entity_file`,
  `evaluation_run_dir`. `feeds_file` takes no argument.
  Flat file getters that join a caller-supplied filename unchecked:
  `original_audio_file`, `downsampled_audio_file`, `raw_transcript_file`,
  `clean_transcript_file`, `summary_file`, `evaluation_file`.
  Helpers: `ensure_directories_exist`, `file_exists`, `get_file_path`,
  `to_relative` (bridge to `FileStorage` keys), `require_file_exists`.
- `audio_integrity.py:assert_audio_file` and `InvalidAudioFile` - check the
  file's magic bytes against an allow-list (mp3, aac-adts, wav, ogg, flac,
  mp4 family).
- `ffmpeg_path.py:ensure_ffmpeg_on_path` - prepends a known Homebrew or
  MacPorts directory to `PATH` when `ffmpeg` and `ffprobe` cannot be found.

### Errors

- `exceptions.py` - `ThestillError(message, *, error_class=None, **context)`
  is the base class. Its subclasses are `TranscriptCleaningError`,
  `ProhibitedContentError`, `TransientError` (retried) and `FatalError` (sent
  to the DLQ). `StoragePathError` subclasses both `FatalError` and
  `ValueError`. `VALID_ERROR_CLASSES = {"fatal", "infra", "item"}`.

### URLs and network safety

- `url_guard.py` - `validate_public_url` returns a `ResolvedHost`.
  `guarded_session`, `guarded_get` and `guarded_redirect_fetch` build on it.
  Errors: `UnsafeURLError` with subclasses `URLResolutionError`,
  `UnsafeDestinationError` and `TooManyRedirects`.
- `url_patterns.py` - pre-compiled regexes and helpers:
  - YouTube: `is_youtube_url`, `is_remote_fetchable_audio_url`,
    `extract_youtube_video_id`, `find_youtube_video_ids`,
    `find_youtube_channel_urls`.
  - Spotify: `is_spotify_url`, `is_spotify_short_link`,
    `extract_spotify_entity`, `find_spotify_entities`.
  - Apple: `extract_apple_podcast_id`, `extract_apple_episode_id`,
    `is_apple_podcast_url`, `find_apple_show_links`.
  - RSS: `looks_like_rss`.
  - `ALL_PATTERNS` - every pattern above, for the ReDoS tests.
- `redirect_uris.py` - OAuth redirect policy: `LOOPBACK_HOSTS`,
  `is_loopback_host`, `is_loopback_uri`, `is_allowed_redirect_uri`.
- `url_generator.py:UrlGenerator` - `podcast`, `episode`, `episode_at`
  (`?t=`), `episode_summary`, `digest`. `episode_links(...)` returns
  `web_url`, `episode_url` and `audio_url`.
- `http_text.py` - `decode_feed_body(raw, content_type)` returns
  `(text, encoding)`. `response_text(resp)` replaces `requests` `.text`.
- `geoip.py:lookup_country_from_ip` (async) - best-effort ISO country code
  from ipinfo.io.

### Logging and safety

- `log_safety.py` - `log_safety_processor` (structlog processor),
  `redact_mapping`, `sanitize_control_chars`, `redact_capability_path`,
  `UvicornAccessRedactFilter`, `install_uvicorn_access_redaction`.
- `cli_logging.py:log_command` - decorator that binds `command_id` and logs the
  command lifecycle. `SENSITIVE_KEYS` lists the arguments it redacts.
- `timing.py:log_phase_timing` - context manager that emits a
  `feed_phase_timing` event.
- `logger.py:setup_logger` - legacy stdlib logger setup.
- `console.py:ConsoleOutput` - user-facing stdout/stderr messages, with a quiet
  mode.
- `cli_formatter.py:CLIFormatter` - `format_podcast_list`, `format_header`,
  `format_activity_log`.
- `prompt_safety.py` - `wrap_untrusted(content, label)`, `UNTRUSTED_OPEN`,
  `UNTRUSTED_CLOSE`, `UNTRUSTED_CONTENT_PREAMBLE`.

### Auth tokens

- `jwt.py` - `create_access_token` (includes a `jti`), `decode_token` (returns
  `TokenPayload` or `None`), `get_token_expiry`, `is_token_expiring_soon`.
- `unsubscribe_token.py` - `make_unsubscribe_token` and
  `verify_unsubscribe_token`: HMAC-SHA256 tokens, deliberately not JWTs.
  Each takes a `purpose` (`BRIEFING_PURPOSE`, the default, or
  `TIPS_PURPOSE`); a token only verifies for the purpose it was made for.

### Databases

- `sqlite_ext.py` - `connect(db_path, *, load_vec, row_factory)` (context
  manager), `load_vec_extension`, `maybe_load_vec_extension`,
  `SqliteVecNotInstalledError`, `BUSY_TIMEOUT_MS`.
- `postgres_ext.py` - `connect(dsn, *, vector=False)` returns a psycopg
  connection with `dict_row`. `as_str` converts UUIDs to strings.
- `db_migration.py` - SQLite → Postgres text mirror: `migrate_all`,
  `migrate_table`, `verify_parity` (returns `ParityReport` / `TableReport`),
  `discover_source_tables`, `MIRROR_SCHEMA`. Run it as
  `python -m thestill.utils.db_migration migrate|verify|tables`.
- `db_promotion.py` - promotes `sqlite_mirror` into the typed schema:
  `promote(dsn, embedding_dim=384)` and `verify(dsn)`. Run it as
  `python -m thestill.utils.db_promotion promote|verify`.
- `sql_like.py` - `escape_like_wildcards`, `substring_pattern`,
  `LIKE_ESCAPE_CLAUSE`.
- `ids.py:is_uuid`.

### Time and duration

- `datetime_utils.py` - `now_utc`, `ensure_utc`,
  `parse_struct_time_utc_or_none`, `parse_struct_time_utc`.
- `briefing_cadence.py` - `next_occurrence`, `next_run_for`,
  `latest_occurrence_at_or_before`, `latest_run_for`.
- `duration.py`:
  - Parsing: `parse_time_window` (to days), `parse_duration`,
    `parse_target_duration`, `resolve_target_or_default`.
  - `format_duration`.
  - Narration presets: `NARRATION_DURATION_PRESETS` and
    `slug_for_duration_seconds`.
  - Audio length via ffprobe: `get_audio_duration`,
    `get_audio_duration_minutes`, `get_audio_duration_float`.
  - `calculate_speed_ratio`, `format_speed_stats`.

### Text, language and taxonomy

- `html_utils.py` - `html_to_plain_text`, `count_html_tags`,
  `unescape_entities_stable`, `resolve_description_variants`,
  `extract_links_from_html`.
- `text_sanitizer.py` - `sanitize_text` returns `(text, removed_count)`.
  `repair_mojibake` returns `(text, changed)`.
- `slug.py:generate_slug` and `MAX_SLUG_LENGTH`.
- `language_config.py` - `LANGUAGE_CONFIG`, `LanguageSpec`,
  `normalize_language_code`, `resolve_language_spec`.
- `podcast_categories.py` - `APPLE_PODCAST_TAXONOMY`, `APPLE_GENRE_IDS` and
  `VALID_CATEGORIES`, loaded from `podcast_categories.json`. Helpers:
  `validate_category` (returns `ValidatedCategory`), `normalize_category_name`,
  `is_valid_category`, `is_valid_subcategory`, `get_subcategories`.

### Classifiers and miscellaneous

- `user_agent.py` - `classify_user_agent` returns `browser`, `bot` or
  `preview`. `crawler_family` names the crawler.
- `page_kind.py` - `public_page_kind` returns a `PublicPageKind`
  (`landing`, `top`, `podcast`, `episode`, `entity`, `share`, `legal`,
  `claude`) or `None`. `/claude` is the connector guide (spec #108).
  `signup_source_for` maps a kind to `SIGNUP_SOURCES`; `legal`, `claude` and
  non-public paths collapse to `direct`.
- `episode_origin.py:derive_episode_origin` - returns `(origin, import_kind)`
  from `canonical_id`.
- `youtube_errors.py` - `describe_youtube_failure` and
  `YOUTUBE_BOT_CHECK_MESSAGE`.
- `transcript_capabilities.py` - `classify_transcript_degeneracy`,
  `has_usable_segment_structure`.
- `device.py` - `is_cuda_available`, `is_mps_available`, `resolve_device`,
  `resolve_hybrid_devices`.
- `stdout_capture.py` - `StdoutProgressCapture` and
  `WHISPERX_PROGRESS_PATTERN`.

## Invariants

### Configuration

- `load_config` fails fast and raises `ValueError` for any of these:
  - The selected `LLM_PROVIDER` has no API key.
  - `ENVIRONMENT=production` with `COOKIE_SECURE=false`.
  - Narration bounds out of range: `NARRATION_STATED_TARGET_RATIO` outside
    0.5-1.0, `NARRATION_MATERIAL_MAX_WORDS <= 0`, or an empty anchor prompt.
  - `ENTITY_LINKER` is not `live` or `refined`.
  - A bad `ENTITY_LINKING_MIN_CONFIDENCE`, `ENTITY_LINKING_NONE_TTL_DAYS` or
    `WIKIDATA_MAX_RPS`.
  - A bad `INDEXNOW_KEY`, `SIGNUP_MODE`, `SIGNUP_ALLOWLIST`,
    `ALERT_SPEND_BREAKER_ACTION` or `ADMIN_DIGEST_HOUR`.
  - A negative `MCP_TOKEN_TTL_DAYS` or a non-positive
    `MCP_TOKEN_REQUESTS_PER_MINUTE`.
  - `MCP_OAUTH_ENABLED` without `MCP_HTTP_ENABLED`, or without an https (or
    loopback) `PUBLIC_BASE_URL`.
  - `MULTI_USER=true` without `PUBLIC_BASE_URL`.
  - `DATABASE_URL` set but `psycopg` not installed.

  All of these checks are in `config.py:load_config`.
- `Config.__init__` defaults `database_path` to `storage_path/podcasts.db` and
  builds `file_storage` through `make_storage`, which fails fast on a bad
  `STORAGE_BACKEND` (`config.py:Config.__init__`).
- `.env` loading must go through `load_env_file`. A bare `load_dotenv()` skips
  `THESTILL_ENV_FILE` (`config.py:load_env_file`).
- Known issue: `thestill/migrations/env.py:44-48` breaks that rule. It calls
  `load_dotenv(_find_dotenv_from_package())`, so a standalone `alembic` run
  ignores `THESTILL_ENV_FILE` and the CWD walk.
- `SYNC_TRACKS_ENABLED` and `DRIFT_ALIGNMENT_ENABLED`, like many fields that
  `load_config` parses inline, are true only for the value `true` (case
  insensitive). Fields read through `_env_bool` also accept `1`, `yes` and
  `on`.
- An env getter given an invalid int logs a warning and falls back to its
  default (`config.py:_env_int`). Booleans accept `1`, `true`, `yes` or `on`
  (`config.py:_env_bool`).
- `get_stage_watchdog_seconds` leaves `transcribe` unbounded by default.
  `QUEUE_STAGE_WATCHDOG_SECONDS=0` disables the watchdog everywhere. The stale
  timeout is never below the global window, and where a watchdog exists it is
  at least the watchdog plus a margin
  (`config.py:get_stale_timeout_seconds_per_stage`).

### Paths

- `path_manager.py:_validate_slug` requires slugs to match
  `^[a-z0-9][a-z0-9-]{0,99}$`. Run ids must match `_RUN_ID_RE`
  (`path_manager.py:_validate_run_id`).
- The checked getters (slug or id arguments, plus `briefing_dir` and
  `external_transcript_dir_for_podcast`) resolve the path and require it to
  stay under `storage_path`; otherwise `ValueError` is raised
  (`path_manager.py:PathManager._assert_inside_root`).
- The flat getters `original_audio_file`, `downsampled_audio_file`,
  `raw_transcript_file`, `clean_transcript_file`, `summary_file` and
  `evaluation_file` (and `get_file_path`, which dispatches to them) join the
  caller's filename onto their directory with no slug or root check
  (`path_manager.py:288-386, 419-429`). Callers must pass trusted, stored
  filenames.
- `path_manager.py:PathManager.to_relative` raises `StoragePathError` for a
  path outside the root. That error is fatal and is never retried.
- `path_manager.py:PathManager.sync_track_file` puts the generation in the file
  name, so a sync track file is never overwritten in place.

### Errors

- `error_class` must be in `VALID_ERROR_CLASSES`
  (`exceptions.py:ThestillError.__init__`). `TransientError` means retry with
  backoff; `FatalError` means DLQ (`exceptions.py` docstrings).

### SSRF and URL guards

- `url_guard.py:validate_public_url` accepts only http(s). Every resolved
  A/AAAA address must be public: `ip.is_global`, and not multicast, reserved
  or unspecified. DNS failure raises `URLResolutionError` (retryable); a
  forbidden target raises `UnsafeDestinationError`. Hostnames listed in
  `URL_GUARD_ALLOWLIST` are still resolved but skip the public-IP check. A
  bare-IP URL is checked with `_ip_is_public` before the allow-list is
  consulted, so listing a private IP literal does not admit it
  (`url_guard.py:validate_public_url`).
- Redirects are re-validated on every hop. `_GuardedHTTPAdapter.send` does this
  for sessions. `guarded_redirect_fetch` follows at most `_MAX_REDIRECTS`
  (10) hops and resolves each `Location` with `urljoin`.
- `redirect_uris.py:is_allowed_redirect_uri` accepts https with a host, http
  only on loopback, or a private-use scheme. It rejects
  `javascript`/`data`/`file`-type schemes, fragments and credentials.

### Log, prompt and auth safety

- `log_safety.py:redact_mapping` redacts the values of sensitive keys (token,
  secret, password, cookie, session, `code`, OAuth/CSRF `state`, and so on)
  and walks nested mappings. A number under a compound key, such as
  `input_tokens`, is kept. Control characters in strings are escaped.
  `log_safety_processor` must run last in the structlog chain.
- `/mcp/<capability>` paths are collapsed to `/mcp/<redacted>` in uvicorn
  access logs (`log_safety.py:redact_capability_path`).
- `prompt_safety.py:wrap_untrusted` strips any embedded `<<<UNTRUSTED_`
  sentinels before fencing the content.
- `jwt.py:is_token_expiring_soon` always verifies the signature, and returns
  `True` for an invalid or expired token. `jwt.py:decode_token` returns `None`
  rather than raising.
- `unsubscribe_token.py:verify_unsubscribe_token` uses `hmac.compare_digest`.
  Every failure returns `None`, and the token never expires.
  `unsubscribe_token.py:make_unsubscribe_token` requires a non-empty secret.

### Time

- `briefing_cadence.py:next_occurrence` and `latest_occurrence_at_or_before`
  require tz-aware input, `hour_local` 0-23, and a weekday if and only if the
  frequency is weekly. They return UTC.
- `datetime_utils.py:ensure_utc` stamps a naive datetime as UTC without
  shifting it.

### Databases

- `sqlite_ext.py:connect` sets `foreign_keys=ON`, `journal_mode=WAL`,
  `busy_timeout=5000` and `synchronous=NORMAL`. It commits on success, rolls
  back on an exception, and always closes. `load_vec` must be `none`, `soft`
  or `require`.
- `db_migration.py:MIRROR_SCHEMA` (`sqlite_mirror`) is never `public`.
  `discover_source_tables` excludes virtual FTS/vec0 tables and their shadow
  tables. The SQLite source is opened read-only (`mode=ro`).
- `db_promotion.py:promote` runs in one transaction and TRUNCATEs the typed
  tables first, so it is only for a fresh cutover database, never a live one.
  Embeddings whose dimension does not match are skipped with a warning
  (`db_promotion.py:_promote_vectors`).
- `sql_like.py:escape_like_wildcards` doubles the escape character before
  escaping `%` and `_`. The SQL must pair the pattern with
  `LIKE_ESCAPE_CLAUSE`.

### Text and parsing

- `http_text.py:decode_feed_body` trusts encodings in this order: BOM, strict
  UTF-8, the XML declaration, the header charset, and finally `cp1252` with
  replacement. It never raises.
- `text_sanitizer.py:sanitize_text` keeps tab, LF and CR and strips every other
  C0/C1 control character.
- `duration.py:get_audio_duration` runs `assert_audio_file` before calling
  `ffprobe`, and passes `--` before the path.
- Several classifiers are total and never raise: `page_kind.py:public_page_kind`,
  `episode_origin.py:derive_episode_origin`, `geoip.py:lookup_country_from_ip`
  and `url_patterns.py:extract_youtube_video_id`.

## Side Effects

- Environment:
  - `config.py:load_env_file` loads `.env` into `os.environ` and reads many
    env vars.
  - `ffmpeg_path.py:ensure_ffmpeg_on_path` mutates `PATH`.
  - `url_guard.py:_env_allowlist` reads `URL_GUARD_ALLOWLIST` on every call
    and caches the parsed list in a module global until the value changes.
- Filesystem:
  - `Config.__init__` calls `PathManager.ensure_directories_exist`, which
    creates the storage root and the stage, facts, debug-feed,
    pending-operation, narration, sync-track and corpus directories. It does
    not create `briefings_dir()` or `external_transcripts_dir()`
    (`path_manager.py:ensure_directories_exist`).
  - `logger.py:setup_logger` may create a log directory and file.
  - `podcast_categories.py` reads `podcast_categories.json` at import time.
- Network:
  - `url_guard.py` performs DNS lookups (`socket.getaddrinfo`) and HTTP GETs.
  - `geoip.py` calls `https://ipinfo.io/{ip}/country` with a 2 s timeout.
  - `file_storage/s3.py` makes S3 calls (see the subpackage contract).
- Subprocess: `duration.py:get_audio_duration` runs `ffprobe` with a 30 s
  timeout.
- Databases:
  - `sqlite_ext.py:connect` sets PRAGMAs and can load the `sqlite-vec`
    extension. `maybe_load_vec_extension` caches unavailability in the module
    global `_vec_available`.
  - `db_migration.py:migrate_table` creates the schema, then
    `DROP TABLE ... CASCADE`, `CREATE` and `COPY` inside `sqlite_mirror`.
  - `db_promotion.py:promote` calls `ensure_schema`, TRUNCATEs the typed
    tables, drops and rebuilds the HNSW indexes, and resyncs identity
    sequences.
- Process-global state:
  - `stdout_capture.py:StdoutProgressCapture` replaces `sys.stdout` while it is
    entered.
  - `cli_logging.py:log_command` binds and clears structlog contextvars.
  - `log_safety.py:install_uvicorn_access_redaction` adds a filter to the
    `uvicorn.access` logger. It is idempotent.
- Output:
  - `console.py:ConsoleOutput` writes to stdout and stderr.
  - `timing.py:log_phase_timing`, `cli_logging.py:log_command` and
    `file_storage/factory.py:make_storage` emit structlog events.
- Device probing: `device.py` imports `torch`, if it is installed, to check for
  CUDA or MPS.

## Verification

- Run: `./venv/bin/pytest tests/unit/utils`
