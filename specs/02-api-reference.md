# API Reference

This document describes the REST API endpoints provided by the Thestill web server.

## Overview

- **Base URL**: `http://localhost:8000` (development)
- **Authentication**: Cookie-based JWT or Bearer token (multi-user mode only)
- **Content-Type**: `application/json`

### Authorization Levels

Every `/api` route except `/api/auth` requires an authenticated session
(enforced at router registration — default-deny). Endpoints that control
the processing pipeline additionally require an admin user in multi-user
mode; in single-user mode all checks pass. Admin-only endpoints:

- `POST /api/commands/{refresh,download,downsample,transcribe,clean,summarize,run-pipeline}`
- `POST /api/commands/episode/{id}/cancel-pipeline`
- `/api/commands/queue/*` and `/api/commands/dlq*` (queue + DLQ management)
- `POST /api/episodes/bulk/process` and `POST /api/episodes/{id}/retry`
- `GET /api/episodes` (corpus-wide browser), `GET /api/episodes/failed` and `GET /api/episodes/{id}/failure` (raw failure reasons)
- `GET /api/entities/review-queue` and `POST /api/entities/corrections` (global entity-resolution surgery)
- `GET /api/status` and `GET /api/dashboard/*` (operator dashboards)
- `GET`/`DELETE` `/webhook/elevenlabs/results*` (stored payload inspection)

Episode payloads on user routes (`/api/podcasts/{slug}/episodes`,
`/api/podcasts/{slug}/episodes/{episode_slug}`) keep `is_failed`,
`failed_at_stage` and `failure_type`, but return `failure_reason: null`
to non-admins.

Deliberately public (no session): `/health`, `/unsubscribe/briefings`
(signed token), and `POST /webhook/elevenlabs/speech-to-text`
(HMAC-verified). The canonical machine-checked list lives in
`tests/integration/web/test_default_deny_auth.py`.

## Response Format

All responses follow a standard envelope format:

```json
{
  "status": "ok",
  "timestamp": "2026-01-21T12:00:00.000000+00:00",
  ...additional fields
}
```

### Paginated Responses

Paginated endpoints include:

```json
{
  "status": "ok",
  "timestamp": "...",
  "items": [...],
  "count": 10,
  "total": 100,
  "offset": 0,
  "limit": 20,
  "has_more": true,
  "next_offset": 20
}
```

### Error Responses

```json
{
  "detail": "Error message"
}
```

Common HTTP status codes:

- `400` - Bad Request (invalid parameters)
- `401` - Unauthorized (authentication required)
- `404` - Not Found
- `409` - Conflict (resource already exists or task already running)
- `500` - Internal Server Error

---

## Health Check

Infrastructure endpoint at root level (not under `/api`) for load balancers and Kubernetes probes.

### GET /health

Health check endpoint for load balancers and monitoring.

**Response:**

```json
{
  "status": "healthy",
  "timestamp": "2026-01-21T12:00:00.000000+00:00"
}
```

---

## System Status

### GET /api/status

Detailed system status with pipeline statistics and configuration.

**Response:**

```json
{
  "status": "ok",
  "timestamp": "...",
  "storage": {
    "path": "/data",
    "audio_files": 42,
    "transcripts": 35
  },
  "podcasts": {
    "tracked": 5,
    "total_episodes": 150
  },
  "pipeline": {
    "discovered": 150,
    "downloaded": 100,
    "downsampled": 90,
    "transcribed": 80,
    "cleaned": 70,
    "summarized": 60,
    "unprocessed": 90
  },
  "configuration": {
    "transcription_provider": "whisper",
    "llm_provider": "openai",
    "diarization_enabled": true
  },
  "refresh_health": {
    "active": 88,
    "due_now": 3,
    "backing_off": 2,
    "parked_total": 2,
    "parked_by_reason": {
      "feed_gone": 1,
      "auth_required": 1
    }
  }
}
```

`refresh_health` (spec #60): feed refresh scheduling health. `backing_off`
counts feeds currently failing but still scheduled (never silently parked);
`parked_by_reason` buckets quarantined feeds by `refresh_disabled_reason`
(`feed_gone` | `blocked_unsafe` | `auth_required` | `invalid_content`;
legacy pre-#60 parks appear as `unknown`).

---

## Authentication

Routes for user authentication. Behavior depends on `MULTI_USER` environment variable.

### GET /api/auth/status

Get authentication status and configuration.

**Response:**

```json
{
  "status": "ok",
  "timestamp": "...",
  "multi_user": true,
  "authenticated": true,
  "user": {
    "id": "uuid",
    "email": "user@example.com",
    "name": "User Name",
    "picture": "https://...",
    "created_at": "2026-01-01T...",
    "last_login_at": "2026-01-21T...",
    "usage_analytics_enabled": true
  },
  "usage_analytics_available": true,
  "analytics": {"key": "phc_..."},
  "account_disabled": false,
  "admin_contact_email": "ops@example.com"
}
```

`usage_analytics_available` is true when usage events are configured
(spec #95); `analytics` carries the PostHog project key only for a signed-in
user who has not opted out and whose request has no `Sec-GPC: 1`, else null.

Spec #104: for a suspended or banned account this endpoint answers
`authenticated: false`, `user: null` and `account_disabled: true` rather
than 403, so the app shows `/login`'s explanation instead of looping.
`admin_contact_email` is `ADMIN_CONTACT_EMAIL`, or null.

### Refusals (spec #104)

Policy refusals use one body shape on every route, under `detail` like any
`HTTPException`. `error` is a stable code; `message` is written for the
reader.

| Status | `error` | When |
|---|---|---|
| 403 | `account_disabled` | The session belongs to a suspended or banned account |
| 503 | `feature_paused` (with `feature`) | A runtime switch is off: `new_feeds`, `imports`, `translations`, `narration` |
| 503 | `read_only` | Panic mode: a non-admin write, or any paused feature while `read_only` is on |
| 429 | `quota_exceeded` (with `quota`, `limit`, `retry_after`) | A per-user daily quota is used up; `Retry-After` is set unless no wait lifts it (`follows_total`) |
| 429 | `rate_limited` | The per-user request budget (`RATE_LIMIT_USER_*`) is spent; `Retry-After` is the window |

```json
{"detail": {"error": "feature_paused", "feature": "new_feeds", "message": "Adding new podcasts is paused right now."}}
```

### PATCH /api/auth/me

Update the current user. Only the fields present in the body change.

| Field | Type | Description |
|---|---|---|
| region | string \| null | ISO 3166-1 alpha-2 code; setting it (even to null) locks IP inference |
| usage_analytics_enabled | bool | Spec #95: the Settings switch for usage events |

### GET /api/auth/google/login

Initiate Google OAuth login flow. Redirects to Google's authorization page.

**Availability:** Multi-user mode only

**Response:** `302 Redirect` to Google OAuth

### GET /api/auth/google/callback

Handle Google OAuth callback. Sets auth cookie and redirects to home.

**Query Parameters:**

| Parameter | Type   | Description                    |
|-----------|--------|--------------------------------|
| code      | string | Authorization code from Google |
| state     | string | CSRF protection state token    |

**Response:** `302 Redirect` to `/` with `auth_token` cookie set. A
suspended or banned account redirects to `/login?error=account_disabled`, and
a new identity the signup gate refuses (spec #104) to
`/login?error=signups_closed`, both without a cookie.

### POST /api/auth/logout

Log out the current user. Clears the authentication cookie.

**Response:**

```json
{
  "status": "ok",
  "timestamp": "...",
  "message": "Logged out successfully"
}
```

### GET /api/auth/me

Get the current authenticated user.

**Response:**

```json
{
  "status": "ok",
  "timestamp": "...",
  "user": {
    "id": "uuid",
    "email": "user@example.com",
    "name": "User Name",
    "picture": "https://...",
    "created_at": "2026-01-01T...",
    "last_login_at": "2026-01-21T..."
  }
}
```

**Errors:**

- `401` - Not authenticated (multi-user mode)

---

## Dashboard

### GET /api/dashboard/stats

Get dashboard statistics including podcast counts, episode processing states, and storage info.

**Response:**

```json
{
  "status": "ok",
  "timestamp": "...",
  "podcasts_tracked": 5,
  "episodes_total": 150,
  "episodes_processed": 60,
  "episodes_pending": 90,
  "storage_path": "/data",
  "audio_files_count": 42,
  "transcripts_available": 35,
  "pipeline": {
    "discovered": 150,
    "downloaded": 100,
    "downsampled": 90,
    "transcribed": 80,
    "cleaned": 70,
    "summarized": 60
  }
}
```

### GET /api/dashboard/activity

Get recent processing activity with pagination.

**Query Parameters:**

| Parameter | Type | Default | Description                |
|-----------|------|---------|----------------------------|
| limit     | int  | 10      | Max items to return        |
| offset    | int  | 0       | Items to skip for pagination |

**Response:**

```json
{
  "status": "ok",
  "timestamp": "...",
  "items": [
    {
      "episode_id": "uuid",
      "episode_title": "Episode Title",
      "episode_slug": "episode-title",
      "podcast_title": "Podcast Name",
      "podcast_id": "uuid",
      "podcast_slug": "podcast-name",
      "action": "summarized",
      "timestamp": "2026-01-21T...",
      "pub_date": "2026-01-20T...",
      "duration": 3600,
      "duration_formatted": "1h 0m",
      "episode_image_url": "https://...",
      "podcast_image_url": "https://..."
    }
  ],
  "count": 10,
  "total": 150,
  "offset": 0,
  "limit": 10,
  "has_more": true,
  "next_offset": 10
}
```

### GET /api/dashboard/analytics

Spec #95 Layer A product analytics, read from product tables (no event
collection). Admin accounts and the single-user `local` account are
excluded. Each activation step counts only users who signed up at least
that step's horizon ago. Cached in-process for 10 minutes per range.

**Query Parameters:**

| Parameter | Type   | Default | Description                     |
|-----------|--------|---------|---------------------------------|
| range     | string | 28d     | `7d`, `28d` or `90d` (else 422) |

**Response** (abridged):

```json
{
  "status": "ok",
  "timestamp": "...",
  "available": true,
  "range": "28d",
  "window": {"start": "2026-09-17T00:00:00+00:00", "end": "...", "days": 28},
  "signups": {"total": 12, "by_source": {"episode": 9, "before_tracking": 3}, "days": [...]},
  "activation": {
    "overall": {"source": "all", "signups": 12, "steps": [
      {"key": "follow_24h", "horizon_hours": 24, "eligible": 10, "reached": 4, "pct": 0.4, "median_hours": 2.0}
    ]},
    "by_source": [...]
  },
  "cohorts": {"weeks": [1, 2, 4, 8], "rows": [{"week_start": "2026-08-03", "size": 7, "cells": [...]}]},
  "engagement": {"active_users": 6, "reads": 30, "opened_ratio": 0.75, ...},
  "content": {
    "podcasts": [
      {"podcast_id": "...", "slug": "hard-fork", "title": "Hard Fork", "category": "Technology",
       "followers": 12, "new_follows": 3, "follower_readers": 6, "readers": 9, "readers_previous": 7,
       "listeners": 4, "completions": 2, "public_views": 31, "change": "+2"}
    ],
    "episodes": [
      {"episode_id": "...", "slug": "...", "title": "...", "podcast_slug": "hard-fork", "podcast_title": "Hard Fork",
       "published_at": "2026-10-10T00:00:00Z", "imported": false, "readers": 6, "listeners": 3, "completions": 1, "public_views": 12}
    ],
    "categories": [
      {"category": "Technology", "shows": 4, "shows_read": 3, "followers": 15, "readers": 10,
       "readers_previous": 12, "public_views": 44, "change": "−2"}
    ],
    "notes": ["..."]
  },
  "notes": ["..."]
}
```

`content` (spec #103) says what readers read. A reader opened an episode
(an inbox read) or listened to it in the window. `readers_previous` covers
the window of the same elapsed length before it, and `change` is the
server's label for the difference: `+n`, `−n`, `0`, `new` or `—`.
`podcasts` is the union of the top 20 by readers and the top 20 by
followers, so either sort is an exact top 20; synthetic import parents are
left out. `episodes` is the top 20 by readers. `categories` rolls each show
up to the top level of its primary category, plus "Uncategorised".
`public_views` counts signed-out views in the window: a show's own page
plus its episode pages, an episode's page, and a category's shows. Every
figure is an aggregate; no row carries user ids.

In single-user mode or on the SQLite backend the response is
`{"status": "ok", "timestamp": "...", "available": false}` with status 200.

---

## Podcasts

### GET /api/podcasts

Get tracked podcasts with pagination.

**Query Parameters:**

| Parameter | Type | Default | Description                                             |
|-----------|------|---------|---------------------------------------------------------|
| limit     | int  | 12      | Max items to return                                     |
| offset    | int  | 0       | Items to skip                                           |
| q         | str  | —       | Case-insensitive substring filter on title or author. Applied before pagination, so `total` reflects the filtered set. Blank/whitespace values are ignored. |

**Response:**

```json
{
  "status": "ok",
  "timestamp": "...",
  "podcasts": [
    {
      "id": "uuid",
      "title": "Podcast Name",
      "description": "...",
      "rss_url": "https://...",
      "slug": "podcast-name",
      "image_url": "https://...",
      "episodes": []
    }
  ],
  "count": 5,
  "total": 5,
  "offset": 0,
  "limit": 12,
  "has_more": false,
  "next_offset": null
}
```

### GET /api/podcasts/{podcast_slug}

Get a specific podcast by slug.

**Path Parameters:**

| Parameter    | Type   | Description          |
|--------------|--------|----------------------|
| podcast_slug | string | URL-safe podcast ID  |

**Response:**

```json
{
  "status": "ok",
  "timestamp": "...",
  "podcast": {
    "id": "uuid",
    "index": 1,
    "title": "Podcast Name",
    "description": "...",
    "rss_url": "https://...",
    "slug": "podcast-name",
    "image_url": "https://...",
    "last_processed": "2026-01-21T...",
    "episodes_count": 50,
    "episodes_processed": 30
  }
}
```

### GET /api/podcasts/{podcast_slug}/episodes

Get episodes for a specific podcast with pagination.

**Path Parameters:**

| Parameter    | Type   | Description         |
|--------------|--------|---------------------|
| podcast_slug | string | URL-safe podcast ID |

**Query Parameters:**

| Parameter   | Type | Default | Description                           |
|-------------|------|---------|---------------------------------------|
| limit       | int  | 20      | Max items to return                   |
| offset      | int  | 0       | Items to skip                         |
| since_hours | int  | null    | Only episodes published in last N hours |

**Response:**

```json
{
  "status": "ok",
  "timestamp": "...",
  "episodes": [
    {
      "id": "uuid",
      "title": "Episode Title",
      "description": "...",
      "slug": "episode-title",
      "pub_date": "2026-01-20T...",
      "audio_url": "https://...",
      "duration": 3600,
      "state": "summarized",
      "image_url": "https://..."
    }
  ],
  "count": 20,
  "total": 50,
  "offset": 0,
  "limit": 20,
  "has_more": true,
  "next_offset": 20
}
```

### GET /api/podcasts/{podcast_slug}/episodes/{episode_slug}

Get a specific episode by podcast and episode slugs.

**Path Parameters:**

| Parameter    | Type   | Description          |
|--------------|--------|----------------------|
| podcast_slug | string | URL-safe podcast ID  |
| episode_slug | string | URL-safe episode ID  |

**Response:**

```json
{
  "status": "ok",
  "timestamp": "...",
  "episode": {
    "id": "uuid",
    "podcast_id": "uuid",
    "podcast_slug": "podcast-name",
    "podcast_title": "Podcast Name",
    "podcast_author": "Podcast Author",
    "podcast_language": "en",
    "origin": "feed",
    "import_kind": null,
    "platform_links": [{"platform": "apple", "url": "https://podcasts.apple.com/us/podcast/show/id123?i=456"}],
    "title": "Episode Title",
    "description": "Plain text description...",
    "description_html": "<p>HTML description with <a href=\"...\">links</a>...</p>",
    "slug": "episode-title",
    "pub_date": "2026-01-20T...",
    "audio_url": "https://...",
    "duration": 3600,
    "duration_formatted": "1h 0m",
    "external_id": "...",
    "state": "summarized",
    "has_transcript": true,
    "has_summary": true,
    "image_url": "https://...",
    "podcast_image_url": "https://...",
    "is_failed": false,
    "failed_at_stage": null,
    "failure_reason": null,
    "failure_type": null,
    "failed_at": null
  },
  "listening": {
    "position_seconds": 1934.2,
    "duration_seconds": 7412.0,
    "completed_at": null,
    "updated_at": "2026-10-16T09:00:00Z"
  }
}
```

**Listening (spec #90):**

- `listening`: the caller's progress on the episode, or `null` when never played. `position_seconds` is on the logical (transcript) timeline; `duration_seconds` is the player-reported duration; `completed_at` is set once finished or marked played (the position is then 0). The same object rides on each `GET /api/inbox` item. Writes go through [`/api/me/listening`](#listening-progress).

**Provenance Fields (spec #76 §3.6):**

- `podcast_author`, `podcast_language`: show facts read from the podcast row (no extra request).
- `origin`: `feed` for episodes discovered from a followed feed, `import` for spec #31 imports.
- `import_kind`: `bare_audio`, `youtube`, `apple_episode`, `spotify_episode` or `rss_episode` for imports; `null` for feed episodes or an unrecognised import source.

**Platform Links (spec #87):**

- `platform_links`: this episode's page on other listening platforms, `[{platform, url}]` with `platform` one of `apple`, `spotify`, `youtube`, ordered by platform. Only resolved links appear; an episode with none gets `[]`. Apple comes from the iTunes lookup window, Spotify only from links the publisher put in the feed, YouTube from publisher links or the show's channel listing.

**Description Fields:**

- `description` - Plain text version (links stripped). Use for LLM prompts, CLI, text processing.
- `description_html` - HTML version with `<a>` tags preserved. Use for web UI display with clickable links. May be empty if RSS feed doesn't provide HTML content.

### GET /api/podcasts/{podcast_slug}/episodes/{episode_slug}/transcript

Get the transcript for an episode. Returns cleaned transcript if available, otherwise raw.

**Response:**

```json
{
  "status": "ok",
  "timestamp": "...",
  "episode_id": "uuid",
  "episode_title": "Episode Title",
  "content": "# Transcript\n\n...",
  "available": true,
  "transcript_type": "cleaned"
}
```

### GET /api/podcasts/{podcast_slug}/episodes/{episode_slug}/summary

Get the summary for an episode.

**Response:**

```json
{
  "status": "ok",
  "timestamp": "...",
  "episode_id": "uuid",
  "episode_title": "Episode Title",
  "content": "# Summary\n\n...",
  "available": true
}
```

---

## Episodes

Cross-podcast episode operations.

### GET /api/episodes

Get episodes across all podcasts with filtering and pagination.

**Query Parameters:**

| Parameter    | Type   | Default   | Description                                    |
|--------------|--------|-----------|------------------------------------------------|
| limit        | int    | 20        | Max items to return                            |
| offset       | int    | 0         | Items to skip                                  |
| search       | string | null      | Case-insensitive title search                  |
| podcast_slug | string | null      | Filter by podcast                              |
| state        | string | null      | Filter by processing state                     |
| date_from    | string | null      | ISO date - episodes published on/after         |
| date_to      | string | null      | ISO date - episodes published on/before        |
| sort_by      | string | pub_date  | Sort field: pub_date, title, updated_at        |
| sort_order   | string | desc      | Sort direction: asc, desc                      |

**Response:**

```json
{
  "status": "ok",
  "timestamp": "...",
  "episodes": [
    {
      "id": "uuid",
      "podcast_id": "uuid",
      "podcast_slug": "podcast-name",
      "podcast_title": "Podcast Name",
      "podcast_image_url": "https://...",
      "image_url": "https://...",
      "title": "Episode Title",
      "slug": "episode-title",
      "description": "Plain text description...",
      "description_html": "<p>HTML description with <a href=\"...\">links</a>...</p>",
      "pub_date": "2026-01-20T...",
      "audio_url": "https://...",
      "duration": 3600,
      "duration_formatted": "1h 0m",
      "external_id": "...",
      "state": "summarized",
      "transcript_available": true,
      "summary_available": true,
      "summary_preview": "Brief summary preview..."
    }
  ],
  "count": 20,
  "total": 150,
  "offset": 0,
  "limit": 20,
  "has_more": true,
  "next_offset": 20
}
```

**Description Fields:**

- `description` - Plain text version. Use for LLM prompts, CLI, text processing.
- `description_html` - HTML version with links preserved. Use for web UI display.

### POST /api/episodes/bulk/process

Queue full pipeline processing for multiple episodes.

**Request Body:**

```json
{
  "episode_ids": ["uuid1", "uuid2", "uuid3"]
}
```

**Response:**

```json
{
  "status": "ok",
  "queued": 3,
  "skipped": 0,
  "tasks": [
    {
      "episode_id": "uuid1",
      "task_id": "task-uuid",
      "stage": "download"
    }
  ]
}
```

### GET /api/episodes/failed

List all episodes in failed state.

**Query Parameters:**

| Parameter | Type | Default | Description         |
|-----------|------|---------|---------------------|
| limit     | int  | 100     | Max items to return |

**Response:**

```json
{
  "status": "ok",
  "episodes": [
    {
      "status": "ok",
      "episode_id": "uuid",
      "episode_title": "Episode Title",
      "episode_slug": "episode-title",
      "podcast_title": "Podcast Name",
      "podcast_slug": "podcast-name",
      "is_failed": true,
      "failed_at_stage": "transcribe",
      "failure_reason": "Out of memory",
      "failure_type": "transient",
      "failed_at": "2026-01-21T...",
      "last_successful_state": "downsampled",
      "can_retry": true
    }
  ],
  "count": 1
}
```

### GET /api/episodes/{episode_id}/failure

Get failure details for a specific episode.

**Response:** Same structure as individual item in `/api/episodes/failed`

### POST /api/episodes/{episode_id}/retry

Retry a failed episode. Clears failure state and queues new task.

**Response:**

```json
{
  "status": "ok",
  "message": "Episode queued for retry at transcribe stage",
  "episode_id": "uuid",
  "task_id": "task-uuid",
  "stage": "transcribe"
}
```

---

## Commands

Pipeline commands and task management.

### POST /api/commands/refresh

Refresh podcast feeds and discover new episodes. Runs in background.

**Request Body:**

```json
{
  "podcast_id": "optional-uuid",
  "max_episodes": 10,
  "dry_run": false
}
```

**Response:**

```json
{
  "status": "started",
  "message": "Refresh task started. Use GET /api/commands/refresh/status to check progress.",
  "task_type": "refresh"
}
```

**Errors:**

- `409` - Refresh already in progress

### GET /api/commands/refresh/status

Get status of current or last refresh task.

**Response:**

```json
{
  "task_type": "refresh",
  "status": "completed",
  "started_at": "2026-01-21T12:00:00...",
  "completed_at": "2026-01-21T12:01:00...",
  "progress": 100,
  "message": "Discovered 15 new episode(s)",
  "result": {
    "total_episodes": 15,
    "podcasts_refreshed": 3,
    "dry_run": false,
    "episodes_by_podcast": [
      {"podcast": "Podcast 1", "new_episodes": 5},
      {"podcast": "Podcast 2", "new_episodes": 10}
    ]
  },
  "error": null
}
```

### POST /api/commands/add

Add a new podcast to tracking.

**Request Body:**

```json
{
  "url": "https://example.com/rss.xml"
}
```

**Response:**

```json
{
  "status": "started",
  "message": "Add podcast task started. Use GET /api/commands/add/status to check progress.",
  "task_type": "add_podcast"
}
```

### GET /api/commands/add/status

Get status of current or last add podcast task.

### GET /api/commands/status

Get status of all tracked command tasks.

**Response:**

```json
{
  "tasks": {
    "refresh": {...},
    "add_podcast": {...}
  }
}
```

### Pipeline Stage Commands

Queue individual pipeline stages for an episode.

#### POST /api/commands/download

Queue download task. Episode must be in `discovered` state.

#### POST /api/commands/downsample

Queue downsample task. Episode must be in `downloaded` state.

#### POST /api/commands/transcribe

Queue transcription task. Episode must be in `downsampled` state.

#### POST /api/commands/clean

Queue transcript cleaning task. Episode must be in `transcribed` state.

#### POST /api/commands/summarize

Queue summarization task. Episode must be in `cleaned` state.

**Request Body (all stage commands):**

```json
{
  "podcast_slug": "podcast-name",
  "episode_slug": "episode-title"
}
```

**Response:**

```json
{
  "task_id": "task-uuid",
  "status": "queued",
  "message": "Download queued for Episode Title",
  "stage": "download",
  "episode_id": "uuid",
  "episode_title": "Episode Title"
}
```

**Errors:**

- `404` - Episode not found
- `400` - Episode not in correct state
- `409` - Task already queued

### POST /api/commands/run-pipeline

Run full pipeline from current state to target state.

**Request Body:**

```json
{
  "podcast_slug": "podcast-name",
  "episode_slug": "episode-title",
  "target_state": "summarized"
}
```

**Response:**

```json
{
  "task_id": "task-uuid",
  "status": "queued",
  "message": "Pipeline started for Episode Title: download → summarized",
  "starting_stage": "download",
  "target_state": "summarized",
  "episode_id": "uuid",
  "episode_title": "Episode Title"
}
```

### POST /api/commands/episode/{episode_id}/cancel-pipeline

Cancel all pending pipeline tasks for an episode.

**Response:**

```json
{
  "status": "ok",
  "message": "Cancelled 2 pending task(s) for Episode Title",
  "episode_id": "uuid",
  "cancelled_tasks": 2
}
```

### GET /api/commands/task/{task_id}

Get status of a queued task.

**Response:**

```json
{
  "task_id": "uuid",
  "episode_id": "uuid",
  "stage": "transcribe",
  "status": "processing",
  "error_message": null,
  "created_at": "2026-01-21T...",
  "updated_at": "2026-01-21T...",
  "started_at": "2026-01-21T...",
  "completed_at": null
}
```

### GET /api/commands/task/{task_id}/progress

Stream real-time progress updates via Server-Sent Events (SSE).

**Response:** `text/event-stream`

```
data: {"stage": "transcribing", "progress_pct": 45, "message": "Processing audio..."}

data: {"stage": "diarizing", "progress_pct": 75, "message": "Identifying speakers..."}

data: {"stage": "completed", "progress_pct": 100, "message": "Done"}
```

### GET /api/commands/task/{task_id}/progress/current

Get current progress (non-streaming fallback).

**Response:**

```json
{
  "task_id": "uuid",
  "has_progress": true,
  "stage": "transcribing",
  "progress_pct": 45,
  "message": "Processing audio...",
  "estimated_remaining_seconds": 120
}
```

### GET /api/commands/queue/status

Get overall queue and worker status.

**Response:**

```json
{
  "pending_count": 5,
  "worker_running": true,
  "current_task": {
    "id": "uuid",
    "episode_id": "uuid",
    "stage": "transcribe",
    "status": "processing"
  },
  "stats": {
    "pending": 5,
    "processing": 1,
    "completed": 100,
    "failed": 2,
    "dead": 0
  }
}
```

### GET /api/commands/episode/{episode_id}/tasks

Get all tasks for a specific episode.

**Response:**

```json
{
  "episode_id": "uuid",
  "tasks": [
    {
      "id": "uuid",
      "episode_id": "uuid",
      "stage": "download",
      "status": "completed",
      "created_at": "...",
      "completed_at": "..."
    }
  ]
}
```

### Dead Letter Queue (DLQ)

Tasks that failed with fatal errors.

#### GET /api/commands/dlq

List tasks in the Dead Letter Queue.

**Response:**

```json
{
  "status": "ok",
  "tasks": [
    {
      "task_id": "uuid",
      "episode_id": "uuid",
      "episode_title": "Episode Title",
      "episode_slug": "episode-title",
      "podcast_title": "Podcast Name",
      "podcast_slug": "podcast-name",
      "stage": "transcribe",
      "error_message": "Fatal error message",
      "error_type": "fatal",
      "retry_count": 3,
      "max_retries": 3,
      "created_at": "...",
      "completed_at": "..."
    }
  ],
  "count": 1
}
```

#### POST /api/commands/dlq/{task_id}/retry

Retry a task from the DLQ. Moves task back to pending and clears episode failure state.

**Response:**

```json
{
  "status": "ok",
  "message": "Task moved back to pending queue",
  "task_id": "uuid",
  "new_status": "pending"
}
```

#### POST /api/commands/dlq/{task_id}/skip

Skip (resolve) a task from the DLQ without processing.

**Response:**

```json
{
  "status": "ok",
  "message": "Task marked as skipped/resolved",
  "task_id": "uuid",
  "new_status": "completed"
}
```

#### POST /api/commands/dlq/retry-all

Bulk retry multiple tasks from the DLQ.

**Request Body:**

```json
{
  "task_ids": ["uuid1", "uuid2"]
}
```

Or omit `task_ids` to retry all dead tasks.

**Response:**

```json
{
  "status": "ok",
  "retried": 2,
  "skipped": 0,
  "task_ids": ["uuid1", "uuid2"]
}
```

---

## Listening Progress

Per-user listening positions (spec #90). Every route acts on the authenticated user (the default user in single-user mode). Positions are on the logical (transcript) timeline.

### PUT /api/me/listening/{episode_id}

Record a position. Writes from one tab are ordered by `session_id` + `seq`, never by client clocks: a write is accepted when its `session_id` differs from the stored one or its `seq` is higher. `mark_played` / `mark_unplayed` always win and ignore `position_seconds`.

**Request:**

```json
{
  "event": "tick",
  "position_seconds": 1934.2,
  "duration_seconds": 7412.0,
  "session_id": "b1f0c6…",
  "seq": 41
}
```

`event` is one of `tick`, `pause`, `seek`, `ended`, `stop`, `mark_played`, `mark_unplayed`. `ended`, `mark_played`, or a `pause` / `stop` with no more than 45 s of a known duration left, completes the episode: the position resets to 0 and an unread inbox row becomes read. `stop` hides the episode from `latest` until its next write. A position beyond `duration_seconds + 5` is clamped.

`tick`, `pause`, `ended` and a completing `stop` also set `listened_at` (spec #103), which only analytics reads and no response carries. It is left unchanged for a caller who turned usage analytics off or sent `Sec-GPC: 1`.

**Response:**

```json
{
  "status": "ok",
  "timestamp": "...",
  "accepted": true,
  "completed": false,
  "listening": {"position_seconds": 1934.2, "duration_seconds": 7412.0, "completed_at": null, "updated_at": "..."}
}
```

`accepted: false` means an older write from the same tab was ignored; `listening` is then the stored, newer row. 404 for an unknown episode, 400 for a non-finite or negative position or a bad `session_id`.

### GET /api/me/listening/{episode_id}

`{"item": {position_seconds, duration_seconds, completed_at, updated_at}}`, or `{"item": null}` when never played.

### GET /api/me/listening/latest

The restore candidate: the most recently updated unfinished episode with a position and no Stop since, however old. `{"item": {"listening": {…}, "episode": {…}}}` where `episode` carries `id`, `slug`, `title`, `podcast_id`, `podcast_slug`, `podcast_title`, `audio_url`, `image_url`, `podcast_image_url`, `duration` and the `playback` manifest; `{"item": null}` when there is none.

### GET /api/me/listening

**Query Parameters:** `state=in_progress` (the only value), `limit` (1–20, default 3).

Unfinished episodes with a position written in the last 3 days, newest first, in the same `{listening, episode}` shape as `latest`. Stop does not remove an episode from this list; an older one drops out but keeps its position.

## Webhooks

External service callbacks.

### POST /webhook/elevenlabs/speech-to-text

Receive ElevenLabs speech-to-text transcription results.

**Headers:**

| Header              | Description                           |
|---------------------|---------------------------------------|
| ElevenLabs-Signature | HMAC-SHA256 signature for verification |

**Security:**

1. HMAC signature verification (if `ELEVENLABS_WEBHOOK_SECRET` is set)
2. Metadata validation (if `ELEVENLABS_WEBHOOK_REQUIRE_METADATA` is true)

**Response:**

```json
{
  "status": "processed",
  "transcription_id": "elevenlabs-id",
  "episode_id": "uuid",
  "saved_to": "elevenlabs_xxx.json",
  "transcript_path": "raw_transcripts/..."
}
```

### GET /webhook/elevenlabs/results

List all received webhook results.

**Response:**

```json
{
  "results": [
    {
      "transcription_id": "xxx",
      "received_at": "2026-01-21T...",
      "has_text": true,
      "language": "en",
      "metadata": {"episode_id": "uuid"}
    }
  ],
  "count": 5
}
```

### GET /webhook/elevenlabs/results/{transcription_id}

Get a specific webhook result by transcription ID.

### DELETE /webhook/elevenlabs/results/{transcription_id}

Delete a webhook result.

---

## Episode States

Episodes progress through these states:

| State       | Description                    |
|-------------|--------------------------------|
| discovered  | Found in feed, not downloaded  |
| downloaded  | Audio file downloaded          |
| downsampled | Converted to 16kHz WAV         |
| transcribed | Raw transcript generated       |
| cleaned     | Transcript cleaned by LLM      |
| summarized  | Summary generated (final)      |

## Task Stages

Pipeline tasks correspond to state transitions:

| Stage      | From State  | To State    |
|------------|-------------|-------------|
| download   | discovered  | downloaded  |
| downsample | downloaded  | downsampled |
| transcribe | downsampled | transcribed |
| clean      | transcribed | cleaned     |
| summarize  | cleaned     | summarized  |

## Task Statuses

| Status          | Description                                |
|-----------------|--------------------------------------------|
| pending         | Waiting to be processed                    |
| processing      | Currently being executed                   |
| completed       | Successfully finished                      |
| failed          | Failed (may retry automatically)           |
| retry_scheduled | Waiting for automatic retry                |
| dead            | Failed fatally, requires manual intervention |
