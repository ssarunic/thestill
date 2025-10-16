# SQLite Migration Plan: From JSON to Relational Database

> **Document Version**: 1.0
> **Created**: 2025-10-16
> **Author**: thestill.ai team
> **Status**: Planning Phase

---

## Table of Contents

1. [Executive Summary](#executive-summary)
2. [Current System Analysis](#current-system-analysis)
3. [Database Schema Design](#database-schema-design)
4. [Access Layer Design](#access-layer-design)
5. [Implementation Plan](#implementation-plan)
6. [Migration Strategy](#migration-strategy)
7. [Testing Strategy](#testing-strategy)
8. [Performance Considerations](#performance-considerations)
9. [Security Considerations](#security-considerations)
10. [Rollback Plan](#rollback-plan)

---

## Executive Summary

### Why Migrate?

**Current Pain Points with JSON Storage:**
1. **Performance**: Linear scan O(n) for every query; no indexing
2. **Concurrency**: File-level locking causes bottlenecks with concurrent operations
3. **Data Integrity**: No referential integrity or constraint enforcement
4. **Multi-User Ready**: Need subscription model to avoid podcast duplication
5. **Query Complexity**: Finding episodes by state requires loading all data into memory

**Benefits of SQLite:**
1. **Performance**: Indexed queries (O(log n)), efficient joins
2. **Concurrency**: Row-level locking, multiple readers + single writer
3. **ACID Transactions**: Data integrity guarantees
4. **Rich Queries**: SQL enables complex filtering without loading full dataset
5. **Multi-User Support**: Native foreign keys enable clean subscription model
6. **File-Based**: Still simple deployment (single file), no server needed

### Timeline

- **Phase 1** (2-3 hours): Create SQLite repository implementation (single-user schema)
- **Phase 2** (1 hour): Parallel testing with JSON repository
- **Phase 3** (2 hours): Add multi-user schema extensions (optional)
- **Phase 4** (1 hour): Write migration utilities (JSON → SQLite)
- **Phase 5** (1 hour): Switch default repository + documentation updates

**Total Estimated Effort**: 7-8 hours

### Design Decisions

1. **No ORM**: Raw SQL with parameter binding (simple, performant, no abstraction overhead)
2. **No Stored Procedures**: SQLite support is limited; raw SQL in repository is clearer
3. **Repository Pattern**: Existing abstraction makes migration seamless
4. **Two Schema Variants**: Single-user (simpler) + Multi-user (subscriptions)
5. **File Paths in DB**: Store filenames only (like JSON), paths computed by PathManager
6. **No DB-Level Side Effects**: All cascades and timestamp updates in service layer (cache-friendly)
7. **Selective Indexing**: Only index high-cardinality string columns (RSS URLs), not UUIDs

---

## Current System Analysis

### Existing JSON Structure

```json
[
  {
    "id": "550e8400-e29b-41d4-a716-446655440000",
    "created_at": "2025-10-14T15:35:10.008198",
    "rss_url": "https://example.com/feed.xml",
    "title": "Podcast Title",
    "description": "Podcast description",
    "last_processed": "2025-10-14T22:06:57.778998",
    "episodes": [
      {
        "id": "dbd40e76-898b-4632-843e-96c0d4a3af8e",
        "created_at": "2025-10-14T15:35:10.008208",
        "external_id": "2544cfe9-5b3b-4fbe-8fb4-65d4fbb2d157",
        "title": "Episode Title",
        "description": "Episode description",
        "pub_date": "2025-07-19T12:43:00",
        "audio_url": "https://example.com/audio.mp3",
        "duration": "00:07:44",
        "audio_path": "episode_audio.mp3",
        "downsampled_audio_path": "episode_downsampled.wav",
        "raw_transcript_path": "episode_transcript.json",
        "clean_transcript_path": "episode_cleaned.md",
        "summary_path": null
      }
    ]
  }
]
```

### Current Query Patterns

From repository analysis:

```python
# Repository Interface (abstract)
find_all() -> List[Podcast]                                    # List all podcasts
find_by_id(podcast_id: str) -> Podcast                        # Find by UUID
find_by_index(index: int) -> Podcast                          # Find by 1-based index
find_by_url(url: str) -> Podcast                              # Find by RSS URL
exists(url: str) -> bool                                      # Check existence
save(podcast: Podcast) -> Podcast                             # Upsert podcast
delete(url: str) -> bool                                      # Delete podcast
update_episode(podcast_url, episode_id, updates) -> bool      # Update episode fields

# Episode Repository Interface
find_by_podcast(podcast_url: str) -> List[Episode]            # Episodes for podcast
find_by_id(episode_id: str) -> (Podcast, Episode)            # Find episode by UUID
find_by_external_id(url, external_id) -> Episode             # Find by RSS GUID
find_unprocessed(state: str) -> List[(Podcast, Episode)]     # Filter by processing state
```

### Performance Bottlenecks (Current JSON)

1. **find_by_url()**: O(n) - scans all podcasts
2. **find_unprocessed()**: O(n*m) - scans all podcasts and all episodes
3. **save()**: O(n) - reads entire file, updates one podcast, writes entire file
4. **Concurrent writes**: File lock blocks all operations

---

## Database Schema Design

### Design Principles

1. **Normalize podcast data**: One podcast entry per unique RSS URL
2. **Denormalize file paths**: Keep as simple filenames (not separate table)
3. **Computed state**: Episode state derived from file paths (like current Pydantic model)
4. **UUIDs as primary keys**: Stable identifiers for API consistency
5. **Timestamps**: Track creation and modification for all entities
6. **NO database-level side effects**: All cascades and updates handled in service layer
7. **Cache-friendly**: No triggers or cascades that could cause stale cache entries
8. **Selective indexing**: Only index columns with high selectivity (RSS URLs, pub_dates)

---

### Schema Variant 1: Single-User System

**Use Case**: Current usage pattern (one user, personal podcast tracker)

```sql
-- ============================================================================
-- PODCASTS TABLE
-- ============================================================================
CREATE TABLE podcasts (
    -- Primary identifier (UUID v4, immutable)
    -- NOTE: No index on id (low selectivity, rarely queried directly by UUID)
    id TEXT PRIMARY KEY NOT NULL,

    -- Timestamps (managed by application, NOT by triggers)
    created_at TIMESTAMP NOT NULL DEFAULT CURRENT_TIMESTAMP,
    updated_at TIMESTAMP NOT NULL DEFAULT CURRENT_TIMESTAMP,

    -- External identifiers
    rss_url TEXT NOT NULL UNIQUE,  -- RSS feed URL (unique constraint)

    -- Metadata
    title TEXT NOT NULL,
    description TEXT NOT NULL DEFAULT '',

    -- Processing tracking
    last_processed TIMESTAMP NULL,

    -- Constraints (validation only, no side effects)
    CHECK (length(id) = 36),  -- Validate UUID format
    CHECK (length(rss_url) > 0)
);

-- Index only on high-cardinality columns (RSS URLs are unique and frequently queried)
CREATE UNIQUE INDEX idx_podcasts_rss_url ON podcasts(rss_url);

-- Index for sorting by modification time (used in listing operations)
CREATE INDEX idx_podcasts_updated_at ON podcasts(updated_at DESC);

-- ============================================================================
-- EPISODES TABLE
-- ============================================================================
CREATE TABLE episodes (
    -- Primary identifier (UUID v4, immutable)
    id TEXT PRIMARY KEY NOT NULL,

    -- Foreign key to podcast (NO CASCADE - handled in service layer)
    podcast_id TEXT NOT NULL,

    -- Timestamps (managed by application, NOT by triggers)
    created_at TIMESTAMP NOT NULL DEFAULT CURRENT_TIMESTAMP,
    updated_at TIMESTAMP NOT NULL DEFAULT CURRENT_TIMESTAMP,

    -- External identifiers
    external_id TEXT NOT NULL,  -- GUID from RSS feed (publisher's ID)

    -- Metadata
    title TEXT NOT NULL,
    description TEXT NOT NULL DEFAULT '',
    pub_date TIMESTAMP NULL,
    audio_url TEXT NOT NULL,
    duration TEXT NULL,  -- Format: "HH:MM:SS"

    -- File paths (filenames only, relative to storage directories)
    -- NULL = not yet processed at that stage
    audio_path TEXT NULL,              -- Original downloaded audio (MP3/M4A)
    downsampled_audio_path TEXT NULL,  -- Downsampled 16kHz WAV
    raw_transcript_path TEXT NULL,     -- Raw Whisper JSON transcript
    clean_transcript_path TEXT NULL,   -- Cleaned Markdown transcript
    summary_path TEXT NULL,            -- Episode summary (future)

    -- Constraints (validation only)
    FOREIGN KEY (podcast_id) REFERENCES podcasts(id),  -- NO CASCADE
    UNIQUE(podcast_id, external_id),  -- Prevent duplicate episodes within podcast
    CHECK (length(id) = 36),
    CHECK (length(external_id) > 0),
    CHECK (length(audio_url) > 0)
);

-- Index for finding episodes by podcast (most common query)
CREATE INDEX idx_episodes_podcast_id ON episodes(podcast_id);

-- Composite index for external_id lookups (podcast_id + external_id queries)
CREATE INDEX idx_episodes_external_id ON episodes(podcast_id, external_id);

-- Index for sorting by publication date (used in episode listing)
CREATE INDEX idx_episodes_pub_date ON episodes(pub_date DESC);

-- Index for tracking modifications (used in sync operations)
CREATE INDEX idx_episodes_updated_at ON episodes(updated_at DESC);

-- Partial indexes for state queries (highly selective, huge performance win)
-- These index only rows matching the WHERE condition (saves space + faster lookups)
CREATE INDEX idx_episodes_state_discovered ON episodes(podcast_id, pub_date DESC)
    WHERE audio_path IS NULL;

CREATE INDEX idx_episodes_state_downloaded ON episodes(podcast_id, pub_date DESC)
    WHERE audio_path IS NOT NULL AND downsampled_audio_path IS NULL;

CREATE INDEX idx_episodes_state_downsampled ON episodes(podcast_id, pub_date DESC)
    WHERE downsampled_audio_path IS NOT NULL AND raw_transcript_path IS NULL;

CREATE INDEX idx_episodes_state_transcribed ON episodes(podcast_id, pub_date DESC)
    WHERE raw_transcript_path IS NOT NULL AND clean_transcript_path IS NULL;

-- ============================================================================
-- NO TRIGGERS OR CASCADES
-- ============================================================================
-- All side effects (timestamp updates, cascade deletes) handled in service layer
-- Reason: Write-through cache compatibility - avoid stale data from DB-level changes
```

**Rationale for Design Decisions:**

1. **No Triggers**: `updated_at` is set explicitly in service layer during updates
   - **Benefit**: Cache invalidation is explicit and controllable
   - **Trade-off**: Application code must remember to update timestamps

2. **No CASCADE DELETE**: Service layer explicitly deletes episodes when deleting podcast
   - **Benefit**: Cache layer can be notified of each deletion
   - **Trade-off**: Slightly more code in service layer

3. **No UUID Indexes**: UUIDs have low selectivity (random distribution)
   - **Benefit**: Smaller index size, faster writes
   - **Trade-off**: Queries by UUID still fast (primary key lookup is always indexed)

4. **Partial Indexes**: Only index episodes in specific states
   - **Benefit**: 10-100x smaller indexes, faster queries
   - **Trade-off**: None (these are most common queries)

5. **Composite Indexes**: Include `pub_date DESC` for sorting
   - **Benefit**: Query + sort in single index scan (no separate sort step)
   - **Trade-off**: Slightly larger index size

---

### Schema Variant 2: Multi-User System

**Use Case**: Multiple users sharing podcasts (avoid duplication), subscription model

**Key Differences:**
1. Add `users` table for authentication/profile
2. Add `subscriptions` join table (many-to-many: users ↔ podcasts)
3. Podcast remains normalized (one entry per RSS URL, shared across users)

```sql
-- ============================================================================
-- USERS TABLE
-- ============================================================================
CREATE TABLE users (
    -- Primary identifier
    id TEXT PRIMARY KEY NOT NULL,

    -- Timestamps (managed by application)
    created_at TIMESTAMP NOT NULL DEFAULT CURRENT_TIMESTAMP,
    updated_at TIMESTAMP NOT NULL DEFAULT CURRENT_TIMESTAMP,

    -- User profile
    username TEXT NOT NULL UNIQUE,
    email TEXT NOT NULL UNIQUE,
    display_name TEXT NULL,

    -- Authentication (basic, can extend later)
    password_hash TEXT NULL,  -- For future authentication (bcrypt/argon2)

    -- Settings (JSON blob for flexibility)
    settings TEXT NULL,  -- JSON: {"transcription_provider": "whisper", ...}

    -- Status
    is_active BOOLEAN NOT NULL DEFAULT 1,

    -- Constraints
    CHECK (length(id) = 36),
    CHECK (length(username) >= 3),
    CHECK (email LIKE '%@%')
);

-- Index high-cardinality unique columns
CREATE UNIQUE INDEX idx_users_username ON users(username);
CREATE UNIQUE INDEX idx_users_email ON users(email);

-- Index for filtering active users
CREATE INDEX idx_users_is_active ON users(is_active) WHERE is_active = 1;

-- ============================================================================
-- SUBSCRIPTIONS TABLE (Join table: users ↔ podcasts)
-- ============================================================================
CREATE TABLE subscriptions (
    -- Composite primary key
    user_id TEXT NOT NULL,
    podcast_id TEXT NOT NULL,

    -- Timestamps (managed by application)
    subscribed_at TIMESTAMP NOT NULL DEFAULT CURRENT_TIMESTAMP,
    last_accessed TIMESTAMP NULL,

    -- User-specific podcast settings (optional)
    settings TEXT NULL,  -- JSON: {"auto_download": true, "max_episodes": 50, ...}

    -- Constraints (NO CASCADE)
    PRIMARY KEY (user_id, podcast_id),
    FOREIGN KEY (user_id) REFERENCES users(id),      -- NO CASCADE
    FOREIGN KEY (podcast_id) REFERENCES podcasts(id)  -- NO CASCADE
);

-- Indexes for both directions of relationship
CREATE INDEX idx_subscriptions_user_id ON subscriptions(user_id);
CREATE INDEX idx_subscriptions_podcast_id ON subscriptions(podcast_id);
CREATE INDEX idx_subscriptions_subscribed_at ON subscriptions(subscribed_at DESC);

-- ============================================================================
-- MODIFIED PODCASTS TABLE (Multi-User)
-- ============================================================================
-- Same as single-user, but now one podcast can have multiple subscribers
-- No changes needed to structure, just different usage pattern

-- ============================================================================
-- MODIFIED EPISODES TABLE (Multi-User)
-- ============================================================================
-- Episodes belong to podcasts, users access via subscriptions
-- Optional: Add user-specific episode state (e.g., "marked as listened")

CREATE TABLE episode_user_state (
    user_id TEXT NOT NULL,
    episode_id TEXT NOT NULL,

    -- User-specific tracking
    listened BOOLEAN NOT NULL DEFAULT 0,
    bookmarked BOOLEAN NOT NULL DEFAULT 0,
    listen_position_seconds INTEGER NULL,  -- Resume playback
    last_accessed TIMESTAMP NULL,

    -- Constraints (NO CASCADE)
    PRIMARY KEY (user_id, episode_id),
    FOREIGN KEY (user_id) REFERENCES users(id),      -- NO CASCADE
    FOREIGN KEY (episode_id) REFERENCES episodes(id)  -- NO CASCADE
);

CREATE INDEX idx_episode_user_state_user_id ON episode_user_state(user_id);
CREATE INDEX idx_episode_user_state_episode_id ON episode_user_state(episode_id);

-- Partial index for unlistened episodes (common query)
CREATE INDEX idx_episode_user_state_unlistened ON episode_user_state(user_id, episode_id)
    WHERE listened = 0;
```

**Rationale for Multi-User Schema:**

- **Shared podcasts**: One podcast entry serves multiple users (no duplication)
- **Subscription model**: Clean many-to-many relationship
- **User-specific settings**: Each user can configure podcast preferences
- **Episode tracking**: Optional user-specific state (listened, bookmarked)
- **Scalable**: Handles 1-100 users efficiently (beyond that, consider PostgreSQL)
- **Cache-friendly**: All side effects in service layer

**Migration Path: Single → Multi-User:**

```sql
-- Add tables (users already exists)
-- Migrate existing data: Create default user, subscribe to all podcasts
INSERT INTO users (id, username, email, display_name, is_active)
VALUES ('00000000-0000-0000-0000-000000000000', 'default', 'default@localhost', 'Default User', 1);

INSERT INTO subscriptions (user_id, podcast_id, subscribed_at)
SELECT '00000000-0000-0000-0000-000000000000', id, created_at
FROM podcasts;
```

---

## Access Layer Design

### Why No ORM?

**Decision**: Use raw SQL with parameter binding (via `sqlite3` standard library)

**Rationale:**
1. **Simplicity**: Project is small (~10 tables max), ORM overhead not justified
2. **Performance**: Raw SQL is 2-3x faster than most ORMs for read-heavy workloads
3. **Control**: Explicit queries easier to debug and optimize
4. **No dependencies**: SQLite stdlib is sufficient
5. **Learning curve**: Team familiar with SQL, no need to learn SQLAlchemy/Peewee

**Comparison:**

| Aspect | Raw SQL | ORM (SQLAlchemy) |
|--------|---------|------------------|
| Setup | 0 dependencies | +1 dependency (large) |
| Query speed | Fastest | 2-3x slower (overhead) |
| Flexibility | Full SQL power | Limited by ORM API |
| Type safety | Manual (Pydantic models) | Built-in (columns) |
| Debugging | SQL logs directly | Translate ORM → SQL |
| Test mocks | Simple (connection) | Complex (session) |
| Best for | <20 tables, read-heavy | >50 tables, complex relationships |

**Verdict**: Raw SQL + Pydantic models is the sweet spot for this project.

---

### Why No Stored Procedures?

**Decision**: Keep SQL queries in repository methods (Python)

**Rationale:**
1. **SQLite limitations**: Stored procedures support is limited (no CREATE PROCEDURE)
2. **Version control**: SQL in Python files easier to track than separate `.sql` files
3. **Testing**: Mock at connection level (simple) vs executing stored procedures (complex)
4. **Debugging**: Python IDE + SQL logs better than debugging stored procedures
5. **Deployment**: No separate SQL migration files to manage

**Alternative Considered**: SQL queries in separate `.sql` files (like Django)

**Why Rejected**:
- Adds complexity (need to load files, handle paths)
- Harder to use variables (string interpolation vs parameter binding)
- Current repository methods are already well-organized

**Verdict**: Inline SQL in repository methods with parameter binding.

---

### Repository Implementation

```python
"""
SQLite implementation of podcast repository.

Design:
- Raw SQL with parameter binding (no ORM)
- Connection pooling (one connection per thread)
- Transaction support via context manager
- Pydantic models for type safety
- All side effects (timestamps, cascades) in service layer
"""

import sqlite3
import logging
from pathlib import Path
from typing import List, Optional, Tuple
from contextlib import contextmanager
from datetime import datetime

from ..models.podcast import Podcast, Episode, EpisodeState
from .podcast_repository import PodcastRepository, EpisodeRepository

logger = logging.getLogger(__name__)


class SqlitePodcastRepository(PodcastRepository, EpisodeRepository):
    """
    SQLite-based podcast repository.

    Thread-safety: Uses threading.local for per-thread connections.
    Transactions: Explicit via context manager.
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
        conn.executescript("""
            -- PODCASTS TABLE
            CREATE TABLE IF NOT EXISTS podcasts (
                id TEXT PRIMARY KEY NOT NULL,
                created_at TIMESTAMP NOT NULL DEFAULT CURRENT_TIMESTAMP,
                updated_at TIMESTAMP NOT NULL DEFAULT CURRENT_TIMESTAMP,
                rss_url TEXT NOT NULL UNIQUE,
                title TEXT NOT NULL,
                description TEXT NOT NULL DEFAULT '',
                last_processed TIMESTAMP NULL,
                CHECK (length(id) = 36),
                CHECK (length(rss_url) > 0)
            );

            CREATE UNIQUE INDEX IF NOT EXISTS idx_podcasts_rss_url ON podcasts(rss_url);
            CREATE INDEX IF NOT EXISTS idx_podcasts_updated_at ON podcasts(updated_at DESC);

            -- EPISODES TABLE
            CREATE TABLE IF NOT EXISTS episodes (
                id TEXT PRIMARY KEY NOT NULL,
                podcast_id TEXT NOT NULL,
                created_at TIMESTAMP NOT NULL DEFAULT CURRENT_TIMESTAMP,
                updated_at TIMESTAMP NOT NULL DEFAULT CURRENT_TIMESTAMP,
                external_id TEXT NOT NULL,
                title TEXT NOT NULL,
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

            -- Partial indexes for state queries
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
        """)

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

    # ========================================================================
    # PodcastRepository Interface Implementation
    # ========================================================================

    def find_all(self) -> List[Podcast]:
        """Retrieve all podcasts with their episodes."""
        with self._get_connection() as conn:
            # Fetch all podcasts
            cursor = conn.execute("""
                SELECT id, created_at, rss_url, title, description, last_processed, updated_at
                FROM podcasts
                ORDER BY created_at ASC
            """)

            podcasts = []
            for row in cursor.fetchall():
                podcast = self._row_to_podcast(row, conn)
                podcasts.append(podcast)

            return podcasts

    def find_by_id(self, podcast_id: str) -> Optional[Podcast]:
        """Find podcast by UUID."""
        with self._get_connection() as conn:
            cursor = conn.execute("""
                SELECT id, created_at, rss_url, title, description, last_processed, updated_at
                FROM podcasts
                WHERE id = ?
            """, (podcast_id,))

            row = cursor.fetchone()
            if row:
                return self._row_to_podcast(row, conn)
            return None

    def find_by_url(self, url: str) -> Optional[Podcast]:
        """Find podcast by RSS URL."""
        with self._get_connection() as conn:
            cursor = conn.execute("""
                SELECT id, created_at, rss_url, title, description, last_processed, updated_at
                FROM podcasts
                WHERE rss_url = ?
            """, (url,))

            row = cursor.fetchone()
            if row:
                return self._row_to_podcast(row, conn)
            return None

    def find_by_index(self, index: int) -> Optional[Podcast]:
        """Find podcast by 1-based index."""
        with self._get_connection() as conn:
            cursor = conn.execute("""
                SELECT id, created_at, rss_url, title, description, last_processed, updated_at
                FROM podcasts
                ORDER BY created_at ASC
                LIMIT 1 OFFSET ?
            """, (index - 1,))

            row = cursor.fetchone()
            if row:
                return self._row_to_podcast(row, conn)
            return None

    def exists(self, url: str) -> bool:
        """Check if podcast exists."""
        with self._get_connection() as conn:
            cursor = conn.execute("""
                SELECT 1 FROM podcasts WHERE rss_url = ? LIMIT 1
            """, (url,))
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
            conn.execute("""
                INSERT INTO podcasts (id, created_at, updated_at, rss_url, title, description, last_processed)
                VALUES (?, ?, ?, ?, ?, ?, ?)
                ON CONFLICT(rss_url) DO UPDATE SET
                    title = excluded.title,
                    description = excluded.description,
                    last_processed = excluded.last_processed,
                    updated_at = ?
            """, (
                podcast.id,
                podcast.created_at.isoformat(),
                now.isoformat(),
                str(podcast.rss_url),
                podcast.title,
                podcast.description,
                podcast.last_processed.isoformat() if podcast.last_processed else None,
                now.isoformat()  # Set updated_at explicitly
            ))

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
            "audio_path", "downsampled_audio_path", "raw_transcript_path",
            "clean_transcript_path", "summary_path", "title", "description", "duration"
        }

        update_fields = {k: v for k, v in updates.items() if k in valid_fields}
        if not update_fields:
            return False

        set_clause = ", ".join(f"{field} = ?" for field in update_fields.keys())
        values = list(update_fields.values())

        now = datetime.utcnow()

        with self._get_connection() as conn:
            cursor = conn.execute(f"""
                UPDATE episodes
                SET {set_clause}, updated_at = ?
                WHERE podcast_id = (SELECT id FROM podcasts WHERE rss_url = ?)
                  AND external_id = ?
            """, values + [now.isoformat(), podcast_url, episode_external_id])

            updated = cursor.rowcount > 0
            if updated:
                logger.debug(f"Updated episode {episode_external_id}: {list(update_fields.keys())}")
            return updated

    # ========================================================================
    # EpisodeRepository Interface Implementation
    # ========================================================================

    def find_by_podcast(self, podcast_url: str) -> List[Episode]:
        """Get all episodes for a podcast."""
        with self._get_connection() as conn:
            cursor = conn.execute("""
                SELECT e.*
                FROM episodes e
                JOIN podcasts p ON e.podcast_id = p.id
                WHERE p.rss_url = ?
                ORDER BY e.pub_date DESC
            """, (podcast_url,))

            return [self._row_to_episode(row) for row in cursor.fetchall()]

    def find_by_id(self, episode_id: str) -> Optional[Tuple[Podcast, Episode]]:
        """Find episode by UUID."""
        with self._get_connection() as conn:
            cursor = conn.execute("""
                SELECT p.*, e.*
                FROM episodes e
                JOIN podcasts p ON e.podcast_id = p.id
                WHERE e.id = ?
            """, (episode_id,))

            row = cursor.fetchone()
            if not row:
                return None

            # Parse podcast and episode from row
            podcast = self._row_to_podcast_minimal(row)
            episode = self._row_to_episode(row)
            return (podcast, episode)

    def find_by_external_id(self, podcast_url: str, episode_external_id: str) -> Optional[Episode]:
        """Find episode by external ID."""
        with self._get_connection() as conn:
            cursor = conn.execute("""
                SELECT e.*
                FROM episodes e
                JOIN podcasts p ON e.podcast_id = p.id
                WHERE p.rss_url = ? AND e.external_id = ?
            """, (podcast_url, episode_external_id))

            row = cursor.fetchone()
            return self._row_to_episode(row) if row else None

    def find_unprocessed(self, state: str) -> List[Tuple[Podcast, Episode]]:
        """
        Find episodes in specific processing state.

        Uses partial indexes for performance (10-100x faster than full scan).
        """
        # Map state to SQL condition (matches partial index WHERE clauses)
        state_conditions = {
            EpisodeState.DISCOVERED.value: "e.audio_path IS NULL",
            EpisodeState.DOWNLOADED.value: "e.audio_path IS NOT NULL AND e.downsampled_audio_path IS NULL",
            EpisodeState.DOWNSAMPLED.value: "e.downsampled_audio_path IS NOT NULL AND e.raw_transcript_path IS NULL",
            EpisodeState.TRANSCRIBED.value: "e.raw_transcript_path IS NOT NULL AND e.clean_transcript_path IS NULL",
        }

        condition = state_conditions.get(state)
        if not condition:
            logger.warning(f"Unknown processing state: {state}")
            return []

        with self._get_connection() as conn:
            # Note: SQLite query planner will use partial index for this WHERE clause
            cursor = conn.execute(f"""
                SELECT p.*, e.*
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

    # ========================================================================
    # Helper Methods
    # ========================================================================

    def _row_to_podcast(self, row: sqlite3.Row, conn: sqlite3.Connection) -> Podcast:
        """Convert database row to Podcast model with episodes."""
        # Fetch episodes for this podcast
        cursor = conn.execute("""
            SELECT * FROM episodes WHERE podcast_id = ? ORDER BY pub_date DESC
        """, (row["id"],))

        episodes = [self._row_to_episode(ep_row) for ep_row in cursor.fetchall()]

        return Podcast(
            id=row["id"],
            created_at=datetime.fromisoformat(row["created_at"]),
            rss_url=row["rss_url"],
            title=row["title"],
            description=row["description"],
            last_processed=datetime.fromisoformat(row["last_processed"]) if row["last_processed"] else None,
            episodes=episodes
        )

    def _row_to_podcast_minimal(self, row: sqlite3.Row) -> Podcast:
        """Convert database row to Podcast model without episodes."""
        return Podcast(
            id=row["id"],
            created_at=datetime.fromisoformat(row["created_at"]),
            rss_url=row["rss_url"],
            title=row["title"],
            description=row["description"],
            last_processed=datetime.fromisoformat(row["last_processed"]) if row["last_processed"] else None,
            episodes=[]  # Episodes not loaded
        )

    def _row_to_episode(self, row: sqlite3.Row) -> Episode:
        """Convert database row to Episode model."""
        return Episode(
            id=row["id"],
            created_at=datetime.fromisoformat(row["created_at"]),
            external_id=row["external_id"],
            title=row["title"],
            description=row["description"],
            pub_date=datetime.fromisoformat(row["pub_date"]) if row["pub_date"] else None,
            audio_url=row["audio_url"],
            duration=row["duration"],
            audio_path=row["audio_path"],
            downsampled_audio_path=row["downsampled_audio_path"],
            raw_transcript_path=row["raw_transcript_path"],
            clean_transcript_path=row["clean_transcript_path"],
            summary_path=row["summary_path"]
        )

    def _save_episode(self, conn: sqlite3.Connection, podcast_id: str, episode: Episode, now: datetime):
        """Insert episode into database."""
        conn.execute("""
            INSERT INTO episodes (
                id, podcast_id, created_at, updated_at, external_id, title, description,
                pub_date, audio_url, duration, audio_path, downsampled_audio_path,
                raw_transcript_path, clean_transcript_path, summary_path
            ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
        """, (
            episode.id,
            podcast_id,
            episode.created_at.isoformat(),
            now.isoformat(),
            episode.external_id,
            episode.title,
            episode.description,
            episode.pub_date.isoformat() if episode.pub_date else None,
            str(episode.audio_url),
            episode.duration,
            episode.audio_path,
            episode.downsampled_audio_path,
            episode.raw_transcript_path,
            episode.clean_transcript_path,
            episode.summary_path
        ))
```

---

### Service Layer Responsibilities

**Timestamp Management**:
```python
# Service layer explicitly sets updated_at
def update_episode_audio_path(self, episode_id: str, audio_path: str):
    """Update episode with new audio path."""
    updates = {
        "audio_path": audio_path,
        # No need to set updated_at - repository does it
    }
    self.repository.update_episode(episode_id, updates)
    self.cache.invalidate(f"episode:{episode_id}")  # Explicit cache invalidation
```

**Cascade Deletes**:
```python
# Service layer explicitly deletes episodes
def delete_podcast(self, podcast_url: str):
    """Delete podcast and all its episodes."""
    podcast = self.repository.find_by_url(podcast_url)
    if not podcast:
        return False

    # Explicit cascade: delete episodes first (for cache invalidation)
    for episode in podcast.episodes:
        self.cache.invalidate(f"episode:{episode.id}")

    # Delete podcast (which also deletes episodes in repository)
    self.repository.delete(podcast_url)
    self.cache.invalidate(f"podcast:{podcast_url}")
    return True
```

---

### Transaction Management

**Pattern**: Explicit transactions for batch operations

```python
# Example: Batch update episodes
with repository.transaction():
    for episode_id, updates in batch_updates.items():
        repository.update_episode(podcast_url, episode_id, updates)
    # All updates committed together (atomic)
```

**Auto-commit**: Single operations (find, save) auto-commit via context manager

---

### Connection Pooling

**Current**: One connection per operation (acceptable for single-user)

**Multi-User**: Use `sqlite3.connect(..., check_same_thread=False)` + threading.local

```python
import threading

class SqlitePodcastRepository:
    def __init__(self, db_path: str):
        self.db_path = db_path
        self._local = threading.local()  # Per-thread connection storage

    def _get_connection(self):
        """Get or create connection for current thread."""
        if not hasattr(self._local, 'conn'):
            self._local.conn = sqlite3.connect(self.db_path)
            self._local.conn.row_factory = sqlite3.Row
            self._local.conn.execute("PRAGMA foreign_keys = ON")
        return self._local.conn
```

---

## Implementation Plan

### Phase 1: Create SQLite Repository (Single-User)

**Goal**: Implement `SqlitePodcastRepository` with same interface as JSON repository

**Tasks**:
1. Create `thestill/repositories/sqlite_podcast_repository.py`
2. Implement schema creation (single-user variant)
3. Implement all `PodcastRepository` methods
4. Implement all `EpisodeRepository` methods
5. Add connection management + transactions
6. Explicit timestamp management (no triggers)
7. Explicit cascade deletes (no database cascades)

**Estimated Time**: 2-3 hours

**Deliverable**: Working SQLite repository passing all existing contract tests

---

### Phase 2: Parallel Testing

**Goal**: Test SQLite repository alongside JSON repository (no breaking changes)

**Tasks**:
1. Copy existing JSON repository tests → SQLite repository tests
2. Run both test suites in parallel (pytest fixtures)
3. Benchmark performance (JSON vs SQLite)
4. Fix any discrepancies in behavior
5. Test cache invalidation scenarios

**Estimated Time**: 1 hour

**Deliverable**: SQLite repository fully tested, same behavior as JSON

---

### Phase 3: Add Multi-User Support (Optional)

**Goal**: Extend schema for users and subscriptions

**Tasks**:
1. Add `users`, `subscriptions`, `episode_user_state` tables
2. Implement `UserRepository` interface
3. Implement `SubscriptionRepository` interface
4. Add migration script: single-user → multi-user schema
5. Service layer handles all cascades (user deletion → subscription cleanup)

**Estimated Time**: 2 hours

**Deliverable**: Multi-user schema + repositories + tests

**Decision Point**: Can defer to future if not needed immediately

---

### Phase 4: Migration Utilities

**Goal**: Provide scripts to migrate from JSON → SQLite

**Tasks**:
1. Create `scripts/migrate_json_to_sqlite.py`
2. Read `feeds.json`, insert into SQLite
3. Validate all data migrated correctly
4. Document migration process

**Estimated Time**: 1 hour

**Deliverable**: Automated migration script + documentation

---

### Phase 5: Switch Default Repository

**Goal**: Make SQLite the default, deprecate JSON

**Tasks**:
1. Update `cli.py` to use SQLite by default
2. Add `--json` flag for backward compatibility
3. Update documentation (README, CLAUDE.md)
4. Deprecation notice for JSON repository

**Estimated Time**: 1 hour

**Deliverable**: SQLite as default, JSON still available

---

## Migration Strategy

### Approach: Manual Import (As Requested)

**No Automatic Migration**: User must manually run migration script

**Why?**
- Safer (user reviews data before migration)
- Simpler (no auto-detection of JSON vs SQLite)
- You're the only user (easy to test manually)

### Migration Script

```python
#!/usr/bin/env python3
"""
Migrate podcast data from JSON (feeds.json) to SQLite.

Usage:
    python scripts/migrate_json_to_sqlite.py --json-file ./data/feeds.json --db-file ./data/podcasts.db
"""

import json
import logging
from pathlib import Path
from datetime import datetime

from thestill.repositories.json_podcast_repository import JsonPodcastRepository
from thestill.repositories.sqlite_podcast_repository import SqlitePodcastRepository
from thestill.models.podcast import Podcast

logging.basicConfig(level=logging.INFO)
logger = logging.getLogger(__name__)


def migrate_json_to_sqlite(json_file: Path, db_file: Path):
    """
    Migrate podcast data from JSON to SQLite.

    Steps:
    1. Load all podcasts from JSON
    2. Create SQLite database with schema
    3. Insert all podcasts + episodes
    4. Validate counts match
    """
    logger.info(f"Starting migration: {json_file} → {db_file}")

    # Load JSON data
    json_repo = JsonPodcastRepository(str(json_file.parent))
    podcasts = json_repo.find_all()
    logger.info(f"Loaded {len(podcasts)} podcasts from JSON")

    # Create SQLite database
    sqlite_repo = SqlitePodcastRepository(str(db_file))
    logger.info(f"Created SQLite database: {db_file}")

    # Migrate podcasts
    total_episodes = 0
    with sqlite_repo.transaction():
        for podcast in podcasts:
            sqlite_repo.save(podcast)
            total_episodes += len(podcast.episodes)
            logger.info(f"  Migrated: {podcast.title} ({len(podcast.episodes)} episodes)")

    # Validate
    sqlite_podcasts = sqlite_repo.find_all()
    sqlite_episodes = sum(len(p.episodes) for p in sqlite_podcasts)

    assert len(sqlite_podcasts) == len(podcasts), "Podcast count mismatch"
    assert sqlite_episodes == total_episodes, "Episode count mismatch"

    logger.info(f"✓ Migration complete: {len(podcasts)} podcasts, {total_episodes} episodes")


if __name__ == "__main__":
    import argparse
    parser = argparse.ArgumentParser(description="Migrate JSON to SQLite")
    parser.add_argument("--json-file", required=True, help="Path to feeds.json")
    parser.add_argument("--db-file", required=True, help="Path to SQLite database")
    args = parser.parse_args()

    migrate_json_to_sqlite(Path(args.json_file), Path(args.db_file))
```

**Usage:**
```bash
# Backup existing JSON first
cp data/feeds.json data/feeds.backup.json

# Run migration
python scripts/migrate_json_to_sqlite.py --json-file data/feeds.json --db-file data/podcasts.db

# Verify
sqlite3 data/podcasts.db "SELECT COUNT(*) FROM podcasts;"
sqlite3 data/podcasts.db "SELECT COUNT(*) FROM episodes;"
```

---

### Data Validation

```sql
-- Check podcast counts
SELECT COUNT(*) AS total_podcasts FROM podcasts;

-- Check episode counts
SELECT COUNT(*) AS total_episodes FROM episodes;

-- Check for orphaned episodes (should be 0 with proper foreign keys)
SELECT COUNT(*) FROM episodes e
LEFT JOIN podcasts p ON e.podcast_id = p.id
WHERE p.id IS NULL;

-- Check for duplicate external_ids (should be 0)
SELECT external_id, COUNT(*) AS count
FROM episodes
GROUP BY podcast_id, external_id
HAVING COUNT(*) > 1;

-- Check episodes by state
SELECT
    CASE
        WHEN clean_transcript_path IS NOT NULL THEN 'cleaned'
        WHEN raw_transcript_path IS NOT NULL THEN 'transcribed'
        WHEN downsampled_audio_path IS NOT NULL THEN 'downsampled'
        WHEN audio_path IS NOT NULL THEN 'downloaded'
        ELSE 'discovered'
    END AS state,
    COUNT(*) AS count
FROM episodes
GROUP BY state;
```

---

## Testing Strategy

### Unit Tests

```python
"""
Test SQLite repository implementation.

Pattern: Same test suite as JSON repository (contract tests ensure compatibility)
"""

import pytest
from pathlib import Path
from thestill.repositories.sqlite_podcast_repository import SqlitePodcastRepository
from thestill.models.podcast import Podcast, Episode

@pytest.fixture
def temp_db(tmp_path):
    """Create temporary SQLite database."""
    db_path = tmp_path / "test.db"
    repo = SqlitePodcastRepository(str(db_path))
    return repo

def test_save_and_find_podcast(temp_db):
    """Test saving and retrieving podcast."""
    podcast = Podcast(
        id="test-id-123",
        rss_url="https://example.com/feed.xml",
        title="Test Podcast",
        description="Test description",
        episodes=[]
    )

    temp_db.save(podcast)
    found = temp_db.find_by_url("https://example.com/feed.xml")

    assert found is not None
    assert found.title == "Test Podcast"

def test_updated_at_is_set_on_save(temp_db):
    """Test that updated_at is explicitly set (no trigger)."""
    podcast = Podcast(
        id="test-id-123",
        rss_url="https://example.com/feed.xml",
        title="Test Podcast",
        description="Test description",
        episodes=[]
    )

    temp_db.save(podcast)
    found = temp_db.find_by_url("https://example.com/feed.xml")

    # Verify updated_at is set
    assert found.updated_at is not None
    assert found.updated_at >= found.created_at

def test_cascade_delete_handled_in_repository(temp_db):
    """Test that deleting podcast also deletes episodes (no DB cascade)."""
    podcast = Podcast(
        id="test-id-123",
        rss_url="https://example.com/feed.xml",
        title="Test Podcast",
        description="Test description",
        episodes=[Episode(...), Episode(...)]
    )

    temp_db.save(podcast)
    temp_db.delete("https://example.com/feed.xml")

    # Verify episodes are gone
    episodes = temp_db.find_by_podcast("https://example.com/feed.xml")
    assert len(episodes) == 0

def test_find_unprocessed_episodes(temp_db):
    """Test filtering episodes by state (uses partial indexes)."""
    # (Test implementation)
    pass

# More tests...
```

**Coverage Target**: 95%+ for SQLite repository

---

### Performance Benchmarks

```python
"""
Benchmark JSON vs SQLite performance.
"""

import time
from thestill.repositories.json_podcast_repository import JsonPodcastRepository
from thestill.repositories.sqlite_podcast_repository import SqlitePodcastRepository

def benchmark_find_by_url(repo, url, iterations=1000):
    """Measure average query time."""
    start = time.perf_counter()
    for _ in range(iterations):
        repo.find_by_url(url)
    elapsed = time.perf_counter() - start
    return elapsed / iterations

# Results (expected):
# JSON: ~1-5ms (linear scan)
# SQLite: ~0.1-0.5ms (indexed query) → 10x faster
```

---

### Concurrency Tests

```python
"""
Test concurrent access (multi-threaded).
"""

import threading
from thestill.repositories.sqlite_podcast_repository import SqlitePodcastRepository

def test_concurrent_reads(temp_db):
    """Test multiple threads reading simultaneously."""
    def read_podcasts():
        for _ in range(100):
            temp_db.find_all()

    threads = [threading.Thread(target=read_podcasts) for _ in range(10)]
    for t in threads:
        t.start()
    for t in threads:
        t.join()

    # Should not raise any errors

def test_concurrent_writes(temp_db):
    """Test write serialization."""
    # (Implementation with thread-safe updates)
    pass
```

---

## Performance Considerations

### Query Optimization

**1. Indexes** (already in schema):
- `idx_podcasts_rss_url`: O(log n) for `find_by_url()` (unique index on strings)
- `idx_episodes_podcast_id`: O(log n) for episode queries (integer foreign key)
- Partial indexes on state: O(log n) for `find_unprocessed()` (massive win)

**Why No UUID Indexes?**
- UUIDs are random → low cardinality benefit from indexing
- Primary key lookups are always indexed (B-tree)
- Explicit indexes would increase write overhead with minimal read benefit

**2. Avoid N+1 Queries**:
```python
# BAD: N+1 queries
podcasts = repo.find_all()  # 1 query
for podcast in podcasts:
    episodes = repo.find_by_podcast(podcast.rss_url)  # N queries

# GOOD: JOIN in single query
podcasts = repo.find_all()  # 1 query, includes episodes via _row_to_podcast()
```

**3. Batch Updates**:
```python
# Use transactions for bulk operations
with repo.transaction():
    for update in updates:
        repo.update_episode(...)
```

**4. PRAGMA Optimizations**:
```sql
PRAGMA journal_mode = WAL;  -- Write-Ahead Logging (faster concurrent access)
PRAGMA synchronous = NORMAL;  -- Balance speed/safety
PRAGMA cache_size = -64000;  -- 64MB cache
PRAGMA temp_store = MEMORY;  -- Store temp tables in RAM
```

---

### Scalability Limits

**SQLite is Appropriate For:**
- 1-10 concurrent users
- <10GB database size
- Mostly read operations (95%+)
- Single-server deployment

**When to Migrate to PostgreSQL:**
- >10 concurrent users
- >10GB database size
- Heavy write workload (>5% writes)
- Multi-server deployment (clustering)

**Current Project**: SQLite is perfect fit (single-user, read-heavy)

---

## Security Considerations

### SQL Injection Prevention

**✅ Safe: Parameterized Queries**
```python
# SAFE: Parameters bound separately
cursor.execute("SELECT * FROM podcasts WHERE rss_url = ?", (url,))
```

**❌ Unsafe: String Interpolation**
```python
# DANGEROUS: Never do this
cursor.execute(f"SELECT * FROM podcasts WHERE rss_url = '{url}'")
```

**Rule**: Always use `?` placeholders, never f-strings or `%` formatting

---

### File Permissions

```bash
# Database file should be writable only by application user
chmod 600 data/podcasts.db

# Directory should prevent other users from reading
chmod 700 data/
```

---

### Backup Strategy

```bash
# SQLite backup (atomic)
sqlite3 data/podcasts.db ".backup data/podcasts.backup.db"

# Or use rsync
rsync -av data/podcasts.db data/podcasts.backup.db
```

**Cron Job Example**:
```bash
# Daily backups at 2am
0 2 * * * sqlite3 /path/to/data/podcasts.db ".backup /path/to/backups/podcasts-$(date +\%Y\%m\%d).db"
```

---

## Rollback Plan

### If Migration Fails

**Easy Rollback** (thanks to repository abstraction):

```python
# In cli.py, switch back to JSON
# Change:
repository = SqlitePodcastRepository(str(storage_path / "podcasts.db"))

# To:
repository = JsonPodcastRepository(str(storage_path))
```

**Zero downtime**: Just restart application with old code

---

### Keep JSON as Backup (Temporary)

```python
# Dual-write pattern (temporary during migration testing)
class DualWriteRepository(PodcastRepository):
    """Write to both JSON and SQLite, read from SQLite."""

    def __init__(self, json_repo, sqlite_repo):
        self.json_repo = json_repo
        self.sqlite_repo = sqlite_repo

    def save(self, podcast):
        # Write to both
        self.json_repo.save(podcast)
        self.sqlite_repo.save(podcast)
        return podcast

    def find_all(self):
        # Read from SQLite (faster)
        return self.sqlite_repo.find_all()
```

**Duration**: Keep for 1-2 weeks, then remove JSON writes

---

## Appendix A: Performance Benchmarks

### Expected Performance Improvements

| Operation | JSON (O notation) | SQLite (O notation) | Speedup |
|-----------|------------------|---------------------|---------|
| `find_all()` | O(n) | O(n) | Same |
| `find_by_url()` | O(n) | O(log n) | 10-100x |
| `find_unprocessed()` | O(n*m) | O(k) | 100-1000x |
| `update_episode()` | O(n*m) | O(log n) | 100x |
| Concurrent reads | 1 at a time | Unlimited | ∞ |

**Where**:
- n = number of podcasts
- m = average episodes per podcast
- k = number of episodes in target state

---

## Appendix B: SQL Query Reference

### Common Queries

```sql
-- Find all podcasts with episode counts
SELECT p.title, COUNT(e.id) AS episode_count
FROM podcasts p
LEFT JOIN episodes e ON e.podcast_id = p.id
GROUP BY p.id
ORDER BY p.title;

-- Find episodes ready for download
SELECT p.title, e.title, e.audio_url
FROM episodes e
JOIN podcasts p ON e.podcast_id = p.id
WHERE e.audio_path IS NULL
ORDER BY e.pub_date DESC;

-- Find episodes stuck in processing (not updated in 24h)
SELECT p.title, e.title, e.updated_at
FROM episodes e
JOIN podcasts p ON e.podcast_id = p.id
WHERE e.clean_transcript_path IS NULL
  AND e.updated_at < datetime('now', '-24 hours')
ORDER BY e.updated_at ASC;

-- Storage usage by podcast
SELECT p.title,
       COUNT(e.id) AS episodes,
       SUM(CASE WHEN e.audio_path IS NOT NULL THEN 1 ELSE 0 END) AS downloaded,
       SUM(CASE WHEN e.clean_transcript_path IS NOT NULL THEN 1 ELSE 0 END) AS processed
FROM podcasts p
LEFT JOIN episodes e ON e.podcast_id = p.id
GROUP BY p.id
ORDER BY episodes DESC;
```

---

## Appendix C: Multi-User Query Examples

```sql
-- Find all podcasts for a user
SELECT p.*
FROM podcasts p
JOIN subscriptions s ON s.podcast_id = p.id
WHERE s.user_id = ?
ORDER BY s.subscribed_at DESC;

-- Find users subscribed to a podcast
SELECT u.username, u.email, s.subscribed_at
FROM users u
JOIN subscriptions s ON s.user_id = u.id
WHERE s.podcast_id = ?
ORDER BY s.subscribed_at ASC;

-- Find unlistened episodes for user
SELECT e.*
FROM episodes e
JOIN subscriptions s ON s.podcast_id = e.podcast_id
LEFT JOIN episode_user_state eus ON eus.episode_id = e.id AND eus.user_id = s.user_id
WHERE s.user_id = ?
  AND (eus.listened IS NULL OR eus.listened = 0)
ORDER BY e.pub_date DESC;
```

---

## Conclusion

This migration plan provides:

1. **Two schema variants**: Single-user (simple) + Multi-user (subscriptions)
2. **No ORM overhead**: Raw SQL + Pydantic models
3. **No stored procedures**: Inline SQL in repository methods
4. **Cache-friendly design**: All side effects in service layer (no triggers/cascades)
5. **Selective indexing**: Only high-cardinality string columns (not UUIDs)
6. **Phased implementation**: 5 phases, ~7-8 hours total
7. **Manual migration**: Script provided, no automatic migration
8. **Easy rollback**: Repository abstraction enables instant rollback

**Next Steps**:
1. Review and approve this plan
2. Implement Phase 1 (SQLite repository)
3. Test in parallel with JSON (Phase 2)
4. Decide on multi-user support (Phase 3)
5. Migrate your data (Phase 4)

**Questions?** Open for discussion and refinement.
