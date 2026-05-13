# Per-User Briefing from Inbox

> **Status:** 📝 Draft
> **Created:** 2026-05-08
> **Updated:** 2026-05-08
> **Author:** Product & Engineering
> **Related:** [#29 per-user-inbox-fanout](29-per-user-inbox-fanout.md), [#33 narrated-briefing](33-narrated-briefing.md), [#34 briefing-audio-and-feeds](34-briefing-audio-and-feeds.md)

---

## Executive Summary

Now that [#29](29-per-user-inbox-fanout.md) has shipped, the morning briefing
should select from each user's **inbox**, not from a global "recent episodes"
window. Same generator, same script schema, same audio pipeline — only the
selection input changes. This spec covers the small wiring change that
unblocks the per-user briefing path explicitly anticipated by [#33 §87,
§490, §515](33-narrated-briefing.md) and [#34 §78, O2](34-briefing-audio-and-feeds.md).

The unit of work is *what landed in my inbox since my last briefing*. The
output surfaces as a "Today's briefing" card at the top of `/inbox` (per
[#33 §490](33-narrated-briefing.md#L490)). Audio becomes per-user as a
natural consequence (per [#34 §78](34-briefing-audio-and-feeds.md#L78)).

**Mental model:** the inbox is the substrate; the briefing is a recurring
read-out of *the part of the inbox the user hasn't been read out yet*.

---

## Motivation

`thestill briefing` today selects episodes via
[briefing_selector.py:35](../thestill/services/briefing_selector.py#L35) using a
global `since_days` window — every user gets the same selection. Three
problems:

1. **Wrong scope.** The Inbox is now the per-user truth. A briefing pulled from
   the global window can include episodes that never reached my inbox (I
   don't follow that podcast) and miss episodes that did (I followed yesterday
   and the seed delivered them).
2. **No "since I last looked" semantics.** `since_days=7` is a moving window,
   not a delivery cursor. Two briefings 36 hours apart double-cover the same
   episodes; a briefing skipped on vacation under-covers what's piled up.
3. **Audio can't go per-user.** [#34](34-briefing-audio-and-feeds.md)
   deferred per-user audio behind exactly this gate ("Per-user audio without
   per-user *content* doesn't pay for itself", [#34 O2](34-briefing-audio-and-feeds.md#L690)).

---

## Product Requirements

### User stories

| As a... | I want... | So that... |
|---|---|---|
| User | A briefing that covers what arrived in *my* inbox since my last briefing | I'm not re-read episodes I already heard or shown episodes from podcasts I don't follow |
| User | A "Today's briefing" card at the top of my inbox | The morning ritual lives where I already triage |
| User | Mark a briefing as listened so the next one starts from a fresh cursor | I don't re-cover ground |
| User | An empty-state message when nothing new has landed | The system feels honest, not scrambling for filler |
| Self-hoster | Continue running `thestill briefing` from cron | The CLI workflow doesn't break |

### Core behaviors

1. **Selection is per-user, inbox-bounded.** Candidate set =
   `InboxService.list(user_id, since=last_briefing_at)` filtered to inbox
   `state IN ('unread','saved')` (dismissed and read are excluded).
2. **Cursor is `last_briefing_at`.** Stored on a new `user_briefings` table
   (one row per generated briefing per user). Defaults: epoch on first run.
3. **Re-runs are idempotent within a window.** A second `briefing` call within
   `BRIEFING_MIN_INTERVAL` (default 6h) returns the same briefing rather
   than generating a new one. Forces explicit `--force` to override.
4. **No retroactive coverage.** A briefing covers exactly the inbox items
   delivered between `last_briefing_at` and `now()`. Missed days compound
   into a longer briefing rather than splitting.
5. **CLI compatibility.** `thestill briefing` keeps working. Without
   `--user-id`, it iterates over every user with at least one unbriefed
   inbox row; with `--user-id`, it generates only that user's briefing.
6. **Inbox card is read-only delivery.** Clicking "Today's briefing" routes
   to the briefing detail (or audio player); generation is server-driven,
   not on click.

### Non-Goals

- Briefing personalization beyond inbox membership (no topic preferences,
  no priority weighting). Punted to a later spec; the inbox subset is
  already a meaningful personalization signal.
- Background scheduler for "auto-generate at 7 AM local time". Generation
  stays operator-triggered (CLI / future cron) for v1.
- Cross-device read-state for the briefing itself. The
  `user_briefings.listened_at` column carries it; client just renders.
- New surface for "browse all my past briefings" — that already exists in
  [#34 §125](34-briefing-audio-and-feeds.md#L125) via the personal feed.

---

## Architecture Overview

```
┌─────────────────────────────────────────────────────────────────┐
│  CLI: thestill briefing [--user-id X] [--force]                   │
└─────────────────────────────────────────────────────────────────┘
                              │
┌─────────────────────────────────────────────────────────────────┐
│  Service: BriefingService                                       │
│    .generate_for_user(user_id, *, force=False) → Briefing       │
│    .latest_for_user(user_id) → Briefing | None                  │
│    .mark_listened(user_id, briefing_id)                         │
│                                                                 │
│    1. cursor = last_briefing_at(user_id) or epoch               │
│    2. inbox = InboxService.list(user_id, since=cursor,          │
│                                 state in {unread, saved})       │
│    3. if inbox empty → return None (or "nothing new" briefing)  │
│    4. script = BriefingGenerator.generate(inbox.episodes, …)      │
│    5. persist user_briefings row + script artifact              │
│    6. (optional / spec #34) render audio, persist briefing_audio│
└─────────────────────────────────────────────────────────────────┘
                              │
┌─────────────────────────────────────────────────────────────────┐
│  Repositories: BriefingRepository (new) + reuse InboxRepository │
└─────────────────────────────────────────────────────────────────┘
                              │
┌─────────────────────────────────────────────────────────────────┐
│  Frontend: /inbox top-of-page card (spec #33 §490)              │
│    - "Today's briefing — N episodes from your inbox"            │
│    - Listen / Read links                                        │
└─────────────────────────────────────────────────────────────────┘
```

The existing `BriefingGenerator` is unchanged: it accepts a list of
`BriefingEpisodeInfo` and emits the [#33](33-narrated-briefing.md) script.
The only new piece is the *selector* on the inbox.

---

## Database Schema Changes

### `user_briefings` — new table

```sql
CREATE TABLE IF NOT EXISTS user_briefings (
    id              TEXT PRIMARY KEY NOT NULL,
    user_id         TEXT NOT NULL,
    cursor_from     TIMESTAMP NOT NULL,          -- inclusive lower bound
    cursor_to       TIMESTAMP NOT NULL,          -- exclusive upper bound (= now() at gen time)
    script_path     TEXT NOT NULL,               -- path to the rendered script (#33)
    audio_path      TEXT NULL,                   -- nullable; populated by #34 pipeline
    episode_count   INTEGER NOT NULL,
    created_at      TIMESTAMP NOT NULL
                    DEFAULT (strftime('%Y-%m-%dT%H:%M:%fZ','now') || '+00:00'),
    listened_at     TIMESTAMP NULL,

    FOREIGN KEY (user_id) REFERENCES users(id) ON DELETE CASCADE,
    CHECK (length(id) = 36),
    CHECK (cursor_to > cursor_from),
    CHECK (episode_count >= 0)
);

CREATE INDEX IF NOT EXISTS idx_user_briefings_user_recent
    ON user_briefings(user_id, created_at DESC);
```

Notes:

- `cursor_from` is the previous briefing's `cursor_to`, or epoch on first
  run. Storing both ends makes "what episodes did this briefing cover"
  reproducible without joining back to the inbox at the same moment in
  time.
- `audio_path` is intentionally nullable so the audio pipeline (spec #34)
  can run lazily / out-of-band without blocking script generation.
- ISO-8601 with explicit `+00:00`, matching the rest of the project.

### `user_episode_inbox` — no schema change

The existing `delivered_at` column is the cursor key. No migration needed.

---

## Data Model

```python
class Briefing(BaseModel):
    id: str                          # uuid4
    user_id: str
    cursor_from: datetime
    cursor_to: datetime
    script_path: Path
    audio_path: Optional[Path]
    episode_count: int
    created_at: datetime
    listened_at: Optional[datetime]
```

Lives at [thestill/models/briefing.py](../thestill/models/briefing.py)
(new).

---

## Service Layer Changes

### New: `BriefingService`

Lives at [thestill/services/briefing_service.py](../thestill/services/briefing_service.py)
(new). Pattern matches `InboxService` from [#29](29-per-user-inbox-fanout.md).

```python
class BriefingService:
    def __init__(
        self,
        briefing_repository: BriefingRepository,
        inbox_service: InboxService,
        briefing_generator: BriefingGenerator,
        config: AppConfig,
    ) -> None: ...

    def generate_for_user(
        self,
        user_id: str,
        *,
        force: bool = False,
    ) -> Optional[Briefing]:
        """Generate a briefing covering [last_cursor, now()].
        Returns None if no eligible inbox items and the caller should
        treat this as a no-op.

        Honors BRIEFING_MIN_INTERVAL: a recent briefing is returned
        unchanged unless force=True.
        """

    def generate_for_all_users(
        self,
        *,
        force: bool = False,
    ) -> List[Briefing]:
        """Iterate over users with unbriefed inbox rows."""

    def latest_for_user(self, user_id: str) -> Optional[Briefing]: ...

    def mark_listened(self, user_id: str, briefing_id: str) -> Briefing: ...
```

### `BriefingGenerator` — no shape change

`BriefingGenerator.generate()` already takes a list of episode info
([briefing_generator.py:77](../thestill/services/briefing_generator.py#L77)).
The new selector feeds it directly; the generator does not need to know
about users or inboxes.

### `BriefingEpisodeSelector` — keep as fallback

[briefing_selector.py:75](../thestill/services/briefing_selector.py#L75) stays
for the global / admin path (e.g., a self-hoster running single-user mode
with no follows yet). New code paths go through `BriefingService`.

---

## Pipeline Integration Points

### 1. CLI: `thestill briefing`

Behavior changes:

- **Without `--user-id`:** delegate to
  `BriefingService.generate_for_all_users()`.
- **With `--user-id X`:** delegate to `BriefingService.generate_for_user(X)`.
- **Existing flags** (`--since`, `--ready-only`, `--no-limit`, `--yes`)
  retained; `--since` now overrides `cursor_from` with an absolute window
  for ad-hoc runs.

Single-user mode (no users in DB beyond `local@thestill.me`) shortcuts
to the existing global selector path so self-hosters don't lose the
behavior they had on day one.

### 2. Inbox card

Frontend: when the user opens `/inbox`, fetch
`GET /api/briefings/latest`. If a briefing exists and has unread items,
render a card at the top of the list:

```
┌─────────────────────────────────────────────────────────┐
│  Today's briefing                                       │
│  N episodes • generated 2 hours ago                     │
│  [▶ Listen 12:30]  [Read script]                        │
└─────────────────────────────────────────────────────────┘
```

Card disappears when `listened_at` is set or when the briefing's
`cursor_to` is older than the next generation window.

### 3. Web fan-out hook (no change)

The publish hook from [#29 §Pipeline Integration Points](29-per-user-inbox-fanout.md#pipeline-integration-points)
keeps fanning out as before. The briefing is generated *from* the inbox,
not *into* it.

---

## API Changes

### New endpoints

| Method | Path | Description |
|---|---|---|
| `GET`  | `/api/briefings/latest` | Most recent briefing for the current user. 404 if none. |
| `GET`  | `/api/briefings/{briefing_id}` | Specific briefing (script + audio metadata). |
| `POST` | `/api/briefings/{briefing_id}/listened` | Mark listened. Idempotent. |
| `GET`  | `/api/briefings` | Paginated history (mirrors `/api/inbox` pagination). |

`POST /api/briefings/generate` is **deliberately not exposed** in v1.
Generation is operator-triggered (CLI). Self-serve generation goes
behind feature work that builds rate limits + cost controls.

### Existing endpoints

- `/api/inbox` is unchanged.
- `/api/briefings/...` (the legacy global briefing endpoint, if present at
  [thestill/web/routes/api_briefings.py](../thestill/web/routes/api_briefings.py))
  is kept for one release as the fallback for single-user mode, then
  deprecated. Not in v1.

---

## Migration Strategy

1. **Schema:** add `user_briefings` table + indexes inside the existing
   `_ensure_database_exists` migration block at
   [thestill/repositories/sqlite_podcast_repository.py](../thestill/repositories/sqlite_podcast_repository.py).
2. **No data backfill needed.** Pre-existing global briefings under
   [data/briefings/](../data/briefings/) stay where they are; the new
   per-user briefings live under `data/briefings/<user_id>/<briefing_id>/`.
3. **First run per user starts from epoch.** Empty `user_briefings`
   means `cursor_from = epoch`, so the first briefing covers the whole
   inbox. Operator can `--since 24h` to clip on first run if that's
   noisy.
4. **Frontend:** add the inbox card + briefing detail route.
5. **Rollback:** dropping `user_briefings` and restoring the old
   `thestill briefing` CLI is a single migration. No data loss.

---

## Naming Conventions

- **Schema/code:** `briefing` for the per-user object; `briefing` for the
  legacy global object (kept until removed). Table `user_briefings`,
  service `BriefingService`, model `Briefing`.
- **User-facing label:** "Today's briefing" / "Briefing". `Briefing` is
  retired from the UI on this work.
- **Cursor field name:** `cursor_from` / `cursor_to`, not `since` /
  `until`. The latter pair reads like a query filter; the former reads
  as state.

---

## Implementation Phases

### Phase 1 — Service skeleton

- [ ] Migration: `user_briefings` table.
- [ ] `BriefingRepository` interface + SQLite impl.
- [ ] `BriefingService` with `generate_for_user`, `latest_for_user`,
      `mark_listened`.
- [ ] Wire `BriefingService.from_config` into the existing config-driven
      builder used by web app + CLI (mirrors `InboxService.from_config`).
- [ ] Unit tests: cursor math, idempotency window, empty-inbox path.

### Phase 2 — CLI integration

- [ ] `thestill briefing` switches to `BriefingService.generate_for_all_users`.
- [ ] `--user-id` flag added.
- [ ] Single-user-mode shortcut: skip the new path if there's only one
      user and zero followed podcasts (preserves day-one self-host
      behavior).
- [ ] Integration test: follow → publish → briefing produces a per-user
      briefing covering exactly the new inbox rows.

### Phase 3 — API + frontend

- [ ] Routes: `GET /api/briefings/latest`, `/api/briefings/{id}`,
      `POST /api/briefings/{id}/listened`, `GET /api/briefings`.
- [ ] Inbox top-card component (placement matches [#33 §490](33-narrated-briefing.md#L490)).
- [ ] Briefing detail route: render script (markdown) + audio player
      when `audio_path` is set.
- [ ] E2E test: empty inbox → no card; populated inbox → card → read →
      mark listened → card collapses.

### Phase 4 — Audio per-user (interlocks with [#34](34-briefing-audio-and-feeds.md))

- [ ] Audio render trigger fires per `user_briefings` row instead of
      per global briefing.
- [ ] Personal feed (#34 §125) sources from `user_briefings` filtered
      to the requesting user.
- [ ] Update [#34 O2](34-briefing-audio-and-feeds.md#L690) to mark the
      gate as satisfied.

---

## Open Questions

1. **What's "since I last looked" when the user has never opened the
   inbox?** Treat first briefing as covering the seed-on-follow rows
   plus everything since (i.e., `cursor_from = epoch`). The seed is
   small by design ([#29 default 2 per podcast](29-per-user-inbox-fanout.md#L70))
   so this isn't catastrophic.
2. **Should `dismissed` items count toward "covered"?** No — they're a
   negative signal. Excluding them from selection (already in §Core
   Behaviors #1) avoids re-surfacing them.
3. **Does the inbox card show audio progress / resume state?** v1 no;
   it's a delivery card. Resume state belongs to the player, which the
   floating-player work in [#22](22-floating-media-player.md) already
   tracks.
4. **What if two users follow the same podcast?** Their briefings
   converge on the same source episodes but diverge on cursor and read
   state. Expected.
5. **Generation cadence in v1.** Operator-triggered only. Auto-cadence
   ("every morning at 7 AM local time") is a separate spec — needs cron
   scheduling, timezone awareness, and rate-limiting.

---

## Non-Goals

- **Topic personalization, "likes you might like" weighting.** Inbox
  membership is the only personalization in v1.
- **Multi-recipient briefings** ("send my partner my briefing").
  Briefings are 1:1 with users.
- **Cross-day briefing rollup** (combining 3 missed days into one). v1
  generates per-cadence; the cursor naturally widens if the user skips a
  day, which produces the right effect for free.
- **Mid-briefing edits** ("swap this episode out").
- **Auto-generation scheduler.** Out of scope; CLI-triggered for v1.
- **Migration of historical global briefings** under [data/briefings/](../data/briefings/)
  into per-user briefings. They stay where they are; the new flow
  starts from now.

---

## References

- [#29 per-user-inbox-fanout](29-per-user-inbox-fanout.md) — supplies
  the per-user inbox rows this spec selects from. Shipped.
- [#33 narrated-briefing](33-narrated-briefing.md) — defines the script
  schema and inbox-card placement; this spec wires the per-user
  selection into the same generator.
- [#34 briefing-audio-and-feeds](34-briefing-audio-and-feeds.md) —
  audio rendering + personal feed. Per-user audio is unblocked by
  this spec (§Phase 4).
- [#22 floating-media-player](22-floating-media-player.md) — the
  player surface that briefing audio plays through.

---

## Decision Log

| Date | Decision | Rationale |
|---|---|---|
| 2026-05-08 | Briefings live next to the inbox, not in a separate "Briefings" tab | Reinforces the mental model: briefing = readout of the inbox subset. Two surfaces = two competing concepts. |
| 2026-05-08 | Cursor stored as (`cursor_from`, `cursor_to`) on the briefing row | Reproducibility without time-travel queries against the inbox; future "rebuild this briefing" is straightforward. |
| 2026-05-08 | Operator-triggered generation in v1 | Adds the wiring without committing to a cron / timezone / rate-limit story; that's a follow-up. |
| 2026-05-08 | Keep global `BriefingEpisodeSelector` as single-user-mode fallback | Avoids breaking day-one self-host UX where no follows exist yet. |
| 2026-05-08 | Briefings are per-user 1:1 (no shared briefings) | Keeps content / audio / cursor / read-state on a single owner; no co-edit semantics needed. |
