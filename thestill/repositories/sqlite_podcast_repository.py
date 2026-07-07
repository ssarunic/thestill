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

"""
SQLite implementation of podcast repository.

Design principles:
- Raw SQL with parameter binding (no ORM)
- Connection pooling (one connection per thread)
- Transaction support via context manager
- Pydantic models for type safety
- All side effects (timestamps, cascades) in service layer
- Cache-friendly: no database triggers or cascades
"""

import json
import sqlite3
import uuid
from contextlib import contextmanager
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Any, Dict, List, Optional, Sequence, Set, Tuple

from structlog import get_logger

from ..models.podcast import Episode, EpisodeState, FailureType, Podcast, TranscriptLink
from ..utils.datetime_utils import now_utc
from ..utils.podcast_categories import APPLE_GENRE_IDS, APPLE_PODCAST_TAXONOMY, normalize_category_name
from ..utils.slug import generate_slug
from .podcast_repository import EpisodeRepository, PodcastRepository

logger = get_logger(__name__)

# Float round-trip tolerance for SQLite REAL mtime comparison: ``stat().st_mtime``
# is float64 but SQLite REAL → Python float can drift below microsecond precision.
_MTIME_EPSILON = 1e-6

# Keep bulk ``id IN (…)`` writes under SQLite's host-parameter ceiling
# (``SQLITE_MAX_VARIABLE_NUMBER``; historically 999) so large batch clears land
# in a handful of statements instead of one oversized — or rejected — one.
_SQL_PARAM_CHUNK = 900

# Deterministic UUID5 so the synthetic-audio-imports parent has a stable id
# across runs and machines without persisting it as configuration.
SYNTHETIC_AUDIO_IMPORTS_RSS = "synthetic://audio-imports"
SYNTHETIC_AUDIO_IMPORTS_ID = str(uuid.uuid5(uuid.NAMESPACE_URL, SYNTHETIC_AUDIO_IMPORTS_RSS))


def episode_from_row(row: sqlite3.Row, *, prefix: str = "") -> Episode:
    """
    Build an ``Episode`` from a SQLite row.

    ``prefix`` lets composed-JOIN queries (e.g. inbox list, where the SELECT
    aliases episode columns to ``ep_*`` to disambiguate from joined tables)
    reuse the same mapping logic without duplicating field-by-field plumbing.
    """

    def col(name: str):
        return row[f"{prefix}{name}"]

    def has(name: str) -> bool:
        return f"{prefix}{name}" in row.keys()

    failure_type = None
    if col("failure_type"):
        try:
            failure_type = FailureType(col("failure_type"))
        except ValueError:
            logger.warning(f"Unknown failure_type '{col('failure_type')}' for episode {col('id')}")

    explicit: Optional[bool] = None
    if col("explicit") is not None:
        explicit = col("explicit") == 1

    # ``clean_transcript_json_path`` and ``published_at`` are guarded with
    # ``has()`` because legacy databases predate those migrations; the row
    # may simply not carry the column.
    return Episode(
        id=col("id"),
        podcast_id=col("podcast_id"),
        created_at=datetime.fromisoformat(col("created_at")),
        updated_at=datetime.fromisoformat(col("updated_at")),
        external_id=col("external_id"),
        title=col("title"),
        slug=col("slug") or "",
        description=col("description"),
        description_html=col("description_html") or "",
        pub_date=datetime.fromisoformat(col("pub_date")) if col("pub_date") else None,
        audio_url=col("audio_url"),
        duration=col("duration"),
        image_url=col("image_url"),
        explicit=explicit,
        episode_type=col("episode_type"),
        episode_number=col("episode_number"),
        season_number=col("season_number"),
        website_url=col("website_url"),
        audio_file_size=col("audio_file_size"),
        audio_mime_type=col("audio_mime_type"),
        audio_path=col("audio_path"),
        downsampled_audio_path=col("downsampled_audio_path"),
        raw_transcript_path=col("raw_transcript_path"),
        clean_transcript_path=col("clean_transcript_path"),
        clean_transcript_json_path=(col("clean_transcript_json_path") if has("clean_transcript_json_path") else None),
        playback_time_offset_seconds=(
            col("playback_time_offset_seconds")
            if has("playback_time_offset_seconds") and col("playback_time_offset_seconds") is not None
            else 0.0
        ),
        summary_path=col("summary_path"),
        published_at=(
            datetime.fromisoformat(col("published_at")) if has("published_at") and col("published_at") else None
        ),
        failed_at_stage=col("failed_at_stage"),
        failure_reason=col("failure_reason"),
        failure_type=failure_type,
        failed_at=datetime.fromisoformat(col("failed_at")) if col("failed_at") else None,
    )


def _normalize_artwork_url(url: Optional[str]) -> Optional[str]:
    """Upgrade ``http://`` artwork URLs to ``https://`` before storage.

    The web UI's CSP is ``img-src 'self' data: https:`` — any stored
    ``http://`` URL is silently dropped by the browser. Every podcast/episode
    artwork CDN we've seen serves the same path over TLS, so an unconditional
    upgrade is safe and idempotent. Anything not ``http://`` is returned
    unchanged.
    """
    if url and url.startswith("http://"):
        return "https://" + url[len("http://") :]
    return url


def _row_opt_dt(row: sqlite3.Row, key: str) -> Optional[datetime]:
    """Parse an optional ISO-datetime column, tolerating SELECTs that omit it.

    ``_row_to_podcast_minimal`` and friends are fed by several different
    SELECTs; not all project every column. ``sqlite3.Row[missing]`` raises
    ``IndexError``, so guard the lookup and return ``None`` when the column
    isn't present (or is NULL) rather than forcing every query to carry it.
    """
    try:
        value = row[key]
    except (IndexError, KeyError):
        return None
    return datetime.fromisoformat(value) if value else None


class SqlitePodcastRepository(PodcastRepository, EpisodeRepository):
    """
    SQLite-based podcast repository.

    Thread-safety: Uses context manager for per-operation connections.
    Transactions: Explicit via transaction() context manager.
    Side effects: All handled in service layer (no triggers/cascades).
    """

    def __init__(self, db_path: str):
        """
        Initialize SQLite repository.

        Args:
            db_path: Path to SQLite database file (e.g., "./data/podcasts.db")
        """
        self.db_path = Path(db_path)
        # Category lookup caches: populated after migrations seed the table.
        # ``_cat_pair_to_id`` keys are normalized (lowercase, alphanumeric-only)
        # so RSS-derived strings with whitespace/casing differences still match.
        # Top-level rows are stored under ``(top_norm, None)``; subcategories
        # under ``(top_norm, sub_norm)`` — a single dict covers both lookups.
        self._cat_id_to_pair: Dict[int, Tuple[Optional[str], Optional[str]]] = {}
        self._cat_pair_to_id: Dict[Tuple[str, Optional[str]], int] = {}
        self._ensure_database_exists()
        logger.info(f"Initialized SQLite repository: {self.db_path}")

    def _ensure_database_exists(self):
        """Create database and schema if not exists."""
        self.db_path.parent.mkdir(parents=True, exist_ok=True)

        with self._get_connection() as conn:
            # Enable foreign keys (disabled by default in SQLite)
            conn.execute("PRAGMA foreign_keys = ON")

            # Performance optimizations
            conn.execute("PRAGMA journal_mode = WAL")  # Write-Ahead Logging
            conn.execute("PRAGMA synchronous = NORMAL")  # Balance speed/safety
            conn.execute("PRAGMA cache_size = -64000")  # 64MB cache
            conn.execute("PRAGMA temp_store = MEMORY")  # Temp tables in RAM

            # Create schema (idempotent)
            self._create_schema(conn)

            # Run migrations for existing databases
            self._run_migrations(conn)

            # Load the category cache after migrations have seeded the table.
            self._load_categories_cache(conn)

            # Sync the top_podcasts tables from data/top_podcasts_<region>.json
            # (no-op when each region's source file is unchanged since last init).
            self._seed_top_podcasts(conn)

            logger.debug("Database schema initialized")

    def _run_migrations(self, conn: sqlite3.Connection):
        """Run schema migrations for existing databases."""
        # Check if image_url column exists in podcasts table
        cursor = conn.execute("PRAGMA table_info(podcasts)")
        podcast_columns = {row["name"] for row in cursor.fetchall()}

        if "image_url" not in podcast_columns:
            logger.info("Migrating database: adding image_url column to podcasts table")
            conn.execute("ALTER TABLE podcasts ADD COLUMN image_url TEXT NULL")
            logger.info("Migration complete: image_url column added to podcasts")

        # Check if image_url column exists in episodes table
        cursor = conn.execute("PRAGMA table_info(episodes)")
        episode_columns = {row["name"] for row in cursor.fetchall()}

        if "image_url" not in episode_columns:
            logger.info("Migrating database: adding image_url column to episodes table")
            conn.execute("ALTER TABLE episodes ADD COLUMN image_url TEXT NULL")
            logger.info("Migration complete: image_url column added to episodes")

        # Migration: Add failure tracking columns (idempotent)
        if "failed_at_stage" not in episode_columns:
            logger.info("Migrating database: adding failure tracking columns to episodes table")
            conn.execute("ALTER TABLE episodes ADD COLUMN failed_at_stage TEXT NULL")
            conn.execute("ALTER TABLE episodes ADD COLUMN failure_reason TEXT NULL")
            conn.execute("ALTER TABLE episodes ADD COLUMN failure_type TEXT NULL")
            conn.execute("ALTER TABLE episodes ADD COLUMN failed_at TIMESTAMP NULL")
            logger.info("Migration complete: failure tracking columns added to episodes")

        # Migration: Add language column to podcasts (idempotent)
        if "language" not in podcast_columns:
            logger.info("Migrating database: adding language column to podcasts table")
            conn.execute("ALTER TABLE podcasts ADD COLUMN language TEXT NOT NULL DEFAULT 'en'")
            logger.info("Migration complete: language column added to podcasts")

        # Migration: split the overloaded ``last_processed`` field (idempotent).
        #
        # ``last_processed`` was written with two incompatible meanings: the
        # incremental-refresh discovery watermark (newest episode pub_date) AND
        # a wall-clock "we just processed an episode" timestamp (set by
        # ``mark_episode_processed``). The wall-clock writes pushed the watermark
        # ahead of every real episode, so the ``episode_date > last_processed``
        # discovery gate silently skipped newly-published episodes whose pub_date
        # fell before the processing time. Split them:
        #   - ``last_processed``    -> discovery watermark only (repaired below)
        #   - ``last_processed_at`` -> wall-clock processing time (new column)
        if "last_processed_at" not in podcast_columns:
            logger.info("Migrating database: splitting last_processed into watermark + last_processed_at")
            conn.execute("ALTER TABLE podcasts ADD COLUMN last_processed_at TIMESTAMP NULL")
            # The repair only applies to real DBs that already carry the
            # ``last_processed`` watermark (guard for legacy/minimal fixtures
            # whose podcasts table predates it).
            if "last_processed" in podcast_columns:
                # Preserve the historical wall-clock value for display.
                conn.execute("UPDATE podcasts SET last_processed_at = last_processed")
                # Repair the watermark: reset to the newest real episode pub_date
                # so any episode published before a past processing run is
                # discoverable again. NULL for podcasts with no episodes.
                conn.execute(
                    "UPDATE podcasts SET last_processed = "
                    "(SELECT MAX(e.pub_date) FROM episodes e WHERE e.podcast_id = podcasts.id)"
                )
            # Clear stale conditional-GET validators so the next refresh does a
            # full 200 + parse and re-evaluates every feed against the repaired
            # watermark, self-healing any episode missed while the bug was live.
            if {"etag", "last_modified"} <= podcast_columns:
                conn.execute("UPDATE podcasts SET etag = NULL, last_modified = NULL")
            podcast_columns.add("last_processed_at")
            logger.info("Migration complete: last_processed split; watermarks repaired; feed caches cleared")

        # Migration: Add description_html column to episodes (idempotent)
        if "description_html" not in episode_columns:
            logger.info("Migrating database: adding description_html column to episodes table")
            conn.execute("ALTER TABLE episodes ADD COLUMN description_html TEXT NOT NULL DEFAULT ''")
            logger.info("Migration complete: description_html column added to episodes")

        # Migration: legacy free-text category columns. Older databases may
        # still have these from before the categories-normalization change.
        # We add them on first ever creation only if NEITHER the FK columns
        # NOR the legacy columns exist (truly fresh DBs go through
        # _create_schema and already have the FK columns; this block exists
        # solely to keep databases that were created mid-history bootable
        # so the normalization migration below can backfill from them).
        if "primary_category" not in podcast_columns and "primary_category_id" not in podcast_columns:
            logger.info("Migrating database: adding legacy category columns (will be normalized below)")
            conn.execute("ALTER TABLE podcasts ADD COLUMN primary_category TEXT NULL")
            conn.execute("ALTER TABLE podcasts ADD COLUMN primary_subcategory TEXT NULL")
            conn.execute("ALTER TABLE podcasts ADD COLUMN secondary_category TEXT NULL")
            conn.execute("ALTER TABLE podcasts ADD COLUMN secondary_subcategory TEXT NULL")
            podcast_columns |= {
                "primary_category",
                "primary_subcategory",
                "secondary_category",
                "secondary_subcategory",
            }

        # Spec #20 Migration: normalize categories into a lookup table.
        # 1. Create categories table (top-level + subcategories) if missing.
        # 2. Seed it from data/podcast_categories.json (Apple's official taxonomy).
        # 3. Add primary_category_id / secondary_category_id FK columns to podcasts.
        # 4. Backfill FKs from the legacy free-text columns (best-effort match).
        # 5. Drop the four legacy free-text columns and their indexes.
        cursor = conn.execute("SELECT name FROM sqlite_master WHERE type='table' AND name='categories'")
        if cursor.fetchone() is None:
            logger.info("Migrating database: creating categories table")
            conn.executescript(
                """
                CREATE TABLE IF NOT EXISTS categories (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    name TEXT NOT NULL,
                    slug TEXT NOT NULL,
                    parent_id INTEGER NULL,
                    apple_genre_id INTEGER NULL,
                    FOREIGN KEY (parent_id) REFERENCES categories(id) ON DELETE CASCADE
                );
                CREATE INDEX IF NOT EXISTS idx_categories_parent ON categories(parent_id);
                CREATE UNIQUE INDEX IF NOT EXISTS idx_categories_top_unique
                    ON categories(name) WHERE parent_id IS NULL;
                CREATE UNIQUE INDEX IF NOT EXISTS idx_categories_sub_unique
                    ON categories(parent_id, name) WHERE parent_id IS NOT NULL;
                """
            )

        # Seed categories table if it's empty.
        cursor = conn.execute("SELECT COUNT(*) AS n FROM categories")
        if cursor.fetchone()["n"] == 0:
            logger.info("Seeding categories table from Apple Podcasts taxonomy")
            self._seed_categories(conn)

        # Load the cache now (before backfill below) so the backfill can reuse
        # the runtime resolver instead of re-implementing it. Re-loaded again at
        # the end of _ensure_database_exists in case migrations after this
        # point change anything (currently they don't, but the call is cheap).
        self._load_categories_cache(conn)

        # Refresh column info (may have changed above).
        cursor = conn.execute("PRAGMA table_info(podcasts)")
        podcast_columns = {row["name"] for row in cursor.fetchall()}

        if "primary_category_id" not in podcast_columns:
            logger.info("Migrating database: adding category FK columns to podcasts")
            conn.execute(
                "ALTER TABLE podcasts ADD COLUMN primary_category_id INTEGER NULL "
                "REFERENCES categories(id) ON DELETE SET NULL"
            )
            conn.execute(
                "ALTER TABLE podcasts ADD COLUMN secondary_category_id INTEGER NULL "
                "REFERENCES categories(id) ON DELETE SET NULL"
            )
            podcast_columns |= {"primary_category_id", "secondary_category_id"}

            # Backfill FKs from the legacy free-text columns if those still exist.
            if "primary_category" in podcast_columns:
                self._backfill_category_fks(conn)
        elif "primary_category" in podcast_columns:
            # Crash-recovery: FK columns + legacy columns coexisting means a
            # previous migration died between ADD COLUMN and backfill. Without
            # this branch the legacy columns get dropped below with FKs still NULL.
            logger.info("Resuming interrupted migration: backfilling category FKs from legacy columns")
            self._backfill_category_fks(conn)

        # FK indexes (partial, since most podcasts may have NULL secondary).
        conn.execute(
            "CREATE INDEX IF NOT EXISTS idx_podcasts_primary_category_id "
            "ON podcasts(primary_category_id) WHERE primary_category_id IS NOT NULL"
        )
        conn.execute(
            "CREATE INDEX IF NOT EXISTS idx_podcasts_secondary_category_id "
            "ON podcasts(secondary_category_id) WHERE secondary_category_id IS NOT NULL"
        )

        # Drop the four legacy free-text columns and their indexes (SQLite >=3.35).
        if "primary_category" in podcast_columns:
            logger.info("Migrating database: dropping legacy free-text category columns")
            conn.execute("DROP INDEX IF EXISTS idx_podcasts_primary_category")
            conn.execute("DROP INDEX IF EXISTS idx_podcasts_secondary_category")
            conn.execute("ALTER TABLE podcasts DROP COLUMN primary_category")
            conn.execute("ALTER TABLE podcasts DROP COLUMN primary_subcategory")
            conn.execute("ALTER TABLE podcasts DROP COLUMN secondary_category")
            conn.execute("ALTER TABLE podcasts DROP COLUMN secondary_subcategory")
            logger.info("Migration complete: categories normalized")

        # Migration: Create podcast_followers table if it doesn't exist (idempotent)
        cursor = conn.execute("SELECT name FROM sqlite_master WHERE type='table' AND name='podcast_followers'")
        if cursor.fetchone() is None:
            logger.info("Migrating database: creating podcast_followers table")
            conn.executescript(
                """
                CREATE TABLE IF NOT EXISTS podcast_followers (
                    id TEXT PRIMARY KEY NOT NULL,
                    user_id TEXT NOT NULL,
                    podcast_id TEXT NOT NULL,
                    created_at TIMESTAMP NOT NULL DEFAULT CURRENT_TIMESTAMP,
                    FOREIGN KEY (user_id) REFERENCES users(id) ON DELETE CASCADE,
                    FOREIGN KEY (podcast_id) REFERENCES podcasts(id) ON DELETE CASCADE,
                    UNIQUE(user_id, podcast_id),
                    CHECK (length(id) = 36)
                );

                CREATE INDEX IF NOT EXISTS idx_podcast_followers_user
                    ON podcast_followers(user_id);

                CREATE INDEX IF NOT EXISTS idx_podcast_followers_podcast
                    ON podcast_followers(podcast_id);
                """
            )
            logger.info("Migration complete: podcast_followers table created")

        # Refresh column info after previous migrations
        cursor = conn.execute("PRAGMA table_info(podcasts)")
        podcast_columns = {row["name"] for row in cursor.fetchall()}
        cursor = conn.execute("PRAGMA table_info(episodes)")
        episode_columns = {row["name"] for row in cursor.fetchall()}

        # THES-142 Migration: Add RSS parser enhancement columns to podcasts (idempotent)
        if "author" not in podcast_columns:
            logger.info("Migrating database: adding THES-142 columns to podcasts table")
            # THES-143: Essential metadata
            conn.execute("ALTER TABLE podcasts ADD COLUMN author TEXT NULL")
            conn.execute("ALTER TABLE podcasts ADD COLUMN explicit INTEGER NULL")
            # THES-144: Show organization
            conn.execute("ALTER TABLE podcasts ADD COLUMN show_type TEXT NULL")
            conn.execute("ALTER TABLE podcasts ADD COLUMN website_url TEXT NULL")
            # THES-145: Feed management
            conn.execute("ALTER TABLE podcasts ADD COLUMN is_complete INTEGER NOT NULL DEFAULT 0")
            conn.execute("ALTER TABLE podcasts ADD COLUMN copyright TEXT NULL")
            logger.info("Migration complete: THES-142 columns added to podcasts")

        # THES-142 Migration: Add RSS parser enhancement columns to episodes (idempotent)
        if "explicit" not in episode_columns:
            logger.info("Migrating database: adding THES-142 columns to episodes table")
            # THES-143: Essential metadata
            conn.execute("ALTER TABLE episodes ADD COLUMN explicit INTEGER NULL")
            conn.execute("ALTER TABLE episodes ADD COLUMN episode_type TEXT NULL")
            # THES-144: Episode organization
            conn.execute("ALTER TABLE episodes ADD COLUMN episode_number INTEGER NULL")
            conn.execute("ALTER TABLE episodes ADD COLUMN season_number INTEGER NULL")
            conn.execute("ALTER TABLE episodes ADD COLUMN website_url TEXT NULL")
            # THES-145: Enclosure metadata
            conn.execute("ALTER TABLE episodes ADD COLUMN audio_file_size INTEGER NULL")
            conn.execute("ALTER TABLE episodes ADD COLUMN audio_mime_type TEXT NULL")
            logger.info("Migration complete: THES-142 columns added to episodes")

        # spec #18 Migration: segmented cleanup sidecar + playback offset.
        # Refresh the column set in case earlier migrations added columns.
        cursor = conn.execute("PRAGMA table_info(episodes)")
        episode_columns = {row["name"] for row in cursor.fetchall()}
        if "clean_transcript_json_path" not in episode_columns:
            logger.info("Migrating database: adding spec #18 columns to episodes table")
            conn.execute("ALTER TABLE episodes ADD COLUMN clean_transcript_json_path TEXT NULL")
            conn.execute("ALTER TABLE episodes ADD COLUMN playback_time_offset_seconds REAL NOT NULL DEFAULT 0.0")
            logger.info("Migration complete: spec #18 columns added to episodes")

        # spec #19 Migration: HTTP conditional-GET cache columns on podcasts.
        cursor = conn.execute("PRAGMA table_info(podcasts)")
        podcast_columns = {row["name"] for row in cursor.fetchall()}
        if "etag" not in podcast_columns:
            logger.info("Migrating database: adding spec #19 conditional-GET columns to podcasts table")
            conn.execute("ALTER TABLE podcasts ADD COLUMN etag TEXT NULL")
            conn.execute("ALTER TABLE podcasts ADD COLUMN last_modified TEXT NULL")
            logger.info("Migration complete: spec #19 conditional-GET columns added to podcasts")

        # Migration: Create revoked_tokens table for JWT revocation
        # deny-list (spec #25 item 4.2, idempotent).
        cursor = conn.execute("SELECT name FROM sqlite_master WHERE type='table' AND name='revoked_tokens'")
        if cursor.fetchone() is None:
            logger.info("Migrating database: creating revoked_tokens table")
            conn.executescript(
                """
                CREATE TABLE IF NOT EXISTS revoked_tokens (
                    jti TEXT PRIMARY KEY NOT NULL,
                    expires_at TIMESTAMP NOT NULL,
                    revoked_at TIMESTAMP NOT NULL DEFAULT CURRENT_TIMESTAMP
                );
                CREATE INDEX IF NOT EXISTS idx_revoked_tokens_expires_at
                    ON revoked_tokens(expires_at);
                """
            )
            logger.info("Migration complete: revoked_tokens table created")

        # Migration: Add region columns to users table (idempotent).
        # `region` is an ISO 3166-1 alpha-2 country code (lowercase) or NULL.
        # `region_locked` is 1 once the user has explicitly chosen one — used
        # to suppress further IP-based inference on subsequent logins.
        cursor = conn.execute("SELECT name FROM sqlite_master WHERE type='table' AND name='users'")
        if cursor.fetchone() is not None:
            cursor = conn.execute("PRAGMA table_info(users)")
            user_columns = {row["name"] for row in cursor.fetchall()}
            if "region" not in user_columns:
                logger.info("Migrating database: adding region columns to users table")
                conn.execute("ALTER TABLE users ADD COLUMN region TEXT NULL")
                conn.execute("ALTER TABLE users ADD COLUMN region_locked INTEGER NOT NULL DEFAULT 0")
                logger.info("Migration complete: region columns added to users")

            # Migration: Add is_admin column to users table (idempotent).
            # Gates the operator-only pipeline views (task queue + DLQ). Off by
            # default; flipped manually in multi-user mode. The single-user
            # default user is granted admin at creation time (auth_service).
            if "is_admin" not in user_columns:
                logger.info("Migrating database: adding is_admin column to users table")
                conn.execute("ALTER TABLE users ADD COLUMN is_admin INTEGER NOT NULL DEFAULT 0")
                logger.info("Migration complete: is_admin column added to users")

        # Digest retirement: the legacy global digests tables are dropped.
        # Briefings (spec #36/#50) are the only consumer-facing concept;
        # historical digest markdown under data/digests/ stays on disk.
        cursor = conn.execute("SELECT name FROM sqlite_master WHERE type='table' AND name='digests'")
        if cursor.fetchone() is not None:
            logger.info("Migrating database: dropping retired digests tables")
            conn.executescript(
                """
                DROP TABLE IF EXISTS digest_episodes;
                DROP TABLE IF EXISTS digests;
                """
            )
            logger.info("Migration complete: digests tables dropped")

        # Spec #21 Migration: top_podcasts + rankings + meta tables (idempotent).
        cursor = conn.execute("SELECT name FROM sqlite_master WHERE type='table' AND name='top_podcasts'")
        if cursor.fetchone() is None:
            logger.info("Migrating database: creating top_podcasts tables")
            conn.executescript(
                """
                CREATE TABLE IF NOT EXISTS top_podcasts (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    name TEXT NOT NULL,
                    artist TEXT NULL,
                    rss_url TEXT NOT NULL UNIQUE,
                    apple_url TEXT NULL,
                    youtube_url TEXT NULL,
                    apple_track_id TEXT NULL,
                    category_id INTEGER NULL REFERENCES categories(id) ON DELETE SET NULL,
                    first_seen_at TIMESTAMP NOT NULL,
                    last_seen_at TIMESTAMP NOT NULL
                );
                CREATE INDEX IF NOT EXISTS idx_top_podcasts_category
                    ON top_podcasts(category_id) WHERE category_id IS NOT NULL;

                CREATE TABLE IF NOT EXISTS top_podcast_rankings (
                    top_podcast_id INTEGER NOT NULL,
                    region TEXT NOT NULL,
                    rank INTEGER NOT NULL,
                    source_genre TEXT NULL,
                    scraped_at TIMESTAMP NOT NULL,
                    PRIMARY KEY (region, top_podcast_id),
                    FOREIGN KEY (top_podcast_id) REFERENCES top_podcasts(id) ON DELETE CASCADE
                );
                CREATE INDEX IF NOT EXISTS idx_top_podcast_rankings_region_rank
                    ON top_podcast_rankings(region, rank);
                CREATE INDEX IF NOT EXISTS idx_top_podcast_rankings_podcast
                    ON top_podcast_rankings(top_podcast_id);

                CREATE TABLE IF NOT EXISTS top_podcasts_meta (
                    region TEXT PRIMARY KEY NOT NULL,
                    source_path TEXT NOT NULL,
                    source_mtime REAL NOT NULL,
                    row_count INTEGER NOT NULL,
                    seeded_at TIMESTAMP NOT NULL
                );
                """
            )
            logger.info("Migration complete: top_podcasts tables created")

        # spec #28 Migration: entity-layer schema (idempotent).
        # Adds:
        # - ``episodes.entity_extraction_status`` (per-episode status of
        #   the entity branch — independent of the user-facing pipeline).
        # - ``entities`` / ``entity_mentions`` / ``entity_cooccurrences``
        #   tables. ``entity_mentions.entity_id`` is nullable on purpose —
        #   ``extract-entities`` writes rows with ``resolution_status='pending'``
        #   and a ``NULL`` FK, then ``resolve-entities`` fills it in.
        # ``episode_columns`` was repopulated by the spec #18 block above;
        # nothing between there and here adds ``entity_extraction_status``,
        # so the set is fresh enough for the membership check.
        if "entity_extraction_status" not in episode_columns:
            logger.info("Migrating database: adding spec #28 entity_extraction_status column to episodes")
            conn.execute("ALTER TABLE episodes ADD COLUMN entity_extraction_status TEXT NULL")
            logger.info("Migration complete: entity_extraction_status added to episodes")

        cursor = conn.execute("SELECT name FROM sqlite_master WHERE type='table' AND name='entities'")
        if cursor.fetchone() is None:
            logger.info("Migrating database: creating spec #28 entity tables")
            conn.executescript(
                """
                CREATE TABLE IF NOT EXISTS entities (
                    id              TEXT PRIMARY KEY NOT NULL,
                    type            TEXT NOT NULL,
                    canonical_name  TEXT NOT NULL,
                    wikidata_qid    TEXT NULL,
                    aliases         TEXT NOT NULL DEFAULT '[]',
                    description     TEXT NULL,
                    wikidata_instance_of TEXT NOT NULL DEFAULT '[]',
                    created_at      TIMESTAMP NOT NULL DEFAULT CURRENT_TIMESTAMP,
                    updated_at      TIMESTAMP NOT NULL DEFAULT CURRENT_TIMESTAMP,
                    CHECK (type IN ('person','company','product','topic'))
                );
                CREATE INDEX IF NOT EXISTS idx_entities_type ON entities(type);
                CREATE INDEX IF NOT EXISTS idx_entities_wikidata
                    ON entities(wikidata_qid) WHERE wikidata_qid IS NOT NULL;

                CREATE TABLE IF NOT EXISTS entity_mentions (
                    id                  INTEGER PRIMARY KEY AUTOINCREMENT,
                    entity_id           TEXT NULL REFERENCES entities(id) ON DELETE CASCADE,
                    resolution_status   TEXT NOT NULL DEFAULT 'pending',
                    episode_id          TEXT NOT NULL REFERENCES episodes(id) ON DELETE CASCADE,
                    segment_id          INTEGER NOT NULL,
                    start_ms            INTEGER NOT NULL,
                    end_ms              INTEGER NOT NULL,
                    speaker             TEXT NULL,
                    role                TEXT NULL,
                    surface_form        TEXT NOT NULL,
                    surface_label       TEXT NULL,
                    quote_excerpt       TEXT NOT NULL,
                    sentiment           REAL NULL,
                    confidence          REAL NOT NULL,
                    extractor           TEXT NOT NULL,
                    created_at          TIMESTAMP NOT NULL DEFAULT CURRENT_TIMESTAMP,
                    resolved_at         TIMESTAMP NULL,
                    CHECK (resolution_status IN ('pending','resolved','unresolvable')),
                    CHECK (role IS NULL OR role IN ('host','guest','mentioned','self'))
                );
                CREATE INDEX IF NOT EXISTS idx_mentions_entity
                    ON entity_mentions(entity_id, episode_id) WHERE entity_id IS NOT NULL;
                CREATE INDEX IF NOT EXISTS idx_mentions_episode
                    ON entity_mentions(episode_id);
                CREATE INDEX IF NOT EXISTS idx_mentions_role
                    ON entity_mentions(entity_id, role) WHERE entity_id IS NOT NULL;
                CREATE INDEX IF NOT EXISTS idx_mentions_pending
                    ON entity_mentions(resolution_status) WHERE resolution_status = 'pending';

                CREATE TABLE IF NOT EXISTS entity_cooccurrences (
                    entity_a_id     TEXT NOT NULL REFERENCES entities(id) ON DELETE CASCADE,
                    entity_b_id     TEXT NOT NULL REFERENCES entities(id) ON DELETE CASCADE,
                    episode_count   INTEGER NOT NULL,
                    last_seen_at    TIMESTAMP NOT NULL,
                    PRIMARY KEY (entity_a_id, entity_b_id),
                    CHECK (entity_a_id < entity_b_id)
                );
                """
            )
            logger.info("Migration complete: spec #28 entity tables created")

        # spec #28 §1.5 — record the GLiNER-emitted label
        # (``person``/``company``/``product``/``topic``) on each mention so
        # the resolver can map to ``EntityType`` without re-running the
        # extractor. ReFinED's ``coarse_type`` is the fallback when this
        # column is NULL (legacy rows from before the migration).
        cursor = conn.execute("SELECT name FROM sqlite_master WHERE type='table' AND name='entity_mentions'")
        if cursor.fetchone() is not None:
            cursor = conn.execute("PRAGMA table_info(entity_mentions)")
            mention_columns = {row["name"] for row in cursor.fetchall()}
            if "surface_label" not in mention_columns:
                logger.info("Migrating database: adding surface_label to entity_mentions")
                conn.execute("ALTER TABLE entity_mentions ADD COLUMN surface_label TEXT NULL")
                logger.info("Migration complete: surface_label added to entity_mentions")

            # spec #28 §1.13.6 — resolution_method + candidate_entity_ids
            if "resolution_method" not in mention_columns:
                logger.info("Migrating database: adding resolution_method to entity_mentions")
                conn.execute("ALTER TABLE entity_mentions ADD COLUMN resolution_method TEXT NULL")
            if "candidate_entity_ids" not in mention_columns:
                logger.info("Migrating database: adding candidate_entity_ids to entity_mentions")
                conn.execute("ALTER TABLE entity_mentions ADD COLUMN candidate_entity_ids TEXT NULL")

        # spec #28 §1.13 — relax the entity_mentions CHECK constraints so
        # ``resolution_status`` can be one of {pending,resolved,unresolvable,
        # ambiguous,dropped} and ``role`` can include ``speaking``. SQLite
        # cannot ALTER an existing CHECK; we rebuild the table once when
        # we detect the legacy CHECK still in place. Idempotent — the
        # ``sql`` column on sqlite_master tells us whether ambiguous is
        # already allowed.
        legacy_check = conn.execute(
            "SELECT sql FROM sqlite_master WHERE type='table' AND name='entity_mentions'"
        ).fetchone()
        if legacy_check is not None and "'ambiguous'" not in (legacy_check["sql"] or ""):
            logger.info("Migrating database: rebuilding entity_mentions with relaxed CHECKs (spec #28 §1.13)")
            conn.execute("PRAGMA foreign_keys = OFF")
            try:
                conn.executescript(
                    """
                    CREATE TABLE entity_mentions_new (
                        id                    INTEGER PRIMARY KEY AUTOINCREMENT,
                        entity_id             TEXT NULL REFERENCES entities(id) ON DELETE CASCADE,
                        resolution_status     TEXT NOT NULL DEFAULT 'pending',
                        episode_id            TEXT NOT NULL REFERENCES episodes(id) ON DELETE CASCADE,
                        segment_id            INTEGER NOT NULL,
                        start_ms              INTEGER NOT NULL,
                        end_ms                INTEGER NOT NULL,
                        speaker               TEXT NULL,
                        role                  TEXT NULL,
                        surface_form          TEXT NOT NULL,
                        surface_label         TEXT NULL,
                        quote_excerpt         TEXT NOT NULL,
                        sentiment             REAL NULL,
                        confidence            REAL NOT NULL,
                        extractor             TEXT NOT NULL,
                        resolution_method     TEXT NULL,
                        candidate_entity_ids  TEXT NULL,
                        created_at            TIMESTAMP NOT NULL DEFAULT CURRENT_TIMESTAMP,
                        resolved_at           TIMESTAMP NULL,
                        CHECK (resolution_status IN ('pending','resolved','unresolvable','ambiguous','dropped')),
                        CHECK (role IS NULL OR role IN ('host','guest','mentioned','self','speaking'))
                    );
                    INSERT INTO entity_mentions_new (
                        id, entity_id, resolution_status, episode_id, segment_id,
                        start_ms, end_ms, speaker, role, surface_form, surface_label,
                        quote_excerpt, sentiment, confidence, extractor,
                        resolution_method, candidate_entity_ids,
                        created_at, resolved_at
                    )
                    SELECT
                        id, entity_id, resolution_status, episode_id, segment_id,
                        start_ms, end_ms, speaker, role, surface_form, surface_label,
                        quote_excerpt, sentiment, confidence, extractor,
                        resolution_method, candidate_entity_ids,
                        created_at, resolved_at
                    FROM entity_mentions;
                    DROP TABLE entity_mentions;
                    ALTER TABLE entity_mentions_new RENAME TO entity_mentions;
                    CREATE INDEX IF NOT EXISTS idx_mentions_entity
                        ON entity_mentions(entity_id, episode_id) WHERE entity_id IS NOT NULL;
                    CREATE INDEX IF NOT EXISTS idx_mentions_episode
                        ON entity_mentions(episode_id);
                    CREATE INDEX IF NOT EXISTS idx_mentions_role
                        ON entity_mentions(entity_id, role) WHERE entity_id IS NOT NULL;
                    CREATE INDEX IF NOT EXISTS idx_mentions_pending
                        ON entity_mentions(resolution_status) WHERE resolution_status = 'pending';
                    """
                )
                logger.info("Migration complete: entity_mentions CHECKs relaxed")
            finally:
                conn.execute("PRAGMA foreign_keys = ON")

        # spec #28 §1.13.1 — host/guest/recurring entity ids on
        # podcasts and episodes. Stored as JSON arrays for symmetry
        # with ``entities.aliases`` and to keep the listing logic
        # simple — no junction table for what is fundamentally a
        # short, episode-scoped tag list.
        cursor = conn.execute("PRAGMA table_info(podcasts)")
        podcast_columns_now = {row["name"] for row in cursor.fetchall()}
        if "host_entity_ids" not in podcast_columns_now:
            logger.info("Migrating database: adding host_entity_ids to podcasts")
            conn.execute("ALTER TABLE podcasts ADD COLUMN host_entity_ids TEXT NOT NULL DEFAULT '[]'")
        if "recurring_entity_ids" not in podcast_columns_now:
            logger.info("Migrating database: adding recurring_entity_ids to podcasts")
            conn.execute("ALTER TABLE podcasts ADD COLUMN recurring_entity_ids TEXT NOT NULL DEFAULT '[]'")

        cursor = conn.execute("PRAGMA table_info(episodes)")
        episode_columns_now = {row["name"] for row in cursor.fetchall()}
        if "guest_entity_ids" not in episode_columns_now:
            logger.info("Migrating database: adding guest_entity_ids to episodes")
            conn.execute("ALTER TABLE episodes ADD COLUMN guest_entity_ids TEXT NOT NULL DEFAULT '[]'")

        # spec #28 §5.2 — cached Wikidata ``instance of`` (P31) QIDs for
        # bucket gating. JSON list of QID strings (e.g. ``["Q5"]`` for a
        # human, ``["Q6256"]`` for a country). Empty/NULL means "not
        # fetched yet"; the resolver fills it on first encounter and
        # ``thestill backfill-entity-types`` updates existing rows.
        cursor = conn.execute("SELECT name FROM sqlite_master WHERE type='table' AND name='entities'")
        if cursor.fetchone() is not None:
            cursor = conn.execute("PRAGMA table_info(entities)")
            entity_columns_now = {row["name"] for row in cursor.fetchall()}
            if "wikidata_instance_of" not in entity_columns_now:
                logger.info("Migrating database: adding wikidata_instance_of to entities")
                conn.execute("ALTER TABLE entities ADD COLUMN wikidata_instance_of TEXT NOT NULL DEFAULT '[]'")

        # spec #28 §1.13.7 — mention_overrides + resolution_blacklist tables.
        # The override layer is what guarantees human corrections survive
        # reindex: the resolver consults these tables BEFORE persisting.
        cursor = conn.execute("SELECT name FROM sqlite_master WHERE type='table' AND name='mention_overrides'")
        if cursor.fetchone() is None:
            logger.info("Migrating database: creating mention_overrides table")
            conn.executescript(
                """
                CREATE TABLE mention_overrides (
                    id            INTEGER PRIMARY KEY AUTOINCREMENT,
                    surface_form  TEXT NOT NULL,
                    episode_id    TEXT NULL REFERENCES episodes(id) ON DELETE CASCADE,
                    override_kind TEXT NOT NULL,
                    entity_id     TEXT NULL REFERENCES entities(id) ON DELETE SET NULL,
                    reason        TEXT NULL,
                    created_by    TEXT NULL,
                    created_at    TIMESTAMP NOT NULL
                                  DEFAULT (strftime('%Y-%m-%dT%H:%M:%f+00:00','now')),
                    CHECK (override_kind IN ('drop','force_entity','force_unresolvable'))
                );
                CREATE INDEX idx_overrides_surface_episode
                    ON mention_overrides(LOWER(surface_form), episode_id);
                """
            )
            logger.info("Migration complete: mention_overrides created")

        cursor = conn.execute("SELECT name FROM sqlite_master WHERE type='table' AND name='resolution_blacklist'")
        if cursor.fetchone() is None:
            logger.info("Migrating database: creating resolution_blacklist table")
            conn.executescript(
                """
                CREATE TABLE resolution_blacklist (
                    id            INTEGER PRIMARY KEY AUTOINCREMENT,
                    surface_form  TEXT NOT NULL,
                    wrong_qid     TEXT NOT NULL,
                    reason        TEXT NULL,
                    created_at    TIMESTAMP NOT NULL
                                  DEFAULT (strftime('%Y-%m-%dT%H:%M:%f+00:00','now')),
                    UNIQUE(surface_form, wrong_qid)
                );
                CREATE INDEX idx_blacklist_lookup
                    ON resolution_blacklist(LOWER(surface_form), wrong_qid);
                """
            )
            logger.info("Migration complete: resolution_blacklist created")

        # spec #45 — entity_enrichment: Tier-0 display data (photo/logo,
        # vital stats, Wikipedia lead, cross-links) fetched from Wikidata
        # + Wikipedia, keyed 1:1 by entity_id. Kept in its own table (not
        # on ``entities``) so it survives an entity reindex — the reindex
        # only wipes per-episode mentions, never this. Per-source status +
        # ``retry_after`` keep a transient outage from being cached as
        # "no data" (spec #42 FM-1).
        cursor = conn.execute("SELECT name FROM sqlite_master WHERE type='table' AND name='entity_enrichment'")
        if cursor.fetchone() is None:
            logger.info("Migrating database: creating entity_enrichment table")
            conn.executescript(
                """
                CREATE TABLE entity_enrichment (
                    entity_id            TEXT PRIMARY KEY NOT NULL
                                         REFERENCES entities(id) ON DELETE CASCADE,
                    image_url            TEXT NULL,
                    image_attribution    TEXT NULL,
                    image_license        TEXT NULL,
                    headline             TEXT NULL,
                    wikipedia_extract    TEXT NULL,
                    wikipedia_url        TEXT NULL,
                    facts_json           TEXT NOT NULL DEFAULT '[]',
                    affiliations_json    TEXT NOT NULL DEFAULT '[]',
                    wikidata_status      TEXT NOT NULL DEFAULT 'pending',
                    wikidata_fetched_at  TIMESTAMP NULL,
                    wikipedia_status     TEXT NOT NULL DEFAULT 'pending',
                    wikipedia_fetched_at TIMESTAMP NULL,
                    retry_after          TIMESTAMP NULL,
                    schema_version       INTEGER NOT NULL DEFAULT 1,
                    created_at           TIMESTAMP NOT NULL
                                         DEFAULT (strftime('%Y-%m-%dT%H:%M:%f+00:00','now')),
                    updated_at           TIMESTAMP NOT NULL
                                         DEFAULT (strftime('%Y-%m-%dT%H:%M:%f+00:00','now')),
                    CHECK (wikidata_status IN ('pending','ok','empty','failed')),
                    CHECK (wikipedia_status IN ('pending','ok','empty','failed'))
                );
                CREATE INDEX idx_enrichment_status
                    ON entity_enrichment(wikidata_status, wikipedia_status);
                """
            )
            logger.info("Migration complete: entity_enrichment created")

        # spec #28 §2.10 — chunks + chunks_vec + chunks_fts enable
        # corpus semantic + lexical search via sqlite-vec virtual tables.
        # The guard checks ``chunks_vec`` (the last and extension-
        # dependent table) so a partial migration that crashed after
        # creating ``chunks`` but before ``chunks_vec`` still retries
        # on the next startup. Skipped entirely when the ``sqlite-vec``
        # extension isn't installed (the ``[entities]`` extra is missing).
        cursor = conn.execute("SELECT name FROM sqlite_master WHERE type='table' AND name='chunks_vec'")
        if cursor.fetchone() is None:
            from ..search.base import DEFAULT_EMBEDDING_MODEL, embedding_dim_for
            from ..utils.sqlite_ext import SqliteVecNotInstalledError, load_vec_extension

            try:
                load_vec_extension(conn)
            except SqliteVecNotInstalledError:
                logger.warning(
                    "sqlite_vec_unavailable_skipping_chunks_migration",
                    note="install thestill[entities] to enable corpus chunk search",
                )
            else:
                vec_dim = embedding_dim_for(DEFAULT_EMBEDDING_MODEL)
                logger.info(
                    "Migrating database: creating spec #28 §2.10 chunks tables",
                    embedding_model=DEFAULT_EMBEDDING_MODEL,
                    vec_dim=vec_dim,
                )
                conn.executescript(
                    f"""
                    CREATE TABLE IF NOT EXISTS chunks (
                        id              INTEGER PRIMARY KEY AUTOINCREMENT,
                        episode_id      TEXT NOT NULL REFERENCES episodes(id) ON DELETE CASCADE,
                        segment_id      INTEGER NOT NULL,
                        start_ms        INTEGER NOT NULL,
                        end_ms          INTEGER NOT NULL,
                        speaker         TEXT NULL,
                        text            TEXT NOT NULL,
                        embedding_model TEXT NOT NULL,
                        embedding       BLOB NOT NULL,
                        created_at      TIMESTAMP NOT NULL
                                        DEFAULT (strftime('%Y-%m-%dT%H:%M:%f+00:00','now')),
                        UNIQUE (episode_id, segment_id, embedding_model)
                    );
                    CREATE INDEX IF NOT EXISTS idx_chunks_episode ON chunks(episode_id);

                    CREATE VIRTUAL TABLE IF NOT EXISTS chunks_vec USING vec0(
                        embedding float[{vec_dim}]
                    );

                    -- Contentless FTS5: we manage rows via triggers and
                    -- strip the speaker prefix (``Sarah Paine: ...``)
                    -- before indexing. Without this, BM25 ranks short
                    -- interjections from the named speaker above
                    -- substantive content because length normalization
                    -- rewards term-density and the speaker name can be
                    -- half the chunk. Speaker-aware queries are still
                    -- served by the ``speaker:`` operator on the
                    -- ``chunks.speaker`` column.
                    CREATE VIRTUAL TABLE IF NOT EXISTS chunks_fts
                        USING fts5(text, content='');

                    CREATE TRIGGER IF NOT EXISTS chunks_ai
                        AFTER INSERT ON chunks BEGIN
                        INSERT INTO chunks_vec(rowid, embedding) VALUES (new.id, new.embedding);
                        INSERT INTO chunks_fts(rowid, text) VALUES (
                            new.id,
                            CASE
                                WHEN new.speaker IS NOT NULL
                                 AND new.text LIKE new.speaker || ': %'
                                THEN SUBSTR(new.text, LENGTH(new.speaker) + 3)
                                ELSE new.text
                            END
                        );
                    END;
                    CREATE TRIGGER IF NOT EXISTS chunks_ad
                        AFTER DELETE ON chunks BEGIN
                        DELETE FROM chunks_vec WHERE rowid = old.id;
                        INSERT INTO chunks_fts(chunks_fts, rowid, text) VALUES (
                            'delete',
                            old.id,
                            CASE
                                WHEN old.speaker IS NOT NULL
                                 AND old.text LIKE old.speaker || ': %'
                                THEN SUBSTR(old.text, LENGTH(old.speaker) + 3)
                                ELSE old.text
                            END
                        );
                    END;
                    CREATE TRIGGER IF NOT EXISTS chunks_au
                        AFTER UPDATE ON chunks BEGIN
                        DELETE FROM chunks_vec WHERE rowid = old.id;
                        INSERT INTO chunks_vec(rowid, embedding) VALUES (new.id, new.embedding);
                        INSERT INTO chunks_fts(chunks_fts, rowid, text) VALUES (
                            'delete',
                            old.id,
                            CASE
                                WHEN old.speaker IS NOT NULL
                                 AND old.text LIKE old.speaker || ': %'
                                THEN SUBSTR(old.text, LENGTH(old.speaker) + 3)
                                ELSE old.text
                            END
                        );
                        INSERT INTO chunks_fts(rowid, text) VALUES (
                            new.id,
                            CASE
                                WHEN new.speaker IS NOT NULL
                                 AND new.text LIKE new.speaker || ': %'
                                THEN SUBSTR(new.text, LENGTH(new.speaker) + 3)
                                ELSE new.text
                            END
                        );
                    END;
                    """
                )
                logger.info("Migration complete: spec #28 §2.10 chunks tables created")

        # Migration: rebuild chunks_fts as contentless and strip the
        # ``"{speaker}: "`` prefix before indexing. Without this BM25
        # promotes short interjections by named speakers above
        # substantive content. Detect by inspecting the create-SQL: old
        # form has ``content='chunks'``, new form has ``content=''``.
        cursor = conn.execute("SELECT sql FROM sqlite_master WHERE type='table' AND name='chunks_fts'")
        row = cursor.fetchone()
        if row is not None and "content='chunks'" in (row["sql"] or ""):
            try:
                from ..utils.sqlite_ext import SqliteVecNotInstalledError, load_vec_extension

                load_vec_extension(conn)
            except SqliteVecNotInstalledError:
                # Skip — the chunks tables exist but vec ext is gone;
                # nothing to do here, the next env with the extension
                # will run the rebuild.
                pass
            else:
                logger.info("Migrating database: rebuilding chunks_fts as contentless (strip speaker prefix)")
                conn.executescript(
                    """
                    DROP TRIGGER IF EXISTS chunks_ai;
                    DROP TRIGGER IF EXISTS chunks_ad;
                    DROP TRIGGER IF EXISTS chunks_au;
                    DROP TABLE IF EXISTS chunks_fts;

                    CREATE VIRTUAL TABLE chunks_fts USING fts5(text, content='');

                    CREATE TRIGGER chunks_ai
                        AFTER INSERT ON chunks BEGIN
                        INSERT INTO chunks_vec(rowid, embedding) VALUES (new.id, new.embedding);
                        INSERT INTO chunks_fts(rowid, text) VALUES (
                            new.id,
                            CASE
                                WHEN new.speaker IS NOT NULL
                                 AND new.text LIKE new.speaker || ': %'
                                THEN SUBSTR(new.text, LENGTH(new.speaker) + 3)
                                ELSE new.text
                            END
                        );
                    END;
                    CREATE TRIGGER chunks_ad
                        AFTER DELETE ON chunks BEGIN
                        DELETE FROM chunks_vec WHERE rowid = old.id;
                        INSERT INTO chunks_fts(chunks_fts, rowid, text) VALUES (
                            'delete',
                            old.id,
                            CASE
                                WHEN old.speaker IS NOT NULL
                                 AND old.text LIKE old.speaker || ': %'
                                THEN SUBSTR(old.text, LENGTH(old.speaker) + 3)
                                ELSE old.text
                            END
                        );
                    END;
                    CREATE TRIGGER chunks_au
                        AFTER UPDATE ON chunks BEGIN
                        DELETE FROM chunks_vec WHERE rowid = old.id;
                        INSERT INTO chunks_vec(rowid, embedding) VALUES (new.id, new.embedding);
                        INSERT INTO chunks_fts(chunks_fts, rowid, text) VALUES (
                            'delete',
                            old.id,
                            CASE
                                WHEN old.speaker IS NOT NULL
                                 AND old.text LIKE old.speaker || ': %'
                                THEN SUBSTR(old.text, LENGTH(old.speaker) + 3)
                                ELSE old.text
                            END
                        );
                        INSERT INTO chunks_fts(rowid, text) VALUES (
                            new.id,
                            CASE
                                WHEN new.speaker IS NOT NULL
                                 AND new.text LIKE new.speaker || ': %'
                                THEN SUBSTR(new.text, LENGTH(new.speaker) + 3)
                                ELSE new.text
                            END
                        );
                    END;

                    INSERT INTO chunks_fts(rowid, text)
                    SELECT id,
                           CASE
                               WHEN speaker IS NOT NULL
                                AND text LIKE speaker || ': %'
                               THEN SUBSTR(text, LENGTH(speaker) + 3)
                               ELSE text
                           END
                    FROM chunks;
                    """
                )
                logger.info("Migration complete: chunks_fts rebuilt without speaker prefix")

        # Migration: drop idx_chunks_model. Every chunk shares a single
        # embedding_model value, so the index is non-selective. Worse, it
        # tricked the planner into a chunks-driven join for FTS5 queries
        # (full 100k-row scan with per-row FTS probe → 2-36s). Lexical
        # search now uses ``+c.embedding_model = ?`` to deopt the index,
        # but dropping it removes the trap entirely so future queries
        # can't fall back into it.
        conn.execute("DROP INDEX IF EXISTS idx_chunks_model")

        # Migration: rewrite legacy http:// artwork URLs to https://
        # The web UI's CSP only allows `img-src https:`, so any stored
        # http:// image_url is silently blocked by the browser. Every
        # CDN we've seen serves the same path over TLS, so an
        # unconditional upgrade is safe and idempotent.
        cursor = conn.execute("SELECT COUNT(*) AS n FROM podcasts WHERE image_url LIKE 'http://%'")
        podcast_http_count = cursor.fetchone()["n"]
        cursor = conn.execute("SELECT COUNT(*) AS n FROM episodes WHERE image_url LIKE 'http://%'")
        episode_http_count = cursor.fetchone()["n"]
        if podcast_http_count or episode_http_count:
            logger.info(
                "Migrating database: upgrading http:// artwork URLs to https://",
                podcasts=podcast_http_count,
                episodes=episode_http_count,
            )
            conn.execute(
                "UPDATE podcasts SET image_url = 'https://' || substr(image_url, 8) " "WHERE image_url LIKE 'http://%'"
            )
            conn.execute(
                "UPDATE episodes SET image_url = 'https://' || substr(image_url, 8) " "WHERE image_url LIKE 'http://%'"
            )
            logger.info("Migration complete: artwork URLs upgraded to https")

        # Per-user inbox fan-out. ``episodes.published_at`` gates visibility
        # (NULL until the pipeline finalizes, non-NULL once delivered) and
        # ``user_episode_inbox`` holds the per-user rows. The backfill treats
        # every already-summarized episode as already-published, using its
        # last-touched timestamp as the publication time.
        cursor = conn.execute("PRAGMA table_info(episodes)")
        episode_columns = {row["name"] for row in cursor.fetchall()}
        if "published_at" not in episode_columns:
            logger.info("Migrating database: adding episodes.published_at")
            conn.execute("ALTER TABLE episodes ADD COLUMN published_at TIMESTAMP NULL")
            conn.execute(
                "CREATE INDEX IF NOT EXISTS idx_episodes_published_at "
                "ON episodes(published_at DESC) WHERE published_at IS NOT NULL"
            )
            conn.execute(
                """
                UPDATE episodes
                   SET published_at = COALESCE(updated_at, created_at)
                 WHERE summary_path IS NOT NULL
                   AND published_at IS NULL
                """
            )
            logger.info("Migration complete: episodes.published_at added and backfilled")

        # Spec #28 §5.2 — precomputed "Related episodes" for the reader
        # rail. Relevance is a corpus-global blend (TF-IDF topical
        # similarity + dense vector + entity overlap) that's too expensive
        # to compute per request, so the batch ``thestill related build``
        # (run after reindex/backfill) writes the top-N neighbours here and
        # the API reads them straight back. Plain table — no vec extension
        # needed, so it migrates even on the slim image.
        cursor = conn.execute("SELECT name FROM sqlite_master WHERE type='table' AND name='episode_related'")
        if cursor.fetchone() is None:
            logger.info("Migrating database: creating spec #28 §5.2 episode_related")
            conn.executescript(
                """
                CREATE TABLE IF NOT EXISTS episode_related (
                    episode_id         TEXT NOT NULL REFERENCES episodes(id) ON DELETE CASCADE,
                    related_episode_id TEXT NOT NULL REFERENCES episodes(id) ON DELETE CASCADE,
                    rank               INTEGER NOT NULL,
                    score              REAL NOT NULL,
                    computed_at        TIMESTAMP NOT NULL
                                       DEFAULT (strftime('%Y-%m-%dT%H:%M:%f+00:00','now')),
                    PRIMARY KEY (episode_id, related_episode_id)
                );
                CREATE INDEX IF NOT EXISTS idx_episode_related_src
                    ON episode_related(episode_id, rank);
                """
            )
            logger.info("Migration complete: episode_related created")

        # Spec #46 Tier 0 — materialised per-episode centroid vectors. The
        # related-episodes builder used to reload every chunk embedding
        # (O(total chunks)) on each run; this caches one L2-normalised
        # centroid per (episode, model) so the builder reads O(episodes)
        # rows instead. Written by ``ChunkWriter`` at chunk-write time and
        # self-healed by the builder for pre-existing episodes.
        cursor = conn.execute("SELECT name FROM sqlite_master WHERE type='table' AND name='episode_vectors'")
        if cursor.fetchone() is None:
            logger.info("Migrating database: creating spec #46 episode_vectors")
            conn.executescript(
                """
                CREATE TABLE IF NOT EXISTS episode_vectors (
                    episode_id      TEXT NOT NULL REFERENCES episodes(id) ON DELETE CASCADE,
                    embedding_model TEXT NOT NULL,
                    chunk_count     INTEGER NOT NULL,
                    centroid        BLOB NOT NULL,
                    computed_at     TIMESTAMP NOT NULL
                                    DEFAULT (strftime('%Y-%m-%dT%H:%M:%f+00:00','now')),
                    PRIMARY KEY (episode_id, embedding_model)
                );
                """
            )
            logger.info("Migration complete: episode_vectors created")

        # Spec #46 Tier 2 — persisted IDF model (vocabulary + idf weight per
        # term). The related-episodes builder fits TF-IDF over the whole
        # corpus once per full build and stores it here so incremental
        # updates can transform a new episode's text without refitting.
        cursor = conn.execute("SELECT name FROM sqlite_master WHERE type='table' AND name='related_idf'")
        if cursor.fetchone() is None:
            logger.info("Migrating database: creating spec #46 related_idf")
            conn.executescript(
                """
                CREATE TABLE IF NOT EXISTS related_idf (
                    term TEXT PRIMARY KEY,
                    idf  REAL NOT NULL
                );
                """
            )
            logger.info("Migration complete: related_idf created")

        # Spec #46 Tier 2 — ANN index over episode centroids for candidate
        # generation (find episodes near a source without an O(N²) scan).
        # Mirrors the chunks_vec pattern: a vec0 virtual table kept in sync
        # with episode_vectors by triggers, keyed on episode_vectors.rowid.
        # Guarded on the sqlite-vec extension like the chunks migration.
        cursor = conn.execute("SELECT name FROM sqlite_master WHERE type='table' AND name='episode_vec'")
        if cursor.fetchone() is None:
            from ..search.base import DEFAULT_EMBEDDING_MODEL, embedding_dim_for
            from ..utils.sqlite_ext import SqliteVecNotInstalledError, load_vec_extension

            try:
                load_vec_extension(conn)
            except SqliteVecNotInstalledError:
                logger.warning("sqlite_vec_unavailable_skipping_episode_vec_migration")
            else:
                vec_dim = embedding_dim_for(DEFAULT_EMBEDDING_MODEL)
                logger.info("Migrating database: creating spec #46 episode_vec ANN index", vec_dim=vec_dim)
                conn.executescript(
                    f"""
                    CREATE VIRTUAL TABLE IF NOT EXISTS episode_vec USING vec0(
                        embedding float[{vec_dim}]
                    );

                    CREATE TRIGGER IF NOT EXISTS episode_vectors_ai
                        AFTER INSERT ON episode_vectors BEGIN
                        INSERT INTO episode_vec(rowid, embedding) VALUES (new.rowid, new.centroid);
                    END;
                    CREATE TRIGGER IF NOT EXISTS episode_vectors_ad
                        AFTER DELETE ON episode_vectors BEGIN
                        DELETE FROM episode_vec WHERE rowid = old.rowid;
                    END;
                    CREATE TRIGGER IF NOT EXISTS episode_vectors_au
                        AFTER UPDATE ON episode_vectors BEGIN
                        DELETE FROM episode_vec WHERE rowid = old.rowid;
                        INSERT INTO episode_vec(rowid, embedding) VALUES (new.rowid, new.centroid);
                    END;

                    -- Backfill the index from any centroids already present.
                    INSERT INTO episode_vec(rowid, embedding)
                        SELECT rowid, centroid FROM episode_vectors;
                    """
                )
                logger.info("Migration complete: episode_vec created")

        cursor = conn.execute("SELECT name FROM sqlite_master WHERE type='table' AND name='user_episode_inbox'")
        if cursor.fetchone() is None:
            logger.info("Migrating database: creating user_episode_inbox")
            conn.executescript(
                """
                CREATE TABLE IF NOT EXISTS user_episode_inbox (
                    id              TEXT PRIMARY KEY NOT NULL,
                    user_id         TEXT NOT NULL,
                    episode_id      TEXT NOT NULL,
                    source          TEXT NOT NULL,
                    state           TEXT NOT NULL DEFAULT 'unread',
                    delivered_at    TIMESTAMP NOT NULL
                                    DEFAULT (strftime('%Y-%m-%dT%H:%M:%f+00:00','now')),
                    state_changed_at TIMESTAMP NULL,
                    FOREIGN KEY (user_id) REFERENCES users(id) ON DELETE CASCADE,
                    FOREIGN KEY (episode_id) REFERENCES episodes(id) ON DELETE CASCADE,
                    UNIQUE(user_id, episode_id),
                    CHECK (length(id) = 36),
                    CHECK (source IN ('follow_new','follow_seed','ad_hoc','import')),
                    CHECK (state IN ('unread','read','saved','dismissed'))
                );

                -- Hot path: render an unread inbox. Partial index keeps it
                -- cheap even when read/dismissed rows accumulate.
                CREATE INDEX IF NOT EXISTS idx_inbox_user_unread
                    ON user_episode_inbox(user_id, delivered_at DESC)
                    WHERE state = 'unread';

                CREATE INDEX IF NOT EXISTS idx_inbox_user_all
                    ON user_episode_inbox(user_id, delivered_at DESC);

                CREATE INDEX IF NOT EXISTS idx_inbox_episode
                    ON user_episode_inbox(episode_id);
                """
            )
            logger.info("Migration complete: user_episode_inbox table created")

        # Per-user briefings (spec #36). Cursor (cursor_from, cursor_to)
        # makes "what inbox window did this briefing cover" reproducible
        # without joining back to the inbox at the same moment in time.
        # ``script_path`` / ``audio_path`` are nullable so the rendering
        # pipeline (Phase 1.5 / spec #34) can populate them later without
        # blocking the state-machine on writes.
        cursor = conn.execute("SELECT name FROM sqlite_master WHERE type='table' AND name='user_briefings'")
        if cursor.fetchone() is None:
            logger.info("Migrating database: creating user_briefings")
            conn.executescript(
                """
                CREATE TABLE IF NOT EXISTS user_briefings (
                    id              TEXT PRIMARY KEY NOT NULL,
                    user_id         TEXT NOT NULL,
                    cursor_from     TIMESTAMP NOT NULL,
                    cursor_to       TIMESTAMP NOT NULL,
                    episode_count   INTEGER NOT NULL,
                    script_path     TEXT NULL,
                    audio_path      TEXT NULL,
                    created_at      TIMESTAMP NOT NULL
                                    DEFAULT (strftime('%Y-%m-%dT%H:%M:%f+00:00','now')),
                    listened_at     TIMESTAMP NULL,
                    FOREIGN KEY (user_id) REFERENCES users(id) ON DELETE CASCADE,
                    CHECK (length(id) = 36),
                    CHECK (cursor_to > cursor_from),
                    CHECK (episode_count >= 0)
                );

                CREATE INDEX IF NOT EXISTS idx_user_briefings_user_recent
                    ON user_briefings(user_id, created_at DESC);
                """
            )
            logger.info("Migration complete: user_briefings table created")

        # Briefing schedules (spec #50). One row per user: when (hour_local
        # in timezone) and how often (frequency + weekday) their briefing is
        # generated. ``next_run_at`` is the materialized UTC due-time the
        # scheduler scans; NULL = parked/disabled (same idiom as feeds with
        # next_refresh_at = NULL in spec #48).
        cursor = conn.execute("SELECT name FROM sqlite_master WHERE type='table' AND name='user_briefing_schedules'")
        if cursor.fetchone() is None:
            logger.info("Migrating database: creating user_briefing_schedules")
            conn.executescript(
                """
                CREATE TABLE IF NOT EXISTS user_briefing_schedules (
                    user_id     TEXT PRIMARY KEY NOT NULL,
                    frequency   TEXT NOT NULL DEFAULT 'daily',
                    hour_local  INTEGER NOT NULL DEFAULT 8,
                    weekday     INTEGER NULL,
                    timezone    TEXT NOT NULL,
                    enabled     INTEGER NOT NULL DEFAULT 1,
                    next_run_at TIMESTAMP NULL,
                    created_at  TIMESTAMP NOT NULL
                                DEFAULT (strftime('%Y-%m-%dT%H:%M:%f+00:00','now')),
                    updated_at  TIMESTAMP NOT NULL
                                DEFAULT (strftime('%Y-%m-%dT%H:%M:%f+00:00','now')),
                    FOREIGN KEY (user_id) REFERENCES users(id) ON DELETE CASCADE,
                    CHECK (frequency IN ('daily','weekly')),
                    CHECK (hour_local BETWEEN 0 AND 23),
                    CHECK (weekday IS NULL OR weekday BETWEEN 0 AND 6),
                    CHECK ((frequency = 'weekly') = (weekday IS NOT NULL)),
                    CHECK (enabled IN (0, 1))
                );

                CREATE INDEX IF NOT EXISTS idx_briefing_schedules_due
                    ON user_briefing_schedules(next_run_at)
                    WHERE enabled = 1 AND next_run_at IS NOT NULL;
                """
            )
            logger.info("Migration complete: user_briefing_schedules table created")

        # Imports + auto-add columns. Indexes are created unconditionally
        # (IF NOT EXISTS) so fresh databases (columns came from _create_schema)
        # and legacy databases (columns just added below) end up identical.
        cursor = conn.execute("PRAGMA table_info(podcasts)")
        podcast_columns = {row["name"] for row in cursor.fetchall()}
        if "synthetic" not in podcast_columns:
            logger.info("Migrating database: adding podcasts.synthetic column")
            conn.execute("ALTER TABLE podcasts ADD COLUMN synthetic INTEGER NOT NULL DEFAULT 0")
        if "auto_added" not in podcast_columns:
            logger.info("Migrating database: adding podcasts.auto_added column")
            conn.execute("ALTER TABLE podcasts ADD COLUMN auto_added INTEGER NOT NULL DEFAULT 0")

        conn.execute("CREATE INDEX IF NOT EXISTS idx_podcasts_synthetic " "ON podcasts(synthetic) WHERE synthetic = 1")
        conn.execute(
            "CREATE INDEX IF NOT EXISTS idx_podcasts_auto_added " "ON podcasts(auto_added) WHERE auto_added = 1"
        )

        cursor = conn.execute("PRAGMA table_info(episodes)")
        episode_columns = {row["name"] for row in cursor.fetchall()}
        if "canonical_id" not in episode_columns:
            logger.info("Migrating database: adding episodes.canonical_id column")
            conn.execute("ALTER TABLE episodes ADD COLUMN canonical_id TEXT NULL")

        # Migration: ``auto_process_excluded`` marks back-catalog episodes that a
        # brand-new podcast's initial backfill deliberately chose NOT to
        # auto-transcribe (only the most-recent N are processed on subscribe).
        # The spec #48 refresh-feed recovery sweep skips these so they are never
        # auto-enqueued, while genuine crash-orphans (flag = 0) are still
        # recovered. A user can still manually process an excluded episode.
        if "auto_process_excluded" not in episode_columns:
            logger.info("Migrating database: adding episodes.auto_process_excluded column")
            conn.execute("ALTER TABLE episodes ADD COLUMN auto_process_excluded INTEGER NOT NULL DEFAULT 0")

        conn.execute(
            "CREATE UNIQUE INDEX IF NOT EXISTS idx_episodes_canonical_id "
            "ON episodes(canonical_id) WHERE canonical_id IS NOT NULL"
        )

        # SQLite has no ALTER COLUMN for CHECK constraints, so extending the
        # user_episode_inbox source enum requires a table rebuild. Detect the
        # need via the stored CREATE TABLE SQL.
        cursor = conn.execute("SELECT sql FROM sqlite_master WHERE type='table' AND name='user_episode_inbox'")
        row = cursor.fetchone()
        if row is not None and "'import'" not in (row["sql"] or ""):
            logger.info("Migrating database: extending user_episode_inbox.source CHECK")
            conn.executescript(
                """
                CREATE TABLE user_episode_inbox_new (
                    id              TEXT PRIMARY KEY NOT NULL,
                    user_id         TEXT NOT NULL,
                    episode_id      TEXT NOT NULL,
                    source          TEXT NOT NULL,
                    state           TEXT NOT NULL DEFAULT 'unread',
                    delivered_at    TIMESTAMP NOT NULL
                                    DEFAULT (strftime('%Y-%m-%dT%H:%M:%f+00:00','now')),
                    state_changed_at TIMESTAMP NULL,
                    FOREIGN KEY (user_id) REFERENCES users(id) ON DELETE CASCADE,
                    FOREIGN KEY (episode_id) REFERENCES episodes(id) ON DELETE CASCADE,
                    UNIQUE(user_id, episode_id),
                    CHECK (length(id) = 36),
                    CHECK (source IN ('follow_new','follow_seed','ad_hoc','import')),
                    CHECK (state IN ('unread','read','saved','dismissed'))
                );

                INSERT INTO user_episode_inbox_new
                    (id, user_id, episode_id, source, state, delivered_at, state_changed_at)
                SELECT id, user_id, episode_id, source, state, delivered_at, state_changed_at
                  FROM user_episode_inbox;

                DROP TABLE user_episode_inbox;
                ALTER TABLE user_episode_inbox_new RENAME TO user_episode_inbox;

                CREATE INDEX IF NOT EXISTS idx_inbox_user_unread
                    ON user_episode_inbox(user_id, delivered_at DESC)
                    WHERE state = 'unread';
                CREATE INDEX IF NOT EXISTS idx_inbox_user_all
                    ON user_episode_inbox(user_id, delivered_at DESC);
                CREATE INDEX IF NOT EXISTS idx_inbox_episode
                    ON user_episode_inbox(episode_id);
                """
            )
            logger.info("Migration complete: user_episode_inbox CHECK extended")

        # Spec #40 — pending transcription operations move from
        # ``data/pending_operations/*.json`` files into a real SQLite table.
        # They were always DB-shaped data (UUID PK, queried by status,
        # minute-scale lifecycle); the file-on-disk implementation was a
        # historical accident this spec corrects. Idempotent on the table
        # existence check.
        cursor = conn.execute(
            "SELECT name FROM sqlite_master WHERE type='table' AND name='pending_transcription_operations'"
        )
        if cursor.fetchone() is None:
            logger.info("Migrating database: creating pending_transcription_operations table (spec #40)")
            conn.executescript(
                """
                CREATE TABLE IF NOT EXISTS pending_transcription_operations (
                    operation_id    TEXT PRIMARY KEY NOT NULL,
                    provider        TEXT NOT NULL,
                    episode_id      TEXT NOT NULL,
                    payload_json    TEXT NOT NULL,
                    created_at      TEXT NOT NULL DEFAULT (strftime('%Y-%m-%dT%H:%M:%f+00:00','now')),
                    updated_at      TEXT NOT NULL DEFAULT (strftime('%Y-%m-%dT%H:%M:%f+00:00','now')),
                    CHECK (provider IN ('google','elevenlabs'))
                );

                CREATE INDEX IF NOT EXISTS idx_pending_ops_provider
                    ON pending_transcription_operations(provider);
                CREATE INDEX IF NOT EXISTS idx_pending_ops_episode
                    ON pending_transcription_operations(episode_id);
                """
            )
            # Backfill any in-flight JSON files from the old layout into the
            # new table. Runs once (gated by the table-existence check above)
            # and moves the source files to a sibling ``.migrated/`` directory
            # as a belt-and-braces undo channel.
            self._backfill_pending_operations_from_files(conn)
            logger.info("Migration complete: pending_transcription_operations created (spec #40)")

        # spec #48 — background refresh scheduling + per-feed adaptive cadence.
        # Runs LAST in _run_migrations so the backfill's ``synthetic`` /
        # ``is_complete`` filters reference columns that earlier migration
        # steps have already added. Columns are nullable with no default; a
        # migration that only adds them leaves every existing podcast PARKED
        # (next_refresh_at NULL), so the queued path would enqueue zero feeds —
        # hence the backfill.
        cursor = conn.execute("PRAGMA table_info(podcasts)")
        podcast_columns_now = {row["name"] for row in cursor.fetchall()}
        added_cadence = False
        if "refresh_interval_seconds" not in podcast_columns_now:
            logger.info("Migrating database: adding refresh_interval_seconds to podcasts")
            conn.execute("ALTER TABLE podcasts ADD COLUMN refresh_interval_seconds INTEGER NULL")
            added_cadence = True
        if "next_refresh_at" not in podcast_columns_now:
            logger.info("Migrating database: adding next_refresh_at to podcasts")
            conn.execute("ALTER TABLE podcasts ADD COLUMN next_refresh_at TIMESTAMP NULL")
        if "last_refresh_at" not in podcast_columns_now:
            logger.info("Migrating database: adding last_refresh_at to podcasts")
            conn.execute("ALTER TABLE podcasts ADD COLUMN last_refresh_at TIMESTAMP NULL")
        if "last_refresh_error" not in podcast_columns_now:
            logger.info("Migrating database: adding last_refresh_error to podcasts")
            conn.execute("ALTER TABLE podcasts ADD COLUMN last_refresh_error TEXT NULL")

        # Backfill: seed active (non-synthetic, ongoing) feeds with the default
        # interval and a due time jittered across the first window — NOT all at
        # ``now`` (that would thunder-herd the scheduler's first tick). Runs
        # once (gated by ``added_cadence``).
        if added_cadence:
            from ..utils.config import get_default_refresh_interval_seconds

            default_interval = get_default_refresh_interval_seconds()
            now_dt = now_utc()
            rows = conn.execute(
                "SELECT id FROM podcasts WHERE COALESCE(synthetic, 0) = 0 AND COALESCE(is_complete, 0) = 0 "
                "AND (COALESCE(auto_added, 0) = 0 "
                "OR EXISTS (SELECT 1 FROM podcast_followers pf WHERE pf.podcast_id = podcasts.id))"
            ).fetchall()
            for row in rows:
                pid = row["id"]
                offset = (hash(pid) % max(1, default_interval)) if default_interval > 0 else 0
                next_at = (now_dt + timedelta(seconds=offset)).isoformat()
                conn.execute(
                    "UPDATE podcasts SET refresh_interval_seconds = ?, next_refresh_at = ? WHERE id = ?",
                    (default_interval, next_at, pid),
                )
            logger.info("Backfilled refresh schedule for active podcasts", count=len(rows))

        conn.execute(
            "CREATE INDEX IF NOT EXISTS idx_podcasts_due ON podcasts(next_refresh_at) WHERE next_refresh_at IS NOT NULL"
        )

    # ------------------------------------------------------------------
    # Spec #40 — file → DB backfill
    # ------------------------------------------------------------------

    def _backfill_pending_operations_from_files(self, conn: sqlite3.Connection) -> None:
        """One-shot import of ``data/pending_operations/*.json`` rows.

        Called only from the spec #40 migration block (above), once. The
        guard there is the table-existence check, so re-running this is not
        possible through the normal path.

        Per-file failure logs and continues — a malformed legacy file should
        not block startup, and the source file is left in place (not moved
        to ``.migrated/``) so an operator can inspect it.
        """
        import json
        import shutil

        # The migration runs from inside ``SqlitePodcastRepository.__init__`` so
        # ``self.db_path`` is set. The pending_operations directory sits next
        # to ``podcasts.db`` in the same data root. If the directory doesn't
        # exist (fresh install, no in-flight ops) the backfill is a no-op.
        pending_dir = Path(self.db_path).resolve().parent / "pending_operations"
        if not pending_dir.is_dir():
            return

        migrated_dir = pending_dir / ".migrated"
        imported = 0
        failed = 0

        for op_file in sorted(pending_dir.glob("*.json")):
            if op_file.parent.name == ".migrated":
                continue  # Defensive: ignore anything that's already migrated.
            try:
                with op_file.open("r", encoding="utf-8") as f:
                    payload = json.load(f)

                # Provider: filename prefix is the discriminator. ElevenLabs
                # files use ``elevenlabs_*.json``; Google files have no prefix.
                provider = "elevenlabs" if op_file.stem.startswith("elevenlabs_") else "google"

                # operation_id: filename stem (sans provider prefix).
                operation_id = op_file.stem
                if provider == "elevenlabs" and operation_id.startswith("elevenlabs_"):
                    operation_id = operation_id[len("elevenlabs_") :]

                # episode_id: both providers carry it inside the payload.
                # Skip rows that lack one — they couldn't have been written by
                # the current code (which guards), so they're either stale or
                # malformed.
                episode_id = payload.get("episode_id")
                if not episode_id:
                    logger.warning(
                        "pending_operations_backfill_skipped_missing_episode_id",
                        file=str(op_file),
                    )
                    failed += 1
                    continue

                conn.execute(
                    """
                    INSERT OR IGNORE INTO pending_transcription_operations
                        (operation_id, provider, episode_id, payload_json)
                    VALUES (?, ?, ?, ?)
                    """,
                    (operation_id, provider, episode_id, json.dumps(payload)),
                )
                imported += 1

                # Move the source file out of the way. ``.migrated/`` is a
                # recovery hatch — kept until a follow-up spec retires
                # ``PathManager.pending_operations_dir()`` entirely.
                migrated_dir.mkdir(parents=True, exist_ok=True)
                shutil.move(str(op_file), str(migrated_dir / op_file.name))
            except Exception as exc:
                logger.warning(
                    "pending_operations_backfill_failed_for_file",
                    file=str(op_file),
                    error=str(exc),
                )
                failed += 1

        if imported or failed:
            logger.info(
                "pending_operations_backfill_complete",
                imported=imported,
                failed=failed,
                migrated_dir=str(migrated_dir),
            )

    # ------------------------------------------------------------------
    # Category helpers
    # ------------------------------------------------------------------

    def _seed_categories(self, conn: sqlite3.Connection) -> None:
        """Populate the categories table from Apple's official taxonomy.

        Idempotent: callers must check the table is empty first. We insert
        each top-level category, capture its id, then insert its
        subcategories with parent_id pointing at the parent row.
        """
        for top_name, sub_names in APPLE_PODCAST_TAXONOMY.items():
            genre_id = APPLE_GENRE_IDS.get(top_name)
            cursor = conn.execute(
                "INSERT INTO categories (name, slug, parent_id, apple_genre_id) " "VALUES (?, ?, NULL, ?)",
                (top_name, generate_slug(top_name), genre_id),
            )
            parent_id = cursor.lastrowid
            for sub_name in sorted(sub_names):
                conn.execute(
                    "INSERT INTO categories (name, slug, parent_id, apple_genre_id) " "VALUES (?, ?, ?, NULL)",
                    (sub_name, generate_slug(sub_name), parent_id),
                )

    def _backfill_category_fks(self, conn: sqlite3.Connection) -> None:
        """One-time migration step: resolve legacy free-text category columns
        to category FK ids and populate primary_category_id / secondary_category_id.

        Best-effort match: tolerant of casing and whitespace. Strings that
        don't match the canonical taxonomy are stored as NULL (Q4-iii).
        Requires ``_load_categories_cache`` to have run already so the runtime
        resolver can be reused (one source of truth for the matching rules).
        """
        cursor = conn.execute(
            "SELECT id, primary_category, primary_subcategory, "
            "       secondary_category, secondary_subcategory FROM podcasts"
        )
        unresolved: List[str] = []
        updates: List[Tuple[Optional[int], Optional[int], str]] = []
        for row in cursor.fetchall():
            primary_id = self._resolve_category_strings_to_id(row["primary_category"], row["primary_subcategory"])
            secondary_id = self._resolve_category_strings_to_id(row["secondary_category"], row["secondary_subcategory"])
            if row["primary_category"] and primary_id is None:
                unresolved.append(f"primary={row['primary_category']!r}")
            if row["secondary_category"] and secondary_id is None:
                unresolved.append(f"secondary={row['secondary_category']!r}")
            updates.append((primary_id, secondary_id, row["id"]))

        if updates:
            conn.executemany(
                "UPDATE podcasts SET primary_category_id = ?, secondary_category_id = ? WHERE id = ?",
                updates,
            )
        logger.info(
            "category backfill complete",
            podcasts_updated=len(updates),
            unresolved_count=len(unresolved),
            unresolved_sample=unresolved[:10] if unresolved else None,
        )

    def _load_categories_cache(self, conn: sqlite3.Connection) -> None:
        """Load the categories table into in-memory dicts for hot-path lookup.

        The taxonomy is small (~100 rows) and effectively read-only at runtime,
        so a one-shot read on init is fine. The ``ORDER BY parent_id IS NOT NULL``
        sort guarantees top-level rows arrive before any of their subcategories,
        so we can build everything in a single pass.
        """
        rows = conn.execute("SELECT id, name, parent_id FROM categories ORDER BY parent_id IS NOT NULL, id").fetchall()
        self._cat_id_to_pair = {}
        self._cat_pair_to_id = {}
        top_id_to_name: Dict[int, str] = {}
        for row in rows:
            if row["parent_id"] is None:
                top_id_to_name[row["id"]] = row["name"]
                self._cat_id_to_pair[row["id"]] = (row["name"], None)
                self._cat_pair_to_id[(normalize_category_name(row["name"]), None)] = row["id"]
            else:
                top_name = top_id_to_name.get(row["parent_id"])
                if top_name is None:
                    continue  # orphan subcategory — defensive, FK should prevent
                self._cat_id_to_pair[row["id"]] = (top_name, row["name"])
                self._cat_pair_to_id[(normalize_category_name(top_name), normalize_category_name(row["name"]))] = row[
                    "id"
                ]

    def _resolve_category_strings_to_id(self, top: Optional[str], sub: Optional[str]) -> Optional[int]:
        """Return the most-specific category FK id matching the inputs.

        - (None, _) → None
        - (top, None) or (top, unknown_sub) → id of the top-level row, or
          None if the top-level itself doesn't match the taxonomy.
        - (top, sub) → id of the subcategory row if both match, else top id,
          else None. (Best-effort matching per Q4-iii.)
        """
        if not top:
            return None
        top_norm = normalize_category_name(top)
        top_id = self._cat_pair_to_id.get((top_norm, None))
        if top_id is None:
            return None
        if not sub:
            return top_id
        sub_id = self._cat_pair_to_id.get((top_norm, normalize_category_name(sub)))
        return sub_id if sub_id is not None else top_id

    def _resolve_category_id_to_pair(self, cat_id: Optional[int]) -> Tuple[Optional[str], Optional[str]]:
        """Return (top_name, sub_name) for a category FK id; both None if unknown."""
        if cat_id is None:
            return (None, None)
        return self._cat_id_to_pair.get(cat_id, (None, None))

    # ------------------------------------------------------------------
    # Top-podcasts (chart) seeding
    # ------------------------------------------------------------------

    # Where the per-region chart JSONs live. Each file is named
    # data/top_podcasts_<region>.json and is produced by
    # scripts/build_top_podcasts.py. The seeder discovers regions by glob —
    # adding a new region is just dropping a new JSON file in the same dir.
    _TOP_PODCASTS_DIR = Path(__file__).resolve().parent.parent.parent / "data"
    _TOP_PODCASTS_GLOB = "top_podcasts_*.json"

    def _seed_top_podcasts(self, conn: sqlite3.Connection) -> None:
        """Sync the top_podcasts + top_podcast_rankings tables from per-region JSON.

        Two-table model: ``top_podcasts`` is one row per unique podcast
        (deduped by ``rss_url`` across all regions); ``top_podcast_rankings``
        is one thin row per (region, podcast) chart appearance.

        Smart-refresh: each region's import is gated on the JSON file's mtime
        versus the mtime stored in ``top_podcasts_meta``. Unchanged regions
        are skipped, so re-opening the DB is cheap and regenerating any
        ``data/top_podcasts_<region>.json`` automatically propagates next
        time the repo is constructed.

        Each region is reseeded atomically: drop that region's rankings,
        upsert the metadata rows, insert fresh rankings. Per-row category
        names are resolved to FK ids via the in-memory cache populated
        earlier in __init__.
        """
        json_files = sorted(self._TOP_PODCASTS_DIR.glob(self._TOP_PODCASTS_GLOB))
        if not json_files:
            return

        meta = {
            row["region"]: row["source_mtime"]
            for row in conn.execute("SELECT region, source_mtime FROM top_podcasts_meta").fetchall()
        }

        for path in json_files:
            region = path.stem.removeprefix("top_podcasts_").lower()
            if not region:
                continue
            mtime = path.stat().st_mtime
            if region in meta and abs(meta[region] - mtime) < _MTIME_EPSILON:
                continue  # unchanged — skip

            try:
                rows = json.loads(path.read_text())
            except (OSError, ValueError) as exc:
                logger.warning(
                    "skipping malformed top-podcasts file",
                    region=region,
                    path=str(path),
                    error=str(exc),
                )
                continue

            now_iso = datetime.now(timezone.utc).isoformat()

            # 1. Drop this region's old rankings.
            conn.execute("DELETE FROM top_podcast_rankings WHERE region = ?", (region,))

            # 2. Dedupe by rss_url within the region — Apple sometimes lists
            #    the same canonical feed under two track_ids (republished
            #    podcasts), but we want one chart slot per podcast. Keep
            #    the better (lower) rank when collapsing duplicates.
            best_by_rss: Dict[str, Tuple[int, Dict[str, Any]]] = {}
            for i, row in enumerate(rows, start=1):
                rss_url = (row.get("rss_url") or "").strip()
                if not rss_url:
                    continue
                rank = row.get("rank") or i
                existing = best_by_rss.get(rss_url)
                if existing is None or rank < existing[0]:
                    best_by_rss[rss_url] = (rank, row)

            # 3. Upsert podcast metadata + insert rankings.
            inserted = 0
            for rss_url, (rank, row) in best_by_rss.items():
                category_id = self._resolve_category_strings_to_id(row.get("category"), row.get("subcategory"))
                # Upsert metadata + grab the id in one round-trip via RETURNING
                # (SQLite >= 3.35). ``last_seen_at`` is bumped every refresh;
                # ``first_seen_at`` is preserved on conflict.
                pid = conn.execute(
                    """
                    INSERT INTO top_podcasts (
                        name, artist, rss_url, apple_url, youtube_url,
                        apple_track_id, category_id, first_seen_at, last_seen_at
                    ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)
                    ON CONFLICT(rss_url) DO UPDATE SET
                        name = excluded.name,
                        artist = excluded.artist,
                        apple_url = excluded.apple_url,
                        youtube_url = excluded.youtube_url,
                        apple_track_id = excluded.apple_track_id,
                        category_id = excluded.category_id,
                        last_seen_at = excluded.last_seen_at
                    RETURNING id
                    """,
                    (
                        row.get("name") or "",
                        row.get("artist"),
                        rss_url,
                        row.get("apple_url"),
                        row.get("youtube_url"),
                        row.get("track_id"),
                        category_id,
                        now_iso,
                        now_iso,
                    ),
                ).fetchone()[0]
                conn.execute(
                    """
                    INSERT INTO top_podcast_rankings (
                        top_podcast_id, region, rank, source_genre, scraped_at
                    ) VALUES (?, ?, ?, ?, ?)
                    """,
                    (pid, region, rank, row.get("source_genre"), now_iso),
                )
                inserted += 1

            conn.execute(
                """
                INSERT INTO top_podcasts_meta (region, source_path, source_mtime, row_count, seeded_at)
                VALUES (?, ?, ?, ?, ?)
                ON CONFLICT(region) DO UPDATE SET
                    source_path = excluded.source_path,
                    source_mtime = excluded.source_mtime,
                    row_count = excluded.row_count,
                    seeded_at = excluded.seeded_at
                """,
                (region, str(path), mtime, inserted, now_iso),
            )
            logger.info(
                "seeded top podcasts",
                region=region,
                rows=inserted,
                source=path.name,
            )

    def _create_schema(self, conn: sqlite3.Connection):
        """Create database schema (single-user variant)."""
        conn.executescript(
            """
            -- ========================================================================
            -- CATEGORIES TABLE (Apple Podcasts taxonomy)
            -- ========================================================================
            -- Self-referential lookup: top-level categories have parent_id NULL
            -- and a populated apple_genre_id; subcategory rows have parent_id
            -- pointing at their top-level. Seeded from data/podcast_categories.json.
            CREATE TABLE IF NOT EXISTS categories (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                name TEXT NOT NULL,
                slug TEXT NOT NULL,
                parent_id INTEGER NULL,
                apple_genre_id INTEGER NULL,
                FOREIGN KEY (parent_id) REFERENCES categories(id) ON DELETE CASCADE
            );

            CREATE INDEX IF NOT EXISTS idx_categories_parent ON categories(parent_id);
            -- Partial unique indexes: SQLite UNIQUE constraints treat NULLs as
            -- distinct, so a single UNIQUE(name, parent_id) won't prevent
            -- duplicate top-level rows where parent_id IS NULL. Split into two
            -- partial indexes so both top-levels and subcategories are unique.
            CREATE UNIQUE INDEX IF NOT EXISTS idx_categories_top_unique
                ON categories(name) WHERE parent_id IS NULL;
            CREATE UNIQUE INDEX IF NOT EXISTS idx_categories_sub_unique
                ON categories(parent_id, name) WHERE parent_id IS NOT NULL;

            -- ========================================================================
            -- TOP PODCASTS (Apple chart snapshots, normalized into 2 tables)
            -- ========================================================================
            -- ``top_podcasts`` is one row per unique podcast (deduped on rss_url),
            -- holding the metadata that's the same regardless of which region's
            -- chart it appears in. ``top_podcast_rankings`` is the per-region
            -- chart fact table — adding/removing a region only touches rankings.
            -- ``top_podcasts_meta`` caches per-region JSON-file mtime so re-init
            -- is a no-op when the source file is unchanged.
            CREATE TABLE IF NOT EXISTS top_podcasts (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                name TEXT NOT NULL,
                artist TEXT NULL,
                rss_url TEXT NOT NULL UNIQUE,
                apple_url TEXT NULL,
                youtube_url TEXT NULL,
                apple_track_id TEXT NULL,
                category_id INTEGER NULL REFERENCES categories(id) ON DELETE SET NULL,
                first_seen_at TIMESTAMP NOT NULL,
                last_seen_at TIMESTAMP NOT NULL
            );
            CREATE INDEX IF NOT EXISTS idx_top_podcasts_category
                ON top_podcasts(category_id) WHERE category_id IS NOT NULL;

            CREATE TABLE IF NOT EXISTS top_podcast_rankings (
                top_podcast_id INTEGER NOT NULL,
                region TEXT NOT NULL,
                rank INTEGER NOT NULL,
                source_genre TEXT NULL,
                scraped_at TIMESTAMP NOT NULL,
                PRIMARY KEY (region, top_podcast_id),
                FOREIGN KEY (top_podcast_id) REFERENCES top_podcasts(id) ON DELETE CASCADE
            );
            CREATE INDEX IF NOT EXISTS idx_top_podcast_rankings_region_rank
                ON top_podcast_rankings(region, rank);
            CREATE INDEX IF NOT EXISTS idx_top_podcast_rankings_podcast
                ON top_podcast_rankings(top_podcast_id);

            CREATE TABLE IF NOT EXISTS top_podcasts_meta (
                region TEXT PRIMARY KEY NOT NULL,
                source_path TEXT NOT NULL,
                source_mtime REAL NOT NULL,
                row_count INTEGER NOT NULL,
                seeded_at TIMESTAMP NOT NULL
            );

            -- ========================================================================
            -- PODCASTS TABLE
            -- ========================================================================
            CREATE TABLE IF NOT EXISTS podcasts (
                id TEXT PRIMARY KEY NOT NULL,
                created_at TIMESTAMP NOT NULL DEFAULT CURRENT_TIMESTAMP,
                updated_at TIMESTAMP NOT NULL DEFAULT CURRENT_TIMESTAMP,
                rss_url TEXT NOT NULL UNIQUE,
                title TEXT NOT NULL,
                slug TEXT NOT NULL DEFAULT '',
                description TEXT NOT NULL DEFAULT '',
                image_url TEXT NULL,
                language TEXT NOT NULL DEFAULT 'en',
                primary_category_id INTEGER NULL REFERENCES categories(id) ON DELETE SET NULL,
                secondary_category_id INTEGER NULL REFERENCES categories(id) ON DELETE SET NULL,
                -- THES-143: Essential metadata
                author TEXT NULL,
                explicit INTEGER NULL,  -- Boolean: 0=false, 1=true, NULL=unknown
                -- THES-144: Show organization
                show_type TEXT NULL,  -- "episodic" or "serial"
                website_url TEXT NULL,
                -- THES-145: Feed management
                is_complete INTEGER NOT NULL DEFAULT 0,  -- Boolean: 0=ongoing, 1=complete
                copyright TEXT NULL,
                -- Incremental-refresh discovery watermark (newest episode
                -- pub_date seen). Compared against feed entries — must track a
                -- real pub_date, never a wall clock.
                last_processed TIMESTAMP NULL,
                -- Wall-clock time an episode was last processed (display only).
                last_processed_at TIMESTAMP NULL,
                -- spec #19: HTTP conditional-GET cache
                etag TEXT NULL,
                last_modified TEXT NULL,
                -- spec #48: background refresh scheduling + adaptive cadence.
                -- ISO-8601 UTC text. ``next_refresh_at IS NULL`` is the PARKED
                -- state (never seeded, or terminally failed) — the scheduler's
                -- due query excludes it. ``last_refresh_error`` stays set while
                -- parked so the staleness/FM-4 alarm still sees the failure.
                refresh_interval_seconds INTEGER NULL,
                next_refresh_at TIMESTAMP NULL,
                last_refresh_at TIMESTAMP NULL,
                last_refresh_error TEXT NULL,
                -- Synthetic fallback parent (e.g. bare-audio imports);
                -- excluded from refresh, browse, and follow flows.
                synthetic INTEGER NOT NULL DEFAULT 0,
                -- Auto-inserted as a side effect of an import. Hidden until
                -- a user follows it; refresh skips rows with zero followers.
                auto_added INTEGER NOT NULL DEFAULT 0,
                CHECK (length(id) = 36),
                CHECK (length(rss_url) > 0)
            );

            CREATE UNIQUE INDEX IF NOT EXISTS idx_podcasts_rss_url ON podcasts(rss_url);
            CREATE INDEX IF NOT EXISTS idx_podcasts_updated_at ON podcasts(updated_at DESC);
            CREATE INDEX IF NOT EXISTS idx_podcasts_slug ON podcasts(slug) WHERE slug != '';
            -- synthetic / auto_added indexes are created in _run_migrations
            -- so legacy DBs add the columns (via ALTER TABLE) first.
            -- Note: category FK indexes are created in _run_migrations so legacy
            -- databases (which lack the FK columns until migration runs) don't
            -- choke on a CREATE INDEX referencing a not-yet-added column.

            -- ========================================================================
            -- EPISODES TABLE
            -- ========================================================================
            CREATE TABLE IF NOT EXISTS episodes (
                id TEXT PRIMARY KEY NOT NULL,
                podcast_id TEXT NOT NULL,
                created_at TIMESTAMP NOT NULL DEFAULT CURRENT_TIMESTAMP,
                updated_at TIMESTAMP NOT NULL DEFAULT CURRENT_TIMESTAMP,
                external_id TEXT NOT NULL,
                title TEXT NOT NULL,
                slug TEXT NOT NULL DEFAULT '',
                description TEXT NOT NULL DEFAULT '',
                description_html TEXT NOT NULL DEFAULT '',
                pub_date TIMESTAMP NULL,
                audio_url TEXT NOT NULL,
                duration INTEGER NULL,
                image_url TEXT NULL,
                -- THES-143: Essential metadata
                explicit INTEGER NULL,  -- Boolean: 0=false, 1=true, NULL=unknown
                episode_type TEXT NULL,  -- "full", "trailer", or "bonus"
                -- THES-144: Episode organization
                episode_number INTEGER NULL,
                season_number INTEGER NULL,
                website_url TEXT NULL,
                -- THES-145: Enclosure metadata
                audio_file_size INTEGER NULL,  -- File size in bytes
                audio_mime_type TEXT NULL,  -- e.g., "audio/mpeg"
                -- File paths
                audio_path TEXT NULL,
                downsampled_audio_path TEXT NULL,
                raw_transcript_path TEXT NULL,
                clean_transcript_path TEXT NULL,
                -- spec #18: structured AnnotatedTranscript JSON sidecar
                clean_transcript_json_path TEXT NULL,
                summary_path TEXT NULL,
                -- spec #18: per-episode playback offset (DB is source of truth;
                -- the JSON sidecar carries a cached copy of this value)
                playback_time_offset_seconds REAL NOT NULL DEFAULT 0.0,
                -- Typed canonical id for resolver-based dedup,
                -- e.g. "youtube:<video_id>" or "audio:<sha256>". NULL for
                -- non-imported episodes (those dedup on
                -- (podcast_id, external_id)).
                canonical_id TEXT NULL,
                FOREIGN KEY (podcast_id) REFERENCES podcasts(id),
                UNIQUE(podcast_id, external_id),
                CHECK (length(id) = 36),
                CHECK (length(external_id) > 0),
                CHECK (length(audio_url) > 0)
            );

            CREATE INDEX IF NOT EXISTS idx_episodes_podcast_id ON episodes(podcast_id);
            CREATE INDEX IF NOT EXISTS idx_episodes_external_id ON episodes(podcast_id, external_id);
            CREATE INDEX IF NOT EXISTS idx_episodes_pub_date ON episodes(pub_date DESC);
            CREATE INDEX IF NOT EXISTS idx_episodes_updated_at ON episodes(updated_at DESC);
            CREATE INDEX IF NOT EXISTS idx_episodes_slug ON episodes(podcast_id, slug) WHERE slug != '';
            -- canonical_id index lives in _run_migrations for the same
            -- legacy-DB reason as the synthetic / auto_added indexes above.

            -- Partial indexes for state queries (highly selective)
            CREATE INDEX IF NOT EXISTS idx_episodes_state_discovered
                ON episodes(podcast_id, pub_date DESC)
                WHERE audio_path IS NULL;

            CREATE INDEX IF NOT EXISTS idx_episodes_state_downloaded
                ON episodes(podcast_id, pub_date DESC)
                WHERE audio_path IS NOT NULL AND downsampled_audio_path IS NULL;

            CREATE INDEX IF NOT EXISTS idx_episodes_state_downsampled
                ON episodes(podcast_id, pub_date DESC)
                WHERE downsampled_audio_path IS NOT NULL AND raw_transcript_path IS NULL;

            CREATE INDEX IF NOT EXISTS idx_episodes_state_transcribed
                ON episodes(podcast_id, pub_date DESC)
                WHERE raw_transcript_path IS NOT NULL AND clean_transcript_path IS NULL;

            -- ========================================================================
            -- EPISODE TRANSCRIPT LINKS TABLE (Podcasting 2.0 <podcast:transcript>)
            -- ========================================================================
            -- Stores external transcript URLs from RSS feeds for evaluation/debugging.
            -- Each episode can have multiple transcript formats (SRT, VTT, JSON, etc.)
            CREATE TABLE IF NOT EXISTS episode_transcript_links (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                episode_id TEXT NOT NULL,
                url TEXT NOT NULL,
                mime_type TEXT NOT NULL,
                language TEXT NULL,
                rel TEXT NULL,
                downloaded_path TEXT NULL,
                created_at TIMESTAMP NOT NULL DEFAULT CURRENT_TIMESTAMP,
                FOREIGN KEY (episode_id) REFERENCES episodes(id) ON DELETE CASCADE,
                UNIQUE(episode_id, url),
                CHECK (length(url) > 0),
                CHECK (length(mime_type) > 0)
            );

            CREATE INDEX IF NOT EXISTS idx_transcript_links_episode
                ON episode_transcript_links(episode_id);
            CREATE INDEX IF NOT EXISTS idx_transcript_links_mime_type
                ON episode_transcript_links(mime_type);
            CREATE INDEX IF NOT EXISTS idx_transcript_links_not_downloaded
                ON episode_transcript_links(episode_id)
                WHERE downloaded_path IS NULL;

            -- ========================================================================
            -- USERS TABLE (Authentication)
            -- ========================================================================
            -- Supports single-user mode (default user) and multi-user mode (Google OAuth)
            CREATE TABLE IF NOT EXISTS users (
                id TEXT PRIMARY KEY NOT NULL,
                email TEXT NOT NULL UNIQUE,
                name TEXT NULL,
                picture TEXT NULL,
                google_id TEXT UNIQUE,
                created_at TIMESTAMP NOT NULL DEFAULT CURRENT_TIMESTAMP,
                last_login_at TIMESTAMP NULL,
                region TEXT NULL,
                region_locked INTEGER NOT NULL DEFAULT 0,
                is_admin INTEGER NOT NULL DEFAULT 0,
                CHECK (length(id) = 36),
                CHECK (length(email) > 0),
                CHECK (region IS NULL OR length(region) = 2),
                CHECK (region_locked IN (0, 1)),
                CHECK (is_admin IN (0, 1))
            );

            CREATE INDEX IF NOT EXISTS idx_users_email ON users(email);
            CREATE INDEX IF NOT EXISTS idx_users_google_id ON users(google_id) WHERE google_id IS NOT NULL;

            -- ========================================================================
            -- REVOKED TOKENS TABLE (spec #25 item 4.2 — JWT revocation deny-list)
            -- ========================================================================
            -- Every issued JWT carries a `jti`; on logout the jti lands here
            -- with the token's original `exp`. Auth verification rejects any
            -- token whose jti appears here. The expires_at column lets us
            -- prune entries that have aged past their original expiry — a
            -- revoked-then-expired token is rejected by the signature check
            -- anyway, so keeping the row would just bloat the table.
            CREATE TABLE IF NOT EXISTS revoked_tokens (
                jti TEXT PRIMARY KEY NOT NULL,
                expires_at TIMESTAMP NOT NULL,
                revoked_at TIMESTAMP NOT NULL DEFAULT CURRENT_TIMESTAMP
            );
            CREATE INDEX IF NOT EXISTS idx_revoked_tokens_expires_at
                ON revoked_tokens(expires_at);

            -- ========================================================================
            -- PODCAST FOLLOWERS TABLE (User-Podcast following relationship)
            -- ========================================================================
            -- Many-to-many relationship: users follow podcasts
            -- Podcasts are shared resources; processing happens once, delivered to many
            CREATE TABLE IF NOT EXISTS podcast_followers (
                id TEXT PRIMARY KEY NOT NULL,
                user_id TEXT NOT NULL,
                podcast_id TEXT NOT NULL,
                created_at TIMESTAMP NOT NULL DEFAULT CURRENT_TIMESTAMP,
                FOREIGN KEY (user_id) REFERENCES users(id) ON DELETE CASCADE,
                FOREIGN KEY (podcast_id) REFERENCES podcasts(id) ON DELETE CASCADE,
                UNIQUE(user_id, podcast_id),
                CHECK (length(id) = 36)
            );

            -- Index for "get podcasts user follows" query
            CREATE INDEX IF NOT EXISTS idx_podcast_followers_user
                ON podcast_followers(user_id);

            -- Index for "get followers of podcast" query
            CREATE INDEX IF NOT EXISTS idx_podcast_followers_podcast
                ON podcast_followers(podcast_id);

        """
        )

    @contextmanager
    def _get_connection(self) -> sqlite3.Connection:
        """
        Get database connection with proper setup.

        Features:
        - Row factory for dict-like access
        - Foreign keys enabled
        - WAL journal mode + ``busy_timeout=5000`` so writes from this
          repo participate in the same concurrency story as the entity
          repo and the queue manager. Without the per-connection
          ``busy_timeout`` PRAGMA, contended ``BEGIN IMMEDIATE`` from
          this repo would fail-fast with ``database is locked`` while
          peers with a timeout serialize gracefully.
        - Automatic commit/rollback
        - sqlite-vec loaded when available, so cascade DELETEs from
          ``episodes`` to ``chunks`` can fire the ``chunks_ad`` trigger
          that touches the ``chunks_vec`` virtual table without
          ``no such module: vec0``
        """
        from ..utils.sqlite_ext import connect

        with connect(self.db_path, load_vec="soft") as conn:
            yield conn

    @contextmanager
    def transaction(self):
        """
        Explicit transaction context manager.

        Usage:
            with repository.transaction():
                repository.save(podcast1)
                repository.save(podcast2)
                # Atomic: all or nothing
        """
        with self._get_connection() as conn:
            conn.execute("BEGIN TRANSACTION")
            try:
                yield conn
                conn.commit()
            except Exception:
                conn.rollback()
                raise

    # ============================================================================
    # PodcastRepository Interface Implementation
    # ============================================================================

    def get_podcasts_for_refresh(self) -> Tuple[List[Podcast], Dict[str, Set[str]]]:
        """Lightweight refresh loader (spec #19).

        Replaces ``get_all()`` on the refresh hot path. Two queries
        total: one for all podcasts (no episode hydration), one for
        every ``(podcast_id, external_id)`` pair. The returned dict is
        used by the fetch-episodes filter for in-memory dedup, so the
        refresh loop never needs the full Episode models — it just
        needs to know which externals are already tracked.

        Skips synthetic parents and auto_added podcasts that no user
        follows — auto-imports shouldn't drive recurring feed polls until
        someone explicitly subscribes.

        Returns:
            ``(podcasts, known_external_ids_by_podcast)`` where each
            ``Podcast`` has an empty ``episodes`` list and the dict maps
            ``podcast_id`` to the set of known ``external_id`` values.
            A podcast with no tracked episodes has no key in the dict.
        """
        with self._get_connection() as conn:
            cursor = conn.execute(
                """
                SELECT p.id, p.created_at, p.rss_url, p.title, p.slug, p.description, p.image_url, p.language,
                       p.primary_category_id, p.secondary_category_id,
                       p.author, p.explicit, p.show_type, p.website_url, p.is_complete, p.copyright,
                       p.last_processed, p.last_processed_at, p.etag, p.last_modified, p.updated_at
                FROM podcasts p
                WHERE p.synthetic = 0
                  AND (p.auto_added = 0
                       OR EXISTS (SELECT 1 FROM podcast_followers pf WHERE pf.podcast_id = p.id))
                ORDER BY p.created_at DESC
                """
            )
            podcast_rows = cursor.fetchall()

            dedup: Dict[str, Set[str]] = {}
            for ext_row in conn.execute("SELECT podcast_id, external_id FROM episodes"):
                dedup.setdefault(ext_row["podcast_id"], set()).add(ext_row["external_id"])

        podcasts: List[Podcast] = []
        for row in podcast_rows:
            explicit = None
            if row["explicit"] is not None:
                explicit = row["explicit"] == 1
            primary_top, primary_sub = self._resolve_category_id_to_pair(row["primary_category_id"])
            secondary_top, secondary_sub = self._resolve_category_id_to_pair(row["secondary_category_id"])
            podcasts.append(
                Podcast(
                    id=row["id"],
                    created_at=datetime.fromisoformat(row["created_at"]),
                    rss_url=row["rss_url"],
                    title=row["title"],
                    slug=row["slug"] or "",
                    description=row["description"],
                    image_url=row["image_url"],
                    language=row["language"] if row["language"] else "en",
                    primary_category=primary_top,
                    primary_subcategory=primary_sub,
                    secondary_category=secondary_top,
                    secondary_subcategory=secondary_sub,
                    author=row["author"],
                    explicit=explicit,
                    show_type=row["show_type"],
                    website_url=row["website_url"],
                    is_complete=row["is_complete"] == 1 if row["is_complete"] is not None else False,
                    copyright=row["copyright"],
                    last_processed=datetime.fromisoformat(row["last_processed"]) if row["last_processed"] else None,
                    last_processed_at=_row_opt_dt(row, "last_processed_at"),
                    etag=row["etag"],
                    last_modified=row["last_modified"],
                    episodes=[],
                )
            )
        return podcasts, dedup

    def get_podcast_for_refresh(self, podcast_id: str) -> Optional[Tuple[Podcast, Set[str]]]:
        """Single-feed analogue of :meth:`get_podcasts_for_refresh` (spec #48).

        Loads one podcast (cache headers + watermark, episodes left empty) plus
        the set of its known ``external_id`` values — exactly the inputs
        ``_refresh_single_podcast`` expects. Returns ``None`` if not found.
        """
        with self._get_connection() as conn:
            row = conn.execute(
                """
                SELECT p.id, p.created_at, p.rss_url, p.title, p.slug, p.description, p.image_url, p.language,
                       p.primary_category_id, p.secondary_category_id,
                       p.author, p.explicit, p.show_type, p.website_url, p.is_complete, p.copyright,
                       p.last_processed, p.last_processed_at, p.etag, p.last_modified, p.updated_at
                FROM podcasts p WHERE p.id = ?
                """,
                (podcast_id,),
            ).fetchone()
            if row is None:
                return None
            known = {
                r["external_id"]
                for r in conn.execute("SELECT external_id FROM episodes WHERE podcast_id = ?", (podcast_id,))
            }

        explicit = None
        if row["explicit"] is not None:
            explicit = row["explicit"] == 1
        primary_top, primary_sub = self._resolve_category_id_to_pair(row["primary_category_id"])
        secondary_top, secondary_sub = self._resolve_category_id_to_pair(row["secondary_category_id"])
        podcast = Podcast(
            id=row["id"],
            created_at=datetime.fromisoformat(row["created_at"]),
            rss_url=row["rss_url"],
            title=row["title"],
            slug=row["slug"] or "",
            description=row["description"],
            image_url=row["image_url"],
            language=row["language"] if row["language"] else "en",
            primary_category=primary_top,
            primary_subcategory=primary_sub,
            secondary_category=secondary_top,
            secondary_subcategory=secondary_sub,
            author=row["author"],
            explicit=explicit,
            show_type=row["show_type"],
            website_url=row["website_url"],
            is_complete=row["is_complete"] == 1 if row["is_complete"] is not None else False,
            copyright=row["copyright"],
            last_processed=datetime.fromisoformat(row["last_processed"]) if row["last_processed"] else None,
            last_processed_at=_row_opt_dt(row, "last_processed_at"),
            etag=row["etag"],
            last_modified=row["last_modified"],
            episodes=[],
        )
        return podcast, known

    def get_top_podcast_regions(self) -> List[str]:
        """Return the list of regions that currently have top-podcast data."""
        with self._get_connection() as conn:
            cursor = conn.execute("SELECT region FROM top_podcasts_meta ORDER BY region")
            return [row["region"] for row in cursor.fetchall()]

    def get_top_podcast_categories(self, region: str) -> List[str]:
        """Return the distinct **top-level** category names in a region's chart.

        Chart entries can be tagged with either a top-level category (Comedy)
        or a sub-category (Comedy Interviews); we roll sub-categories up to
        their parent so the UI matches Apple's primary category browser.

        Sorted alphabetically, ``NULL``-categories suppressed. The list is
        computed from the *unfiltered* ranking so the picker doesn't shrink
        when the user applies a search or category filter.
        """
        if not region:
            return []
        with self._get_connection() as conn:
            cursor = conn.execute(
                """
                SELECT DISTINCT COALESCE(parent.name, c.name) AS name
                FROM top_podcast_rankings r
                JOIN top_podcasts p ON p.id = r.top_podcast_id
                JOIN categories c ON c.id = p.category_id
                LEFT JOIN categories parent ON parent.id = c.parent_id
                WHERE r.region = ? AND COALESCE(parent.name, c.name) IS NOT NULL
                ORDER BY name COLLATE NOCASE
                """,
                (region.lower(),),
            )
            return [row["name"] for row in cursor.fetchall()]

    def get_chunks_health(self) -> Tuple[int, str]:
        """Spec #28 §2.10 — chunk row count + dominant embedding model.

        Returns ``(0, "")`` when the ``chunks`` table doesn't exist
        (deployments without the ``[entities]`` extra) or is empty.
        """
        with self._get_connection() as conn:
            try:
                row = conn.execute(
                    "SELECT COUNT(*) AS n, "
                    "(SELECT embedding_model FROM chunks GROUP BY embedding_model "
                    "ORDER BY COUNT(*) DESC LIMIT 1) AS model "
                    "FROM chunks"
                ).fetchone()
            except sqlite3.OperationalError:
                return 0, ""
        if row is None:
            return 0, ""
        return int(row["n"] or 0), row["model"] or ""

    def count_episodes_skipped_legacy(self) -> int:
        """Spec #28 Phase 3.4 — episodes the entity branch declined to process.

        An episode is ``skipped_legacy`` when ``extract-entities`` ran but
        the episode lacked a structured ``AnnotatedTranscript`` JSON
        sidecar (legacy Markdown-only cleaning). Surfacing the count in
        ``thestill status`` makes the size of the legacy backlog visible
        without scraping logs.

        Returns 0 on databases that predate the spec #28 Phase 1
        ``entity_extraction_status`` migration — same defensiveness
        ``get_chunks_health`` uses for the corpus chunks table.
        """
        with self._get_connection() as conn:
            try:
                row = conn.execute(
                    "SELECT COUNT(*) AS n FROM episodes " "WHERE entity_extraction_status = 'skipped_legacy'"
                ).fetchone()
            except sqlite3.OperationalError:
                return 0
        return int(row["n"] or 0)

    def get_all(self) -> List[Podcast]:
        """Retrieve all podcasts with their episodes."""
        with self._get_connection() as conn:
            # Fetch all podcasts
            cursor = conn.execute(
                """
                SELECT id, created_at, rss_url, title, slug, description, image_url, language,
                       primary_category_id, secondary_category_id,
                       author, explicit, show_type, website_url, is_complete, copyright,
                       last_processed, last_processed_at, etag, last_modified, updated_at
                FROM podcasts
                ORDER BY created_at DESC
            """
            )

            podcasts = []
            for row in cursor.fetchall():
                podcast = self._row_to_podcast(row, conn)
                podcasts.append(podcast)

            return podcasts

    def get(self, podcast_id: str) -> Optional[Podcast]:
        """Get podcast by internal UUID (primary key)."""
        with self._get_connection() as conn:
            cursor = conn.execute(
                """
                SELECT id, created_at, rss_url, title, slug, description, image_url, language,
                       primary_category_id, secondary_category_id,
                       author, explicit, show_type, website_url, is_complete, copyright,
                       last_processed, last_processed_at, etag, last_modified, updated_at
                FROM podcasts
                WHERE id = ?
            """,
                (podcast_id,),
            )

            row = cursor.fetchone()
            if row:
                return self._row_to_podcast(row, conn)
            return None

    def get_by_id(self, podcast_id: str) -> Optional[Podcast]:
        """Find podcast by internal UUID."""
        with self._get_connection() as conn:
            cursor = conn.execute(
                """
                SELECT id, created_at, rss_url, title, slug, description, image_url, language,
                       primary_category_id, secondary_category_id,
                       author, explicit, show_type, website_url, is_complete, copyright,
                       last_processed, last_processed_at, etag, last_modified, updated_at
                FROM podcasts
                WHERE id = ?
            """,
                (podcast_id,),
            )

            row = cursor.fetchone()
            if row:
                return self._row_to_podcast(row, conn)
            return None

    def get_by_url(self, url: str) -> Optional[Podcast]:
        """Find podcast by RSS URL."""
        with self._get_connection() as conn:
            cursor = conn.execute(
                """
                SELECT id, created_at, rss_url, title, slug, description, image_url, language,
                       primary_category_id, secondary_category_id,
                       author, explicit, show_type, website_url, is_complete, copyright,
                       last_processed, last_processed_at, etag, last_modified, updated_at
                FROM podcasts
                WHERE rss_url = ?
            """,
                (url,),
            )

            row = cursor.fetchone()
            if row:
                return self._row_to_podcast(row, conn)
            return None

    def get_by_index(self, index: int) -> Optional[Podcast]:
        """Find podcast by 1-based index."""
        if index < 1:  # Invalid index (must be 1-based)
            return None

        with self._get_connection() as conn:
            cursor = conn.execute(
                """
                SELECT id, created_at, rss_url, title, slug, description, image_url, language,
                       primary_category_id, secondary_category_id,
                       author, explicit, show_type, website_url, is_complete, copyright,
                       last_processed, last_processed_at, etag, last_modified, updated_at
                FROM podcasts
                ORDER BY created_at DESC
                LIMIT 1 OFFSET ?
            """,
                (index - 1,),
            )

            row = cursor.fetchone()
            if row:
                return self._row_to_podcast(row, conn)
            return None

    def get_by_slug(self, slug: str) -> Optional[Podcast]:
        """Find podcast by URL-safe slug."""
        if not slug:
            return None

        with self._get_connection() as conn:
            cursor = conn.execute(
                """
                SELECT id, created_at, rss_url, title, slug, description, image_url, language,
                       primary_category_id, secondary_category_id,
                       author, explicit, show_type, website_url, is_complete, copyright,
                       last_processed, last_processed_at, etag, last_modified, updated_at
                FROM podcasts
                WHERE slug = ?
            """,
                (slug,),
            )

            row = cursor.fetchone()
            if row:
                return self._row_to_podcast(row, conn)
            return None

    def exists(self, url: str) -> bool:
        """Check if podcast exists."""
        with self._get_connection() as conn:
            cursor = conn.execute(
                """
                SELECT 1 FROM podcasts WHERE rss_url = ? LIMIT 1
            """,
                (url,),
            )
            return cursor.fetchone() is not None

    def save(self, podcast: Podcast) -> Podcast:
        """
        Save or update podcast with ALL episodes (destructive).

        WARNING: This method DELETES all existing episodes and re-inserts them.
        Use save_podcast() + save_episode()/save_episodes() for targeted updates.

        Strategy: UPSERT podcast, then DELETE + INSERT all episodes
        Side effects: updated_at set on podcast and ALL episodes
        """
        with self._get_connection() as conn:
            now = datetime.now(timezone.utc)

            # Resolve string categories on the model into FK ids before write.
            primary_cat_id = self._resolve_category_strings_to_id(podcast.primary_category, podcast.primary_subcategory)
            secondary_cat_id = self._resolve_category_strings_to_id(
                podcast.secondary_category, podcast.secondary_subcategory
            )

            # Upsert podcast
            conn.execute(
                """
                INSERT INTO podcasts (id, created_at, updated_at, rss_url, title, slug, description, image_url, language,
                                      primary_category_id, secondary_category_id,
                                      author, explicit, show_type, website_url, is_complete, copyright, last_processed)
                VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                ON CONFLICT(rss_url) DO UPDATE SET
                    title = excluded.title,
                    slug = excluded.slug,
                    description = excluded.description,
                    image_url = excluded.image_url,
                    language = excluded.language,
                    primary_category_id = excluded.primary_category_id,
                    secondary_category_id = excluded.secondary_category_id,
                    author = excluded.author,
                    explicit = excluded.explicit,
                    show_type = excluded.show_type,
                    website_url = excluded.website_url,
                    is_complete = excluded.is_complete,
                    copyright = excluded.copyright,
                    last_processed = excluded.last_processed,
                    updated_at = ?
            """,
                (
                    podcast.id,
                    podcast.created_at.isoformat(),
                    now.isoformat(),
                    str(podcast.rss_url),
                    podcast.title,
                    podcast.slug,
                    podcast.description,
                    _normalize_artwork_url(podcast.image_url),
                    podcast.language,
                    primary_cat_id,
                    secondary_cat_id,
                    podcast.author,
                    1 if podcast.explicit is True else (0 if podcast.explicit is False else None),
                    podcast.show_type,
                    podcast.website_url,
                    1 if podcast.is_complete else 0,
                    podcast.copyright,
                    podcast.last_processed.isoformat() if podcast.last_processed else None,
                    now.isoformat(),  # Set updated_at explicitly (no trigger)
                ),
            )

            # Get final podcast_id (in case URL already existed)
            cursor = conn.execute("SELECT id FROM podcasts WHERE rss_url = ?", (str(podcast.rss_url),))
            podcast_id = cursor.fetchone()["id"]

            # Delete existing episodes (simpler than complex merge logic)
            # Note: No CASCADE - we explicitly delete here
            conn.execute("DELETE FROM episodes WHERE podcast_id = ?", (podcast_id,))

            # Insert all episodes
            for episode in podcast.episodes:
                self._save_episode(conn, podcast_id, episode, now)

            logger.debug(f"Saved podcast: {podcast.title} ({len(podcast.episodes)} episodes)")
            return podcast

    def touch_last_processed_at(self, podcast_id: str, when: datetime) -> None:
        """Record the wall-clock time an episode was last processed.

        Targeted single-column UPDATE so it can never clobber the discovery
        watermark (``last_processed``) — keeping the two semantics fully
        separate. Safe to call inside a feed-manager transaction: it's an
        immediate write and the column is untouched by the deferred metadata
        saves, so the value survives the later flush.
        """
        with self._get_connection() as conn:
            conn.execute(
                "UPDATE podcasts SET last_processed_at = ? WHERE id = ?",
                (when.isoformat(), podcast_id),
            )

    def save_podcast(self, podcast: Podcast) -> Podcast:
        """
        Save or update podcast metadata only. Does NOT touch episodes.

        Idempotent: Only updates updated_at if data actually changed.

        Args:
            podcast: Podcast model with metadata to save

        Returns:
            The saved podcast (with updated timestamps if changed)
        """
        with self._get_connection() as conn:
            now = datetime.now(timezone.utc)

            # Check if podcast exists and if data changed
            cursor = conn.execute(
                """
                SELECT id, title, slug, description, image_url, language,
                       primary_category_id, secondary_category_id,
                       author, explicit, show_type, website_url, is_complete, copyright,
                       last_processed, etag, last_modified
                FROM podcasts WHERE rss_url = ?
                """,
                (str(podcast.rss_url),),
            )
            existing = cursor.fetchone()

            primary_cat_id = self._resolve_category_strings_to_id(podcast.primary_category, podcast.primary_subcategory)
            secondary_cat_id = self._resolve_category_strings_to_id(
                podcast.secondary_category, podcast.secondary_subcategory
            )

            if existing:
                # Compare fields to see if anything changed
                last_processed_str = podcast.last_processed.isoformat() if podcast.last_processed else None
                existing_last_processed = existing["last_processed"]
                explicit_int = 1 if podcast.explicit is True else (0 if podcast.explicit is False else None)
                normalized_image_url = _normalize_artwork_url(podcast.image_url)

                changed = (
                    existing["title"] != podcast.title
                    or existing["slug"] != podcast.slug
                    or existing["description"] != podcast.description
                    or existing["image_url"] != normalized_image_url
                    or existing["language"] != podcast.language
                    or existing["primary_category_id"] != primary_cat_id
                    or existing["secondary_category_id"] != secondary_cat_id
                    or existing["author"] != podcast.author
                    or existing["explicit"] != explicit_int
                    or existing["show_type"] != podcast.show_type
                    or existing["website_url"] != podcast.website_url
                    or existing["is_complete"] != (1 if podcast.is_complete else 0)
                    or existing["copyright"] != podcast.copyright
                    or existing_last_processed != last_processed_str
                    or existing["etag"] != podcast.etag
                    or existing["last_modified"] != podcast.last_modified
                )

                if changed:
                    # Update with new updated_at
                    conn.execute(
                        """
                        UPDATE podcasts
                        SET title = ?, slug = ?, description = ?, image_url = ?, language = ?,
                            primary_category_id = ?, secondary_category_id = ?,
                            author = ?, explicit = ?, show_type = ?, website_url = ?, is_complete = ?, copyright = ?,
                            last_processed = ?, etag = ?, last_modified = ?, updated_at = ?
                        WHERE rss_url = ?
                        """,
                        (
                            podcast.title,
                            podcast.slug,
                            podcast.description,
                            normalized_image_url,
                            podcast.language,
                            primary_cat_id,
                            secondary_cat_id,
                            podcast.author,
                            explicit_int,
                            podcast.show_type,
                            podcast.website_url,
                            1 if podcast.is_complete else 0,
                            podcast.copyright,
                            last_processed_str,
                            podcast.etag,
                            podcast.last_modified,
                            now.isoformat(),
                            str(podcast.rss_url),
                        ),
                    )
                    logger.debug(f"Updated podcast metadata: {podcast.title}")
                else:
                    logger.debug(f"Podcast metadata unchanged: {podcast.title}")
            else:
                # Insert new podcast
                conn.execute(
                    """
                    INSERT INTO podcasts (id, created_at, updated_at, rss_url, title, slug, description, image_url, language,
                                          primary_category_id, secondary_category_id,
                                          author, explicit, show_type, website_url, is_complete, copyright,
                                          last_processed, etag, last_modified)
                    VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                    """,
                    (
                        podcast.id,
                        podcast.created_at.isoformat(),
                        now.isoformat(),
                        str(podcast.rss_url),
                        podcast.title,
                        podcast.slug,
                        podcast.description,
                        _normalize_artwork_url(podcast.image_url),
                        podcast.language,
                        primary_cat_id,
                        secondary_cat_id,
                        podcast.author,
                        1 if podcast.explicit is True else (0 if podcast.explicit is False else None),
                        podcast.show_type,
                        podcast.website_url,
                        1 if podcast.is_complete else 0,
                        podcast.copyright,
                        podcast.last_processed.isoformat() if podcast.last_processed else None,
                        podcast.etag,
                        podcast.last_modified,
                    ),
                )
                logger.debug(f"Inserted new podcast: {podcast.title}")

            return podcast

    def save_episode(self, episode: Episode) -> Episode:
        """
        Save or update a single episode.

        Idempotent: Only updates updated_at if data actually changed.
        Requires: episode.podcast_id must be set.

        Args:
            episode: Episode model to save

        Returns:
            The saved episode

        Raises:
            ValueError: If episode.podcast_id is not set
        """
        if not episode.podcast_id:
            raise ValueError("episode.podcast_id must be set before saving")

        with self._get_connection() as conn:
            return self._save_episode_idempotent(conn, episode)

    def save_episodes(self, episodes: List[Episode]) -> List[Episode]:
        """
        Save or update multiple episodes in a single transaction.

        Idempotent: Only updates updated_at for episodes with actual changes.
        Requires: Each episode.podcast_id must be set.

        Args:
            episodes: List of Episode models to save

        Returns:
            List of saved episodes

        Raises:
            ValueError: If any episode.podcast_id is not set
        """
        if not episodes:
            return []

        # Validate all episodes have podcast_id
        for ep in episodes:
            if not ep.podcast_id:
                raise ValueError(f"episode.podcast_id must be set for episode: {ep.title}")

        with self._get_connection() as conn:
            return [self._save_episode_idempotent(conn, ep) for ep in episodes]

    def save_refresh_batch(
        self,
        changed_podcasts: List[Podcast],
        new_episodes: List[Episode],
        episode_image_updates: Optional[List[Tuple[str, str, Optional[str]]]] = None,
        episode_audio_updates: Optional[List[Tuple[str, str, str]]] = None,
    ) -> None:
        """
        Commit one refresh's worth of state in a single transaction (spec #19).

        Avoids the N+1 pattern of calling ``save_podcast`` / ``save_episodes``
        once per podcast (each opens its own connection + commits). Expects
        ``new_episodes`` to already be deduped against the DB — the refresh
        loop filters against the loaded ``podcast.episodes`` list before
        queueing, and the INSERT relies on the ``UNIQUE(podcast_id,
        external_id)`` constraint as a defensive backstop via
        ``INSERT OR IGNORE``.

        Args:
            changed_podcasts: Podcasts whose bookkeeping changed — metadata,
                ``last_processed``, or conditional-GET cache headers.
            new_episodes: Newly discovered episodes to insert. Must each
                carry ``podcast_id``.
            episode_image_updates: Optional ``(podcast_id, external_id,
                image_url)`` triples re-syncing existing episodes' artwork from
                the feed. Applied as a guarded UPDATE so only drifted rows write
                (rotating signed URLs go stale because the INSERT above never
                revisits an existing episode). New episodes inserted in this same
                batch already carry the current URL, so their update is a no-op.
            episode_audio_updates: Optional ``(podcast_id, external_id,
                audio_url)`` triples re-syncing existing episodes' enclosure
                URLs (hosts like BBC re-publish audio under a new URL for the
                same GUID, so the stored URL 404s before the episode is
                fetched). Guarded the same way, and additionally scoped to
                rows whose audio hasn't been downloaded or transcribed yet —
                feeds that rotate enclosure URLs on every fetch must not churn
                already-processed episodes.
        """
        if not changed_podcasts and not new_episodes and not episode_image_updates and not episode_audio_updates:
            return

        for ep in new_episodes:
            if not ep.podcast_id:
                raise ValueError(f"episode.podcast_id must be set for episode: {ep.title}")

        now_iso = datetime.now(timezone.utc).isoformat()
        with self._get_connection() as conn:
            # Blind UPDATE keyed by id — the refresh loop already chose
            # these rows to write, so we skip the read-then-diff of
            # ``save_podcast``.
            podcast_params = [
                (
                    p.title,
                    p.slug,
                    p.description,
                    _normalize_artwork_url(p.image_url),
                    p.language,
                    self._resolve_category_strings_to_id(p.primary_category, p.primary_subcategory),
                    self._resolve_category_strings_to_id(p.secondary_category, p.secondary_subcategory),
                    p.author,
                    1 if p.explicit is True else (0 if p.explicit is False else None),
                    p.show_type,
                    p.website_url,
                    1 if p.is_complete else 0,
                    p.copyright,
                    p.last_processed.isoformat() if p.last_processed else None,
                    p.etag,
                    p.last_modified,
                    now_iso,
                    p.id,
                )
                for p in changed_podcasts
            ]
            if podcast_params:
                conn.executemany(
                    """
                    UPDATE podcasts
                    SET title = ?, slug = ?, description = ?, image_url = ?, language = ?,
                        primary_category_id = ?, secondary_category_id = ?,
                        author = ?, explicit = ?, show_type = ?, website_url = ?, is_complete = ?, copyright = ?,
                        last_processed = ?, etag = ?, last_modified = ?, updated_at = ?
                    WHERE id = ?
                    """,
                    podcast_params,
                )

            # Refresh only discovers brand-new episodes, so INSERT OR
            # IGNORE defends against the rare concurrent-refresh race on
            # ``(podcast_id, external_id)``.
            episode_params = [
                (
                    ep.id,
                    ep.podcast_id,
                    ep.created_at.isoformat(),
                    now_iso,
                    ep.external_id,
                    ep.title,
                    ep.slug,
                    ep.description,
                    ep.description_html,
                    ep.pub_date.isoformat() if ep.pub_date else None,
                    str(ep.audio_url),
                    ep.duration,
                    _normalize_artwork_url(ep.image_url),
                    1 if ep.explicit is True else (0 if ep.explicit is False else None),
                    ep.episode_type,
                    ep.episode_number,
                    ep.season_number,
                    ep.website_url,
                    ep.audio_file_size,
                    ep.audio_mime_type,
                    ep.audio_path,
                    ep.downsampled_audio_path,
                    ep.raw_transcript_path,
                    ep.clean_transcript_path,
                    ep.clean_transcript_json_path,
                    ep.summary_path,
                    ep.playback_time_offset_seconds,
                )
                for ep in new_episodes
            ]
            if episode_params:
                conn.executemany(
                    """
                    INSERT OR IGNORE INTO episodes (
                        id, podcast_id, created_at, updated_at, external_id, title, slug, description,
                        description_html, pub_date, audio_url, duration, image_url,
                        explicit, episode_type, episode_number, season_number, website_url,
                        audio_file_size, audio_mime_type,
                        audio_path, downsampled_audio_path, raw_transcript_path, clean_transcript_path,
                        clean_transcript_json_path, summary_path, playback_time_offset_seconds
                    ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                    """,
                    episode_params,
                )

            # Re-sync drifted artwork for existing episodes (stale signed-URL
            # repair). Keyed by (podcast_id, external_id) so no episode
            # hydration is needed; the ``IS NOT`` guard keeps it a no-op — and
            # leaves ``updated_at`` untouched — unless the URL actually changed.
            # New episodes inserted just above already carry the current URL, so
            # their update here is a guarded no-op.
            if episode_image_updates:
                image_params = [
                    (
                        _normalize_artwork_url(image_url),
                        now_iso,
                        podcast_id,
                        external_id,
                        _normalize_artwork_url(image_url),
                    )
                    for podcast_id, external_id, image_url in episode_image_updates
                ]
                conn.executemany(
                    """
                    UPDATE episodes
                    SET image_url = ?, updated_at = ?
                    WHERE podcast_id = ? AND external_id = ? AND image_url IS NOT ?
                    """,
                    image_params,
                )

            # Re-sync drifted enclosure URLs for existing episodes that still
            # need their audio. The ``audio_path IS NULL AND
            # raw_transcript_path IS NULL`` scope keeps feeds that rotate
            # enclosure URLs on every fetch from churning already-processed
            # rows; the ``IS NOT`` guard keeps unchanged rows — and their
            # ``updated_at`` — untouched.
            if episode_audio_updates:
                audio_params = [
                    (
                        audio_url,
                        now_iso,
                        podcast_id,
                        external_id,
                        audio_url,
                    )
                    for podcast_id, external_id, audio_url in episode_audio_updates
                ]
                conn.executemany(
                    """
                    UPDATE episodes
                    SET audio_url = ?, updated_at = ?
                    WHERE podcast_id = ? AND external_id = ? AND audio_url IS NOT ?
                      AND audio_path IS NULL AND raw_transcript_path IS NULL
                    """,
                    audio_params,
                )

    def update_episode_image_urls(self, updates: List[Tuple[str, Optional[str]]]) -> int:
        """Update ``image_url`` for existing episodes in one transaction.

        Used by the image-repair routine: ``refresh`` discovers episodes with
        ``INSERT OR IGNORE`` and never revisits an existing row, so artwork that
        the feed later re-signs (e.g. Transistor's imgproxy URLs, whose
        signatures rotate and start 404ing) goes stale forever. This re-syncs
        the stored URL from the live feed.

        Args:
            updates: ``(episode_id, image_url)`` pairs. ``image_url`` is
                normalized (``http`` -> ``https``) before storage. The
                ``WHERE`` guard skips no-op writes so ``updated_at`` only moves
                when the value actually changed.

        Returns:
            Number of rows actually changed.
        """
        if not updates:
            return 0
        now_iso = datetime.now(timezone.utc).isoformat()
        params = [
            (_normalize_artwork_url(image_url), now_iso, episode_id, _normalize_artwork_url(image_url))
            for episode_id, image_url in updates
        ]
        with self._get_connection() as conn:
            before = conn.total_changes
            conn.executemany(
                """
                UPDATE episodes
                SET image_url = ?, updated_at = ?
                WHERE id = ? AND image_url IS NOT ?
                """,
                params,
            )
            return conn.total_changes - before

    # ------------------------------------------------------------------
    # Spec #48 — background refresh scheduling (cadence + failure state)
    # ------------------------------------------------------------------
    def get_due_podcasts(self, now: Optional[datetime] = None, limit: int = 500) -> List[str]:
        """Return ids of feeds DUE for refresh, oldest-due first.

        Due = scheduled (``next_refresh_at IS NOT NULL`` — parked feeds are
        excluded) and ``next_refresh_at <= now``, restricted to active feeds
        (non-synthetic, ongoing). The explicit ``IS NOT NULL`` makes the
        parked-vs-future distinction clear even though ``NULL <= now`` is NULL.
        """
        now_iso = (now or now_utc()).isoformat()
        with self._get_connection() as conn:
            rows = conn.execute(
                """
                SELECT id FROM podcasts
                WHERE next_refresh_at IS NOT NULL
                  AND next_refresh_at <= ?
                  AND COALESCE(synthetic, 0) = 0
                  AND COALESCE(is_complete, 0) = 0
                  AND (COALESCE(auto_added, 0) = 0
                       OR EXISTS (SELECT 1 FROM podcast_followers pf WHERE pf.podcast_id = podcasts.id))
                ORDER BY next_refresh_at ASC
                LIMIT ?
                """,
                (now_iso, limit),
            ).fetchall()
            return [row["id"] for row in rows]

    def get_discovered_unqueued_episodes(
        self, podcast_id: str, within_days: int = 2, limit: int = 25
    ) -> List[Tuple[str, Optional[str]]]:
        """Spec #48 P1 — RECENT episodes persisted but never enqueued (orphans).

        ``handle_refresh_feed`` persists new episodes, then enqueues their first
        task in a *separate* write. If the process dies (or ``add_task`` fails)
        in between, the episodes are durable but have no task; the next refresh
        reloads their external_ids as "known", so ``new_eps`` is empty and the
        fan-out is never repaired. This query is the idempotent recovery: an
        episode in DISCOVERED state (no artifact paths, not failed) with **no
        task row at all** is an orphan. The handler enqueues these every run, so
        a healthy run just re-finds the episodes it persisted this cycle, and a
        crashed prior run is self-healed on the next tick.

        Scoped to ``within_days`` (default 2): refresh runs at most every ~24h,
        so a genuine crash-orphan is always recent. This deliberately EXCLUDES
        the historical "discovered but never processed" backlog (pre-spec-48
        episodes the user never queued for processing) — enqueuing those would
        be a surprise flood, not a repair. Bounded by ``limit`` per call so even
        a burst drains gradually. Returns ``(episode_id, audio_url)`` pairs.
        """
        cutoff = (now_utc() - timedelta(days=within_days)).isoformat()
        with self._get_connection() as conn:
            rows = conn.execute(
                """
                SELECT e.id, e.audio_url
                FROM episodes e
                WHERE e.podcast_id = ?
                  AND e.created_at >= ?
                  AND e.failed_at_stage IS NULL
                  AND e.auto_process_excluded = 0
                  AND e.audio_path IS NULL
                  AND e.downsampled_audio_path IS NULL
                  AND e.raw_transcript_path IS NULL
                  AND e.clean_transcript_path IS NULL
                  AND e.summary_path IS NULL
                  AND NOT EXISTS (SELECT 1 FROM tasks t WHERE t.episode_id = e.id)
                ORDER BY e.pub_date DESC
                LIMIT ?
                """,
                (podcast_id, cutoff, limit),
            ).fetchall()
            return [(row["id"], row["audio_url"]) for row in rows]

    def get_unqueued_unprocessed_episodes(self, episode_ids: List[str]) -> List[Tuple[str, Optional[str]]]:
        """Filter ``episode_ids`` to those that still need the full pipeline.

        Returns ``(episode_id, audio_url)`` for each given episode that is an
        untouched DISCOVERED orphan — no artifact paths, not failed, and with no
        task row at all — using the same predicate as
        ``get_discovered_unqueued_episodes`` but scoped to a caller-supplied set
        (the inbox follow-seed / publish fan-out paths, which already know which
        episodes they delivered). Episodes that are already processing, done, or
        failed are dropped, so callers can enqueue the result unconditionally.

        Unlike the refresh-recovery query this is NOT time-windowed: a follow can
        legitimately seed an old-but-never-processed episode that should still be
        transcribed on demand.
        """
        if not episode_ids:
            return []
        placeholders = ",".join("?" for _ in episode_ids)
        with self._get_connection() as conn:
            rows = conn.execute(
                f"""
                SELECT e.id, e.audio_url
                FROM episodes e
                WHERE e.id IN ({placeholders})
                  AND e.failed_at_stage IS NULL
                  AND e.audio_path IS NULL
                  AND e.downsampled_audio_path IS NULL
                  AND e.raw_transcript_path IS NULL
                  AND e.clean_transcript_path IS NULL
                  AND e.summary_path IS NULL
                  AND NOT EXISTS (SELECT 1 FROM tasks t WHERE t.episode_id = e.id)
                """,
                tuple(episode_ids),
            ).fetchall()
            return [(row["id"], row["audio_url"]) for row in rows]

    def get_recent_unqueued_unprocessed_episodes(self, podcast_id: str, limit: int) -> List[Tuple[str, Optional[str]]]:
        """The podcast's ``limit`` most-recent un-started episodes, by air date.

        Returns ``(episode_id, audio_url)`` for the newest episodes (ordered by
        ``COALESCE(pub_date, published_at) DESC`` — the listener's notion of
        "recent", matching the inbox seed) that are still untouched orphans: no
        artifact paths, not failed, and with no task row.

        This drives the subscribe-time transcription backlog. Unlike
        ``recent_published_episode_ids`` it does NOT require ``published_at`` —
        a brand-new podcast's episodes are all unpublished until the pipeline
        runs, so gating on publish state would (wrongly) enqueue nothing. And
        unlike ``get_discovered_unqueued_episodes`` it is NOT time-windowed:
        following a podcast should transcribe its recent backlog even when the
        latest episode aired weeks ago.
        """
        if limit <= 0:
            return []
        with self._get_connection() as conn:
            rows = conn.execute(
                """
                SELECT e.id, e.audio_url
                FROM episodes e
                WHERE e.podcast_id = ?
                  AND e.failed_at_stage IS NULL
                  AND e.auto_process_excluded = 0
                  AND e.audio_path IS NULL
                  AND e.downsampled_audio_path IS NULL
                  AND e.raw_transcript_path IS NULL
                  AND e.clean_transcript_path IS NULL
                  AND e.summary_path IS NULL
                  AND NOT EXISTS (SELECT 1 FROM tasks t WHERE t.episode_id = e.id)
                ORDER BY COALESCE(e.pub_date, e.published_at) DESC
                LIMIT ?
                """,
                (podcast_id, limit),
            ).fetchall()
            return [(row["id"], row["audio_url"]) for row in rows]

    def has_processed_episodes(self, podcast_id: str) -> bool:
        """True once the podcast has at least one episode past discovery.

        "Processed" means any pipeline progress — an artifact path or a publish
        timestamp. Distinguishes a brand-new podcast still in its initial
        backfill (False → cap auto-transcription to the most-recent N) from an
        established one (True → auto-transcribe every newly-discovered episode).
        """
        with self._get_connection() as conn:
            row = conn.execute(
                """
                SELECT 1 FROM episodes
                WHERE podcast_id = ?
                  AND (published_at IS NOT NULL
                       OR raw_transcript_path IS NOT NULL
                       OR clean_transcript_path IS NOT NULL
                       OR summary_path IS NOT NULL)
                LIMIT 1
                """,
                (podcast_id,),
            ).fetchone()
            return row is not None

    def count_episodes_with_tasks(self, podcast_id: str) -> int:
        """Number of the podcast's episodes that have ever had a queue task.

        Used to size the initial-backfill cap: episodes already auto-submitted
        (e.g. by the follow-seed path) count against the "most-recent N" budget
        so seed + refresh-feed don't together exceed it.
        """
        with self._get_connection() as conn:
            row = conn.execute(
                """
                SELECT COUNT(*) AS n FROM episodes e
                WHERE e.podcast_id = ?
                  AND EXISTS (SELECT 1 FROM tasks t WHERE t.episode_id = e.id)
                """,
                (podcast_id,),
            ).fetchone()
            return row["n"] if row else 0

    def mark_episodes_auto_process_excluded(self, episode_ids: List[str]) -> int:
        """Flag ``episode_ids`` so the refresh-feed sweep never auto-enqueues them.

        Applied to a brand-new podcast's back catalog beyond the most-recent N.
        Idempotent. Returns the number of rows updated.
        """
        if not episode_ids:
            return 0
        placeholders = ",".join("?" for _ in episode_ids)
        with self._get_connection() as conn:
            cur = conn.execute(
                f"UPDATE episodes SET auto_process_excluded = 1 WHERE id IN ({placeholders})",
                tuple(episode_ids),
            )
            return cur.rowcount

    def seed_unscheduled_feeds(self, default_interval_seconds: int, now: Optional[datetime] = None) -> int:
        """Seed active feeds that have NEVER been scheduled or attempted.

        Distinguishes never-seeded (``next_refresh_at`` NULL *and* no prior
        attempt) from PARKED (terminally failed — carries ``last_refresh_error``
        / ``last_refresh_at``): only the former is seeded, so a parked feed is
        never silently revived. Lets newly-added podcasts become due without
        editing every insert path. Returns the number seeded.
        """
        now_dt = now or now_utc()
        with self._get_connection() as conn:
            rows = conn.execute(
                """
                SELECT id FROM podcasts
                WHERE next_refresh_at IS NULL
                  AND last_refresh_at IS NULL
                  AND last_refresh_error IS NULL
                  AND COALESCE(synthetic, 0) = 0
                  AND COALESCE(is_complete, 0) = 0
                  AND (COALESCE(auto_added, 0) = 0
                       OR EXISTS (SELECT 1 FROM podcast_followers pf WHERE pf.podcast_id = podcasts.id))
                """
            ).fetchall()
            for row in rows:
                pid = row["id"]
                offset = (hash(pid) % max(1, default_interval_seconds)) if default_interval_seconds > 0 else 0
                next_at = (now_dt + timedelta(seconds=offset)).isoformat()
                conn.execute(
                    "UPDATE podcasts SET refresh_interval_seconds = ?, next_refresh_at = ? WHERE id = ?",
                    (default_interval_seconds, next_at, pid),
                )
            if rows:
                logger.info("Seeded refresh schedule for unscheduled feeds", count=len(rows))
            return len(rows)

    def record_refresh_success(
        self,
        podcast_id: str,
        found_new: bool,
        min_interval: int,
        max_interval: int,
        default_interval: int,
        now: Optional[datetime] = None,
    ) -> str:
        """Record a successful refresh and recompute the adaptive (AIMD)
        interval. New episodes → shorten (÷2, decrease); none → lengthen
        (×1.5, increase). Clears ``last_refresh_error``. Returns the new
        ``next_refresh_at`` ISO string.
        """
        now_dt = now or now_utc()
        with self._get_connection() as conn:
            row = conn.execute(
                "SELECT refresh_interval_seconds FROM podcasts WHERE id = ?",
                (podcast_id,),
            ).fetchone()
            current = row["refresh_interval_seconds"] if row and row["refresh_interval_seconds"] else default_interval
            if found_new:
                new_interval = max(min_interval, current // 2)
            else:
                new_interval = min(max_interval, int(current * 1.5))
            new_interval = max(min_interval, min(max_interval, new_interval))
            next_at = (now_dt + timedelta(seconds=new_interval)).isoformat()
            conn.execute(
                """
                UPDATE podcasts
                SET refresh_interval_seconds = ?,
                    next_refresh_at = ?,
                    last_refresh_at = ?,
                    last_refresh_error = NULL,
                    updated_at = ?
                WHERE id = ?
                """,
                (new_interval, next_at, now_dt.isoformat(), now_dt.isoformat(), podcast_id),
            )
            return next_at

    def record_refresh_error(
        self,
        podcast_id: str,
        error: str,
        terminal: bool,
        now: Optional[datetime] = None,
    ) -> None:
        """Record a feed-scoped refresh failure (spec #48 failure isolation).

        Always stamps ``last_refresh_at`` + ``last_refresh_error``. A
        **terminal** failure PARKS the feed (``next_refresh_at = NULL``) so the
        scheduler stops re-enqueuing it every interval; only operator retry
        (:meth:`clear_podcast_refresh_failure`) re-arms it. Retryable errors
        leave ``next_refresh_at`` alone — the task's own retry will re-fetch.
        Never touches cache headers (FM-2 is enforced on the feed path).
        """
        now_dt = now or now_utc()
        with self._get_connection() as conn:
            if terminal:
                conn.execute(
                    """
                    UPDATE podcasts
                    SET last_refresh_at = ?, last_refresh_error = ?, next_refresh_at = NULL, updated_at = ?
                    WHERE id = ?
                    """,
                    (now_dt.isoformat(), error[:2000], now_dt.isoformat(), podcast_id),
                )
            else:
                conn.execute(
                    """
                    UPDATE podcasts
                    SET last_refresh_at = ?, last_refresh_error = ?, updated_at = ?
                    WHERE id = ?
                    """,
                    (now_dt.isoformat(), error[:2000], now_dt.isoformat(), podcast_id),
                )

    def clear_podcast_refresh_failure(
        self,
        podcast_id: str,
        default_interval: int,
        now: Optional[datetime] = None,
    ) -> str:
        """Operator retry of a parked feed: clear the error and re-arm
        ``next_refresh_at`` to ``now`` so the next tick re-enqueues it.
        Returns the new ``next_refresh_at``.
        """
        now_dt = now or now_utc()
        next_at = now_dt.isoformat()
        with self._get_connection() as conn:
            conn.execute(
                """
                UPDATE podcasts
                SET last_refresh_error = NULL,
                    next_refresh_at = ?,
                    refresh_interval_seconds = COALESCE(refresh_interval_seconds, ?),
                    updated_at = ?
                WHERE id = ?
                """,
                (next_at, default_interval, now_dt.isoformat(), podcast_id),
            )
        return next_at

    def _save_episode_idempotent(self, conn: sqlite3.Connection, episode: Episode) -> Episode:
        """
        Internal: Save episode with idempotent updated_at handling.

        Only updates updated_at if data actually changed.
        """
        now = datetime.now(timezone.utc)

        # Check if episode exists (by podcast_id + external_id).
        # IMPORTANT: every column the UPDATE below writes must also be SELECTed
        # and compared in the `changed` check, otherwise updates to those
        # fields are silently dropped when no other field happens to differ.
        cursor = conn.execute(
            """
            SELECT id, title, slug, description, description_html, pub_date, audio_url, duration, image_url,
                   explicit, episode_type, episode_number, season_number, website_url,
                   audio_file_size, audio_mime_type,
                   audio_path, downsampled_audio_path, raw_transcript_path,
                   clean_transcript_path, clean_transcript_json_path, summary_path,
                   playback_time_offset_seconds
            FROM episodes
            WHERE podcast_id = ? AND external_id = ?
            """,
            (episode.podcast_id, episode.external_id),
        )
        existing = cursor.fetchone()

        if existing:
            # Compare fields to see if anything changed
            pub_date_str = episode.pub_date.isoformat() if episode.pub_date else None
            explicit_int = 1 if episode.explicit is True else (0 if episode.explicit is False else None)
            normalized_image_url = _normalize_artwork_url(episode.image_url)

            changed = (
                existing["title"] != episode.title
                or existing["slug"] != episode.slug
                or existing["description"] != episode.description
                or existing["description_html"] != episode.description_html
                or existing["pub_date"] != pub_date_str
                or existing["audio_url"] != str(episode.audio_url)
                or existing["duration"] != episode.duration
                or existing["image_url"] != normalized_image_url
                or existing["explicit"] != explicit_int
                or existing["episode_type"] != episode.episode_type
                or existing["episode_number"] != episode.episode_number
                or existing["season_number"] != episode.season_number
                or existing["website_url"] != episode.website_url
                or existing["audio_file_size"] != episode.audio_file_size
                or existing["audio_mime_type"] != episode.audio_mime_type
                or existing["audio_path"] != episode.audio_path
                or existing["downsampled_audio_path"] != episode.downsampled_audio_path
                or existing["raw_transcript_path"] != episode.raw_transcript_path
                or existing["clean_transcript_path"] != episode.clean_transcript_path
                or existing["clean_transcript_json_path"] != episode.clean_transcript_json_path
                or existing["summary_path"] != episode.summary_path
                or existing["playback_time_offset_seconds"] != episode.playback_time_offset_seconds
            )

            if changed:
                # Update with new updated_at
                conn.execute(
                    """
                    UPDATE episodes
                    SET title = ?, slug = ?, description = ?, description_html = ?, pub_date = ?, audio_url = ?,
                        duration = ?, image_url = ?,
                        explicit = ?, episode_type = ?, episode_number = ?, season_number = ?, website_url = ?,
                        audio_file_size = ?, audio_mime_type = ?,
                        audio_path = ?, downsampled_audio_path = ?,
                        raw_transcript_path = ?, clean_transcript_path = ?,
                        clean_transcript_json_path = ?, summary_path = ?,
                        playback_time_offset_seconds = ?,
                        updated_at = ?
                    WHERE podcast_id = ? AND external_id = ?
                    """,
                    (
                        episode.title,
                        episode.slug,
                        episode.description,
                        episode.description_html,
                        pub_date_str,
                        str(episode.audio_url),
                        episode.duration,
                        normalized_image_url,
                        explicit_int,
                        episode.episode_type,
                        episode.episode_number,
                        episode.season_number,
                        episode.website_url,
                        episode.audio_file_size,
                        episode.audio_mime_type,
                        episode.audio_path,
                        episode.downsampled_audio_path,
                        episode.raw_transcript_path,
                        episode.clean_transcript_path,
                        # spec #18: segmented-cleanup sidecar + playback offset
                        episode.clean_transcript_json_path,
                        episode.summary_path,
                        episode.playback_time_offset_seconds,
                        now.isoformat(),
                        episode.podcast_id,
                        episode.external_id,
                    ),
                )
                logger.debug(f"Updated episode: {episode.title}")
            else:
                logger.debug(f"Episode unchanged: {episode.title}")
        else:
            # Insert new episode
            conn.execute(
                """
                INSERT INTO episodes (
                    id, podcast_id, created_at, updated_at, external_id, title, slug, description,
                    description_html, pub_date, audio_url, duration, image_url,
                    explicit, episode_type, episode_number, season_number, website_url,
                    audio_file_size, audio_mime_type,
                    audio_path, downsampled_audio_path, raw_transcript_path, clean_transcript_path,
                    clean_transcript_json_path, summary_path, playback_time_offset_seconds
                ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                """,
                (
                    episode.id,
                    episode.podcast_id,
                    episode.created_at.isoformat(),
                    now.isoformat(),
                    episode.external_id,
                    episode.title,
                    episode.slug,
                    episode.description,
                    episode.description_html,
                    episode.pub_date.isoformat() if episode.pub_date else None,
                    str(episode.audio_url),
                    episode.duration,
                    _normalize_artwork_url(episode.image_url),
                    1 if episode.explicit is True else (0 if episode.explicit is False else None),
                    episode.episode_type,
                    episode.episode_number,
                    episode.season_number,
                    episode.website_url,
                    episode.audio_file_size,
                    episode.audio_mime_type,
                    episode.audio_path,
                    episode.downsampled_audio_path,
                    episode.raw_transcript_path,
                    episode.clean_transcript_path,
                    # spec #18: segmented-cleanup sidecar + playback offset
                    episode.clean_transcript_json_path,
                    episode.summary_path,
                    episode.playback_time_offset_seconds,
                ),
            )
            logger.debug(f"Inserted new episode: {episode.title}")

        return episode

    def delete(self, url: str) -> bool:
        """
        Delete podcast by URL.

        Note: Episodes must be deleted first (no CASCADE).
        This is intentional for cache invalidation control.
        """
        with self._get_connection() as conn:
            # First, get podcast ID
            cursor = conn.execute("SELECT id FROM podcasts WHERE rss_url = ?", (url,))
            row = cursor.fetchone()
            if not row:
                return False

            podcast_id = row["id"]

            # Explicitly delete episodes (for cache invalidation tracking)
            conn.execute("DELETE FROM episodes WHERE podcast_id = ?", (podcast_id,))

            # Then delete podcast
            conn.execute("DELETE FROM podcasts WHERE id = ?", (podcast_id,))

            logger.info(f"Deleted podcast: {url}")
            return True

    def update_episode(self, podcast_url: str, episode_external_id: str, updates: dict) -> bool:
        """
        Update specific episode fields.

        Side effects: updated_at set explicitly here (no trigger).
        """
        # Build dynamic UPDATE query (safe: we validate field names)
        valid_fields = {
            "audio_path",
            "downsampled_audio_path",
            "raw_transcript_path",
            "clean_transcript_path",
            # spec #18: segmented-cleanup sidecar path
            "clean_transcript_json_path",
            "playback_time_offset_seconds",
            "summary_path",
            "title",
            "slug",
            "description",
            "description_html",
            "duration",
            "image_url",
            # THES-142: New fields
            "explicit",
            "episode_type",
            "episode_number",
            "season_number",
            "website_url",
            "audio_file_size",
            "audio_mime_type",
            # Failure tracking fields
            "failed_at_stage",
            "failure_reason",
            "failure_type",
            "failed_at",
        }

        update_fields = {k: v for k, v in updates.items() if k in valid_fields}
        if not update_fields:
            return False

        set_clause = ", ".join(f"{field} = ?" for field in update_fields.keys())
        values = list(update_fields.values())

        now = datetime.now(timezone.utc)

        with self._get_connection() as conn:
            cursor = conn.execute(
                f"""
                UPDATE episodes
                SET {set_clause}, updated_at = ?
                WHERE podcast_id = (SELECT id FROM podcasts WHERE rss_url = ?)
                  AND external_id = ?
            """,
                values + [now.isoformat(), podcast_url, episode_external_id],
            )

            updated = cursor.rowcount > 0
            if updated:
                logger.debug(f"Updated episode {episode_external_id}: {list(update_fields.keys())}")
            return updated

    def mark_episode_published(self, episode_id: str) -> bool:
        """Set ``published_at`` if not already set; return whether it transitioned."""
        now = datetime.now(timezone.utc).isoformat()
        with self._get_connection() as conn:
            cursor = conn.execute(
                """
                UPDATE episodes
                   SET published_at = ?, updated_at = ?
                 WHERE id = ? AND published_at IS NULL
                """,
                (now, now, episode_id),
            )
            return cursor.rowcount == 1

    def mark_episode_failed(
        self,
        episode_id: str,
        failed_at_stage: str,
        failure_reason: str,
        failure_type: str,
    ) -> bool:
        """
        Mark an episode as failed at a specific stage.

        This is called when a task exhausts its retries (transient) or hits a fatal error.

        Args:
            episode_id: Episode UUID
            failed_at_stage: Stage where failure occurred ('download', 'transcribe', etc.)
            failure_reason: Human-readable error message
            failure_type: 'transient' (exhausted retries) or 'fatal' (permanent)

        Returns:
            True if episode was updated, False if not found
        """
        now = datetime.now(timezone.utc)

        with self._get_connection() as conn:
            cursor = conn.execute(
                """
                UPDATE episodes
                SET failed_at_stage = ?,
                    failure_reason = ?,
                    failure_type = ?,
                    failed_at = ?,
                    updated_at = ?
                WHERE id = ?
            """,
                (failed_at_stage, failure_reason, failure_type, now.isoformat(), now.isoformat(), episode_id),
            )

            updated = cursor.rowcount > 0
            if updated:
                logger.info(f"Marked episode {episode_id} as failed at stage '{failed_at_stage}' ({failure_type})")
            else:
                logger.warning(f"Failed to mark episode {episode_id} as failed: not found")
            return updated

    def clear_episode_failure(self, episode_id: str) -> bool:
        """
        Clear failure state from an episode, allowing retry.

        This is called when manually retrying a failed episode from the DLQ.

        Args:
            episode_id: Episode UUID

        Returns:
            True if episode was updated, False if not found
        """
        now = datetime.now(timezone.utc)

        with self._get_connection() as conn:
            cursor = conn.execute(
                """
                UPDATE episodes
                SET failed_at_stage = NULL,
                    failure_reason = NULL,
                    failure_type = NULL,
                    failed_at = NULL,
                    updated_at = ?
                WHERE id = ?
            """,
                (now.isoformat(), episode_id),
            )

            updated = cursor.rowcount > 0
            if updated:
                logger.info(f"Cleared failure state for episode {episode_id}")
            else:
                logger.warning(f"Failed to clear failure for episode {episode_id}: not found")
            return updated

    def clear_episode_failures(self, episode_ids: Sequence[str]) -> int:
        """Bulk variant of :meth:`clear_episode_failure` — clear many episodes'
        failure banners in one write per chunk.

        Bulk DLQ retry used to call :meth:`clear_episode_failure` once per task,
        opening a fresh write transaction each time. Collapsing to a single
        ``UPDATE … WHERE id IN (…)`` per chunk keeps the failure-state clear from
        adding its own write storm to the queue requeue (see specs/49).

        Args:
            episode_ids: Episode UUIDs whose failure state should be cleared.

        Returns:
            Number of episode rows updated.
        """
        ids = list(dict.fromkeys(episode_ids))  # de-dupe, preserve order
        if not ids:
            return 0

        now = datetime.now(timezone.utc).isoformat()
        updated = 0
        with self._get_connection() as conn:
            for start in range(0, len(ids), _SQL_PARAM_CHUNK):
                chunk = ids[start : start + _SQL_PARAM_CHUNK]
                placeholders = ",".join("?" for _ in chunk)
                cursor = conn.execute(
                    f"""
                    UPDATE episodes
                    SET failed_at_stage = NULL,
                        failure_reason = NULL,
                        failure_type = NULL,
                        failed_at = NULL,
                        updated_at = ?
                    WHERE id IN ({placeholders})
                    """,
                    (now, *chunk),
                )
                updated += cursor.rowcount or 0
        logger.info("Bulk-cleared episode failure state", requested=len(ids), updated=updated)
        return updated

    def clear_podcast_refresh_failures(
        self,
        podcast_ids: Sequence[str],
        default_interval: int,
        now: Optional[datetime] = None,
    ) -> int:
        """Bulk variant of :meth:`clear_podcast_refresh_failure` — re-arm many
        parked feeds in one write per chunk. Returns the number of rows updated.
        """
        ids = list(dict.fromkeys(podcast_ids))  # de-dupe, preserve order
        if not ids:
            return 0

        now_dt = now or now_utc()
        next_at = now_dt.isoformat()
        updated = 0
        with self._get_connection() as conn:
            for start in range(0, len(ids), _SQL_PARAM_CHUNK):
                chunk = ids[start : start + _SQL_PARAM_CHUNK]
                placeholders = ",".join("?" for _ in chunk)
                cursor = conn.execute(
                    f"""
                    UPDATE podcasts
                    SET last_refresh_error = NULL,
                        next_refresh_at = ?,
                        refresh_interval_seconds = COALESCE(refresh_interval_seconds, ?),
                        updated_at = ?
                    WHERE id IN ({placeholders})
                    """,
                    (next_at, default_interval, next_at, *chunk),
                )
                updated += cursor.rowcount or 0
        logger.info("Bulk-cleared podcast refresh failures", requested=len(ids), updated=updated)
        return updated

    def clear_episode_failure_for_stages(self, episode_id: str, stages: Sequence[str]) -> bool:
        """Clear an episode's failure banner only if it was recorded at one of ``stages``.

        Called on successful stage completion: when a stage finishes (often on a
        later run after a transient outage), the failure it had recorded is now
        moot and the inbox should stop showing it as failed. Scoping by stage
        (rather than clearing unconditionally) means a success at an earlier
        stage does not wipe a failure recorded at a later, not-yet-rerun stage.

        Args:
            episode_id: Episode UUID
            stages: Stage names whose recorded failure this success supersedes
                (typically the same-branch stages at or before the completed one).

        Returns:
            True if a stale failure was cleared, False otherwise.
        """
        if not stages:
            return False

        now = datetime.now(timezone.utc)
        placeholders = ",".join("?" * len(stages))

        with self._get_connection() as conn:
            cursor = conn.execute(
                f"""
                UPDATE episodes
                SET failed_at_stage = NULL,
                    failure_reason = NULL,
                    failure_type = NULL,
                    failed_at = NULL,
                    updated_at = ?
                WHERE id = ?
                  AND failed_at_stage IN ({placeholders})
            """,
                (now.isoformat(), episode_id, *stages),
            )

            updated = cursor.rowcount > 0
            if updated:
                logger.info(
                    "Cleared stale failure for episode %s on successful stage completion",
                    episode_id,
                )
            return updated

    def update_entity_extraction_status(self, episode_id: str, status: str) -> bool:
        """Set ``episodes.entity_extraction_status`` for one episode.

        Spec #28 §6 ("Failure isolation rule"): the entity branch
        progresses independently of the user-facing pipeline. A failure
        here must NOT touch ``failed_at_stage`` (which would render the
        episode card red); it lives in its own status column.

        Allowed values: ``pending`` | ``complete`` | ``failed`` |
        ``skipped_legacy``. Validation is the caller's responsibility —
        the column has no CHECK constraint so we don't reject ``NULL``
        explicitly here either.
        """
        now = datetime.now(timezone.utc)
        with self._get_connection() as conn:
            cursor = conn.execute(
                """
                UPDATE episodes
                SET entity_extraction_status = ?,
                    updated_at = ?
                WHERE id = ?
                """,
                (status, now.isoformat(), episode_id),
            )
            updated = cursor.rowcount > 0
            if updated:
                logger.info(
                    "entity_extraction_status updated",
                    episode_id=episode_id,
                    status=status,
                )
            else:
                logger.warning(
                    "entity_extraction_status update missed: episode not found",
                    episode_id=episode_id,
                )
            return updated

    def get_failed_episodes(self, limit: int = 100) -> List[Tuple[Podcast, Episode]]:
        """
        Get episodes in failed state.

        Args:
            limit: Maximum number of episodes to return

        Returns:
            List of (Podcast, Episode) tuples for failed episodes, ordered by most recent first
        """
        with self._get_connection() as conn:
            cursor = conn.execute(
                """
                SELECT p.id as p_id, p.created_at as p_created_at, p.rss_url, p.title as p_title,
                       p.slug as p_slug, p.description as p_description, p.image_url as p_image_url,
                       p.language as p_language,
                       p.primary_category_id as p_primary_category_id,
                       p.secondary_category_id as p_secondary_category_id,
                       p.author as p_author, p.explicit as p_explicit, p.show_type as p_show_type,
                       p.website_url as p_website_url, p.is_complete as p_is_complete, p.copyright as p_copyright,
                       p.last_processed, p.last_processed_at, p.updated_at as p_updated_at, e.*
                FROM episodes e
                JOIN podcasts p ON e.podcast_id = p.id
                WHERE e.failed_at_stage IS NOT NULL
                ORDER BY e.failed_at DESC
                LIMIT ?
            """,
                (limit,),
            )

            results = []
            for row in cursor.fetchall():
                podcast = self._row_to_podcast_minimal(row)
                episode = self._row_to_episode(row)
                results.append((podcast, episode))

            return results

    # ============================================================================
    # EpisodeRepository Interface Implementation
    # ============================================================================

    def get_episodes_by_podcast(self, podcast_url: str) -> List[Episode]:
        """Get all episodes for a podcast."""
        with self._get_connection() as conn:
            cursor = conn.execute(
                """
                SELECT e.*
                FROM episodes e
                JOIN podcasts p ON e.podcast_id = p.id
                WHERE p.rss_url = ?
                ORDER BY e.pub_date DESC
            """,
                (podcast_url,),
            )

            return [self._row_to_episode(row) for row in cursor.fetchall()]

    def get_episode(self, episode_id: str) -> Optional[Tuple[Podcast, Episode]]:
        """Get episode by internal UUID (primary key)."""
        with self._get_connection() as conn:
            cursor = conn.execute(
                """
                SELECT p.id as p_id, p.created_at as p_created_at, p.rss_url, p.title as p_title,
                       p.slug as p_slug, p.description as p_description, p.image_url as p_image_url,
                       p.language as p_language,
                       p.primary_category_id as p_primary_category_id,
                       p.secondary_category_id as p_secondary_category_id,
                       p.author as p_author, p.explicit as p_explicit, p.show_type as p_show_type,
                       p.website_url as p_website_url, p.is_complete as p_is_complete, p.copyright as p_copyright,
                       p.last_processed, p.last_processed_at, p.updated_at as p_updated_at, e.*
                FROM episodes e
                JOIN podcasts p ON e.podcast_id = p.id
                WHERE e.id = ?
            """,
                (episode_id,),
            )

            row = cursor.fetchone()
            if not row:
                return None

            # Parse podcast and episode from row
            podcast = self._row_to_podcast_minimal(row)
            episode = self._row_to_episode(row)
            return (podcast, episode)

    def get_episode_by_external_id(self, podcast_url: str, episode_external_id: str) -> Optional[Episode]:
        """Get specific episode by external ID (from RSS feed)."""
        with self._get_connection() as conn:
            cursor = conn.execute(
                """
                SELECT e.*
                FROM episodes e
                JOIN podcasts p ON e.podcast_id = p.id
                WHERE p.rss_url = ? AND e.external_id = ?
            """,
                (podcast_url, episode_external_id),
            )

            row = cursor.fetchone()
            return self._row_to_episode(row) if row else None

    def get_episode_by_slug(self, podcast_slug: str, episode_slug: str) -> Optional[Tuple[Podcast, Episode]]:
        """Get episode by podcast slug and episode slug."""
        if not podcast_slug or not episode_slug:
            return None

        with self._get_connection() as conn:
            cursor = conn.execute(
                """
                SELECT p.id as p_id, p.created_at as p_created_at, p.rss_url, p.title as p_title,
                       p.slug as p_slug, p.description as p_description, p.image_url as p_image_url,
                       p.language as p_language,
                       p.primary_category_id as p_primary_category_id,
                       p.secondary_category_id as p_secondary_category_id,
                       p.author as p_author, p.explicit as p_explicit, p.show_type as p_show_type,
                       p.website_url as p_website_url, p.is_complete as p_is_complete, p.copyright as p_copyright,
                       p.last_processed, p.last_processed_at, p.updated_at as p_updated_at, e.*
                FROM episodes e
                JOIN podcasts p ON e.podcast_id = p.id
                WHERE p.slug = ? AND e.slug = ?
            """,
                (podcast_slug, episode_slug),
            )

            row = cursor.fetchone()
            if not row:
                return None

            podcast = self._row_to_podcast_minimal(row)
            episode = self._row_to_episode(row)
            return (podcast, episode)

    def get_unprocessed_episodes(self, state: str) -> List[Tuple[Podcast, Episode]]:
        """
        Get episodes in specific processing state.

        Uses partial indexes for performance (10-100x faster than full scan).
        """
        # Map state to SQL condition (matches partial index WHERE clauses)
        state_conditions = {
            EpisodeState.DISCOVERED.value: "e.audio_path IS NULL",
            EpisodeState.DOWNLOADED.value: "e.audio_path IS NOT NULL AND e.downsampled_audio_path IS NULL",
            EpisodeState.DOWNSAMPLED.value: "e.downsampled_audio_path IS NOT NULL AND e.raw_transcript_path IS NULL",
            EpisodeState.TRANSCRIBED.value: "e.raw_transcript_path IS NOT NULL AND e.clean_transcript_path IS NULL",
            EpisodeState.CLEANED.value: "e.clean_transcript_path IS NOT NULL AND e.summary_path IS NULL",
        }

        condition = state_conditions.get(state)
        if not condition:
            logger.warning(f"Unknown processing state: {state}")
            return []

        with self._get_connection() as conn:
            # Note: SQLite query planner will use partial index for this WHERE clause
            cursor = conn.execute(
                f"""
                SELECT p.id as p_id, p.created_at as p_created_at, p.rss_url, p.title as p_title,
                       p.slug as p_slug, p.description as p_description, p.image_url as p_image_url,
                       p.language as p_language,
                       p.primary_category_id as p_primary_category_id,
                       p.secondary_category_id as p_secondary_category_id,
                       p.author as p_author, p.explicit as p_explicit, p.show_type as p_show_type,
                       p.website_url as p_website_url, p.is_complete as p_is_complete, p.copyright as p_copyright,
                       p.last_processed, p.last_processed_at, p.updated_at as p_updated_at, e.*
                FROM episodes e
                JOIN podcasts p ON e.podcast_id = p.id
                WHERE {condition}
                ORDER BY e.pub_date DESC
            """
            )

            results = []
            for row in cursor.fetchall():
                podcast = self._row_to_podcast_minimal(row)
                episode = self._row_to_episode(row)
                results.append((podcast, episode))

            return results

    def get_all_episodes(
        self,
        limit: int = 20,
        offset: int = 0,
        search: Optional[str] = None,
        podcast_id: Optional[str] = None,
        state: Optional[str] = None,
        date_from: Optional[datetime] = None,
        date_to: Optional[datetime] = None,
        updated_from: Optional[datetime] = None,
        sort_by: str = "pub_date",
        sort_order: str = "desc",
    ) -> Tuple[List[Tuple[Podcast, Episode]], int]:
        """
        Get episodes across all podcasts with filtering and pagination.

        Args:
            date_from: Filter by publication date (pub_date >= date_from)
            date_to: Filter by publication date (pub_date <= date_to)
            updated_from: Filter by last modified date (updated_at >= updated_from)

        Returns (episodes_with_podcasts, total_count).
        """
        # Build WHERE conditions
        conditions = []
        params: List[Any] = []

        if search:
            conditions.append("e.title LIKE ?")
            params.append(f"%{search}%")

        if podcast_id:
            conditions.append("e.podcast_id = ?")
            params.append(podcast_id)

        if state:
            # Map state to SQL condition matching the Episode.state computed property logic.
            # The model checks states from most-progressed first: FAILED > SUMMARIZED > CLEANED >
            # TRANSCRIBED > DOWNSAMPLED > DOWNLOADED > DISCOVERED.
            # Each SQL condition must exclude episodes that would be classified as a more-progressed state.
            state_conditions = {
                EpisodeState.FAILED.value: "e.failed_at_stage IS NOT NULL",
                EpisodeState.SUMMARIZED.value: ("e.summary_path IS NOT NULL " "AND e.failed_at_stage IS NULL"),
                EpisodeState.CLEANED.value: (
                    "e.clean_transcript_path IS NOT NULL " "AND e.summary_path IS NULL " "AND e.failed_at_stage IS NULL"
                ),
                EpisodeState.TRANSCRIBED.value: (
                    "e.raw_transcript_path IS NOT NULL "
                    "AND e.clean_transcript_path IS NULL "
                    "AND e.summary_path IS NULL "
                    "AND e.failed_at_stage IS NULL"
                ),
                EpisodeState.DOWNSAMPLED.value: (
                    "e.downsampled_audio_path IS NOT NULL "
                    "AND e.raw_transcript_path IS NULL "
                    "AND e.clean_transcript_path IS NULL "
                    "AND e.summary_path IS NULL "
                    "AND e.failed_at_stage IS NULL"
                ),
                EpisodeState.DOWNLOADED.value: (
                    "e.audio_path IS NOT NULL "
                    "AND e.downsampled_audio_path IS NULL "
                    "AND e.raw_transcript_path IS NULL "
                    "AND e.clean_transcript_path IS NULL "
                    "AND e.summary_path IS NULL "
                    "AND e.failed_at_stage IS NULL"
                ),
                EpisodeState.DISCOVERED.value: (
                    "e.audio_path IS NULL "
                    "AND e.downsampled_audio_path IS NULL "
                    "AND e.raw_transcript_path IS NULL "
                    "AND e.clean_transcript_path IS NULL "
                    "AND e.summary_path IS NULL "
                    "AND e.failed_at_stage IS NULL"
                ),
            }
            condition = state_conditions.get(state)
            if condition:
                conditions.append(f"({condition})")

        if date_from:
            conditions.append("e.pub_date >= ?")
            params.append(date_from.isoformat())

        if date_to:
            conditions.append("e.pub_date <= ?")
            params.append(date_to.isoformat())

        if updated_from:
            conditions.append("e.updated_at >= ?")
            params.append(updated_from.isoformat())

        # Build WHERE clause
        where_clause = " AND ".join(conditions) if conditions else "1=1"

        # Validate and build ORDER BY clause
        valid_sort_fields = {"pub_date": "e.pub_date", "title": "e.title", "updated_at": "e.updated_at"}
        sort_field = valid_sort_fields.get(sort_by, "e.pub_date")
        order_direction = "ASC" if sort_order.lower() == "asc" else "DESC"

        with self._get_connection() as conn:
            # Get total count
            count_query = f"""
                SELECT COUNT(*) as total
                FROM episodes e
                JOIN podcasts p ON e.podcast_id = p.id
                WHERE {where_clause}
            """
            cursor = conn.execute(count_query, params)
            total = cursor.fetchone()["total"]

            # Get paginated results
            query = f"""
                SELECT p.id as p_id, p.created_at as p_created_at, p.rss_url, p.title as p_title,
                       p.slug as p_slug, p.description as p_description, p.image_url as p_image_url,
                       p.language as p_language,
                       p.primary_category_id as p_primary_category_id,
                       p.secondary_category_id as p_secondary_category_id,
                       p.author as p_author, p.explicit as p_explicit, p.show_type as p_show_type,
                       p.website_url as p_website_url, p.is_complete as p_is_complete, p.copyright as p_copyright,
                       p.last_processed, p.last_processed_at, p.updated_at as p_updated_at, e.*
                FROM episodes e
                JOIN podcasts p ON e.podcast_id = p.id
                WHERE {where_clause}
                ORDER BY {sort_field} {order_direction}
                LIMIT ? OFFSET ?
            """
            cursor = conn.execute(query, params + [limit, offset])

            results = []
            for row in cursor.fetchall():
                podcast = self._row_to_podcast_minimal(row)
                episode = self._row_to_episode(row)
                results.append((podcast, episode))

            return results, total

    # ============================================================================
    # Helper Methods
    # ============================================================================

    def _row_to_podcast(self, row: sqlite3.Row, conn: sqlite3.Connection) -> Podcast:
        """Convert database row to Podcast model with episodes."""
        try:
            # Fetch episodes for this podcast
            cursor = conn.execute(
                """
                SELECT * FROM episodes WHERE podcast_id = ? ORDER BY pub_date DESC
            """,
                (row["id"],),
            )

            episodes = [self._row_to_episode(ep_row) for ep_row in cursor.fetchall()]

            # Convert explicit from INTEGER to Optional[bool]
            explicit = None
            if row["explicit"] is not None:
                explicit = row["explicit"] == 1

            primary_top, primary_sub = self._resolve_category_id_to_pair(row["primary_category_id"])
            secondary_top, secondary_sub = self._resolve_category_id_to_pair(row["secondary_category_id"])

            return Podcast(
                id=row["id"],
                created_at=datetime.fromisoformat(row["created_at"]),
                rss_url=row["rss_url"],
                title=row["title"],
                slug=row["slug"] or "",
                description=row["description"],
                image_url=row["image_url"],
                language=row["language"] if row["language"] else "en",
                primary_category=primary_top,
                primary_subcategory=primary_sub,
                secondary_category=secondary_top,
                secondary_subcategory=secondary_sub,
                # THES-142: New fields
                author=row["author"],
                explicit=explicit,
                show_type=row["show_type"],
                website_url=row["website_url"],
                is_complete=row["is_complete"] == 1 if row["is_complete"] is not None else False,
                copyright=row["copyright"],
                last_processed=datetime.fromisoformat(row["last_processed"]) if row["last_processed"] else None,
                last_processed_at=_row_opt_dt(row, "last_processed_at"),
                etag=row["etag"],
                last_modified=row["last_modified"],
                episodes=episodes,
            )
        except Exception as e:
            logger.error(f"Error in _row_to_podcast: {e}", exc_info=True)
            raise

    def _row_to_podcast_minimal(self, row: sqlite3.Row) -> Podcast:
        """Convert database row to Podcast model without episodes."""
        # Convert explicit from INTEGER to Optional[bool]
        explicit = None
        if row["p_explicit"] is not None:
            explicit = row["p_explicit"] == 1

        primary_top, primary_sub = self._resolve_category_id_to_pair(row["p_primary_category_id"])
        secondary_top, secondary_sub = self._resolve_category_id_to_pair(row["p_secondary_category_id"])

        return Podcast(
            id=row["p_id"],
            created_at=datetime.fromisoformat(row["p_created_at"]),
            rss_url=row["rss_url"],
            title=row["p_title"],
            slug=row["p_slug"] or "",
            description=row["p_description"],
            image_url=row["p_image_url"],
            language=row["p_language"] if row["p_language"] else "en",
            primary_category=primary_top,
            primary_subcategory=primary_sub,
            secondary_category=secondary_top,
            secondary_subcategory=secondary_sub,
            # THES-142: New fields
            author=row["p_author"],
            explicit=explicit,
            show_type=row["p_show_type"],
            website_url=row["p_website_url"],
            is_complete=row["p_is_complete"] == 1 if row["p_is_complete"] is not None else False,
            copyright=row["p_copyright"],
            last_processed=datetime.fromisoformat(row["last_processed"]) if row["last_processed"] else None,
            last_processed_at=_row_opt_dt(row, "last_processed_at"),
            episodes=[],  # Episodes not loaded
        )

    def _row_to_episode(self, row: sqlite3.Row) -> Episode:
        """Convert database row to Episode model."""
        return episode_from_row(row)

    def _save_episode(self, conn: sqlite3.Connection, podcast_id: str, episode: Episode, now: datetime):
        """Insert episode into database."""
        conn.execute(
            """
            INSERT INTO episodes (
                id, podcast_id, created_at, updated_at, external_id, title, slug, description,
                description_html, pub_date, audio_url, duration, image_url,
                explicit, episode_type, episode_number, season_number, website_url,
                audio_file_size, audio_mime_type,
                audio_path, downsampled_audio_path, raw_transcript_path, clean_transcript_path,
                clean_transcript_json_path, summary_path, playback_time_offset_seconds,
                failed_at_stage, failure_reason, failure_type, failed_at
            ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
        """,
            (
                episode.id,
                podcast_id,
                episode.created_at.isoformat(),
                now.isoformat(),
                episode.external_id,
                episode.title,
                episode.slug,
                episode.description,
                episode.description_html,
                episode.pub_date.isoformat() if episode.pub_date else None,
                str(episode.audio_url),
                episode.duration,
                _normalize_artwork_url(episode.image_url),
                # THES-142: New fields
                1 if episode.explicit is True else (0 if episode.explicit is False else None),
                episode.episode_type,
                episode.episode_number,
                episode.season_number,
                episode.website_url,
                episode.audio_file_size,
                episode.audio_mime_type,
                # File paths
                episode.audio_path,
                episode.downsampled_audio_path,
                episode.raw_transcript_path,
                episode.clean_transcript_path,
                # spec #18: segmented-cleanup sidecar + playback offset
                episode.clean_transcript_json_path,
                episode.summary_path,
                episode.playback_time_offset_seconds,
                episode.failed_at_stage,
                episode.failure_reason,
                episode.failure_type.value if episode.failure_type else None,
                episode.failed_at.isoformat() if episode.failed_at else None,
            ),
        )

    # ============================================================================
    # Top podcasts (chart) lookups
    # ============================================================================

    def is_top_podcast_in_region(self, rss_url: str, region: str) -> bool:
        """Return True if the given RSS URL is in the top chart for ``region``.

        Used by the free-tier subscription gate: non-paying users may only
        subscribe to podcasts that appear on their region's top chart.
        Index-backed via ``top_podcasts.rss_url`` UNIQUE + the (region, podcast_id)
        primary key on ``top_podcast_rankings`` — single round-trip, no scan.
        """
        if not rss_url or not region:
            return False
        with self._get_connection() as conn:
            row = conn.execute(
                """
                SELECT 1
                FROM top_podcast_rankings r
                JOIN top_podcasts p ON p.id = r.top_podcast_id
                WHERE r.region = ? AND p.rss_url = ?
                LIMIT 1
                """,
                (region.lower(), rss_url),
            ).fetchone()
        return row is not None

    def get_top_podcasts(
        self,
        region: str,
        *,
        limit: int = 500,
        category: Optional[str] = None,
        q: Optional[str] = None,
        user_id: Optional[str] = None,
    ) -> List[Dict[str, Any]]:
        """List the top chart for a region, optionally filtered by category and/or query.

        Returns plain dicts (not Podcast models) since chart entries may not
        correspond to a subscribed ``Podcasts`` row.

        ``q`` is a case-insensitive substring matched against ``name`` and
        ``artist``. Rank order is preserved — top-ranked matches come first
        regardless of where the substring lands.

        ``user_id`` enables the ``is_following`` flag per row: a chart entry
        is "followed" iff a ``podcasts`` row with the same ``rss_url`` exists
        AND the given user follows it. Pass ``None`` (anonymous) to make every
        row report ``is_following=False`` without special-casing — the
        ``LEFT JOIN`` simply misses for ``user_id IS NULL``.

        ``podcast_slug`` is surfaced from the same ``podcasts`` join so the
        UI can link directly to the detail page for entries that already
        exist in the local DB (regardless of follow state). ``None`` means
        the podcast has not been imported yet.

        ``image_url`` rides the same join and is whatever artwork URL the
        original ``add_podcast`` flow stored (read from the RSS feed at
        import time). ``None`` for unimported entries — chart-only rows
        carry no artwork.
        """
        if not region:
            return []

        # `user_id` is bound first because the LEFT JOIN's `pf.user_id = ?`
        # appears in the FROM clause, before WHERE-clause params.
        params: List[Any] = [user_id, region.lower()]
        category_filter = ""
        if category:
            # The UI picker shows top-level Apple categories, but chart
            # entries can be tagged with either a top-level or sub-category
            # (e.g. "Comedy Interviews" under "Comedy"). Match both sides
            # so picking "Comedy" includes its sub-categorised rows too.
            category_filter = " AND (c.name = ? OR cat_parent.name = ?)"
            params.extend([category, category])

        query_filter = ""
        if q:
            query_filter = " AND (LOWER(p.name) LIKE ? OR LOWER(p.artist) LIKE ?)"
            like = f"%{q.lower()}%"
            params.extend([like, like])

        params.append(limit)

        with self._get_connection() as conn:
            rows = conn.execute(
                f"""
                SELECT r.rank, p.name, p.artist, p.rss_url, p.apple_url, p.youtube_url,
                       c.name AS category, r.source_genre,
                       up.slug AS podcast_slug,
                       up.image_url AS image_url,
                       CASE WHEN pf.user_id IS NOT NULL THEN 1 ELSE 0 END AS is_following
                FROM top_podcast_rankings r
                JOIN top_podcasts p ON p.id = r.top_podcast_id
                LEFT JOIN categories c ON c.id = p.category_id
                LEFT JOIN categories cat_parent ON cat_parent.id = c.parent_id
                LEFT JOIN podcasts up ON up.rss_url = p.rss_url
                LEFT JOIN podcast_followers pf
                       ON pf.podcast_id = up.id AND pf.user_id = ?
                WHERE r.region = ?{category_filter}{query_filter}
                ORDER BY r.rank ASC
                LIMIT ?
                """,
                params,
            ).fetchall()

        return [{**dict(row), "is_following": bool(row["is_following"])} for row in rows]

    # ============================================================================
    # TranscriptLink Methods (Podcasting 2.0 <podcast:transcript> support)
    # ============================================================================

    def get_transcript_links(self, episode_id: str) -> List[TranscriptLink]:
        """
        Get all transcript links for an episode.

        Args:
            episode_id: Episode UUID

        Returns:
            List of TranscriptLink objects for the episode
        """
        with self._get_connection() as conn:
            cursor = conn.execute(
                """
                SELECT id, episode_id, url, mime_type, language, rel, downloaded_path, created_at
                FROM episode_transcript_links
                WHERE episode_id = ?
                ORDER BY created_at ASC
            """,
                (episode_id,),
            )

            return [self._row_to_transcript_link(row) for row in cursor.fetchall()]

    def add_transcript_links(self, episode_id: str, links: List[TranscriptLink]) -> int:
        """
        Add transcript links for an episode.

        Skips duplicates (same episode_id + url).

        Args:
            episode_id: Episode UUID
            links: List of TranscriptLink objects to add

        Returns:
            Number of links actually inserted (excludes duplicates)
        """
        if not links:
            return 0

        inserted = 0
        with self._get_connection() as conn:
            for link in links:
                try:
                    conn.execute(
                        """
                        INSERT INTO episode_transcript_links (episode_id, url, mime_type, language, rel)
                        VALUES (?, ?, ?, ?, ?)
                    """,
                        (
                            episode_id,
                            str(link.url),
                            link.mime_type,
                            link.language,
                            link.rel,
                        ),
                    )
                    inserted += 1
                except sqlite3.IntegrityError:
                    # Duplicate (episode_id, url) - skip
                    logger.debug(f"Transcript link already exists: {link.url}")
                    continue

        if inserted > 0:
            logger.debug(f"Added {inserted} transcript links for episode {episode_id}")

        return inserted

    def mark_transcript_downloaded(self, link_id: int, local_path: str) -> bool:
        """
        Mark a transcript link as downloaded.

        Args:
            link_id: Primary key of the transcript link
            local_path: Local file path where transcript was saved

        Returns:
            True if update succeeded, False if link not found
        """
        with self._get_connection() as conn:
            cursor = conn.execute(
                """
                UPDATE episode_transcript_links
                SET downloaded_path = ?
                WHERE id = ?
            """,
                (local_path, link_id),
            )
            return cursor.rowcount > 0

    def get_episodes_with_undownloaded_transcript_links(
        self, podcast_id: Optional[str] = None
    ) -> List[Tuple[Episode, List[TranscriptLink]]]:
        """
        Get episodes that have transcript links not yet downloaded.

        Args:
            podcast_id: Optional podcast UUID to filter by

        Returns:
            List of (Episode, List[TranscriptLink]) tuples for episodes with pending downloads
        """
        with self._get_connection() as conn:
            # Find episodes with undownloaded transcript links. ``SELECT e.*``
            # with an EXISTS predicate — ``_row_to_episode`` reads the full
            # episode column set, and the previous partial DISTINCT projection
            # raised IndexError on hydration (latent bug surfaced by the spec
            # #44 Postgres port; prior callers only exercised this via mocks).
            if podcast_id:
                cursor = conn.execute(
                    """
                    SELECT e.*
                    FROM episodes e
                    WHERE e.podcast_id = ?
                      AND EXISTS (
                        SELECT 1 FROM episode_transcript_links etl
                        WHERE etl.episode_id = e.id AND etl.downloaded_path IS NULL
                      )
                    ORDER BY e.pub_date DESC
                """,
                    (podcast_id,),
                )
            else:
                cursor = conn.execute(
                    """
                    SELECT e.*
                    FROM episodes e
                    WHERE EXISTS (
                        SELECT 1 FROM episode_transcript_links etl
                        WHERE etl.episode_id = e.id AND etl.downloaded_path IS NULL
                      )
                    ORDER BY e.pub_date DESC
                """
                )

            results = []
            for row in cursor.fetchall():
                episode = self._row_to_episode(row)
                # Fetch undownloaded links for this episode
                link_cursor = conn.execute(
                    """
                    SELECT id, episode_id, url, mime_type, language, rel, downloaded_path, created_at
                    FROM episode_transcript_links
                    WHERE episode_id = ? AND downloaded_path IS NULL
                """,
                    (episode.id,),
                )
                links = [self._row_to_transcript_link(link_row) for link_row in link_cursor.fetchall()]
                results.append((episode, links))

            return results

    def _row_to_transcript_link(self, row: sqlite3.Row) -> TranscriptLink:
        """Convert database row to TranscriptLink model."""
        return TranscriptLink(
            id=row["id"],
            episode_id=row["episode_id"],
            url=row["url"],
            mime_type=row["mime_type"],
            language=row["language"],
            rel=row["rel"],
            downloaded_path=row["downloaded_path"],
            created_at=datetime.fromisoformat(row["created_at"]) if row["created_at"] else None,
        )

    def get_podcast_for_episode(self, episode_id: str) -> Optional[Podcast]:
        """
        Get the podcast that owns a specific episode.

        Args:
            episode_id: Episode UUID

        Returns:
            Podcast object if found, None otherwise
        """
        with self._get_connection() as conn:
            # Full column set — ``_row_to_podcast`` reads author/explicit/
            # show_type/… and the previous partial SELECT raised IndexError
            # (latent bug surfaced by the spec #44 Postgres port; prior
            # callers only exercised this via mocks).
            cursor = conn.execute(
                """
                SELECT p.id, p.created_at, p.rss_url, p.title, p.slug, p.description,
                       p.image_url, p.language,
                       p.primary_category_id, p.secondary_category_id,
                       p.author, p.explicit, p.show_type, p.website_url, p.is_complete,
                       p.copyright, p.last_processed, p.last_processed_at, p.etag,
                       p.last_modified, p.updated_at
                FROM podcasts p
                INNER JOIN episodes e ON e.podcast_id = p.id
                WHERE e.id = ?
            """,
                (episode_id,),
            )

            row = cursor.fetchone()
            if row:
                return self._row_to_podcast(row, conn)
            return None

    # ------------------------------------------------------------------
    # Import (paste-a-URL) helpers
    # ------------------------------------------------------------------

    def ensure_synthetic_audio_imports_parent(self) -> str:
        """
        Find-or-create the synthetic parent for bare-audio imports.

        The row is marked ``synthetic=1`` so refresh and discovery skip it.
        Returns the (deterministic) podcast id; callers store this as the
        ``podcast_id`` on imported episodes when no real parent can be
        deduced from the URL.
        """
        title = "Audio imports"
        now = datetime.now(timezone.utc).isoformat()
        with self._get_connection() as conn:
            conn.execute(
                """
                INSERT INTO podcasts (id, created_at, updated_at, rss_url, title, slug,
                                      description, language, synthetic, auto_added)
                VALUES (?, ?, ?, ?, ?, ?,
                        'Synthetic parent for bare-audio imports.',
                        'en', 1, 0)
                ON CONFLICT(rss_url) DO NOTHING
                """,
                (
                    SYNTHETIC_AUDIO_IMPORTS_ID,
                    now,
                    now,
                    SYNTHETIC_AUDIO_IMPORTS_RSS,
                    title,
                    generate_slug(title),
                ),
            )
        return SYNTHETIC_AUDIO_IMPORTS_ID

    def upsert_auto_added_podcast(
        self,
        *,
        rss_url: str,
        title: str,
        description: str = "",
        image_url: Optional[str] = None,
    ) -> Tuple[str, str, str]:
        """
        Find-or-create a real ``podcasts`` row for an import-deduced parent.

        Returns ``(id, title, slug)``. New rows are inserted with
        ``auto_added=1``; existing rows (whether previously auto-added or
        manually subscribed) are returned unchanged so a user who already
        follows the channel does not silently lose that signal.
        """
        podcast_id = str(uuid.uuid4())
        now = datetime.now(timezone.utc).isoformat()
        slug = generate_slug(title)
        with self._get_connection() as conn:
            inserted = conn.execute(
                """
                INSERT INTO podcasts
                    (id, created_at, updated_at, rss_url, title, slug,
                     description, image_url, language, synthetic, auto_added)
                VALUES (?, ?, ?, ?, ?, ?, ?, ?, 'en', 0, 1)
                ON CONFLICT(rss_url) DO NOTHING
                RETURNING id, title, slug
                """,
                (podcast_id, now, now, rss_url, title, slug, description, image_url),
            ).fetchone()
            if inserted is not None:
                return inserted["id"], inserted["title"], inserted["slug"]
            existing = conn.execute("SELECT id, title, slug FROM podcasts WHERE rss_url = ?", (rss_url,)).fetchone()
            if existing is None:
                raise RuntimeError(
                    f"upsert_auto_added_podcast: row for rss_url={rss_url!r} " "neither inserted nor found"
                )
            return existing["id"], existing["title"], existing["slug"]

    def get_real_parent_podcast_for_episode(self, episode_id: str) -> Optional[Tuple[str, str, str]]:
        """
        Return ``(id, title, slug)`` for the parent podcast of ``episode_id``
        IFF the parent is a real (non-synthetic) row — otherwise ``None``.

        Used by the import flow's dedup path to surface a follow target for
        already-imported episodes without re-hydrating the full Podcast
        model (which would also load every episode of the channel).
        """
        with self._get_connection() as conn:
            row = conn.execute(
                """
                SELECT p.id, p.title, p.slug
                  FROM episodes e
                  JOIN podcasts p ON p.id = e.podcast_id
                 WHERE e.id = ? AND p.synthetic = 0
                """,
                (episode_id,),
            ).fetchone()
            if row is None:
                return None
            return row["id"], row["title"], row["slug"]

    def find_episode_id_by_canonical_id(self, canonical_id: str) -> Optional[str]:
        """Return the episode UUID for a given canonical id, or None."""
        with self._get_connection() as conn:
            row = conn.execute(
                "SELECT id FROM episodes WHERE canonical_id = ?",
                (canonical_id,),
            ).fetchone()
            return row["id"] if row else None

    def find_episode_id_by_audio_url(self, podcast_id: str, audio_url: str) -> Optional[str]:
        """Return the episode UUID for a given audio_url within a podcast, or None.

        Used by the import flow to attach a resolver-issued canonical_id to an
        episode that was already discovered via the parent's RSS feed (Apple
        imports auto-ingest the show). Apple's iTunes ``episodeUrl`` matches
        the RSS enclosure URL byte-for-byte, so this is the cheapest dedup.
        """
        with self._get_connection() as conn:
            row = conn.execute(
                "SELECT id FROM episodes WHERE podcast_id = ? AND audio_url = ? LIMIT 1",
                (podcast_id, audio_url),
            ).fetchone()
            return row["id"] if row else None

    def set_episode_canonical_id(self, episode_id: str, canonical_id: str) -> None:
        """Stamp ``canonical_id`` on an existing episode row.

        No-op when the row already carries this canonical_id. Raises
        ``sqlite3.IntegrityError`` if a different episode already owns the
        canonical_id (the unique partial index would catch this).
        """
        with self._get_connection() as conn:
            conn.execute(
                "UPDATE episodes SET canonical_id = ?, updated_at = ? WHERE id = ?",
                (canonical_id, datetime.now(timezone.utc).isoformat(), episode_id),
            )

    def insert_imported_episode(
        self,
        *,
        podcast_id: str,
        canonical_id: str,
        external_id: str,
        title: str,
        audio_url: str,
        description: str = "",
        pub_date: Optional[datetime] = None,
        duration: Optional[int] = None,
        image_url: Optional[str] = None,
    ) -> str:
        """
        Insert a new imported episode and return its UUID.

        The episode is created in the ``discovered`` state (no audio_path,
        no transcript, no summary) so the existing pipeline can pick it up
        from the download stage. ``canonical_id`` is the resolver-issued
        typed key used for cross-user dedup.

        Raises:
            sqlite3.IntegrityError: if ``canonical_id`` is already in use
                (unique partial index). Callers should look up by
                canonical_id first via ``find_episode_id_by_canonical_id``.
        """
        episode_id = str(uuid.uuid4())
        now = datetime.now(timezone.utc).isoformat()
        # Slug must be populated for the (podcast_slug, episode_slug) URL
        # lookup to find the episode. RSS-ingested episodes get this via the
        # ``Episode`` Pydantic model's ``ensure_slug`` validator; the import
        # path skips that model so we generate the slug here.
        slug = generate_slug(title)
        with self._get_connection() as conn:
            conn.execute(
                """
                INSERT INTO episodes (
                    id, podcast_id, created_at, updated_at,
                    external_id, title, slug, description, description_html,
                    pub_date, audio_url, duration, image_url,
                    canonical_id
                ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, '', ?, ?, ?, ?, ?)
                """,
                (
                    episode_id,
                    podcast_id,
                    now,
                    now,
                    external_id,
                    title,
                    slug,
                    description,
                    pub_date.isoformat() if pub_date else None,
                    audio_url,
                    duration,
                    image_url,
                    canonical_id,
                ),
            )
        return episode_id
