# Per-User Inbox & Episode Fan-Out Specification

> **Status:** 🚧 Active development (2026-07-01 — corrected: inbox repository + `inbox_service` shipped)
> **Created:** 2026-05-05
> **Updated:** 2026-05-05
> **Author:** Product & Engineering
> **Related:** [#13 multi-user-shared-podcasts](13-multi-user-shared-podcasts.md), [#19 refresh-performance](19-refresh-performance.md)

---

## Executive Summary

Replace the current "everyone sees the same recent episodes from podcasts they follow" UX with a **per-user Inbox**: when an episode finishes processing, the system fans it out to one inbox row per follower of that podcast. Read state, saved state, and dismissal live on the inbox row. New followers receive a small **seed** of recent episodes on follow so the inbox is non-empty immediately.

**Mental model:** Substack-shaped delivery. Publications (podcasts) → publish events (transcribed episodes) → per-subscriber inbox rows with explicit state. No admin curation in the steady state — transcription is automatic, delivery is automatic.

**Key principle:** Decouple *what exists* (episodes in the system) from *what each user sees* (rows in their inbox). The pipeline being incomplete is a sufficient gate for visibility; an episode delivers to inboxes the moment its final pipeline stage commits, and not before.

---

## Table of Contents

1. [Motivation](#motivation)
2. [Product Requirements](#product-requirements)
3. [Architecture Overview](#architecture-overview)
4. [Database Schema Changes](#database-schema-changes)
5. [Data Model](#data-model)
6. [Service Layer Changes](#service-layer-changes)
7. [Pipeline Integration Points](#pipeline-integration-points)
8. [API Changes](#api-changes)
9. [Migration Strategy](#migration-strategy)
10. [Transitional Behavior (Cost-Constrained Mode)](#transitional-behavior-cost-constrained-mode)
11. [Naming Conventions](#naming-conventions)
12. [Implementation Phases](#implementation-phases)
13. [Open Questions](#open-questions)
14. [Non-Goals](#non-goals)

---

## Motivation

Today the user-facing episode list at [thestill/web/frontend/src/pages/](../thestill/web/frontend/src/pages) shows "recent episodes from podcasts I follow," computed as a join at request time with a global recency limit. Every user with overlapping follows sees the same slice. This has three problems:

1. **No per-user state.** Read/unread, saved, dismissed cannot be expressed without a per-user side table; today there is none.
2. **No notion of "newly delivered to me."** A user who has been away for a week cannot easily see "what's arrived since I last looked." The current view is a moving window, not a delivery feed.
3. **Backfilling old episodes pollutes everyone's view.** When the admin (today) or the system (future) decides to transcribe an older RSS entry, it appears in every follower's "recent" list because `pub_date` is old but it processed today. Either ordering choice (by pub_date or by processed_at) is wrong for someone.

A per-user inbox solves all three by making delivery an explicit event with per-user state.

---

## Product Requirements

### User Stories

| As a... | I want to... | So that... |
|---------|--------------|------------|
| User | See episodes that have been delivered *to me* | I can triage what's new without rescanning everything |
| User | Have read/unread state on each episode in my inbox | I can find what I haven't listened to yet |
| User | Save (star) episodes for later | I can build a listen-later list distinct from "unread" |
| User | Dismiss episodes I'm not interested in | They stop cluttering my unread count |
| User | Receive a few recent episodes immediately when I follow a new podcast | The inbox isn't empty after first follow |
| User | Not be flooded with hundreds of historical episodes when I follow a podcast | The inbox stays useful, not overwhelming |
| User | See an unfollowed podcast's older delivered episodes still in my inbox | Delivery is final; unfollowing only stops *new* deliveries |
| User | Browse all episodes of a followed podcast (including ones not in my inbox) | The podcast detail page remains an archive view, not constrained by inbox |

### Core Behaviors

1. **Delivery is event-based.** An episode enters inboxes exactly once, at the moment it transitions from "pipeline-incomplete" to "published." Re-publish is a no-op.
2. **Seed-on-follow.** Following a podcast inserts up to `INBOX_SEED_ON_FOLLOW` (default `2`) of its most recent published episodes into the new follower's inbox with `source='follow_seed'`.
3. **No retroactive delivery on follow beyond the seed.** Older published episodes from a newly-followed podcast are visible on the podcast detail page but never auto-fanned into existing follows. The follower can manually pull them in via a "Add to inbox" action (future enhancement; not in v1).
4. **Unfollow does not retract.** Inbox rows from an unfollowed podcast remain. New episodes from that podcast stop being delivered to the user. This matches email semantics — "unsubscribe" stops future mail; it doesn't recall prior mail.
5. **State is per-user.** Read/unread, saved, dismissed live on the inbox row. Two users following the same podcast can have completely independent state on the same episode.
6. **Idempotency.** `(user_id, episode_id)` is unique in the inbox. Any code path that tries to deliver an already-delivered episode is a no-op.
7. **CLI is unchanged.** Inbox is a web/web-frontend concept. CLI commands continue to operate on episodes globally.

### Non-Goals

See [Non-Goals](#non-goals) below.

---

## Architecture Overview

### Layered View

```
┌────────────────────────────────────────────────────────────┐
│  Web Frontend (React)                                      │
│    - Inbox page (replaces or augments current home/list)   │
│    - Mark read / save / dismiss actions                    │
└────────────────────────────────────────────────────────────┘
                            │
┌────────────────────────────────────────────────────────────┐
│  Web Routes (FastAPI)                                      │
│    - GET  /api/inbox?state=&limit=&before=                 │
│    - POST /api/inbox/{episode_id}/state {state}            │
└────────────────────────────────────────────────────────────┘
                            │
┌────────────────────────────────────────────────────────────┐
│  Services                                                  │
│    InboxService                                            │
│      .fanout_on_publish(episode_id)                        │
│      .seed_on_follow(user_id, podcast_id, count)           │
│      .list(user_id, *, state, limit, before)               │
│      .mark_state(user_id, episode_id, state)               │
│    FollowerService.follow()  ─── hook ──▶ seed_on_follow   │
└────────────────────────────────────────────────────────────┘
                            │
┌────────────────────────────────────────────────────────────┐
│  Repositories (SQLite)                                     │
│    InboxRepository (interface) + SQLiteInboxRepository     │
└────────────────────────────────────────────────────────────┘
                            │
┌────────────────────────────────────────────────────────────┐
│  Pipeline (existing)                                       │
│    task_handlers.summarize  ─── tail hook ──▶ publish      │
│      → set episodes.published_at                           │
│      → InboxService.fanout_on_publish (same txn)           │
└────────────────────────────────────────────────────────────┘
```

### Delivery Lifecycle

```
RSS feed
   │
   ▼
refresh ─────▶ episodes (published_at = NULL)   ◀── invisible to inboxes
                       │
                       ▼
                 pipeline (download → downsample → transcribe → clean → summarize)
                       │
                       ▼
       ┌──────────────────────────────┐
       │  PUBLISH transition          │     ◀── single atomic event
       │  episodes.published_at = NOW │
       │  fan-out to followers        │
       └──────────────────────────────┘
                       │
                       ▼
              user_episode_inbox rows  ◀── one per (user, episode)
                       │
                       ▼
                  user actions (read / save / dismiss)
```

### Why Fan-Out-On-Write (Not Read)

Considered and rejected: computing the inbox at read time as `episodes ⋈ follows ⋈ user_episode_state`. Fan-out-on-write was chosen because:

- **Seed-on-follow is explicit, not derived.** "User got 2 episodes when they followed" is a stateful fact; expressing it as a read predicate ("show 2 most recent published at follow time") is fragile and breaks when the user dismisses one of the seeded episodes.
- **Delivery is the user-facing semantic.** The Inbox is a list of *delivered things*, not a query result. Fan-out-on-write encodes that directly.
- **Scale doesn't force the choice.** thestill is a small-N system; write amplification (1 episode × M followers) is a non-issue at expected scale (M in single or low double digits).
- **Read queries are trivial.** Single-table scan with one indexed predicate. No joins to compose at request time.

If thestill ever grows to scale where write fan-out hurts (~10⁵+ followers per podcast), the read-side migration is straightforward and does not change the public API.

---

## Database Schema Changes

### `episodes` table — add `published_at`

```sql
ALTER TABLE episodes ADD COLUMN published_at TIMESTAMP NULL;

CREATE INDEX IF NOT EXISTS idx_episodes_published_at
    ON episodes(published_at DESC) WHERE published_at IS NOT NULL;
```

**Semantics:**

- `NULL` = pipeline incomplete; episode exists but is invisible to inboxes.
- non-`NULL` = pipeline complete (or admin-published during transition); has been fanned out.

This **replaces** any consideration of a multi-state `visibility` enum. The pipeline-completeness signal is sufficient, and avoids a parallel state machine.

**Optional, deferred:** `episodes.retracted_at TIMESTAMP NULL` for moderation/DMCA pulls. Not in v1; add when a real retraction case appears.

### `user_episode_inbox` — new table

```sql
CREATE TABLE IF NOT EXISTS user_episode_inbox (
    id              TEXT PRIMARY KEY NOT NULL,
    user_id         TEXT NOT NULL,
    episode_id      TEXT NOT NULL,
    source          TEXT NOT NULL,
    state           TEXT NOT NULL DEFAULT 'unread',
    delivered_at    TIMESTAMP NOT NULL
                    DEFAULT (strftime('%Y-%m-%dT%H:%M:%fZ','now') || '+00:00'),
    state_changed_at TIMESTAMP NULL,

    FOREIGN KEY (user_id) REFERENCES users(id) ON DELETE CASCADE,
    FOREIGN KEY (episode_id) REFERENCES episodes(id) ON DELETE CASCADE,
    UNIQUE(user_id, episode_id),
    CHECK (length(id) = 36),
    CHECK (source IN ('follow_new','follow_seed')),
    CHECK (state IN ('unread','read','saved','dismissed'))
);

CREATE INDEX IF NOT EXISTS idx_inbox_user_unread
    ON user_episode_inbox(user_id, delivered_at DESC)
    WHERE state = 'unread';

CREATE INDEX IF NOT EXISTS idx_inbox_user_all
    ON user_episode_inbox(user_id, delivered_at DESC);

CREATE INDEX IF NOT EXISTS idx_inbox_episode
    ON user_episode_inbox(episode_id);
```

**Notes:**

- Timestamps use the project's standard ISO-8601 format with explicit `+00:00`, *not* `CURRENT_TIMESTAMP`.
- `source` is intentionally narrow. Future sources (e.g. `recommendation`) can be added via migration.
- The partial index on `state='unread'` keeps the hot path (rendering an unread inbox) cheap even when `read`/`dismissed` rows accumulate.

### `inbox_dispatch_queue` — optional, deferred

A durable outbox table for fan-out crash recovery. **Not in v1.** Inline fan-out inside the publish transaction is acceptable at thestill's scale. If/when fan-out grows to thousands of rows per publish or runs in a separate worker, introduce this table without changing the API.

---

## Data Model

### `InboxEntry` (Pydantic)

```python
class InboxEntry(BaseModel):
    id: str                      # uuid4
    user_id: str
    episode_id: str
    source: Literal["follow_new", "follow_seed"]
    state: Literal["unread", "read", "saved", "dismissed"]
    delivered_at: datetime
    state_changed_at: Optional[datetime]
```

Lives at [thestill/models/inbox.py](../thestill/models/inbox.py).

### Composed read view: `InboxItem`

For UI/API responses, join `InboxEntry` with the underlying `Episode` and minimal `Podcast` fields. Defined in the same model file or as a TypedDict in the API serializer:

```python
class InboxItem(BaseModel):
    entry: InboxEntry
    episode: Episode          # existing model
    podcast: PodcastSummary   # subset: id, title, slug, image_url
```

---

## Service Layer Changes

### New: `InboxService`

Lives at [thestill/services/inbox_service.py](../thestill/services/inbox_service.py). Pattern matches existing `FollowerService` and `RefreshService` shapes.

```python
class InboxService:
    def __init__(
        self,
        inbox_repository: InboxRepository,
        follower_repository: PodcastFollowerRepository,
        config: AppConfig,
    ) -> None: ...

    # ── Write paths ─────────────────────────────────────────

    def fanout_on_publish(self, episode_id: str, *, conn=None) -> int:
        """Insert one inbox row per follower of this episode's podcast.
        Idempotent via UNIQUE(user_id, episode_id). Source = 'follow_new'.
        Called from the publish transition. Returns rows inserted."""

    def seed_on_follow(self, user_id: str, podcast_id: str) -> int:
        """Pick the most recent INBOX_SEED_ON_FOLLOW published episodes
        and deliver them with source='follow_seed'. Called from
        FollowerService.follow() after the follow row commits.
        Returns rows inserted (may be 0 if podcast has no published episodes)."""

    def mark_state(
        self, user_id: str, episode_id: str, state: str
    ) -> InboxEntry:
        """Update state + state_changed_at. Validates state enum."""

    # ── Read paths ──────────────────────────────────────────

    def list(
        self,
        user_id: str,
        *,
        state: Optional[str] = None,
        limit: int = 50,
        before: Optional[datetime] = None,
    ) -> List[InboxItem]:
        """Return paginated inbox items, newest first.
        If state is None, returns everything except 'dismissed'."""

    def unread_count(self, user_id: str) -> int: ...
```

### `FollowerService.follow` — add hook

After the existing `self._repository.create(...)` call at [thestill/services/follower_service.py:83](../thestill/services/follower_service.py#L83), call `self._inbox.seed_on_follow(user_id, podcast_id)`. Failure of seed must **not** roll back the follow — log and continue.

### `FollowerService.unfollow` — no change

Unfollow does not delete inbox rows. This is a deliberate product decision (see Core Behaviors §4).

### Repository: `InboxRepository`

Standard pattern: interface in [thestill/repositories/inbox_repository.py](../thestill/repositories/inbox_repository.py), SQLite impl in [thestill/repositories/sqlite_inbox_repository.py](../thestill/repositories/sqlite_inbox_repository.py). Mirrors the structure of `PodcastFollowerRepository` / `SQLitePodcastFollowerRepository`.

---

## Pipeline Integration Points

There are exactly **two** places the fan-out fires.

### 1. Publish on summarize-complete (the new event)

At the tail of the summarize task handler in [thestill/core/task_handlers.py](../thestill/core/task_handlers.py), after `summary_path` is committed to the episode row:

```python
# Inside the same transaction that finalizes the episode:
conn.execute(
    "UPDATE episodes SET published_at = ?, updated_at = ? WHERE id = ? "
    "AND published_at IS NULL",
    (now_iso(), now_iso(), episode_id),
)
if cursor.rowcount == 1:
    inbox_service.fanout_on_publish(episode_id, conn=conn)
```

The `AND published_at IS NULL` guard makes re-runs idempotent — a re-summarized episode does not re-deliver.

**Failure mode:** if fan-out raises mid-loop, the transaction rolls back and `published_at` is *not* set. The next pipeline retry will re-attempt cleanly. This is the right behavior.

**Performance note:** at expected scale (≤ tens of followers per podcast), inline fan-out adds milliseconds. Profile before optimizing. If outbox-based dispatch is later needed, `inbox_dispatch_queue` (described above) is the upgrade path.

### 2. Seed on follow

In `FollowerService.follow`, after the follow row commits. See [Service Layer Changes](#service-layer-changes).

### Explicitly out of scope

- **`thestill refresh` does not fan out.** Refresh only discovers RSS entries; it does not transition episodes to published.
- **Manual CLI publish.** Not in v1. The pipeline is the only path to `published_at`.
- **Admin-push to a specific user.** Not in v1; admin curation is being removed (see Transitional Behavior).

---

## API Changes

### New endpoints

| Method | Path | Description |
|--------|------|-------------|
| `GET`  | `/api/inbox` | List current user's inbox. Query: `state`, `limit`, `before` (cursor by `delivered_at`). Returns `InboxItem[]` in the standard envelope. |
| `GET`  | `/api/inbox/unread-count` | Lightweight unread count for badge rendering. |
| `POST` | `/api/inbox/{episode_id}/state` | Body: `{"state": "read"\|"saved"\|"dismissed"\|"unread"}`. Returns updated `InboxEntry`. 404 when no row exists. |
| `POST` | `/api/inbox/{episode_id}/read` | View-driven read tracking: transitions the row `unread → read` and nothing else — never clobbers `saved`/`dismissed`. Always 200 with `{"marked": bool}`; a missing row is a no-op (`marked: false`), so the episode page fires it blindly on every summary view. |

All endpoints require authentication. No admin-only inbox endpoints in v1.

### Existing endpoints

The current "list episodes from followed podcasts" endpoint (if present in [thestill/web/routes/](../thestill/web/routes/)) is **not** removed in v1. It becomes the "all episodes from podcasts I follow" archive view, complementary to the inbox. The frontend home page switches its primary list source from that endpoint to `/api/inbox`.

### Response envelope

Follows the standard envelope from [02-api-reference.md](02-api-reference.md). No deviations.

---

## Migration Strategy

### Schema migrations

1. **`episodes.published_at`** — `ALTER TABLE` adds a NULL column. Backfill in the same migration:

   ```sql
   UPDATE episodes
      SET published_at = COALESCE(updated_at, created_at)
    WHERE summary_path IS NOT NULL
      AND published_at IS NULL;
   ```

   This treats every existing episode that has been summarized as already-published, using its last-touched timestamp as the publish time. Episodes still in-flight stay NULL.
2. **`user_episode_inbox`** — `CREATE TABLE` with indexes.

Both run idempotently inside `_ensure_database_exists` in [thestill/repositories/sqlite_podcast_repository.py](../thestill/repositories/sqlite_podcast_repository.py), following the existing migration-block pattern.

### One-time data backfill

After schema migration, seed existing followers' inboxes so they don't see an empty inbox on first load:

```sql
-- Pseudo-SQL; real implementation as a Python migration step,
-- because we need uuid4() per row and INBOX_SEED_ON_FOLLOW from config.
INSERT INTO user_episode_inbox (id, user_id, episode_id, source, state, delivered_at)
SELECT
    <uuid4()>,
    f.user_id,
    e.id,
    'follow_seed',
    'unread',
    e.published_at
FROM podcast_followers f
JOIN episodes e ON e.podcast_id = f.podcast_id
WHERE e.published_at IS NOT NULL
  AND e.id IN (
      SELECT id FROM episodes e2
       WHERE e2.podcast_id = f.podcast_id AND e2.published_at IS NOT NULL
       ORDER BY e2.published_at DESC
       LIMIT INBOX_SEED_ON_FOLLOW
  )
ON CONFLICT (user_id, episode_id) DO NOTHING;
```

The `ON CONFLICT DO NOTHING` makes the backfill idempotent if re-run. The Python migration uses `strftime` ISO-8601 timestamps for `delivered_at` (not `CURRENT_TIMESTAMP`).

### Frontend migration

- Add inbox page (or repurpose the home page) at [thestill/web/frontend/src/pages/](../thestill/web/frontend/src/pages/).
- Update API client at [thestill/web/frontend/src/api/client.ts](../thestill/web/frontend/src/api/client.ts) and types at [thestill/web/frontend/src/api/types.ts](../thestill/web/frontend/src/api/types.ts).
- Surface unread-count badge in the layout.

### Rollback

If v1 needs to be reverted:

- The `published_at` column can stay (harmless when unused).
- The `user_episode_inbox` table can be dropped via migration.
- Frontend rolls back to pre-inbox routing.

No data loss. The existing "episodes from followed podcasts" join still works against the unchanged `episodes` and `podcast_followers` tables.

---

## Transitional Behavior (Cost-Constrained Mode)

**Context:** as of 2026-05-05, transcription is not fully automated. The operator (currently the project owner) manually triggers transcription for a small subset of episodes to control cost. The end-state goal is: refresh feeds → automatically transcribe → automatically deliver.

**The inbox model handles the transition for free.** No special "admin staging" state is required. The reasoning:

- An episode that has not been transcribed has `summary_path = NULL` → `published_at = NULL` → not in any inbox.
- Whether the reason is "automation hasn't gotten there yet" or "operator deliberately hasn't kicked it off" is the same data state.

**During the transition:**

- `thestill refresh` discovers episodes; they sit with `published_at = NULL`.
- Operator manually enqueues a subset for processing (current workflow).
- When summarize completes, the episode auto-publishes and fans out — same code path as the future automated mode.
- The admin podcast page (if/when built) shows everything in `episodes`, including not-yet-transcribed entries, for operator visibility.

**Crossing into automated mode** later only requires changing *what enqueues episodes for processing* — the data model and delivery logic are identical. No schema changes. No code rewrites.

**One persistent operator concern (not transitional): cost cap on first podcast-add.** When a user adds a brand-new podcast that has 500 historical episodes in its RSS, transcribing all 500 is unaffordable. This is solved by a config knob `BACKFILL_LIMIT_ON_PODCAST_ADD` (default e.g. `5`) used at the `thestill add` boundary. Older episodes stay as `discovered` rows; an explicit `thestill backfill --podcast-id X --limit N` command (future) can pull more on demand. **Not in this spec's scope** beyond noting that the inbox model does not block the backfill pattern from being added later.

---

## Naming Conventions

- **Schema/code:** `inbox` everywhere. Table `user_episode_inbox`, service `InboxService`, model `InboxEntry`.
- **User-facing label:** `"Inbox"`. Avoid `"Feed"` and `"Timeline"` — those carry passive-scroll connotations that mislead about per-user state and delivery semantics.
- **Verb:** `deliver`. The episode is *delivered* to a user's inbox. Not "pushed", not "published to feed". `delivered_at` on the row.
- **Source enum values:**
  - `follow_new` — delivered because the user follows the podcast and a new episode was published.
  - `follow_seed` — delivered as part of the on-follow seed, retroactively from the podcast's archive.
  - (Future) `recommendation` — out of scope for v1.
- **State enum values:** `unread`, `read`, `saved`, `dismissed`. `dismissed` is excluded from default list queries; it is *not* deletion (the row remains for audit/undo).

The Substack analogy is the dominant mental model. If a future contributor is confused about expected behavior, default to "what would Substack do?"

---

## Implementation Phases

### Phase 1 — Schema + service skeleton

- [ ] Migration: `episodes.published_at` column + index + backfill from `summary_path`.
- [ ] Migration: `user_episode_inbox` table + indexes.
- [ ] `InboxRepository` interface + SQLite implementation.
- [ ] `InboxService` with `fanout_on_publish`, `seed_on_follow`, `list`, `mark_state`, `unread_count`.
- [ ] Unit tests for repository and service (mirroring `tests/unit/repositories/` and `tests/unit/services/` patterns).

### Phase 2 — Pipeline integration

- [ ] Hook in summarize task handler: set `published_at` + call `fanout_on_publish` in the same transaction.
- [ ] Hook in `FollowerService.follow`: call `seed_on_follow` after follow row commits.
- [ ] Idempotency tests: re-summarize, re-follow, both no-op.
- [ ] One-time backfill script for existing followers (run once on first deploy).

### Phase 3 — API + frontend

- [ ] Routes: `GET /api/inbox`, `GET /api/inbox/unread-count`, `POST /api/inbox/{episode_id}/state`.
- [x] Route: `POST /api/inbox/{episode_id}/read` (guarded `unread → read`) + `useMarkInboxReadOnView` firing from the episode page once a summary is available (2026-07-07).
- [ ] Frontend page: Inbox view with read/save/dismiss actions and unread filter. *(Read/save/dismiss action buttons now planned as [spec #52](52-inbox-reader-overlay.md) Phase 2 — reader-overlay header.)*
- [ ] Layout badge: unread count.
- [ ] Cypress/E2E test for the follow → publish → delivered → mark-read flow.

### Phase 4 (optional, deferred) — Hardening

- [ ] `inbox_dispatch_queue` outbox for crash-resilient fan-out (only if profiling shows inline fan-out is a bottleneck).
- [ ] `episodes.retracted_at` for moderation/DMCA pulls.
- [ ] Observability: structured-log events `inbox.delivered`, `inbox.seeded`, `inbox.state_changed` with correlation IDs (`episode_id`, `user_id`).

---

## Open Questions

1. **Inbox vs. archive on the home page.** Should the home page *replace* the current "recent episodes from followed podcasts" view with the inbox, or show both side-by-side initially? Recommendation: replace, with a "Browse all" link to the archive view.
2. **What does "read" mean operationally?** Two reasonable definitions: (a) explicitly clicked the episode, (b) listened past some threshold (e.g., 30 seconds). v1 should pick (a) for simplicity; (b) can be layered later via a media-player hook into [thestill/web/frontend/src/components/](../thestill/web/frontend/src/components/) PlayerContext.
   **Resolved (2026-07-07):** a refinement of (a) — *viewed the episode page while a summary existed*, regardless of how the user navigated there. The gate on summary presence means an episode still working through the pipeline is never marked read by a premature click. Implemented as `POST /api/inbox/{episode_id}/read` (guarded `unread → read`, no-op without a row) fired by `useMarkInboxReadOnView` on the episode page; (b) remains a possible later refinement.
3. **`saved` vs `read` orthogonality.** Should `saved` be a separate axis (a flag) instead of a state, so an episode can be both `read` and `saved`? Probably yes, but introduces UI complexity. v1 treats them as mutually exclusive states; revisit if users complain.
4. **Should `dismissed` deliveries be counted somewhere for analytics?** Useful signal for "what kinds of episodes do users skip" recommendations later. Not blocking v1.
5. **Cross-device read state.** Trivially handled by the server-authoritative state column. No client-side reconciliation needed.

---

## Non-Goals

The following are explicitly out of scope for this spec, even though they are natural adjacent features:

- **Admin push of specific episodes to specific users.** The end-state architecture removes admin curation entirely; building admin-push would be transitional scaffolding that gets deleted later.
- **Multi-state visibility enum on episodes (`discovered` / `staged` / `published` / `hidden`).** A single `published_at TIMESTAMP NULL` is sufficient. The pipeline-completeness signal is the only gate.
- **Recommendation/discovery feed.** A separate "Discover" surface is its own product question; the inbox is strictly post-subscription delivery.
- **Cross-user social signals** (likes, comments, follower-of-follower). thestill is a 1-to-N podcast → subscriber graph, not an N-to-N social graph.
- **Inbox retention policies** (auto-archive after 30 days, etc.). Add later if inbox bloat becomes a real problem; for now, dismissed rows accumulate harmlessly under the partial index.
- **Backfill-on-podcast-add (`BACKFILL_LIMIT_ON_PODCAST_ADD`).** Mentioned in [Transitional Behavior](#transitional-behavior-cost-constrained-mode) as a related concern, but the implementation belongs in a separate spec covering podcast-add cost controls.
- **Outbox-based fan-out (`inbox_dispatch_queue`).** Inline fan-out is sufficient at current scale. Migrate later only if profiling demands it.

---

## References

- [13-multi-user-shared-podcasts.md](13-multi-user-shared-podcasts.md) — established the follow/unfollow model this spec builds on.
- [01-architecture.md](01-architecture.md) — layered architecture pattern (services → repositories → SQLite) followed throughout.
- [02-api-reference.md](02-api-reference.md) — response envelope and pagination conventions for the new inbox endpoints.
- [04-testing.md](04-testing.md) — test coverage targets and structure.

---

## Decision Log

| Date | Decision | Rationale |
|------|----------|-----------|
| 2026-05-05 | Use fan-out-on-write, not fan-out-on-read | Seed-on-follow is explicit state, not a derivation; delivery semantics are user-facing; scale doesn't force the choice. |
| 2026-05-05 | Single `published_at` column instead of `visibility` enum | Pipeline-completeness is a sufficient gate; multi-state enum was scaffolding for an admin role that won't exist in steady state. |
| 2026-05-05 | Unfollow does not retract delivered rows | Email/Substack semantics; "unsubscribe" stops future delivery, not past delivery. |
| 2026-05-05 | Default `INBOX_SEED_ON_FOLLOW = 2` | Enough to make the inbox non-empty after first follow, few enough to avoid drowning new followers in archive. Tunable via config. |
| 2026-05-05 | Name is `Inbox`, not `Feed` or `Timeline` | Triage UX with explicit per-item state matches Substack/email semantics, not Twitter passive-scroll semantics. |
| 2026-05-05 | No admin-push, no staged state | The end-state architecture removes admin curation; transitional cost-control is naturally expressed as "the pipeline hasn't run yet". |
