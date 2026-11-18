# repositories

## Overview

The persistence layer: abstract repository interfaces (ABCs plus shared
dataclasses and SQL fragments), with a SQLite and a Postgres implementation of
each. `factory.py` picks the backend once per process and returns a
`RepositoryBundle`. Services, the CLI, the web server and MCP talk to the
interfaces, never to a backend class.

- **Backend switch**: Postgres when `config.database_url` is set, else SQLite
  at `config.database_path` (`factory.py:uses_postgres`, `factory.py:make_repositories`).
- **Schema ownership differs per backend**:
  - SQLite: `SqlitePodcastRepository.__init__` creates and migrates every table
    except `tasks` (`sqlite_podcast_repository.py:_ensure_database_exists` runs
    `_create_schema` then `_run_migrations`). All other `Sqlite*Repository`
    classes run no DDL and only store `db_path`. The SQLite `tasks` table and
    its indexes are created outside this package, by
    `core/queue_manager.py:QueueManager._ensure_table` (run from
    `QueueManager.__init__`, which `factory.py:make_repositories` builds).
  - Postgres: `postgres_schema.py:ensure_schema` (idempotent DDL, run once per
    DSN per process by `factory.py:_ensure_pg_schema`), plus
    `postgres_schema.py:run_alembic_upgrade` (alembic `upgrade head` against
    `thestill/migrations`, called from `thestill/web/app.py` when
    migrate-on-startup is enabled). `Postgres*Repository` classes run no DDL,
    except when built with `ensure_schema=True`
    (`postgres_user_repository.py:PostgresUserRepository`,
    `postgres_entity_repository.py:PostgresEntityRepository`).
- **Connections**: both backends open one connection per operation.
  - SQLite uses `utils.sqlite_ext.connect` (foreign_keys ON, WAL,
    busy_timeout, synchronous NORMAL). Its default is `load_vec="none"`;
    only `SqlitePodcastRepository._get_connection`
    (`sqlite_podcast_repository.py:2906`) and `SqliteEntityRepository`
    (`sqlite_entity_repository.py:138`) pass `load_vec="soft"`, so cascades
    that fire the `chunks_vec` triggers still work.
  - Postgres uses `utils.postgres_ext.connect` (dict rows; commit on clean
    exit, rollback on exception).
  - Neither backend pools connections.

## Interface

### Backend selection and bundle (`factory.py`)

- `factory.py:uses_postgres(config)`: the only backend switch.
- `factory.py:make_repositories(config) -> RepositoryBundle`: builds every
  repository plus the queue manager (`PostgresQueueManager` or
  `core.queue_manager.QueueManager`).
- `factory.py:RepositoryBundle`: dataclass with these fields.
  - `backend`, `podcast`, `user`, `follower`, `inbox`, `briefing`
  - `briefing_schedule`, `briefing_delivery`, `mcp_token`, `oauth`
  - `listening_progress`, `link_decision`, `legacy_claim`, `pending_ops`
  - `entity`, `queue_manager`, `analytics`, `episode_view`
  - `public_page_view`, `connector_day`, `sync_track`, `settings`
  - `audit_log`, `suspended_follow`, `user_directory`, `usage`
  - `transcript_edit`
- `factory.py:make_user_repository`, `make_search_backend` (PgVectorBackend /
  SqliteVecBackend), `make_chunk_writer`, `make_queue_manager`: single-purpose
  factories.
- `factory.py:ping(config)`: readiness probe. Postgres runs `SELECT 1`;
  SQLite opens with `mode=rw` and checks that the `podcasts` table exists.
- `__init__.py` re-exports `EpisodeRepository`, `PodcastRepository`,
  `PodcastFollowerRepository`, `SqlitePodcastRepository`,
  `SqlitePodcastFollowerRepository`.

### Interfaces and their implementations

Every row lists the ABC, its SQLite and Postgres classes, and the tables the
concrete classes read or write (taken from their SQL strings).

| Interface (ABC) | SQLite class | Postgres class | Tables touched |
| --- | --- | --- | --- |
| `podcast_repository.py:PodcastRepository` + `EpisodeRepository` | `sqlite_podcast_repository.py:SqlitePodcastRepository` | `postgres_podcast_repository.py:PostgresPodcastRepository` (`PodcastsMixin` in `postgres_podcast_repository_podcasts.py` + `EpisodesMixin` in `postgres_podcast_repository_episodes.py`) | `podcasts`, `episodes`, `categories`, `podcast_followers`, `tasks` (read), `episode_transcript_links`, `episode_alternate_enclosures`, `episode_platform_links`, `top_podcasts`, `top_podcast_rankings`, `top_podcasts_meta`, `chunks` (read); `fold_import_twin` also writes `user_episode_inbox`, `user_episode_progress`, `user_episode_views` |
| `entity_repository.py:EntityRepository` | `sqlite_entity_repository.py:SqliteEntityRepository` | `postgres_entity_repository.py:PostgresEntityRepository` | `entities`, `entity_mentions`, `entity_cooccurrences`, `entity_enrichment`, `mention_overrides`, `resolution_blacklist`, `podcasts` (`host_entity_ids`, `recurring_entity_ids`), `episodes` (`guest_entity_ids`) |
| `user_repository.py:UserRepository` | `sqlite_user_repository.py:SqliteUserRepository` | `postgres_user_repository.py:PostgresUserRepository` | `users`, `revoked_tokens`, `user_episode_views`, `user_connector_days`, `user_episode_progress` |
| `user_directory_repository.py:UserDirectoryRepository` | `sqlite_user_directory_repository.py:SqliteUserDirectoryRepository` | `postgres_user_directory_repository.py:PostgresUserDirectoryRepository` | `users`, `podcast_followers`, `suspended_follows`, `usage_events` |
| `podcast_follower_repository.py:PodcastFollowerRepository` | `sqlite_podcast_follower_repository.py:SqlitePodcastFollowerRepository` | `postgres_podcast_follower_repository.py:PostgresPodcastFollowerRepository` | `podcast_followers`, `suspended_follows`, `usage_events` |
| `suspended_follow_repository.py:SuspendedFollowRepository` | `sqlite_suspended_follow_repository.py:SqliteSuspendedFollowRepository` | `postgres_suspended_follow_repository.py:PostgresSuspendedFollowRepository` | `suspended_follows`, `podcast_followers` |
| `usage_event_repository.py:UsageEventRepository` | `sqlite_usage_event_repository.py:SqliteUsageEventRepository` | `postgres_usage_event_repository.py:PostgresUsageEventRepository` | `usage_events` |
| `inbox_repository.py:InboxRepository` | `sqlite_inbox_repository.py:SqliteInboxRepository` | `postgres_inbox_repository.py:PostgresInboxRepository` | `user_episode_inbox`, `episodes`, `podcasts`, `user_episode_progress`, `podcast_followers`, `tasks` |
| `briefing_repository.py:BriefingRepository` | `sqlite_briefing_repository.py:SqliteBriefingRepository` | `postgres_briefing_repository.py:PostgresBriefingRepository` | `user_briefings`, `tasks`, `episodes`, `podcast_followers`, `user_episode_inbox` |
| `briefing_schedule_repository.py:BriefingScheduleRepository` | `sqlite_briefing_schedule_repository.py:SqliteBriefingScheduleRepository` | `postgres_briefing_schedule_repository.py:PostgresBriefingScheduleRepository` | `user_briefing_schedules` |
| `briefing_delivery_repository.py:BriefingDeliveryRepository` | `sqlite_briefing_delivery_repository.py:SqliteBriefingDeliveryRepository` | `postgres_briefing_delivery_repository.py:PostgresBriefingDeliveryRepository` | `briefing_deliveries` |
| `listening_progress_repository.py:ListeningProgressRepository` | `sqlite_listening_progress_repository.py:SqliteListeningProgressRepository` | `postgres_listening_progress_repository.py:PostgresListeningProgressRepository` | `user_episode_progress`, `users` |
| `episode_view_repository.py:EpisodeViewRepository` | `sqlite_episode_view_repository.py:SqliteEpisodeViewRepository` | `postgres_episode_view_repository.py:PostgresEpisodeViewRepository` | `user_episode_views`, `episodes`, `users` |
| `connector_day_repository.py:ConnectorDayRepository` | `sqlite_connector_day_repository.py:SqliteConnectorDayRepository` | `postgres_connector_day_repository.py:PostgresConnectorDayRepository` | `user_connector_days`, `users`; Postgres also reads `user_briefing_schedules` |
| `public_page_view_repository.py:PublicPageViewRepository` | `sqlite_public_page_view_repository.py:SqlitePublicPageViewRepository` | `postgres_public_page_view_repository.py:PostgresPublicPageViewRepository` | `public_page_daily_views` |
| `mcp_token_repository.py:McpTokenRepository` | `sqlite_mcp_token_repository.py:SqliteMcpTokenRepository` | `postgres_mcp_token_repository.py:PostgresMcpTokenRepository` | `mcp_tokens` |
| `oauth_repository.py:OAuthRepository` | `sqlite_oauth_repository.py:SqliteOAuthRepository` | `postgres_oauth_repository.py:PostgresOAuthRepository` | `oauth_clients`, `oauth_pending`, `oauth_grants`, `oauth_codes`, `oauth_tokens` |
| `link_decision_repository.py:LinkDecisionRepository` | `sqlite_link_decision_repository.py:SqliteLinkDecisionRepository` | `postgres_link_decision_repository.py:PostgresLinkDecisionRepository` | `entity_link_decisions` |
| `legacy_claim_repository.py:LegacyClaimRepository` | `sqlite_legacy_claim_repository.py:SqliteLegacyClaimRepository` | `postgres_legacy_claim_repository.py:PostgresLegacyClaimRepository` | `users`, `podcast_followers`, `user_episode_inbox`, `user_briefings`, `user_briefing_schedules`, `user_episode_progress` |
| `pending_operations_repository.py:PendingOperationsRepository` | `sqlite_pending_operations_repository.py:SqlitePendingOperationsRepository` | `postgres_pending_operations_repository.py:PostgresPendingOperationsRepository` | `pending_transcription_operations` |
| `sync_track_repository.py:SyncTrackRepository` | `sqlite_sync_track_repository.py:SqliteSyncTrackRepository` | `postgres_sync_track_repository.py:PostgresSyncTrackRepository` | `episode_sync_tracks` |
| `transcript_edit_repository.py:TranscriptEditRepository` | `sqlite_transcript_edit_repository.py:SqliteTranscriptEditRepository` | `postgres_transcript_edit_repository.py:PostgresTranscriptEditRepository` | `transcript_edits`, `transcript_edit_refreshes` |
| `settings_repository.py:SettingsRepository` | `sqlite_settings_repository.py:SqliteSettingsRepository` | `postgres_settings_repository.py:PostgresSettingsRepository` | `app_settings` |
| `audit_log_repository.py:AuditLogRepository` | `sqlite_audit_log_repository.py:SqliteAuditLogRepository` | `postgres_audit_log_repository.py:PostgresAuditLogRepository` | `admin_audit_log` |
| `analytics_repository.py:AnalyticsRepository` | `analytics_repository.py:UnavailableAnalyticsRepository` (stub; every method raises `AnalyticsUnavailableError`) | `postgres_analytics_repository.py:PostgresAnalyticsRepository` (read-only) | `users`, `user_episode_inbox`, `user_episode_views`, `user_episode_progress`, `user_briefing_schedules`, `user_briefings`, `user_connector_days`, `podcast_followers`, `public_page_daily_views`, `episodes`, `podcasts`, `categories`, `entities` |

### Key public methods by area

- **Podcasts**:
  - CRUD: `get`, `get_by_id`, `get_by_url`, `get_by_slug`, `get_by_index`,
    `get_all`, `exists`, `save` (destructive), `save_podcast`
    (metadata-only), `delete`, `touch_last_processed_at`.
  - Synthetic and auto-added parents: `ensure_synthetic_audio_imports_parent`,
    `upsert_auto_added_podcast`.
  - Admin listing: `count_podcasts`, `count_episode_states`,
    `list_podcast_rows`, `get_podcast_row_by_slug`, `get_recent_activity_rows`.
  - Defined on `SqlitePodcastRepository` and `PodcastsMixin`.
- **Refresh scheduling (spec #48/#60)**: `get_podcasts_for_refresh`,
  `get_due_podcasts`, `get_quarantine_probe_due`, `seed_unscheduled_feeds`,
  `reschedule_unscheduled_feed`, `record_refresh_success`,
  `record_refresh_failure`, `clear_podcast_refresh_failure(s)`,
  `get_refresh_health_counts`, `get_refresh_on_open_state`, `has_followers`,
  `follow_started_at`.
- **Episodes**:
  - Saving: `save_episode`, `save_episodes`, `save_refresh_batch`,
    `update_episode`.
  - State changes: `mark_episode_published`, `mark_episode_failed`,
    `clear_episode_failure(s)`, `clear_episode_failure_for_stages`,
    `update_entity_extraction_status`, `settle_linking_deferred`.
  - Reads: `get_episode`, `get_episodes_by_ids`, `get_episode_by_slug`,
    `get_episode_by_external_id`, `get_unprocessed_episodes`,
    `get_all_episodes`, `get_failed_episodes`.
  - Orphan finders: `get_discovered_unqueued_candidates`,
    `get_unqueued_unprocessed_episodes`,
    `get_recent_unqueued_unprocessed_episodes`.
- **Imports and twins**: `insert_imported_episode`, `set_episode_canonical_id`,
  `find_episode_id_by_canonical_id` / `_by_audio_url` / `_by_external_id`,
  `find_import_twin_candidates`, `fold_import_twin`.
- **Links and public pages**:
  - Transcript links: `add_transcript_links`, `mark_transcript_downloaded`.
  - Enclosures: `get_alternate_enclosures(_for_episodes)`.
  - Platform links (spec #87): `get_platform_links`,
    `get_platform_link_candidates`, `upsert_platform_links`,
    `set_podcast_platform_url`, `sync_podcast_chart_urls`,
    `forget_discovered_youtube_channels`.
  - Public pages: `set_podcast_public_pages`, `is_podcast_public`,
    `list_public_episode_rows`, `set_episode_summary_preview`.
  - Charts: `get_top_podcasts`.
- **Entities**:
  - Entity rows: `upsert_entity`, `set_entity_wikidata`, `replace_aliases`,
    `delete_entity`.
  - Merge and prune: `repoint_entity_references`, `move_mentions`,
    `find_orphan_entity_ids`, `delete_orphan_entities`.
  - Mentions: `insert_mentions`, `resolve_mention`,
    `reset_mentions_to_pending`.
  - Queries: `find_mentions`, `search_entities_by_prefix`,
    `get_entity_summary`.
  - Enrichment: `upsert_enrichment`, `entity_ids_needing_enrichment`.
  - Anchors (hosts, recurring, guests): `set_podcast_hosts`,
    `set_podcast_recurring`, `set_episode_guests`, `get_episode_guests`,
    `get_podcast_anchors`, `get_episode_anchors`, `detect_top_speakers`.
    `get_episode_guests` (used by `services/role_linker.py`) returns
    `episodes.guest_entity_ids` as stored, or `[]` when the episode is missing
    or has none [`entity_repository.py:EntityRepository.get_episode_guests`].
  - Co-occurrence and review: `rebuild_cooccurrences`, `add_override`,
    `add_blacklist_entry`, `find_duplicate_qid_pairs`,
    `find_mistyped_entities`.
  - Public pages: `public_quote_queries`.
- **Shared module symbols**:
  - `entity_repository.py`: `EntityHit`, `MentionContext`, `EntityEpisode`,
    `AnchorScanOverlap`, `AliasEvidence`, `ROLE_BY_SCORE`,
    `RECENT_MENTIONS_BY_EPISODE_SQL`, `ANCHOR_SCAN_OVERLAPS_SQL`.
  - `inbox_repository.py`: `ARRIVING_WHERE_SQL`, `ARRIVING_TASK_COLUMNS_SQL`.
  - `oauth_repository.py`: `GRANT_COLS`, `CODE_COLS`, `TOKEN_COLS`,
    `PENDING_COLS`, `split_scopes`.
  - `usage_event_repository.py`: `USAGE_KINDS`, `COUNTED_SQL`,
    `PRUNE_KEEP_KINDS`, `FOLLOW_LOCK_GROUP`, `usage_lock_key`,
    `UsageRecord`, `ReserveResult`.
  - `podcast_follower_repository.py`: `FollowFacts`, `FollowDecision`,
    `FollowOutcome`, `ALLOW_FOLLOW`, `allow_all`.
  - `sync_track_repository.py`: `STATE_PENDING`, `STATE_CURRENT`,
    `SyncTrackRecord`.
  - `user_repository.py`: `LastActiveAdminError`.
  - `user_directory_repository.py`: `summary_columns`, `listing_where`,
    `search_pattern`, `UserSummary`.
  - `settings_repository.py`: `StoredSetting`.
  - `podcast_repository.py`: `RefreshOnOpenState`.
- **Shared SQL builders**:
  - `public_quotes.py:PublicQuoteQueries`, `QuoteFilter` and `*_sql`
    builders (quotes, speakers, topics, trending, entity index, presence).
    Both backends use them, each with its own placeholder.
  - `postgres_category_cache.py:CategoryCacheMixin`: per-instance category
    map cache for the Postgres podcast mixins.
- **Cross-backend helpers**:
  - `sqlite_podcast_repository.py`: `episode_from_row`,
    `PLATFORM_URL_SOURCES`, `SYNTHETIC_AUDIO_IMPORTS_RSS`,
    `SYNTHETIC_AUDIO_IMPORTS_ID` (uuid5, deterministic).
  - `postgres_user_repository.py`: `consents_to_measurement`, `row_to_user`,
    `SELECT_COLS`.
  - `postgres_usage_event_repository.py`: `lock_usage`, `count_usage`,
    `insert_usage`. `sqlite_usage_event_repository.py` has only
    `count_usage` and `insert_usage`; there is no SQLite `lock_usage`, and
    `SqliteUsageEventRepository.reserve` serialises with `BEGIN IMMEDIATE`
    instead (`sqlite_usage_event_repository.py:73`).
- **Postgres schema**:
  - `postgres_schema.py:ensure_schema(dsn, *, embedding_dim=384)` and
    `SCHEMA_SQL`.
  - `run_alembic_upgrade(dsn)` and `SCHEMA_BOOTSTRAP_LOCK_KEY = 0x7E5711`.

No subdirectories.

## Invariants

### Backend and schema

- Postgres is used iff `config.database_url` is non-empty. Backend imports are
  lazy, so a SQLite-only install never imports psycopg [`factory.py:uses_postgres`].
- Postgres DDL bootstrap runs at most once per DSN per process, guarded by a
  `threading.Lock` and the `_schema_ready` set. Every Postgres factory calls it
  first [`factory.py:_ensure_pg_schema`].
- `ensure_schema` runs all DDL in one transaction under
  `pg_advisory_xact_lock(0x7E5711)`. `run_alembic_upgrade` holds a session
  `pg_advisory_lock` on the same key [`postgres_schema.py:ensure_schema`,
  `postgres_schema.py:run_alembic_upgrade`].
- The SQLite constructor runs its setup in this order [`sqlite_podcast_repository.py:_ensure_database_exists`]:
  1. mkdir the parent directory.
  2. Set PRAGMAs.
  3. `_create_schema`.
  4. `_run_migrations`.
  5. `_load_categories_cache`.
  6. `_seed_top_podcasts`.
- SQLite migrations do not use `PRAGMA user_version`. Each step is gated by a
  `PRAGMA table_info` column check, a `sqlite_master` lookup, or a substring
  test on the stored CREATE SQL. Steps are not wrapped in one transaction, so
  a crash can leave a partial state [`sqlite_podcast_repository.py:_run_migrations`].
- SQLite CHECK changes are done by rebuilding the table (`entity_mentions`,
  `user_episode_inbox`, `pending_transcription_operations`).
- When sqlite-vec is not installed, the whole spec #28 chunks step is skipped
  with a warning: the plain `chunks` table and `chunks_fts` are not created
  either, not only `chunks_vec`. The `episode_vec` step is skipped the same
  way. Both steps are gated on the vec0 table's absence, so they retry on the
  next startup (`sqlite_podcast_repository.py:1167-1178`, `:1497-1505`).

### Cross-backend contracts (both implementations honor them)

- **Timestamps**:
  - SQLite writes tz-aware ISO-8601 strings (`+00:00`) and compares them as
    strings [`sqlite_mcp_token_repository.py:SqliteMcpTokenRepository`,
    `sqlite_transcript_edit_repository.py:SqliteTranscriptEditRepository`].
  - Postgres passes tz-aware datetimes into `timestamptz` columns [`postgres_podcast_repository_podcasts.py` module docstring].
  - Some older SQLite tables still default to `CURRENT_TIMESTAMP`
    [`sqlite_podcast_repository.py:_run_migrations`].
- **Idempotent episode save**:
  - `_save_episode_idempotent` is keyed on `(podcast_id, external_id)` and
    bumps `updated_at` only when a compared column changed.
  - Every column the UPDATE writes must also be SELECTed and compared, or the
    update is silently dropped.
  - `canonical_id` is written on INSERT only.
  - Sources: `sqlite_podcast_repository.py:_save_episode_idempotent`,
    `postgres_podcast_repository_episodes.py:_save_episode_idempotent`.
- **Save methods**:
  - `save()` is destructive: it hard-deletes all of the podcast's episodes
    and re-inserts them.
  - `save_podcast` never touches episodes.
  - `save_episode(s)` and `save_refresh_batch` raise `ValueError` when
    `podcast_id` is unset.
  - Sources: `sqlite_podcast_repository.py:save`,
    `postgres_podcast_repository_podcasts.py:save`,
    `podcast_repository.py:PodcastRepository.save_episode`.
- **`save_refresh_batch`** [`sqlite_podcast_repository.py:save_refresh_batch`,
  `postgres_podcast_repository_episodes.py:save_refresh_batch`]:
  - Runs as one transaction.
  - New episodes are inserted with ignore-on-conflict.
  - Audio URL re-sync only touches episodes with no `audio_path` and no
    `raw_transcript_path`.
  - Alternate enclosures are deduplicated on `(episode_id, source_uri)`.
- **`touch_last_processed_at`** writes only `last_processed_at` and never
  moves the `last_processed` watermark [`podcast_repository.py:PodcastRepository.touch_last_processed_at`].
- **Refresh eligibility** is defined in one place, `_active_feed_sql`: the
  feed is not synthetic, (optionally) not complete, and has at least one
  follower [`sqlite_podcast_repository.py:_active_feed_sql`,
  `postgres_podcast_repository_podcasts.py:_active_feed_sql`].
- **Parked feeds**:
  - `next_refresh_at IS NULL` means parked or quarantined.
  - Only reasons `feed_gone` and `invalid_content` are re-probed.
  - `seed_unscheduled_feeds` never revives a parked feed.
  - Sources: `get_quarantine_probe_due`, `seed_unscheduled_feeds` in both
    podcast backends.
- **AIMD refresh**:
  - Success halves the interval when new episodes were found, else multiplies
    it by 1.5, clamped to `[min, max]`, and clears all failure state.
  - Failure has three outcomes: IGNORE only stamps visibility fields,
    QUARANTINE parks the feed, BACKOFF never parks.
  - The error text is truncated to 2000 characters.
  - Sources: `record_refresh_success`, `record_refresh_failure` in both
    podcast backends.
- **`mark_episode_published`** is one-way (`WHERE published_at IS NULL`) and
  returns True only on the first transition [`podcast_repository.py:EpisodeRepository.mark_episode_published`].
- **`clear_episode_failure_for_stages`** only clears when `failed_at_stage` is
  in the given stages. `update_entity_extraction_status` never touches
  `failed_at_stage` [`postgres_podcast_repository_episodes.py:clear_episode_failure_for_stages`].
- **`set_podcast_platform_url`** raises `KeyError` for an unknown platform and
  `ValueError` for an unknown source. A `curated` value yields only to another
  `curated` write [`postgres_podcast_repository_podcasts.py:set_podcast_platform_url`,
  `sqlite_podcast_repository.py:set_podcast_platform_url`].
- **`upsert_platform_links`** never blanks a stored URL (COALESCE) and always
  bumps `checked_at` [`podcast_repository.py:PodcastRepository.upsert_platform_links`].
- **`fold_import_twin`** runs in one transaction (SQLite `BEGIN IMMEDIATE`,
  Postgres `FOR UPDATE`). It re-checks that both rows still qualify and
  returns False with no writes otherwise [`sqlite_podcast_repository.py:fold_import_twin`,
  `postgres_podcast_repository_episodes.py:fold_import_twin`].
- **`set_episode_summary_preview`** does not bump `updated_at`. A public
  episode needs a summary, a non-empty preview, both slugs, `public_pages` on
  its podcast, and must not be synthetic [`podcast_repository.py:EpisodeRepository.list_public_episode_rows`].
- **Entities**:
  - Ownership: entity repositories own no DDL [`entity_repository.py:EntityRepository`].
  - `upsert_entity` is a single `INSERT ... ON CONFLICT(id) DO UPDATE`:
    - `type` and `canonical_name` are replaced.
    - Aliases become the sorted distinct union of old and new.
    - QID and description use `COALESCE(excluded.x, entities.x)`: a non-NULL
      incoming value overwrites the stored one, and only a NULL incoming
      value keeps it (`sqlite_entity_repository.py:175, 185`;
      `postgres_entity_repository.py:176, 185`).
    - `wikidata_instance_of` is replaced only when the new list is non-empty.
  - Known issue: the `upsert_entity` docstring says QID and description
    "only fill in when previously NULL", which the SQL does not do
    (`entity_repository.py:228-230`).
  - Removing data needs a dedicated method: `set_entity_wikidata` overwrites
    QID data, and `replace_aliases` is the only way to remove an alias
    [`entity_repository.py:EntityRepository.upsert_entity`].
  - `delete_entity` is a hard delete that cascades to mentions and
    co-occurrences.
  - `delete_orphan_entities` re-checks the orphan predicate inside its
    DELETE, in batches: SQLite takes 500 ids per batch, Postgres 1000 per
    batch with `FOR UPDATE` [`sqlite_entity_repository.py:delete_orphan_entities`,
    `postgres_entity_repository.py:delete_orphan_entities`].
  - Input checks that raise `ValueError`:
    - `resolve_mention` accepts only resolved, unresolvable, ambiguous or
      dropped.
    - `add_override` accepts only drop, force_entity or force_unresolvable,
      and force_entity needs an `entity_id`.
    - Source: `entity_repository.py:EntityRepository`.
  - `rebuild_cooccurrences` stores each pair with `entity_a_id < entity_b_id`
    and counts only resolved mentions. With `episode_ids=None` it wipes and
    rebuilds the whole table [`sqlite_entity_repository.py:rebuild_cooccurrences`].
  - `upsert_enrichment` keeps the earlier content from any source that failed
    this run, and never changes `created_at` [`entity_repository.py:EntityRepository.upsert_enrichment`].
- **Inbox**:
  - Rows are unique on `(user_id, episode_id)`; `insert_many` ignores
    conflicts.
  - `mark_read_if_unread` only moves `unread` to `read`.
  - `list_items(state=None)` excludes `dismissed`.
  - Source: `inbox_repository.py:InboxRepository`.
- **Briefings**:
  - Delivery is send-once: `ensure_pending` relies on
    `UNIQUE(briefing_id, channel)`.
  - `claim` is a leased compare-and-set that spends a retry attempt at claim
    time [`briefing_delivery_repository.py:BriefingDeliveryRepository.claim`].
  - The schedule `claim` succeeds only when `next_run_at` equals the expected
    value [`briefing_schedule_repository.py:BriefingScheduleRepository.claim`].
- **Measurement consent**:
  - `record_view`, `record_day` and the `listened_at` update in
    `upsert_if_newer` write only when `usage_analytics_enabled` is on.
  - Postgres checks consent under `SELECT ... FOR SHARE`
    [`postgres_user_repository.py:consents_to_measurement`].
  - Opting out erases `user_episode_views` and `user_connector_days` and
    nulls `listened_at` in the same transaction [`user_repository.py:UserRepository.update_usage_analytics`].
- **Follows and quotas**:
  - `follow_with_gate` is one transaction and locks the user before the
    podcast (Postgres advisory locks; SQLite `BEGIN IMMEDIATE`).
  - Refusal records are committed before the refusal is raised
    [`podcast_follower_repository.py:PodcastFollowerRepository.follow_with_gate`].
  - `usage_events.reserve` counts reserved plus committed rows. A refusal
    writes a `quota_exceeded` row. `prune` keeps `feed_introduced` rows
    [`usage_event_repository.py:UsageEventRepository`].
- **Tokens**:
  - Each user has one `mcp_tokens` row; revoke is soft (sets `revoked_at`).
  - OAuth `approve` consumes the pending row via DELETE ... RETURNING.
  - `redeem_code` and `rotate_refresh` are single-use compare-and-sets that
    write nothing when they fail.
  - `prune` keeps used codes and retired refresh tokens long enough to
    detect replay.
  - Sources: `mcp_token_repository.py:McpTokenRepository`,
    `oauth_repository.py:OAuthRepository`.
- **Transcript edits** are append-only. `append` writes
  `seq = expected_last_seq + 1` and returns None for a stale writer
  [`transcript_edit_repository.py:TranscriptEditRepository.append`].
- **Sync tracks**: each episode has at most one `pending` and one `current`
  row. `promote` retires the old current row in the same transaction
  [`sync_track_repository.py:SyncTrackRepository`].
- **Users**:
  - `save` upserts by email and never overwrites `region`, `is_admin`,
    `signup_source` or the status columns.
  - `apply_status` and `set_admin` raise `LastActiveAdminError` for a change
    that would leave no active admin only when the caller passes
    `keep_an_active_admin=True`; the default is `False`, which applies the
    change unguarded. `save` has no such guard
    [`user_repository.py:165, 175-178`,
    `sqlite_user_repository.py:231-233, 252-254`,
    `postgres_user_repository.py:221-222, 241-242`].
  - The guarded check is serialised: SQLite runs `BEGIN IMMEDIATE` before
    `_keep_an_active_admin`; Postgres takes
    `pg_advisory_xact_lock(hashtextextended('thestill:active-admins', 0))`
    [`postgres_user_repository.py:_keep_an_active_admin`].
- **Audit log** is append-only, with no update or delete methods
  [`audit_log_repository.py:AuditLogRepository`].
- **Analytics** is Postgres-only. Callers must check `available` first
  [`analytics_repository.py:UnavailableAnalyticsRepository`].

### Known backend divergences and doc drift (observed in code)

- `record_day` buckets by UTC day on SQLite but by the reader's schedule time
  zone on Postgres (`AT TIME ZONE COALESCE(s.timezone, 'UTC')`)
  [`sqlite_connector_day_repository.py:record_day`,
  `postgres_connector_day_repository.py:record_day`].
- The `search_entities_by_prefix` docstring ranks guest > host > recurring.
  `ROLE_BY_SCORE` and the SQL rank host (3) > guest (2) > recurring (1)
  [`entity_repository.py:ROLE_BY_SCORE`].
- `SqliteEntityRepository`'s docstring still says its methods raise
  `NotImplementedError`, but all of them are implemented
  [`sqlite_entity_repository.py:SqliteEntityRepository`].
- The jitter in `seed_unscheduled_feeds` uses Python `hash(pid)`, which is
  salted per process, so offsets change between runs
  [`sqlite_podcast_repository.py:seed_unscheduled_feeds`,
  `postgres_podcast_repository_podcasts.py:seed_unscheduled_feeds`].
- `save_podcast` returns its input object unchanged on both backends
  (`sqlite_podcast_repository.py:3534`,
  `postgres_podcast_repository_podcasts.py:1151`). Known issue: the ABC
  docstring promises "The saved podcast (with updated timestamps if changed)"
  (`podcast_repository.py:212-213`); neither backend does that.

## Side Effects

- **DDL at construction**:
  - SQLite: `SqlitePodcastRepository.__init__` creates the DB directory, sets
    the WAL journal (creates `-wal`/`-shm` files), and runs
    `_create_schema` and `_run_migrations`. These drop the retired
    `digests`/`digest_episodes` tables, rebuild tables, and backfill data
    [`sqlite_podcast_repository.py:_ensure_database_exists`].
  - Postgres: the first factory call in a process runs `CREATE EXTENSION
    vector/pg_trgm` plus all DDL [`factory.py:_ensure_pg_schema`].
- **Tables created**:
  - Postgres `SCHEMA_SQL`: `users`, `revoked_tokens`, `mcp_tokens`,
    `oauth_*`, `app_settings`, `admin_audit_log`, `categories`, `podcasts`,
    `episodes`, `episode_alternate_enclosures`, `episode_platform_links`,
    `episode_transcript_links`, `transcript_edits`,
    `transcript_edit_refreshes`, `podcast_followers`, `suspended_follows`,
    `usage_events`, `user_episode_inbox`, `user_episode_progress`,
    `user_episode_views`, `user_connector_days`, `episode_sync_tracks`,
    `public_page_daily_views`, `user_briefings`, `user_briefing_schedules`,
    `briefing_deliveries`, `tasks`, `pending_transcription_operations`,
    `entities`, `entity_mentions`, `entity_cooccurrences`,
    `entity_enrichment`, `mention_overrides`, `resolution_blacklist`,
    `entity_link_decisions`, `chunks`, `episode_vectors`, `episode_related`,
    `related_idf`, `top_podcasts`, `top_podcast_rankings`,
    `top_podcasts_meta` [`postgres_schema.py:SCHEMA_SQL`].
  - SQLite creates the same set except `tasks` (created by
    `core/queue_manager.py:QueueManager._ensure_table`), plus the virtual
    tables `chunks_vec`, `chunks_fts` and `episode_vec`. Without sqlite-vec,
    `chunks`, `chunks_fts`, `chunks_vec` and `episode_vec` are not created
    [`sqlite_podcast_repository.py:_run_migrations`].
- **Disk reads and writes**:
  - Both podcast repositories read `data/top_podcasts_*.json` at
    construction. The mtime-gated seed writes the `top_podcasts*` tables and
    backfills podcast chart URLs. It fails open
    [`sqlite_podcast_repository.py:_seed_top_podcasts`,
    `postgres_podcast_repository_podcasts.py:_seed_top_podcasts`].
  - A one-time SQLite migration imports
    `<db dir>/pending_operations/*.json` and moves each file into
    `.migrated/` [`sqlite_podcast_repository.py:_backfill_pending_operations_from_files`].
  - Sync-track methods return file paths for the caller to delete; the
    repository does no file I/O [`sync_track_repository.py:SyncTrackRepository`].
- **Locks**:
  - SQLite `BEGIN IMMEDIATE` writer lock:
    - `fold_import_twin`, legacy claim and `follow_with_gate`.
    - settings `modify`, `usage reserve`, `transcript append`,
      `sync_track promote`.
    - `repoint_entity_references` and `delete_orphan_entities`.
    - OAuth `approve`, `redeem_code` and `rotate_refresh`
      (`sqlite_oauth_repository.py:233, 313, 356`).
    - `move_follows_aside` and `restore_follows`
      (`sqlite_suspended_follow_repository.py:32, 50`).
    - `apply_status` and `set_admin`, only when `keep_an_active_admin=True`
      (`sqlite_user_repository.py:232, 253`).
  - Postgres advisory transaction locks, all keyed by
    `hashtextextended(key, 0)` except sync tracks:
    - `usage:{user}:{group}` [`postgres_usage_event_repository.py:lock_usage`].
    - `follow_gate:<podcast_id>`, `app_settings:<key>`.
    - `thestill:active-admins` and `oauth_grant:<user_id>:<client_id>`
      (OAuth `approve`).
    - `sync_track:<episode_id>`, keyed by `hashtext(key)` instead
      (`postgres_sync_track_repository.py:88`).
  - Postgres row locks: `FOR UPDATE` in legacy claim, orphan delete, twin
    fold and sync-track `promote` (the pending row,
    `postgres_sync_track_repository.py:90-95`); `FOR SHARE` in
    `consents_to_measurement`.
- **In-memory caches**: per-instance category maps (`_cat_id_to_pair`,
  `_cat_pair_to_id`) [`postgres_category_cache.py:CategoryCacheMixin`,
  `sqlite_podcast_repository.py:_load_categories_cache`]. There are no
  cross-request caches.
- **Session settings**: Postgres typeahead runs `SET LOCAL jit = off`
  [`postgres_entity_repository.py:search_entities_by_prefix`].
- **Logging** (structlog): most constructors log at info. Writes log events
  such as `entity_upserted`, `entity_mentions_inserted`,
  `cooccurrences_rebuilt`, `token_revoked`, `legacy_claim_transferred` and
  `postgres_schema_ensured`.
- **Network**: none. The readiness `ping` opens a short-lived connection with
  a 3 s timeout [`factory.py:ping`]. `make_search_backend` may load a reranker
  model [`factory.py:_rerank_options`].

## Verification

- Run: `./venv/bin/pytest tests/unit/repositories`
- Full suite: `make test`
- Postgres contract tests need `TEST_DATABASE_URL`.
