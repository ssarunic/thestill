# Refresh as a Pipeline Stage — `REFRESH_FEED`

> **Status:** 📝 Draft (2026-06-10)
> **Created:** 2026-06-10
> **Updated:** 2026-06-10
> **Author:** Engineering
> **Related:** [#19 refresh-performance](19-refresh-performance.md) (owns the parallel `refresh_feeds` batch + conditional-GET this spec decomposes), [#20 parallel-task-queues](20-parallel-task-queues.md) (per-stage worker pools this stage plugs into), [#42 robustness](42-robustness-and-failure-mode-hardening.md) (FM-2 checkpoint-before-durability + FM-4 silent-fleet counter this must preserve), [#28 corpus-search-and-entities](28-corpus-search-and-entities.md) (the `_NON_USER_FAILING_STAGES` failure-isolation pattern this mirrors for a feed-scoped domain), [#44 postgres-migration](44-postgres-migration.md) (schema change below must stay forward-compatible)

---

## Executive Summary

Today `thestill refresh` is a **monolithic in-process batch**: `refresh_feeds()`
([feed_manager.py:471](../thestill/core/feed_manager.py#L471)) fans every feed out
across a `ThreadPoolExecutor`, accumulates all changed podcasts and new episodes
in memory, then persists the whole lot in **one end-of-batch transaction**
(`save_refresh_batch`, [feed_manager.py:628](../thestill/core/feed_manager.py#L628)).

This is well-built for single-user scale — all network I/O happens *outside* the
transaction, and the commit is fast. But it has a hard ceiling:

- **Throughput is one process.** Concurrency is `max_workers` threads in one CLI
  invocation. There is no horizontal scale across worker processes/machines.
- **All-or-nothing visibility.** Episodes from the first feed aren't persisted
  (and can't start `DOWNLOAD`) until *every* feed in the batch finishes. One slow
  feed delays all of them.
- **Thundering herd.** Every feed is fetched in one burst each cycle; there is no
  per-feed cadence or load-spreading.
- **Batch-scoped failure accounting.** Error isolation exists (FM-2 excludes
  errored feeds from the commit; FM-4 counts them), but retry is "run the whole
  refresh again," not per-feed.

This spec promotes per-feed refresh to a **queued pipeline stage**, `REFRESH_FEED`,
so it rides the same worker infrastructure as `DOWNLOAD … ENRICH_ENTITIES`
([#20](20-parallel-task-queues.md)). One task = one feed. The win is **not raw
speed** (concurrency already exists) — it is **failure isolation, horizontal
scale, per-feed cadence, and incremental persistence**.

## Why a stage, not a bigger thread pool

Bumping `max_workers` (default **1**, [feed_manager.py:84](../thestill/core/feed_manager.py#L84))
is the right *first* move and is independent of this spec. The stage is the
*scaling* move, justified when feed count grows past what one process should fan
out. Rejected alternatives:

- **Chunk the existing loop into batches-of-N.** Adds a barrier between chunks
  (idles fast workers behind one slow feed in the chunk) and buys nothing the
  queue doesn't: no horizontal scale, no independent retry, no cadence. A
  half-step.
- **Async I/O (aiohttp) in one process.** Scales fan-out further than threads,
  but still one process, still one failure domain, still no cadence. The queue
  model is more in keeping with the rest of the pipeline and reuses the DLQ /
  retry / queue-viewer machinery we already have.

## The central tension: the queue is episode-centric

`REFRESH_FEED` is the first **podcast-scoped** stage in an otherwise
**episode-scoped** queue. This is the crux of the design:

- `Task.episode_id` is **required** and carries `FOREIGN KEY (episode_id)
  REFERENCES episodes(id)` ([queue_manager.py:400](../thestill/core/queue_manager.py#L400)).
- The worker's concurrency guard is a **per-episode mutex**
  (`exclude_episode_ids`, [task_worker.py:296](../thestill/core/task_worker.py#L296)),
  meaningless for a feed task.
- A `REFRESH_FEED` task has **no episode** at enqueue time — it *produces* them.
- Failure handling (`_mark_episode_failed`, [task_worker.py:525](../thestill/core/task_worker.py#L525))
  writes `episodes.failed_at_stage`; there is no episode to mark.

### Schema decision (recommended: nullable `episode_id` + `podcast_id`)

Make the queue able to carry a podcast-scoped target without overloading
`episode_id`:

```sql
-- tasks table (new columns; both nullable for forward-compat with #44 Postgres)
ALTER TABLE tasks ADD COLUMN podcast_id TEXT REFERENCES podcasts(id);
-- episode_id becomes nullable; exactly one of (episode_id, podcast_id) is set,
-- enforced by a CHECK in the rebuilt table (SQLite needs table rebuild, which
-- queue_manager already does once — see tasks_new_spec28).
CREATE INDEX idx_tasks_podcast_stage ON tasks(podcast_id, stage);
```

Rejected: **stuffing the podcast id into `episode_id`**. It breaks the FK, the
per-episode mutex would treat a feed id as an episode, and every episode-scoped
query (`idx_tasks_episode_id`, DLQ joins, queue viewer) would need a sentinel
filter. The nullable-column split is one table rebuild (we already do one for
spec #28) and keeps both target types first-class.

`add_task` gains an overload / sibling `add_feed_task(podcast_id, stage=…)`; the
worker's mutex logic skips feed tasks (they coordinate by feed, see Coalescing).

## Design

`REFRESH_FEED` is a **root/producer** stage — no predecessor, dynamic fan-out:

```
REFRESH_FEED (one feed) ──► DOWNLOAD (episode 1)
                       ├──► DOWNLOAD (episode 2)
                       └──► … one per newly-discovered episode
```

It does **not** live in `STAGE_SUCCESSORS` (that map is static one→one; this
fan-out is dynamic and data-driven). The handler enqueues `DOWNLOAD` per new
episode directly, exactly as the post-refresh pipeline kickoff does today.

### The unit of work already exists

`_refresh_single_podcast(podcast, max_episodes_per_podcast, known_external_ids)`
([feed_manager.py:611](../thestill/core/feed_manager.py#L611)) is *precisely* one
feed's fetch. `handle_refresh_feed` wraps it:

1. Load the podcast + its known `external_id`s (one feed's worth, not the corpus).
2. Call `_refresh_single_podcast` — conditional-GET, parse, diff. Network happens
   **outside** any DB transaction (preserves the #19 property).
3. **Per-feed persist** in its own short transaction: `save_refresh_batch([podcast],
   new_episode_rows)` for just this feed. This is the incremental-visibility win —
   feed 1's episodes are durable and downloadable while feed 2 is still fetching.
4. Enqueue `DOWNLOAD` for each new episode.
5. Best-effort transcript-link extraction (unchanged, still outside the txn).

The monolithic `refresh_feeds` batch persist becomes N per-feed persists. Each is
tiny, so the SQLite writer is held briefly and often rather than once and long —
friendlier to the concurrently-running web server.

### Failure isolation — a new feed-scoped domain (mirrors #28 §6)

A `REFRESH_FEED` failure must **not** touch any episode's `failed_at_stage` (there
is no episode) and must **preserve FM-2**: never certify the etag / last_modified /
last_processed checkpoint on a failed fetch. Add a feed-scoped analogue of the
entity-branch contract:

- New `_FEED_SCOPED_STAGES = frozenset({TaskStage.REFRESH_FEED})` and an
  `is_feed_scoped_stage(stage)` predicate, parallel to `is_entity_branch_stage`
  ([queue_manager.py:108](../thestill/core/queue_manager.py#L108)).
- `_mark_episode_failed` branches: for feed-scoped stages it writes a
  **podcast-level** failure (`podcasts.last_refresh_error` / `last_refresh_at`),
  not an episode row.
- **FM-2 is structural here.** The handler simply doesn't persist the podcast's
  rotated cache headers on the failure path (don't add it to `changed_podcasts`),
  so the next `REFRESH_FEED` re-fetches instead of receiving a self-hiding 304.
  This is the same rule as today's `_record_outcome`, just per-task.
- **FM-4 (silent fleet)** moves from a batch counter to per-task DLQ rows: a feed
  that keeps erroring lands in the Dead Letter Queue with `stage=refresh-feed`,
  which is *more* visible than the aggregate `podcasts_with_errors` count, not
  less. The scheduler (below) reads DLQ depth for the silent-fleet alarm.

### Coalescing — one in-flight refresh per feed

Two `REFRESH_FEED` tasks for the same podcast must never run concurrently (they'd
double-fetch and race on the cache-header write). Reuse the coalescing primitive
already used by the corpus stages (`claim_pending_for_coalescing`,
[queue_manager.py:915](../thestill/core/queue_manager.py#L915)) keyed on
`podcast_id`, **and** a uniqueness guard at enqueue: skip `add_feed_task` if a
non-terminal `REFRESH_FEED` row already exists for that podcast. The per-episode
mutex is replaced by a per-podcast mutex for this stage.

### Per-host politeness

Today the per-host throttle lives inside the pool
([feed_manager.py:300](../thestill/core/feed_manager.py#L300)) — "don't slam one
CDN when many feeds share a host." In the queue model, workers are independent, so
this must move to a **host-level rate limiter** shared across workers (token bucket
keyed on feed host), or be approximated by capping
`parallel_jobs_per_stage[REFRESH_FEED]`. Single-process deployments can keep an
in-memory limiter; the #44 Postgres / multi-process future needs a shared one
(out of scope here, flagged as a follow-up).

## Scheduling — what enqueues the tasks

A lightweight **scheduler** replaces the monolithic burst:

- `thestill refresh` (and the future cron tick) enqueues `REFRESH_FEED` for **due**
  feeds, not all feeds. "Due" = per-podcast `refresh_interval` elapsed since
  `last_refresh_at`. This spreads load across the window and lets noisy feeds poll
  faster than monthly ones — the cadence the monolith can't express.
- Conditional-GET still makes a due-but-unchanged feed nearly free (304), so over-
  scheduling is cheap.
- The scheduler is the FM-4 owner: it alarms on `REFRESH_FEED` DLQ depth and on
  feeds whose `last_refresh_at` has gone stale (a feed that stopped being
  enqueued at all — the spec #42 "silent fleet" failure, caught structurally).

## Backward compatibility & rollout

- **Keep `refresh_feeds` for single-user CLI.** `thestill refresh` gains a path
  selector: inline batch (today, default for now) vs. enqueue `REFRESH_FEED`
  tasks. Gate the queued path behind a config flag (`REFRESH_VIA_QUEUE`) so it
  ships dark and flips per deployment.
- **`get_new_episodes` / `RefreshResult` shape unchanged** for the inline path.
- The queued path reuses `_refresh_single_podcast`, so feed-parsing behavior is
  identical between paths — no relevance/dedup drift.

## Observability & UX

- Queue viewer ([#10](10-queue-viewer.md)) / monitor ([#11](11-task-queue-monitor.md))
  must render a feed task by **podcast title**, not episode. The viewer currently
  joins on `episode_id`; add a `podcast_id` join branch.
- Admin-gating: the task queue + failed-tasks pages are already admin-only (commit
  `7a67017`); `REFRESH_FEED` rows inherit that gate.

## Out of scope / follow-ups

- Shared cross-process host rate limiter (needed only post-#44 multi-process).
- Cron infrastructure for the scheduler tick (this spec defines *what* gets
  enqueued; *when* is the existing refresh trigger / a future cron).
- Migrating the inline path away — it stays as the single-user default until the
  queued path is proven.

## Testing (per [#04](04-testing.md))

- **Handler unit:** `handle_refresh_feed` on a feed with N new episodes →
  per-feed persist called once, N `DOWNLOAD` tasks enqueued, transcript-link work
  attempted.
- **FM-2 regression:** a fetch that errors must leave the podcast's stored
  etag/last_modified **unchanged** (next task re-fetches, no 304 self-hide). This
  is the 20VC-incident test, re-expressed per-task.
- **Coalescing:** two `REFRESH_FEED` enqueues for one podcast → second is skipped /
  coalesced; never two concurrent fetches of one feed.
- **Failure domain:** a dead `REFRESH_FEED` writes `podcasts.last_refresh_error`
  and a DLQ row, and touches **no** `episodes.failed_at_stage`.
- **Schema:** `add_feed_task` round-trips with null `episode_id`; episode-scoped
  queries/indexes ignore feed rows; the CHECK rejects rows with both/neither id.
- **Parity:** queued path and inline path discover the same episodes for a fixture
  feed (shared `_refresh_single_podcast`).
