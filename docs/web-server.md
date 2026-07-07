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
| `/api/podcasts` | GET | List followed podcasts (paginated; `?q=` filters by title/author) |
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

### Briefings (`/api/briefings`)

| Endpoint | Method | Description |
|----------|--------|-------------|
| `/api/briefings` | GET | Paginated briefing history for the current user, newest first |
| `/api/briefings/latest` | GET | Latest briefing for the current user (lazy-generates when eligible) |
| `/api/briefings/schedule` | GET | Current user's briefing schedule (spec #50) |
| `/api/briefings/schedule` | PUT | Upsert schedule (frequency, hour, weekday, timezone, enabled) |
| `/api/briefings/{briefing_id}` | GET | Briefing metadata + narration variants |
| `/api/briefings/{briefing_id}/script` | GET | Rendered script markdown |
| `/api/briefings/{briefing_id}/narrate` | POST | Generate a narration variant (spec #33) |
| `/api/briefings/{briefing_id}/listened` | POST | Mark briefing listened |

### Inbox (`/api/inbox`)

Per-user episode deliveries (spec #29). All endpoints operate on the authenticated user's rows.

| Endpoint | Method | Description |
|----------|--------|-------------|
| `/api/inbox` | GET | List inbox items, newest delivery first. Query: `state`, `limit`, `before` (cursor by `delivered_at`) |
| `/api/inbox/unread-count` | GET | Lightweight unread count for badge rendering |
| `/api/inbox/{episode_id}/state` | POST | Set row state explicitly. Body: `{"state": "read"\|"saved"\|"dismissed"\|"unread"}`. 404 when no row exists |
| `/api/inbox/{episode_id}/read` | POST | View-driven read tracking: transitions `unread → read` only, never touching `saved`/`dismissed`. Always 200 with `{"marked": bool}`; a missing row is a no-op. Fired by the episode page once a summary is available |

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
│   └── auth.py              # Authentication endpoints (OAuth, JWT)
├── frontend/                # React SPA
│   ├── src/
│   │   ├── App.tsx
│   │   ├── pages/
│   │   │   ├── Dashboard.tsx
│   │   │   ├── Podcasts.tsx
│   │   │   ├── Episodes.tsx
│   │   │   ├── EpisodeDetail.tsx
│   │   │   ├── BriefingDetail.tsx   # Briefing script + narration reader
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

## Briefing Interface

Briefings are per-user readouts of the inbox (spec #36): each briefing covers
everything that landed in your inbox since the previous one. Generation is
lazy (opening the inbox creates one when eligible) or scheduled per user
(spec #50 — daily/weekly at a chosen hour and timezone, configured in
Settings).

### Inbox Card

The top of `/inbox` shows a "Today's briefing" card when a briefing with
unread coverage exists. It links to the briefing detail page.

### Briefing Detail Page (`/briefings/{id}`)

- **Script reader**: the rendered morning-briefing markdown
- **Narration variants** (spec #33): short/medium/long length switcher;
  generating a missing length calls `POST /api/briefings/{id}/narrate`
- **Mark listened**: advances the read state (the next briefing's window
  is cursor-based either way)

### Schedule Settings

The Settings page configures per-user scheduled generation: enable toggle,
daily/weekly frequency (with weekday), hour, and IANA timezone. The server
must run with `BRIEFING_SCHEDULER_ENABLED=true` for schedules to fire.
