# Copyright 2025-2026 Thestill
#
# Licensed under the Apache License, Version 2.0 (the "License");
# you may not use this file except in compliance with the License.
# You may obtain a copy of the License at
#
#     http://www.apache.org/licenses/LICENSE-2.0
#
# Unless required by applicable law or agreed to in writing, software
# distributed under the License is distributed on an "AS IS" BASIS,
# WITHOUT WARRANTIES OR CONDITIONS OF ANY KIND, either express or implied.
# See the License for the specific language governing permissions and
# limitations under the License.

"""Canonical PostgreSQL schema for thestill (spec #44).

Single source of truth for the typed Postgres DDL, translated from the live
SQLite schema with **native Postgres types** throughout:

- ``uuid`` for every UUID-shaped key (podcasts/episodes/users/tasks/briefings/
  followers/inbox/jti). Entity ids are slugs (``company:01-advisors``)
  and stay ``text``; provider-issued ``operation_id`` stays ``text``.
- ``timestamptz`` for every timestamp (removes the SQLite text-timestamp
  foot-gun — spec #42 FM-3). Defaults use ``now()``.
- ``boolean`` for SQLite's 0/1 integer flags.
- ``jsonb`` for JSON-in-TEXT columns (aliases, facts, id-lists, task metadata).
- ``vector(N)`` (pgvector) for embeddings — replaces sqlite-vec BLOBs. HNSW
  indexes serve the k-NN queries that ``vec0`` virtual tables served.
- ``bigint GENERATED ALWAYS AS IDENTITY`` for AUTOINCREMENT surrogate keys.

Until alembic lands (spec #44 Phase 5 follow-up), ``ensure_schema`` is the
idempotent bootstrap used by the repository factory at startup and by tests.
"""

from __future__ import annotations

from pathlib import Path

from structlog import get_logger

logger = get_logger(__name__)

# Default embedding dimensionality: paraphrase-multilingual-MiniLM-L12-v2.
DEFAULT_EMBEDDING_DIM = 384

# Tables in FK dependency order. {dim} is the pgvector dimensionality.
SCHEMA_SQL = """
CREATE EXTENSION IF NOT EXISTS vector;
-- pg_trgm backs the ILIKE '%term%' searches (episode titles, entity
-- typeahead) with GIN trigram indexes (spec #69 Phase 1.5).
CREATE EXTENSION IF NOT EXISTS pg_trgm;

-- ===== users / auth ======================================================
CREATE TABLE IF NOT EXISTS users (
    id uuid PRIMARY KEY,
    email text NOT NULL UNIQUE CHECK (length(email) > 0),
    name text NULL,
    picture text NULL,
    google_id text UNIQUE,
    created_at timestamptz NOT NULL DEFAULT now(),
    last_login_at timestamptz NULL,
    region text NULL CHECK (region IS NULL OR length(region) = 2),
    region_locked boolean NOT NULL DEFAULT false,
    is_admin boolean NOT NULL DEFAULT false,
    signup_source text NULL,
    usage_analytics_enabled boolean NOT NULL DEFAULT true,
    status text NOT NULL DEFAULT 'active' CHECK (status IN ('active', 'suspended', 'banned')),
    status_reason text NULL,
    status_changed_at timestamptz NULL,
    status_changed_by uuid NULL,
    tokens_valid_after timestamptz NULL
);
CREATE INDEX IF NOT EXISTS idx_users_email ON users(email);
-- Spec #95 — converge databases bootstrapped before signup_source (alembic 0017).
ALTER TABLE users ADD COLUMN IF NOT EXISTS signup_source text NULL;
-- Spec #95 Layer B — per-user usage-events switch (alembic 0018).
ALTER TABLE users ADD COLUMN IF NOT EXISTS usage_analytics_enabled boolean NOT NULL DEFAULT true;
-- Spec #104 — account status + token revocation (alembic 0021).
ALTER TABLE users ADD COLUMN IF NOT EXISTS status text NOT NULL DEFAULT 'active'
    CHECK (status IN ('active', 'suspended', 'banned'));
ALTER TABLE users ADD COLUMN IF NOT EXISTS status_reason text NULL;
ALTER TABLE users ADD COLUMN IF NOT EXISTS status_changed_at timestamptz NULL;
ALTER TABLE users ADD COLUMN IF NOT EXISTS status_changed_by uuid NULL;
ALTER TABLE users ADD COLUMN IF NOT EXISTS tokens_valid_after timestamptz NULL;
CREATE INDEX IF NOT EXISTS idx_users_google_id ON users(google_id) WHERE google_id IS NOT NULL;

CREATE TABLE IF NOT EXISTS revoked_tokens (
    jti uuid PRIMARY KEY,
    expires_at timestamptz NOT NULL,
    revoked_at timestamptz NOT NULL DEFAULT now()
);
CREATE INDEX IF NOT EXISTS idx_revoked_tokens_expires_at ON revoked_tokens(expires_at);

-- Spec #78 Phase 2 — per-user remote MCP tokens (one row per user,
-- replaced in place on rotate; looked up by hash from the ASGI guard).
CREATE TABLE IF NOT EXISTS mcp_tokens (
    user_id uuid PRIMARY KEY REFERENCES users(id) ON DELETE CASCADE,
    token_hash text NOT NULL,
    token_prefix text NOT NULL,
    scopes text NOT NULL DEFAULT 'read',
    created_at timestamptz NOT NULL DEFAULT now(),
    expires_at timestamptz NULL,
    last_used_at timestamptz NULL,
    last_used_ip text NULL,
    revoked_at timestamptz NULL
);
CREATE UNIQUE INDEX IF NOT EXISTS idx_mcp_tokens_hash ON mcp_tokens(token_hash);

-- Spec #104 — runtime switches that override env defaults, and the
-- append-only record of every admin action. No FK on the user columns:
-- the log must outlive a deleted account.
CREATE TABLE IF NOT EXISTS app_settings (
    key text PRIMARY KEY,
    value jsonb NOT NULL,
    updated_at timestamptz NOT NULL DEFAULT now(),
    updated_by uuid NULL
);

CREATE TABLE IF NOT EXISTS admin_audit_log (
    id bigint GENERATED ALWAYS AS IDENTITY PRIMARY KEY,
    created_at timestamptz NOT NULL DEFAULT now(),
    actor_user_id uuid NULL,
    actor text NOT NULL CHECK (actor IN ('web', 'cli', 'system')),
    action text NOT NULL,
    target_user_id uuid NULL,
    details jsonb NOT NULL DEFAULT '{{}}'::jsonb
);
CREATE INDEX IF NOT EXISTS idx_admin_audit_created ON admin_audit_log(created_at DESC);
CREATE INDEX IF NOT EXISTS idx_admin_audit_target ON admin_audit_log(target_user_id, created_at DESC);

-- ===== categories / podcasts / episodes ==================================
CREATE TABLE IF NOT EXISTS categories (
    id bigint GENERATED ALWAYS AS IDENTITY PRIMARY KEY,
    name text NOT NULL,
    slug text NOT NULL,
    parent_id bigint NULL REFERENCES categories(id) ON DELETE CASCADE,
    apple_genre_id bigint NULL
);
CREATE INDEX IF NOT EXISTS idx_categories_parent ON categories(parent_id);
CREATE UNIQUE INDEX IF NOT EXISTS idx_categories_sub_unique ON categories(parent_id, name) WHERE parent_id IS NOT NULL;
CREATE UNIQUE INDEX IF NOT EXISTS idx_categories_top_unique ON categories(name) WHERE parent_id IS NULL;

CREATE TABLE IF NOT EXISTS podcasts (
    id uuid PRIMARY KEY,
    created_at timestamptz NOT NULL DEFAULT now(),
    updated_at timestamptz NOT NULL DEFAULT now(),
    rss_url text NOT NULL UNIQUE CHECK (length(rss_url) > 0),
    title text NOT NULL,
    description text NOT NULL DEFAULT '',
    last_processed timestamptz NULL,
    slug text NOT NULL DEFAULT '',
    image_url text NULL,
    language text NOT NULL DEFAULT 'en',
    author text NULL,
    explicit boolean NULL,
    show_type text NULL,
    website_url text NULL,
    is_complete boolean NOT NULL DEFAULT false,
    copyright text NULL,
    etag text NULL,
    last_modified text NULL,
    -- Spec #73 follow-up: store links copied from top_podcasts on rss_url
    -- (migration 0010); never parsed from the feed.
    apple_url text NULL,
    youtube_url text NULL,
    spotify_url text NULL,
    -- Spec #87 Phase 3c: where each show link came from; a curated value is
    -- never overwritten by a chart sync or a resolver (migration 0013).
    apple_url_source text NULL CHECK (apple_url_source IN ('chart', 'publisher', 'resolver', 'curated')),
    youtube_url_source text NULL CHECK (youtube_url_source IN ('chart', 'publisher', 'resolver', 'curated')),
    spotify_url_source text NULL CHECK (spotify_url_source IN ('chart', 'publisher', 'resolver', 'curated')),
    -- Spec #87 Phase 3d: when YouTube channel discovery last ran (migration 0023).
    youtube_discovery_checked_at timestamptz NULL,
    primary_category_id bigint NULL REFERENCES categories(id) ON DELETE SET NULL,
    secondary_category_id bigint NULL REFERENCES categories(id) ON DELETE SET NULL,
    host_entity_ids jsonb NOT NULL DEFAULT '[]'::jsonb,
    recurring_entity_ids jsonb NOT NULL DEFAULT '[]'::jsonb,
    synthetic boolean NOT NULL DEFAULT false,
    auto_added boolean NOT NULL DEFAULT false,
    last_processed_at timestamptz NULL,
    refresh_interval_seconds bigint NULL,
    next_refresh_at timestamptz NULL,
    last_refresh_at timestamptz NULL,
    last_refresh_error text NULL,
    -- spec #60: durable per-kind failure policy state. A feed is QUARANTINED
    -- (next_refresh_at NULL) only with an explicit refresh_disabled_reason;
    -- connectivity failures never park.
    last_refresh_failure_kind text NULL,
    last_refresh_status_code integer NULL,
    consecutive_refresh_failures integer NOT NULL DEFAULT 0,
    refresh_failure_streak_started_at timestamptz NULL,
    refresh_disabled_reason text NULL,
    refresh_retry_after_at timestamptz NULL,
    -- Spec #94: false hides the show from every signed-out surface (migration 0015).
    public_pages boolean NOT NULL DEFAULT true
);
CREATE INDEX IF NOT EXISTS idx_podcasts_slug ON podcasts(slug) WHERE slug != '';
CREATE INDEX IF NOT EXISTS idx_podcasts_next_refresh ON podcasts(next_refresh_at) WHERE next_refresh_at IS NOT NULL;
-- Spec #60 — converge databases bootstrapped before failure classification
-- landed (CREATE TABLE IF NOT EXISTS skips existing tables, so the new
-- columns need explicit idempotent ALTERs; same contract as alembic 0006).
ALTER TABLE podcasts
    ADD COLUMN IF NOT EXISTS last_refresh_failure_kind text NULL,
    ADD COLUMN IF NOT EXISTS last_refresh_status_code integer NULL,
    ADD COLUMN IF NOT EXISTS consecutive_refresh_failures integer NOT NULL DEFAULT 0,
    ADD COLUMN IF NOT EXISTS refresh_failure_streak_started_at timestamptz NULL,
    ADD COLUMN IF NOT EXISTS refresh_disabled_reason text NULL,
    ADD COLUMN IF NOT EXISTS refresh_retry_after_at timestamptz NULL;
ALTER TABLE podcasts ADD COLUMN IF NOT EXISTS public_pages boolean NOT NULL DEFAULT true;
-- Spec #87 Phase 3d (alembic 0023).
ALTER TABLE podcasts ADD COLUMN IF NOT EXISTS youtube_discovery_checked_at timestamptz NULL;
CREATE INDEX IF NOT EXISTS idx_podcasts_quarantine ON podcasts(refresh_disabled_reason, last_refresh_at) WHERE refresh_disabled_reason IS NOT NULL;

CREATE TABLE IF NOT EXISTS episodes (
    id uuid PRIMARY KEY,
    podcast_id uuid NOT NULL REFERENCES podcasts(id),
    created_at timestamptz NOT NULL DEFAULT now(),
    updated_at timestamptz NOT NULL DEFAULT now(),
    external_id text NOT NULL CHECK (length(external_id) > 0),
    title text NOT NULL,
    description text NOT NULL DEFAULT '',
    pub_date timestamptz NULL,
    audio_url text NOT NULL CHECK (length(audio_url) > 0),
    duration text NULL,
    audio_path text NULL,
    downsampled_audio_path text NULL,
    raw_transcript_path text NULL,
    clean_transcript_path text NULL,
    summary_path text NULL,
    summary_preview text NULL,
    slug text NOT NULL DEFAULT '',
    image_url text NULL,
    failed_at_stage text NULL,
    failure_reason text NULL,
    failure_type text NULL,
    failed_at timestamptz NULL,
    description_html text NOT NULL DEFAULT '',
    explicit boolean NULL,
    episode_type text NULL,
    episode_number bigint NULL,
    season_number bigint NULL,
    website_url text NULL,
    audio_file_size bigint NULL,
    audio_mime_type text NULL,
    clean_transcript_json_path text NULL,
    playback_time_offset_seconds double precision NOT NULL DEFAULT 0.0,
    entity_extraction_status text NULL,
    guest_entity_ids jsonb NOT NULL DEFAULT '[]'::jsonb,
    published_at timestamptz NULL,
    canonical_id text NULL,
    auto_process_excluded boolean NOT NULL DEFAULT false,
    UNIQUE(podcast_id, external_id)
);
CREATE UNIQUE INDEX IF NOT EXISTS idx_episodes_canonical_id ON episodes(canonical_id) WHERE canonical_id IS NOT NULL;
CREATE INDEX IF NOT EXISTS idx_episodes_external_id ON episodes(podcast_id, external_id);
CREATE INDEX IF NOT EXISTS idx_episodes_podcast_id ON episodes(podcast_id);
-- NULLS LAST matches every hot listing's ORDER BY (spec #69 Phase 1.1);
-- the default DESC NULLS FIRST index could not serve those sorts.
-- Migration 0007 drops the old idx_episodes_pub_date on existing DBs.
CREATE INDEX IF NOT EXISTS idx_episodes_pub_date_nulls_last ON episodes(pub_date DESC NULLS LAST);
CREATE INDEX IF NOT EXISTS idx_episodes_published_at ON episodes(published_at DESC) WHERE published_at IS NOT NULL;
CREATE INDEX IF NOT EXISTS idx_episodes_slug ON episodes(podcast_id, slug) WHERE slug != '';
CREATE INDEX IF NOT EXISTS idx_episodes_state_discovered ON episodes(podcast_id, pub_date DESC) WHERE audio_path IS NULL;
CREATE INDEX IF NOT EXISTS idx_episodes_state_downloaded ON episodes(podcast_id, pub_date DESC) WHERE audio_path IS NOT NULL AND downsampled_audio_path IS NULL;
CREATE INDEX IF NOT EXISTS idx_episodes_state_downsampled ON episodes(podcast_id, pub_date DESC) WHERE downsampled_audio_path IS NOT NULL AND raw_transcript_path IS NULL;
CREATE INDEX IF NOT EXISTS idx_episodes_state_transcribed ON episodes(podcast_id, pub_date DESC) WHERE raw_transcript_path IS NOT NULL AND clean_transcript_path IS NULL;
CREATE INDEX IF NOT EXISTS idx_episodes_state_cleaned ON episodes(podcast_id, pub_date DESC) WHERE clean_transcript_path IS NOT NULL AND summary_path IS NULL;
CREATE INDEX IF NOT EXISTS idx_episodes_failed ON episodes(failed_at DESC) WHERE failed_at_stage IS NOT NULL;
CREATE INDEX IF NOT EXISTS idx_episodes_updated_at ON episodes(updated_at DESC);
CREATE INDEX IF NOT EXISTS idx_episodes_title_trgm ON episodes USING gin (title gin_trgm_ops);
-- jsonb_path_ops: only @> containment is queried (entity guest-episode lookups).
CREATE INDEX IF NOT EXISTS idx_episodes_guest_entities ON episodes USING gin (guest_entity_ids jsonb_path_ops);

CREATE TABLE IF NOT EXISTS episode_alternate_enclosures (
    id bigint GENERATED ALWAYS AS IDENTITY PRIMARY KEY,
    episode_id uuid NOT NULL REFERENCES episodes(id) ON DELETE CASCADE,
    source_uri text NOT NULL CHECK (length(source_uri) > 0),
    mime_type text NOT NULL CHECK (length(mime_type) > 0),
    length bigint NULL,
    bitrate double precision NULL,
    height bigint NULL,
    title text NULL,
    rel text NULL,
    language text NULL,
    is_default boolean NOT NULL DEFAULT false,
    created_at timestamptz NOT NULL DEFAULT now(),
    UNIQUE(episode_id, source_uri)
);
CREATE INDEX IF NOT EXISTS idx_alt_enclosures_episode ON episode_alternate_enclosures(episode_id);
CREATE INDEX IF NOT EXISTS idx_alt_enclosures_mime_type ON episode_alternate_enclosures(mime_type);

-- Spec #87 — per-episode links to the same episode on a listening platform.
-- url NULL = checked, not found; checked_at throttles re-lookups.
CREATE TABLE IF NOT EXISTS episode_platform_links (
    id bigint GENERATED ALWAYS AS IDENTITY PRIMARY KEY,
    episode_id uuid NOT NULL REFERENCES episodes(id) ON DELETE CASCADE,
    platform text NOT NULL CHECK (platform IN ('apple', 'spotify', 'youtube')),
    url text NULL,
    external_ref text NULL,
    match_method text NULL CHECK (match_method IN ('guid', 'audio_url', 'title_date', 'title_duration', 'publisher')),
    checked_at timestamptz NOT NULL,
    created_at timestamptz NOT NULL DEFAULT now(),
    UNIQUE(episode_id, platform)
);

CREATE TABLE IF NOT EXISTS episode_transcript_links (
    id bigint GENERATED ALWAYS AS IDENTITY PRIMARY KEY,
    episode_id uuid NOT NULL REFERENCES episodes(id) ON DELETE CASCADE,
    url text NOT NULL CHECK (length(url) > 0),
    mime_type text NOT NULL CHECK (length(mime_type) > 0),
    language text NULL,
    rel text NULL,
    downloaded_path text NULL,
    created_at timestamptz NOT NULL DEFAULT now(),
    UNIQUE(episode_id, url)
);
CREATE INDEX IF NOT EXISTS idx_transcript_links_episode ON episode_transcript_links(episode_id);
CREATE INDEX IF NOT EXISTS idx_transcript_links_mime_type ON episode_transcript_links(mime_type);
CREATE INDEX IF NOT EXISTS idx_transcript_links_not_downloaded ON episode_transcript_links(episode_id) WHERE downloaded_path IS NULL;

-- ===== follows / inbox / briefings ========================================
CREATE TABLE IF NOT EXISTS podcast_followers (
    id uuid PRIMARY KEY,
    user_id uuid NOT NULL REFERENCES users(id) ON DELETE CASCADE,
    podcast_id uuid NOT NULL REFERENCES podcasts(id) ON DELETE CASCADE,
    created_at timestamptz NOT NULL DEFAULT now(),
    UNIQUE(user_id, podcast_id)
);
CREATE INDEX IF NOT EXISTS idx_followers_user ON podcast_followers(user_id);
CREATE INDEX IF NOT EXISTS idx_followers_podcast ON podcast_followers(podcast_id);

-- Spec #104 — a banned user's follows, moved out of podcast_followers so
-- the follower gate stops feeds only they followed. Unban moves them back
-- with the original created_at.
CREATE TABLE IF NOT EXISTS suspended_follows (
    user_id uuid NOT NULL REFERENCES users(id) ON DELETE CASCADE,
    podcast_id uuid NOT NULL REFERENCES podcasts(id) ON DELETE CASCADE,
    created_at timestamptz NOT NULL,
    suspended_at timestamptz NOT NULL DEFAULT now(),
    PRIMARY KEY (user_id, podcast_id)
);
CREATE INDEX IF NOT EXISTS idx_suspended_follows_podcast ON suspended_follows(podcast_id);

-- Spec #104 Phase 2 — the per-user usage ledger behind quotas (alembic 0022).
CREATE TABLE IF NOT EXISTS usage_events (
    id uuid PRIMARY KEY,
    user_id uuid NOT NULL REFERENCES users(id) ON DELETE CASCADE,
    kind text NOT NULL
        CHECK (kind IN ('feed_introduced', 'follow', 'import', 'translation', 'narration', 'quota_exceeded')),
    state text NOT NULL CHECK (state IN ('reserved', 'committed', 'released')),
    created_at timestamptz NOT NULL,
    settled_at timestamptz NULL,
    subject_id text NULL
);
CREATE INDEX IF NOT EXISTS idx_usage_events_user_kind ON usage_events(user_id, kind, created_at);
CREATE INDEX IF NOT EXISTS idx_usage_events_kind_created ON usage_events(kind, created_at);
CREATE INDEX IF NOT EXISTS idx_usage_events_reserved ON usage_events(created_at) WHERE state = 'reserved';

CREATE TABLE IF NOT EXISTS user_episode_inbox (
    id uuid PRIMARY KEY,
    user_id uuid NOT NULL REFERENCES users(id) ON DELETE CASCADE,
    episode_id uuid NOT NULL REFERENCES episodes(id) ON DELETE CASCADE,
    source text NOT NULL CHECK (source IN ('follow_new','follow_seed','ad_hoc','import')),
    state text NOT NULL DEFAULT 'unread' CHECK (state IN ('unread','read','saved','dismissed')),
    delivered_at timestamptz NOT NULL DEFAULT now(),
    state_changed_at timestamptz NULL,
    UNIQUE(user_id, episode_id)
);
CREATE INDEX IF NOT EXISTS idx_inbox_user_state ON user_episode_inbox(user_id, state, delivered_at DESC);
-- Default inbox view filters ``state != 'dismissed'`` — the ``!=`` breaks
-- idx_inbox_user_state's middle column, so the sort needs (user_id,
-- delivered_at) directly. Name mirrors the SQLite schema's idx_inbox_user_all.
CREATE INDEX IF NOT EXISTS idx_inbox_user_all ON user_episode_inbox(user_id, delivered_at DESC);
CREATE INDEX IF NOT EXISTS idx_inbox_user_source ON user_episode_inbox(user_id, source, delivered_at);
CREATE INDEX IF NOT EXISTS idx_inbox_episode ON user_episode_inbox(episode_id);

-- Spec #90 — one listening position per user and episode, on the logical
-- (transcript) timeline. Writes are ordered by (session_id, seq), never by
-- client clocks; updated_at is server time.
CREATE TABLE IF NOT EXISTS user_episode_progress (
    user_id uuid NOT NULL REFERENCES users(id) ON DELETE CASCADE,
    episode_id uuid NOT NULL REFERENCES episodes(id) ON DELETE CASCADE,
    position_seconds double precision NOT NULL CHECK (position_seconds >= 0),
    duration_seconds double precision NULL CHECK (duration_seconds IS NULL OR duration_seconds > 0),
    completed_at timestamptz NULL,
    stopped_at timestamptz NULL,
    session_id text NOT NULL,
    seq bigint NOT NULL,
    updated_at timestamptz NOT NULL,
    listened_at timestamptz NULL,
    PRIMARY KEY (user_id, episode_id)
);
CREATE INDEX IF NOT EXISTS idx_progress_user_updated ON user_episode_progress(user_id, updated_at DESC);
-- Spec #103 — converge databases bootstrapped before listened_at (alembic
-- 0019; the backfill lives only in the migration).
ALTER TABLE user_episode_progress ADD COLUMN IF NOT EXISTS listened_at timestamptz NULL;

-- Spec #103 — the first time each user opened each episode (analytics only).
CREATE TABLE IF NOT EXISTS user_episode_views (
    user_id uuid NOT NULL REFERENCES users(id) ON DELETE CASCADE,
    episode_id uuid NOT NULL REFERENCES episodes(id) ON DELETE CASCADE,
    first_viewed_at timestamptz NOT NULL,
    PRIMARY KEY (user_id, episode_id)
);
CREATE INDEX IF NOT EXISTS idx_episode_views_at ON user_episode_views(first_viewed_at);

-- Spec #103 Phase 2 — signed-out views per public page per UTC day, keyed
-- by object id ('' for landing and top). No visitor id, IP or referrer.
CREATE TABLE IF NOT EXISTS public_page_daily_views (
    day date NOT NULL,
    kind text NOT NULL,
    object_id text NOT NULL,
    views integer NOT NULL,
    PRIMARY KEY (day, kind, object_id)
);

CREATE TABLE IF NOT EXISTS user_briefings (
    id uuid PRIMARY KEY,
    user_id uuid NOT NULL REFERENCES users(id) ON DELETE CASCADE,
    cursor_from timestamptz NOT NULL,
    cursor_to timestamptz NOT NULL,
    episode_count bigint NOT NULL CHECK (episode_count >= 0),
    script_path text NULL,
    audio_path text NULL,
    created_at timestamptz NOT NULL DEFAULT now(),
    listened_at timestamptz NULL,
    opened_at timestamptz NULL,
    CHECK (cursor_to > cursor_from)
);
CREATE INDEX IF NOT EXISTS idx_user_briefings_user ON user_briefings(user_id, created_at DESC);
-- Spec #95 — converge databases bootstrapped before opened_at (alembic 0017;
-- the listened_at backfill lives only in the migration).
ALTER TABLE user_briefings ADD COLUMN IF NOT EXISTS opened_at timestamptz NULL;

CREATE TABLE IF NOT EXISTS user_briefing_schedules (
    user_id uuid PRIMARY KEY REFERENCES users(id) ON DELETE CASCADE,
    frequency text NOT NULL DEFAULT 'daily' CHECK (frequency IN ('daily','weekly')),
    hour_local bigint NOT NULL DEFAULT 8 CHECK (hour_local BETWEEN 0 AND 23),
    weekday bigint NULL CHECK (weekday IS NULL OR weekday BETWEEN 0 AND 6),
    timezone text NOT NULL,
    enabled boolean NOT NULL DEFAULT true,
    email_enabled boolean NOT NULL DEFAULT false,
    next_run_at timestamptz NULL,
    created_at timestamptz NOT NULL DEFAULT now(),
    updated_at timestamptz NOT NULL DEFAULT now(),
    CHECK ((frequency = 'weekly') = (weekday IS NOT NULL))
);
CREATE INDEX IF NOT EXISTS idx_briefing_schedules_due
    ON user_briefing_schedules(next_run_at)
    WHERE enabled AND next_run_at IS NOT NULL;
-- Spec #51 — converge databases bootstrapped before email delivery landed
-- (CREATE TABLE IF NOT EXISTS skips existing tables, so the new column
-- needs an explicit idempotent ALTER).
ALTER TABLE user_briefing_schedules
    ADD COLUMN IF NOT EXISTS email_enabled boolean NOT NULL DEFAULT false;

-- Spec #51 — briefing email deliveries. One row per (briefing, channel):
-- the UNIQUE constraint is the send-once anchor. ``next_attempt_at``
-- drives the due-scan while pending and acts as the claim lease while
-- sending; NULL once terminal.
CREATE TABLE IF NOT EXISTS briefing_deliveries (
    id uuid PRIMARY KEY,
    briefing_id uuid NOT NULL REFERENCES user_briefings(id) ON DELETE CASCADE,
    channel text NOT NULL DEFAULT 'email' CHECK (channel IN ('email')),
    status text NOT NULL DEFAULT 'pending' CHECK (status IN ('pending','sending','sent','failed')),
    attempts bigint NOT NULL DEFAULT 0 CHECK (attempts >= 0),
    next_attempt_at timestamptz NULL,
    sent_at timestamptz NULL,
    last_error text NULL,
    created_at timestamptz NOT NULL DEFAULT now(),
    UNIQUE (briefing_id, channel)
);
CREATE INDEX IF NOT EXISTS idx_briefing_deliveries_due
    ON briefing_deliveries(next_attempt_at)
    WHERE status IN ('pending','sending');

-- ===== task queue =========================================================
CREATE TABLE IF NOT EXISTS tasks (
    id uuid PRIMARY KEY,
    episode_id uuid NULL REFERENCES episodes(id),
    podcast_id uuid NULL REFERENCES podcasts(id),
    stage text NOT NULL CHECK (stage IN ('download','downsample','transcribe','clean','summarize','extract-entities','resolve-entities','reindex','rebuild-cooccurrences','compute-related','enrich-entities','refresh-feed')),
    status text NOT NULL DEFAULT 'pending',
    priority bigint DEFAULT 0,
    error_message text NULL,
    created_at timestamptz NOT NULL DEFAULT now(),
    updated_at timestamptz NOT NULL DEFAULT now(),
    started_at timestamptz NULL,
    completed_at timestamptz NULL,
    retry_count bigint DEFAULT 0,
    max_retries bigint DEFAULT 3,
    next_retry_at timestamptz NULL,
    error_type text NULL,
    last_error text NULL,
    metadata jsonb NULL,
    error_class text NULL,
    heal_attempts bigint DEFAULT 0,
    last_heal_at timestamptz NULL,
    CHECK ((episode_id IS NOT NULL) <> (podcast_id IS NOT NULL))
);
CREATE INDEX IF NOT EXISTS idx_tasks_claim ON tasks(stage, priority DESC, created_at) WHERE status IN ('pending','retry_scheduled');
CREATE INDEX IF NOT EXISTS idx_tasks_episode ON tasks(episode_id) WHERE episode_id IS NOT NULL;
-- Mirrors SQLite's idx_tasks_podcast_stage: feed-scoped lookups plus the
-- otherwise-unindexed FK for podcast cascade checks.
CREATE INDEX IF NOT EXISTS idx_tasks_podcast_stage ON tasks(podcast_id, stage) WHERE podcast_id IS NOT NULL;
CREATE INDEX IF NOT EXISTS idx_tasks_status ON tasks(status);

-- ===== pending transcription ops =========================================
CREATE TABLE IF NOT EXISTS pending_transcription_operations (
    operation_id text PRIMARY KEY,
    provider text NOT NULL CHECK (provider IN ('google','elevenlabs','dalston')),
    episode_id uuid NOT NULL,
    payload_json jsonb NOT NULL,
    created_at timestamptz NOT NULL DEFAULT now(),
    updated_at timestamptz NOT NULL DEFAULT now()
);

-- ===== entities ===========================================================
CREATE TABLE IF NOT EXISTS entities (
    id text PRIMARY KEY,
    type text NOT NULL CHECK (type IN ('person','company','product','topic')),
    canonical_name text NOT NULL,
    wikidata_qid text NULL,
    aliases jsonb NOT NULL DEFAULT '[]'::jsonb,
    description text NULL,
    created_at timestamptz NOT NULL DEFAULT now(),
    updated_at timestamptz NOT NULL DEFAULT now(),
    wikidata_instance_of jsonb NOT NULL DEFAULT '[]'::jsonb,
    -- Spec #94: true hides the entity's public page and every quote it speaks (migration 0015).
    public_hidden boolean NOT NULL DEFAULT false
);
ALTER TABLE entities ADD COLUMN IF NOT EXISTS public_hidden boolean NOT NULL DEFAULT false;
CREATE INDEX IF NOT EXISTS idx_entities_type ON entities(type);
CREATE INDEX IF NOT EXISTS idx_entities_wikidata ON entities(wikidata_qid) WHERE wikidata_qid IS NOT NULL;
CREATE INDEX IF NOT EXISTS idx_entities_name_trgm ON entities USING gin (canonical_name gin_trgm_ops);
CREATE INDEX IF NOT EXISTS idx_entities_name_lower ON entities(LOWER(canonical_name));

CREATE TABLE IF NOT EXISTS entity_mentions (
    id bigint GENERATED ALWAYS AS IDENTITY PRIMARY KEY,
    entity_id text NULL REFERENCES entities(id) ON DELETE CASCADE,
    resolution_status text NOT NULL DEFAULT 'pending' CHECK (resolution_status IN ('pending','resolved','unresolvable','ambiguous','dropped')),
    episode_id uuid NOT NULL REFERENCES episodes(id) ON DELETE CASCADE,
    segment_id bigint NOT NULL,
    start_ms bigint NOT NULL,
    end_ms bigint NOT NULL,
    speaker text NULL,
    role text NULL CHECK (role IS NULL OR role IN ('host','guest','mentioned','self','speaking')),
    surface_form text NOT NULL,
    surface_label text NULL,
    quote_excerpt text NOT NULL,
    sentiment double precision NULL,
    confidence double precision NOT NULL,
    extractor text NOT NULL,
    resolution_method text NULL,
    candidate_entity_ids jsonb NULL,
    created_at timestamptz NOT NULL DEFAULT now(),
    resolved_at timestamptz NULL
);
CREATE INDEX IF NOT EXISTS idx_mentions_entity ON entity_mentions(entity_id, episode_id) WHERE entity_id IS NOT NULL;
CREATE INDEX IF NOT EXISTS idx_mentions_episode ON entity_mentions(episode_id);
-- Spec #94: public quotes join a mention to its segment's speaking row (migration 0015).
CREATE INDEX IF NOT EXISTS idx_mentions_episode_segment ON entity_mentions(episode_id, segment_id);
CREATE INDEX IF NOT EXISTS idx_mentions_pending ON entity_mentions(resolution_status) WHERE resolution_status = 'pending';
CREATE INDEX IF NOT EXISTS idx_mentions_role ON entity_mentions(entity_id, role) WHERE entity_id IS NOT NULL;
-- Per-mention resolution looks up by case-folded surface form; without this
-- every lookup scans the largest entity table (spec #69 Phase 1.5).
CREATE INDEX IF NOT EXISTS idx_mentions_surface_lower ON entity_mentions(LOWER(surface_form));

CREATE TABLE IF NOT EXISTS entity_cooccurrences (
    entity_a_id text NOT NULL REFERENCES entities(id) ON DELETE CASCADE,
    entity_b_id text NOT NULL REFERENCES entities(id) ON DELETE CASCADE,
    episode_count bigint NOT NULL,
    last_seen_at timestamptz NOT NULL,
    PRIMARY KEY (entity_a_id, entity_b_id),
    CHECK (entity_a_id < entity_b_id)
);
-- The PK only covers the a-side; the ``OR entity_b_id = %s`` query leg and
-- the ON DELETE CASCADE from entities need the b-side covered too.
CREATE INDEX IF NOT EXISTS idx_cooccur_entity_b ON entity_cooccurrences(entity_b_id);

CREATE TABLE IF NOT EXISTS entity_enrichment (
    entity_id text PRIMARY KEY REFERENCES entities(id) ON DELETE CASCADE,
    image_url text NULL,
    image_attribution text NULL,
    image_license text NULL,
    headline text NULL,
    wikipedia_extract text NULL,
    wikipedia_url text NULL,
    facts_json jsonb NOT NULL DEFAULT '[]'::jsonb,
    affiliations_json jsonb NOT NULL DEFAULT '[]'::jsonb,
    wikidata_status text NOT NULL DEFAULT 'pending' CHECK (wikidata_status IN ('pending','ok','empty','failed')),
    wikidata_fetched_at timestamptz NULL,
    wikipedia_status text NOT NULL DEFAULT 'pending' CHECK (wikipedia_status IN ('pending','ok','empty','failed')),
    wikipedia_fetched_at timestamptz NULL,
    retry_after timestamptz NULL,
    schema_version bigint NOT NULL DEFAULT 1,
    created_at timestamptz NOT NULL DEFAULT now(),
    updated_at timestamptz NOT NULL DEFAULT now()
);
CREATE INDEX IF NOT EXISTS idx_enrichment_status ON entity_enrichment(wikidata_status, wikipedia_status);

CREATE TABLE IF NOT EXISTS mention_overrides (
    id bigint GENERATED ALWAYS AS IDENTITY PRIMARY KEY,
    surface_form text NOT NULL,
    episode_id uuid NULL REFERENCES episodes(id) ON DELETE CASCADE,
    override_kind text NOT NULL CHECK (override_kind IN ('drop','force_entity','force_unresolvable')),
    entity_id text NULL REFERENCES entities(id) ON DELETE SET NULL,
    reason text NULL,
    created_by text NULL,
    created_at timestamptz NOT NULL DEFAULT now()
);
CREATE INDEX IF NOT EXISTS idx_overrides_surface_lower ON mention_overrides(LOWER(surface_form));
CREATE INDEX IF NOT EXISTS idx_overrides_episode ON mention_overrides(episode_id) WHERE episode_id IS NOT NULL;
CREATE INDEX IF NOT EXISTS idx_overrides_entity ON mention_overrides(entity_id) WHERE entity_id IS NOT NULL;

CREATE TABLE IF NOT EXISTS resolution_blacklist (
    id bigint GENERATED ALWAYS AS IDENTITY PRIMARY KEY,
    surface_form text NOT NULL,
    wrong_qid text NOT NULL,
    reason text NULL,
    created_at timestamptz NOT NULL DEFAULT now(),
    UNIQUE(surface_form, wrong_qid)
);
-- is_blacklisted matches on LOWER(surface_form), which the UNIQUE above
-- cannot serve.
CREATE INDEX IF NOT EXISTS idx_blacklist_surface_lower ON resolution_blacklist(LOWER(surface_form));

-- Spec #81 — live-linker decision cache. One row per name per scope: a
-- podcast, or corpus-wide when podcast_id is NULL. NULLs never collide in a
-- UNIQUE, so each scope gets its own partial unique index, which is also the
-- upsert's conflict target. surface_key is case-folded in Python.
CREATE TABLE IF NOT EXISTS entity_link_decisions (
    id bigint GENERATED ALWAYS AS IDENTITY PRIMARY KEY,
    surface_key text NOT NULL,
    podcast_id uuid NULL REFERENCES podcasts(id) ON DELETE CASCADE,
    qid text NULL,
    label text NULL,
    description text NULL,
    confidence text NOT NULL CHECK (confidence IN ('high','medium','low')),
    reason text NULL,
    decided_at timestamptz NOT NULL DEFAULT now(),
    linker_version text NOT NULL,
    hits integer NOT NULL DEFAULT 0
);
CREATE UNIQUE INDEX IF NOT EXISTS idx_link_decisions_podcast
    ON entity_link_decisions(surface_key, podcast_id) WHERE podcast_id IS NOT NULL;
CREATE UNIQUE INDEX IF NOT EXISTS idx_link_decisions_corpus
    ON entity_link_decisions(surface_key) WHERE podcast_id IS NULL;
-- Spec #92: the chooser saw a Resource List hint for this name (NULL = no).
ALTER TABLE entity_link_decisions ADD COLUMN IF NOT EXISTS hinted boolean NULL;

-- ===== search: chunks + vectors (pgvector replaces sqlite-vec/FTS5) ======
CREATE TABLE IF NOT EXISTS chunks (
    id bigint GENERATED ALWAYS AS IDENTITY PRIMARY KEY,
    episode_id uuid NOT NULL REFERENCES episodes(id) ON DELETE CASCADE,
    segment_id bigint NOT NULL,
    start_ms bigint NOT NULL,
    end_ms bigint NOT NULL,
    speaker text NULL,
    text text NOT NULL,
    embedding_model text NOT NULL,
    embedding vector({dim}) NOT NULL,
    created_at timestamptz NOT NULL DEFAULT now(),
    -- FTS: generated tsvector replaces the chunks_fts FTS5 mirror. Speaker
    -- prefix is stripped by the writer before storage, so plain text here.
    text_tsv tsvector GENERATED ALWAYS AS (to_tsvector('english', text)) STORED,
    UNIQUE (episode_id, segment_id, embedding_model)
);
CREATE INDEX IF NOT EXISTS idx_chunks_episode ON chunks(episode_id);
CREATE INDEX IF NOT EXISTS idx_chunks_embedding_hnsw ON chunks USING hnsw (embedding vector_cosine_ops);
CREATE INDEX IF NOT EXISTS idx_chunks_text_tsv ON chunks USING gin (text_tsv);

CREATE TABLE IF NOT EXISTS episode_vectors (
    episode_id uuid NOT NULL REFERENCES episodes(id) ON DELETE CASCADE,
    embedding_model text NOT NULL,
    chunk_count bigint NOT NULL,
    centroid vector({dim}) NOT NULL,
    computed_at timestamptz NOT NULL DEFAULT now(),
    PRIMARY KEY (episode_id, embedding_model)
);
CREATE INDEX IF NOT EXISTS idx_episode_vectors_hnsw ON episode_vectors USING hnsw (centroid vector_cosine_ops);

CREATE TABLE IF NOT EXISTS episode_related (
    episode_id uuid NOT NULL REFERENCES episodes(id) ON DELETE CASCADE,
    related_episode_id uuid NOT NULL REFERENCES episodes(id) ON DELETE CASCADE,
    rank bigint NOT NULL,
    score double precision NOT NULL,
    computed_at timestamptz NOT NULL DEFAULT now(),
    PRIMARY KEY (episode_id, related_episode_id)
);
CREATE INDEX IF NOT EXISTS idx_episode_related_src ON episode_related(episode_id, rank);
-- The PK only covers the source side; episode cascade-deletes need the
-- related side covered or each delete seq-scans the table.
CREATE INDEX IF NOT EXISTS idx_episode_related_target ON episode_related(related_episode_id);

CREATE TABLE IF NOT EXISTS related_idf (
    term text PRIMARY KEY,
    idf double precision NOT NULL
);

-- ===== top podcasts (discovery) ==========================================
CREATE TABLE IF NOT EXISTS top_podcasts (
    id bigint GENERATED ALWAYS AS IDENTITY PRIMARY KEY,
    name text NOT NULL,
    artist text NULL,
    rss_url text NOT NULL UNIQUE,
    apple_url text NULL,
    youtube_url text NULL,
    apple_track_id text NULL,
    image_url text NULL,
    category_id bigint NULL REFERENCES categories(id) ON DELETE SET NULL,
    first_seen_at timestamptz NOT NULL,
    last_seen_at timestamptz NOT NULL
);

CREATE TABLE IF NOT EXISTS top_podcast_rankings (
    top_podcast_id bigint NOT NULL REFERENCES top_podcasts(id) ON DELETE CASCADE,
    region text NOT NULL,
    rank bigint NOT NULL,
    source_genre text NULL,
    scraped_at timestamptz NOT NULL,
    PRIMARY KEY (region, top_podcast_id)
);

CREATE TABLE IF NOT EXISTS top_podcasts_meta (
    region text PRIMARY KEY,
    source_path text NOT NULL,
    source_mtime double precision NOT NULL,
    row_count bigint NOT NULL,
    seeded_at timestamptz NOT NULL
);
"""


# Fixed application-wide advisory-lock key serializing schema bootstrap
# across PROCESSES. The in-process guard in factory._ensure_pg_schema is not
# enough: two entry points starting together (two MCP servers, MCP + web
# server, CLI + server) each run the multi-statement DDL below in one
# transaction, and their exclusive locks interleave into a deadlock
# (2026-07-28 incident: two thestill-mcp instances deadlocked on `podcasts`
# and the MCP server died at startup). Any stable bigint works; this one is
# arbitrary but must never be reused for another advisory lock in this app.
SCHEMA_BOOTSTRAP_LOCK_KEY = 0x7E5711
_LOCK_SQL = "SELECT pg_advisory_xact_lock(%s)"


def ensure_schema(dsn: str, *, embedding_dim: int = DEFAULT_EMBEDDING_DIM) -> None:
    """Idempotently create the full typed schema on ``dsn``.

    Single bootstrap call used by the repository factory and tests; replaced
    by alembic migrations once those land. ``embedding_dim`` sizes the
    pgvector columns (must match the configured embedding model).

    Concurrent callers (other processes bootstrapping the same database)
    serialize on a transaction-scoped advisory lock: the second caller waits
    for the first to commit, then runs the same DDL as a fast no-op.
    """
    import psycopg

    with psycopg.connect(dsn) as conn:
        conn.execute(_LOCK_SQL, (SCHEMA_BOOTSTRAP_LOCK_KEY,))
        conn.execute(SCHEMA_SQL.format(dim=embedding_dim))
    logger.info("postgres_schema_ensured", embedding_dim=embedding_dim)


def run_alembic_upgrade(dsn: str) -> None:
    """Run ``alembic upgrade head`` in-process against ``dsn`` (spec #66).

    Sibling of :func:`ensure_schema`: the same bootstrap-lock key serializes
    the two paths across processes, so a migrating container and a plain
    ``ensure_schema`` boot can never interleave DDL. The lock here is
    **session-level** (not the xact-scoped variant ``ensure_schema`` uses)
    because alembic opens its own transaction per revision — the lock must
    outlive any single transaction and is released in the ``finally`` (and,
    belt-and-braces, when the lock connection closes).

    The alembic config is built programmatically — ``alembic.ini`` is not
    shipped in the wheel — pointing ``script_location`` at the installed
    ``thestill/migrations`` package. The DSN travels via
    ``config.attributes`` which ``migrations/env.py`` resolves FIRST, ahead
    of any ambient ``DATABASE_URL``: the advisory lock below is held on
    ``dsn``, so alembic must never be redirected to a different database.
    """
    import psycopg
    from alembic import command
    from alembic.config import Config as AlembicConfig

    script_location = Path(__file__).resolve().parent.parent / "migrations"
    cfg = AlembicConfig()
    cfg.set_main_option("script_location", str(script_location))
    cfg.attributes["thestill_dsn"] = dsn

    with psycopg.connect(dsn, autocommit=True) as lock_conn:
        lock_conn.execute("SELECT pg_advisory_lock(%s)", (SCHEMA_BOOTSTRAP_LOCK_KEY,))
        try:
            logger.info("alembic_upgrade_started")
            command.upgrade(cfg, "head")
            logger.info("alembic_upgrade_finished")
        finally:
            lock_conn.execute("SELECT pg_advisory_unlock(%s)", (SCHEMA_BOOTSTRAP_LOCK_KEY,))
