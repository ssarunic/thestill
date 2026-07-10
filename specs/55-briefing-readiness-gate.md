# Briefing Readiness Gate

> **Status:** 🚧 Implemented — Phases 1–2 (2026-07-10); Phase 3 remains optional
> **Created:** 2026-07-10
> **Author:** Product & Engineering
> **Related:** [#36 per-user-digest-from-inbox](36-per-user-digest-from-inbox.md), [#50 scheduled-briefings](50-scheduled-briefings.md), [#51 briefing-email-delivery](51-briefing-email-delivery.md), [#48 refresh-feed-stage](48-refresh-feed-stage.md), [#42 failure-mode catalogue](42-robustness-and-failure-mode-hardening.md)

---

## Executive Summary

A briefing cut at time *T* includes only the episodes that reached the
user's inbox before *T* — but inbox arrival is the **end** of the pipeline
(summarize → publish → fan-out), and generation currently pays no attention
to what is still **in flight**. When generation races a draining queue, the
briefing is technically correct and practically useless: it covers a
half-day window with one episode while nine more land minutes later.

This spec adds a **readiness gate**: before cutting a briefing, check
whether any episode the user is waiting for — *from a podcast they follow,
published before the briefing's cutoff, already discovered, not yet
delivered to their inbox, and still able to make pipeline progress* — is
pending. If so, defer generation and re-check, up to a bounded grace
deadline. At the deadline, cut with whatever is ready; stragglers roll into
the next briefing via the existing cursor semantics, so late never means
lost.

Selection semantics from [#36](36-per-user-digest-from-inbox.md) are
untouched — the gate decides **when** to cut, never **what** is included.

---

## Motivation: the one-episode briefing (2026-07-10)

Observed on the production instance, all times local (+01):

| Time | Event |
|---|---|
| Jul 9 20:23 | Lazy briefing generated on inbox open (9 episodes). Cursor → 20:23. |
| Jul 9 22:17 – Jul 10 10:07 | Machine effectively asleep. macOS maintenance wakes let short `refresh-feed` tasks run (00:55, 02:25, 02:47, 05:06, 06:07…), so episodes kept being **discovered and queued** — but no transcribe/clean/summarize work ran. |
| Jul 10 10:01 | Full wake; refresh burst queues ~25 more episodes. |
| Jul 10 10:07–10:13 | Workers resume; first backlog episode finishes and hits the inbox at **10:15:54**. |
| Jul 10 10:16:36 | User opens the inbox → lazy briefing cut. Window [Jul 9 20:23, Jul 10 10:16:36) contains exactly **one** inbox delivery. `episode_count = 1`. |
| Jul 10 10:19–10:29+ | Seven more episodes reach the inbox, minutes past the cutoff. |

Nothing was lost (the cursor carried everything into the next window), but
the product promise — "the briefing covers what happened since last time" —
was broken at the moment it mattered. The same race will make a
[#50](50-scheduled-briefings.md) 8:00 scheduled briefing near-empty on any
machine that sleeps overnight: at 8:00 the overnight discoveries are queued
but unprocessed.

---

## Product Requirements

### User stories

| As a... | I want... | So that... |
|---|---|---|
| User | My briefing to wait for episodes that are still processing | I get one full briefing, not a sliver plus a catch-up |
| User | It to wait only for **my** podcasts | Someone else's subscriptions never delay my morning |
| User | It to wait only for episodes **released before my briefing hour** | An episode published at 8:03 doesn't hold the 8:00 briefing hostage |
| User | A hard cap on the wait | A stuck transcription delays my briefing by minutes, not forever |
| User (lazy path) | To see *why* the briefing isn't ready and force it anyway | I stay in control when I'm standing at the coffee machine |

### Behavior rules

1. **Gate, don't filter.** Inclusion stays exactly as in #36: inbox rows
   with `delivered_at` in `[cursor_from, cursor_to)`. The pub-date cutoff
   below controls only what is *worth waiting for*. Consequence: an episode
   published after the cutoff that happens to finish processing before the
   cut rides along one briefing early — accepted, and it preserves the
   no-loss cursor invariant without new bookkeeping.
2. **Cutoff.** Scheduled run: the most recent nominal local slot at or
   before the actual fire time (e.g. today 08:00 in the user's
   `timezone_name`, converted to UTC). This matters for downtime catch-up:
   a Monday wake must wait for work published through Monday's slot, not use
   the oldest overdue `next_run_at` left from Saturday. Lazy run: `now()` at
   the moment the inbox open triggers generation.
3. **Wait-set scope.** Episode is in user *U*'s wait-set iff **all** of:
   - its podcast has a `podcast_followers` row for *U*
     (same scope [`fanout_on_publish`](../thestill/services/inbox_service.py)
     uses for delivery — the gate waits only for episodes that *would*
     land in this user's inbox);
   - `pub_date < cutoff`;
   - the episode row exists (**discovered** — we cannot wait for what no
     feed refresh has seen yet; see Accepted Limitations);
   - no `user_episode_inbox` row for (*U*, episode) yet;
   - it is **not yet published** (`published_at IS NULL`) — a published
     episode already fanned out; user-chain re-runs on it can never deliver
     anything new to this user (added 2026-07-10);
   - it has an **active** pipeline task: status `pending` or `processing`,
     or `retry_scheduled` with retries remaining
     (`retry_count < max_retries` and a `next_retry_at`). In the queue state
     machine, `failed` already means retries are exhausted; `failed`, `dead`,
     `completed`, and `superseded` tasks are therefore *not* waited for;
   - the active task is a **user-chain stage** (`download` → `summarize`,
     the `_USER_CHAIN_ORDER` in
     [queue_manager.py](../thestill/core/queue_manager.py)). Post-summarize
     entity/corpus stages (`extract-entities`, `resolve-entities`,
     `reindex`, `rebuild-cooccurrences`, `compute-related`,
     `enrich-entities`) are enrichment — an episode is briefing-ready the
     moment it is summarised, so post-processing never holds the cut
     (decision 2026-07-10).
4. **Grace deadline.** If the wait-set is non-empty, defer and re-check.
   Deadline = **actual fire time** + `BRIEFING_READINESS_GRACE_MINUTES`
   (default 60). Anchoring at fire time, not the nominal slot, is
   deliberate: after overnight sleep the #50 catch-up fires at 9:30, and a
   deadline of "08:00 + 60min" would already be expired — the grace must
   buy the pipeline real awake-time to drain.
5. **At the deadline, cut unconditionally** with whatever the window
   holds (including an empty window → the existing no-briefing path).
   Log what was abandoned.
6. **Fail open.** If the readiness query itself errors, log and generate
   immediately (per FM-1, an error must not masquerade as "wait forever" —
   a briefing on time with a hole beats no briefing).

---

## Design

### Readiness check

One new query, owned by the briefing side (repository method
`count_pending_for_user(user_id, since, cutoff)`), conceptually:

```sql
SELECT COUNT(DISTINCT e.id)
FROM tasks t
JOIN episodes e ON e.id = t.episode_id
JOIN podcast_followers pf
  ON pf.podcast_id = e.podcast_id AND pf.user_id = :user_id
WHERE e.pub_date < :cutoff
  AND e.pub_date >= :since
  AND NOT EXISTS (SELECT 1 FROM user_episode_inbox i
                  WHERE i.user_id = :user_id AND i.episode_id = e.id)
  AND (t.status IN ('pending', 'processing')
       OR (t.status = 'retry_scheduled'
           AND t.retry_count < t.max_retries
           AND t.next_retry_at IS NOT NULL))
```

Task-first execution keeps a first briefing bounded by active queue depth
instead of range-scanning the user's entire followed back catalogue, while
distinctness protects against duplicate active rows for one episode.

Bound `pub_date` below by the briefing's `cursor_from` as well — episodes
older than the window's start were already someone's business in a prior
briefing (or were never followed), and an ancient stuck task must not gate
every future briefing.

`BriefingService.generate_for_user` gains the gate in front of the
existing window computation and returns a new
`Deferred(pending_count, deadline)` outcome alongside the current
`Briefing | None`. **The cursor does not move on deferral** — deferral is
"not yet", not "empty window".

### Scheduler integration (#50)

The [#50](50-scheduled-briefings.md) tick uses advance-before-generate:
`next_run_at` has already moved to tomorrow when generation runs, so a
deferral must not orphan the slot. Reuse the pattern the scheduler already
has for narration retries (in-memory retry set,
[briefing_scheduler.py:92–94](../thestill/core/briefing_scheduler.py#L92)):
on `Deferred`, park `user_id → (cutoff, deadline)` in an in-memory
pending map and re-attempt on each subsequent tick (tick cadence, 60 s, is the
re-check interval — no new poll loop). `cutoff` remains the most recent
nominal UTC slot while `deadline` already encodes the actual fire-time anchor.
On success the entry is dropped. If generation itself raises, retain the entry
only while grace remains; at/after the deadline, evict it so the parked slot
cannot block every future cadence for that user. Server restart mid-grace
loses the map — acceptable under the no-schema-change constraint:
`next_run_at` was already advanced by #50's claim-before-generate flow, so
that scheduled slot is abandoned rather than re-fired on boot. The briefing
cursor is untouched, which guarantees the episodes roll into the next lazy or
scheduled generation window, but that day's scheduled briefing/email may be
skipped.

### Lazy path (inbox open)

`GET /api/briefings/latest` currently generates inline. With the gate:

- Deferred → respond `202` with
  `{"briefing_pending": {"pending_count": N, "deadline": "..."}}` and do
  **not** advance the cursor. The inbox UI shows *"Your briefing is
  catching up — N episodes still processing"* with a **Generate now**
  action.
- **Generate now** → same endpoint with `?force=true`, which skips the
  gate and cuts immediately (today's behavior, but now it's an explicit
  user choice instead of a silent race).
- If Generate now finds zero delivered episodes, the existing no-briefing
  path still returns 404. The UI surfaces "No episodes are ready yet" rather
  than silently ignoring the action, and the current wait-set remains
  explicitly bypassed instead of starting a fresh grace window on the next
  poll.
- The frontend may re-poll on its normal inbox cadence; each poll
  re-evaluates the gate, so the briefing appears as soon as the queue
  drains or the grace expires.

The lazy grace deadline is anchored at the *first* deferred request for
the current cursor window (persisting it in memory keyed by user is enough;
worst case after a restart the grace restarts). Once a request actually
observes deadline expiry — or the user forces generation — mark that anchor
exhausted and do not re-arm it while its original wait-set remains active.
If an unobserved anchor has sat more than one grace interval past its deadline
(for example the user returns the next day), treat the open as a new session
and re-anchor cutoff and deadline at the new request so fresh backlog is not
hidden behind a stale cutoff. The extra interval is a polling tolerance: a
request arriving seconds after the deadline must observe expiry, not mint a
new grace window.

### Configuration

| Variable | Default | Meaning |
|---|---|---|
| `BRIEFING_READINESS_GRACE_MINUTES` | `60` | Max deferral past fire time. `0` disables the gate entirely (current behavior). |

No new tables, no schema changes, no new daemon threads.

### Observability

Structured events (per the logging conventions):
`briefing_deferred` (user_id, pending_count, cutoff, deadline),
`briefing_grace_expired` (user_id, abandoned_count — the FM "silent
degradation" guard: a briefing cut at deadline says so), `briefing_forced`
(lazy `?force=true`). `pending_count` is also the number the UI displays.

---

## Failure modes ([#42](42-robustness-and-failure-mode-hardening.md) checklist)

| FM | Risk here | Mitigation |
|---|---|---|
| FM-1 errors-as-empty-results | Readiness query error read as "0 pending" *or* as "pending forever" | Fail open with an explicit `briefing_readiness_check_failed` log; generate immediately |
| FM-2 checkpoint-before-durability | Cursor advanced on deferral would drop the window | Cursor moves only when a briefing row is actually persisted (unchanged from #36) |
| FM-3 mixed-tz | Cutoff computed in the wrong zone shifts the wait-set by hours | Cutoff derived via `zoneinfo` from the schedule's `timezone_name`, compared in UTC (same discipline as #50) |
| FM-4 silent degradation | Deadline-expiry cut looks identical to a clean cut | `briefing_grace_expired` event with abandoned count |
| FM-5 consistent-mock tests | Tests that mock the readiness query to always-0 prove nothing | Test matrix drives real rows: pending task, retrying task, exhausted task, unfollowed podcast, post-cutoff pub_date |
| FM-6 path drift | — | No new paths |
| FM-7 unsanitized input | — | No LLM output in the gate |

Also FM-adjacent: the gate must never wait on **permanently failed**
episodes (rule 3) or the daily briefing inherits every poison-pill episode
in the backlog.

---

## Phases

**Phase 1 — the gate.** Repository readiness query, `Deferred` outcome in
`BriefingService.generate_for_user`, scheduler pending-map retry, config,
and logs. Scheduled briefings stop racing the queue.

**Phase 2 — lazy-path UX.** `202 briefing_pending` response, `?force=true`,
inbox banner with pending count and Generate-now. Fixes the exact
2026-07-10 incident surface.

**Phase 3 (optional) — smarter deadline.** Estimate drain time from queue
depth × recent per-stage throughput and surface "ready in ~12 min" instead
of a bare count. Punt until the fixed grace proves insufficient.

---

## Accepted limitations

1. **Undiscovered episodes cannot be waited for.** An episode published at
   07:00 on a feed whose 24 h refresh slot is 12:10 is invisible at 08:00;
   it lands in tomorrow's briefing. The error is bounded by per-feed
   refresh intervals ([#48](48-refresh-feed-stage.md)), not by this spec.
2. **Very long episodes can outlive the grace.** A 3 h episode queued at
   07:55 won't transcribe in any reasonable grace window; it rolls over.
   By design — the deadline exists precisely so one whale doesn't sink the
   briefing.
3. **In-memory deferral state.** A restart mid-grace loses the deferred
   scheduled slot because #50 already advanced `next_run_at`; the cursor is
   untouched, so coverage rolls into the next lazy or scheduled generation,
   but that slot's briefing/email may be skipped. Durable same-slot recovery
   would require persisted deferral state (or a claim-flow redesign) and is
   explicitly out of scope for the no-schema-change version.

## Open questions

1. Should `?force=true` also mark the skipped wait-set episodes for the
   *next* briefing's headline ("3 episodes arrived after your last
   briefing was forced")? Nice-to-have, needs no schema either way.
2. Should email delivery (#51) hold until grace resolution even if the
   user's schedule fires earlier? Current answer: yes trivially — delivery
   keys off the briefing row, which doesn't exist until the gate opens.

---

## Decision Log

| Date | Decision |
|------|----------|
| 2026-07-10 | Wait-set narrowed to **user-chain stages only** (`download` → `summarize`) plus a `published_at IS NULL` guard. Rationale (user direction): an episode is briefing-ready when summarised; entity/corpus post-processing (`compute-related` etc.) is enrichment and must never defer a cut. The publish guard additionally stops user-chain *re-runs* on already-published episodes from stalling briefings for users who followed after publish (fan-out never re-fires). |
