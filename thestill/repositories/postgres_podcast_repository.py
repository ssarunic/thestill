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
PostgreSQL implementation of podcast repository.

Design principles:
- Raw SQL with parameter binding (no ORM)
- Connection pooling via psycopg2 pool
- Transaction support via context manager
- Pydantic models for type safety
- Compatible schema with SQLite implementation
"""

import logging
from contextlib import contextmanager
from datetime import datetime, timezone
from typing import Any, Dict, List, Optional, Tuple

import psycopg2
import psycopg2.extras
import psycopg2.pool

from ..models.podcast import Episode, EpisodeState, FailureType, Podcast, TranscriptLink
from .podcast_repository import EpisodeRepository, PodcastRepository

logger = logging.getLogger(__name__)


class PostgresPodcastRepository(PodcastRepository, EpisodeRepository):
    """
    PostgreSQL-based podcast repository.

    Thread-safety: Uses connection pool for thread-safe access.
    Transactions: Explicit via transaction() context manager.
    """

    def __init__(self, database_url: str, min_connections: int = 1, max_connections: int = 10):
        """
        Initialize PostgreSQL repository.

        Args:
            database_url: PostgreSQL connection URL
            min_connections: Minimum connections in pool
            max_connections: Maximum connections in pool
        """
        self.database_url = database_url
        self._pool = psycopg2.pool.ThreadedConnectionPool(
            min_connections,
            max_connections,
            database_url,
        )
        self._ensure_schema()
        logger.info("Initialized PostgreSQL repository with connection pool")

    def close(self):
        """Close all connections in the pool."""
        if self._pool:
            self._pool.closeall()
            logger.info("Closed PostgreSQL connection pool")

    def _ensure_schema(self):
        """Create database schema if not exists."""
        with self._get_connection() as conn:
            with conn.cursor() as cursor:
                self._create_schema(cursor)
                self._run_migrations(cursor)
            conn.commit()
            logger.debug("PostgreSQL schema initialized")

    def _run_migrations(self, cursor):
        """Run schema migrations for existing databases."""
        # Check for missing columns and add them
        cursor.execute("""
            SELECT column_name FROM information_schema.columns
            WHERE table_name = 'podcasts'
        """)
        podcast_columns = {row[0] for row in cursor.fetchall()}

        if "image_url" not in podcast_columns:
            logger.info("Migrating: adding image_url to podcasts")
            cursor.execute("ALTER TABLE podcasts ADD COLUMN image_url TEXT")

        if "language" not in podcast_columns:
            logger.info("Migrating: adding language to podcasts")
            cursor.execute("ALTER TABLE podcasts ADD COLUMN language TEXT NOT NULL DEFAULT 'en'")

        cursor.execute("""
            SELECT column_name FROM information_schema.columns
            WHERE table_name = 'episodes'
        """)
        episode_columns = {row[0] for row in cursor.fetchall()}

        if "image_url" not in episode_columns:
            logger.info("Migrating: adding image_url to episodes")
            cursor.execute("ALTER TABLE episodes ADD COLUMN image_url TEXT")

        if "failed_at_stage" not in episode_columns:
            logger.info("Migrating: adding failure tracking to episodes")
            cursor.execute("ALTER TABLE episodes ADD COLUMN failed_at_stage TEXT")
            cursor.execute("ALTER TABLE episodes ADD COLUMN failure_reason TEXT")
            cursor.execute("ALTER TABLE episodes ADD COLUMN failure_type TEXT")
            cursor.execute("ALTER TABLE episodes ADD COLUMN failed_at TIMESTAMPTZ")

        if "description_html" not in episode_columns:
            logger.info("Migrating: adding description_html to episodes")
            cursor.execute("ALTER TABLE episodes ADD COLUMN description_html TEXT NOT NULL DEFAULT ''")

    def _create_schema(self, cursor):
        """Create database schema."""
        cursor.execute("""
            -- ========================================================================
            -- PODCASTS TABLE
            -- ========================================================================
            CREATE TABLE IF NOT EXISTS podcasts (
                id VARCHAR(36) PRIMARY KEY,
                created_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),
                updated_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),
                rss_url TEXT NOT NULL UNIQUE,
                title TEXT NOT NULL,
                slug TEXT NOT NULL DEFAULT '',
                description TEXT NOT NULL DEFAULT '',
                image_url TEXT,
                language TEXT NOT NULL DEFAULT 'en',
                last_processed TIMESTAMPTZ,
                CONSTRAINT chk_podcasts_id_length CHECK (length(id) = 36),
                CONSTRAINT chk_podcasts_rss_url_not_empty CHECK (length(rss_url) > 0)
            );

            CREATE INDEX IF NOT EXISTS idx_podcasts_rss_url ON podcasts(rss_url);
            CREATE INDEX IF NOT EXISTS idx_podcasts_updated_at ON podcasts(updated_at DESC);
            CREATE INDEX IF NOT EXISTS idx_podcasts_slug ON podcasts(slug) WHERE slug != '';
        """)

        cursor.execute("""
            -- ========================================================================
            -- EPISODES TABLE
            -- ========================================================================
            CREATE TABLE IF NOT EXISTS episodes (
                id VARCHAR(36) PRIMARY KEY,
                podcast_id VARCHAR(36) NOT NULL REFERENCES podcasts(id),
                created_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),
                updated_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),
                external_id TEXT NOT NULL,
                title TEXT NOT NULL,
                slug TEXT NOT NULL DEFAULT '',
                description TEXT NOT NULL DEFAULT '',
                description_html TEXT NOT NULL DEFAULT '',
                pub_date TIMESTAMPTZ,
                audio_url TEXT NOT NULL,
                duration INTEGER,
                image_url TEXT,
                audio_path TEXT,
                downsampled_audio_path TEXT,
                raw_transcript_path TEXT,
                clean_transcript_path TEXT,
                summary_path TEXT,
                failed_at_stage TEXT,
                failure_reason TEXT,
                failure_type TEXT,
                failed_at TIMESTAMPTZ,
                CONSTRAINT chk_episodes_id_length CHECK (length(id) = 36),
                CONSTRAINT chk_episodes_external_id_not_empty CHECK (length(external_id) > 0),
                CONSTRAINT chk_episodes_audio_url_not_empty CHECK (length(audio_url) > 0),
                UNIQUE(podcast_id, external_id)
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

            -- Index for failed episodes (missing in original SQLite)
            CREATE INDEX IF NOT EXISTS idx_episodes_failed
                ON episodes(failed_at DESC)
                WHERE failed_at_stage IS NOT NULL;
        """)

        cursor.execute("""
            -- ========================================================================
            -- EPISODE TRANSCRIPT LINKS TABLE
            -- ========================================================================
            CREATE TABLE IF NOT EXISTS episode_transcript_links (
                id SERIAL PRIMARY KEY,
                episode_id VARCHAR(36) NOT NULL REFERENCES episodes(id) ON DELETE CASCADE,
                url TEXT NOT NULL,
                mime_type TEXT NOT NULL,
                language TEXT,
                rel TEXT,
                downloaded_path TEXT,
                created_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),
                CONSTRAINT chk_transcript_url_not_empty CHECK (length(url) > 0),
                CONSTRAINT chk_transcript_mime_not_empty CHECK (length(mime_type) > 0),
                UNIQUE(episode_id, url)
            );

            CREATE INDEX IF NOT EXISTS idx_transcript_links_episode ON episode_transcript_links(episode_id);
            CREATE INDEX IF NOT EXISTS idx_transcript_links_mime_type ON episode_transcript_links(mime_type);
            CREATE INDEX IF NOT EXISTS idx_transcript_links_not_downloaded
                ON episode_transcript_links(episode_id)
                WHERE downloaded_path IS NULL;
        """)

        cursor.execute("""
            -- ========================================================================
            -- USERS TABLE
            -- ========================================================================
            CREATE TABLE IF NOT EXISTS users (
                id VARCHAR(36) PRIMARY KEY,
                email TEXT NOT NULL UNIQUE,
                name TEXT,
                picture TEXT,
                google_id TEXT UNIQUE,
                created_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),
                last_login_at TIMESTAMPTZ,
                CONSTRAINT chk_users_id_length CHECK (length(id) = 36),
                CONSTRAINT chk_users_email_not_empty CHECK (length(email) > 0)
            );

            CREATE INDEX IF NOT EXISTS idx_users_email ON users(email);
            CREATE INDEX IF NOT EXISTS idx_users_google_id ON users(google_id) WHERE google_id IS NOT NULL;
        """)

        cursor.execute("""
            -- ========================================================================
            -- PODCAST FOLLOWERS TABLE
            -- ========================================================================
            CREATE TABLE IF NOT EXISTS podcast_followers (
                id VARCHAR(36) PRIMARY KEY,
                user_id VARCHAR(36) NOT NULL REFERENCES users(id) ON DELETE CASCADE,
                podcast_id VARCHAR(36) NOT NULL REFERENCES podcasts(id) ON DELETE CASCADE,
                created_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),
                CONSTRAINT chk_followers_id_length CHECK (length(id) = 36),
                UNIQUE(user_id, podcast_id)
            );

            CREATE INDEX IF NOT EXISTS idx_podcast_followers_user ON podcast_followers(user_id);
            CREATE INDEX IF NOT EXISTS idx_podcast_followers_podcast ON podcast_followers(podcast_id);
        """)

    @contextmanager
    def _get_connection(self):
        """
        Get database connection from pool.

        Features:
        - Connection pooling for thread safety
        - Automatic return to pool
        - RealDictCursor for dict-like access
        """
        conn = self._pool.getconn()
        try:
            yield conn
        finally:
            self._pool.putconn(conn)

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
            with conn.cursor(cursor_factory=psycopg2.extras.RealDictCursor) as cursor:
                cursor.execute("""
                    SELECT id, created_at, rss_url, title, slug, description,
                           image_url, language, last_processed, updated_at
                    FROM podcasts
                    ORDER BY created_at DESC
                """)

                podcasts = []
                for row in cursor.fetchall():
                    podcast = self._row_to_podcast(row, cursor)
                    podcasts.append(podcast)

                return podcasts

    def get(self, podcast_id: str) -> Optional[Podcast]:
        """Get podcast by internal UUID (primary key)."""
        with self._get_connection() as conn:
            with conn.cursor(cursor_factory=psycopg2.extras.RealDictCursor) as cursor:
                cursor.execute("""
                    SELECT id, created_at, rss_url, title, slug, description,
                           image_url, language, last_processed, updated_at
                    FROM podcasts
                    WHERE id = %s
                """, (podcast_id,))

                row = cursor.fetchone()
                if row:
                    return self._row_to_podcast(row, cursor)
                return None

    def get_by_id(self, podcast_id: str) -> Optional[Podcast]:
        """Find podcast by internal UUID."""
        return self.get(podcast_id)

    def get_by_url(self, url: str) -> Optional[Podcast]:
        """Find podcast by RSS URL."""
        with self._get_connection() as conn:
            with conn.cursor(cursor_factory=psycopg2.extras.RealDictCursor) as cursor:
                cursor.execute("""
                    SELECT id, created_at, rss_url, title, slug, description,
                           image_url, language, last_processed, updated_at
                    FROM podcasts
                    WHERE rss_url = %s
                """, (url,))

                row = cursor.fetchone()
                if row:
                    return self._row_to_podcast(row, cursor)
                return None

    def get_by_index(self, index: int) -> Optional[Podcast]:
        """Find podcast by 1-based index."""
        if index < 1:
            return None

        with self._get_connection() as conn:
            with conn.cursor(cursor_factory=psycopg2.extras.RealDictCursor) as cursor:
                cursor.execute("""
                    SELECT id, created_at, rss_url, title, slug, description,
                           image_url, language, last_processed, updated_at
                    FROM podcasts
                    ORDER BY created_at DESC
                    LIMIT 1 OFFSET %s
                """, (index - 1,))

                row = cursor.fetchone()
                if row:
                    return self._row_to_podcast(row, cursor)
                return None

    def get_by_slug(self, slug: str) -> Optional[Podcast]:
        """Find podcast by URL-safe slug."""
        if not slug:
            return None

        with self._get_connection() as conn:
            with conn.cursor(cursor_factory=psycopg2.extras.RealDictCursor) as cursor:
                cursor.execute("""
                    SELECT id, created_at, rss_url, title, slug, description,
                           image_url, language, last_processed, updated_at
                    FROM podcasts
                    WHERE slug = %s
                """, (slug,))

                row = cursor.fetchone()
                if row:
                    return self._row_to_podcast(row, cursor)
                return None

    def exists(self, url: str) -> bool:
        """Check if podcast exists."""
        with self._get_connection() as conn:
            with conn.cursor() as cursor:
                cursor.execute("SELECT 1 FROM podcasts WHERE rss_url = %s LIMIT 1", (url,))
                return cursor.fetchone() is not None

    def save(self, podcast: Podcast) -> Podcast:
        """
        Save or update podcast with ALL episodes (destructive).

        WARNING: This method DELETES all existing episodes and re-inserts them.
        """
        with self._get_connection() as conn:
            with conn.cursor() as cursor:
                now = datetime.now(timezone.utc)

                # Upsert podcast
                cursor.execute("""
                    INSERT INTO podcasts (id, created_at, updated_at, rss_url, title, slug,
                                         description, image_url, language, last_processed)
                    VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s, %s)
                    ON CONFLICT (rss_url) DO UPDATE SET
                        title = EXCLUDED.title,
                        slug = EXCLUDED.slug,
                        description = EXCLUDED.description,
                        image_url = EXCLUDED.image_url,
                        language = EXCLUDED.language,
                        last_processed = EXCLUDED.last_processed,
                        updated_at = %s
                """, (
                    podcast.id,
                    podcast.created_at,
                    now,
                    str(podcast.rss_url),
                    podcast.title,
                    podcast.slug,
                    podcast.description,
                    podcast.image_url,
                    podcast.language,
                    podcast.last_processed,
                    now,
                ))

                # Get final podcast_id
                cursor.execute("SELECT id FROM podcasts WHERE rss_url = %s", (str(podcast.rss_url),))
                podcast_id = cursor.fetchone()[0]

                # Delete existing episodes
                cursor.execute("DELETE FROM episodes WHERE podcast_id = %s", (podcast_id,))

                # Insert all episodes
                for episode in podcast.episodes:
                    self._save_episode_internal(cursor, podcast_id, episode, now)

            conn.commit()
            logger.debug(f"Saved podcast: {podcast.title} ({len(podcast.episodes)} episodes)")
            return podcast

    def save_podcast(self, podcast: Podcast) -> Podcast:
        """Save or update podcast metadata only. Does NOT touch episodes."""
        with self._get_connection() as conn:
            with conn.cursor(cursor_factory=psycopg2.extras.RealDictCursor) as cursor:
                now = datetime.now(timezone.utc)

                # Check if podcast exists
                cursor.execute("""
                    SELECT id, title, slug, description, image_url, language, last_processed
                    FROM podcasts WHERE rss_url = %s
                """, (str(podcast.rss_url),))
                existing = cursor.fetchone()

                if existing:
                    # Compare fields
                    changed = (
                        existing["title"] != podcast.title
                        or existing["slug"] != podcast.slug
                        or existing["description"] != podcast.description
                        or existing["image_url"] != podcast.image_url
                        or existing["language"] != podcast.language
                        or existing["last_processed"] != podcast.last_processed
                    )

                    if changed:
                        cursor.execute("""
                            UPDATE podcasts
                            SET title = %s, slug = %s, description = %s, image_url = %s,
                                language = %s, last_processed = %s, updated_at = %s
                            WHERE rss_url = %s
                        """, (
                            podcast.title,
                            podcast.slug,
                            podcast.description,
                            podcast.image_url,
                            podcast.language,
                            podcast.last_processed,
                            now,
                            str(podcast.rss_url),
                        ))
                        logger.debug(f"Updated podcast metadata: {podcast.title}")
                else:
                    cursor.execute("""
                        INSERT INTO podcasts (id, created_at, updated_at, rss_url, title, slug,
                                             description, image_url, language, last_processed)
                        VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s, %s)
                    """, (
                        podcast.id,
                        podcast.created_at,
                        now,
                        str(podcast.rss_url),
                        podcast.title,
                        podcast.slug,
                        podcast.description,
                        podcast.image_url,
                        podcast.language,
                        podcast.last_processed,
                    ))
                    logger.debug(f"Inserted new podcast: {podcast.title}")

            conn.commit()
            return podcast

    def save_episode(self, episode: Episode) -> Episode:
        """Save or update a single episode."""
        if not episode.podcast_id:
            raise ValueError("episode.podcast_id must be set before saving")

        with self._get_connection() as conn:
            with conn.cursor(cursor_factory=psycopg2.extras.RealDictCursor) as cursor:
                result = self._save_episode_idempotent(cursor, episode)
            conn.commit()
            return result

    def save_episodes(self, episodes: List[Episode]) -> List[Episode]:
        """Save or update multiple episodes in a single transaction."""
        if not episodes:
            return []

        for ep in episodes:
            if not ep.podcast_id:
                raise ValueError(f"episode.podcast_id must be set for episode: {ep.title}")

        with self._get_connection() as conn:
            with conn.cursor(cursor_factory=psycopg2.extras.RealDictCursor) as cursor:
                results = [self._save_episode_idempotent(cursor, ep) for ep in episodes]
            conn.commit()
            return results

    def _save_episode_idempotent(self, cursor, episode: Episode) -> Episode:
        """Internal: Save episode with idempotent updated_at handling."""
        now = datetime.now(timezone.utc)

        cursor.execute("""
            SELECT id, title, slug, description, description_html, pub_date, audio_url,
                   duration, image_url, audio_path, downsampled_audio_path,
                   raw_transcript_path, clean_transcript_path, summary_path
            FROM episodes
            WHERE podcast_id = %s AND external_id = %s
        """, (episode.podcast_id, episode.external_id))
        existing = cursor.fetchone()

        if existing:
            changed = (
                existing["title"] != episode.title
                or existing["slug"] != episode.slug
                or existing["description"] != episode.description
                or existing["description_html"] != episode.description_html
                or existing["pub_date"] != episode.pub_date
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
                cursor.execute("""
                    UPDATE episodes
                    SET title = %s, slug = %s, description = %s, description_html = %s,
                        pub_date = %s, audio_url = %s, duration = %s, image_url = %s,
                        audio_path = %s, downsampled_audio_path = %s, raw_transcript_path = %s,
                        clean_transcript_path = %s, summary_path = %s, updated_at = %s
                    WHERE podcast_id = %s AND external_id = %s
                """, (
                    episode.title,
                    episode.slug,
                    episode.description,
                    episode.description_html,
                    episode.pub_date,
                    str(episode.audio_url),
                    episode.duration,
                    episode.image_url,
                    episode.audio_path,
                    episode.downsampled_audio_path,
                    episode.raw_transcript_path,
                    episode.clean_transcript_path,
                    episode.summary_path,
                    now,
                    episode.podcast_id,
                    episode.external_id,
                ))
                logger.debug(f"Updated episode: {episode.title}")
        else:
            cursor.execute("""
                INSERT INTO episodes (
                    id, podcast_id, created_at, updated_at, external_id, title, slug,
                    description, description_html, pub_date, audio_url, duration, image_url,
                    audio_path, downsampled_audio_path, raw_transcript_path,
                    clean_transcript_path, summary_path
                ) VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s)
            """, (
                episode.id,
                episode.podcast_id,
                episode.created_at,
                now,
                episode.external_id,
                episode.title,
                episode.slug,
                episode.description,
                episode.description_html,
                episode.pub_date,
                str(episode.audio_url),
                episode.duration,
                episode.image_url,
                episode.audio_path,
                episode.downsampled_audio_path,
                episode.raw_transcript_path,
                episode.clean_transcript_path,
                episode.summary_path,
            ))
            logger.debug(f"Inserted new episode: {episode.title}")

        return episode

    def delete(self, url: str) -> bool:
        """Delete podcast by URL."""
        with self._get_connection() as conn:
            with conn.cursor() as cursor:
                cursor.execute("SELECT id FROM podcasts WHERE rss_url = %s", (url,))
                row = cursor.fetchone()
                if not row:
                    return False

                podcast_id = row[0]
                cursor.execute("DELETE FROM episodes WHERE podcast_id = %s", (podcast_id,))
                cursor.execute("DELETE FROM podcasts WHERE id = %s", (podcast_id,))

            conn.commit()
            logger.info(f"Deleted podcast: {url}")
            return True

    def update_episode(self, podcast_url: str, episode_external_id: str, updates: dict) -> bool:
        """Update specific episode fields."""
        valid_fields = {
            "audio_path", "downsampled_audio_path", "raw_transcript_path",
            "clean_transcript_path", "summary_path", "title", "slug",
            "description", "description_html", "duration", "image_url",
            "failed_at_stage", "failure_reason", "failure_type", "failed_at",
        }

        update_fields = {k: v for k, v in updates.items() if k in valid_fields}
        if not update_fields:
            return False

        set_clause = ", ".join(f"{field} = %s" for field in update_fields.keys())
        values = list(update_fields.values())
        now = datetime.now(timezone.utc)

        with self._get_connection() as conn:
            with conn.cursor() as cursor:
                cursor.execute(f"""
                    UPDATE episodes
                    SET {set_clause}, updated_at = %s
                    WHERE podcast_id = (SELECT id FROM podcasts WHERE rss_url = %s)
                      AND external_id = %s
                """, values + [now, podcast_url, episode_external_id])

                updated = cursor.rowcount > 0
            conn.commit()

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
        """Mark an episode as failed at a specific stage."""
        now = datetime.now(timezone.utc)

        with self._get_connection() as conn:
            with conn.cursor() as cursor:
                cursor.execute("""
                    UPDATE episodes
                    SET failed_at_stage = %s, failure_reason = %s, failure_type = %s,
                        failed_at = %s, updated_at = %s
                    WHERE id = %s
                """, (failed_at_stage, failure_reason, failure_type, now, now, episode_id))

                updated = cursor.rowcount > 0
            conn.commit()

            if updated:
                logger.info(f"Marked episode {episode_id} as failed at '{failed_at_stage}' ({failure_type})")
            return updated

    def clear_episode_failure(self, episode_id: str) -> bool:
        """Clear failure state from an episode."""
        now = datetime.now(timezone.utc)

        with self._get_connection() as conn:
            with conn.cursor() as cursor:
                cursor.execute("""
                    UPDATE episodes
                    SET failed_at_stage = NULL, failure_reason = NULL, failure_type = NULL,
                        failed_at = NULL, updated_at = %s
                    WHERE id = %s
                """, (now, episode_id))

                updated = cursor.rowcount > 0
            conn.commit()

            if updated:
                logger.info(f"Cleared failure state for episode {episode_id}")
            return updated

    def get_failed_episodes(self, limit: int = 100) -> List[Tuple[Podcast, Episode]]:
        """Get episodes in failed state."""
        with self._get_connection() as conn:
            with conn.cursor(cursor_factory=psycopg2.extras.RealDictCursor) as cursor:
                cursor.execute("""
                    SELECT p.id as p_id, p.created_at as p_created_at, p.rss_url, p.title as p_title,
                           p.slug as p_slug, p.description as p_description, p.image_url as p_image_url,
                           p.language as p_language, p.last_processed, p.updated_at as p_updated_at, e.*
                    FROM episodes e
                    JOIN podcasts p ON e.podcast_id = p.id
                    WHERE e.failed_at_stage IS NOT NULL
                    ORDER BY e.failed_at DESC
                    LIMIT %s
                """, (limit,))

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
            with conn.cursor(cursor_factory=psycopg2.extras.RealDictCursor) as cursor:
                cursor.execute("""
                    SELECT e.*
                    FROM episodes e
                    JOIN podcasts p ON e.podcast_id = p.id
                    WHERE p.rss_url = %s
                    ORDER BY e.pub_date DESC
                """, (podcast_url,))

                return [self._row_to_episode(row) for row in cursor.fetchall()]

    def get_episode(self, episode_id: str) -> Optional[Tuple[Podcast, Episode]]:
        """Get episode by internal UUID."""
        with self._get_connection() as conn:
            with conn.cursor(cursor_factory=psycopg2.extras.RealDictCursor) as cursor:
                cursor.execute("""
                    SELECT p.id as p_id, p.created_at as p_created_at, p.rss_url, p.title as p_title,
                           p.slug as p_slug, p.description as p_description, p.image_url as p_image_url,
                           p.language as p_language, p.last_processed, p.updated_at as p_updated_at, e.*
                    FROM episodes e
                    JOIN podcasts p ON e.podcast_id = p.id
                    WHERE e.id = %s
                """, (episode_id,))

                row = cursor.fetchone()
                if not row:
                    return None

                podcast = self._row_to_podcast_minimal(row)
                episode = self._row_to_episode(row)
                return (podcast, episode)

    def get_episode_by_external_id(self, podcast_url: str, episode_external_id: str) -> Optional[Episode]:
        """Get specific episode by external ID."""
        with self._get_connection() as conn:
            with conn.cursor(cursor_factory=psycopg2.extras.RealDictCursor) as cursor:
                cursor.execute("""
                    SELECT e.*
                    FROM episodes e
                    JOIN podcasts p ON e.podcast_id = p.id
                    WHERE p.rss_url = %s AND e.external_id = %s
                """, (podcast_url, episode_external_id))

                row = cursor.fetchone()
                return self._row_to_episode(row) if row else None

    def get_episode_by_slug(self, podcast_slug: str, episode_slug: str) -> Optional[Tuple[Podcast, Episode]]:
        """Get episode by podcast slug and episode slug."""
        if not podcast_slug or not episode_slug:
            return None

        with self._get_connection() as conn:
            with conn.cursor(cursor_factory=psycopg2.extras.RealDictCursor) as cursor:
                cursor.execute("""
                    SELECT p.id as p_id, p.created_at as p_created_at, p.rss_url, p.title as p_title,
                           p.slug as p_slug, p.description as p_description, p.image_url as p_image_url,
                           p.language as p_language, p.last_processed, p.updated_at as p_updated_at, e.*
                    FROM episodes e
                    JOIN podcasts p ON e.podcast_id = p.id
                    WHERE p.slug = %s AND e.slug = %s
                """, (podcast_slug, episode_slug))

                row = cursor.fetchone()
                if not row:
                    return None

                podcast = self._row_to_podcast_minimal(row)
                episode = self._row_to_episode(row)
                return (podcast, episode)

    def get_unprocessed_episodes(self, state: str) -> List[Tuple[Podcast, Episode]]:
        """Get episodes in specific processing state."""
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
            with conn.cursor(cursor_factory=psycopg2.extras.RealDictCursor) as cursor:
                cursor.execute(f"""
                    SELECT p.id as p_id, p.created_at as p_created_at, p.rss_url, p.title as p_title,
                           p.slug as p_slug, p.description as p_description, p.image_url as p_image_url,
                           p.language as p_language, p.last_processed, p.updated_at as p_updated_at, e.*
                    FROM episodes e
                    JOIN podcasts p ON e.podcast_id = p.id
                    WHERE {condition}
                    ORDER BY e.pub_date DESC
                """)

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
        """Get episodes with filtering and pagination."""
        conditions = []
        params: List[Any] = []

        if search:
            conditions.append("e.title ILIKE %s")
            params.append(f"%{search}%")

        if podcast_id:
            conditions.append("e.podcast_id = %s")
            params.append(podcast_id)

        if state:
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
            conditions.append("e.pub_date >= %s")
            params.append(date_from)

        if date_to:
            conditions.append("e.pub_date <= %s")
            params.append(date_to)

        where_clause = " AND ".join(conditions) if conditions else "1=1"

        valid_sort_fields = {"pub_date": "e.pub_date", "title": "e.title", "updated_at": "e.updated_at"}
        sort_field = valid_sort_fields.get(sort_by, "e.pub_date")
        order_direction = "ASC" if sort_order.lower() == "asc" else "DESC"

        with self._get_connection() as conn:
            with conn.cursor(cursor_factory=psycopg2.extras.RealDictCursor) as cursor:
                # Get total count
                cursor.execute(f"""
                    SELECT COUNT(*) as total
                    FROM episodes e
                    JOIN podcasts p ON e.podcast_id = p.id
                    WHERE {where_clause}
                """, params)
                total = cursor.fetchone()["total"]

                # Get paginated results
                cursor.execute(f"""
                    SELECT p.id as p_id, p.created_at as p_created_at, p.rss_url, p.title as p_title,
                           p.slug as p_slug, p.description as p_description, p.image_url as p_image_url,
                           p.language as p_language, p.last_processed, p.updated_at as p_updated_at, e.*
                    FROM episodes e
                    JOIN podcasts p ON e.podcast_id = p.id
                    WHERE {where_clause}
                    ORDER BY {sort_field} {order_direction}
                    LIMIT %s OFFSET %s
                """, params + [limit, offset])

                results = []
                for row in cursor.fetchall():
                    podcast = self._row_to_podcast_minimal(row)
                    episode = self._row_to_episode(row)
                    results.append((podcast, episode))

                return results, total

    def get_episode_state_counts(self) -> Dict[str, int]:
        """
        Get counts of episodes in each processing state.

        Uses SQL COUNT aggregates for O(1) memory usage instead of loading all episodes.
        """
        with self._get_connection() as conn:
            with conn.cursor(cursor_factory=psycopg2.extras.RealDictCursor) as cursor:
                cursor.execute("""
                    SELECT
                        COUNT(*) as total,
                        SUM(CASE WHEN audio_path IS NULL THEN 1 ELSE 0 END) as discovered,
                        SUM(CASE WHEN audio_path IS NOT NULL AND downsampled_audio_path IS NULL THEN 1 ELSE 0 END) as downloaded,
                        SUM(CASE WHEN downsampled_audio_path IS NOT NULL AND raw_transcript_path IS NULL THEN 1 ELSE 0 END) as downsampled,
                        SUM(CASE WHEN raw_transcript_path IS NOT NULL AND clean_transcript_path IS NULL THEN 1 ELSE 0 END) as transcribed,
                        SUM(CASE WHEN clean_transcript_path IS NOT NULL AND summary_path IS NULL THEN 1 ELSE 0 END) as cleaned,
                        SUM(CASE WHEN summary_path IS NOT NULL THEN 1 ELSE 0 END) as summarized,
                        SUM(CASE WHEN failed_at_stage IS NOT NULL THEN 1 ELSE 0 END) as failed
                    FROM episodes
                """)

                row = cursor.fetchone()
                return {
                    "total": row["total"] or 0,
                    "discovered": row["discovered"] or 0,
                    "downloaded": row["downloaded"] or 0,
                    "downsampled": row["downsampled"] or 0,
                    "transcribed": row["transcribed"] or 0,
                    "cleaned": row["cleaned"] or 0,
                    "summarized": row["summarized"] or 0,
                    "failed": row["failed"] or 0,
                }

    def get_recent_activity(
        self,
        limit: int = 20,
        offset: int = 0,
    ) -> List[Tuple[Podcast, Episode]]:
        """
        Get recently updated episodes for activity feed.

        Uses SQL ORDER BY and LIMIT instead of loading all episodes.
        """
        with self._get_connection() as conn:
            with conn.cursor(cursor_factory=psycopg2.extras.RealDictCursor) as cursor:
                cursor.execute("""
                    SELECT p.id as p_id, p.created_at as p_created_at, p.rss_url, p.title as p_title,
                           p.slug as p_slug, p.description as p_description, p.image_url as p_image_url,
                           p.language as p_language, p.last_processed, p.updated_at as p_updated_at, e.*
                    FROM episodes e
                    JOIN podcasts p ON e.podcast_id = p.id
                    ORDER BY e.updated_at DESC
                    LIMIT %s OFFSET %s
                """, (limit, offset))

                results = []
                for row in cursor.fetchall():
                    podcast = self._row_to_podcast_minimal(row)
                    episode = self._row_to_episode(row)
                    results.append((podcast, episode))

                return results

    # ============================================================================
    # Helper Methods
    # ============================================================================

    def _row_to_podcast(self, row: dict, cursor) -> Podcast:
        """Convert database row to Podcast model with episodes."""
        cursor.execute("""
            SELECT * FROM episodes WHERE podcast_id = %s ORDER BY pub_date DESC
        """, (row["id"],))

        episodes = [self._row_to_episode(ep_row) for ep_row in cursor.fetchall()]

        return Podcast(
            id=row["id"],
            created_at=row["created_at"],
            rss_url=row["rss_url"],
            title=row["title"],
            slug=row["slug"] or "",
            description=row["description"],
            image_url=row["image_url"],
            language=row["language"] if row["language"] else "en",
            last_processed=row["last_processed"],
            episodes=episodes,
        )

    def _row_to_podcast_minimal(self, row: dict) -> Podcast:
        """Convert database row to Podcast model without episodes."""
        return Podcast(
            id=row["p_id"],
            created_at=row["p_created_at"],
            rss_url=row["rss_url"],
            title=row["p_title"],
            slug=row["p_slug"] or "",
            description=row["p_description"],
            image_url=row["p_image_url"],
            language=row["p_language"] if row["p_language"] else "en",
            last_processed=row["last_processed"],
            episodes=[],
        )

    def _row_to_episode(self, row: dict) -> Episode:
        """Convert database row to Episode model."""
        failure_type = None
        if row.get("failure_type"):
            try:
                failure_type = FailureType(row["failure_type"])
            except ValueError:
                logger.warning(f"Unknown failure_type '{row['failure_type']}' for episode {row['id']}")

        return Episode(
            id=row["id"],
            podcast_id=row["podcast_id"],
            created_at=row["created_at"],
            updated_at=row["updated_at"],
            external_id=row["external_id"],
            title=row["title"],
            slug=row["slug"] or "",
            description=row["description"],
            description_html=row.get("description_html", ""),
            pub_date=row["pub_date"],
            audio_url=row["audio_url"],
            duration=row["duration"],
            image_url=row.get("image_url"),
            audio_path=row["audio_path"],
            downsampled_audio_path=row["downsampled_audio_path"],
            raw_transcript_path=row["raw_transcript_path"],
            clean_transcript_path=row["clean_transcript_path"],
            summary_path=row["summary_path"],
            failed_at_stage=row.get("failed_at_stage"),
            failure_reason=row.get("failure_reason"),
            failure_type=failure_type,
            failed_at=row.get("failed_at"),
        )

    def _save_episode_internal(self, cursor, podcast_id: str, episode: Episode, now: datetime):
        """Insert episode into database."""
        cursor.execute("""
            INSERT INTO episodes (
                id, podcast_id, created_at, updated_at, external_id, title, slug,
                description, description_html, pub_date, audio_url, duration, image_url,
                audio_path, downsampled_audio_path, raw_transcript_path,
                clean_transcript_path, summary_path, failed_at_stage, failure_reason,
                failure_type, failed_at
            ) VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s)
        """, (
            episode.id,
            podcast_id,
            episode.created_at,
            now,
            episode.external_id,
            episode.title,
            episode.slug,
            episode.description,
            episode.description_html,
            episode.pub_date,
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
            episode.failed_at,
        ))

    # ============================================================================
    # TranscriptLink Methods
    # ============================================================================

    def get_transcript_links(self, episode_id: str) -> List[TranscriptLink]:
        """Get all transcript links for an episode."""
        with self._get_connection() as conn:
            with conn.cursor(cursor_factory=psycopg2.extras.RealDictCursor) as cursor:
                cursor.execute("""
                    SELECT id, episode_id, url, mime_type, language, rel, downloaded_path, created_at
                    FROM episode_transcript_links
                    WHERE episode_id = %s
                    ORDER BY created_at ASC
                """, (episode_id,))

                return [self._row_to_transcript_link(row) for row in cursor.fetchall()]

    def add_transcript_links(self, episode_id: str, links: List[TranscriptLink]) -> int:
        """Add transcript links for an episode."""
        if not links:
            return 0

        inserted = 0
        with self._get_connection() as conn:
            with conn.cursor() as cursor:
                for link in links:
                    try:
                        cursor.execute("""
                            INSERT INTO episode_transcript_links (episode_id, url, mime_type, language, rel)
                            VALUES (%s, %s, %s, %s, %s)
                        """, (episode_id, str(link.url), link.mime_type, link.language, link.rel))
                        inserted += 1
                    except psycopg2.errors.UniqueViolation:
                        conn.rollback()
                        logger.debug(f"Transcript link already exists: {link.url}")
                        continue

            conn.commit()

        if inserted > 0:
            logger.debug(f"Added {inserted} transcript links for episode {episode_id}")

        return inserted

    def mark_transcript_downloaded(self, link_id: int, local_path: str) -> bool:
        """Mark a transcript link as downloaded."""
        with self._get_connection() as conn:
            with conn.cursor() as cursor:
                cursor.execute("""
                    UPDATE episode_transcript_links
                    SET downloaded_path = %s
                    WHERE id = %s
                """, (local_path, link_id))
                updated = cursor.rowcount > 0
            conn.commit()
            return updated

    def get_episodes_with_undownloaded_transcript_links(
        self, podcast_id: Optional[str] = None
    ) -> List[Tuple[Episode, List[TranscriptLink]]]:
        """Get episodes that have transcript links not yet downloaded."""
        with self._get_connection() as conn:
            with conn.cursor(cursor_factory=psycopg2.extras.RealDictCursor) as cursor:
                if podcast_id:
                    cursor.execute("""
                        SELECT DISTINCT e.*
                        FROM episodes e
                        INNER JOIN episode_transcript_links etl ON e.id = etl.episode_id
                        WHERE etl.downloaded_path IS NULL AND e.podcast_id = %s
                        ORDER BY e.pub_date DESC
                    """, (podcast_id,))
                else:
                    cursor.execute("""
                        SELECT DISTINCT e.*
                        FROM episodes e
                        INNER JOIN episode_transcript_links etl ON e.id = etl.episode_id
                        WHERE etl.downloaded_path IS NULL
                        ORDER BY e.pub_date DESC
                    """)

                results = []
                for row in cursor.fetchall():
                    episode = self._row_to_episode(row)
                    cursor.execute("""
                        SELECT id, episode_id, url, mime_type, language, rel, downloaded_path, created_at
                        FROM episode_transcript_links
                        WHERE episode_id = %s AND downloaded_path IS NULL
                    """, (episode.id,))
                    links = [self._row_to_transcript_link(link_row) for link_row in cursor.fetchall()]
                    results.append((episode, links))

                return results

    def _row_to_transcript_link(self, row: dict) -> TranscriptLink:
        """Convert database row to TranscriptLink model."""
        return TranscriptLink(
            id=row["id"],
            episode_id=row["episode_id"],
            url=row["url"],
            mime_type=row["mime_type"],
            language=row["language"],
            rel=row["rel"],
            downloaded_path=row["downloaded_path"],
            created_at=row["created_at"],
        )

    def get_podcast_for_episode(self, episode_id: str) -> Optional[Podcast]:
        """Get the podcast that owns a specific episode."""
        with self._get_connection() as conn:
            with conn.cursor(cursor_factory=psycopg2.extras.RealDictCursor) as cursor:
                cursor.execute("""
                    SELECT p.id, p.created_at, p.rss_url, p.title, p.slug, p.description,
                           p.image_url, p.language, p.last_processed, p.updated_at
                    FROM podcasts p
                    INNER JOIN episodes e ON e.podcast_id = p.id
                    WHERE e.id = %s
                """, (episode_id,))

                row = cursor.fetchone()
                if row:
                    return self._row_to_podcast(row, cursor)
                return None
