# Web Server

FastAPI-based web server with REST API, React frontend, and webhook handlers.

## Starting the Server

```bash
thestill server                    # Start on localhost:8000
thestill server --host 0.0.0.0     # Expose to network
thestill server --port 8080        # Custom port
thestill server --reload           # Development mode with auto-reload
thestill server --workers 4        # Multiple worker processes
```

## API Endpoints

### Health & Status

| Endpoint | Method | Description |
|----------|--------|-------------|
| `/` | GET | Service identification |
| `/health` | GET | Health check for load balancers |
| `/status` | GET | System statistics |
| `/docs` | GET | OpenAPI documentation |

### Podcasts (`/api/podcasts`)

| Endpoint | Method | Description |
|----------|--------|-------------|
| `/api/podcasts` | GET | List all podcasts |
| `/api/podcasts` | POST | Add new podcast `{url}` |
| `/api/podcasts/{slug}` | GET | Get podcast details |
| `/api/podcasts/{slug}` | DELETE | Remove podcast |
| `/api/podcasts/{slug}/refresh` | POST | Trigger feed refresh |

### Episodes (`/api/episodes`)

| Endpoint | Method | Description |
|----------|--------|-------------|
| `/api/podcasts/{podcast_slug}/episodes` | GET | List episodes (filterable) |
| `/api/podcasts/{podcast_slug}/episodes/{episode_slug}` | GET | Get episode details |
| `/api/episodes/{id}/transcript` | GET | Get transcript content |
| `/api/episodes/{id}/summary` | GET | Get summary content |
| `/api/episodes/{id}/failure` | GET | Get failure details |
| `/api/episodes/{id}/retry` | POST | Clear failure and retry |

### Commands (`/api/commands`)

| Endpoint | Method | Description |
|----------|--------|-------------|
| `/api/commands/run-pipeline` | POST | Run full pipeline for episode |
| `/api/commands/dlq` | GET | List dead letter queue tasks |
| `/api/commands/dlq/{task_id}/retry` | POST | Retry dead task |
| `/api/commands/dlq/{task_id}/skip` | POST | Skip/resolve dead task |

### Digests (`/api/digests`)

| Endpoint | Method | Description |
|----------|--------|-------------|
| `/api/digests` | GET | List digests (filterable by status) |
| `/api/digests` | POST | Create new digest |
| `/api/digests/latest` | GET | Get most recent digest |
| `/api/digests/preview` | POST | Preview episode selection |
| `/api/digests/{digest_id}` | GET | Get digest details |
| `/api/digests/{digest_id}` | DELETE | Delete digest |
| `/api/digests/{digest_id}/content` | GET | Get digest markdown content |
| `/api/digests/{digest_id}/episodes` | GET | Get episodes in digest |

### Authentication (`/auth`)

| Endpoint | Method | Description |
|----------|--------|-------------|
| `/auth/status` | GET | Get authentication mode and user info |
| `/auth/google/login` | GET | Initiate Google OAuth flow |
| `/auth/google/callback` | GET | OAuth callback handler |
| `/auth/logout` | POST | Clear authentication cookie |
| `/auth/me` | GET | Get current user info (requires auth in multi-user mode) |

### Webhooks

| Endpoint | Method | Description |
|----------|--------|-------------|
| `/webhook/elevenlabs/speech-to-text` | POST | Receive transcription callback |
| `/webhook/elevenlabs/results` | GET | List webhook results |
| `/webhook/elevenlabs/results/{id}` | GET | Get specific result |
| `/webhook/elevenlabs/results/{id}` | DELETE | Delete result |

## Project Structure

```
thestill/web/
├── __init__.py              # Package init with create_app export
├── app.py                   # FastAPI application factory
├── dependencies.py          # Dependency injection (AppState, get_app_state)
├── routes/
│   ├── __init__.py
│   ├── health.py            # Health check and status endpoints
│   ├── webhooks.py          # ElevenLabs webhook handlers
│   ├── api_podcasts.py      # Podcast CRUD endpoints
│   ├── api_episodes.py      # Episode content endpoints
│   ├── api_commands.py      # Processing commands (pipeline, DLQ)
│   ├── api_digests.py       # Digest CRUD and content endpoints
│   └── auth.py              # Authentication endpoints (OAuth, JWT)
├── frontend/                # React SPA
│   ├── src/
│   │   ├── App.tsx
│   │   ├── pages/
│   │   │   ├── Dashboard.tsx
│   │   │   ├── Podcasts.tsx
│   │   │   ├── Episodes.tsx
│   │   │   ├── EpisodeDetail.tsx
│   │   │   ├── Digests.tsx          # Digest list with create modal
│   │   │   ├── DigestDetail.tsx     # Digest content and episodes
│   │   │   ├── FailedTasks.tsx
│   │   │   └── Login.tsx            # Google OAuth login page
│   │   ├── contexts/
│   │   │   └── AuthContext.tsx      # Authentication state management
│   │   ├── components/
│   │   │   ├── Layout.tsx
│   │   │   ├── EpisodeCard.tsx
│   │   │   ├── PipelineActionButton.tsx
│   │   │   ├── FailureBanner.tsx
│   │   │   ├── FailureDetailsModal.tsx
│   │   │   ├── MorningBriefingWidget.tsx  # Dashboard digest widget
│   │   │   ├── ProtectedRoute.tsx   # Route protection wrapper
│   │   │   └── UserMenu.tsx         # User avatar dropdown
│   │   └── api/
│   │       ├── client.ts
│   │       └── types.ts
│   └── package.json
└── static/                  # Built frontend assets
```

## Architecture

```
CLI (cli.py)                    Web (web/app.py)
     |                               |
     v                               v
  CLIContext                     AppState
     |                               |
     +--------> Services <-----------+
                   |
          PodcastService
          StatsService
          Repository
          PathManager
```

- **app.py**: Application factory with lifespan management
  - Initializes services once at startup (same pattern as CLI)
  - Stores `AppState` in `app.state` for route access
  - Registers route modules

- **dependencies.py**: FastAPI dependency injection
  - `AppState`: Dataclass mirroring `CLIContext` from CLI
  - `get_app_state()`: Dependency function for routes

## Authentication

The web server supports two authentication modes:

### Single-User Mode (Default)

When `MULTI_USER=false`:

- No login required, all routes accessible
- A default user is auto-created for data ownership tracking
- `UserMenu` shows "Single-user mode" indicator
- Best for personal/local deployments

### Multi-User Mode

When `MULTI_USER=true`:

- Google OAuth 2.0 authentication required
- Protected routes redirect unauthenticated users to `/login`
- JWT tokens stored in httpOnly cookies (30-day expiry by default)
- User data isolated by account

**Authentication Flow**:

1. User visits protected route → redirected to `/login`
2. User clicks "Sign in with Google" → redirected to Google OAuth
3. After Google approval → callback to `/auth/google/callback`
4. Server creates/updates user, issues JWT cookie
5. User redirected to dashboard

**Frontend Components**:

- `AuthContext`: Manages auth state, provides `login`/`logout` functions
- `ProtectedRoute`: Wrapper that enforces authentication
- `UserMenu`: Displays user avatar with logout option

## Webhook Security

### Dual-Layer Security

1. **HMAC Signature Verification** (Layer 1):
   - Validates `ElevenLabs-Signature` header
   - Uses `ELEVENLABS_WEBHOOK_SECRET` from config
   - Proves request actually came from ElevenLabs

2. **Metadata Validation** (Layer 2):
   - Requires `episode_id` in `webhook_metadata`
   - Verifies episode exists in database
   - Prevents processing webhooks from other apps sharing the same ElevenLabs account

### Configuration

```bash
# .env
ELEVENLABS_WEBHOOK_SECRET=your_secret_from_elevenlabs_dashboard
ELEVENLABS_WEBHOOK_REQUIRE_METADATA=true  # default: true
```

## Full Pipeline Execution

When "Run Full Pipeline" is triggered from the Web UI:

1. Task is created with `metadata.run_full_pipeline = true`
2. Each stage completion automatically enqueues the next stage
3. Pipeline continues until summarization or failure
4. Progress is tracked in real-time via polling

## Digest Interface

The web UI provides a full interface for creating and viewing digests - consolidated summaries of multiple podcast episodes.

### Dashboard Widget

The **Morning Briefing Widget** on the dashboard provides quick access to digest functionality:

- Shows count of episodes ready to summarize
- Displays status of the latest digest
- **Quick Catch-Up** button creates a digest with default settings:
  - Last 7 days of episodes
  - Maximum 10 episodes
  - Only already-summarized episodes (`ready_only=true`)
  - Excludes previously digested episodes

### Digests Page (`/digests`)

The digests page provides:

- **Stats cards**: Total digests, completed count, partial count
- **Digest list**: All digests with status badges and episode counts
- **Progress indicators**: Active digests show real-time progress with polling
- **Create modal**: Configure and create new digests

### Creating a Digest

1. Click "New Digest" button
2. Configure options:
   - **Time range**: Episodes from last N days (1-365)
   - **Max episodes**: Limit number of episodes (1-100)
   - **Podcast filter**: Optional - limit to specific podcast
   - **Ready only**: Only include already-summarized episodes
   - **Exclude digested**: Skip episodes already in other digests
3. Click "Preview" to see which episodes would be included
4. Click "Create Digest" to generate

**Digest Status Values**:

| Status | Description |
|--------|-------------|
| `pending` | Digest created, episodes being processed |
| `in_progress` | Actively generating digest content |
| `completed` | Successfully generated with all episodes |
| `partial` | Completed with some episode failures |
| `failed` | Generation failed |

### Digest Detail Page (`/digests/{id}`)

Shows complete digest information:

- **Header**: Creation date, period covered, status badge
- **Stats**: Episode counts (total/completed/failed), success rate, processing time
- **Content tab**: Rendered markdown digest document
- **Episodes tab**: List of included episodes with links to episode details
- **Delete action**: Remove digest with confirmation

### Real-Time Progress

When a digest is being processed (status `pending` or `in_progress`):

- Digest list automatically polls every 3 seconds
- Progress bar shows episodes completed vs total
- Status updates as processing progresses
