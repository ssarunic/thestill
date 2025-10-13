# Repository Layer Implementation Plan

> Created: 2025-10-13
> Status: Proposed
> Replaces: Task R-006 (CLI helper extraction)

## Executive Summary

This plan introduces a **Repository Pattern** to abstract data persistence, enabling future migration from JSON files to SQLite/PostgreSQL with minimal code changes. This architectural improvement provides a clean separation between business logic and data access.

## Current State Problems

### 1. Direct Coupling to JSON Storage
```python
# FeedManager directly handles JSON I/O
def _load_podcasts(self) -> List[Podcast]:
    with open(self.feeds_file, "r") as f:
        data = json.load(f)
        return [Podcast(**podcast_data) for podcast_data in data]
```

**Issues:**
- FeedManager mixes business logic with persistence
- Cannot switch to SQLite without rewriting FeedManager
- Hard to test (requires actual file system)
- No transaction support
- Race conditions possible with concurrent access

### 2. Tight Coupling in Service Layer
```python
# PodcastService depends on FeedManager implementation
class PodcastService:
    def __init__(self, storage_path: str):
        self.feed_manager = PodcastFeedManager(str(storage_path))
```

**Issues:**
- Services depend on concrete implementation
- Cannot mock persistence for testing
- Business logic mixed with data access

### 3. No Query Optimization
- Full file read/write on every operation
- No indexes or caching
- Cannot optimize for common queries

## Proposed Architecture

```
┌─────────────────────────────────────────────────────────────┐
│                         CLI Layer                           │
│  (Argument parsing, user interaction, output formatting)    │
└────────────────────────────┬────────────────────────────────┘
                             │
┌────────────────────────────▼────────────────────────────────┐
│                      Service Layer                          │
│  ┌──────────────────────────────────────────────────────┐   │
│  │ PodcastService                                       │   │
│  │  - add_podcast(url) -> Podcast                       │   │
│  │  - remove_podcast(id) -> bool                        │   │
│  │  - list_podcasts() -> List[Podcast]                  │   │
│  │  - get_podcast(id) -> Optional[Podcast]              │   │
│  └──────────────────────────────────────────────────────┘   │
│  ┌──────────────────────────────────────────────────────┐   │
│  │ EpisodeService                                       │   │
│  │  - get_episodes(podcast_id) -> List[Episode]         │   │
│  │  - mark_downloaded(episode_id, path)                 │   │
│  │  - mark_transcribed(episode_id, path)                │   │
│  └──────────────────────────────────────────────────────┘   │
│                                                              │
│  Business Logic: Orchestration, validation, workflows       │
└────────────────────────────┬────────────────────────────────┘
                             │
┌────────────────────────────▼────────────────────────────────┐
│                   Repository Layer                          │
│  ┌──────────────────────────────────────────────────────┐   │
│  │ PodcastRepository (Abstract Interface)               │   │
│  │  - find_all() -> List[Podcast]                       │   │
│  │  - find_by_id(id) -> Optional[Podcast]               │   │
│  │  - find_by_url(url) -> Optional[Podcast]             │   │
│  │  - save(podcast: Podcast) -> Podcast                 │   │
│  │  - delete(id) -> bool                                │   │
│  │  - exists(url) -> bool                               │   │
│  └──────────────────────────────────────────────────────┘   │
│                       ▲         ▲         ▲                 │
│                       │         │         │                 │
│      ┌────────────────┘         │         └──────────────┐  │
│      │                          │                         │  │
│  ┌───▼──────────┐   ┌───────────▼────────┐   ┌──────────▼─┐│
│  │ Json         │   │ Sqlite             │   │ Postgres   ││
│  │ Repository   │   │ Repository         │   │ Repository ││
│  │              │   │ (Future)           │   │ (Future)   ││
│  └──────────────┘   └────────────────────┘   └────────────┘│
│                                                              │
│  Data Access: CRUD operations, queries, transactions        │
└──────────────────────────────────────────────────────────────┘
```

## Implementation Tasks

### Phase 1: Repository Interface & JSON Implementation (Week 1, Day 6-7)

#### Task R-006b-1: Create Repository Abstractions
**Effort**: 1 hour
**Files**: `repositories/__init__.py`, `repositories/podcast_repository.py`

Create abstract base classes for repositories:

```python
# repositories/podcast_repository.py
from abc import ABC, abstractmethod
from typing import List, Optional
from ..models.podcast import Podcast, Episode

class PodcastRepository(ABC):
    """Abstract repository for podcast persistence operations"""

    @abstractmethod
    def find_all(self) -> List[Podcast]:
        """Retrieve all podcasts"""
        pass

    @abstractmethod
    def find_by_id(self, podcast_id: int) -> Optional[Podcast]:
        """Find podcast by 1-based index"""
        pass

    @abstractmethod
    def find_by_url(self, url: str) -> Optional[Podcast]:
        """Find podcast by RSS URL"""
        pass

    @abstractmethod
    def exists(self, url: str) -> bool:
        """Check if podcast with URL exists"""
        pass

    @abstractmethod
    def save(self, podcast: Podcast) -> Podcast:
        """Save or update a podcast"""
        pass

    @abstractmethod
    def delete(self, url: str) -> bool:
        """Delete podcast by URL"""
        pass

    @abstractmethod
    def update_episode(self, podcast_url: str, episode_guid: str, updates: dict) -> bool:
        """Update specific episode fields"""
        pass


class EpisodeRepository(ABC):
    """Abstract repository for episode-specific operations"""

    @abstractmethod
    def find_by_podcast(self, podcast_url: str) -> List[Episode]:
        """Get all episodes for a podcast"""
        pass

    @abstractmethod
    def find_by_guid(self, podcast_url: str, episode_guid: str) -> Optional[Episode]:
        """Find specific episode by GUID"""
        pass

    @abstractmethod
    def find_unprocessed(self, state: str) -> List[tuple[Podcast, Episode]]:
        """
        Find episodes in specific processing state.

        States: 'discovered', 'downloaded', 'downsampled', 'transcribed'
        Returns: List of (Podcast, Episode) tuples
        """
        pass
```

**Benefits:**
- Defines clear contract for all implementations
- Type hints ensure consistency
- Easy to mock for testing

---

#### Task R-006b-2: Implement JsonPodcastRepository
**Effort**: 1.5 hours
**Files**: `repositories/json_podcast_repository.py`

Extract all JSON I/O from FeedManager into repository:

```python
# repositories/json_podcast_repository.py
import json
import logging
from pathlib import Path
from typing import List, Optional

from .podcast_repository import PodcastRepository, EpisodeRepository
from ..models.podcast import Podcast, Episode
from ..utils.path_manager import PathManager

logger = logging.getLogger(__name__)


class JsonPodcastRepository(PodcastRepository, EpisodeRepository):
    """
    JSON file-based implementation of podcast repository.

    Storage format: Single JSON file (feeds.json) containing array of podcasts.
    Each podcast contains nested episodes array.
    """

    def __init__(self, storage_path: str):
        self.path_manager = PathManager(storage_path)
        self.feeds_file = self.path_manager.feeds_file()
        self._ensure_storage_exists()

    def _ensure_storage_exists(self):
        """Create storage directory and file if needed"""
        self.feeds_file.parent.mkdir(parents=True, exist_ok=True)
        if not self.feeds_file.exists():
            self._write_podcasts([])

    def _read_podcasts(self) -> List[Podcast]:
        """Read all podcasts from JSON file"""
        try:
            with open(self.feeds_file, "r", encoding="utf-8") as f:
                data = json.load(f)
                return [Podcast(**podcast_data) for podcast_data in data]
        except FileNotFoundError:
            return []
        except json.JSONDecodeError as e:
            logger.error(f"Invalid JSON in feeds file: {e}")
            return []
        except Exception as e:
            logger.error(f"Error reading podcasts: {e}")
            return []

    def _write_podcasts(self, podcasts: List[Podcast]):
        """Write all podcasts to JSON file"""
        try:
            with open(self.feeds_file, "w", encoding="utf-8") as f:
                json.dump(
                    [p.model_dump(mode="json") for p in podcasts],
                    f,
                    indent=2,
                    ensure_ascii=False
                )
        except Exception as e:
            logger.error(f"Error writing podcasts: {e}")
            raise

    # Implement PodcastRepository methods

    def find_all(self) -> List[Podcast]:
        """Retrieve all podcasts"""
        return self._read_podcasts()

    def find_by_id(self, podcast_id: int) -> Optional[Podcast]:
        """Find podcast by 1-based index"""
        podcasts = self._read_podcasts()
        if 1 <= podcast_id <= len(podcasts):
            return podcasts[podcast_id - 1]
        return None

    def find_by_url(self, url: str) -> Optional[Podcast]:
        """Find podcast by RSS URL"""
        podcasts = self._read_podcasts()
        for podcast in podcasts:
            if str(podcast.rss_url) == url:
                return podcast
        return None

    def exists(self, url: str) -> bool:
        """Check if podcast with URL exists"""
        return self.find_by_url(url) is not None

    def save(self, podcast: Podcast) -> Podcast:
        """
        Save or update a podcast.

        If podcast with same URL exists, updates it.
        Otherwise, appends new podcast.
        """
        podcasts = self._read_podcasts()

        # Check if podcast already exists
        existing_index = None
        for i, p in enumerate(podcasts):
            if str(p.rss_url) == str(podcast.rss_url):
                existing_index = i
                break

        if existing_index is not None:
            # Update existing
            podcasts[existing_index] = podcast
        else:
            # Add new
            podcasts.append(podcast)

        self._write_podcasts(podcasts)
        return podcast

    def delete(self, url: str) -> bool:
        """Delete podcast by URL"""
        podcasts = self._read_podcasts()
        initial_count = len(podcasts)

        podcasts = [p for p in podcasts if str(p.rss_url) != url]

        if len(podcasts) < initial_count:
            self._write_podcasts(podcasts)
            return True
        return False

    def update_episode(self, podcast_url: str, episode_guid: str, updates: dict) -> bool:
        """
        Update specific episode fields.

        Args:
            podcast_url: URL of the podcast containing the episode
            episode_guid: GUID of the episode to update
            updates: Dictionary of field names and new values

        Returns:
            True if episode was found and updated
        """
        podcasts = self._read_podcasts()

        for podcast in podcasts:
            if str(podcast.rss_url) != podcast_url:
                continue

            for episode in podcast.episodes:
                if episode.guid != episode_guid:
                    continue

                # Update episode fields
                for field, value in updates.items():
                    if hasattr(episode, field):
                        setattr(episode, field, value)

                self._write_podcasts(podcasts)
                return True

        return False

    # Implement EpisodeRepository methods

    def find_by_podcast(self, podcast_url: str) -> List[Episode]:
        """Get all episodes for a podcast"""
        podcast = self.find_by_url(podcast_url)
        return podcast.episodes if podcast else []

    def find_by_guid(self, podcast_url: str, episode_guid: str) -> Optional[Episode]:
        """Find specific episode by GUID"""
        episodes = self.find_by_podcast(podcast_url)
        for episode in episodes:
            if episode.guid == episode_guid:
                return episode
        return None

    def find_unprocessed(self, state: str) -> List[tuple[Podcast, Episode]]:
        """
        Find episodes in specific processing state.

        States:
        - 'discovered': Has audio_url but no audio_path
        - 'downloaded': Has audio_path but no downsampled_audio_path
        - 'downsampled': Has downsampled_audio_path but no raw_transcript_path
        - 'transcribed': Has raw_transcript_path but no clean_transcript_path
        """
        podcasts = self._read_podcasts()
        results = []

        for podcast in podcasts:
            for episode in podcast.episodes:
                matches = False

                if state == 'discovered':
                    matches = bool(episode.audio_url and not episode.audio_path)
                elif state == 'downloaded':
                    matches = bool(episode.audio_path and not episode.downsampled_audio_path)
                elif state == 'downsampled':
                    matches = bool(episode.downsampled_audio_path and not episode.raw_transcript_path)
                elif state == 'transcribed':
                    matches = bool(episode.raw_transcript_path and not episode.clean_transcript_path)

                if matches:
                    results.append((podcast, episode))

        return results
```

**Benefits:**
- All JSON I/O in one place
- FeedManager becomes pure business logic
- Easy to add caching layer later
- Transaction support possible (write to temp file, then rename)

---

#### Task R-006b-3: Add Unit Tests for JsonPodcastRepository
**Effort**: 1 hour
**Files**: `tests/test_json_podcast_repository.py`

```python
# tests/test_json_podcast_repository.py
import json
import tempfile
from pathlib import Path
import pytest

from thestill.repositories.json_podcast_repository import JsonPodcastRepository
from thestill.models.podcast import Podcast, Episode


@pytest.fixture
def temp_storage():
    """Create temporary storage directory"""
    with tempfile.TemporaryDirectory() as tmpdir:
        yield tmpdir


@pytest.fixture
def repository(temp_storage):
    """Create repository with temp storage"""
    return JsonPodcastRepository(temp_storage)


@pytest.fixture
def sample_podcast():
    """Create sample podcast for testing"""
    return Podcast(
        title="Test Podcast",
        description="A test podcast",
        rss_url="https://example.com/feed.xml",
        episodes=[
            Episode(
                title="Episode 1",
                audio_url="https://example.com/ep1.mp3",
                guid="ep1",
                pub_date=None,
                description="First episode"
            )
        ]
    )


class TestJsonPodcastRepository:
    """Test JSON repository implementation"""

    def test_find_all_empty(self, repository):
        """Should return empty list when no podcasts exist"""
        podcasts = repository.find_all()
        assert podcasts == []

    def test_save_new_podcast(self, repository, sample_podcast):
        """Should save new podcast and return it"""
        result = repository.save(sample_podcast)

        assert result.title == sample_podcast.title
        assert result.rss_url == sample_podcast.rss_url

        # Verify it was persisted
        podcasts = repository.find_all()
        assert len(podcasts) == 1
        assert podcasts[0].title == sample_podcast.title

    def test_save_updates_existing(self, repository, sample_podcast):
        """Should update existing podcast with same URL"""
        # Save initial
        repository.save(sample_podcast)

        # Update and save again
        sample_podcast.title = "Updated Title"
        repository.save(sample_podcast)

        # Should have only one podcast
        podcasts = repository.find_all()
        assert len(podcasts) == 1
        assert podcasts[0].title == "Updated Title"

    def test_find_by_url(self, repository, sample_podcast):
        """Should find podcast by URL"""
        repository.save(sample_podcast)

        found = repository.find_by_url(str(sample_podcast.rss_url))
        assert found is not None
        assert found.title == sample_podcast.title

    def test_find_by_url_not_found(self, repository):
        """Should return None when URL not found"""
        found = repository.find_by_url("https://nonexistent.com/feed.xml")
        assert found is None

    def test_find_by_id(self, repository, sample_podcast):
        """Should find podcast by 1-based index"""
        repository.save(sample_podcast)

        found = repository.find_by_id(1)
        assert found is not None
        assert found.title == sample_podcast.title

    def test_find_by_id_out_of_range(self, repository):
        """Should return None for invalid ID"""
        assert repository.find_by_id(0) is None
        assert repository.find_by_id(999) is None

    def test_exists(self, repository, sample_podcast):
        """Should check if podcast exists"""
        assert not repository.exists(str(sample_podcast.rss_url))

        repository.save(sample_podcast)

        assert repository.exists(str(sample_podcast.rss_url))

    def test_delete(self, repository, sample_podcast):
        """Should delete podcast by URL"""
        repository.save(sample_podcast)
        assert len(repository.find_all()) == 1

        result = repository.delete(str(sample_podcast.rss_url))
        assert result is True
        assert len(repository.find_all()) == 0

    def test_delete_nonexistent(self, repository):
        """Should return False when deleting nonexistent podcast"""
        result = repository.delete("https://nonexistent.com/feed.xml")
        assert result is False

    def test_update_episode(self, repository, sample_podcast):
        """Should update episode fields"""
        repository.save(sample_podcast)

        result = repository.update_episode(
            str(sample_podcast.rss_url),
            "ep1",
            {"audio_path": "/path/to/audio.mp3"}
        )

        assert result is True

        # Verify update persisted
        podcast = repository.find_by_url(str(sample_podcast.rss_url))
        assert podcast.episodes[0].audio_path == "/path/to/audio.mp3"

    def test_find_unprocessed_discovered(self, repository, sample_podcast):
        """Should find episodes in 'discovered' state"""
        repository.save(sample_podcast)

        results = repository.find_unprocessed('discovered')
        assert len(results) == 1

        podcast, episode = results[0]
        assert episode.guid == "ep1"

    def test_find_unprocessed_downloaded(self, repository, sample_podcast):
        """Should find episodes in 'downloaded' state"""
        # Mark as downloaded
        sample_podcast.episodes[0].audio_path = "/path/to/audio.mp3"
        repository.save(sample_podcast)

        results = repository.find_unprocessed('downloaded')
        assert len(results) == 1

        podcast, episode = results[0]
        assert episode.audio_path is not None
        assert episode.downsampled_audio_path is None

    def test_persistence_across_instances(self, temp_storage, sample_podcast):
        """Should persist data across repository instances"""
        # Save with first instance
        repo1 = JsonPodcastRepository(temp_storage)
        repo1.save(sample_podcast)

        # Load with second instance
        repo2 = JsonPodcastRepository(temp_storage)
        podcasts = repo2.find_all()

        assert len(podcasts) == 1
        assert podcasts[0].title == sample_podcast.title
```

**Coverage Target**: 95%+ for repository

---

### Phase 2: Refactor FeedManager to Use Repository (Week 2, Day 1-2)

#### Task R-006b-4: Inject Repository into FeedManager
**Effort**: 2 hours
**Files**: `core/feed_manager.py`

Refactor FeedManager to use repository instead of direct JSON I/O:

```python
# core/feed_manager.py
class PodcastFeedManager:
    """
    Manages podcast feeds and episodes.

    Responsibilities:
    - Fetch RSS/YouTube feeds
    - Parse feed data
    - Coordinate episode discovery
    - Manage episode state transitions

    Does NOT handle:
    - Data persistence (delegates to repository)
    - Business logic (delegates to service layer)
    """

    def __init__(
        self,
        storage_path: str = "./data",
        podcast_repository: Optional[PodcastRepository] = None
    ):
        """
        Initialize feed manager.

        Args:
            storage_path: Path to storage directory (for PathManager)
            podcast_repository: Repository for persistence (defaults to JSON)
        """
        self.storage_path = Path(storage_path)
        self.path_manager = PathManager(str(storage_path))

        # Use provided repository or default to JSON
        if podcast_repository is None:
            from ..repositories.json_podcast_repository import JsonPodcastRepository
            podcast_repository = JsonPodcastRepository(str(storage_path))

        self.repository = podcast_repository
        self.youtube_downloader = YouTubeDownloader(str(self.path_manager.original_audio_dir()))

    # Remove _load_podcasts and _save_podcasts methods

    def add_podcast(self, url: str) -> bool:
        """Add a new podcast feed"""
        try:
            # ... parsing logic ...

            podcast = Podcast(
                title=feed.get("title", "Unknown Podcast"),
                description=feed.get("description", ""),
                rss_url=rss_url
            )

            if not self.repository.exists(rss_url):
                self.repository.save(podcast)  # Use repository
                return True
            return False

        except Exception as e:
            logger.error(f"Error adding podcast {url}: {e}")
            return False

    def remove_podcast(self, rss_url: str) -> bool:
        """Remove a podcast feed"""
        return self.repository.delete(rss_url)  # Delegate to repository

    def list_podcasts(self) -> List[Podcast]:
        """List all tracked podcasts"""
        return self.repository.find_all()  # Delegate to repository

    def mark_episode_downloaded(self, podcast_url: str, episode_guid: str, audio_path: str):
        """Mark episode as downloaded"""
        self.repository.update_episode(
            podcast_url,
            episode_guid,
            {"audio_path": audio_path}
        )

    # Similar for other mark_episode_* methods
```

**Benefits:**
- FeedManager becomes pure business logic
- Can inject mock repository for testing
- Easier to reason about responsibilities

---

#### Task R-006b-5: Update PodcastService to Use Repository
**Effort**: 1 hour
**Files**: `services/podcast_service.py`

Update service to optionally accept repository:

```python
# services/podcast_service.py
class PodcastService:
    """Service for podcast and episode management"""

    def __init__(
        self,
        storage_path: str,
        podcast_repository: Optional[PodcastRepository] = None
    ):
        """
        Initialize podcast service.

        Args:
            storage_path: Path to data storage directory
            podcast_repository: Optional repository (for testing/DI)
        """
        self.storage_path = Path(storage_path)

        # Initialize repository
        if podcast_repository is None:
            from ..repositories.json_podcast_repository import JsonPodcastRepository
            podcast_repository = JsonPodcastRepository(str(storage_path))

        self.repository = podcast_repository

        # FeedManager still needed for RSS parsing
        self.feed_manager = PodcastFeedManager(
            str(storage_path),
            podcast_repository=podcast_repository
        )

        logger.info(f"PodcastService initialized with storage: {storage_path}")

    def list_podcasts(self) -> List[PodcastWithIndex]:
        """List all podcasts with human-friendly indexes"""
        podcasts = self.repository.find_all()  # Use repository directly

        return [
            PodcastWithIndex(
                index=i + 1,
                title=podcast.title,
                # ... etc
            )
            for i, podcast in enumerate(podcasts)
        ]

    def get_podcast(self, podcast_id: Union[int, str]) -> Optional[Podcast]:
        """Get podcast by ID or URL"""
        if isinstance(podcast_id, int):
            return self.repository.find_by_id(podcast_id)
        else:
            return self.repository.find_by_url(podcast_id)
```

**Benefits:**
- Services can be tested without FeedManager
- Clear dependency injection
- Repository shared between service and feed manager

---

### Phase 3: Future Database Migration (Future)

#### Task R-006b-6: Implement SqlitePodcastRepository (Future)
**Effort**: 3-4 hours
**Files**: `repositories/sqlite_podcast_repository.py`

When ready to migrate to SQLite:

```python
# repositories/sqlite_podcast_repository.py
import sqlite3
from typing import List, Optional

from .podcast_repository import PodcastRepository
from ..models.podcast import Podcast, Episode


class SqlitePodcastRepository(PodcastRepository):
    """SQLite-based repository implementation"""

    def __init__(self, db_path: str):
        self.db_path = db_path
        self._create_tables()

    def _create_tables(self):
        """Create database schema"""
        with sqlite3.connect(self.db_path) as conn:
            conn.execute("""
                CREATE TABLE IF NOT EXISTS podcasts (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    title TEXT NOT NULL,
                    description TEXT,
                    rss_url TEXT UNIQUE NOT NULL,
                    created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
                )
            """)

            conn.execute("""
                CREATE TABLE IF NOT EXISTS episodes (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    podcast_id INTEGER NOT NULL,
                    title TEXT NOT NULL,
                    description TEXT,
                    audio_url TEXT NOT NULL,
                    guid TEXT NOT NULL,
                    pub_date TIMESTAMP,
                    audio_path TEXT,
                    downsampled_audio_path TEXT,
                    raw_transcript_path TEXT,
                    clean_transcript_path TEXT,
                    FOREIGN KEY (podcast_id) REFERENCES podcasts(id) ON DELETE CASCADE,
                    UNIQUE(podcast_id, guid)
                )
            """)

            # Create indexes for common queries
            conn.execute("CREATE INDEX IF NOT EXISTS idx_episodes_podcast_id ON episodes(podcast_id)")
            conn.execute("CREATE INDEX IF NOT EXISTS idx_episodes_guid ON episodes(guid)")
            conn.execute("CREATE INDEX IF NOT EXISTS idx_episodes_audio_path ON episodes(audio_path)")

    def find_all(self) -> List[Podcast]:
        """Retrieve all podcasts with episodes"""
        with sqlite3.connect(self.db_path) as conn:
            conn.row_factory = sqlite3.Row
            cursor = conn.cursor()

            # Fetch podcasts
            cursor.execute("SELECT * FROM podcasts ORDER BY id")
            podcasts_data = cursor.fetchall()

            podcasts = []
            for podcast_row in podcasts_data:
                # Fetch episodes for this podcast
                cursor.execute(
                    "SELECT * FROM episodes WHERE podcast_id = ? ORDER BY pub_date DESC",
                    (podcast_row['id'],)
                )
                episodes_data = cursor.fetchall()

                episodes = [
                    Episode(
                        title=ep['title'],
                        description=ep['description'],
                        audio_url=ep['audio_url'],
                        guid=ep['guid'],
                        pub_date=ep['pub_date'],
                        audio_path=ep['audio_path'],
                        # ... other fields
                    )
                    for ep in episodes_data
                ]

                podcast = Podcast(
                    title=podcast_row['title'],
                    description=podcast_row['description'],
                    rss_url=podcast_row['rss_url'],
                    episodes=episodes
                )
                podcasts.append(podcast)

            return podcasts

    # Implement other methods...
```

**Migration Path:**
1. Keep JsonPodcastRepository as default
2. Add `REPOSITORY_TYPE=json|sqlite` to .env
3. Update config to instantiate correct repository
4. Run migration script to convert JSON → SQLite
5. Switch config to `REPOSITORY_TYPE=sqlite`

**Benefits:**
- Proper transactions (ACID compliance)
- Concurrent access with locking
- Query optimization with indexes
- Scalable to 100,000+ episodes
- No application code changes needed

---

## Testing Strategy

### Unit Tests
- **Repository tests**: Mock file system (tempfile)
- **Service tests**: Mock repository
- **FeedManager tests**: Mock repository and HTTP calls

### Integration Tests
```python
def test_full_workflow_with_json_repository():
    """Test full workflow: add → refresh → download → transcribe"""
    with tempfile.TemporaryDirectory() as tmpdir:
        repo = JsonPodcastRepository(tmpdir)
        service = PodcastService(tmpdir, podcast_repository=repo)

        # Add podcast
        podcast = service.add_podcast("https://example.com/feed.xml")
        assert podcast is not None

        # Verify persisted
        podcasts = repo.find_all()
        assert len(podcasts) == 1
```

---

## Migration Benefits

### Immediate Benefits (JSON Repository)
- ✅ Clear separation of concerns
- ✅ Easier testing with mocks
- ✅ Consistent error handling
- ✅ Foundation for future migrations

### Future Benefits (SQLite Repository)
- ✅ 10-100x faster queries (indexed lookups)
- ✅ Concurrent access without corruption
- ✅ Transactions for atomic operations
- ✅ Scalable to millions of episodes
- ✅ Standard SQL query tools

---

## Rollout Plan

### Week 1, Day 6-7 (This Weekend)
- R-006b-1: Create abstractions (1 hour)
- R-006b-2: Implement JSON repository (1.5 hours)
- R-006b-3: Add unit tests (1 hour)
- **Total**: 3.5 hours

### Week 2, Day 1-2
- R-006b-4: Refactor FeedManager (2 hours)
- R-006b-5: Update PodcastService (1 hour)
- Run full test suite
- **Total**: 3 hours

### Week 2, Day 3+
- Continue with other refactoring tasks (R-004, R-007, R-008, etc.)

### Future (When Needed)
- R-006b-6: Implement SQLite repository
- Migration script
- Performance benchmarks

---

## Success Metrics

### Phase 1 (JSON Repository)
- ✅ All JSON I/O in repository layer
- ✅ 95%+ test coverage for repository
- ✅ No direct file I/O in FeedManager
- ✅ All existing tests pass

### Phase 2 (Service Integration)
- ✅ Services use repository
- ✅ FeedManager uses repository
- ✅ Can inject mock repository for tests
- ✅ CLI unchanged (backward compatible)

### Phase 3 (SQLite Migration)
- ✅ 10x faster podcast list queries
- ✅ Concurrent access without corruption
- ✅ Zero application code changes

---

## Risk Mitigation

### Risk: Breaking Existing Functionality
**Mitigation:**
- Keep JSON repository as default (no behavior change)
- Run full test suite after each task
- Keep original `_load_podcasts` / `_save_podcasts` until repository is proven

### Risk: Performance Regression
**Mitigation:**
- JSON repository should have same performance (same I/O operations)
- Add benchmarks before/after
- Profile with `cProfile` if concerns

### Risk: Data Corruption During Refactor
**Mitigation:**
- Repository writes to temp file, then renames (atomic)
- Add backup/restore functionality
- Test with real `feeds.json` file

---

## Comparison: Task R-006 vs R-006b

| Aspect | R-006 (CLI Helper) | R-006b (Repository Layer) |
|--------|-------------------|--------------------------|
| **Scope** | Extract CLI duplication | Architectural refactor |
| **Effort** | 45 minutes | 6.5 hours total |
| **Benefits** | Less duplicate code | Migration-ready, testable, scalable |
| **Risk** | Low | Medium |
| **Future Value** | Minimal | High (enables SQLite/Postgres) |
| **Testing** | Manual | Comprehensive unit tests |
| **LOC Changed** | ~60 lines | ~800 lines |

---

## Recommendation

**Implement R-006b (Repository Layer)** instead of R-006 because:

1. **Future-proof**: Enables SQLite/Postgres migration with minimal code changes
2. **Better testing**: Can mock repositories easily
3. **Clear architecture**: Separates data access from business logic
4. **Scalability**: JSON works now, but SQLite ready when needed
5. **Industry standard**: Repository pattern is proven best practice

**Timeline**: Complete Phase 1-2 over next 2 days (6.5 hours), then continue with other refactoring tasks.

---

## Next Steps

1. ✅ Review and approve this plan
2. ⬜ Create feature branch: `refactor/repository-layer`
3. ⬜ Implement R-006b-1 (abstractions)
4. ⬜ Implement R-006b-2 (JSON repository)
5. ⬜ Implement R-006b-3 (tests)
6. ⬜ Commit and push
7. ⬜ Implement R-006b-4 (FeedManager refactor)
8. ⬜ Implement R-006b-5 (Service refactor)
9. ⬜ Run full test suite
10. ⬜ Create PR for review

**Ready to start?** Let me know and I'll begin with Task R-006b-1.
