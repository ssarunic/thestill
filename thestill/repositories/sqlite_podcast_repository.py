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
from contextlib import contextmanager
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Dict, List, Optional, Set, Tuple

from structlog import get_logger

from ..models.podcast import Episode, EpisodeState, FailureType, Podcast, TranscriptLink
from ..utils.podcast_categories import APPLE_GENRE_IDS, APPLE_PODCAST_TAXONOMY, normalize_category_name
from ..utils.slug import generate_slug
from .podcast_repository import EpisodeRepository, PodcastRepository

logger = get_logger(__name__)

# Float round-trip tolerance for SQLite REAL mtime comparison: ``stat().st_mtime``
# is float64 but SQLite REAL → Python float can drift below microsecond precision.
_MTIME_EPSILON = 1e-6


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

        # THES-153 Migration: Create digests tables if they don't exist (idempotent)
        cursor = conn.execute("SELECT name FROM sqlite_master WHERE type='table' AND name='digests'")
        if cursor.fetchone() is None:
            logger.info("Migrating database: creating digests tables for THES-153")
            conn.executescript(
                """
                -- Digest metadata table
                -- user_id references users table (required, uses default user in CLI mode)
                CREATE TABLE IF NOT EXISTS digests (
                    id TEXT PRIMARY KEY NOT NULL,
                    user_id TEXT NOT NULL,
                    created_at TIMESTAMP NOT NULL DEFAULT CURRENT_TIMESTAMP,
                    updated_at TIMESTAMP NOT NULL DEFAULT CURRENT_TIMESTAMP,
                    period_start TIMESTAMP NOT NULL,
                    period_end TIMESTAMP NOT NULL,
                    status TEXT NOT NULL DEFAULT 'pending',
                    file_path TEXT NULL,
                    episodes_total INTEGER NOT NULL DEFAULT 0,
                    episodes_completed INTEGER NOT NULL DEFAULT 0,
                    episodes_failed INTEGER NOT NULL DEFAULT 0,
                    processing_time_seconds REAL NULL,
                    error_message TEXT NULL,
                    FOREIGN KEY (user_id) REFERENCES users(id) ON DELETE CASCADE,
                    CHECK (length(id) = 36),
                    CHECK (status IN ('pending', 'in_progress', 'completed', 'partial', 'failed'))
                );

                -- Junction table linking digests to episodes
                -- Note: No FK on episode_id to preserve digest history if episodes are deleted
                CREATE TABLE IF NOT EXISTS digest_episodes (
                    digest_id TEXT NOT NULL,
                    episode_id TEXT NOT NULL,
                    PRIMARY KEY (digest_id, episode_id),
                    FOREIGN KEY (digest_id) REFERENCES digests(id) ON DELETE CASCADE
                );

                CREATE INDEX IF NOT EXISTS idx_digests_created_at ON digests(created_at);
                CREATE INDEX IF NOT EXISTS idx_digests_status ON digests(status);
                CREATE INDEX IF NOT EXISTS idx_digests_user_id ON digests(user_id);
                CREATE INDEX IF NOT EXISTS idx_digest_episodes_episode ON digest_episodes(episode_id);
                """
            )
            logger.info("Migration complete: digests tables created for THES-153")

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
                last_processed TIMESTAMP NULL,
                -- spec #19: HTTP conditional-GET cache
                etag TEXT NULL,
                last_modified TEXT NULL,
                CHECK (length(id) = 36),
                CHECK (length(rss_url) > 0)
            );

            CREATE UNIQUE INDEX IF NOT EXISTS idx_podcasts_rss_url ON podcasts(rss_url);
            CREATE INDEX IF NOT EXISTS idx_podcasts_updated_at ON podcasts(updated_at DESC);
            CREATE INDEX IF NOT EXISTS idx_podcasts_slug ON podcasts(slug) WHERE slug != '';
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
                CHECK (length(id) = 36),
                CHECK (length(email) > 0),
                CHECK (region IS NULL OR length(region) = 2),
                CHECK (region_locked IN (0, 1))
            );

            CREATE INDEX IF NOT EXISTS idx_users_email ON users(email);
            CREATE INDEX IF NOT EXISTS idx_users_google_id ON users(google_id) WHERE google_id IS NOT NULL;

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

            -- ========================================================================
            -- DIGESTS TABLE (THES-153: Digest persistence)
            -- ========================================================================
            -- Stores metadata about generated digests for tracking and querying.
            -- user_id references users table (required, uses default user in CLI mode).
            CREATE TABLE IF NOT EXISTS digests (
                id TEXT PRIMARY KEY NOT NULL,
                user_id TEXT NOT NULL,
                created_at TIMESTAMP NOT NULL DEFAULT CURRENT_TIMESTAMP,
                updated_at TIMESTAMP NOT NULL DEFAULT CURRENT_TIMESTAMP,
                period_start TIMESTAMP NOT NULL,
                period_end TIMESTAMP NOT NULL,
                status TEXT NOT NULL DEFAULT 'pending',
                file_path TEXT NULL,
                episodes_total INTEGER NOT NULL DEFAULT 0,
                episodes_completed INTEGER NOT NULL DEFAULT 0,
                episodes_failed INTEGER NOT NULL DEFAULT 0,
                processing_time_seconds REAL NULL,
                error_message TEXT NULL,
                FOREIGN KEY (user_id) REFERENCES users(id) ON DELETE CASCADE,
                CHECK (length(id) = 36),
                CHECK (status IN ('pending', 'in_progress', 'completed', 'partial', 'failed'))
            );

            CREATE INDEX IF NOT EXISTS idx_digests_created_at ON digests(created_at);
            CREATE INDEX IF NOT EXISTS idx_digests_status ON digests(status);
            CREATE INDEX IF NOT EXISTS idx_digests_user_id ON digests(user_id);

            -- ========================================================================
            -- DIGEST_EPISODES TABLE (THES-153: Digest-Episode junction)
            -- ========================================================================
            -- Many-to-many relationship: which episodes are included in each digest.
            -- Note: No FK on episode_id to preserve digest history if episodes are deleted.
            CREATE TABLE IF NOT EXISTS digest_episodes (
                digest_id TEXT NOT NULL,
                episode_id TEXT NOT NULL,
                PRIMARY KEY (digest_id, episode_id),
                FOREIGN KEY (digest_id) REFERENCES digests(id) ON DELETE CASCADE
            );

            CREATE INDEX IF NOT EXISTS idx_digest_episodes_episode ON digest_episodes(episode_id);
        """
        )

    @contextmanager
    def _get_connection(self) -> sqlite3.Connection:
        """
        Get database connection with proper setup.

        Features:
        - Row factory for dict-like access
        - Foreign keys enabled
        - Automatic commit/rollback
        """
        conn = sqlite3.connect(str(self.db_path))
        conn.row_factory = sqlite3.Row  # Dict-like access
        conn.execute("PRAGMA foreign_keys = ON")

        try:
            yield conn
            conn.commit()
        except Exception:
            conn.rollback()
            raise
        finally:
            conn.close()

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

        Returns:
            ``(podcasts, known_external_ids_by_podcast)`` where each
            ``Podcast`` has an empty ``episodes`` list and the dict maps
            ``podcast_id`` to the set of known ``external_id`` values.
            A podcast with no tracked episodes has no key in the dict.
        """
        with self._get_connection() as conn:
            cursor = conn.execute(
                """
                SELECT id, created_at, rss_url, title, slug, description, image_url, language,
                       primary_category_id, secondary_category_id,
                       author, explicit, show_type, website_url, is_complete, copyright,
                       last_processed, etag, last_modified, updated_at
                FROM podcasts
                ORDER BY created_at DESC
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
                    etag=row["etag"],
                    last_modified=row["last_modified"],
                    episodes=[],
                )
            )
        return podcasts, dedup

    def get_top_podcast_regions(self) -> List[str]:
        """Return the list of regions that currently have top-podcast data."""
        with self._get_connection() as conn:
            cursor = conn.execute("SELECT region FROM top_podcasts_meta ORDER BY region")
            return [row["region"] for row in cursor.fetchall()]

    def get_all(self) -> List[Podcast]:
        """Retrieve all podcasts with their episodes."""
        with self._get_connection() as conn:
            # Fetch all podcasts
            cursor = conn.execute(
                """
                SELECT id, created_at, rss_url, title, slug, description, image_url, language,
                       primary_category_id, secondary_category_id,
                       author, explicit, show_type, website_url, is_complete, copyright,
                       last_processed, etag, last_modified, updated_at
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
                       last_processed, etag, last_modified, updated_at
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
                       last_processed, etag, last_modified, updated_at
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
                       last_processed, etag, last_modified, updated_at
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
                       last_processed, etag, last_modified, updated_at
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
                       last_processed, etag, last_modified, updated_at
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
                    podcast.image_url,
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

                changed = (
                    existing["title"] != podcast.title
                    or existing["slug"] != podcast.slug
                    or existing["description"] != podcast.description
                    or existing["image_url"] != podcast.image_url
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
                            podcast.image_url,
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
                        podcast.image_url,
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

    def save_refresh_batch(self, changed_podcasts: List[Podcast], new_episodes: List[Episode]) -> None:
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
        """
        if not changed_podcasts and not new_episodes:
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
                    p.image_url,
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
                    ep.image_url,
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

            changed = (
                existing["title"] != episode.title
                or existing["slug"] != episode.slug
                or existing["description"] != episode.description
                or existing["description_html"] != episode.description_html
                or existing["pub_date"] != pub_date_str
                or existing["audio_url"] != str(episode.audio_url)
                or existing["duration"] != episode.duration
                or existing["image_url"] != episode.image_url
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
                        episode.image_url,
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
                    episode.image_url,
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
                       p.last_processed, p.updated_at as p_updated_at, e.*
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
                       p.last_processed, p.updated_at as p_updated_at, e.*
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
                       p.last_processed, p.updated_at as p_updated_at, e.*
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
                       p.last_processed, p.updated_at as p_updated_at, e.*
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
                       p.last_processed, p.updated_at as p_updated_at, e.*
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
            episodes=[],  # Episodes not loaded
        )

    def _row_to_episode(self, row: sqlite3.Row) -> Episode:
        """Convert database row to Episode model."""
        # Parse failure_type enum if present
        failure_type = None
        if row["failure_type"]:
            try:
                failure_type = FailureType(row["failure_type"])
            except ValueError:
                logger.warning(f"Unknown failure_type '{row['failure_type']}' for episode {row['id']}")

        # Convert explicit from INTEGER to Optional[bool]
        explicit = None
        if row["explicit"] is not None:
            explicit = row["explicit"] == 1

        return Episode(
            id=row["id"],
            podcast_id=row["podcast_id"],
            created_at=datetime.fromisoformat(row["created_at"]),
            updated_at=datetime.fromisoformat(row["updated_at"]),
            external_id=row["external_id"],
            title=row["title"],
            slug=row["slug"] or "",
            description=row["description"],
            description_html=row["description_html"] if row["description_html"] else "",
            pub_date=datetime.fromisoformat(row["pub_date"]) if row["pub_date"] else None,
            audio_url=row["audio_url"],
            duration=row["duration"],
            image_url=row["image_url"],
            # THES-142: New fields
            explicit=explicit,
            episode_type=row["episode_type"],
            episode_number=row["episode_number"],
            season_number=row["season_number"],
            website_url=row["website_url"],
            audio_file_size=row["audio_file_size"],
            audio_mime_type=row["audio_mime_type"],
            # File paths
            audio_path=row["audio_path"],
            downsampled_audio_path=row["downsampled_audio_path"],
            raw_transcript_path=row["raw_transcript_path"],
            clean_transcript_path=row["clean_transcript_path"],
            # spec #18: structured JSON sidecar + playback offset. Row
            # accessors default to ``None`` / absent keys when the column
            # hasn't been migrated yet; the ``or 0.0`` below keeps
            # ``Episode.playback_time_offset_seconds`` a plain float.
            clean_transcript_json_path=(
                row["clean_transcript_json_path"] if "clean_transcript_json_path" in row.keys() else None
            ),
            playback_time_offset_seconds=(
                row["playback_time_offset_seconds"]
                if "playback_time_offset_seconds" in row.keys() and row["playback_time_offset_seconds"] is not None
                else 0.0
            ),
            summary_path=row["summary_path"],
            # Failure tracking fields
            failed_at_stage=row["failed_at_stage"],
            failure_reason=row["failure_reason"],
            failure_type=failure_type,
            failed_at=datetime.fromisoformat(row["failed_at"]) if row["failed_at"] else None,
        )

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
                episode.image_url,
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

    def get_top_podcasts(self, region: str, limit: int = 500, category: Optional[str] = None) -> List[Dict[str, Any]]:
        """List the top chart for a region, optionally filtered by category name.

        Returns plain dicts (not Podcast models) since the chart entries may
        not correspond to a subscribed Podcast row.
        """
        if not region:
            return []
        params: List[Any] = [region.lower()]
        category_filter = ""
        if category:
            category_filter = " AND c.name = ?"
            params.append(category)
        params.append(limit)
        with self._get_connection() as conn:
            rows = conn.execute(
                f"""
                SELECT r.rank, p.name, p.artist, p.rss_url, p.apple_url, p.youtube_url,
                       c.name AS category, r.source_genre
                FROM top_podcast_rankings r
                JOIN top_podcasts p ON p.id = r.top_podcast_id
                LEFT JOIN categories c ON c.id = p.category_id
                WHERE r.region = ?{category_filter}
                ORDER BY r.rank ASC
                LIMIT ?
                """,
                params,
            ).fetchall()
        return [dict(row) for row in rows]

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
            # Find episodes with undownloaded transcript links
            if podcast_id:
                cursor = conn.execute(
                    """
                    SELECT DISTINCT e.id, e.podcast_id, e.created_at, e.updated_at, e.external_id,
                           e.title, e.slug, e.description, e.pub_date, e.audio_url, e.duration,
                           e.audio_path, e.downsampled_audio_path, e.raw_transcript_path,
                           e.clean_transcript_path, e.summary_path
                    FROM episodes e
                    INNER JOIN episode_transcript_links etl ON e.id = etl.episode_id
                    WHERE etl.downloaded_path IS NULL AND e.podcast_id = ?
                    ORDER BY e.pub_date DESC
                """,
                    (podcast_id,),
                )
            else:
                cursor = conn.execute(
                    """
                    SELECT DISTINCT e.id, e.podcast_id, e.created_at, e.updated_at, e.external_id,
                           e.title, e.slug, e.description, e.pub_date, e.audio_url, e.duration,
                           e.audio_path, e.downsampled_audio_path, e.raw_transcript_path,
                           e.clean_transcript_path, e.summary_path
                    FROM episodes e
                    INNER JOIN episode_transcript_links etl ON e.id = etl.episode_id
                    WHERE etl.downloaded_path IS NULL
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
            cursor = conn.execute(
                """
                SELECT p.id, p.created_at, p.rss_url, p.title, p.slug, p.description,
                       p.image_url, p.language,
                       p.primary_category_id, p.secondary_category_id,
                       p.last_processed, p.updated_at
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
