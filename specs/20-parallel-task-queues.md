# Parallel Task Queues — Per-Stage Worker Pools

**Status**: 🚧 Active development
**Created**: 2026-04-19
**Updated**: 2026-04-19 (initial implementation shipped on branch `claude/parallel-task-queues-lzRWT`)
**Priority**: Medium (directly affects pipeline throughput whenever multiple episodes are in-flight)

## Overview

The background task worker previously ran a single poll loop over the queue,
gated by one `asyncio.Semaphore(parallel_jobs)`. Since stages typically run on
different hosts (Dalston for transcription, Gemini/OpenAI for cleaning and
summarization, local CPU for download/downsample), serializing them behind a
single queue meant a slow transcription task would block a fast cleaning task
on a different episode — even though the two physically cannot contend for the
same resource.

This spec introduces **one poll loop per pipeline stage**, each with its own
semaphore and capacity knob, so independent stages can execute in parallel.
The Task Queue page was redesigned to match: the status-grouped layout
becomes stage swimlanes, making the new concurrency model legible.

## Goals

1. Eliminate head-of-line blocking across pipeline stages. A stuck transcribe
   must not starve clean, summarize, or download on unrelated episodes.
2. Expose per-stage capacity as individual config knobs so operators can tune
   each pool to the host it lives on (e.g. `TRANSCRIBE_PARALLEL_JOBS=1`,
   `CLEAN_PARALLEL_JOBS=3`).
3. Preserve the per-episode linear ordering inside a pipeline
   (download → downsample → transcribe → clean → summarize), which the chain-
   enqueue logic already enforces.
4. Keep the default behavior compatible with `PARALLEL_JOBS=1` — each stage
   runs one task at a time, but they run **concurrently** across stages.
5. Visualize the new model so operators can see at a glance which stages are
   busy, which are idle, and which are backpressured.

## Non-goals

- Replacing SQLite with a dedicated queue (Redis/RQ/SQS). The per-stage
  refactor is a pure in-process change; `QueueManager.get_next_task(stage=…)`
  already supports stage-scoped queries, so no storage change is needed.
- Dynamic / adaptive capacity. Capacity is static per process start, set via
  env vars. Autoscaling is a future concern.
- Per-stage priority or fairness across podcasts. Today's priority rules
  (priority DESC, created_at ASC) stay in place, now scoped per stage.
- Distributed workers across machines. This stays a single-process worker.

## Background findings

Traced the task-processing flow:
[cli.py](../thestill/cli.py) and
[web/app.py:161](../thestill/web/app.py#L161) construct a single `TaskWorker`,
which spawns one background thread running
[task_worker.py `_async_worker_loop`](../thestill/core/task_worker.py). That
loop polled [`QueueManager.get_next_task()`](../thestill/core/queue_manager.py#L379)
with no stage filter.

### Pre-refactor behavior

- **Single FIFO across stages.**
  [`get_next_task`](../thestill/core/queue_manager.py#L420) ordered by
  `priority DESC, created_at ASC` with no stage filter — fast CLEAN tasks
  waited behind slow TRANSCRIBE tasks.
- **One semaphore, `parallel_jobs` slots.**
  [`task_worker.py:196`](../thestill/core/task_worker.py#L196) capped
  concurrency across all stages. Default `PARALLEL_JOBS=1` meant strict
  serialization of the entire pipeline.
- **Episode-level exclusion.** `exclude_episode_ids` prevented the same
  episode from being picked twice, but did nothing for cross-stage
  contention across different episodes.
- **Chain-enqueue.** After a successful task,
  [`_maybe_enqueue_next_stage`](../thestill/core/task_worker.py#L332) enqueued
  the next stage for the same episode. Stages are therefore **never**
  enqueued out of order for an episode — so per-stage parallelism cannot
  violate pipeline ordering.

### Already-present abstractions the refactor builds on

1. `TaskStage` enum in [queue_manager.py:56](../thestill/core/queue_manager.py#L56).
2. Optional `stage` parameter on
   [`get_next_task(stage=…)`](../thestill/core/queue_manager.py#L381) —
   already implemented, just never called with a stage filter.
3. Indexed queries (`idx_tasks_episode_stage_pending`) on stage column.
4. Per-stage handlers via
   [`create_task_handlers`](../thestill/core/task_handlers.py) — each stage
   already has an isolated handler.

These combined to mean the refactor is a **worker** change, not a **queue**
change.

## Design

### Worker model

Replace the single poll loop with one coroutine per `TaskStage`, each bounded
by its own `asyncio.Semaphore`. All coroutines share the same event loop on
the same background thread (unchanged).

```
TaskWorker (thread)
 └─ asyncio loop
     ├─ _stage_poll_loop(DOWNLOAD,    sem=capacity[DOWNLOAD])
     ├─ _stage_poll_loop(DOWNSAMPLE,  sem=capacity[DOWNSAMPLE])
     ├─ _stage_poll_loop(TRANSCRIBE,  sem=capacity[TRANSCRIBE])
     ├─ _stage_poll_loop(CLEAN,       sem=capacity[CLEAN])
     └─ _stage_poll_loop(SUMMARIZE,   sem=capacity[SUMMARIZE])
```

Each loop:

1. Checks `capacity - len(active_for_stage)` slots.
2. Calls `queue_manager.get_next_task(stage=X, exclude_episode_ids=…)`.
3. Dispatches `_process_task_async(task, sem, stage)`, which runs the handler
   in `asyncio.to_thread(...)` — same pattern as before.
4. Sleeps `poll_interval` seconds and repeats.

`_active_by_stage: Dict[TaskStage, Dict[str, Task]]` (stage → episode_id →
Task) replaces the flat `_active_tasks` dict. This keeps episode-level
exclusion correct per stage (the bump endpoint could in principle enqueue
multiple tasks for one episode; we defensively guard against picking an
already-in-flight episode-within-stage twice).

### Config

Added to [`Config`](../thestill/utils/config.py):

- `download_parallel_jobs: Optional[int]`
- `downsample_parallel_jobs: Optional[int]`
- `transcribe_parallel_jobs: Optional[int]`
- `clean_parallel_jobs: Optional[int]`
- `summarize_parallel_jobs: Optional[int]`

Each falls back to `parallel_jobs` (historical global knob) when unset. A new
helper `Config.get_parallel_jobs_per_stage()` returns a resolved dict.

Env vars: `DOWNLOAD_PARALLEL_JOBS`, `DOWNSAMPLE_PARALLEL_JOBS`,
`TRANSCRIBE_PARALLEL_JOBS`, `CLEAN_PARALLEL_JOBS`, `SUMMARIZE_PARALLEL_JOBS`.

Typical deployment (Dalston + Gemini):

```
PARALLEL_JOBS=1                # legacy default
DOWNLOAD_PARALLEL_JOBS=4       # network-bound, cheap
DOWNSAMPLE_PARALLEL_JOBS=2
TRANSCRIBE_PARALLEL_JOBS=1     # single GPU on Dalston
CLEAN_PARALLEL_JOBS=3          # Gemini 3 Flash scales out
SUMMARIZE_PARALLEL_JOBS=2
```

Default (all unset) is one worker per stage — **five concurrent tasks** at
peak, one per stage. This alone solves the original bottleneck.

### API surface

[`/api/commands/queue/tasks`](../thestill/web/routes/api_commands.py) gains a
`stages: StageWorkerStatus[]` field in pipeline order, with
`{stage, active, capacity, pending, retry_scheduled}` per entry. The legacy
`worker_running` boolean and aggregate counts are preserved for backward
compat.

`TaskWorker.get_status()` now returns a `stages` sub-dict with per-stage
`{active, capacity}`, driven directly from the in-memory state.

### UI — stage swimlanes

The Task Queue page ([QueueViewer.tsx](../thestill/web/frontend/src/pages/QueueViewer.tsx))
was reorganized around the new mental model:

```
┌─ Pipeline Stages ──────────────────────────────────┐
│ ▶ Download     [2/4 busy]  pending: 3 · bar graph  │
│   - Episode A                                      │
│   - Episode B                                      │
│ ▶ Transcribe   [1/1 busy]  pending: 5 (RED)        │
│   - Episode C (Processing for 4m12s)               │
│ ▶ Clean        [1/2 busy]  pending: 0              │
│   - Episode D                                      │
│ Idle: Downsample (2), Summarize (2)                │
└────────────────────────────────────────────────────┘
Retry Scheduled (2)
Recently Completed (10)
```

- Active lanes (busy **or** with pending work) render fully.
- Idle lanes collapse into a single line to save vertical space.
- Lanes flash red when `pending >= 3 × capacity` — a visual hint for
  operators that the stage is the current bottleneck and needs more
  workers.
- Retry-scheduled and recently-completed tasks keep their status-grouped
  sections below (they're inherently cross-stage).

## Tradeoffs

### Kept (+)

- **Cross-stage parallelism by default.** Even with `PARALLEL_JOBS=1`, five
  stages run concurrently (one task each), which is the unblock the user
  explicitly asked for.
- **No queue / storage change.** SQLite schema is unchanged;
  `get_next_task(stage=…)` was already supported.
- **Zero-migration rollout.** Tasks enqueued before the upgrade keep working;
  per-stage env vars default to the legacy global.
- **Bump / cancel still work.** They operate on task IDs, which are unchanged.

### Gave up (-)

- **More knobs to tune.** Five per-stage env vars instead of one. Mitigation:
  all fall back to `PARALLEL_JOBS`, so users who don't care can keep using
  the single knob. The UI help text points operators to the per-stage knobs
  only when a lane turns red.
- **Five poll queries per poll interval instead of one.** At the default 2 s
  poll interval, that's 2.5 SQLite queries/sec — negligible on an indexed
  table. We considered a single "all stages with slots" query but rejected
  it because it complicates slot accounting across async coroutines.
- **Local stages can over-parallelize CPU if misconfigured.** If someone
  sets `DOWNSAMPLE_PARALLEL_JOBS=16` on a 2-core machine they'll thrash.
  We chose not to cap automatically — that's standard operator territory,
  and the defaults are conservative (fall back to `PARALLEL_JOBS=1`).
- **Five always-visible UI lanes take more vertical space than the old
  compact stats row.** Mitigated by collapsing idle lanes into one line and
  keeping the aggregate stats grid at the top.

### Rejected alternatives

- **Per-episode parallelism (more `parallel_jobs`) with single queue.**
  Doesn't solve the bottleneck. Episode A's transcribe still blocks a slot
  that could've been used by episode B's clean.
- **Status-grouped UI with per-stage badges sprinkled in.** Keeps the old
  mental model but hides the new capability. The point of the refactor is
  that stages are independent; the UI should make that legible.
- **`asyncio.Queue` per stage feeding a worker pool.** Would require
  in-memory queues decoupled from SQLite, plus a dispatcher that moves
  tasks DB → queue. Adds two failure modes (lost enqueues on crash, queue
  drift vs DB) for no functional gain over polling-per-stage at a 2 s
  cadence.
- **Process-level isolation (separate workers per stage).** Would enable
  horizontal scale-out, but overshoots today's need (single-user, single
  host) and multiplies operational surface area (five supervisord units).
  If/when a multi-host deploy lands, the poll-per-stage design ports to
  per-process workers trivially — each process just starts one poller.

## Implementation summary

Files changed (branch `claude/parallel-task-queues-lzRWT`):

- `thestill/core/task_worker.py` — per-stage poll loops, per-stage active
  tracking, per-stage semaphores, `get_status()` exposes per-stage
  utilization. `stop()` no longer calls `loop.stop()`; pollers exit
  cleanly on `_running = False`.
- `thestill/utils/config.py` — five per-stage `*_parallel_jobs` fields,
  five env-var bindings, `get_parallel_jobs_per_stage()` helper.
- `thestill/web/app.py` — passes resolved per-stage capacities into
  `TaskWorker(..., parallel_jobs_per_stage=...)`.
- `thestill/web/routes/api_commands.py` — new `StageWorkerStatus` response
  model; `/api/commands/queue/tasks` returns `stages: [...]` alongside
  existing fields.
- `thestill/web/frontend/src/api/types.ts` — `StageWorkerStatus` type;
  `QueueTasksResponse.stages` field.
- `thestill/web/frontend/src/pages/QueueViewer.tsx` — full redesign around
  stage swimlanes with collapsed idle lane, utilization bar, backpressure
  flagging.

## Verification

- Smoke test: with `PARALLEL_JOBS=1` (default), enqueuing TRANSCRIBE (slow,
  500 ms) and CLEAN (fast, 50 ms) for different episodes simultaneously
  confirms both stages start within 3 ms of each other. Under the old
  worker, CLEAN would wait ~500 ms for TRANSCRIBE to finish.
- `npx tsc -b` clean; `npm run build` produces a working bundle.
- Worker `stop()` drains cleanly with no `Event loop stopped before Future
  completed` errors.

## Follow-ups / open questions

- **Autoscaling per stage.** Today capacity is static per process. A
  follow-up could watch the per-stage backpressure signal (the same one
  driving the red lane indicator) and adjust capacity dynamically.
- **Per-stage metrics / throughput chart.** The UI currently shows
  `active/capacity` and pending depth. Per-stage tasks-per-minute over a
  rolling window would make tuning easier.
- **Pause/resume per stage.** If transcribe's host goes offline, an
  operator might want to pause that lane without pausing the whole
  worker. Today's `_running` flag is process-wide.
- **Surface `metadata.run_full_pipeline` in the UI.** When a pending
  TRANSCRIBE is part of a full-pipeline chain vs a one-off, the UI
  doesn't currently distinguish.
