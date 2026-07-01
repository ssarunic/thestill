# Import Arbitrary Episodes Specification

> **Status:** 🚧 Active development (2026-07-01 — corrected: `services/import_service.py` (`import_url`) shipped)
> **Created:** 2026-05-06
> **Updated:** 2026-05-08
> **Author:** Product & Engineering
> **Related:** [#29 per-user-inbox-fanout](29-per-user-inbox-fanout.md), [#32 episodes-as-first-class](32-episodes-as-first-class.md)

---

## Executive Summary

Let users paste a URL — RSS episode, Apple Podcasts share link, YouTube video, or bare audio file — and have it land in their inbox **immediately**, with the pipeline running in the background. The user does not need to follow the source podcast; **adding a podcast to the system is decoupled from following it**. When a parent podcast can be deduced from the URL (RSS, Apple, YouTube channel), it is upserted into the regular `podcasts` table without subscribing the user. Bare audio URLs with no deducible parent fall back to a single synthetic `audio-imports` parent.

**Mental model:** Personal transcription queue layered onto the podcast aggregator. The pipeline is reused as-is; the new surface is URL parsing, parent deduction, and a live "processing" state on the inbox row.

**Key principle:** Don't introduce a new pipeline. Imports become regular `episodes` rows — under a real parent podcast where deducible, otherwise under a single synthetic fallback — and run through the existing `transcribe → clean → summarize → entity branch` chain. The only new code is URL resolution, optional parent upsert, the bare-audio fallback parent, and the inbox row that appears before processing finishes.

---

## Table of Contents

1. [Motivation](#motivation)
2. [Product Requirements](#product-requirements)
3. [Architecture Overview](#architecture-overview)
4. [URL Resolvers](#url-resolvers)
5. [Synthetic Parents](#synthetic-parents)
6. [Database Schema Changes](#database-schema-changes)
7. [Service Layer](#service-layer)
8. [Pipeline Integration](#pipeline-integration)
9. [API Changes](#api-changes)
10. [Frontend UX](#frontend-ux)
11. [Migration Strategy](#migration-strategy)
12. [Out of Scope (deferred to #32)](#out-of-scope-deferred-to-32)
13. [Resolved Decisions](#resolved-decisions)
14. [Implementation Phases](#implementation-phases)

---

## Motivation

Two adjacent product needs that don't fit the current "follow a podcast → wait for episodes" loop:

1. **Friend-shared episodes.** Someone DMs a link to a single episode of a podcast you don't follow. Today the only path is: subscribe to the whole show → wait for it to refresh → find that episode in the list. High friction for a one-shot interest.
2. **Stumbled-on YouTube content.** A long-form interview or talk on YouTube that the user wants to read as text. Today the user has no path inside thestill at all — they go elsewhere.

Both collapse into the same primitive: **"add this URL to my inbox; transcribe it on my behalf."** The pipeline is already URL-driven (yt-dlp handles YouTube; the RSS path takes audio URLs). What's missing is the surface to invoke it on a single arbitrary URL.

This is also a **product positioning move**: thestill stops being just a podcast aggregator and becomes a personal transcription queue that *also* handles podcast subscriptions. Worth recognising explicitly.

### Why not deferred to #32?

Spec #32 (episodes-as-first-class) is the cleaner long-term model. But it's a bigger refactor (canonical episode identity, membership table, URL design changes). #31 delivers the user-visible feature now using only additive synthetic podcasts. When #32 lands, imports cleanly migrate by attaching a "user-imports" membership instead of a synthetic podcast.

---

## Product Requirements

### User Stories

| As a... | I want to... | So that... |
|---------|--------------|------------|
| User | Paste a YouTube URL and get a transcript | I can read long-form video content |
| User | Paste an RSS episode link without subscribing | I can consume one-off recommendations |
| User | See the import appear in my inbox immediately | The system feels responsive even before transcription finishes |
| User | See live progress (downloading → transcribing → ready) | I know whether to wait or come back later |
| User | Have the import behave identically to a delivered episode once ready | Read/save/dismiss/play all work the same |
| User | Re-paste the same URL without creating a duplicate | I get the existing row, not a new one |
| User | Have my import be private to me (no one else sees it in their inbox) | Imports don't pollute other users' feeds |

### Core Behaviors

1. **URL recognition.** The import endpoint accepts:
   - YouTube watch / shorts / playlist-item URLs
   - RSS `<enclosure>` audio URLs (.mp3, .m4a, .opus, .ogg, .wav)
   - Direct audio file URLs not from an RSS feed
   - Apple Podcasts share links (resolved to underlying RSS feed; ships as a follow-up resolver, not v1)
   - **Out of scope:** Spotify / Pocket Casts share links (Spotify exclusives have no audio enclosure; defer or reject).
2. **Idempotency on canonical id.** Each resolver produces a typed canonical id (`youtube:<video_id>`, `audio:<sha256_url>`, `rss:<guid>`). Re-importing the same URL returns the existing episode row and adds the user's inbox row if they didn't already have one.
3. **Inbox-first UX.** The inbox row is created **before** processing starts so the user sees the import immediately with a `processing` state. The row updates live as pipeline stages complete.
4. **Ad-hoc and Import are different sources.**
   - `user_episode_inbox.source='ad_hoc'` — added from an episode already in the system (the (3.1) collapse from earlier discussions).
   - `user_episode_inbox.source='import'` — added from an external URL we hadn't seen before.
5. **No follow side-effect.** Importing does not subscribe the user to the source. Future episodes from that channel/feed do NOT auto-arrive. Adding a podcast to the `podcasts` table (when the URL has a deducible parent) is decoupled from following it — the user's `follows` relation is not touched.
6. **Shared episode rows.** Two users importing the same URL share the same `episodes` row (one Whisper run); each user gets their own inbox row pointing at it. Episodes are not sensitive — the user pasted a public URL.
7. **Quotas (multi-user only).** A future hosted-multi-user mode needs per-user import quotas (e.g. N imports per day) to prevent runaway costs. Self-hosted single-user mode has no quota.

### Non-Goals

- Cross-user sharing of imported content beyond the dedup behaviour above.
- Importing a whole channel / feed (that's a follow, which is the existing flow).
- Resolving Apple/Spotify/Pocket Casts share links.
- Background re-fetching of imported content (imports are one-shot).
- Editing the title or description of an imported episode (always derived from the source).

---

## Architecture Overview

### Layered View

```
┌────────────────────────────────────────────────────────────┐
│  Web Frontend                                              │
│    - "Add episode" / "Import URL" entry point in nav       │
│    - Inbox row renders processing state for in-flight      │
└────────────────────────────────────────────────────────────┘
                            │
┌────────────────────────────────────────────────────────────┐
│  Web Routes (FastAPI)                                      │
│    POST /api/imports {url}                                 │
│      → 201 with {episode_id, status, inbox_row}            │
│    GET  /api/imports/{episode_id}/status (optional)        │
└────────────────────────────────────────────────────────────┘
                            │
┌────────────────────────────────────────────────────────────┐
│  Services                                                  │
│    ImportService                                           │
│      .import_url(user_id, url) → ImportResult              │
│        1. Resolve URL → CanonicalSource                    │
│        2. Find-or-create synthetic Podcast                 │
│        3. Find-or-create Episode (idempotent on            │
│           canonical_id)                                    │
│        4. Find-or-create inbox row for this user           │
│        5. Enqueue first pipeline stage if episode is fresh │
│      .resolve_url(url) → CanonicalSource (pluggable)       │
└────────────────────────────────────────────────────────────┘
                            │
┌────────────────────────────────────────────────────────────┐
│  URL Resolvers (pluggable, one per source kind)            │
│    YouTubeResolver — yt-dlp metadata, builds Episode shell │
│    RssEnclosureResolver — fetches enclosure metadata       │
│    BareAudioResolver — heads the URL, sniffs duration      │
└────────────────────────────────────────────────────────────┘
                            │
┌────────────────────────────────────────────────────────────┐
│  Pipeline (existing — no changes to handlers)              │
│    transcribe → clean → summarize → entity branch          │
└────────────────────────────────────────────────────────────┘
```

### Data Flow on Import

```
User pastes URL
   │
   ▼
POST /api/imports {url}
   │
   ▼
ImportService.import_url(user_id, url)
   │
   ├── resolve_url(url) → CanonicalSource(kind, canonical_id, ..., parent?)
   │
   ├── if canonical.parent:
   │      upsert_auto_added_podcast(parent)   # real podcast row, auto_added=1, no follow
   │   else:
   │      ensure_synthetic_audio_imports_parent()
   │
   ├── find_or_create_episode(canonical_id, ...) →
   │      • exists? return existing
   │      • new? insert with state='discovered', enqueue DOWNLOAD task
   │
   ├── find_or_create_inbox_row(user_id, episode_id, source='import')
   │
   ▼
201 {episode_id, status: 'processing'|'ready', inbox_row}
   │
   ▼
Pipeline runs in background
Inbox row's "processing" state derived from episode.state + entity_extraction_status
```

---

## URL Resolvers

A `Resolver` is a small class with two methods:

```python
class Resolver(Protocol):
    def matches(self, url: str) -> bool: ...
    def resolve(self, url: str) -> CanonicalSource: ...
```

`CanonicalSource` carries everything we need to mint an Episode and (when deducible) its parent Podcast:

```python
@dataclass
class CanonicalParent:
    """A real parent podcast deduced from the URL. None for bare audio."""
    external_id: str              # YouTube channel_id, RSS feed URL, Apple show id
    rss_url: str                  # feed URL the refresh loop will use IF followed
    title: str                    # channel / show name
    image_url: Optional[str]

@dataclass
class CanonicalSource:
    kind: Literal["youtube", "rss_episode", "bare_audio"]
    canonical_id: str             # e.g. "youtube:dQw4w9WgXcQ"
    audio_url: str                # what yt-dlp / downloader fetches
    title: str
    description: Optional[str]
    duration_seconds: Optional[int]
    pub_date: Optional[datetime]
    image_url: Optional[str]
    source_handle: str            # YouTube channel name, RSS feed name, hostname
    external_id: str              # YouTube video id, RSS guid, sha256 of URL
    parent: Optional[CanonicalParent]  # None → falls back to synthetic audio-imports
```

### YouTubeResolver

- **Match:** URLs whose host is `youtube.com`, `youtu.be`, or `m.youtube.com`.
- **Resolve:** Run `yt-dlp --skip-download --print-json <url>` to get metadata. Map `id → external_id`, `webpage_url → audio_url`, `channel → source_handle`, `upload_date → pub_date`, `thumbnails[-1].url → image_url`.
- **Canonical id:** `youtube:<video_id>`. Two URLs (e.g. `youtu.be/X` and `youtube.com/watch?v=X`) collapse to the same canonical id.
- **Parent:** `CanonicalParent(external_id=channel_id, rss_url="https://www.youtube.com/feeds/videos.xml?channel_id=<id>", title=channel, image_url=channel_thumbnail)`.
- Existing pipeline already supports YouTube via yt-dlp in the download stage — no changes needed there.

### BareAudioResolver

- **Match:** URL ends in a known audio extension (`.mp3`, `.m4a`, `.opus`, `.ogg`, `.wav`) AND is reachable via HEAD with `Content-Type: audio/*`.
- **Resolve:** HEAD the URL for `Content-Length` and `Content-Type`. Synthesize a title from the filename and use `audio:<sha256_url>` as the external id.
- **Canonical id:** `audio:<sha256_url>` after URL normalisation (drop tracking params, lowercase host).
- **Parent:** `None` — falls back to the synthetic `audio-imports` parent.

### ApplePodcastsResolver (follow-up, not v1)

- **Match:** URLs whose host is `podcasts.apple.com`.
- **Resolve:** Extract show id and episode id from the path / `?i=` query param. Hit the iTunes Search API to get the RSS feed URL, fetch the feed, locate the matching episode by guid.
- **Canonical id:** `rss:<guid>` (real RSS guid available).
- **Parent:** `CanonicalParent` from the resolved RSS feed. This is the cleanest case — both episode and parent are first-class.

### Out-of-scope resolvers

- **Spotify share links** — exclusives have no audio enclosure; non-exclusives need API auth and are not worth the complexity. Rejected with a clear error.
- **Pocket Casts share links** — deferred until there's user demand.

---

## Parent Podcast Handling

Imported episodes need a `podcast_id` (current schema requires it; spec #32 lifts that). The parent comes from one of two paths:

### Path A — Real parent (deducible)

When the resolver can identify a parent, we upsert a normal row in `podcasts` and link the episode to it:

| Resolver | Deduced parent | Notes |
|---|---|---|
| `RssEnclosureResolver` (with feed URL) | The RSS feed itself | Only when we have the feed URL, not for bare enclosures. |
| `ApplePodcastsResolver` (follow-up) | RSS feed resolved via iTunes Search API | Apple share links always carry show id → feed → episode guid. |
| `YouTubeResolver` | YouTube channel as a podcast (RSS: `youtube.com/feeds/videos.xml?channel_id=...`) | One channel = one parent podcast row. |

These podcasts are inserted as **regular podcasts**, not synthetic. They are marked `auto_added=1` so behaviors that should not fire for un-followed auto-adds can filter on it (see [Refresh Behavior](#refresh-behavior) and [Discovery Behavior](#discovery-behavior) below).

**No follow side-effect.** Inserting the parent podcast does NOT add a `follows` row for the importing user. Following remains a separate explicit action (and the post-import success state may offer it as a CTA per O4).

### Path B — Synthetic fallback (no deducible parent)

For bare audio URLs with no associated feed, we fall back to a single synthetic parent:

| `id` | `slug` | `title` | `rss_url` |
|---|---|---|---|
| `synthetic:audio-imports` | `audio-imports` | Audio imports | `synthetic://audio-imports` |

This row is:

- **Marked synthetic** — column `podcasts.synthetic BOOLEAN DEFAULT 0`.
- **Excluded from the main podcasts list, discovery, refresh.**
- **Created lazily** on first bare-audio import.
- **Never followed.** No role in the follow / inbox-fanout pipeline of #29.

The synthetic fallback has no page; clicking through from an imported episode takes the user to the original audio URL.

### Refresh Behavior

The refresh loop must not poll auto-added podcasts that no user follows (otherwise importing one YouTube video makes us poll that channel forever).

**Current state (verified):** `SqlitePodcastRepository.get_podcasts_for_refresh()` selects **all** rows from `podcasts` with no filter. The `podcast_followers` table exists (spec #29 is implemented) but the refresh predicate doesn't consult it.

**Required change:** modify `get_podcasts_for_refresh()` to exclude `synthetic=1` and exclude `auto_added=1 AND no rows in podcast_followers`. Concretely:

```sql
SELECT p.id, ... FROM podcasts p
WHERE p.synthetic = 0
  AND (p.auto_added = 0
       OR EXISTS (SELECT 1 FROM podcast_followers pf WHERE pf.podcast_id = p.id))
ORDER BY p.created_at DESC;
```

This is a Phase 1 deliverable, not a "verify" item.

### Discovery Behavior

**Current state (verified):**

- `GET /api/podcasts` already filters to the calling user's follows (via `follower_repository.get_followed_podcast_ids`). Auto-added podcasts won't appear here unless the user follows them — no change required.
- `GET /api/top-podcasts` reads from a separate curated `top_podcasts` table, not from `podcasts`. Unaffected by imports — no change required.

So no listing-side filter work is required for v1. If a future endpoint exposes the raw `podcasts` table, it must filter `synthetic=0 AND (auto_added=0 OR has_followers)`.

---

## Database Schema Changes

```sql
-- Mark synthetic podcasts (bare-audio fallback parent) so listings/refresh skip them
ALTER TABLE podcasts ADD COLUMN synthetic BOOLEAN NOT NULL DEFAULT 0;
CREATE INDEX idx_podcasts_synthetic ON podcasts(synthetic) WHERE synthetic = 1;

-- Mark podcasts that were auto-inserted by an import (real parent, no follower yet)
ALTER TABLE podcasts ADD COLUMN auto_added BOOLEAN NOT NULL DEFAULT 0;
CREATE INDEX idx_podcasts_auto_added ON podcasts(auto_added) WHERE auto_added = 1;

-- Canonical-id index on episodes for resolver-based dedup
ALTER TABLE episodes ADD COLUMN canonical_id TEXT;  -- "youtube:abc", "audio:sha256:..."
CREATE UNIQUE INDEX idx_episodes_canonical_id
  ON episodes(canonical_id) WHERE canonical_id IS NOT NULL;

-- Inbox source extension on user_episode_inbox.source
-- The existing CHECK constraint allows ('follow_new', 'follow_seed').
-- We extend it to ('follow_new', 'follow_seed', 'ad_hoc', 'import').
--
-- Naming note: spec #29 documents this value as 'fanout', but the implemented
-- code uses 'follow_new'. We keep the implemented name 'follow_new' for the
-- enum and treat 'fanout' as informal shorthand in prose only.
--
-- New values: 'ad_hoc'  — episode existed in system, user added it
--             'import'  — user pasted external URL, we materialised it
--
-- SQLite has no ALTER COLUMN; the migration recreates the CHECK constraint
-- via the standard table-rebuild pattern (CREATE NEW → INSERT SELECT →
-- DROP OLD → RENAME) used elsewhere in _run_migrations.
```

`synthetic` and `auto_added` are distinct flags:

- `synthetic=1` — the bare-audio fallback parent only. Never user-facing.
- `auto_added=1` — a real podcast row inserted as a side-effect of an import. Becomes a normal podcast as soon as any user follows it (the flag can stay on or be cleared on first follow; either is fine — listings filter on `auto_added=1 AND no_followers`).

**Backfill:** existing episodes have `canonical_id = NULL`. They participate in idempotency only by `(podcast_id, external_id)` as before. Imports going forward set `canonical_id`, and dedup uses it. Future spec #32 may backfill `canonical_id` for all rows; not required here.

---

## Service Layer

### `ImportService`

```python
class ImportService:
    def __init__(self, *, repository, inbox_repository, queue_manager,
                 resolvers: list[Resolver]):
        ...

    def import_url(self, user_id: str, url: str) -> ImportResult:
        canonical = self._resolve(url)
        if canonical.parent is not None:
            # Apple / RSS / YouTube: real parent deducible
            parent = self._upsert_auto_added_podcast(canonical.parent)
        else:
            # Bare audio: synthetic fallback
            parent = self._ensure_synthetic_audio_imports_parent()
        episode, created = self._find_or_create_episode(parent, canonical)
        inbox_row, inbox_created = self._inbox_repo.find_or_create(
            user_id=user_id,
            episode_id=episode.id,
            source='import',
        )
        if created:
            self._queue_manager.add_task(
                episode_id=episode.id,
                stage=TaskStage.DOWNLOAD,
                metadata={'run_full_pipeline': True, 'initiated_by': 'import'},
            )
        return ImportResult(episode=episode, inbox_row=inbox_row,
                            episode_created=created, inbox_created=inbox_created)

    def _resolve(self, url: str) -> CanonicalSource:
        for resolver in self._resolvers:
            if resolver.matches(url):
                return resolver.resolve(url)
        raise UnsupportedUrlError(url)
```

Idempotency contract:

- Same URL pasted twice by the same user → returns existing episode + existing inbox row (no new pipeline task).
- Same URL pasted by two users → same episode (shared Whisper output), one inbox row per user.
- Pipeline does not start a second time when the episode already has a non-`discovered` state.

---

## Pipeline Integration

**No new stages.** Imports walk the existing chain: `download → downsample → transcribe → clean → summarize → extract-entities → resolve-entities → reindex`.

**Two small touch-ups:**

1. **`download` task for synthetic podcasts** must dispatch by `podcast.synthetic` — same dispatcher as today, just need to ensure yt-dlp is invoked when `canonical_id` starts with `youtube:`. Existing handler already runs yt-dlp for known YouTube feeds, so the dispatch may already work; verify.
2. **Inbox row state** is computed from `(episode.state, episode.entity_extraction_status)` so the frontend doesn't need a separate column. A small helper in the inbox response builder derives `processing | ready | failed`.

---

## API Changes

### `POST /api/imports`

```http
POST /api/imports
Content-Type: application/json

{ "url": "https://www.youtube.com/watch?v=dQw4w9WgXcQ" }
```

```json
HTTP/1.1 201 Created
{
  "status": "ok",
  "import": {
    "episode_id": "abc-123",
    "canonical_id": "youtube:dQw4w9WgXcQ",
    "title": "Some Talk",
    "source_handle": "Lex Fridman",
    "kind": "youtube",
    "state": "discovered",
    "inbox_status": "processing",
    "deduplicated": false
  }
}
```

`deduplicated=true` when this URL had already been imported (by anyone, in shared mode); the response is otherwise identical so the frontend doesn't need to branch.

Error responses:

- `400` — `unsupported_url` if no resolver matches.
- `400` — `resolver_failed` with details if yt-dlp / HEAD fails.
- `429` — `quota_exceeded` (multi-user only; not v1).

### `GET /api/inbox` extension

The existing inbox listing (per spec #29) gains a derived `inbox_status` field per row: `processing | ready | failed`. The frontend uses this to render a progress indicator.

---

## Frontend UX

### Entry points

- A "+" button in the global nav with two options: "Add podcast" (existing) and "Import episode" (new).
- A field on the inbox empty-state: "Have a link? Paste it here."

### Modal

Single text input with a smart placeholder:

> Paste a YouTube link, RSS episode URL, or audio file URL.

On submit:

1. POST `/api/imports` and disable the form.
2. On success, close the modal and surface a toast: "Importing… you'll see it in your inbox in a minute."
3. The inbox auto-refreshes (or the row appears via the existing query invalidation).
4. Inbox row shows a small spinner + "Transcribing" / "Cleaning" / "Summarising" caption based on `inbox_status` plus `episode.state`.

### Errors

- Unsupported URL → inline error in the modal:
  - v1: "We support YouTube and direct audio links right now. Apple Podcasts support is on the way; Spotify links aren't supported."
  - After Apple resolver ships: "We support YouTube, Apple Podcasts, and direct audio links. Spotify links aren't supported."
- Resolver failed → inline error with the resolver's message (e.g. "YouTube returned 'Video unavailable'").

---

## Migration Strategy

Pure-additive. No data backfill required.

1. Schema migrations: `podcasts.synthetic`, `episodes.canonical_id` and its unique index. Both wrapped in `IF NOT EXISTS` / `ALTER TABLE` patterns matching `_run_migrations`.
2. Synthetic podcasts are created lazily on first import.
3. Existing podcasts and episodes are unaffected.
4. Inbox rows from imports use `source='import'`; other sources (`follow_new`, `follow_seed`, `ad_hoc`) continue to behave per spec #29.

If spec #32 lands later:

- `canonical_id` already populated for imports — minimal further work.
- Synthetic podcasts become the "primary podcast" for these episodes; the membership table holds the per-user import linkage.

---

## Out of Scope (deferred to #32)

- Multiple podcasts pointing at one episode (e.g. cross-posted shows).
- A YouTube video appearing in multiple user-created playlists.
- Episode-page URL design when an episode has no canonical parent.
- Backfilling `canonical_id` for all existing podcast episodes.

These all require the membership table from spec #32. Without #32, an imported YouTube video lives only under `synthetic:youtube-imports` and only that synthetic-podcast page lists it. Acceptable for v1.

---

## Resolved Decisions

| # | Question | Decision |
|---|---|---|
| O1 | Shared episode (one row, multi-user) vs per-user episode | **Shared.** One `episodes` row per canonical id; each user gets their own inbox row pointing at it. Whisper time is the expensive resource; pasted URLs are not sensitive. |
| O2 | Synthetic parent for every import vs deduce a real parent when possible | **Deduce real parent when possible; synthetic only for bare audio.** RSS, Apple, and YouTube imports upsert a real `podcasts` row (`auto_added=1`). Bare-audio URLs fall back to the single `synthetic:audio-imports` parent. Adding a podcast is decoupled from following it. |
| O3 | Re-fetch metadata on imported YouTube videos when titles change | **No.** Imports are snapshots. Re-pasting the URL is idempotent and can refresh metadata on that path. |
| O4 | Follow-the-source CTA after a successful import | **Yes**, symmetric across kinds (Follow channel for YouTube, Follow podcast for RSS/Apple). Fires when the parent podcast exists in our system — which, post-O2, it always does for these kinds. CTA only adds a `follows` row; it does not change the import behavior. |
| O5 | What to do with Apple Podcasts and Spotify share links | **Apple:** add `ApplePodcastsResolver` as a follow-up (resolves to RSS feed via iTunes Search API, then guid-matches the episode). **Spotify:** reject with a clear error; exclusives have no enclosure and non-exclusives aren't worth the lookup complexity. |
| O6 | Inbox `source` enum size | **Keep all four** (`follow_new`, `follow_seed`, `ad_hoc`, `import`). Provenance is cheap to retain and useful for UI hints + analytics. (Spec #29 originally named the first value `fanout`; the implementation uses `follow_new`. We keep the implemented name.) |

### Pre-implementation Audit Findings (2026-05-08)

Code-reading audit complete. Status of each item:

- **YouTube dispatch — works as-is.** `MediaSourceFactory.detect_source()` (`thestill/core/media_source.py:1325-1344`) routes by URL pattern, not by any podcast field. An episode whose `audio_url` is a YouTube URL gets `YouTubeMediaSource` automatically. No change needed.
- **Refresh keying — change required.** `SqlitePodcastRepository.get_podcasts_for_refresh()` currently iterates all podcasts with no filter. See [Refresh Behavior](#refresh-behavior) above for the required predicate. Promoted from "verify" to a Phase 1 deliverable.
- **Discovery filter — already safe.** `/api/podcasts` is follows-scoped; `/api/top-podcasts` reads from a curated table. Both are unaffected by imports. No change needed.
- **Schema reality check — clean.** No collisions on `synthetic`, `auto_added`, or `canonical_id`. The inbox table is named `user_episode_inbox` (not `inbox`). Its `source` CHECK constraint currently allows `('follow_new', 'follow_seed')` and must be extended.

### Remaining Open Items

- **`auto_added` lifecycle.** Decide whether the flag stays on after first follow or gets cleared. Either works; pick one for consistency.
- **Quota numbers.** Self-hosted v1 has no quota. When multi-user lands, pick a daily import limit (likely tied to user tier).

---

## Implementation Phases

### Phase 1 — Schema + ImportService skeleton

- Migrations:
  - `podcasts.synthetic` and `podcasts.auto_added` columns + indexes.
  - `episodes.canonical_id` column + unique partial index.
  - Rebuild `user_episode_inbox` to extend the `source` CHECK constraint to include `'ad_hoc'` and `'import'`.
- `SqlitePodcastRepository.get_podcasts_for_refresh()` updated with the predicate from [Refresh Behavior](#refresh-behavior) (excludes synthetic and excludes auto_added with no followers).
- `ImportService` with a `BareAudioResolver` only (simplest case — no parent deduction).
- `synthetic:audio-imports` parent created lazily on first bare-audio import.
- `POST /api/imports` end-to-end for direct audio URLs.
- Inbox source `'import'` plumbed through #29's listing.
- Tests: idempotency, dedup on second import, inbox row appears immediately, synthetic parent excluded from refresh, auto_added-without-followers excluded from refresh, refresh still picks up auto_added once a follower row exists.

### Phase 2 — YouTubeResolver with real parent

- `YouTubeResolver` with yt-dlp metadata fetch + `CanonicalParent` (channel id, channel RSS feed URL, channel name, channel image).
- `_upsert_auto_added_podcast` path: insert/update channel as `auto_added=1` podcast row.
- Verify download handler runs yt-dlp for canonical YouTube ids.
- E2E test: paste a known YouTube URL → channel appears in `podcasts` (auto_added, hidden from browse, not refreshed) → episode appears → transcribe completes → entity layer populates.
- Idempotency tests: two users import the same URL → one episode row, one channel row, two inbox rows. User follows the channel → channel becomes visible in browse + starts refreshing.

### Phase 3 — Frontend modal + inbox progress UI

- "Import episode" entry point in nav.
- Modal + form with inline errors (incl. clear messaging for unsupported Spotify links).
- Inbox row processing-state rendering.
- Post-import success state offers "Follow this channel/podcast" CTA when import had a deducible parent (O4).
- Empty-state hint: "Paste a link to import an episode."

### Phase 4 — Polish + ApplePodcastsResolver

- `ApplePodcastsResolver` (iTunes Search API → RSS feed → guid match).
- Quota plumbing (no enforcement yet; just instrumentation).
- Hidden-from-discovery behavior verified for synthetic + auto_added podcasts.
- Docs: `docs/imports.md` with supported URL kinds + examples.

---

## Cross-References

- **Spec #29** — Inbox model. Imports use the same `user_episode_inbox` table with `source='import'`. The inbox listing endpoint extends to derive a processing state.
- **Spec #32** — Episodes-as-first-class. Imports are the first feature where the synthetic-parent workaround is visibly awkward; #32 cleans it up by making episodes carry their own identity and using a membership table for podcast-level grouping.
