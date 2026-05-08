# Import Arbitrary Episodes Specification

> **Status:** 📝 Draft
> **Created:** 2026-05-06
> **Updated:** 2026-05-06
> **Author:** Product & Engineering
> **Related:** [#29 per-user-inbox-fanout](29-per-user-inbox-fanout.md), [#32 episodes-as-first-class](32-episodes-as-first-class.md)

---

## Executive Summary

Let users paste a URL — RSS episode, YouTube video, or bare audio file — and have it land in their inbox **immediately**, with the pipeline running in the background. The user does not need to follow the source podcast; the episode is associated with a synthetic parent ("youtube-imports", "ad-hoc-imports") for storage purposes only.

**Mental model:** Personal transcription queue layered onto the podcast aggregator. The pipeline is reused as-is; the new surface is URL parsing, synthetic-parent management, and a live "processing" state on the inbox row.

**Key principle:** Don't introduce a new pipeline. Imports become regular `episodes` rows under synthetic podcasts and run through the existing `transcribe → clean → summarize → entity branch` chain. The only new code is URL resolution, the synthetic-podcast bootstrap, and the inbox row that appears before processing finishes.

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
13. [Open Questions](#open-questions)
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
   - **Out of scope:** Apple Podcasts / Spotify / Pocket Casts share links (these don't expose audio directly; resolving them needs platform-specific lookup, deferred to a follow-up).
2. **Idempotency on canonical id.** Each resolver produces a typed canonical id (`youtube:<video_id>`, `audio:<sha256_url>`, `rss:<guid>`). Re-importing the same URL returns the existing episode row and adds the user's inbox row if they didn't already have one.
3. **Inbox-first UX.** The inbox row is created **before** processing starts so the user sees the import immediately with a `processing` state. The row updates live as pipeline stages complete.
4. **Ad-hoc and Import are different sources.**
   - `inbox.source='ad_hoc'` — added from an episode already in the system (the (3.1) collapse from earlier discussions).
   - `inbox.source='import'` — added from an external URL we hadn't seen before.
5. **No follow side-effect.** Importing does not subscribe the user to the source. Future episodes from that channel/feed do NOT auto-arrive.
6. **Privacy.** Imports go under one of two visibility models (decision in [Open Questions](#open-questions)):
   - **(a) Shared canonical episode:** Two users importing the same YouTube video share the same `episodes` row, the Whisper run is shared, but each user sees it only via their own inbox row.
   - **(b) Per-user episode:** Each user's import creates a separate `episodes` row, no sharing.
   - Default recommended: **(a)** — Whisper / GPU time is the most expensive resource; sharing is the right tradeoff. The episode itself isn't sensitive (it's a public URL the user pasted).
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
   ├── resolve_url(url) → CanonicalSource(kind, canonical_id, audio_url, title, ...)
   │
   ├── ensure_synthetic_podcast(kind) → Podcast (e.g. "youtube-imports")
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

`CanonicalSource` carries everything we need to mint an Episode:

```python
@dataclass
class CanonicalSource:
    kind: Literal["youtube", "rss_episode", "bare_audio"]
    canonical_id: str            # e.g. "youtube:dQw4w9WgXcQ"
    audio_url: str               # what yt-dlp / downloader fetches
    title: str
    description: Optional[str]
    duration_seconds: Optional[int]
    pub_date: Optional[datetime]
    image_url: Optional[str]
    source_handle: str           # YouTube channel name, RSS feed name, hostname
    external_id: str             # YouTube video id, RSS guid, sha256 of URL
```

### YouTubeResolver

- **Match:** URLs whose host is `youtube.com`, `youtu.be`, or `m.youtube.com`.
- **Resolve:** Run `yt-dlp --skip-download --print-json <url>` to get metadata. Map `id → external_id`, `webpage_url → audio_url`, `channel → source_handle`, `upload_date → pub_date`, `thumbnails[-1].url → image_url`.
- **Canonical id:** `youtube:<video_id>`. Two URLs (e.g. `youtu.be/X` and `youtube.com/watch?v=X`) collapse to the same canonical id.
- Existing pipeline already supports YouTube via yt-dlp in the download stage — no changes needed there.

### RssEnclosureResolver

- **Match:** URL ends in a known audio extension (`.mp3`, `.m4a`, `.opus`, `.ogg`, `.wav`) AND is reachable via HEAD with `Content-Type: audio/*`.
- **Resolve:** HEAD the URL for `Content-Length` and `Content-Type`. We don't have RSS-feed metadata for a bare enclosure URL, so synthesize a title from the filename and use `audio:<sha256_url>` as the external id (no RSS guid available).
- **Canonical id:** `audio:<sha256_url>` after URL normalisation (drop tracking params, lowercase host).

### BareAudioResolver

- Same as RssEnclosureResolver. The two are folded into one resolver in v1; the distinction matters only when we later add Apple/Spotify lookups that produce real RSS guids.

### Future resolvers (deferred)

- **Apple Podcasts share link** → resolve to RSS feed → lookup matching episode by guid.
- **Spotify share link** → similar but Spotify exclusives have no enclosure (deferred or rejected).

---

## Synthetic Parents

Imported episodes need a `podcast_id` (current schema requires it; spec #32 lifts that). We use one synthetic podcast row per resolver kind:

| `id` | `slug` | `title` | `rss_url` |
|---|---|---|---|
| `synthetic:youtube-imports` | `youtube-imports` | YouTube imports | `synthetic://youtube-imports` |
| `synthetic:audio-imports` | `audio-imports` | Audio imports | `synthetic://audio-imports` |

These rows are:

- **Marked synthetic** — a new column `podcasts.synthetic BOOLEAN DEFAULT 0` (or a magic prefix on the id; the column is more honest).
- **Excluded from the main podcasts list and discovery.** Hidden from `/podcasts`, the subscribe-to-feed UI, the refresh loop.
- **Created lazily** on first import of that kind.
- **Never followed.** They have no role in the follow / inbox-fanout pipeline of #29.

The synthetic podcast doesn't have its own page; clicking through from an imported episode takes the user to the source URL (YouTube channel page, original audio host).

---

## Database Schema Changes

```sql
-- Mark synthetic podcasts so listings/refresh skip them
ALTER TABLE podcasts ADD COLUMN synthetic BOOLEAN NOT NULL DEFAULT 0;
CREATE INDEX idx_podcasts_synthetic ON podcasts(synthetic) WHERE synthetic = 1;

-- Canonical-id index on episodes for resolver-based dedup
ALTER TABLE episodes ADD COLUMN canonical_id TEXT;  -- "youtube:abc", "audio:sha256:..."
CREATE UNIQUE INDEX idx_episodes_canonical_id
  ON episodes(canonical_id) WHERE canonical_id IS NOT NULL;

-- Inbox source extension (additive enum value)
-- Existing values: 'fanout', 'follow_seed' (per spec #29)
-- New values:      'ad_hoc'  — episode existed in system, user added it
--                  'import'  — user pasted external URL, we materialised it
-- (No DDL change if source is TEXT; just document the new values.)
```

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
        synthetic = self._ensure_synthetic_podcast(canonical.kind)
        episode, created = self._find_or_create_episode(synthetic, canonical)
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

- Unsupported URL → inline error in the modal: "We support YouTube and direct audio links right now. Apple Podcasts and Spotify links aren't supported yet."
- Resolver failed → inline error with the resolver's message (e.g. "YouTube returned 'Video unavailable'").

---

## Migration Strategy

Pure-additive. No data backfill required.

1. Schema migrations: `podcasts.synthetic`, `episodes.canonical_id` and its unique index. Both wrapped in `IF NOT EXISTS` / `ALTER TABLE` patterns matching `_run_migrations`.
2. Synthetic podcasts are created lazily on first import.
3. Existing podcasts and episodes are unaffected.
4. Inbox rows from imports use `source='import'`; other sources (`fanout`, `follow_seed`, `ad_hoc`) continue to behave per spec #29.

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

## Open Questions

| # | Question | Recommendation |
|---|---|---|
| O1 | Shared episode (one row, multi-user) vs per-user episode (one row per user) | Shared — Whisper time is precious; URLs aren't sensitive |
| O2 | Should `synthetic:audio-imports` collapse all bare audio sources, or split by host? | Single — host-aware grouping is a UX nicety we can layer on later |
| O3 | Do we proactively re-fetch metadata on imported YouTube videos that change titles? | No — imports are snapshots; users can re-import to refresh if needed |
| O4 | Should a follow-the-channel CTA appear after a successful YouTube import? | Yes, on the post-import success state — but only if we already track that channel as a podcast (most YouTube channels won't be in our system) |
| O5 | What happens if an Apple/Spotify link is pasted? | Show a clear "not supported yet" error; future resolver can add it |
| O6 | Inbox `source` enum: are 4 values (`fanout`, `follow_seed`, `ad_hoc`, `import`) too many? | No — they distinguish provenance for analytics and UI hints |

---

## Implementation Phases

### Phase 1 — Schema + ImportService skeleton

- Migrations: `podcasts.synthetic`, `episodes.canonical_id` + unique index.
- `ImportService` with a `BareAudioResolver` only (simplest case).
- `POST /api/imports` end-to-end for direct audio URLs.
- Inbox source `'import'` plumbed through #29's listing.
- Tests: idempotency, dedup on second import, inbox row appears immediately.

### Phase 2 — YouTubeResolver

- `YouTubeResolver` with yt-dlp metadata fetch.
- Synthetic `youtube-imports` podcast created on first YouTube import.
- Verify download handler runs yt-dlp for canonical YouTube ids.
- E2E test: paste a known YouTube URL → episode appears → transcribe completes → entity layer populates.

### Phase 3 — Frontend modal + inbox progress UI

- "Import episode" entry point in nav.
- Modal + form with inline errors.
- Inbox row processing-state rendering.
- Empty-state hint: "Paste a link to import an episode."

### Phase 4 — Polish

- Quota plumbing (no enforcement yet; just instrumentation).
- Hidden-from-discovery behavior verified for synthetic podcasts.
- Docs: `docs/imports.md` with supported URL kinds + examples.

---

## Cross-References

- **Spec #29** — Inbox model. Imports use the same `inbox` table with `source='import'`. The inbox listing endpoint extends to derive a processing state.
- **Spec #32** — Episodes-as-first-class. Imports are the first feature where the synthetic-parent workaround is visibly awkward; #32 cleans it up by making episodes carry their own identity and using a membership table for podcast-level grouping.
