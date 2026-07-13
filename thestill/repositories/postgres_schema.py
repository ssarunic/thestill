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

from structlog import get_logger

logger = get_logger(__name__)

# Default embedding dimensionality: paraphrase-multilingual-MiniLM-L12-v2.
DEFAULT_EMBEDDING_DIM = 384

# Tables in FK dependency order. {dim} is the pgvector dimensionality.
SCHEMA_SQL = """
CREATE EXTENSION IF NOT EXISTS vector;

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
    is_admin boolean NOT NULL DEFAULT false
);
CREATE INDEX IF NOT EXISTS idx_users_email ON users(email);
CREATE INDEX IF NOT EXISTS idx_users_google_id ON users(google_id) WHERE google_id IS NOT NULL;

CREATE TABLE IF NOT EXISTS revoked_tokens (
    jti uuid PRIMARY KEY,
    expires_at timestamptz NOT NULL,
    revoked_at timestamptz NOT NULL DEFAULT now()
);
CREATE INDEX IF NOT EXISTS idx_revoked_tokens_expires_at ON revoked_tokens(expires_at);

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
    last_refresh_error text NULL
);
CREATE INDEX IF NOT EXISTS idx_podcasts_slug ON podcasts(slug) WHERE slug != '';
CREATE INDEX IF NOT EXISTS idx_podcasts_next_refresh ON podcasts(next_refresh_at) WHERE next_refresh_at IS NOT NULL;

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
CREATE INDEX IF NOT EXISTS idx_episodes_pub_date ON episodes(pub_date DESC);
CREATE INDEX IF NOT EXISTS idx_episodes_published_at ON episodes(published_at DESC) WHERE published_at IS NOT NULL;
CREATE INDEX IF NOT EXISTS idx_episodes_slug ON episodes(podcast_id, slug) WHERE slug != '';
CREATE INDEX IF NOT EXISTS idx_episodes_state_discovered ON episodes(podcast_id, pub_date DESC) WHERE audio_path IS NULL;
CREATE INDEX IF NOT EXISTS idx_episodes_state_downloaded ON episodes(podcast_id, pub_date DESC) WHERE audio_path IS NOT NULL AND downsampled_audio_path IS NULL;
CREATE INDEX IF NOT EXISTS idx_episodes_state_downsampled ON episodes(podcast_id, pub_date DESC) WHERE downsampled_audio_path IS NOT NULL AND raw_transcript_path IS NULL;
CREATE INDEX IF NOT EXISTS idx_episodes_state_transcribed ON episodes(podcast_id, pub_date DESC) WHERE raw_transcript_path IS NOT NULL AND clean_transcript_path IS NULL;
CREATE INDEX IF NOT EXISTS idx_episodes_updated_at ON episodes(updated_at DESC);

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
CREATE INDEX IF NOT EXISTS idx_inbox_episode ON user_episode_inbox(episode_id);

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
    CHECK (cursor_to > cursor_from)
);
CREATE INDEX IF NOT EXISTS idx_user_briefings_user ON user_briefings(user_id, created_at DESC);

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
    wikidata_instance_of jsonb NOT NULL DEFAULT '[]'::jsonb
);
CREATE INDEX IF NOT EXISTS idx_entities_type ON entities(type);
CREATE INDEX IF NOT EXISTS idx_entities_wikidata ON entities(wikidata_qid) WHERE wikidata_qid IS NOT NULL;

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
CREATE INDEX IF NOT EXISTS idx_mentions_pending ON entity_mentions(resolution_status) WHERE resolution_status = 'pending';
CREATE INDEX IF NOT EXISTS idx_mentions_role ON entity_mentions(entity_id, role) WHERE entity_id IS NOT NULL;

CREATE TABLE IF NOT EXISTS entity_cooccurrences (
    entity_a_id text NOT NULL REFERENCES entities(id) ON DELETE CASCADE,
    entity_b_id text NOT NULL REFERENCES entities(id) ON DELETE CASCADE,
    episode_count bigint NOT NULL,
    last_seen_at timestamptz NOT NULL,
    PRIMARY KEY (entity_a_id, entity_b_id),
    CHECK (entity_a_id < entity_b_id)
);

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

CREATE TABLE IF NOT EXISTS resolution_blacklist (
    id bigint GENERATED ALWAYS AS IDENTITY PRIMARY KEY,
    surface_form text NOT NULL,
    wrong_qid text NOT NULL,
    reason text NULL,
    created_at timestamptz NOT NULL DEFAULT now(),
    UNIQUE(surface_form, wrong_qid)
);

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


def ensure_schema(dsn: str, *, embedding_dim: int = DEFAULT_EMBEDDING_DIM) -> None:
    """Idempotently create the full typed schema on ``dsn``.

    Single bootstrap call used by the repository factory and tests; replaced
    by alembic migrations once those land. ``embedding_dim`` sizes the
    pgvector columns (must match the configured embedding model).
    """
    import psycopg

    with psycopg.connect(dsn) as conn:
        conn.execute(SCHEMA_SQL.format(dim=embedding_dim))
    logger.info("postgres_schema_ensured", embedding_dim=embedding_dim)
