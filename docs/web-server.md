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
| `/` | GET | Serves the React SPA (`index.html` via the catch-all route) |
| `/health` | GET | Liveness check for load balancers (`{"status": "healthy"}` envelope; no dependency checks) |
| `/health/ready` | GET | Readiness probe (spec #66): one cheap DB round-trip; `200`/`"ready"` or `503`/`"unready"` |
| `/api/status` | GET | Detailed system statistics (same data as the CLI `status` command) |
| `/api/status/mcp` | GET | Remote MCP connector info (spec #71): capability URL for claude.ai custom connectors; admin-gated |
| `/docs` | GET | OpenAPI docs — only when `ENVIRONMENT=development` or `ENABLE_DOCS=true`; disabled in production |

Any path that doesn't match an API route (including bare `/status`) falls
through to the SPA catch-all and returns `index.html`.

### Podcasts (`/api/podcasts`)

| Endpoint | Method | Description |
|----------|--------|-------------|
| `/api/podcasts` | GET | List followed podcasts (paginated; `?q=` filters by title/author) |
| `/api/podcasts/resolve` | POST | Resolve a podcast URL `{url}` to a local slug, creating the row if needed |
| `/api/podcasts/{slug}` | GET | Get podcast details |
| `/api/podcasts/{slug}/follow` | POST | Follow podcast |
| `/api/podcasts/{slug}/follow` | DELETE | Unfollow podcast |
| `/api/podcasts/{slug}/followers/count` | GET | Follower count for a podcast |

Adding a podcast and triggering a feed refresh are commands, not podcast
routes: `POST /api/commands/add` and `POST /api/commands/refresh` (see
Commands below).

### Episodes (`/api/episodes`)

| Endpoint | Method | Description |
|----------|--------|-------------|
| `/api/episodes` | GET | List episodes across all podcasts (filterable, paginated) |
| `/api/episodes/failed` | GET | List episodes with pipeline failures |
| `/api/episodes/bulk/process` | POST | Queue full pipeline processing for multiple episodes |
| `/api/podcasts/{podcast_slug}/episodes` | GET | List episodes (filterable) |
| `/api/podcasts/{podcast_slug}/episodes/{episode_slug}` | GET | Get episode details |
| `/api/podcasts/{podcast_slug}/episodes/{episode_slug}/transcript` | GET | Get transcript content |
| `/api/podcasts/{podcast_slug}/episodes/{episode_slug}/summary` | GET | Get summary content |
| `/api/podcasts/{podcast_slug}/episodes/{episode_slug}/transcript/words` | GET | Word-level transcript timings |
| `/api/episodes/{id}/failure` | GET | Get failure details |
| `/api/episodes/{id}/retry` | POST | Clear failure and retry |

### Commands (`/api/commands`)

| Endpoint | Method | Description |
|----------|--------|-------------|
| `/api/commands/refresh` | POST | Trigger feed refresh (all podcasts or one) |
| `/api/commands/refresh/status` | GET | Status of the last refresh task |
| `/api/commands/status` | GET | Status of all tracked command tasks |
| `/api/commands/add` | POST | Add new podcast `{url}` as a background task |
| `/api/commands/add/status` | GET | Status of the last add-podcast task |
| `/api/commands/download` | POST | Queue audio download for an episode |
| `/api/commands/downsample` | POST | Queue downsampling for an episode |
| `/api/commands/transcribe` | POST | Queue transcription for an episode |
| `/api/commands/clean` | POST | Queue transcript cleaning for an episode |
| `/api/commands/summarize` | POST | Queue summarization for an episode |
| `/api/commands/run-pipeline` | POST | Run full pipeline for episode |
| `/api/commands/episode/{id}/cancel-pipeline` | POST | Cancel remaining pipeline stages for an episode |
| `/api/commands/episode/{id}/tasks` | GET | List tasks for an episode |
| `/api/commands/task/{id}` | GET | Get queued task status |
| `/api/commands/task/{id}/progress` | GET | Stream task progress via Server-Sent Events |
| `/api/commands/task/{id}/progress/current` | GET | Latest progress snapshot for a task |
| `/api/commands/queue/status` | GET | Queue counts by state |
| `/api/commands/queue/tasks` | GET | List queued tasks |
| `/api/commands/queue/task/{id}/bump` | POST | Bump a queued task to the front |
| `/api/commands/queue/task/{id}/cancel` | POST | Cancel a queued task |
| `/api/commands/dlq` | GET | List dead letter queue tasks |
| `/api/commands/dlq/{task_id}/retry` | POST | Retry dead task |
| `/api/commands/dlq/{task_id}/skip` | POST | Skip/resolve dead task |
| `/api/commands/dlq/retry-all` | POST | Retry all dead tasks |

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

### Dashboard (`/api/dashboard`)

| Endpoint | Method | Description |
|----------|--------|-------------|
| `/api/dashboard/stats` | GET | Dashboard statistics |
| `/api/dashboard/activity` | GET | Recent activity feed (paginated) |
| `/api/dashboard/narration` | GET | Aggregated narration runs for the dashboard tile |

### Search (`/api/search`)

| Endpoint | Method | Description |
|----------|--------|-------------|
| `/api/search/corpus` | GET | Search the transcript corpus (`?q=`, `mode=lexical\|semantic\|hybrid`) |
| `/api/search/related` | GET | Episodes related to a source episode (`?episode_id=`) |
| `/api/search/quick` | GET | Grouped quick search across podcasts and episodes |

### Entities (`/api`)

| Endpoint | Method | Description |
|----------|--------|-------------|
| `/api/episodes/{episode_id}/entities` | GET | Entities mentioned in an episode |
| `/api/entities/{entity_type}/{id_slug}` | GET | Entity page payload — record, aggregates, recent mentions |
| `/api/entities/review-queue` | GET | Entities pending review |
| `/api/entities/corrections` | POST | Submit an entity correction |

### Narrations (`/api/narrations`)

| Endpoint | Method | Description |
|----------|--------|-------------|
| `/api/narrations/{id}` | GET | JSON script + Markdown body for a stored narration |
| `/api/narrations/{id}/script.json` | GET | Raw JSON script body for downstream TTS consumers |

### Imports (`/api/imports`)

| Endpoint | Method | Description |
|----------|--------|-------------|
| `/api/imports` | POST | Import a single episode by URL `{url}` — 201 with the episode + inbox row |

### Top Podcasts (`/api/top-podcasts`)

| Endpoint | Method | Description |
|----------|--------|-------------|
| `/api/top-podcasts` | GET | Top-podcasts chart (`?region=` ISO code, defaults to the user's region) |

### Authentication (`/api/auth`)

| Endpoint | Method | Description |
|----------|--------|-------------|
| `/api/auth/status` | GET | Get authentication mode and user info |
| `/api/auth/google/login` | GET | Initiate Google OAuth flow |
| `/api/auth/google/callback` | GET | OAuth callback handler |
| `/api/auth/logout` | POST | Clear authentication cookie |
| `/api/auth/me` | GET | Get current user info (requires auth in multi-user mode) |
| `/api/auth/me` | PATCH | Update user region |

### Webhooks

| Endpoint | Method | Description |
|----------|--------|-------------|
| `/webhook/elevenlabs/speech-to-text` | POST | Receive transcription callback (HMAC-verified, no session) |
| `/webhook/elevenlabs/results` | GET | List webhook results (admin only) |
| `/webhook/elevenlabs/results/{id}` | GET | Get specific result (admin only) |
| `/webhook/elevenlabs/results/{id}` | DELETE | Delete result (admin only) |

### Unsubscribe

Root-level, signed-token routes used by briefing-email links (no auth cookie
required).

| Endpoint | Method | Description |
|----------|--------|-------------|
| `/unsubscribe/briefings` | GET | Confirm page for a signed link (read-only; mail gateways prefetch GETs) |
| `/unsubscribe/briefings` | POST | Perform the unsubscribe (confirm button and RFC 8058 one-click) |

## Project Structure

```
thestill/web/
├── __init__.py              # Package init with create_app export
├── app.py                   # FastAPI application factory
├── dependencies.py          # Dependency injection (AppState, get_app_state)
├── responses.py             # api_response envelope helpers
├── task_manager.py          # Background task queue manager
├── background_server.py     # Background server runner
├── middleware/              # logging_middleware, security_headers, body_size, rate_limit
├── services/                # Web-layer services (webhook transcript processor)
├── routes/
│   ├── __init__.py
│   ├── health.py            # Health check endpoint
│   ├── webhooks.py          # ElevenLabs webhook handlers
│   ├── auth.py              # Authentication endpoints (OAuth, JWT)
│   ├── api_status.py        # System statistics
│   ├── api_dashboard.py     # Dashboard stats, activity, narration tile
│   ├── api_podcasts.py      # Podcast endpoints (resolve, follow, details)
│   ├── api_transcript_words.py  # Word-level transcript timings
│   ├── api_top_podcasts.py  # Top-podcasts chart
│   ├── api_episodes.py      # Episode content endpoints
│   ├── api_entities.py      # Entity mentions, review queue, corrections
│   ├── api_search.py        # Corpus, related, quick search
│   ├── api_inbox.py         # Per-user inbox
│   ├── api_briefings.py     # Briefing history, schedule, narration
│   ├── api_narrations.py    # Stored narration scripts
│   ├── api_imports.py       # Single-episode imports
│   ├── api_commands.py      # Processing commands (pipeline, queue, DLQ)
│   └── unsubscribe.py       # Signed one-click briefing-email unsubscribe
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

### Default-Deny API Access

Every `/api` router except `/api/auth` is registered in `app.py` with a
router-level `require_auth` dependency, so any new endpoint requires a
session unless it is consciously opted out. On top of that floor, the
operator-only surface requires an admin session (`require_admin`):

- Manual pipeline triggers: `POST /api/commands/{refresh,download,downsample,transcribe,clean,summarize,run-pipeline}` and `POST /api/commands/episode/{id}/cancel-pipeline`
- Queue and DLQ management: `/api/commands/queue/*`, `/api/commands/dlq*`
- Bulk processing and retries: `POST /api/episodes/bulk/process`, `POST /api/episodes/{id}/retry`
- Entity resolution surgery: `GET /api/entities/review-queue`, `POST /api/entities/corrections` (corrections change resolution state for every user)
- Operator dashboards: `GET /api/status`, `GET /api/dashboard/*` (system-wide activity, storage paths, provider config)
- Remote MCP connector info: `GET /api/status/mcp` (spec #71 — returns the capability URL, which is operator-equivalent access)
- Stored webhook payload inspection: `GET`/`DELETE` `/webhook/elevenlabs/results*`

Ordinary users never drive the pipeline manually — it runs automatically
when they follow a podcast or import an episode. In single-user mode both
checks always pass (the local user is the operator). In multi-user mode,
`require_admin` needs a user whose `is_admin` flag is set. The frontend
mirrors these gates (`AdminRoute` pages plus `isAdmin`-conditional action
buttons), and `tests/integration/web/test_default_deny_auth.py` audits the
route table so an unauthenticated endpoint cannot ship unnoticed.

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
3. After Google approval → callback to `/api/auth/google/callback`
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
