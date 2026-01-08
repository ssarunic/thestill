# thestill.me - Multi-User Podcast Tracking Web App

> **Status**: Draft
> **Created**: 2025-12-18
> **Last Updated**: 2025-12-19

## Vision

A web app where users can track their favorite podcasts, read transcripts and digest summaries, and filter out episodes that interest them. The core value is **saving time** by letting users quickly scan summaries to decide which episodes are worth listening to.

**Key Principle**: "Process Once, Deliver to Many" - episode transcription and summarization happens once and is shared across all users who track that podcast.

---

## Current Architecture (Before Multi-User)

This section documents the existing single-user architecture that will be extended.

### Existing Database Schema

```sql
-- Current schema (SQLite)
CREATE TABLE podcasts (
    id TEXT PRIMARY KEY,                    -- UUID v4 (internal)
    created_at TIMESTAMP,
    updated_at TIMESTAMP,
    rss_url TEXT UNIQUE NOT NULL,           -- External identifier
    title TEXT,
    slug TEXT,
    description TEXT,
    last_processed TIMESTAMP NULL
);

CREATE TABLE episodes (
    id TEXT PRIMARY KEY,                    -- UUID v4 (internal)
    podcast_id TEXT NOT NULL REFERENCES podcasts(id) ON DELETE CASCADE,
    created_at TIMESTAMP,
    updated_at TIMESTAMP,
    external_id TEXT NOT NULL,              -- GUID from RSS feed
    title TEXT,
    slug TEXT,
    description TEXT,
    pub_date TIMESTAMP NULL,
    audio_url TEXT,                         -- Source audio URL
    duration TEXT NULL,
    -- Processing state (file paths)
    audio_path TEXT NULL,                   -- Downloaded audio
    downsampled_audio_path TEXT NULL,       -- 16kHz WAV
    raw_transcript_path TEXT NULL,          -- Whisper JSON
    clean_transcript_path TEXT NULL,        -- Cleaned Markdown
    summary_path TEXT NULL,                 -- Generated summary
    UNIQUE(podcast_id, external_id)
);

CREATE TABLE episode_transcript_links (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    episode_id TEXT NOT NULL REFERENCES episodes(id) ON DELETE CASCADE,
    url TEXT NOT NULL,
    mime_type TEXT,
    language TEXT NULL,
    rel TEXT NULL,
    downloaded_path TEXT NULL,
    created_at TIMESTAMP,
    UNIQUE(episode_id, url)
);
```

### Existing Web Server Structure

```text
thestill/web/
├── __init__.py
├── app.py                         # FastAPI app factory with lifespan
├── dependencies.py                # AppState dataclass, get_app_state()
├── routes/
│   ├── __init__.py
│   ├── health.py                  # GET /, /health, /status
│   └── webhooks.py                # ElevenLabs webhook handlers
└── services/
    └── webhook_transcript_processor.py
```

**Existing Routes:**

- `GET /` - Service identification
- `GET /health` - Health check for load balancers
- `GET /status` - System statistics (mirrors CLI `status` command)
- `POST /webhook/elevenlabs/speech-to-text` - Transcription callbacks
- `GET /webhook/elevenlabs/results` - List webhook payloads
- `GET /webhook/elevenlabs/results/{id}` - Get specific result
- `DELETE /webhook/elevenlabs/results/{id}` - Delete result

### Existing Services

| Service | File | Responsibility |
|---------|------|----------------|
| `PodcastService` | `services/podcast_service.py` | Podcast CRUD, episode retrieval, transcript/summary reading |
| `RefreshService` | `services/refresh_service.py` | RSS feed discovery, episode updates |
| `StatsService` | `services/stats_service.py` | System-wide statistics aggregation |

### Existing Repository

| Repository | File | Methods |
|------------|------|---------|
| `SqlitePodcastRepository` | `repositories/sqlite_podcast_repository.py` | `get_all()`, `get()`, `get_by_url()`, `save()`, `delete()`, `get_episodes_by_podcast()`, `get_episode()`, `get_unprocessed_episodes()` |

### Existing Dependency Injection

```python
@dataclass
class AppState:
    config: Config
    path_manager: PathManager
    repository: SqlitePodcastRepository
    podcast_service: PodcastService
    stats_service: StatsService
```

### Episode State Machine

```python
class EpisodeState(Enum):
    DISCOVERED = "discovered"      # Has audio_url
    DOWNLOADED = "downloaded"      # Has audio_path
    DOWNSAMPLED = "downsampled"    # Has downsampled_audio_path
    TRANSCRIBED = "transcribed"    # Has raw_transcript_path
    CLEANED = "cleaned"            # Has clean_transcript_path
    SUMMARIZED = "summarized"      # Has summary_path
```

### File System Layout

```text
data/
├── podcasts.db                  # SQLite database
├── original_audio/              # Downloaded audio (MP3, M4A)
├── downsampled_audio/           # 16kHz WAV files
├── raw_transcripts/             # Whisper JSON output
├── clean_transcripts/           # Cleaned Markdown
├── summaries/                   # Generated summaries
├── podcast_facts/               # Recurring knowledge (hosts, sponsors)
├── episode_facts/               # Episode-specific (guests, topics)
├── webhook_data/                # ElevenLabs webhook payloads
├── debug_feeds/                 # RSS feed snapshots
└── evaluations/                 # Transcript quality scores
```

---

## Core Features

### 1. Podcast Discovery & Tracking

- Add podcasts via RSS URL, Apple Podcasts link, or YouTube channel
- Browse/search public podcast catalog (podcasts already in the system)
- Track multiple podcasts per user

### 2. Episode Browsing

- View all episodes from tracked podcasts
- Filter by: unread, read, saved, podcast
- Sort by: date, podcast, processing status

### 3. Transcript & Summary Reading

- Read full cleaned transcripts
- Read AI-generated summaries with:
  - Executive summary (1-2 paragraphs)
  - Key topics/segments
  - Notable quotes
  - Content angles for different audiences
- Mark episodes as read/unread/saved

### 4. Episode Triage

- Quick-scan mode: see summary cards for recent episodes
- "Interested" / "Not Interested" / "Save for Later" actions
- Track listening progress (optional)

### 5. Newsletter Digests (Optional)

- Opt-in daily/weekly email digests
- Summaries of new episodes from tracked podcasts
- Configurable: frequency, day/time, content preferences

---

## Architecture Summary

```text
┌─────────────────────────────────────────────────────────────────────────────┐
│                              THREE-STAGE PIPELINE                            │
├─────────────────────────────────────────────────────────────────────────────┤
│                                                                              │
│  ┌──────────────┐    ┌──────────────────────────────┐    ┌───────────────┐  │
│  │   LISTENER   │───▶│          FACTORY             │───▶│    POSTMAN    │  │
│  │              │    │                              │    │   (optional)  │  │
│  │ - Poll RSS   │    │ download ─▶ downsample ─▶    │    │               │  │
│  │ - Discover   │    │ transcribe ─▶ clean ─▶       │    │ - Compile     │  │
│  │   episodes   │    │ summarize                    │    │   newsletter  │  │
│  └──────────────┘    └──────────────────────────────┘    └───────────────┘  │
│         │                        │                              │           │
│         └────────────────────────┴──────────────────────────────┘           │
│                                  │                                          │
│                         ┌────────▼────────┐                                 │
│                         │   SQLite Queue  │                                 │
│                         │   (job_queue)   │                                 │
│                         └─────────────────┘                                 │
└─────────────────────────────────────────────────────────────────────────────┘

┌─────────────────────────────────────────────────────────────────────────────┐
│                              FULL ARCHITECTURE                               │
├─────────────────────────────────────────────────────────────────────────────┤
│                                                                              │
│  ┌─────────────────────────────────────────────────────────────────────┐    │
│  │                         FRONTEND (React SPA)                         │    │
│  │  Login ─ Dashboard ─ Podcasts ─ Episodes ─ Settings                 │    │
│  └─────────────────────────────────────────────────────────────────────┘    │
│                                    │                                         │
│                                    ▼                                         │
│  ┌─────────────────────────────────────────────────────────────────────┐    │
│  │                         FASTAPI BACKEND                              │    │
│  │  ┌─────────┐ ┌──────────────┐ ┌──────────┐ ┌────────┐ ┌─────────┐  │    │
│  │  │  Auth   │ │ Subscriptions│ │ Episodes │ │  Feed  │ │  Admin  │  │    │
│  │  │ Routes  │ │   Routes     │ │  Routes  │ │ Routes │ │ Routes  │  │    │
│  │  └────┬────┘ └──────┬───────┘ └────┬─────┘ └───┬────┘ └────┬────┘  │    │
│  │       │             │              │           │           │        │    │
│  │  ┌────▼─────────────▼──────────────▼───────────▼───────────▼────┐  │    │
│  │  │                      SERVICE LAYER                            │  │    │
│  │  │  UserService │ SubscriptionService │ EpisodeFeedService │ ... │  │    │
│  │  └────────────────────────────┬──────────────────────────────────┘  │    │
│  │                               │                                      │    │
│  │  ┌────────────────────────────▼──────────────────────────────────┐  │    │
│  │  │                     REPOSITORY LAYER                           │  │    │
│  │  │  UserRepository │ SubscriptionRepository │ SqlitePodcastRepo   │  │    │
│  │  └────────────────────────────┬──────────────────────────────────┘  │    │
│  └───────────────────────────────┼──────────────────────────────────────┘    │
│                                  │                                           │
│  ┌───────────────────────────────▼──────────────────────────────────────┐   │
│  │                         SQLite DATABASE                               │   │
│  │  users │ subscriptions │ user_episodes │ podcasts │ episodes │ jobs  │   │
│  └──────────────────────────────────────────────────────────────────────┘   │
│                                                                              │
└─────────────────────────────────────────────────────────────────────────────┘
```

---

## Technology Stack

| Component | Choice | Notes |
|-----------|--------|-------|
| **Backend** | FastAPI (existing) | Extend current `thestill/web/` |
| **Frontend** | React SPA (Vite) | TypeScript, in `thestill/web/frontend/` |
| **Auth** | Google OAuth | No password management, JWT sessions |
| **Database** | SQLite (existing) | Add new tables, migrate to PostgreSQL later |
| **Email** | SendGrid | For newsletter digests (optional) |
| **Workers** | Python asyncio | Cloud-agnostic, GCP/AWS/Supabase compatible |

---

## Database Schema

### New Tables (Additive - No Breaking Changes to Existing)

```sql
-- Users (Google OAuth only)
CREATE TABLE users (
    id TEXT PRIMARY KEY,                    -- UUID v4
    email TEXT NOT NULL UNIQUE,
    email_verified_at TIMESTAMP NULL,
    display_name TEXT DEFAULT '',
    avatar_url TEXT NULL,
    timezone TEXT DEFAULT 'UTC',
    -- Newsletter preferences (optional feature)
    newsletter_enabled INTEGER DEFAULT 0,   -- SQLite boolean
    newsletter_frequency TEXT DEFAULT 'daily',  -- daily, weekly
    newsletter_send_hour INTEGER DEFAULT 8,
    newsletter_send_day INTEGER NULL,       -- 0-6 for weekly
    is_active INTEGER DEFAULT 1,
    created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
    updated_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
);
CREATE INDEX idx_users_email ON users(email);

-- User-Podcast subscriptions (tracking)
CREATE TABLE subscriptions (
    id TEXT PRIMARY KEY,                    -- UUID v4
    user_id TEXT NOT NULL REFERENCES users(id) ON DELETE CASCADE,
    podcast_id TEXT NOT NULL REFERENCES podcasts(id) ON DELETE CASCADE,
    -- Content preferences
    include_transcript INTEGER DEFAULT 1,
    include_summary INTEGER DEFAULT 1,
    include_quotes INTEGER DEFAULT 1,
    is_active INTEGER DEFAULT 1,
    created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
    UNIQUE(user_id, podcast_id)
);
CREATE INDEX idx_subscriptions_user ON subscriptions(user_id);
CREATE INDEX idx_subscriptions_podcast ON subscriptions(podcast_id);

-- User-Episode interactions (read status, saved, etc.)
CREATE TABLE user_episodes (
    id TEXT PRIMARY KEY,                    -- UUID v4
    user_id TEXT NOT NULL REFERENCES users(id) ON DELETE CASCADE,
    episode_id TEXT NOT NULL REFERENCES episodes(id) ON DELETE CASCADE,
    -- Reading state
    is_read INTEGER DEFAULT 0,
    read_at TIMESTAMP NULL,
    -- Triage state
    interest_level TEXT NULL,               -- interested, not_interested, saved
    interest_set_at TIMESTAMP NULL,
    -- Notes (optional)
    notes TEXT NULL,
    created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
    updated_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
    UNIQUE(user_id, episode_id)
);
CREATE INDEX idx_user_episodes_user ON user_episodes(user_id);
CREATE INDEX idx_user_episodes_episode ON user_episodes(episode_id);
CREATE INDEX idx_user_episodes_interest ON user_episodes(user_id, interest_level);

-- Newsletter records (optional feature)
CREATE TABLE newsletters (
    id TEXT PRIMARY KEY,                    -- UUID v4
    user_id TEXT NOT NULL REFERENCES users(id) ON DELETE CASCADE,
    scheduled_for TIMESTAMP NOT NULL,
    sent_at TIMESTAMP NULL,
    subject TEXT NOT NULL,
    html_content TEXT NOT NULL,
    plain_content TEXT NOT NULL,
    status TEXT DEFAULT 'pending',          -- pending, sent, failed
    error_message TEXT NULL,
    retry_count INTEGER DEFAULT 0,
    created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
);
CREATE INDEX idx_newsletters_user ON newsletters(user_id);
CREATE INDEX idx_newsletters_status ON newsletters(status, scheduled_for);

CREATE TABLE newsletter_items (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    newsletter_id TEXT NOT NULL REFERENCES newsletters(id) ON DELETE CASCADE,
    episode_id TEXT NOT NULL REFERENCES episodes(id),
    UNIQUE(newsletter_id, episode_id)
);

-- Job queue for background processing
CREATE TABLE job_queue (
    id TEXT PRIMARY KEY,                    -- UUID v4
    job_type TEXT NOT NULL,                 -- listener, factory, postman
    stage TEXT NULL,                        -- download, downsample, transcribe, clean, summarize
    payload TEXT NOT NULL,                  -- JSON
    run_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
    priority INTEGER DEFAULT 0,             -- Higher = more urgent
    status TEXT DEFAULT 'pending',          -- pending, processing, completed, failed, dlq
    locked_by TEXT NULL,                    -- Worker ID
    locked_at TIMESTAMP NULL,
    retry_count INTEGER DEFAULT 0,
    max_retries INTEGER DEFAULT 3,
    last_error TEXT NULL,
    created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
);
CREATE INDEX idx_jobs_status ON job_queue(status, run_at);
CREATE INDEX idx_jobs_type ON job_queue(job_type, status);
CREATE INDEX idx_jobs_locked ON job_queue(locked_by, locked_at);
```

### Schema Migration Strategy

1. **Non-breaking**: New tables only, no changes to existing `podcasts`/`episodes`
2. **Backward compatible**: CLI continues to work without authentication
3. **Migration script**: `thestill db migrate` command to apply schema changes

---

## API Endpoints

### Existing Routes (Unchanged)

| Method | Endpoint | Description |
|--------|----------|-------------|
| GET | `/` | Service identification |
| GET | `/health` | Health check |
| GET | `/status` | System statistics |
| POST | `/webhook/elevenlabs/speech-to-text` | Transcription callback |
| GET | `/webhook/elevenlabs/results` | List webhook payloads |
| GET | `/webhook/elevenlabs/results/{id}` | Get specific result |
| DELETE | `/webhook/elevenlabs/results/{id}` | Delete result |

### New Routes: Auth (`/api/auth`)

| Method | Endpoint | Description |
|--------|----------|-------------|
| GET | `/google` | Initiate Google OAuth |
| GET | `/google/callback` | Handle OAuth callback, return JWT |
| GET | `/me` | Get current user profile |
| PATCH | `/me` | Update user preferences |
| POST | `/logout` | Invalidate session |
| POST | `/refresh` | Refresh JWT token |

### New Routes: Podcasts (`/api/podcasts`)

| Method | Endpoint | Description |
|--------|----------|-------------|
| GET | `/` | List all podcasts (public catalog) |
| GET | `/{id}` | Get podcast details with episode count |
| POST | `/` | Add new podcast (by RSS/URL) |
| GET | `/{id}/episodes` | List episodes for a podcast |

### New Routes: Subscriptions (`/api/subscriptions`)

| Method | Endpoint | Description |
|--------|----------|-------------|
| GET | `/` | List user's tracked podcasts |
| POST | `/` | Track a podcast `{podcast_id}` |
| DELETE | `/{podcast_id}` | Untrack a podcast |
| PATCH | `/{podcast_id}` | Update tracking preferences |

### New Routes: Episodes (`/api/episodes`)

| Method | Endpoint | Description |
|--------|----------|-------------|
| GET | `/{id}` | Get episode metadata + processing state |
| GET | `/{id}/transcript` | Get cleaned transcript (Markdown) |
| GET | `/{id}/summary` | Get summary content |

### New Routes: User-Episode Interactions (`/api/episodes/{id}`)

| Method | Endpoint | Description |
|--------|----------|-------------|
| POST | `/read` | Mark as read |
| DELETE | `/read` | Mark as unread |
| POST | `/interest` | Set interest level `{level}` |
| PUT | `/notes` | Add/update notes `{text}` |

### New Routes: Feed (`/api/feed`)

| Method | Endpoint | Description |
|--------|----------|-------------|
| GET | `/` | Personalized feed (tracked podcasts) |
| GET | `/unread` | Unread episodes only |
| GET | `/saved` | Saved episodes |

**Query Parameters for Feed:**

- `?page=1&per_page=20` - Pagination
- `?podcast_id=xxx` - Filter by podcast
- `?since=2025-01-01` - Filter by date
- `?status=summarized` - Filter by processing state

### New Routes: Admin (`/api/admin`)

| Method | Endpoint | Description |
|--------|----------|-------------|
| GET | `/queue/stats` | Job queue statistics |
| GET | `/dlq` | Dead Letter Queue entries |
| POST | `/dlq/{id}/retry` | Retry failed job |
| DELETE | `/dlq/{id}` | Discard failed job |

### New Routes: Newsletters (`/api/newsletters`) - Optional

| Method | Endpoint | Description |
|--------|----------|-------------|
| GET | `/` | User's newsletter history |
| GET | `/{id}` | View newsletter content |
| POST | `/{id}/resend` | Resend newsletter |

---

## Services

### New Services

| Service | File | Responsibility |
|---------|------|----------------|
| `UserService` | `services/user_service.py` | User CRUD, OAuth token handling |
| `SubscriptionService` | `services/subscription_service.py` | Track/untrack podcasts |
| `EpisodeFeedService` | `services/episode_feed_service.py` | Personalized feed with filters |
| `UserEpisodeService` | `services/user_episode_service.py` | Read/saved/interest state |
| `QueueService` | `services/queue_service.py` | Job queue operations |
| `NewsletterService` | `services/newsletter_service.py` | Compile and send digests |

### Enhanced Existing Services

| Service | Changes |
|---------|---------|
| `PodcastService` | Add `get_subscribed_podcasts(user_id)` method |
| `StatsService` | Add `get_user_stats(user_id)` for per-user metrics |

### New Repositories

| Repository | File | Responsibility |
|------------|------|----------------|
| `UserRepository` | `repositories/user_repository.py` | User CRUD |
| `SubscriptionRepository` | `repositories/subscription_repository.py` | User-podcast links |
| `UserEpisodeRepository` | `repositories/user_episode_repository.py` | User-episode state |
| `JobQueueRepository` | `repositories/job_queue_repository.py` | Job queue operations |

---

## Workers

### ListenerWorker

- Runs every 15 minutes (configurable)
- Polls RSS feeds for all podcasts with at least one subscriber
- Discovers new episodes
- Enqueues `factory` jobs for new episodes

```python
class ListenerWorker:
    async def run(self):
        """Poll feeds and enqueue processing jobs."""
        subscribed_podcasts = await self.get_subscribed_podcasts()
        for podcast in subscribed_podcasts:
            new_episodes = await self.refresh_service.refresh(podcast.id)
            for episode in new_episodes:
                await self.queue_service.enqueue(
                    job_type="factory",
                    stage="download",
                    payload={"episode_id": episode.id}
                )
```

### FactoryWorker

- Processes episodes through the existing pipeline
- Stages: download → downsample → transcribe → clean → summarize
- Each stage is a separate job (allows resume on failure)
- Reuses existing core modules:
  - `AudioDownloader` for download
  - `AudioPreprocessor` for downsample
  - `Transcriber` for transcribe
  - `TranscriptCleaner` for clean
  - `PostProcessor` for summarize

```python
class FactoryWorker:
    async def process(self, job: Job):
        """Process a single pipeline stage."""
        episode = await self.get_episode(job.payload["episode_id"])

        if job.stage == "download":
            await self.audio_downloader.download(episode)
            await self.enqueue_next(episode, "downsample")
        elif job.stage == "downsample":
            await self.audio_preprocessor.downsample(episode)
            await self.enqueue_next(episode, "transcribe")
        # ... etc
```

### PostmanWorker (Optional)

- Runs daily/weekly based on user preferences
- Compiles newsletter content from new episodes
- Sends via SendGrid API

---

## Frontend Structure

```text
thestill/web/
├── __init__.py
├── app.py                         # FastAPI app factory (existing)
├── dependencies.py                # AppState + get_current_user (enhanced)
├── routes/
│   ├── __init__.py
│   ├── health.py                  # Existing: /, /health, /status
│   ├── webhooks.py                # Existing: ElevenLabs webhooks
│   ├── auth.py                    # NEW: Google OAuth, JWT
│   ├── podcasts.py                # NEW: Podcast catalog
│   ├── subscriptions.py           # NEW: User tracking
│   ├── episodes.py                # NEW: Episode content
│   ├── feed.py                    # NEW: Personalized feed
│   ├── newsletters.py             # NEW: Newsletter history
│   └── admin.py                   # NEW: Queue management
├── middleware/
│   ├── __init__.py
│   └── auth.py                    # NEW: JWT validation middleware
├── services/
│   ├── webhook_transcript_processor.py  # Existing
│   └── ... (new services)
├── frontend/                      # NEW: React SPA
│   ├── src/
│   │   ├── App.tsx
│   │   ├── main.tsx
│   │   ├── pages/
│   │   │   ├── Login.tsx          # Google OAuth
│   │   │   ├── Dashboard.tsx      # Main feed view
│   │   │   ├── Podcasts.tsx       # Browse/search catalog
│   │   │   ├── PodcastDetail.tsx  # Single podcast + episodes
│   │   │   ├── EpisodeDetail.tsx  # Transcript + summary
│   │   │   ├── Saved.tsx          # Saved episodes
│   │   │   └── Settings.tsx       # User preferences
│   │   ├── components/
│   │   │   ├── EpisodeCard.tsx    # Summary card in feed
│   │   │   ├── EpisodeList.tsx    # Episode list view
│   │   │   ├── TranscriptViewer.tsx
│   │   │   ├── SummaryViewer.tsx
│   │   │   ├── PodcastCard.tsx
│   │   │   ├── InterestButtons.tsx
│   │   │   └── Navbar.tsx
│   │   ├── api/
│   │   │   ├── client.ts          # Axios/fetch wrapper
│   │   │   ├── auth.ts
│   │   │   ├── podcasts.ts
│   │   │   ├── episodes.ts
│   │   │   └── feed.ts
│   │   ├── hooks/
│   │   │   ├── useAuth.ts
│   │   │   ├── useFeed.ts
│   │   │   └── usePodcasts.ts
│   │   └── context/
│   │       └── AuthContext.tsx
│   ├── package.json
│   ├── vite.config.ts
│   ├── tsconfig.json
│   └── index.html
└── static/                        # Built React assets (production)
```

### Development Setup

```bash
# Terminal 1: API server
thestill server --reload --port 8000

# Terminal 2: React dev server
cd thestill/web/frontend
npm install
npm run dev  # Port 3000, proxies /api to 8000
```

### Production Build

```bash
cd thestill/web/frontend
npm run build  # Outputs to ../static/

# FastAPI serves static files
thestill server --port 8000
# React app at http://localhost:8000/
# API at http://localhost:8000/api/
```

---

## Configuration

```bash
# .env additions for multi-user mode

# Enable multi-user features (default: false for CLI-only mode)
ENABLE_MULTI_USER=true

# Google OAuth
GOOGLE_CLIENT_ID=your-client-id
GOOGLE_CLIENT_SECRET=your-client-secret
OAUTH_REDIRECT_URI=http://localhost:3000/auth/callback

# JWT
JWT_SECRET=your-secret-key-min-32-chars
JWT_ALGORITHM=HS256
JWT_ACCESS_TOKEN_EXPIRE_MINUTES=60
JWT_REFRESH_TOKEN_EXPIRE_DAYS=7

# SendGrid (optional, for newsletters)
SENDGRID_API_KEY=your-sendgrid-api-key
EMAIL_FROM_ADDRESS=hello@thestill.me
EMAIL_FROM_NAME=thestill.me

# Workers
WORKER_COUNT=2
LISTENER_POLL_INTERVAL_SECONDS=900  # 15 minutes
FACTORY_POLL_INTERVAL_SECONDS=1.0

# Frontend
FRONTEND_URL=http://localhost:3000
CORS_ORIGINS=http://localhost:3000,http://localhost:8000
```

---

## Implementation Phases

### Phase 1: Database & Models (2-3 days)

- [ ] Create database migration script
- [ ] Add new tables: `users`, `subscriptions`, `user_episodes`, `job_queue`
- [ ] Create Pydantic models: `User`, `Subscription`, `UserEpisode`, `Job`
- [ ] Create repositories: `UserRepository`, `SubscriptionRepository`, `UserEpisodeRepository`, `JobQueueRepository`
- [ ] Add `ENABLE_MULTI_USER` config flag

### Phase 2: Authentication (2-3 days)

- [ ] Implement Google OAuth flow
- [ ] Create JWT token generation/validation
- [ ] Add auth middleware (`get_current_user` dependency)
- [ ] Create `UserService`
- [ ] Add `/api/auth/*` routes

### Phase 3: Subscriptions & Feed (2-3 days)

- [ ] Create `SubscriptionService`
- [ ] Create `EpisodeFeedService` with filtering
- [ ] Add `/api/subscriptions/*` routes
- [ ] Add `/api/feed/*` routes
- [ ] Add `/api/podcasts/*` routes (public catalog)

### Phase 4: Episode Interactions (1-2 days)

- [ ] Create `UserEpisodeService`
- [ ] Add `/api/episodes/{id}/read`, `/interest`, `/notes` routes
- [ ] Integrate with feed queries

### Phase 5: Job Queue & Workers (3-4 days)

- [ ] Create `QueueService`
- [ ] Implement `BaseWorker` with retry logic, exponential backoff
- [ ] Implement `ListenerWorker`
- [ ] Implement `FactoryWorker` (integrate existing pipeline)
- [ ] Add `thestill worker` CLI command
- [ ] Add `/api/admin/queue/*` routes

### Phase 6: React Frontend (5-7 days)

- [ ] Set up Vite + React + TypeScript
- [ ] Implement auth flow (Google OAuth)
- [ ] Dashboard/Feed page
- [ ] Podcast browsing/search
- [ ] Episode detail (transcript + summary)
- [ ] Settings page
- [ ] Production build + static serving

### Phase 7: Newsletter (Optional, 2-3 days)

- [ ] Create `NewsletterService`
- [ ] Implement `PostmanWorker`
- [ ] SendGrid integration
- [ ] Newsletter preferences UI

---

## Error Handling

### Transient Errors (Retry with Backoff)

| Error | Retry Strategy |
|-------|----------------|
| Network timeout | 1min → 2min → 4min → 8min → DLQ |
| Rate limit (429, 503) | Respect `Retry-After` header |
| Database lock | 100ms → 200ms → 400ms |
| LLM API error | 30s → 60s → 120s |

### Permanent Errors (Move to DLQ)

- Corrupt audio files (can't decode)
- 404 URLs (resource deleted)
- Invalid RSS feed format
- Authentication failures

### DLQ (Dead Letter Queue)

- Jobs moved to DLQ after `max_retries` failures
- Admin can inspect, retry, or discard
- Alerts via logging (future: email/Slack)

---

## Security Considerations

### Authentication

- Google OAuth only (no password storage)
- JWT tokens with short expiry (1 hour)
- Refresh tokens stored securely (httpOnly cookie)
- CSRF protection via SameSite cookies

### Authorization

- All `/api/*` routes require authentication (except `/api/auth/*`)
- Users can only access their own subscriptions/episodes
- Admin routes require `is_admin` flag on user

### Data Protection

- No PII stored beyond email/name from Google
- Podcast content is public (shared across users)
- User preferences stored in DB (not cookies)

---

## Future Enhancements

- **Search**: Full-text search within transcripts (SQLite FTS5)
- **Highlights**: Save and annotate transcript sections
- **Sharing**: Share episodes with friends via link
- **Mobile App**: React Native companion
- **Podcast Recommendations**: Based on listening history
- **AI Chat**: Ask questions about episode content (RAG)
- **Export**: Download transcripts as PDF/EPUB
- **Webhooks**: Notify external services on new episodes
