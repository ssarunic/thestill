# Multi-User Shared Podcasts Specification

> **Status:** Implemented (Phase 1 - Follow/Unfollow)
> **Created:** 2026-01-15
> **Updated:** 2026-01-21
> **Author:** Product & Engineering

---

## Executive Summary

Extend thestill to support multiple users who can follow (subscribe to) podcasts, where **processing happens once and results are shared** across all followers. This achieves significant resource savings by eliminating duplicate transcription and summarization work.

**Key Principle:** "Process Once, Deliver to Many" — a podcast's episodes are transcribed and summarized exactly once, regardless of how many users follow it.

---

## Table of Contents

1. [Product Requirements](#product-requirements)
2. [Architecture Overview](#architecture-overview)
3. [Database Schema Changes](#database-schema-changes)
4. [Data Model](#data-model)
5. [Service Layer Changes](#service-layer-changes)
6. [API Changes](#api-changes)
7. [Migration Strategy](#migration-strategy)
8. [Implementation Guidelines](#implementation-guidelines)
9. [Security Considerations](#security-considerations)
10. [Open Questions](#open-questions)

---

## Product Requirements

### User Stories

| As a... | I want to... | So that... |
|---------|--------------|------------|
| New user | Browse existing podcasts in the system | I can follow podcasts without adding them myself |
| User | Follow a podcast someone else added | I get transcripts without re-processing |
| User | See all episodes of a podcast I follow | I can catch up on older content |
| User | Trigger processing for an unprocessed episode | I don't have to wait for background workers |
| User | Track my own read/saved state | My progress is independent of other users |
| Admin | See which podcasts have the most followers | I can prioritize processing |
| Self-hoster | Use the CLI without authentication | Existing workflows continue unchanged |

### Core Behaviors

1. **Shared Podcasts**: Podcasts exist independently of users. Users "follow" them.
2. **Shared Processing**: Transcripts/summaries are stored once, referenced by all followers.
3. **Private State**: Read/saved/interest states are per-user.
4. **Open Processing**: Any follower can trigger processing for any episode.
5. **All Episodes Access**: Following a podcast grants access to all its episodes (past and future).
6. **Backward Compatible CLI**: CLI works without authentication using a synthetic "local" user.

---

## Architecture Overview

### Before (Current Single-User)

```
┌─────────────┐
│    User     │  (implicit, single)
└──────┬──────┘
       │ owns
       ▼
┌─────────────┐
│  Podcasts   │
│  (user_id)  │
└──────┬──────┘
       │ contains
       ▼
┌─────────────┐
│  Episodes   │
└─────────────┘
```

### After (Multi-User with Sharing)

```
┌─────────────┐              ┌─────────────┐
│   Users     │              │  Podcasts   │  (no user ownership)
└──────┬──────┘              └──────┬──────┘
       │                            │
       │ follows                    │ contains
       ▼                            ▼
┌─────────────────┐          ┌─────────────┐
│   Followers     │          │  Episodes   │  (shared content)
│ (user_id,       │          └──────┬──────┘
│  podcast_id)    │                 │
└────────┬────────┘                 │
         │                          │
         └──────────┬───────────────┘
                    │ interacts with
                    ▼
         ┌─────────────────────┐
         │   User Episodes     │  (private state)
         │ (user_id,           │
         │  episode_id)        │
         └─────────────────────┘
```

### Key Insight: Decoupling Ownership from Content

- **Old model**: Podcast belongs to user, user controls everything
- **New model**: Podcast is a shared resource, users follow it, state is private

---

## Database Schema Changes

### New Table: `podcast_followers`

Links users to podcasts they follow. This replaces the `user_id` column on podcasts.

```sql
-- User-Podcast following relationship (IMPLEMENTED)
CREATE TABLE podcast_followers (
    id TEXT PRIMARY KEY NOT NULL,           -- UUID v4
    user_id TEXT NOT NULL,
    podcast_id TEXT NOT NULL,
    created_at TIMESTAMP NOT NULL DEFAULT CURRENT_TIMESTAMP,
    FOREIGN KEY (user_id) REFERENCES users(id) ON DELETE CASCADE,
    FOREIGN KEY (podcast_id) REFERENCES podcasts(id) ON DELETE CASCADE,
    UNIQUE(user_id, podcast_id),
    CHECK (length(id) = 36)
);

-- Indexes for common queries
CREATE INDEX idx_podcast_followers_user_id ON podcast_followers(user_id);
CREATE INDEX idx_podcast_followers_podcast_id ON podcast_followers(podcast_id);
```

### New Table: `user_episodes`

Per-user episode state (read, saved, interest level).

```sql
-- User-Episode interaction state
CREATE TABLE user_episodes (
    id TEXT PRIMARY KEY,                    -- UUID v4
    user_id TEXT NOT NULL REFERENCES users(id) ON DELETE CASCADE,
    episode_id TEXT NOT NULL REFERENCES episodes(id) ON DELETE CASCADE,
    -- Reading state
    is_read INTEGER NOT NULL DEFAULT 0,     -- SQLite boolean
    read_at TIMESTAMP NULL,
    -- Triage state
    interest_level TEXT NULL,               -- 'interested', 'not_interested', 'saved'
    interest_set_at TIMESTAMP NULL,
    -- Timestamps
    created_at TIMESTAMP NOT NULL DEFAULT CURRENT_TIMESTAMP,
    updated_at TIMESTAMP NOT NULL DEFAULT CURRENT_TIMESTAMP,
    UNIQUE(user_id, episode_id)
);

-- Indexes for feed queries
CREATE INDEX idx_user_episodes_user ON user_episodes(user_id);
CREATE INDEX idx_user_episodes_episode ON user_episodes(episode_id);
CREATE INDEX idx_user_episodes_unread ON user_episodes(user_id, is_read)
    WHERE is_read = 0;
CREATE INDEX idx_user_episodes_saved ON user_episodes(user_id, interest_level)
    WHERE interest_level = 'saved';
```

### Table: `users` (from auth spec)

Already defined in [authentication.md](authentication.md). Key fields:

```sql
CREATE TABLE users (
    id TEXT PRIMARY KEY,                    -- UUID v4
    email TEXT UNIQUE,
    name TEXT,
    provider TEXT,                          -- 'local', 'google', 'microsoft'
    provider_id TEXT,
    created_at TIMESTAMP,
    last_login_at TIMESTAMP
);
```

### Podcasts Table: NO CHANGES

The `podcasts` table remains unchanged. Podcasts do NOT have a `user_id` column. They are shared resources.

```sql
-- Existing schema (unchanged)
CREATE TABLE podcasts (
    id TEXT PRIMARY KEY,
    created_at TIMESTAMP,
    updated_at TIMESTAMP,
    rss_url TEXT UNIQUE NOT NULL,
    title TEXT,
    slug TEXT,
    description TEXT,
    image_url TEXT,
    language TEXT DEFAULT 'en',
    last_processed TIMESTAMP
    -- NO user_id column!
);
```

### Episodes Table: NO CHANGES

Episodes remain unchanged. Processing state (audio_path, transcript_path, etc.) is shared.

---

## Data Model

### New Pydantic Models

```python
# thestill/models/user.py

from datetime import datetime
from typing import Optional
from pydantic import BaseModel, Field
import uuid


class User(BaseModel):
    """User account (from OAuth or local auth)."""
    id: str = Field(default_factory=lambda: str(uuid.uuid4()))
    email: Optional[str] = None
    name: Optional[str] = None
    provider: str = "local"  # 'local', 'google', 'microsoft'
    provider_id: Optional[str] = None
    created_at: datetime = Field(default_factory=lambda: datetime.now())
    last_login_at: Optional[datetime] = None


class PodcastFollower(BaseModel):
    """User-Podcast following relationship. (IMPLEMENTED in thestill/models/user.py)"""
    id: str = Field(default_factory=lambda: str(uuid.uuid4()))
    user_id: str
    podcast_id: str
    created_at: datetime = Field(default_factory=lambda: datetime.now(timezone.utc))


class UserEpisode(BaseModel):
    """Per-user episode state (read, saved, interest)."""
    id: str = Field(default_factory=lambda: str(uuid.uuid4()))
    user_id: str
    episode_id: str
    is_read: bool = False
    read_at: Optional[datetime] = None
    interest_level: Optional[str] = None  # 'interested', 'not_interested', 'saved'
    interest_set_at: Optional[datetime] = None
    created_at: datetime = Field(default_factory=lambda: datetime.now())
    updated_at: datetime = Field(default_factory=lambda: datetime.now())
```

### Special "Local" User for CLI

```python
# Synthetic user for AUTH_MODE=none (CLI compatibility)
LOCAL_USER = User(
    id="local",
    email=None,
    name="Local User",
    provider="local",
    provider_id=None,
)
```

---

## Service Layer Changes

### New: `FollowerService` (IMPLEMENTED)

Manages user-podcast following relationships.

```python
# thestill/services/follower_service.py (IMPLEMENTED)

class FollowerService:
    """Manage user-podcast following relationships."""

    def follow(self, user_id: str, podcast_id: str) -> PodcastFollower:
        """User starts following a podcast by ID."""
        pass

    def follow_by_slug(self, user_id: str, podcast_slug: str) -> PodcastFollower:
        """User starts following a podcast by slug."""
        pass

    def unfollow(self, user_id: str, podcast_id: str) -> bool:
        """User stops following a podcast by ID."""
        pass

    def unfollow_by_slug(self, user_id: str, podcast_slug: str) -> bool:
        """User stops following a podcast by slug."""
        pass

    def get_followed_podcasts(self, user_id: str) -> List[Podcast]:
        """Get all podcasts a user follows."""
        pass

    def get_followed_podcast_ids(self, user_id: str) -> List[str]:
        """Get IDs of all podcasts a user follows."""
        pass

    def is_following(self, user_id: str, podcast_id: str) -> bool:
        """Check if user follows a podcast."""
        pass

    def is_following_by_slug(self, user_id: str, podcast_slug: str) -> bool:
        """Check if user follows a podcast by slug."""
        pass

    def get_follower_count(self, podcast_id: str) -> int:
        """Get count of followers for prioritization."""
        pass
```

**Custom Exceptions:**

- `AlreadyFollowingError`: Raised when user tries to follow a podcast they already follow
- `NotFollowingError`: Raised when user tries to unfollow a podcast they don't follow
- `PodcastNotFoundError`: Raised when podcast slug doesn't exist

### New: `UserEpisodeService` (NOT YET IMPLEMENTED)

Manages per-user episode state. This is planned for Phase 2.

```python
# thestill/services/user_episode_service.py (PLANNED)

class UserEpisodeService:
    """Manage per-user episode state (read, saved, interest)."""

    def mark_read(self, user_id: str, episode_id: str) -> UserEpisode:
        """Mark episode as read."""
        pass

    def mark_unread(self, user_id: str, episode_id: str) -> UserEpisode:
        """Mark episode as unread."""
        pass

    def set_interest(self, user_id: str, episode_id: str, level: str) -> UserEpisode:
        """Set interest level (interested, not_interested, saved)."""
        pass

    def get_user_episode(self, user_id: str, episode_id: str) -> Optional[UserEpisode]:
        """Get user's state for an episode."""
        pass

    def get_unread_episodes(self, user_id: str, podcast_id: Optional[str] = None) -> List[Episode]:
        """Get unread episodes for user's followed podcasts."""
        pass

    def get_saved_episodes(self, user_id: str) -> List[Episode]:
        """Get episodes user has saved."""
        pass
```

### Modified: `PodcastService`

Add user-context methods while preserving backward compatibility.

```python
# thestill/services/podcast_service.py (additions)

class PodcastService:
    # Existing methods unchanged...

    # NEW: User-context methods
    def get_user_podcasts(self, user_id: str) -> List[PodcastWithIndex]:
        """Get podcasts user follows (replaces get_podcasts for multi-user)."""
        pass

    def get_user_feed(
        self,
        user_id: str,
        limit: int = 20,
        offset: int = 0,
        unread_only: bool = False,
        podcast_id: Optional[str] = None,
    ) -> List[EpisodeWithUserState]:
        """Get personalized episode feed with user state."""
        pass

    def add_podcast_for_user(self, user_id: str, url: str) -> Optional[Podcast]:
        """Add podcast (if new) and follow it for user."""
        # If podcast doesn't exist, add it
        # Then create follower relationship
        pass
```

### New Repository: `PodcastFollowerRepository` (IMPLEMENTED)

```python
# thestill/repositories/podcast_follower_repository.py (IMPLEMENTED)

class PodcastFollowerRepository(ABC):
    """Abstract repository for follower relationships."""

    @abstractmethod
    def add(self, follower: PodcastFollower) -> PodcastFollower:
        """Add follower relationship."""
        pass

    @abstractmethod
    def remove(self, user_id: str, podcast_id: str) -> bool:
        """Remove follower relationship."""
        pass

    @abstractmethod
    def exists(self, user_id: str, podcast_id: str) -> bool:
        """Check if relationship exists."""
        pass

    @abstractmethod
    def get_by_user(self, user_id: str) -> List[PodcastFollower]:
        """Get all following relationships for user."""
        pass

    @abstractmethod
    def get_by_podcast(self, podcast_id: str) -> List[PodcastFollower]:
        """Get all followers for podcast."""
        pass

    @abstractmethod
    def count_by_podcast(self, podcast_id: str) -> int:
        """Count followers for podcast."""
        pass

    @abstractmethod
    def get_followed_podcast_ids(self, user_id: str) -> List[str]:
        """Get list of podcast IDs user follows."""
        pass

# thestill/repositories/sqlite_podcast_follower_repository.py (IMPLEMENTED)
# SQLite implementation of the above interface
```

---

## API Changes

### Implemented Endpoints (Phase 1)

| Method | Endpoint | Description | Status |
|--------|----------|-------------|--------|
| POST | `/api/podcasts/{slug}/follow` | Follow a podcast (201 Created) | IMPLEMENTED |
| DELETE | `/api/podcasts/{slug}/follow` | Unfollow a podcast (204 No Content) | IMPLEMENTED |
| GET | `/api/podcasts/{slug}/followers/count` | Get follower count | IMPLEMENTED |

### Planned Endpoints (Phase 2 - User Episode State)

| Method | Endpoint | Description | Status |
|--------|----------|-------------|--------|
| GET | `/api/podcasts/catalog` | Browse all podcasts in system (public) | NOT IMPLEMENTED |
| GET | `/api/podcasts/popular` | Get podcasts by follower count | NOT IMPLEMENTED |
| POST | `/api/episodes/{id}/read` | Mark episode as read | NOT IMPLEMENTED |
| DELETE | `/api/episodes/{id}/read` | Mark episode as unread | NOT IMPLEMENTED |
| POST | `/api/episodes/{id}/interest` | Set interest level `{level}` | NOT IMPLEMENTED |
| GET | `/api/feed` | User's personalized feed | NOT IMPLEMENTED |
| GET | `/api/feed/unread` | Unread episodes only | NOT IMPLEMENTED |
| GET | `/api/feed/saved` | Saved episodes | NOT IMPLEMENTED |

### Modified Endpoints (IMPLEMENTED)

| Method | Endpoint | Change | Status |
|--------|----------|--------|--------|
| GET | `/api/podcasts` | Returns user's followed podcasts (requires auth) | IMPLEMENTED |
| POST | `/api/commands/add` | Adds podcast AND auto-follows for current user | IMPLEMENTED |

**Note:** Uses podcast `slug` (not `id`) for all user-facing endpoints per UX decision.

### Query Parameters

**GET `/api/feed`:**

- `?limit=20&offset=0` - Pagination
- `?podcast_id=xxx` - Filter by podcast
- `?unread=true` - Unread only
- `?state=summarized` - Filter by processing state

---

## Migration Strategy

### Phase 1: Schema Migration

```sql
-- migration_001_add_followers.sql

-- Create users table (from auth spec)
CREATE TABLE IF NOT EXISTS users (
    id TEXT PRIMARY KEY,
    email TEXT UNIQUE,
    name TEXT,
    provider TEXT DEFAULT 'local',
    provider_id TEXT,
    created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
    last_login_at TIMESTAMP
);

-- Create synthetic local user for CLI compatibility
INSERT OR IGNORE INTO users (id, name, provider)
VALUES ('local', 'Local User', 'local');

-- Create followers table
CREATE TABLE followers (
    id TEXT PRIMARY KEY,
    user_id TEXT NOT NULL REFERENCES users(id) ON DELETE CASCADE,
    podcast_id TEXT NOT NULL REFERENCES podcasts(id) ON DELETE CASCADE,
    created_at TIMESTAMP NOT NULL DEFAULT CURRENT_TIMESTAMP,
    UNIQUE(user_id, podcast_id)
);

-- Migrate existing podcasts: local user follows all existing podcasts
INSERT INTO followers (id, user_id, podcast_id, created_at)
SELECT
    lower(hex(randomblob(4))) || '-' || lower(hex(randomblob(2))) || '-4' ||
    substr(lower(hex(randomblob(2))),2) || '-' ||
    substr('89ab', abs(random()) % 4 + 1, 1) ||
    substr(lower(hex(randomblob(2))),2) || '-' ||
    lower(hex(randomblob(6))),
    'local',
    id,
    created_at
FROM podcasts;

-- Create user_episodes table
CREATE TABLE user_episodes (
    id TEXT PRIMARY KEY,
    user_id TEXT NOT NULL REFERENCES users(id) ON DELETE CASCADE,
    episode_id TEXT NOT NULL REFERENCES episodes(id) ON DELETE CASCADE,
    is_read INTEGER NOT NULL DEFAULT 0,
    read_at TIMESTAMP,
    interest_level TEXT,
    interest_set_at TIMESTAMP,
    created_at TIMESTAMP NOT NULL DEFAULT CURRENT_TIMESTAMP,
    updated_at TIMESTAMP NOT NULL DEFAULT CURRENT_TIMESTAMP,
    UNIQUE(user_id, episode_id)
);

-- Create indexes
CREATE INDEX idx_followers_user ON followers(user_id);
CREATE INDEX idx_followers_podcast ON followers(podcast_id);
CREATE INDEX idx_user_episodes_user ON user_episodes(user_id);
CREATE INDEX idx_user_episodes_episode ON user_episodes(episode_id);
```

### Phase 2: CLI Command

```bash
thestill db migrate
# Output:
# - Created users table
# - Created followers table
# - Migrated 15 podcasts to local user (follower relationships created)
# - Created user_episodes table
# Migration complete!
```

### Phase 3: Code Changes

1. Add `FollowerRepository` and `UserEpisodeRepository`
2. Add `FollowerService` and `UserEpisodeService`
3. Update `PodcastService` with user-context methods
4. Update API routes to use `get_current_user()` dependency
5. Add new API endpoints

---

## Implementation Guidelines

### 1. Repository Pattern

Follow existing SQLite repository patterns:

```python
class FollowerRepository:
    def __init__(self, db_path: str):
        self.db_path = Path(db_path)

    @contextmanager
    def _get_connection(self) -> sqlite3.Connection:
        # Same pattern as SqlitePodcastRepository
        pass
```

### 2. Dependency Injection

Add new services to `AppState`:

```python
@dataclass
class AppState:
    config: Config
    path_manager: PathManager
    repository: SqlitePodcastRepository
    podcast_service: PodcastService
    stats_service: StatsService
    # NEW
    follower_service: FollowerService
    user_episode_service: UserEpisodeService
```

### 3. User Context in Routes

All protected routes receive current user:

```python
@router.get("/podcasts")
async def get_podcasts(
    state: AppState = Depends(get_app_state),
    user: User = Depends(get_current_user),  # From auth middleware
) -> dict:
    podcasts = state.podcast_service.get_user_podcasts(user.id)
    return paginated_response(...)
```

### 4. CLI Compatibility

For CLI operations, use the synthetic local user:

```python
# In CLI context
def get_current_user_cli() -> User:
    """Return local user for CLI operations."""
    return LOCAL_USER  # id="local"
```

### 5. Error Handling

- `404` if podcast not found
- `409` if already following (on follow)
- `404` if not following (on unfollow)
- Create `user_episode` lazily on first interaction

---

## Security Considerations

| Concern | Mitigation |
|---------|------------|
| User can see others' private state | All `user_episode` queries filter by `user_id` |
| User can delete shared podcast | Delete = unfollow only, podcast remains |
| Processing abuse | Rate limiting on processing triggers |
| Follower count manipulation | No user-facing benefit to follower count |

---

## Open Questions

1. **Should there be podcast "ownership"?** The spec assumes no ownership — podcasts are communal. Consider: should the first user who adds a podcast have any special privileges?

2. **Orphan podcast cleanup?** If a podcast has 0 followers, should it be deleted after X days? Or kept indefinitely?

3. **Processing priority?** Should podcasts with more followers be processed first? The `get_follower_count()` method enables this.

4. **Rate limiting follows?** Should there be a limit on how many podcasts a user can follow? (Prevents abuse in hosted version)

5. **Follower visibility?** Should users see who else follows a podcast? Or keep follower lists private?

---

## Success Metrics

| Metric | Target | Measurement |
|--------|--------|-------------|
| Processing savings | 80%+ | Episodes processed once vs. per-user |
| Migration success | 100% | All existing podcasts migrated |
| CLI compatibility | Zero breaks | All CLI tests pass |
| Query performance | <100ms | Feed queries with 1000+ episodes |

---

## Dependencies

This specification depends on:

- [Authentication Specification](authentication.md) - User model, auth providers, JWT
- [Multi-User Web App Specification](multi-user-web-app.md) - Overall architecture context

---

## Revision History

| Date | Version | Changes |
|------|---------|---------|
| 2026-01-15 | 0.1 | Initial draft |
| 2026-01-21 | 0.2 | Phase 1 implementation complete - Follow/Unfollow feature |

---

## Implementation Notes (Phase 1)

### What Was Implemented

1. **Database**: `podcast_followers` table with migration in `sqlite_podcast_repository.py`
2. **Model**: `PodcastFollower` in `thestill/models/user.py`
3. **Repository**: `PodcastFollowerRepository` (abstract) and `SqlitePodcastFollowerRepository`
4. **Service**: `FollowerService` in `thestill/services/follower_service.py`
5. **API Endpoints**: Follow, unfollow, follower count in `api_podcasts.py`
6. **API Changes**: GET `/api/podcasts` filters by followed, POST `/api/commands/add` auto-follows
7. **Frontend**: "Follow" button (renamed from "Add"), "Unfollow" button on podcast detail

### UX Decisions Made

- **No catalog/browse screen**: Users only see podcasts they follow
- **No unfollow confirmation**: Single click unfollows immediately
- **Unfollow location**: Only on podcast detail page, not in list view
- **Follow flow**: Opens URL dialog, follows on successful add
- **Slug-based API**: All user-facing endpoints use slug, not ID

### Files Changed

Backend:

- `thestill/models/user.py` - Added `PodcastFollower` model
- `thestill/repositories/podcast_follower_repository.py` - New abstract repository
- `thestill/repositories/sqlite_podcast_follower_repository.py` - SQLite implementation
- `thestill/services/follower_service.py` - New service with business logic
- `thestill/web/dependencies.py` - Added follower_repository and follower_service to AppState
- `thestill/web/app.py` - Initialize follower components
- `thestill/web/routes/api_podcasts.py` - Follow/unfollow endpoints, filtered GET
- `thestill/web/routes/api_commands.py` - Auto-follow on add

Frontend:

- `src/api/client.ts` - Added `unfollowPodcast` function
- `src/hooks/useApi.ts` - Added `useUnfollowPodcast` hook
- `src/pages/PodcastDetail.tsx` - Added Unfollow button
- `src/pages/Podcasts.tsx` - Renamed "Add Podcast" to "Follow"
- `src/components/AddPodcastModal.tsx` - Renamed header to "Follow Podcast"
- `src/components/Button.tsx` - Reusable button component with variants

Testing:

- `tests/integration/follower/test_follower_flow.py` - 31 integration tests covering:
  - `TestFollowerServiceFlow` - Follow/unfollow operations, error handling, get_followed_podcasts
  - `TestSingleUserModeCompatibility` - Single-user mode works without auth
  - `TestPodcastFollowerRepository` - Repository operations (add, remove, exists, count)
