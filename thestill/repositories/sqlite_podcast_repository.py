# Copyright 2025 thestill.ai
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
from datetime import datetime
from pathlib import Path
from typing import List, Optional, Tuple

from ..models.podcast import Episode, EpisodeState, Podcast, TranscriptLink
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

            logger.debug("Database schema initialized")

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
                last_processed TIMESTAMP NULL,
                CHECK (length(id) = 36),
                CHECK (length(rss_url) > 0)
            );

            CREATE UNIQUE INDEX IF NOT EXISTS idx_podcasts_rss_url ON podcasts(rss_url);
            CREATE INDEX IF NOT EXISTS idx_podcasts_updated_at ON podcasts(updated_at DESC);
            CREATE INDEX IF NOT EXISTS idx_podcasts_slug ON podcasts(slug) WHERE slug != '';

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
                pub_date TIMESTAMP NULL,
                audio_url TEXT NOT NULL,
                duration TEXT NULL,
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
                SELECT id, created_at, rss_url, title, slug, description, last_processed, updated_at
                FROM podcasts
                ORDER BY created_at ASC
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
                SELECT id, created_at, rss_url, title, slug, description, last_processed, updated_at
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
                SELECT id, created_at, rss_url, title, slug, description, last_processed, updated_at
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
                SELECT id, created_at, rss_url, title, slug, description, last_processed, updated_at
                FROM podcasts
                ORDER BY created_at ASC
                LIMIT 1 OFFSET ?
            """,
                (index - 1,),
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
        Save or update podcast.

        Strategy: UPSERT using INSERT ... ON CONFLICT
        Side effects: updated_at set explicitly here (no trigger)
        """
        with self._get_connection() as conn:
            now = datetime.utcnow()

            # Upsert podcast
            conn.execute(
                """
                INSERT INTO podcasts (id, created_at, updated_at, rss_url, title, slug, description, last_processed)
                VALUES (?, ?, ?, ?, ?, ?, ?, ?)
                ON CONFLICT(rss_url) DO UPDATE SET
                    title = excluded.title,
                    slug = excluded.slug,
                    description = excluded.description,
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
            "duration",
        }

        update_fields = {k: v for k, v in updates.items() if k in valid_fields}
        if not update_fields:
            return False

        set_clause = ", ".join(f"{field} = ?" for field in update_fields.keys())
        values = list(update_fields.values())

        now = datetime.utcnow()

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
                       p.slug as p_slug, p.description as p_description, p.last_processed,
                       p.updated_at as p_updated_at, e.*
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
                       p.slug as p_slug, p.description as p_description, p.last_processed,
                       p.updated_at as p_updated_at, e.*
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
            last_processed=datetime.fromisoformat(row["last_processed"]) if row["last_processed"] else None,
            episodes=[],  # Episodes not loaded
        )

    def _row_to_episode(self, row: sqlite3.Row) -> Episode:
        """Convert database row to Episode model."""
        return Episode(
            id=row["id"],
            created_at=datetime.fromisoformat(row["created_at"]),
            external_id=row["external_id"],
            title=row["title"],
            slug=row["slug"] or "",
            description=row["description"],
            pub_date=datetime.fromisoformat(row["pub_date"]) if row["pub_date"] else None,
            audio_url=row["audio_url"],
            duration=row["duration"],
            audio_path=row["audio_path"],
            downsampled_audio_path=row["downsampled_audio_path"],
            raw_transcript_path=row["raw_transcript_path"],
            clean_transcript_path=row["clean_transcript_path"],
            summary_path=row["summary_path"],
        )

    def _save_episode(self, conn: sqlite3.Connection, podcast_id: str, episode: Episode, now: datetime):
        """Insert episode into database."""
        conn.execute(
            """
            INSERT INTO episodes (
                id, podcast_id, created_at, updated_at, external_id, title, slug, description,
                pub_date, audio_url, duration, audio_path, downsampled_audio_path,
                raw_transcript_path, clean_transcript_path, summary_path
            ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
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
                episode.pub_date.isoformat() if episode.pub_date else None,
                str(episode.audio_url),
                episode.duration,
                episode.audio_path,
                episode.downsampled_audio_path,
                episode.raw_transcript_path,
                episode.clean_transcript_path,
                episode.summary_path,
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
