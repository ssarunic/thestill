# Copyright 2025 thestill.me
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

import logging
import sqlite3
from contextlib import contextmanager
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, List, Optional, Tuple

from ..models.podcast import Episode, EpisodeState, FailureType, Podcast, TranscriptLink
from .podcast_repository import EpisodeRepository, PodcastRepository

logger = logging.getLogger(__name__)


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

        # Migration: Add category columns to podcasts table (idempotent)
        if "primary_category" not in podcast_columns:
            logger.info("Migrating database: adding category columns to podcasts table")
            conn.execute("ALTER TABLE podcasts ADD COLUMN primary_category TEXT NULL")
            conn.execute("ALTER TABLE podcasts ADD COLUMN primary_subcategory TEXT NULL")
            conn.execute("ALTER TABLE podcasts ADD COLUMN secondary_category TEXT NULL")
            conn.execute("ALTER TABLE podcasts ADD COLUMN secondary_subcategory TEXT NULL")
            logger.info("Migration complete: category columns added to podcasts")

        # Always ensure category indexes exist (for both migrated and new databases)
        conn.execute("CREATE INDEX IF NOT EXISTS idx_podcasts_primary_category ON podcasts(primary_category)")
        conn.execute("CREATE INDEX IF NOT EXISTS idx_podcasts_secondary_category ON podcasts(secondary_category)")

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

    def _create_schema(self, conn: sqlite3.Connection):
        """Create database schema (single-user variant)."""
        conn.executescript(
            """
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
                primary_category TEXT NULL,
                primary_subcategory TEXT NULL,
                secondary_category TEXT NULL,
                secondary_subcategory TEXT NULL,
                last_processed TIMESTAMP NULL,
                CHECK (length(id) = 36),
                CHECK (length(rss_url) > 0)
            );

            CREATE UNIQUE INDEX IF NOT EXISTS idx_podcasts_rss_url ON podcasts(rss_url);
            CREATE INDEX IF NOT EXISTS idx_podcasts_updated_at ON podcasts(updated_at DESC);
            CREATE INDEX IF NOT EXISTS idx_podcasts_slug ON podcasts(slug) WHERE slug != '';
            -- Note: Category indexes are created in _run_migrations to support existing DBs

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
                audio_path TEXT NULL,
                downsampled_audio_path TEXT NULL,
                raw_transcript_path TEXT NULL,
                clean_transcript_path TEXT NULL,
                summary_path TEXT NULL,
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
                CHECK (length(id) = 36),
                CHECK (length(email) > 0)
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

    def get_all(self) -> List[Podcast]:
        """Retrieve all podcasts with their episodes."""
        with self._get_connection() as conn:
            # Fetch all podcasts
            cursor = conn.execute(
                """
                SELECT id, created_at, rss_url, title, slug, description, image_url, language,
                       primary_category, primary_subcategory, secondary_category, secondary_subcategory,
                       last_processed, updated_at
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
                       primary_category, primary_subcategory, secondary_category, secondary_subcategory,
                       last_processed, updated_at
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
                       primary_category, primary_subcategory, secondary_category, secondary_subcategory,
                       last_processed, updated_at
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
                       primary_category, primary_subcategory, secondary_category, secondary_subcategory,
                       last_processed, updated_at
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
                       primary_category, primary_subcategory, secondary_category, secondary_subcategory,
                       last_processed, updated_at
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
                       primary_category, primary_subcategory, secondary_category, secondary_subcategory,
                       last_processed, updated_at
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

            # Upsert podcast
            conn.execute(
                """
                INSERT INTO podcasts (id, created_at, updated_at, rss_url, title, slug, description, image_url, language,
                                      primary_category, primary_subcategory, secondary_category, secondary_subcategory, last_processed)
                VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                ON CONFLICT(rss_url) DO UPDATE SET
                    title = excluded.title,
                    slug = excluded.slug,
                    description = excluded.description,
                    image_url = excluded.image_url,
                    language = excluded.language,
                    primary_category = excluded.primary_category,
                    primary_subcategory = excluded.primary_subcategory,
                    secondary_category = excluded.secondary_category,
                    secondary_subcategory = excluded.secondary_subcategory,
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
                    podcast.primary_category,
                    podcast.primary_subcategory,
                    podcast.secondary_category,
                    podcast.secondary_subcategory,
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
                       primary_category, primary_subcategory, secondary_category, secondary_subcategory,
                       last_processed
                FROM podcasts WHERE rss_url = ?
                """,
                (str(podcast.rss_url),),
            )
            existing = cursor.fetchone()

            if existing:
                # Compare fields to see if anything changed
                last_processed_str = podcast.last_processed.isoformat() if podcast.last_processed else None
                existing_last_processed = existing["last_processed"]

                changed = (
                    existing["title"] != podcast.title
                    or existing["slug"] != podcast.slug
                    or existing["description"] != podcast.description
                    or existing["image_url"] != podcast.image_url
                    or existing["language"] != podcast.language
                    or existing["primary_category"] != podcast.primary_category
                    or existing["primary_subcategory"] != podcast.primary_subcategory
                    or existing["secondary_category"] != podcast.secondary_category
                    or existing["secondary_subcategory"] != podcast.secondary_subcategory
                    or existing_last_processed != last_processed_str
                )

                if changed:
                    # Update with new updated_at
                    conn.execute(
                        """
                        UPDATE podcasts
                        SET title = ?, slug = ?, description = ?, image_url = ?, language = ?,
                            primary_category = ?, primary_subcategory = ?, secondary_category = ?, secondary_subcategory = ?,
                            last_processed = ?, updated_at = ?
                        WHERE rss_url = ?
                        """,
                        (
                            podcast.title,
                            podcast.slug,
                            podcast.description,
                            podcast.image_url,
                            podcast.language,
                            podcast.primary_category,
                            podcast.primary_subcategory,
                            podcast.secondary_category,
                            podcast.secondary_subcategory,
                            last_processed_str,
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
                                          primary_category, primary_subcategory, secondary_category, secondary_subcategory, last_processed)
                    VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
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
                        podcast.primary_category,
                        podcast.primary_subcategory,
                        podcast.secondary_category,
                        podcast.secondary_subcategory,
                        podcast.last_processed.isoformat() if podcast.last_processed else None,
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

    def _save_episode_idempotent(self, conn: sqlite3.Connection, episode: Episode) -> Episode:
        """
        Internal: Save episode with idempotent updated_at handling.

        Only updates updated_at if data actually changed.
        """
        now = datetime.now(timezone.utc)

        # Check if episode exists (by podcast_id + external_id)
        cursor = conn.execute(
            """
            SELECT id, title, slug, description, description_html, pub_date, audio_url, duration, image_url,
                   audio_path, downsampled_audio_path, raw_transcript_path,
                   clean_transcript_path, summary_path
            FROM episodes
            WHERE podcast_id = ? AND external_id = ?
            """,
            (episode.podcast_id, episode.external_id),
        )
        existing = cursor.fetchone()

        if existing:
            # Compare fields to see if anything changed
            pub_date_str = episode.pub_date.isoformat() if episode.pub_date else None

            changed = (
                existing["title"] != episode.title
                or existing["slug"] != episode.slug
                or existing["description"] != episode.description
                or existing["description_html"] != episode.description_html
                or existing["pub_date"] != pub_date_str
                or existing["audio_url"] != str(episode.audio_url)
                or existing["duration"] != episode.duration
                or existing["image_url"] != episode.image_url
                or existing["audio_path"] != episode.audio_path
                or existing["downsampled_audio_path"] != episode.downsampled_audio_path
                or existing["raw_transcript_path"] != episode.raw_transcript_path
                or existing["clean_transcript_path"] != episode.clean_transcript_path
                or existing["summary_path"] != episode.summary_path
            )

            if changed:
                # Update with new updated_at
                conn.execute(
                    """
                    UPDATE episodes
                    SET title = ?, slug = ?, description = ?, description_html = ?, pub_date = ?, audio_url = ?,
                        duration = ?, image_url = ?, audio_path = ?, downsampled_audio_path = ?,
                        raw_transcript_path = ?, clean_transcript_path = ?, summary_path = ?,
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
                        episode.audio_path,
                        episode.downsampled_audio_path,
                        episode.raw_transcript_path,
                        episode.clean_transcript_path,
                        episode.summary_path,
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
                    description_html, pub_date, audio_url, duration, image_url, audio_path, downsampled_audio_path,
                    raw_transcript_path, clean_transcript_path, summary_path
                ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
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
                    episode.audio_path,
                    episode.downsampled_audio_path,
                    episode.raw_transcript_path,
                    episode.clean_transcript_path,
                    episode.summary_path,
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
            "summary_path",
            "title",
            "slug",
            "description",
            "description_html",
            "duration",
            "image_url",
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
                       p.language as p_language, p.primary_category as p_primary_category,
                       p.primary_subcategory as p_primary_subcategory, p.secondary_category as p_secondary_category,
                       p.secondary_subcategory as p_secondary_subcategory,
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
                       p.language as p_language, p.primary_category as p_primary_category,
                       p.primary_subcategory as p_primary_subcategory, p.secondary_category as p_secondary_category,
                       p.secondary_subcategory as p_secondary_subcategory,
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
                       p.language as p_language, p.primary_category as p_primary_category,
                       p.primary_subcategory as p_primary_subcategory, p.secondary_category as p_secondary_category,
                       p.secondary_subcategory as p_secondary_subcategory,
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
                       p.language as p_language, p.primary_category as p_primary_category,
                       p.primary_subcategory as p_primary_subcategory, p.secondary_category as p_secondary_category,
                       p.secondary_subcategory as p_secondary_subcategory,
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
        sort_by: str = "pub_date",
        sort_order: str = "desc",
    ) -> Tuple[List[Tuple[Podcast, Episode]], int]:
        """
        Get episodes across all podcasts with filtering and pagination.

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
            # Map state to SQL condition (same logic as get_unprocessed_episodes)
            state_conditions = {
                EpisodeState.DISCOVERED.value: "e.audio_path IS NULL",
                EpisodeState.DOWNLOADED.value: "e.audio_path IS NOT NULL AND e.downsampled_audio_path IS NULL",
                EpisodeState.DOWNSAMPLED.value: "e.downsampled_audio_path IS NOT NULL AND e.raw_transcript_path IS NULL",
                EpisodeState.TRANSCRIBED.value: "e.raw_transcript_path IS NOT NULL AND e.clean_transcript_path IS NULL",
                EpisodeState.CLEANED.value: "e.clean_transcript_path IS NOT NULL AND e.summary_path IS NULL",
                EpisodeState.SUMMARIZED.value: "e.summary_path IS NOT NULL",
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
                       p.language as p_language, p.primary_category as p_primary_category,
                       p.primary_subcategory as p_primary_subcategory, p.secondary_category as p_secondary_category,
                       p.secondary_subcategory as p_secondary_subcategory,
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

            return Podcast(
                id=row["id"],
                created_at=datetime.fromisoformat(row["created_at"]),
                rss_url=row["rss_url"],
                title=row["title"],
                slug=row["slug"] or "",
                description=row["description"],
                image_url=row["image_url"],
                language=row["language"] if row["language"] else "en",
                primary_category=row["primary_category"],
                primary_subcategory=row["primary_subcategory"],
                secondary_category=row["secondary_category"],
                secondary_subcategory=row["secondary_subcategory"],
                last_processed=datetime.fromisoformat(row["last_processed"]) if row["last_processed"] else None,
                episodes=episodes,
            )
        except Exception as e:
            logger.error(f"Error in _row_to_podcast: {e}", exc_info=True)
            raise

    def _row_to_podcast_minimal(self, row: sqlite3.Row) -> Podcast:
        """Convert database row to Podcast model without episodes."""
        return Podcast(
            id=row["p_id"],
            created_at=datetime.fromisoformat(row["p_created_at"]),
            rss_url=row["rss_url"],
            title=row["p_title"],
            slug=row["p_slug"] or "",
            description=row["p_description"],
            image_url=row["p_image_url"],
            language=row["p_language"] if row["p_language"] else "en",
            primary_category=row["p_primary_category"],
            primary_subcategory=row["p_primary_subcategory"],
            secondary_category=row["p_secondary_category"],
            secondary_subcategory=row["p_secondary_subcategory"],
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
            audio_path=row["audio_path"],
            downsampled_audio_path=row["downsampled_audio_path"],
            raw_transcript_path=row["raw_transcript_path"],
            clean_transcript_path=row["clean_transcript_path"],
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
                description_html, pub_date, audio_url, duration, image_url, audio_path, downsampled_audio_path,
                raw_transcript_path, clean_transcript_path, summary_path,
                failed_at_stage, failure_reason, failure_type, failed_at
            ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
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
                episode.audio_path,
                episode.downsampled_audio_path,
                episode.raw_transcript_path,
                episode.clean_transcript_path,
                episode.summary_path,
                episode.failed_at_stage,
                episode.failure_reason,
                episode.failure_type.value if episode.failure_type else None,
                episode.failed_at.isoformat() if episode.failed_at else None,
            ),
        )

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
                       p.image_url, p.language, p.primary_category, p.primary_subcategory,
                       p.secondary_category, p.secondary_subcategory, p.last_processed, p.updated_at
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
