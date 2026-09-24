# Scheduled-Only Briefings

> **Status:** ✅ Implemented (2026-09-24)
> **Created:** 2026-09-24
> **Author:** Product & Engineering
> **Related:** [#36 per-user-digest-from-inbox](36-per-user-digest-from-inbox.md), [#50 scheduled-briefings](50-scheduled-briefings.md), [#55 briefing-readiness-gate](55-briefing-readiness-gate.md)

---

## Executive Summary

A briefing is an edition, not a search result. [#36](36-per-user-digest-from-inbox.md)
made the inbox open the trigger: every `GET /api/briefings/latest` cut a new
briefing whenever the 6-hour throttle had lapsed and anything new sat past
the cursor. [#50](50-scheduled-briefings.md) added a per-user slot but kept
the lazy trigger alongside it ("lazy + scheduled coexist"), and shipped
scheduling as opt-in.

The result on 2026-09-24: a briefing containing one hour-old episode. The
user had opened the inbox the evening before, which cut an edition and moved
the cursor, so the morning open covered only the overnight sliver. The window
was defined by when the reader showed up, not by the clock.

This spec flips the default. **The schedule slot is the only automatic
trigger.** Opening the inbox reads the latest edition and never generates.
Every user gets a daily 08:00 schedule in their browser timezone the first
time they open the inbox. "Generate now" (`?force=true`) stays as the
explicit manual override.

## Problem

Three things go wrong when a reader-triggered cut drives an editorial
product:

1. **The window is defined by behaviour, not time.** Two visits a day give
   two thin editions; a four-day gap gives one enormous one. The 6-hour
   throttle ([#36](36-per-user-digest-from-inbox.md)) and the 24-hour
   empty-window reuse were both patches on this symptom.
2. **Invisible spend.** Opening a page silently runs an LLM, and narration
   if the user taps play. Nothing was asked for.
3. **Non-reproducible.** Two users with identical follows and an identical
   day get different briefings depending on when they clicked, which makes
   the product hard to reason about and hard to eval.

## Design

### Rule

On `GET /api/briefings/latest`, when **the briefing scheduler is running**
(`BRIEFING_SCHEDULER_ENABLED=true`) **and the user has an enabled
schedule**:

- `force=false` → return the latest briefing as-is, with `next_run_at`
  added to the payload. No generation, no readiness gate, no cursor move.
- `force=false` and the user has **no briefing at all** → fall through to
  the [#36](36-per-user-digest-from-inbox.md) lazy path once, so a new user
  sees a first edition rather than waiting for tomorrow's slot.
- `force=true` → the [#55](55-briefing-readiness-gate.md) manual override,
  unchanged: bypasses the throttle and the gate.

When the scheduler is **off**, or the schedule row is disabled, the endpoint
behaves exactly as before. A deployment that ships dark keeps working; a
user who turns scheduling off in Settings gets the lazy behaviour back.

### Default schedule

The server never knows a user's timezone at signup (the Google callback
carries none). The client sends `?tz=<IANA zone>` on every non-forced
`latest` call, and the first such call for a user with no schedule row
upserts:

| Field | Value |
|-------|-------|
| `frequency` | `daily` |
| `hour_local` | `8` |
| `timezone` | the reported zone |
| `enabled` | `true` |
| `next_run_at` | next 08:00 in that zone |

No `tz`, or an unknown zone, seeds nothing and the lazy path applies (FM-4:
never silently fall back to UTC and fire at the wrong hour). The seed is
skipped entirely while the scheduler is off, so a dark deployment does not
accumulate rows it will never fire.

The Settings page ([BriefingScheduleSettings.tsx](../thestill/web/frontend/src/components/BriefingScheduleSettings.tsx))
is unchanged: it already treats a 404 as "disabled defaults at 08:00
browser time", which is the same shape the seed writes.

### Card

The inbox card ([BriefingCard.tsx](../thestill/web/frontend/src/components/BriefingCard.tsx))
shows `• next at 8:00 AM` / `• next tomorrow 8:00 AM` / `• next Mon 8:00 AM`
when `next_run_at` is present, and a small "Generate now" text button next
to "Past briefings". Without `next_run_at` (lazy path) the card is as before.

### What this does not change

- **[#50](50-scheduled-briefings.md) tick semantics.** Claim-before-generate,
  catch-up-once, DST handling, per-user failure isolation, and the
  `BRIEFING_MIN_INTERVAL` throttle inside a scheduled run are untouched.
  The throttle still protects the 08:00 slot from a 07:30 "Generate now".
- **[#55](55-briefing-readiness-gate.md) on the scheduled path.** The tick
  still waits (up to the grace window) for in-flight followed episodes.
  The lazy deferral machinery is simply never reached for scheduled users.
- **`force=true`.** Bypasses the throttle and the gate exactly as [#55](55-briefing-readiness-gate.md)
  specified. It moves the cursor, so a manual cut at 20:00 means the 08:00
  edition covers 20:00→08:00. That is now the user's explicit choice rather
  than a side effect of opening a page.
- **Schema.** None. The seed writes an ordinary `user_briefing_schedules`
  row through the existing repository.

## Implementation

- [x] `GET /latest` scheduled-only rule + `tz` seed
      ([api_briefings.py](../thestill/web/routes/api_briefings.py)).
- [x] `next_run_at` on the latest payload when the scheduler owns generation.
- [x] Client sends `?tz=` ([client.ts](../thestill/web/frontend/src/api/client.ts));
      card shows the next edition and a Generate now action.
- [x] Route tests: scheduled read, first-edition fallback, force, disabled
      schedule, seed with/without/invalid `tz`, scheduler off
      ([test_api_briefings.py](../tests/unit/web/test_api_briefings.py)).
- [x] Card tests ([Inbox.test.tsx](../thestill/web/frontend/src/pages/Inbox.test.tsx)).
- [ ] Production: flip `/thestill/prod/BRIEFING_SCHEDULER_ENABLED` to `true`
      and reconcile. Until then this spec is inert (rule and seed are both
      gated on the scheduler running).

## Open Questions

1. **Should `force=true` respect the throttle?** Today it bypasses it. A
   user tapping "Generate now" twice in a minute pays twice. Left as-is:
   the button is explicit and the sliver it cuts is visible.
2. **Weekly default for low-volume users?** Someone following two shows
   gets an empty daily slot most days (a no-op, no spend). Revisit with
   usage data.

## Decision Log

| Date | Decision | Rationale |
|------|----------|-----------|
| 2026-09-24 | Schedule slot is the only automatic trigger | The lazy trigger made the window a function of reader behaviour; the 6h throttle and 24h reuse were symptoms |
| 2026-09-24 | Seed the default row from the client-reported timezone on first inbox open | Server has no timezone at signup; UTC default violates FM-4 |
| 2026-09-24 | Rule and seed both gated on the scheduler running | A dark deployment must keep the #36 behaviour and not accumulate rows it will never fire |
| 2026-09-24 | First edition still cut lazily | A new user should not wait a day to see what the product does |
