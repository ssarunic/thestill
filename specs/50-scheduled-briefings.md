# Scheduled Briefings

> **Status:** 🚧 Implemented — Phases 1–3 (2026-07-07); Phase 4 pending #34
> **Created:** 2026-07-07
> **Author:** Product & Engineering
> **Related:** [#36 per-user-digest-from-inbox](36-per-user-digest-from-inbox.md), [#48 refresh-feed-stage](48-refresh-feed-stage.md), [#33 narrated-digest](33-narrated-digest.md), [#34 briefing-audio-and-feeds](34-briefing-audio-and-feeds.md)

---

## Executive Summary

[#36](36-per-user-digest-from-inbox.md) built the per-user briefing state
machine (inbox-cursor selection, `user_briefings` rows, idempotency window)
but deliberately left generation operator-triggered, deferring auto-cadence
to "a separate spec" ([#36 Open Question 5](36-per-user-digest-from-inbox.md#L414)).
This is that spec.

The target model: **episodes flow through the pipeline continuously**
(refresh scheduler → download → … → summarize → inbox fan-out, all shipped),
and **each user's briefing is generated on their own schedule** — e.g. every
morning at 8:00, or weekly on Mondays — covering everything that landed in
their inbox since the previous briefing. Hour and frequency are per-user
settings, not a global cron.

The mechanism mirrors the [#48](48-refresh-feed-stage.md) refresh scheduler:
a materialized `next_run_at` per user, a cheap indexed due-query on a
daemon-thread tick, and cadence state advanced after each run. All selection
and idempotency logic stays in `BriefingService` — the scheduler only decides
*when* to call `generate_for_user`.

---

## Motivation

1. **The lazy trigger delivers the briefing too late.** Today a briefing is
   generated when the user opens `/inbox`
   (`GET /api/briefings/latest` → [briefing_service.py](../thestill/services/briefing_service.py)).
   Script generation — and, once [#34](34-briefing-audio-and-feeds.md) lands,
   audio rendering — happens while the user waits. The morning-ritual product
   promise is "it's ready when you wake up".
2. **"Morning" is personal.** A global cron hour is wrong for any multi-user
   deployment spanning timezones, and wrong even for a single user who wants
   6:30 instead of 8:00. The hour must be a user setting with a timezone.
3. **So is cadence.** A daily briefing suits heavy listeners; a weekly
   Monday digest suits light ones. The cursor semantics from #36 already
   make any cadence correct for free (a wider window just yields a longer
   briefing) — only the trigger is missing.

---

## Product Requirements

### User stories

| As a... | I want... | So that... |
|---|---|---|
| User | My briefing generated automatically at an hour I choose | It's ready when I wake up, not when I open the app |
| User | To choose daily or weekly (with a day-of-week) cadence | The briefing matches how often I actually listen |
| User | The schedule interpreted in my timezone | 8am means *my* 8am, including across DST changes |
| User | To turn scheduling off | The lazy on-open behavior from #36 remains available |
| Self-hoster | Scheduling to run inside the existing `thestill server` process | No external cron or new daemon to operate |

### Core behaviors

1. **Per-user schedule settings.** Each user may have at most one schedule:
   `frequency` (`daily` | `weekly`), `hour_local` (0–23), `weekday`
   (0=Monday…6=Sunday, required iff `weekly`), `timezone` (IANA name),
   `enabled`. No row (or `enabled = 0`) means no scheduled generation —
   the #36 lazy path is unaffected.
2. **Materialized due-time.** `next_run_at` (UTC) is computed from the
   settings and stored on the row, indexed. The scheduler's due-query is
   `enabled = 1 AND next_run_at <= now` — a single indexed range scan,
   same shape as `next_refresh_at` in [#48](48-refresh-feed-stage.md).
3. **A scheduled run is just `generate_for_user`.** The scheduler calls
   `BriefingService.generate_for_user(user_id)` — cursor math, empty-window
   handling, and the `BRIEFING_MIN_INTERVAL` throttle apply unchanged.
   An empty inbox window returns `None`: no briefing row, no filler, and
   `next_run_at` still advances (skip, don't retry).
4. **Advance after run, to the next *future* occurrence.** After each due
   run (success, empty-skip, or failure), `next_run_at` moves to the next
   occurrence of `hour_local` (and `weekday`, if weekly) in the user's
   timezone, converted to UTC. A server that was down at 8am fires **once**
   on catch-up when it comes back — the widened cursor makes a single
   catch-up run correct — then advances to the future slot. Never replays
   N missed slots.
5. **Timezone-correct, DST-safe.** Occurrence computation happens in the
   user's IANA zone via `zoneinfo`, then converts to UTC for storage
   (ISO-8601 with explicit `+00:00`, per house rules and FM-3 in
   [#42](42-robustness-and-failure-mode-hardening.md)). A nonexistent local
   time (spring-forward gap) resolves to the post-transition instant; an
   ambiguous one (fall-back) takes the first occurrence (`fold=0`).
6. **Per-user failure isolation.** One user's generation error (FM-1) is
   logged with `user_id` + `exc_info`, `next_run_at` still advances, and
   the tick continues to the next due user. A failing LLM call must not
   stall the whole fleet's mornings, and must not burn retries every tick.
7. **Lazy + scheduled coexist.** If the user opened `/inbox` at 7:30 and
   lazily generated, the 8:00 scheduled run lands inside
   `BRIEFING_MIN_INTERVAL` and returns the existing briefing — no
   double-generation, no 30-minute sliver briefing.

### Non-Goals

- **Multiple schedules per user** (e.g. daily + a weekly roundup). One row
  per user; the schema's `frequency` enum leaves room to extend.
- **Sub-daily cadence.** Nothing below `daily` in v1; the 6h throttle would
  fight it anyway.
- **Delivery channels** (email, push). This spec ends at "the briefing row
  and script exist by the scheduled hour". Notification is its own spec:
  [#51 briefing-email-delivery](51-briefing-email-delivery.md).
- **Quiet periods / vacation mode.** `enabled = 0` is the pause button.
- **Automatic timezone detection server-side.** The frontend sends the
  browser's IANA zone as the *default* when the user first enables
  scheduling; the stored setting is what counts.

---

## Architecture Overview

```
┌────────────────────────────────────────────────────────────────────┐
│  BriefingScheduler (daemon thread in `thestill server`, mirrors    │
│  RefreshScheduler from #48)                                        │
│                                                                    │
│  every tick_seconds:                                               │
│    due = schedules WHERE enabled AND next_run_at <= now            │
│          ORDER BY next_run_at LIMIT max_per_tick                   │
│    for each due user:                                              │
│      1. claim: UPDATE … SET next_run_at = <next occurrence>        │
│                WHERE user_id = ? AND next_run_at = <claimed value> │
│      2. BriefingService.generate_for_user(user_id)                 │
│         (cursor, throttle, empty-window logic unchanged — #36)     │
│      3. (#33/#34 interlock) chain narration / audio render          │
└────────────────────────────────────────────────────────────────────┘
                              │
┌────────────────────────────────────────────────────────────────────┐
│  user_briefing_schedules (new table)                               │
│  frequency · hour_local · weekday · timezone · enabled ·           │
│  next_run_at (UTC, indexed)                                        │
└────────────────────────────────────────────────────────────────────┘
                              │
┌────────────────────────────────────────────────────────────────────┐
│  API: GET/PUT /api/briefings/schedule   Frontend: settings section │
└────────────────────────────────────────────────────────────────────┘
```

The claim-by-conditional-UPDATE in step 1 (advance *before* generating,
guarded on the value we read) makes the tick safe under multiple server
instances on Postgres ([#44](44-postgres-migration.md)): only one instance
wins the UPDATE for a given slot. It also guarantees a crashed generation
doesn't re-fire every tick (behavior 6). On SQLite the single-process
server makes this a no-op formality.

---

## Database Schema Changes

### `user_briefing_schedules` — new table

```sql
CREATE TABLE IF NOT EXISTS user_briefing_schedules (
    user_id     TEXT PRIMARY KEY NOT NULL,
    frequency   TEXT NOT NULL DEFAULT 'daily',
    hour_local  INTEGER NOT NULL DEFAULT 8,
    weekday     INTEGER NULL,                 -- 0=Mon … 6=Sun; required iff weekly
    timezone    TEXT NOT NULL,                -- IANA name, e.g. 'Europe/Zagreb'
    enabled     INTEGER NOT NULL DEFAULT 1,
    next_run_at TIMESTAMP NULL,               -- UTC; NULL when disabled
    created_at  TIMESTAMP NOT NULL
                DEFAULT (strftime('%Y-%m-%dT%H:%M:%fZ','now') || '+00:00'),
    updated_at  TIMESTAMP NOT NULL
                DEFAULT (strftime('%Y-%m-%dT%H:%M:%fZ','now') || '+00:00'),

    FOREIGN KEY (user_id) REFERENCES users(id) ON DELETE CASCADE,
    CHECK (frequency IN ('daily', 'weekly')),
    CHECK (hour_local BETWEEN 0 AND 23),
    CHECK (weekday IS NULL OR weekday BETWEEN 0 AND 6),
    CHECK ((frequency = 'weekly') = (weekday IS NOT NULL)),
    CHECK (enabled IN (0, 1))
);

CREATE INDEX IF NOT EXISTS idx_briefing_schedules_due
    ON user_briefing_schedules(next_run_at)
    WHERE enabled = 1 AND next_run_at IS NOT NULL;
```

Notes:

- One row per user (`user_id` is the PK). `PUT /api/briefings/schedule`
  upserts.
- `next_run_at` is set to `NULL` on disable and recomputed on enable or
  any settings change — the partial index keeps the due-scan tight, the
  same parking idiom as terminally-failed feeds in #48.
- Mirror DDL goes into `postgres_schema.py` and an Alembic migration
  (`weekday` check via a table constraint; partial index syntax is shared).
- `timezone` is validated at the API layer against `zoneinfo` — an invalid
  zone must be rejected at write time, not discovered at 8am (FM-4: no
  silent degradation to UTC).

### `user_briefings` — no change

The cursor state machine from #36 is untouched.

---

## Service Layer Changes

### New: `BriefingScheduler`

Lives at `thestill/core/briefing_scheduler.py` (new). Pattern-matches
`RefreshScheduler` ([refresh_scheduler.py](../thestill/core/refresh_scheduler.py)):
daemon thread, `tick_seconds` granularity, bounded work per tick,
`start()`/`stop()` wired into the server lifespan next to the refresh
scheduler in [web/app.py](../thestill/web/app.py).

Unlike #48 it does **not** enqueue queue tasks in v1: generation is a
direct `BriefingService.generate_for_user` call on the tick thread.
Briefing generation is one LLM-light rendering pass over already-written
summaries (the heavy lifting happened in the pipeline), and due-fleet
size is the user count, not the feed count. If narration/audio chaining
(#34) makes runs heavy, promotion to a `GENERATE_BRIEFING` queue stage is
the escape hatch — noted in Open Questions.

### New: cadence math (pure function)

```python
def next_occurrence(
    *, frequency: str, hour_local: int, weekday: Optional[int],
    tz: ZoneInfo, after: datetime,
) -> datetime:
    """Next occurrence strictly after `after`, computed in `tz`,
    returned in UTC."""
```

Pure and clock-free so unit tests can pin DST transitions, weekly
wrap-around, and month/year boundaries without freezegun gymnastics.

### `BriefingService` — no shape change

`generate_for_user` already has the right contract (throttle, `None` on
empty window). The scheduler is a new caller, not a new code path.

---

## API Changes

| Method | Path | Description |
|---|---|---|
| `GET` | `/api/briefings/schedule` | Current user's schedule. 404 if never configured. |
| `PUT` | `/api/briefings/schedule` | Upsert `{frequency, hour_local, weekday?, timezone, enabled}`. Recomputes `next_run_at`; echoes it back so the UI can show "Next briefing: Mon 08:00". |

Validation: `weekday` required iff `frequency = 'weekly'`; `timezone`
must resolve in `zoneinfo`; `hour_local` 0–23. `POST /api/briefings/generate`
stays unexposed (#36 decision unchanged) — the schedule *is* the
self-serve generation surface, rate-limited by design.

### Frontend

- Settings section (gear on the inbox briefing card, or the account
  settings page): enable toggle, time picker, daily/weekly + weekday
  picker, timezone (defaulted from
  `Intl.DateTimeFormat().resolvedOptions().timeZone`).
- Show computed "Next briefing: …" from the `PUT` response.

---

## Configuration

```bash
BRIEFING_SCHEDULER_ENABLED=true    # master switch, mirrors REFRESH_SCHEDULER_ENABLED
BRIEFING_SCHEDULER_TICK_SECONDS=60 # scheduling granularity, not cadence
BRIEFING_SCHEDULER_MAX_PER_TICK=50 # due-fleet bound per tick
```

Test/rehearsal servers set `BRIEFING_SCHEDULER_ENABLED=false` alongside
`REFRESH_SCHEDULER_ENABLED=false` (same double-run hazard class).

---

## Implementation Phases

### Phase 1 — Schema + cadence math ✅ (2026-07-07)

- [x] `user_briefing_schedules` DDL (SQLite `_ensure_database_exists`,
      `postgres_schema.py`, Alembic migration `0002`).
- [x] `BriefingScheduleRepository` interface + SQLite/Postgres impls
      (get, upsert, due-scan, conditional-claim update).
- [x] `next_occurrence()` with DST/weekly/boundary unit tests
      (`utils/briefing_cadence.py`).

### Phase 2 — Scheduler loop ✅ (2026-07-07)

- [x] `BriefingScheduler` daemon thread (`core/briefing_scheduler.py`);
      lifespan wiring in `web/app.py` behind `BRIEFING_SCHEDULER_ENABLED`.
- [x] Claim-then-generate flow with per-user error isolation (FM-1) and
      structured logs (`user_id`, `briefing_id`, `window`, `outcome`).
- [x] Tick tests: two users, different hours → each fires once at its own
      slot; downed-server catch-up fires once, not N times
      (`tests/unit/core/test_briefing_scheduler.py`).

### Phase 3 — API + frontend ✅ (2026-07-07)

- [x] `GET`/`PUT /api/briefings/schedule` routes + validation
      (`web/routes/api_briefings.py`).
- [x] Settings UI with browser-timezone default and "Next briefing" echo
      (`components/BriefingScheduleSettings.tsx`).
- [ ] E2E: enable schedule → advance clock past slot → briefing card
      present on inbox open without lazy generation. (Deferred: needs a
      live-server harness with clock control; tick-level coverage exists.)

### Phase 4 — Ready-by-morning interlock (#33/#34)

- [ ] Scheduled runs chain narration (and audio, once #34 lands) after
      script generation, so the listenable artifact — not just the
      script — exists by `hour_local`.

---

## Open Questions

1. **Should generation move onto the task queue?** v1 generates on the
   tick thread (see Service Layer rationale). If #34 audio chaining makes
   a run take minutes, promote to a `GENERATE_BRIEFING` queue stage and
   let the scheduler enqueue instead — the claim semantics already fit.
2. **Default schedule on signup?** v1: no row, scheduling is opt-in, lazy
   generation remains the default experience. Revisit once #34 audio makes
   the scheduled path clearly superior.
3. **Staleness guard on catch-up.** If the server was down for 10 days,
   the catch-up briefing covers 10 days of inbox — by design (#36
   "missed days compound"). Is there a window width beyond which we
   should clip (e.g. cap at 2× cadence) or annotate the briefing? Punted;
   the cursor keeps it correct, only length suffers.

---

## References

- [#36 per-user-digest-from-inbox](36-per-user-digest-from-inbox.md) —
  the state machine this spec adds a trigger to; resolves its Open
  Question 5.
- [#48 refresh-feed-stage](48-refresh-feed-stage.md) — the materialized
  due-time + tick-loop pattern this scheduler mirrors.
- [#42 robustness-and-failure-mode-hardening](42-robustness-and-failure-mode-hardening.md) —
  FM-1 (per-item isolation), FM-3 (mixed-tz), FM-4 (silent degradation)
  constraints honored here.
- [#33 narrated-digest](33-narrated-digest.md) /
  [#34 briefing-audio-and-feeds](34-briefing-audio-and-feeds.md) — the
  artifacts that "ready by morning" ultimately means.
- [#51 briefing-email-delivery](51-briefing-email-delivery.md) — the
  delivery follow-up: emails the scheduled briefing with send-once
  delivery records.

---

## Decision Log

| Date | Decision | Rationale |
|---|---|---|
| 2026-07-07 | Per-user schedule row with materialized `next_run_at`, not a global cron hour | "8am" must be per-user + per-timezone; due-scan stays one indexed query (proven pattern from #48) |
| 2026-07-07 | Advance-before-generate via conditional UPDATE | Multi-instance safe on Postgres; a crashing generation can't re-fire every tick |
| 2026-07-07 | Catch-up fires once, never replays missed slots | #36 cursor semantics make one widened briefing correct; N replays would be N near-empty briefings |
| 2026-07-07 | Scheduled run honors `BRIEFING_MIN_INTERVAL` | Lazy + scheduled coexistence for free; prevents sliver briefings after a 7:30 manual open |
| 2026-07-07 | Generation on the tick thread in v1, queue stage as escape hatch | Fleet = user count; rendering is cheap until #34 audio; avoids premature queue plumbing |
| 2026-07-07 | Scheduling is opt-in (no default row) | Preserves #36 lazy behavior as the zero-config default; no surprise LLM spend |
