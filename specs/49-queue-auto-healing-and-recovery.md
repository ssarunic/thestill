# Queue Auto-Healing & Failure Recovery

> **Status:** 🚧 In progress — Phases 1–4 implemented (error attribution +
> healer loop + per-stage circuit breaker + idempotent restart recovery);
> Phase 5 (observability/alerting) still to do.
> **Created:** 2026-06-23
> **Updated:** 2026-06-24
> **Author:** Engineering (failure-recovery design)
> **Related:** [#00 constitution](00-constitution.md) (principles #4 loud failure, #5 self-healing), [#03 error-handling](03-error-handling.md), [#11 task-queue-monitor](11-task-queue-monitor.md), [#16 full-pipeline-and-failure-handling](16-full-pipeline-and-failure-handling.md) (introduced the retry/DLQ model this revises), [#20 parallel-task-queues](20-parallel-task-queues.md) (per-stage pollers this hooks into), [#42 robustness-and-failure-mode-hardening](42-robustness-and-failure-mode-hardening.md) (sibling incident retro), [#48 refresh-feed-stage](48-refresh-feed-stage.md) (idempotent-restart precedent)

---

## Executive Summary

On 2026-06-23 the queue held **101 terminally-failed tasks** (96 `failed`

+ 5 restart-`failed`) and **1 `dead`** task that the system will never retry
on its own. Almost all of them failed for the same reason: a transient
**environmental outage** — DNS resolution failing
(`Failed to connect: [Errno 8] nodename nor servname provided, or not known`)
and a transcription model runtime being unavailable
(`Model selection failed (runtime_unavailable)`). The root cause was gone
within hours, but the tasks stay dead forever and require a human to click
"retry" in the Failed Tasks UI.

The structural defect is that **the queue treats "a shared dependency is
down" identically to "this unit of work is bad."** When DNS or the model
runtime goes down, every in-flight task burns its entire 3-strike retry
budget against the same dead dependency — inside a ~3.5-minute window (see
[The Retry Budget Trap](#the-retry-budget-trap)) — and lands in a terminal
state together. There is no circuit breaker to pause the stage, no
distinction between infra and per-task failures, and no loop that ever
re-examines a terminal task once the dependency recovers.

This spec turns that into a layered auto-healing design. The throughline:
**separate "the environment is unhealthy" from "this work item is bad,"
gate the queue on dependency health, and give terminal states a bounded
second look.** Layers 1 + 3 alone would have made the 2026-06-23 pile-up
self-resolve.

**Key principle:** auto-healing here comes from *correctly attributing
failures* (infra vs item) and *adding a recovery loop over terminal states* —
not from raising `max_retries` blindly, which only delays the same dead end.

---

## Table of Contents

1. [The Incident](#the-incident)
2. [How a Task Reaches a Terminal State Today](#how-a-task-reaches-a-terminal-state-today)
3. [The Retry Budget Trap](#the-retry-budget-trap)
4. [What Auto-Recovery Exists Today](#what-auto-recovery-exists-today)
5. [Design Gaps](#design-gaps)
6. [Proposed Design — Four Layers](#proposed-design--four-layers)
7. [Phased Implementation Plan](#phased-implementation-plan)
8. [Data Model Changes](#data-model-changes)
9. [Failure & Edge Cases](#failure--edge-cases)
10. [Testing](#testing)
11. [Non-Goals](#non-goals)
12. [Open Questions](#open-questions)

---

## The Incident

Snapshot of `data/podcasts.db` `tasks` table on 2026-06-23:

| status | count | auto-retry? | cause |
|---|---:|---|---|
| `completed` | 7919 | — | — |
| `failed` (transcribe/download, transient, retries exhausted) | 96 | ❌ | DNS / `runtime_unavailable` outage |
| `failed` (transcribe, `error_type=NULL`, restart) | 5 | ❌ | "Task interrupted by server restart" |
| `dead` (downsample, fatal) | 1 | ❌ | corrupt/unsupported audio |
| `processing` | 2 | n/a | live worker, mid-run |
| `superseded` | 156 | — | newer task replaced these |

Representative `last_error` values on the 96:

+ `Failed to connect: [Errno 8] nodename nor servname provided, or not known`
  (DNS resolution failed — host was offline or network was down)
+ `Job failed: Model selection failed (runtime_unavailable), stage=transcribe,
  model_id=nvidia/parakeet-tdt-0.6b-v3, engine_id=nemo`

All of these are **environmental and time-bounded.** Re-running them now
would almost certainly succeed. Nothing in the system will do that
automatically.

---

## How a Task Reaches a Terminal State Today

Every failure routes through one decision in the worker
([`task_worker.py:431-453`](../thestill/core/task_worker.py#L431-L453)):

```text
handler raises
├── FatalError        → mark_dead()        → status 'dead'   (terminal)
├── TransientError    → schedule_retry()   → backoff, then 'failed' (terminal)
└── Exception (other) → schedule_retry()   → treated as transient
```

`schedule_retry()`
([`queue_manager.py:1277-1352`](../thestill/core/queue_manager.py#L1277-L1352))
increments `retry_count`; once `retry_count >= max_retries` (default **3**,
`DEFAULT_MAX_RETRIES`) it flips the row to `status='failed'` and stops. The
worker only ever dequeues `pending` or `retry_scheduled` rows whose
`next_retry_at` has passed
([`queue_manager.py:848`](../thestill/core/queue_manager.py#L848)). **`failed`
and `dead` are invisible to the worker.** The only documented path out is
`retry_dead_task()`
([`queue_manager.py:1422`](../thestill/core/queue_manager.py#L1422)), which is
called exclusively by the manual Failed Tasks UI.

The 5 restart rows come from `recover_interrupted_tasks()`
([`queue_manager.py:1200`](../thestill/core/queue_manager.py#L1200)), which by
deliberate design marks crash-interrupted `processing` tasks as `failed`
("safer to let the user manually retry"). Spec #48 already carved out an
exception: feed-scoped stages are reset to `pending` instead, because they are
idempotent.

---

## The Retry Budget Trap

`calculate_backoff()`
([`queue_manager.py:302`](../thestill/core/queue_manager.py#L302)) produces:

| retry | delay |
|---:|---|
| 1 | ~5 s |
| 2 | ~30 s |
| 3 | ~3 min |

So **all three retries are exhausted within ~3.5 minutes.** Any dependency
outage lasting longer than that — a DNS hiccup, a model runtime restart, an
LLM provider blip — guarantees every task that touches it during the outage
dies permanently. The budget is sized for a momentary glitch, but the
failures we actually see are minutes-to-hours long and **correlated across
tasks** (one dead dependency, N dead tasks). Raising `max_retries` alone just
moves the cliff; it does not fix the misattribution.

Secondary finding: `[Errno 8] nodename nor servname provided` does **not**
match any entry in `TRANSIENT_PATTERNS`
([`error_classifier.py:67-90`](../thestill/core/error_classifier.py#L67-L90)) —
it was only retried because transcribe passes `default_transient=True`. The
classifier has `dns.*fail` and `network.*(error|unreachable)` but not the
actual macOS/glibc getaddrinfo strings. So today the system cannot even *name*
this failure as infrastructure; it falls through to a default.

---

## What Auto-Recovery Exists Today

Exactly one self-healing loop, and it only touches `processing`:

+ **`_periodic_stale_task_reset()`**
  ([`task_worker.py:279`](../thestill/core/task_worker.py#L279)) — sweeps tasks
  wedged in `processing` (older than `stale_timeout_minutes`) back to
  `pending`. Cadence is `stale_timeout/5`, clamped to [60 s, 600 s].
+ **`reset_stale_tasks()`** — same logic, run once on startup.

Neither ever looks at `failed` or `dead`. There is no health gating: the
worker keeps dequeuing a stage even when that stage's dependency is known to
be down.

---

## Design Gaps

1. **No infra-vs-item attribution.** A DNS failure (retry indefinitely, it's
   not the task's fault) and a malformed input (give up after a few tries) get
   the same 3-strike budget.
2. **Retry budget is tiny and short** relative to real outage durations, and
   correlated outages drain all in-flight budgets simultaneously.
3. **Terminal states are never re-examined.** Once `failed`/`dead`, the only
   exit is a human click.
4. **No circuit breaker.** During an outage the worker keeps pulling tasks and
   converting healthy work into dead work, instead of pausing the stage.
5. **Restart recovery fails idempotent stages** instead of resuming them
   (only feed stages are exempt, per #48).

---

## Proposed Design — Four Layers

Ordered smallest-blast-radius first. Layers 1 + 2 *prevent* the pile-up;
layer 3 *cleans up* what still slips through; layer 4 closes the restart gap.

### Layer 1 — Per-stage circuit breaker (highest leverage)

Track a rolling error signal per `(stage, dependency)`. When infra-class
errors (see Layer 2) breach a threshold within a window, **open the circuit**
for that stage:

+ the stage poller stops dequeuing new work,
+ in-flight failures do **not** consume retry budget while open,
+ a half-open probe re-tests the dependency on an interval (e.g. one task, or
  a cheap health ping).

When a probe succeeds, **close** the circuit and resume. This directly
prevents the cascade: during an outage the queue *pauses* instead of grinding
every task to death. Hooks into `_stage_poll_loop`
([`task_worker.py`](../thestill/core/task_worker.py)) — the breaker state is
checked before each dequeue.

### Layer 2 — Don't charge infrastructure failures against `max_retries`

Refine the error taxonomy beyond transient/fatal into a third axis:
**infrastructure** (shared dependency down) vs **item** (this work is bad).

+ Extend `error_classifier.py` with an `INFRA_PATTERNS` set covering
  `getaddrinfo`, `nodename nor servname`, `[Errno 8]`, `runtime_unavailable`,
  `connection refused/reset` *when correlated*, provider 5xx/429.
+ Infra-class failures get **unbounded retries with capped backoff** (or a
  much larger dedicated budget) — they never reach `failed`.
+ Item-class failures keep the existing 3-strike budget → `failed`.
+ This is the per-task complement to Layer 1's per-stage view: the breaker
  decides *whether the stage runs*; the classifier decides *whether a given
  failure counts against the item*.

### Layer 3 — Healer loop over terminal states (the missing loop)

Add a periodic sweep (extend `_periodic_stale_task_reset`, or a sibling
`_periodic_terminal_heal`) that scans **`failed` only** (never `dead`):

+ if the recorded failure was infra-class, **and**
+ the stage's dependency is currently healthy (breaker closed), **and**
+ a cooldown has elapsed since `completed_at`,

then auto-requeue (reset `retry_count`, status → `pending`) — **bounded by a
`heal_attempts` cap** (e.g. 2 rounds). After the cap, the row becomes truly
terminal and raises a loud signal, so a genuine poison message cannot loop
forever. This is the loop that would have drained the 96 once DNS returned.

### Layer 4 — Heal idempotent stages on restart

Generalize the spec #48 carve-out. `download`, `downsample`, `transcribe`,
`clean`, `summarize` are all re-runnable from their inputs. On restart,
`recover_interrupted_tasks()` should reset interrupted **idempotent** stages to
`pending` (resume) rather than `failed`. Reserve `→ failed` for stages that
are genuinely unsafe to auto-resume. Drives off an explicit
`is_idempotent_stage()` predicate alongside the existing
`is_feed_scoped_stage()`.

---

## Phased Implementation Plan

Each phase is independently shippable and individually valuable.

### Phase 0 — Backfill the current incident (no code)

Manually requeue the 101 infra-class `failed` tasks via `retry_dead_task()`;
leave the 1 `dead` downsample task (likely genuine bad input). Establishes a
clean baseline before behavioral changes land. *(One-off script; not part of
the durable design.)*

### Phase 1 — Error attribution (Layer 2, classifier only) ✅ DONE

+ ✅ Added `INFRA_PATTERNS` + `is_infrastructure_error()` and
  `classify_error_class()` ('infra' | 'item' | 'fatal') to
  `error_classifier.py`, including the getaddrinfo /
  `nodename nor servname` / `[Errno 8]` / `runtime_unavailable` strings.
+ ✅ Threaded `error_class` onto the task row (additive migration +
  `schedule_retry` / `mark_dead` persist it; `Task.to_dict` exposes it). The
  worker classifies each caught exception and records the label.
+ Label-only: existing rows backfill to NULL and are **not** retroactively
  healed (a clean baseline; the Phase 0 backfill remains a separate one-off).

### Phase 2 — Healer loop (Layer 3) ✅ DONE

+ ✅ Added `heal_attempts` + `last_heal_at` columns.
+ ✅ Added `find_healable_tasks()` (`failed`, `error_class='infra'`, cooldown
  elapsed on both `completed_at` and `last_heal_at`, under cap) and a
  `heal_task()` transition (resets retry budget, increments `heal_attempts`,
  refuses `dead` / over-cap inside the UPDATE's WHERE).
+ ✅ Wired `TaskWorker._periodic_terminal_heal` (+ testable
  `_heal_terminal_tasks` sweep) into the poller set, behind `QUEUE_AUTO_HEAL`
  (default **on**), with `QUEUE_HEAL_INTERVAL_SECONDS` /
  `QUEUE_HEAL_COOLDOWN_MINUTES` / `QUEUE_MAX_HEAL_ATTEMPTS` knobs.

### Phase 3 — Circuit breaker (Layer 1) ✅ DONE

+ ✅ In-memory, thread-safe per-stage breaker (`circuit_breaker.py`,
  `StageCircuitBreaker`) keyed on stage (open question #2 resolved → stage).
  CLOSED → OPEN on N infra failures within a rolling window → HALF_OPEN after
  cooldown (single probe) → CLOSED on probe success / re-OPEN on probe failure.
+ ✅ Gated in `_stage_poll_loop` via `allow_dispatch` / `cancel_dispatch`
  (the latter releases a reserved probe when the queue is empty or another
  stage claims the target, so the half-open slot is never leaked).
+ ✅ Infra failures while OPEN/HALF_OPEN are parked via
  `reschedule_without_budget` — they do **not** spend `max_retries`. Item-class
  failures never touch the breaker.
+ ✅ Surfaced in `TaskWorker.get_status()['circuit_breakers']` (non-closed
  breakers only) for the queue monitor (#11).
+ Knobs: `QUEUE_CIRCUIT_BREAKER` (default on),
  `QUEUE_CIRCUIT_FAILURE_THRESHOLD` / `QUEUE_CIRCUIT_WINDOW_SECONDS` /
  `QUEUE_CIRCUIT_COOLDOWN_SECONDS`.

### Phase 4 — Idempotent restart recovery (Layer 4) ✅ DONE

+ ✅ Added `is_idempotent_stage()` (user chain download→summarize +
  REFRESH_FEED) generalising the spec #48 feed carve-out.
+ ✅ `recover_interrupted_tasks()` now resumes interrupted idempotent stages to
  `pending` and reserves `→ failed` for non-idempotent (entity-branch) stages.
  Explicitly-excluded stages (e.g. a cloud transcribe with a live remote job)
  still win over auto-resume and are left untouched in `processing`. Returns
  total recovered (resumed + failed).

### Phase 5 — Observability & alerting

+ Structured events for breaker open/close, heal attempts, and cap-exhausted
  terminal transitions.
+ Optional: a digest/notification when a task exhausts heal attempts (genuine
  human-attention case).

---

## Data Model Changes

Additive columns on `tasks` (all nullable / defaulted, migration via the
existing `_migrate_add_column` helper):

```sql
ALTER TABLE tasks ADD COLUMN error_class TEXT NULL;       -- 'infra' | 'item' | 'fatal'
ALTER TABLE tasks ADD COLUMN heal_attempts INTEGER DEFAULT 0;
ALTER TABLE tasks ADD COLUMN last_heal_at  TIMESTAMP NULL;
```

Circuit-breaker state is in-memory (per worker process); it does not need to
be persisted — a restart re-probes from a closed state, which is safe.

Timestamps follow the repo convention (`now_utc().isoformat()`, ISO-8601 with
`+00:00`; never raw `CURRENT_TIMESTAMP`).

---

## Failure & Edge Cases

+ **Poison message that always fails infra-class** — bounded by `heal_attempts`
  cap; after the cap it is terminal and loud, exactly like today but delayed.
+ **Misclassified item error as infra** — would retry forever; mitigated by
  the heal cap and by keeping `INFRA_PATTERNS` conservative (explicit strings,
  not broad regex).
+ **Breaker flapping** — half-open probes are rate-limited; require N
  consecutive probe successes before fully closing.
+ **`dead` is never auto-healed** — fatal means fatal; healing only ever
  touches `failed`. A `dead` row always requires explicit human action.
+ **Thundering herd on circuit close** — existing ±20% backoff jitter plus
  staged re-admission (don't release the whole backlog in one tick).
+ **User-cancelled task carrying an infra label** — `fail_task` (cancel path)
  now clears `error_class`, so the healer never resurrects a deliberately
  cancelled task. Found in review; fixed.
+ **Feed fetch error reported as success** — `RSSMediaSource.fetch_and_parse`
  returns an error *sentinel* (not an exception); `_refresh_single_podcast`
  now flags `had_error=True` on it so a DNS/HTTP feed outage retries/parks
  instead of silently clearing `last_refresh_error` (errors-as-empty-results
  anti-pattern, [[feedback_failure_mode_catalogue]]). Found in review; fixed.
+ **REFRESH_FEED task status 500** — the queued-task-status response required
  `episode_id: str`, but feed tasks are podcast-scoped (`episode_id=None`).
  Field is now nullable and `podcast_id` is returned. Found in review; fixed.

---

## Testing

+ Unit: classifier labels each representative `last_error` string correctly
  (getaddrinfo, runtime_unavailable, 429, malformed-input).
+ Unit: `heal_task()` respects cooldown + cap; refuses `dead`.
+ Integration: simulate a dependency outage → assert the stage circuit opens,
  in-flight tasks do **not** drain their budget, and they auto-recover after
  the dependency is restored (no manual intervention).
+ Integration: restart mid-`transcribe` → task resumes from `pending`, not
  `failed`.
+ Per [#42](42-robustness-and-failure-mode-hardening.md) guidance: avoid
  consistent-mock tests that pass because the mock never exercises the outage
  path — the outage simulation must actually break the dependency.

---

## Non-Goals

+ Replacing the SQLite queue with Redis/SQS (the interface is already
  abstracted for that; out of scope here).
+ Distributed/multi-worker breaker coordination — single-process breaker is
  sufficient for current deployments.
+ Auto-healing `dead`/fatal tasks.
+ Changing the manual Failed Tasks UI behavior (it remains the escape hatch).

---

## Open Questions

1. Unbounded infra retries vs a large fixed budget (e.g. 20) — which is safer
   against a silently-permanent dependency failure?
2. Should the breaker key on `stage` or on the underlying `dependency`
   (a single LLM provider serves multiple stages)?
3. Heal cap value and cooldown — `heal_attempts=2`, `cooldown=10min` as
   starting defaults?
4. ~~Should Layer 4 (idempotent restart recovery) feed back into the same heal
   counter, or is restart-resume unbounded since it's strictly idempotent?~~
   **Resolved:** restart-resume is unbounded — it does not touch
   `heal_attempts`. A crash loop is its own (rarer) failure mode; coupling it
   to the infra heal cap would conflate two distinct signals. Revisit if
   restart loops are observed in practice.
