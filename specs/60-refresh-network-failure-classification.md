# Refresh Failure Classification — Network vs Feed-Gone

> **Status:** 📝 Draft
> **Created:** 2026-07-22
> **Author:** Engineering (failure-recovery design)
> **Related:** [#48 refresh-feed-stage](48-refresh-feed-stage.md) (introduced the feed-park mechanism this revises), [#49 queue-auto-healing-and-recovery](49-queue-auto-healing-and-recovery.md) (the retry-budget-trap + infra-vs-item attribution this extends to the feed-park path), [#42 robustness-and-failure-mode-hardening](42-robustness-and-failure-mode-hardening.md) (this is the "transient-treated-as-terminal" + "silent-degradation" anti-patterns, applied to refresh), [#03 error-handling](03-error-handling.md) (exception hierarchy this classification plugs into), [#19 refresh-performance](19-refresh-performance.md) (owns the scheduler + conditional-GET fetch path), [#00 constitution](00-constitution.md) (principle #4 loud failure, #5 self-healing)

---

## Executive Summary

On **2026-07-15/16** a laptop-hosted instance had **all 90 followed feeds
parked** (`next_refresh_at = NULL`, `last_refresh_error` set) and stopped
discovering new episodes entirely. Discovery was dead for **7 days** until an
operator intervened. The machine had simply been **closed / offline** for a
couple of days — not one of the 90 feeds was actually down.

The mechanism is a two-stage loss of information:

1. **The fetch boundary flattens the error.** `fetch_rss_content`
   ([media_source.py:613](../thestill/core/media_source.py#L613)) catches
   *every* `requests.RequestException` and collapses it to
   `FetchRSSResult(status_code=0, error=str(e))`. A DNS failure ("no network")
   and an HTTP `404`/`410` ("feed genuinely gone") become **indistinguishable**
   the moment they leave the fetch call.

2. **Retry-exhaustion parks unconditionally.** The flattened error surfaces as
   `had_error=True` → `TransientError` ([task_handlers.py:1404-1410](../thestill/core/task_handlers.py#L1404-L1410)),
   which the worker retries `max_retries = 3` times with **seconds** of backoff
   ([queue_manager.py:345](../thestill/core/queue_manager.py#L345),
   `calculate_backoff` [queue_manager.py:403](../thestill/core/queue_manager.py#L403)).
   When the outage outlasts that ~minutes-long budget, the task lands in the DLQ
   and the worker calls `record_refresh_error(terminal=True)`
   ([task_worker.py:843-855](../thestill/core/task_worker.py#L843-L855)), which
   **parks the feed**. `get_due_podcasts` then excludes it forever
   ([postgres_podcast_repository_podcasts.py:1075](../thestill/repositories/postgres_podcast_repository_podcasts.py#L1075)),
   and **no automatic path re-arms it** — `clear_podcast_refresh_failure`
   ([postgres_podcast_repository_podcasts.py:1198](../thestill/repositories/postgres_podcast_repository_podcasts.py#L1198))
   is only reachable from the manual DLQ retry endpoints
   ([api_commands.py:1666](../thestill/web/routes/api_commands.py#L1666),
   [:1773](../thestill/web/routes/api_commands.py#L1773)).

The structural defect is the same one spec #49 named — **"a shared dependency is
down" is treated identically to "this unit of work is bad."** Spec #49 fixed it
for the episode queue; the **feed-park path is outside that healing loop**, so
the refresh scheduler still silently self-destructs whenever the host loses
network for longer than three quick retries. For a laptop that sleeps, that is
**every time it sleeps overnight**.

This spec makes refresh **distinguish "couldn't reach the host" from "the host
said the feed is gone,"** never park on the former, and auto-recover after a
connectivity gap. The throughline: **only a definitive "this feed is gone"
signal may park a feed; everything else stays scheduled and retries when the
environment recovers.**

**Preference bias (explicit):** in ambiguous cases (5xx, generic timeout) the
design leans toward *keep trying* rather than *park forever*. A genuinely-dead
feed taking longer to park is a good trade against the whole fleet silently
stalling on a laptop lid-close.

---

## Table of Contents

1. [The Incident](#the-incident)
2. [How a Feed Gets Parked Today](#how-a-feed-gets-parked-today)
3. [Why #49 Doesn't Already Cover This](#why-49-doesnt-already-cover-this)
4. [Failure Taxonomy](#failure-taxonomy)
5. [Proposed Design — Three Layers](#proposed-design--three-layers)
6. [Phased Implementation Plan](#phased-implementation-plan)
7. [Testing](#testing)
8. [Rollout & Recovery](#rollout--recovery)
9. [Open Questions](#open-questions)

---

## The Incident

Reconstructed from the live Postgres instance on 2026-07-22:

| Signal | Value |
|---|---|
| Followed (non-synthetic) feeds | 90 |
| Feeds parked (`next_refresh_at IS NULL`) | **90 (100%)** |
| Feeds with `last_refresh_error` | **90** |
| Feeds due now | 0 |
| Newest `episodes.created_at` | 2026-07-15 09:57 |
| `refresh-feed` tasks `failed` | 86, all between **2026-07-15 12:31 → 2026-07-16 12:45** |
| Error text (all) | `Feed refresh failed for <title> (<id>)` — generic, no category |

The server process itself never stopped (up since 2026-07-14). It refreshed
normally for ~1 day, then every feed failed inside a ~24h window that coincides
exactly with the laptop being closed, every feed exhausted its retry budget
against the dead network, and every feed parked. The scheduler kept ticking
every 60s against an empty due-set — **loud in its cadence, silent in its
output.** The operator's only symptom was "newest episode is a week old."

This is [#42](42-robustness-and-failure-mode-hardening.md)'s **"transient
treated as terminal"** and **"silent degradation"** anti-patterns, reproduced in
a path #42's catalogue predates.

---

## How a Feed Gets Parked Today

```
scheduler.tick()                      # every 60s (refresh_scheduler.py:97)
  └─ get_due_podcasts()               # next_refresh_at NOT NULL AND <= now
       └─ enqueue REFRESH_FEED task
            └─ handle_refresh_feed()  # task_handlers.py:1363
                 └─ _refresh_single_podcast()
                      └─ fetch_rss_content()          # media_source.py:557
                           except requests.RequestException:
                             return FetchRSSResult(status_code=0, error=str(e))   # ← category lost
                 had_error=True → raise TransientError                            # task_handlers.py:1410
       worker retries 3× (calculate_backoff: ~5s, 30s, …)                         # all inside the outage
       retries exhausted → _mark_episode_failed()                                # task_worker.py:827
         is_feed_scoped_stage → record_refresh_error(terminal=True)              # task_worker.py:850
           UPDATE podcasts SET next_refresh_at = NULL, last_refresh_error = …    # pg repo :1183
  ── feed now invisible to get_due_podcasts forever; only manual DLQ retry re-arms ──
```

Two facts make this unrecoverable without a human:

- **`status_code=0` erases the one bit we need.** `raise_for_status()` raises
  `requests.HTTPError` for 404/410 (which *is* a `RequestException`), so the
  404/410 case and the `ConnectionError`/`Timeout` case take the same `except`
  branch and both emit `status_code=0`. The response's real status is dropped.
- **Parking has no automatic inverse.** `record_refresh_success` clears
  `last_refresh_error` and reschedules ([pg repo :1121](../thestill/repositories/postgres_podcast_repository_podcasts.py#L1121)),
  but it only runs on a *successful* fetch — which can't happen for a parked
  feed, because a parked feed is never enqueued.

---

## Why #49 Doesn't Already Cover This

Spec [#49](49-queue-auto-healing-and-recovery.md) diagnosed the identical root
cause (the "retry budget trap": a shared outage burns every task's retries in
one ~3.5-min window) and added a circuit breaker + a healer loop over terminal
tasks. But its recovery surface is the **episode queue** — it re-examines
`failed`/`dead` *tasks* and their `episodes` rows. The feed-park state lives in
**`podcasts.next_refresh_at` / `last_refresh_error`**, written by
`record_refresh_error(terminal=True)`, which #49's healer does not read or
reset. Even if #49's healer re-drives a DLQ'd `refresh-feed` task, success would
call `record_refresh_success` and clear the park — **but the healer only acts on
tasks it can see, and a parked feed produces no new task.** The two systems
don't meet. This spec closes that seam and reuses #49's health-signal
infrastructure where it exists (see Layer 2).

---

## Failure Taxonomy

The fetch/parse path can fail in exactly three ways. Only one may park a feed.

| Category | Signals | Meaning | Park? |
|---|---|---|---|
| **`NETWORK_DOWN`** | `requests.ConnectionError`, `ConnectTimeout`, `socket.gaierror` (DNS "getaddrinfo failed"), no-route-to-host, `UnsafeURLError` on a previously-valid host | The host was never reached. Almost always the *local* environment (asleep, no wifi, VPN, captive portal). | **Never** — reschedule with backoff, leave `next_refresh_at` set. |
| **`FEED_GONE`** | HTTP **404**, **410 Gone**; optionally a persistent `bozo`/parse failure across N online attempts | The host answered and the feed is not there. | **Yes** — this is what parking is for. |
| **`AMBIGUOUS`** | HTTP **5xx**, `429`, read timeout after connect, transient parse hiccup | Host reachable but unhappy; usually temporary. | **No** (bias: keep trying). Park only after **M consecutive failures while provably online** (Layer 2). |

Classification is **reliable** for the two endpoints that matter: connection/DNS
errors and 404/410 are unambiguous. The fuzzy middle is deliberately routed to
"don't park."

---

## Proposed Design — Three Layers

Layer 1 alone fixes the reported incident. Layers 2–3 are defense-in-depth and
make recovery automatic rather than merely non-destructive.

### Layer 1 — Preserve and honor the failure category (required)

**1a. Carry the category out of the fetch boundary.** Extend `FetchRSSResult`
with a `failure_category: Literal["network", "feed_gone", "ambiguous"] | None`
and stop erasing `status_code`. In `fetch_rss_content`
([media_source.py:595-623](../thestill/core/media_source.py#L595-L623)):

- Split the `except`: classify `requests.ConnectionError` / `ConnectTimeout` /
  DNS (`socket.gaierror` inside a `RequestException`) → `network`.
- On `HTTPError`, read `e.response.status_code`: `404`/`410` → `feed_gone`;
  `5xx`/`429` → `ambiguous`. Preserve the real status code, not `0`.
- Read timeouts / other `RequestException` → `ambiguous`.

**1b. Thread the category through the return chain.** `_refresh_single_podcast`
already returns a `had_error` flag; add the category alongside it so
`handle_refresh_feed` can pass it to the failure stamp.

**1c. Gate parking on category.** The park decision at
[task_worker.py:843-855](../thestill/core/task_worker.py#L843-L855) must only set
`terminal=True` when the underlying category is `feed_gone`. For `network` /
`ambiguous`, **do not park** — instead reschedule (`next_refresh_at = now +
backoff`, `last_refresh_error` stamped for visibility, `next_refresh_at` left
non-NULL). This requires plumbing the category into the terminal-failure path
(carry it on the `Task`/failure record, or re-derive from the stored
`last_refresh_error` category prefix).

> **Contract preserved:** FM-2 (no cache-header persistence on the failure path)
> is unchanged — a rescheduled network failure still re-validates on the next
> attempt.

### Layer 2 — Connectivity gate (belt-and-suspenders)

Before **any** feed is parked, and optionally before the scheduler spends a tick
fanning out doomed fetches, check global reachability (resolve/connect a stable
host, cached ~30s). Semantics:

- **Offline** → suppress *all* parking this window; the scheduler may skip the
  tick entirely and log `refresh_scheduler_offline_skip`. Nothing is parked, so
  nothing needs re-arming later.
- **Online but this one feed failed** → Layer 1 classification applies.

This is the single most robust guard for the laptop-sleep case, because
"offline" is a *global* fact independent of any feed. It would have prevented the
incident even without Layer 1. Where #49 already exposes a dependency-health /
circuit-breaker signal for network, reuse it rather than adding a parallel probe
(FM-6 parallel-path drift).

**Mass-failure heuristic (cheap variant, if a live probe is undesirable):** if
≥ X% of feeds fail in one tick/window, treat it as environmental and suppress
parking for that window. 90/90 failing is never 90 feeds dying at once.

### Layer 3 — Wake-gap auto-rearm (the literal ask)

The scheduler records the monotonic timestamp of each tick. When a tick fires
after a gap far larger than `tick_seconds` (e.g. expected 60s, actual 2 days —
the clock jump when a laptop resumes from sleep), treat it as a **resume event**:
auto-clear feeds parked with a `network`/`ambiguous` category (call
`clear_podcast_refresh_failures` for them) so discovery restarts on the next
tick. `feed_gone` parks are left alone. This directly implements *"when I turn on
my laptop, it restarts the fetch of new episodes."*

Layer 3 also retroactively rescues feeds parked *before* this spec ships, as long
as their stored error is (re)classified as non-terminal.

---

## Phased Implementation Plan

| Phase | Scope | Gate |
|---|---|---|
| **0** | One-shot re-arm of the 90 currently-parked feeds (operator unstick; see Rollout). | Discovery resumes; independent of code changes. |
| **1** | Layer 1a/1b — classify at the fetch boundary, preserve `status_code`, add `failure_category` to `FetchRSSResult` and the refresh return chain. Pure plumbing, no behavior change yet. | Unit tests assert category per exception/status; existing refresh tests green. |
| **2** | Layer 1c — gate `record_refresh_error(terminal=…)` on category. Network/ambiguous → reschedule, not park. | Simulated `ConnectionError` never NULLs `next_refresh_at`; `410` still parks. |
| **3** | Layer 2 — connectivity gate (or reuse #49 health signal) suppressing parking while offline. | Offline simulation parks nothing across a full tick. |
| **4** | Layer 3 — wake-gap detection + auto-rearm of network-parked feeds. | Injected clock gap re-arms network parks, leaves `feed_gone` parks. |
| **5** | Observability — structured `refresh_park_suppressed` / `refresh_feeds_rearmed` events; a status-endpoint counter of parked-by-category so a mass park is visible (closes the "silent" half of the incident). | Events present; parked-by-category surfaced in `thestill status` / web. |

Phases 1–2 are the minimum that fixes the incident. 3–5 are the durable,
self-healing finish.

---

## Testing

Following [#04 testing](04-testing.md) and #42's **"consistent-mock"** warning
(a mock that always succeeds hides exactly this class of bug — tests must inject
*failures* of specific types):

- **Classification unit tests** (`media_source`): each of `ConnectionError`,
  `ConnectTimeout`, `gaierror`, `HTTPError(404)`, `HTTPError(410)`,
  `HTTPError(503)`, read-timeout, `bozo` parse → asserts the expected
  `failure_category` and preserved `status_code`.
- **Park-gating tests** (`task_worker` / handler): a `network` failure that
  exhausts retries leaves `next_refresh_at` non-NULL; a `feed_gone` failure
  parks. Assert against a real repo (sqlite) row, not a mock.
- **Offline-window integration test**: patch the fetch to raise
  `ConnectionError` for N feeds across a full scheduler tick; assert **zero**
  feeds parked and all remain due.
- **Wake-gap test**: drive two ticks with an injected monotonic gap; assert
  network-parked feeds are re-armed and `feed_gone` parks are not. (Clock is
  injected — `Date.now()`-style ambient time is banned in this repo's
  deterministic tests; pass `now`/monotonic in.)
- **Regression**: reproduce the incident — 90 feeds, all `ConnectionError` for
  24h of simulated offline — and assert discovery self-resumes when fetch starts
  succeeding again, with no operator action.

---

## Rollout & Recovery

**Immediate unstick (Phase 0, no deploy needed).** Re-arm the parked feeds so the
scheduler picks them up on the next tick:

```sql
UPDATE podcasts
SET last_refresh_error = NULL, next_refresh_at = now()
WHERE next_refresh_at IS NULL AND last_refresh_error IS NOT NULL
  AND synthetic IS NOT TRUE;
```

Or, in the UI, **DLQ → Retry all** ([api_commands.py:1725](../thestill/web/routes/api_commands.py#L1725)),
which clears the failure and re-enqueues. A plain **Refresh feeds** click
discovers the missed episodes but does **not** clear the park, so it is not a
substitute.

**Ships dark → on.** Layers 1–2 change failure handling only and are safe to
enable immediately. Layer 3's auto-rearm should sit behind a flag
(`REFRESH_WAKE_REARM_ENABLED`, default on for self-hosted / laptop installs)
until the wake-gap threshold is tuned against real tick jitter.

**Backfill of legacy parks.** Once Layer 1 (re)classification exists, a one-shot
migration can re-derive category for existing `last_refresh_error` rows and
re-arm any that aren't `feed_gone`, so instances upgrading from a parked state
self-heal on first boot.

---

## Open Questions

1. **Connectivity probe target.** A hardcoded host (e.g. a DNS resolver) is
   simple but is itself a dependency; reusing #49's health signal avoids a second
   probe surface. Decide during Phase 3.
2. **`AMBIGUOUS` park threshold.** How many consecutive *online* failures before
   a 5xx feed parks? Proposal: reuse the AIMD max-interval as the ceiling and
   park only after the feed has backed off to `REFRESH_MAX_INTERVAL_SECONDS` and
   still fails — i.e. "quietly dying," never "briefly flaky."
3. **Wake-gap threshold.** What multiple of `tick_seconds` reliably means
   "resumed from sleep" without false-firing on GC/CPU stalls? Proposal:
   `gap > max(10 × tick_seconds, 10 min)`.
4. **Scope of the connectivity gate.** Skip the whole tick when offline, or fetch
   normally and only suppress *parking*? Skipping saves doomed work; fetching
   keeps conditional-GET warm. Lean toward skip.
