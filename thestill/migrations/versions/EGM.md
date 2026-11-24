# migrations/versions

## Overview

This directory holds the linear Alembic revision chain for the typed Postgres
schema (spec #44 Phase 5 onward). Each revision runs raw SQL through `op.execute`.
None of them uses SQLAlchemy models or autogenerate.
The baseline revision `0001` executes
`thestill.repositories.postgres_schema.SCHEMA_SQL`. That is the same DDL the
`ensure_schema` dev/test bootstrap runs. Every later revision except `0003`
says in its docstring that its DDL also lives in `SCHEMA_SQL`. As a result, a
database bootstrapped with `ensure_schema` and one upgraded through Alembic
converge on the same schema. Because `SCHEMA_SQL` is the current schema, not
the 2026-07 baseline, `0001` alone builds the whole schema on a fresh
database, and the DDL in `0002`-`0028` then finds its objects already
present.

## Interface

Each revision module exposes `revision`, `down_revision`, `upgrade()` and
`downgrade()`. The chain is strictly linear, 0001 → 0028, with no branches.

| Revision | File | `upgrade()` adds or changes |
| --- | --- | --- |
| `0001` | `0001_initial_schema.py` | Runs `SCHEMA_SQL.format(dim=...)`, with the pgvector width from `embedding_dim_for(EMBEDDING_MODEL or DEFAULT_EMBEDDING_MODEL)`. `SCHEMA_SQL` is the current schema (`thestill/repositories/postgres_schema.py:SCHEMA_SQL`), so on a fresh database `0001` creates every current table, including those that later revisions add (`mcp_tokens`, `oauth_*`, `app_settings`, `admin_audit_log`, `episode_platform_links`, `transcript_edits`, `usage_events`, `user_briefing_schedules`, `briefing_deliveries`, `entity_link_decisions`, `episode_sync_tracks` and more). It does not create the dropped `digests`, `digest_episodes`, `briefings` or `briefing_episodes`. `_TABLES` is only the `downgrade()` drop list, which still names those four and omits the later tables |
| `0002` | `0002_user_briefing_schedules.py` | Table `user_briefing_schedules` (PK `user_id`, `frequency` daily/weekly, `hour_local` 0-23, `weekday`, `timezone`, `enabled`, `next_run_at`) with CHECK `(frequency = 'weekly') = (weekday IS NOT NULL)`. Partial index `idx_briefing_schedules_due` |
| `0003` | `0003_drop_digest_tables.py` | Drops `digest_episodes`, `digests`, `briefing_episodes`, `briefings` (`CASCADE`) |
| `0004` | `0004_briefing_email_delivery.py` | `user_briefing_schedules.email_enabled`. Table `briefing_deliveries` (status `pending`/`sending`/`sent`/`failed`, `UNIQUE (briefing_id, channel)`). Partial index `idx_briefing_deliveries_due` |
| `0005` | `0005_top_podcasts_artwork.py` | `top_podcasts.image_url` |
| `0006` | `0006_refresh_failure_classification.py` | `podcasts`: `last_refresh_failure_kind`, `last_refresh_status_code`, `consecutive_refresh_failures`, `refresh_failure_streak_started_at`, `refresh_disabled_reason`, `refresh_retry_after_at`. Partial index `idx_podcasts_quarantine` |
| `0007` | `0007_performance_indices.py` | `CREATE EXTENSION pg_trgm`. Replaces `idx_episodes_pub_date` with `idx_episodes_pub_date_nulls_last`. Adds the inbox indices `idx_inbox_user_all` and `idx_inbox_user_source`; the FK and reverse-side indices (`idx_tasks_podcast_stage`, `idx_episode_related_target`, `idx_cooccur_entity_b`, `idx_overrides_episode`, `idx_overrides_entity`); the pipeline-state partials `idx_episodes_state_cleaned` and `idx_episodes_failed`; trigram GIN indices `idx_episodes_title_trgm` and `idx_entities_name_trgm`; jsonb GIN `idx_episodes_guest_entities`; and `LOWER()` indices `idx_entities_name_lower`, `idx_mentions_surface_lower`, `idx_overrides_surface_lower`, `idx_blacklist_surface_lower` |
| `0008` | `0008_summary_preview.py` | `episodes.summary_preview` (no backfill) |
| `0009` | `0009_mcp_tokens.py` | Table `mcp_tokens` (PK `user_id`, `token_hash`, `token_prefix`, `scopes` default `'read'`, expiry, last use and revocation columns). Unique index `idx_mcp_tokens_hash` |
| `0010` | `0010_podcast_chart_urls.py` | `podcasts.apple_url` and `podcasts.youtube_url`, backfilled from `top_podcasts` by `rss_url` with `COALESCE` |
| `0011` | `0011_entity_link_decisions.py` | Table `entity_link_decisions` (identity `id`, `surface_key`, nullable `podcast_id`, nullable `qid` meaning a decided "none", `confidence` high/medium/low, `linker_version`, `hits`). Partial unique indices `idx_link_decisions_podcast` and `idx_link_decisions_corpus` |
| `0012` | `0012_episode_platform_links.py` | Table `episode_platform_links` (platform apple/spotify/youtube, nullable `url`, `match_method` CHECK, `checked_at`, `UNIQUE(episode_id, platform)`). Column `podcasts.spotify_url` |
| `0013` | `0013_show_link_sources.py` | `podcasts.apple_url_source`, `youtube_url_source`, `spotify_url_source` (CHECK chart/publisher/resolver/curated). Backfill: `chart` when the value equals the chart row's, otherwise `publisher` |
| `0014` | `0014_link_decision_hinted.py` | `entity_link_decisions.hinted` (nullable boolean) |
| `0015` | `0015_public_visibility.py` | `podcasts.public_pages` (default true), `entities.public_hidden` (default false), and index `idx_mentions_episode_segment` |
| `0016` | `0016_user_episode_progress.py` | Table `user_episode_progress` (PK `(user_id, episode_id)`, `position_seconds >= 0`, `session_id`, `seq`). Index `idx_progress_user_updated` |
| `0017` | `0017_signup_source_briefing_opened.py` | `users.signup_source` and `user_briefings.opened_at`, with `opened_at` backfilled from `listened_at` |
| `0018` | `0018_usage_analytics_enabled.py` | `users.usage_analytics_enabled` (default true) |
| `0019` | `0019_episode_views_listened_at.py` | Table `user_episode_views` with index `idx_episode_views_at`, and column `user_episode_progress.listened_at`. Both backfills apply only to users with `usage_analytics_enabled` |
| `0020` | `0020_public_page_daily_views.py` | Table `public_page_daily_views` (PK `(day, kind, object_id)`, `views`) |
| `0021` | `0021_user_status_admin_tables.py` | `users`: `status` (active/suspended/banned), `status_reason`, `status_changed_at`, `status_changed_by`, `tokens_valid_after`. Tables `app_settings` (jsonb `value`), `admin_audit_log` (`actor` web/cli/system, indices `idx_admin_audit_created` and `idx_admin_audit_target`), and `suspended_follows` (index `idx_suspended_follows_podcast`) |
| `0022` | `0022_usage_events.py` | Table `usage_events` (`kind` CHECK feed_introduced/follow/import/translation/narration/quota_exceeded, `state` reserved/committed/released). Indices `idx_usage_events_user_kind`, `idx_usage_events_kind_created`, and partial `idx_usage_events_reserved` |
| `0023` | `0023_youtube_channel_discovery.py` | `podcasts.youtube_discovery_checked_at` |
| `0024` | `0024_podcast_summary_profile.py` | `podcasts.summary_profile` (nullable; NULL means resolve from Apple categories) |
| `0025` | `0025_mcp_oauth.py` | Tables `oauth_clients` (source cimd/dcr), `oauth_pending`, `oauth_grants` (partial unique index `idx_oauth_grants_active` on `(user_id, client_id)` where not revoked), `oauth_codes`, `oauth_tokens` (kind access/refresh), each with its own indices |
| `0026` | `0026_connector_days.py` | Table `user_connector_days` (PK `(user_id, day)`, `first_at`). Index `idx_connector_days_day` |
| `0027` | `0027_transcript_edits.py` | Table `transcript_edits` (append-only op log: `seq`, `op`, `raw_sha256`, jsonb `anchor_json`/`args_json`/`before_json`, self-FK `reverts`, `UNIQUE (episode_id, seq)`, index `idx_transcript_edits_actor`) and table `transcript_edit_refreshes` |
| `0028` | `0028_episode_sync_tracks.py` | Table `episode_sync_tracks` (PK `(episode_id, generation)`, `state` pending/current, `path`, `frames`, `anchors`, `audio_bytes`, `audio_duration_s`). Unique index `idx_sync_tracks_one_per_state` on `(episode_id, state)` |
| `0031` | `0031_quote_shares.py` | Tables `quote_shares` (PK `id`, a truncated hash; FKs to `episodes` and `podcasts` with `ON DELETE CASCADE`; `speaker_entity_id` deliberately without an FK; `revoked_at`) and `quote_share_hits` (PK `share_id, day, referrer_host`), spec #114 |

## Invariants

- The DDL is idempotent. Revisions use `CREATE TABLE/INDEX IF NOT EXISTS`,
  `ADD COLUMN IF NOT EXISTS` and `DROP ... IF EXISTS`, so re-running against a
  database that `ensure_schema` already built is a no-op (for example
  `0002_user_briefing_schedules.py:_DDL` and `0007_performance_indices.py:_DDL`).
- New schema must also be added to
  `thestill/repositories/postgres_schema.py:SCHEMA_SQL` to keep the two
  bootstrap paths converged, as each revision docstring states. The exception
  is `0003_drop_digest_tables.py`, which only drops tables.
- `0003_drop_digest_tables.py:downgrade` cannot be reversed: it is `pass` by
  design.
- `0001_initial_schema.py:upgrade` sizes the vector columns from
  `EMBEDDING_MODEL` at upgrade time. Changing that env var later does not
  resize existing columns.
- Most backfills only fill gaps. `0013_show_link_sources.py:_BACKFILL` only
  sets sources that are NULL, `0017_signup_source_briefing_opened.py:_BACKFILL`
  filters `WHERE opened_at IS NULL`, and
  `0019_episode_views_listened_at.py:_BACKFILL` uses `ON CONFLICT DO NOTHING`
  and respects `usage_analytics_enabled`.
- Known issue: `0010_podcast_chart_urls.py:_BACKFILL` overwrites. It sets
  `apple_url = COALESCE(t.apple_url, p.apple_url)` (and the same for
  `youtube_url`), so the chart value wins and replaces an existing podcast
  link whenever the chart row has one (`0010_podcast_chart_urls.py:50-55`).
  The COALESCE only keeps the stored link when the chart has none.
- Schema-level CHECK constraints encode domain enums, for example
  `0021_user_status_admin_tables.py:_DDL` (`users.status`) and
  `0022_usage_events.py:_DDL` (`usage_events.kind`, `usage_events.state`).
  Adding a new enum value needs a new revision.

## Side Effects

- Postgres DDL and DML only, run through `alembic.op.execute` inside the
  transaction that `../env.py` opens.
- `0007_performance_indices.py:upgrade` installs the `pg_trgm` extension.
- Data backfills (UPDATE or INSERT) run in `0010`, `0013`, `0017` and `0019`.
- Every `downgrade()` except `0003` drops the tables, columns or indices that
  its `upgrade()` added. `0001_initial_schema.py:downgrade` drops the tables
  in `_TABLES` with `CASCADE`.

## Verification

- Run: `make test` (Alembic-path coverage lives in
  `tests/integration/test_migrate_on_startup.py`, which needs Postgres).
