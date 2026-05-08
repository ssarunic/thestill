# Episodes as First-Class Entities Specification

> **Status:** 📝 Draft
> **Created:** 2026-05-06
> **Updated:** 2026-05-06
> **Author:** Product & Engineering
> **Related:** [#29 per-user-inbox-fanout](29-per-user-inbox-fanout.md), [#31 import-arbitrary-episodes](31-import-arbitrary-episodes.md)

---

## Executive Summary

Promote `episodes` to first-class entities that exist on their own and can belong to **multiple collections** — podcasts, user playlists, and synthetic groups (imports, "saved by guest"). Today every episode has exactly one parent podcast (`episodes.podcast_id` is NOT NULL FK). After this change, the parent pointer becomes "primary podcast" and a separate `collection_memberships` table holds the many-to-many.

**Mental model:** Episodes are the canonical content unit, identified by a typed canonical id (`youtube:abc`, `rss:guid:foo`, `audio-hash:...`). Podcasts and user playlists are *collections* of episodes. The same YouTube video can live under the official podcast feed and a user's playlist simultaneously, with the expensive pipeline work (Whisper, embeddings, entity extraction) shared.

**Key principle:** Additive migration, no big-bang. `episodes.podcast_id` stays as a "primary collection" pointer; new features use the membership table; existing queries keep working unchanged.

---

## Table of Contents

1. [Motivation](#motivation)
2. [Why Now](#why-now)
3. [Concept Model](#concept-model)
4. [Database Schema Changes](#database-schema-changes)
5. [URL Design](#url-design)
6. [Service Layer Changes](#service-layer-changes)
7. [Search and Entity Layer Implications](#search-and-entity-layer-implications)
8. [Migration Strategy](#migration-strategy)
9. [Pipeline Changes](#pipeline-changes)
10. [API Changes](#api-changes)
11. [Frontend Changes](#frontend-changes)
12. [Cost / Benefit](#cost--benefit)
13. [Open Questions](#open-questions)
14. [Implementation Phases](#implementation-phases)
15. [Non-Goals](#non-goals)

---

## Motivation

Several real product needs are awkward or impossible under the current `episodes.podcast_id NOT NULL` model:

1. **Cross-posted shows.** Some podcasts publish the same conversation on multiple feeds (a show's main feed + a network-wide feed; an audio podcast that also has a YouTube video version). Today we ingest each twice, run Whisper twice, store two episode rows that look almost-but-not-quite identical. The user sees "the same episode" appear twice in different inboxes.
2. **YouTube videos in multiple playlists.** A single YouTube video can belong to many YouTube playlists (the platform's first-class data model). When we eventually let users create personal playlists or follow channel-curated playlists, a video being in several playlists has no clean representation under the current schema.
3. **User-imported episodes (#31).** Today's spec #31 hangs imports off a synthetic `youtube-imports` podcast as a workaround. That works, but it's clearly a bandage — the import doesn't *belong* to a podcast in any meaningful sense.
4. **Future: user-curated playlists.** "My favourite AI episodes" — a user-created list of episodes from any sources. There's no schema for this today; the closest analog is the `inbox`, which is per-user but flat.
5. **Saved-by-entity collections.** "All episodes featuring Sarah Paine" is a query, but materialising it as a navigable collection (with stable ordering, share URLs, etc.) is an entity-driven playlist.

All of these collapse to the same pattern: **episode-N-to-collection-M** with the episode as the canonical, work-shared unit.

---

## Why Now

Two recent changes make this refactor concretely valuable rather than speculative:

- **Spec #28** built the entity layer. Search, entity ranking, and role-based ranking all key off `episode_id`, not `podcast_id`. The hard work is already episode-centric.
- **Spec #31** introduces user imports via a synthetic-podcast workaround. The first feature whose data model we're explicitly compromising for the current schema. Doing #32 within ~one product cycle of #31 means the workaround is temporary, not permanent debt.

Doing this *before* user playlists or follow-by-entity (the two future features that hardest depend on first-class episodes) means those features land cleanly. Doing it *after* means painful retrofits for each.

---

## Concept Model

### Today

```
podcasts (1) ──< (∞) episodes
                          │
                          └──< (∞) chunks
                          └──< (∞) entity_mentions
```

Each episode has exactly one podcast. The podcast slug + episode slug form the canonical URL. Refresh iterates podcasts; the user follows podcasts; the inbox is fanned out per podcast follow.

### After this spec

```
                    ┌──────────────┐
                    │   episodes   │  (canonical content; identified by canonical_id)
                    │  ┌────────┐  │
                    │  │ chunks │  │  (entity work shared across all memberships)
                    │  └────────┘  │
                    └──────────────┘
                           │
                           │ (∞)
                           ▼
                ┌────────────────────┐
                │ collection_        │
                │   memberships      │
                └────────────────────┘
                           │
                           │ (∞)
                           ▼
                ┌────────────────────┐
                │   collections      │  (kind = 'podcast' | 'user_playlist'
                │                    │          | 'synthetic_imports' | 'entity_pin')
                │  ┌──────────────┐  │
                │  │  podcasts    │  │  (kind='podcast')
                │  └──────────────┘  │
                └────────────────────┘
```

### Names

- **Episode** — canonical content row. Has `canonical_id` (typed), `primary_collection_id`, audio paths, transcripts, embeddings, entity links. No required parent — `primary_collection_id` is NULLABLE for things like ad-hoc one-off imports that don't belong to any feed conceptually.
- **Collection** — anything that groups episodes. Subtypes via `kind` column:
  - `podcast` — RSS feed (today's `podcasts` table folds into this).
  - `youtube_channel` — a YouTube channel.
  - `user_playlist` — user-curated.
  - `synthetic_import` — placeholder for imports that don't belong to a feed (replaces #31's workaround).
  - `entity_pin` — auto-generated "every episode featuring X".
- **Membership** — `(episode_id, collection_id, primary, position, added_at, source)`. `primary=true` exactly once per episode (the original source); other rows are secondary memberships.

### Key invariants

1. Every episode has at most one `primary=true` membership (or zero, for fully-orphaned imports).
2. `episodes.canonical_id` is globally unique and immutable. Two URLs that resolve to the same content collapse to one row.
3. Pipeline work (transcribe, clean, summarize, entity extraction, embeddings) is per-episode, never per-membership.

---

## Database Schema Changes

### New: `collections` table

```sql
CREATE TABLE collections (
    id          TEXT PRIMARY KEY,
    kind        TEXT NOT NULL CHECK (kind IN (
                  'podcast', 'youtube_channel', 'user_playlist',
                  'synthetic_import', 'entity_pin'
                )),
    title       TEXT NOT NULL,
    slug        TEXT,
    -- Owner: NULL for shared/public collections (podcasts, channels);
    -- a user_id for user_playlist; entity_id encoded in metadata for
    -- entity_pin.
    owner_user_id TEXT REFERENCES users(id) ON DELETE CASCADE,
    -- Source-kind-specific metadata. For podcasts: rss_url, image_url.
    -- For youtube_channel: channel_id. For entity_pin: entity_id.
    metadata    TEXT,  -- JSON
    created_at  TIMESTAMP NOT NULL DEFAULT (strftime('%Y-%m-%dT%H:%M:%f+00:00','now')),
    updated_at  TIMESTAMP NOT NULL DEFAULT (strftime('%Y-%m-%dT%H:%M:%f+00:00','now'))
);

CREATE INDEX idx_collections_kind ON collections(kind);
CREATE INDEX idx_collections_owner ON collections(owner_user_id) WHERE owner_user_id IS NOT NULL;
CREATE UNIQUE INDEX idx_collections_slug
  ON collections(kind, slug) WHERE slug IS NOT NULL AND slug != '';
```

### New: `collection_memberships` table

```sql
CREATE TABLE collection_memberships (
    episode_id     TEXT NOT NULL REFERENCES episodes(id) ON DELETE CASCADE,
    collection_id  TEXT NOT NULL REFERENCES collections(id) ON DELETE CASCADE,
    is_primary     BOOLEAN NOT NULL DEFAULT 0,
    position       INTEGER,                           -- nullable, for ordered playlists
    source         TEXT NOT NULL DEFAULT 'rss',       -- 'rss', 'youtube', 'import',
                                                      -- 'manual', 'auto_entity'
    added_at       TIMESTAMP NOT NULL DEFAULT (strftime('%Y-%m-%dT%H:%M:%f+00:00','now')),
    PRIMARY KEY (episode_id, collection_id)
);

CREATE INDEX idx_memberships_collection_pos
  ON collection_memberships(collection_id, position);
CREATE UNIQUE INDEX idx_memberships_one_primary
  ON collection_memberships(episode_id) WHERE is_primary = 1;
```

### `episodes` table changes

```sql
-- Canonical id (added by spec #31, indexed here as the dedup key)
ALTER TABLE episodes ADD COLUMN canonical_id TEXT;
CREATE UNIQUE INDEX idx_episodes_canonical_id
  ON episodes(canonical_id) WHERE canonical_id IS NOT NULL;

-- "Primary collection" — the source where this episode was first
-- discovered. Replaces ``podcast_id`` semantically but stays under
-- the old name for backward compatibility (see Migration Strategy).
-- Becomes nullable for fully-orphaned ad-hoc imports.
ALTER TABLE episodes RENAME COLUMN podcast_id TO primary_collection_id;
-- (Or: keep podcast_id name, but conceptually it now points at the
-- collections table. See Migration Strategy for the choice.)
```

### Backward-compat view: `podcasts`

Existing application code reads `podcasts` extensively. The migration creates the new `collections` table and **re-points the existing `podcasts` table to be a view** (or keeps `podcasts` as a table but forces inserts to go through the collections-aware service):

```sql
-- Option A: convert podcasts to a view over collections
CREATE VIEW podcasts AS
  SELECT id,
         json_extract(metadata, '$.rss_url')   AS rss_url,
         title,
         slug,
         json_extract(metadata, '$.image_url') AS image_url,
         /* ...other columns... */
         created_at, updated_at
  FROM collections
  WHERE kind = 'podcast';
```

A view keeps every existing query working at zero refactor cost. Writes through the view need INSTEAD-OF triggers or service-layer funnelling.

**Or Option B**: keep `podcasts` as a real table, add a `collections` mirror, sync via triggers. Simpler reads, more write surface to keep in sync. Decision in [Open Questions](#open-questions).

---

## URL Design

This is the user-visible part of the refactor. Today's URL convention:

```
/podcasts/<podcast-slug>/episodes/<episode-slug>
```

After this spec, the same episode could belong to multiple collections. Two options:

### Option A — preserve current URLs (recommended)

The episode's `primary_collection_id` defines the canonical URL. Other memberships are accessible via `/collections/<collection-slug>/episodes/<episode-slug>` (or `/podcasts/<another-pod>/episodes/<...>` if the collection is also a podcast). Canonical URL gets a `<link rel="canonical">` for SEO; secondary URLs redirect to canonical or render a "this episode also appears in: [list]" panel.

Pros: zero broken links.
Cons: "primary" feels arbitrary when an episode genuinely cross-posts.

### Option B — episode-rooted URLs

```
/episodes/<global-episode-slug>
```

Plus filtered listings under `/podcasts/<slug>/...`. Old URLs 301-redirect to the new shape.

Pros: honest to the new model. Cleaner future for non-podcast collections (user playlists, entity pins) — they don't pretend to be podcasts.
Cons: every old URL gets a 301 (manageable but real).

**Recommendation:** Option A in this spec; Option B is a follow-up if user playlists become the dominant access pattern.

---

## Service Layer Changes

### New: `CollectionRepository`

Generic CRUD over `collections` and `collection_memberships`. Methods:

```python
class CollectionRepository(Protocol):
    def create(self, collection: Collection) -> str: ...
    def get(self, collection_id: str) -> Optional[Collection]: ...
    def list_episodes(self, collection_id: str, *, limit, before) -> list[Episode]: ...
    def add_episode(self, collection_id: str, episode_id: str, *,
                    is_primary: bool = False, position: Optional[int] = None,
                    source: str = 'manual') -> None: ...
    def remove_episode(self, collection_id: str, episode_id: str) -> None: ...
```

### `PodcastService` evolution

Existing `podcast_service.add(rss_url)` becomes a thin wrapper that:

1. Creates a `collections` row with `kind='podcast'`.
2. Inserts the resulting collection's id everywhere the old code expected `podcast_id` (now `primary_collection_id`).

Discovery / refresh continues to walk `kind='podcast'` collections; the rest of the pipeline doesn't change.

### `EpisodeService` changes

When discovery finds a new episode (RSS feed has a new entry, YouTube channel has a new video):

1. Compute `canonical_id` from the source.
2. **Find-or-create** the episode row by `canonical_id`.
3. **Find-or-create** a membership row `(episode, collection, is_primary=<true if new episode>, source=<source>)`.

If the episode already exists (cross-post case), only the membership is added — no second pipeline run.

---

## Search and Entity Layer Implications

The entity layer is already episode-keyed, so most code is unchanged. A few touch-ups:

- **Filters by podcast** (e.g. `/api/search/quick?podcast_slug=foo`) become "filter by collection". Implementation: `EXISTS (SELECT 1 FROM collection_memberships m WHERE m.episode_id = ... AND m.collection_id = ?)` instead of `episodes.podcast_id = ?`.
- **Entity ranking by role** keeps working — `host_entity_ids` is on `collections.metadata` (was on `podcasts`), `guest_entity_ids` stays on episodes.
- **Inbox fanout (#29)** — the fanout source is the *podcast* a user follows. Today: "user follows podcast X → fan out X's new episodes." After this spec: "user follows collection X → fan out new memberships in X." Cross-posted episodes that newly join a collection trigger one fanout per collection. The inbox row's idempotency on `(user_id, episode_id)` handles dedup if a user follows two collections both holding the same episode.

---

## Migration Strategy

This is the bulk of the work. Approach:

### Phase 1 — Add the new tables, dual-write

1. Create `collections` and `collection_memberships` tables.
2. Backfill: for every existing `podcasts` row, create a `collections` row with the same id, `kind='podcast'`, fields copied from the podcast row to `metadata` JSON. For every existing `episodes` row, create a `collection_memberships` row with `is_primary=true, source='rss'`.
3. Keep `podcasts` table as-is. New code paths can read from either; we ensure they stay in sync via service-level dual-write or via triggers.

### Phase 2 — Read traffic migration

1. Convert read paths one-by-one to query `collections` / `collection_memberships`. Each conversion is small and mechanical.
2. Add tests asserting equivalence between old and new query results during the transition.

### Phase 3 — Drop dual-write

1. Once all read paths are on the new tables, convert `podcasts` to a view (Option A) or remove it entirely (Option C — most code now uses collections directly).
2. `episodes.podcast_id` rename to `primary_collection_id` (or keep the name; see Open Questions).

### Phase 4 — Lift the constraints

1. Make `episodes.primary_collection_id` NULLABLE (allowing fully-orphaned ad-hoc imports).
2. Lift the implicit one-podcast-per-episode assumption everywhere — refresh, fanout, search.

### Risk mitigation

- Every phase is independently shippable and reversible.
- Tests at each phase pin the equivalence between old and new code.
- Dual-write window is bounded: ideally < 2 weeks before flipping reads.

---

## Pipeline Changes

Minimal. The pipeline already operates on `episode_id` for almost all stages. Discovery is the only stage that conceptually iterates "podcasts." It becomes:

```python
for collection in collections.where(kind='podcast', auto_refresh=True):
    new_episodes = refresh_collection(collection)  # was: refresh_podcast
    for ep in new_episodes:
        canonical = compute_canonical(ep)
        episode = episodes.find_or_create(canonical_id=canonical)
        memberships.find_or_create(episode_id=episode.id, collection_id=collection.id,
                                    is_primary=<episode-is-new>, source='rss')
        if episode_is_new:
            queue_manager.add_task(episode_id=episode.id, stage=DOWNLOAD, ...)
```

Cross-posted detection is now automatic: if a YouTube channel's video matches an RSS episode's `canonical_id`, the second discovery just adds a membership. No second Whisper run.

---

## API Changes

### Backward compat

All existing podcast / episode endpoints continue to work. Internally they query `collections WHERE kind='podcast'` and `collection_memberships`, but the response shapes are unchanged.

### New endpoints (post-Phase 4)

```
GET  /api/collections/{collection_id}/episodes
POST /api/collections                    # create user_playlist
POST /api/collections/{id}/episodes      # add episode (manual)
DELETE /api/collections/{id}/episodes/{episode_id}
```

### Episode detail extension

`GET /api/episodes/{id}` gains a `memberships` field listing every collection this episode belongs to (with the `kind`, slug, primary flag, position). The frontend can render "Also appears in: [Lex Fridman Podcast, My Saved AI Talks]".

---

## Frontend Changes

- Episode page: small "Also in: …" panel below the title for episodes with >1 membership.
- Podcast page: unchanged in v1 (Option A URL scheme preserves everything).
- New "Playlists" nav (post-Phase 4 once user_playlists are usable).

---

## Cost / Benefit

### Cost

- Two new tables, one renamed column, one large migration with dual-write.
- Roughly: schema (~1 day) + dual-write (~3 days) + read-path migration (~1-2 weeks across many small PRs) + flip + cleanup (~3 days).
- Risk: the dual-write window is the danger zone. Strong tests + a quick flip mitigate.

### Benefit

- **Cross-posted dedup.** One Whisper run shared across all memberships. For a heavily cross-posted feed (Lex Fridman audio + YouTube), this is real money.
- **Spec #31 cleanup.** Synthetic-podcast workaround is removed; imports become a real `synthetic_import` collection or a fully-orphaned episode.
- **User playlists** become a tractable feature instead of a nightmare.
- **Entity-driven collections** ("everything featuring Sarah Paine") become a stored, navigable thing.
- **Conceptual cleanup.** The data model finally matches how listeners think.

### When NOT to do this

If user playlists, entity-driven feeds, and cross-pod dedup are all 12+ months away, the cost/benefit shifts. But spec #31 already exposes the seam, so doing #32 within a few weeks of #31 lands keeps the workaround visibly temporary.

---

## Open Questions

| # | Question | Recommendation |
|---|---|---|
| O1 | Rename `episodes.podcast_id` to `primary_collection_id`, or keep the old name? | **Rename.** The new name reflects the new model; rename is one mechanical pass. Keeping the old name is technically free but invites confusion forever. |
| O2 | `podcasts` table → view, or kept as table with sync? | **View** (Option A). Reads become free; writes go through services anyway. |
| O3 | Episode URLs: keep `/podcasts/<p>/episodes/<e>` (Option A) or move to `/episodes/<global-slug>` (Option B)? | **Option A in this spec.** Option B as a separate follow-up only if user-playlist usage demands it. |
| O4 | When an episode joins a second collection (cross-post detected), does the inbox fan-out fire for new followers of the second collection? | **Yes**, but the inbox idempotency keeps a user from getting two rows. New followers of the second collection who haven't seen the episode get fanned out normally. |
| O5 | How is `canonical_id` computed for a legacy episode that lacks a YouTube id and lacks a stable RSS guid? | Backfill with `rss:<podcast_slug>:<episode_slug>` as the canonical id; idempotent for re-runs. Document as the legacy fallback. |
| O6 | Should `synthetic_import` collections from #31 be merged into one global collection per user, or kept per-source-kind? | Per source kind (`youtube-imports`, `audio-imports`) initially, matching #31. User-playlists handle the per-user grouping when that feature lands. |
| O7 | What happens to `podcast_followers` (per spec #29) — does it become `collection_followers`? | Yes, eventually. The rename is part of Phase 4 cleanup. The user-facing concept ("following") stays the same. |

---

## Implementation Phases

Each phase is independently mergeable + reversible.

1. **Schema** — `collections`, `collection_memberships`, `episodes.canonical_id`. Backfill from `podcasts` + `episodes`. Tests asserting equivalence (count of podcasts == count of `kind='podcast'` collections, etc.).
2. **Dual-write services** — `PodcastService` writes to both `podcasts` and `collections`; `EpisodeService` writes to `collection_memberships`. Single source of truth: services, never raw SQL.
3. **Read migration** — cut over each query path one PR at a time. Order: search filters → episode listings → podcast detail → refresh.
4. **Drop dual-write** — `podcasts` becomes a view. Rename `episodes.podcast_id` → `primary_collection_id`.
5. **Lift constraints** — make `primary_collection_id` NULLABLE; verify all callers handle absence.
6. **Net-new features** — user playlists, entity-pin collections, "Also in: …" UI.

---

## Non-Goals

- A new transcription pipeline. The pipeline doesn't change — it remains episode-keyed.
- Cross-tenant sharing of user playlists. v1 user playlists are private to the owner.
- Renaming the `podcasts` URL slug to `/collections/<slug>` for podcast-kind collections. URL stability beats internal-naming purity.
- Auto-creating `entity_pin` collections on every entity. Those are opt-in (user pins an entity).
- A spec for hosted-mode quotas / billing — that's whatever spec covers multi-tenant billing in general.

---

## Cross-References

- **Spec #28** — Entity layer. Already episode-keyed; this spec validates that choice.
- **Spec #29** — Inbox fanout. Adapts to "user follows collection" semantics; behaviour for the user is unchanged.
- **Spec #31** — Imports. The synthetic-podcast workaround dissolves here: imports become real `synthetic_import` collections (or fully-orphaned episodes) under the proper data model.
