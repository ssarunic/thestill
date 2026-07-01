# Refresh as a Pipeline Stage — `REFRESH_FEED`

> **Status:** 🚧 Active development (2026-07-01 — corrected: `REFRESH_FEED` stage live in the queue, ships dark behind `REFRESH_VIA_QUEUE`)
> **Created:** 2026-06-10
> **Updated:** 2026-06-16
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

`add_task` gains an overload / sibling `add_feed_task(podcast_id, stage=…)`.

#### Nullable `episode_id` breaks three worker paths — fix them first

"The mutex skips feed tasks" is too glib: nullable `episode_id` silently corrupts
the existing claim logic, so a **task target abstraction must land before** feed
tasks are allowed onto the queue. Three concrete breakages:

- **Claim filter excludes feed tasks.** `get_next_task` appends `episode_id NOT IN
  (…)` ([queue_manager.py:688](../thestill/core/queue_manager.py#L688)). In SQL
  three-valued logic `NULL NOT IN (…)` is `NULL`, not `TRUE`, so **every**
  `REFRESH_FEED` row is filtered out whenever *any* episode task is active — feed
  refresh silently starves behind the heavy stages.
- **In-memory key collapse.** The worker keys active tasks by `task.episode_id`
  (`active[task.episode_id] = task`, the `any(task.episode_id in s …)` recheck —
  [task_worker.py:304](../thestill/core/task_worker.py#L304),
  [task_worker.py:306](../thestill/core/task_worker.py#L306)). Multiple feed tasks
  all key on `None`, so they collapse onto one slot / falsely collide.
- **Mutex is the wrong dimension.** The per-episode mutex is meaningless for a
  feed task; coalescing for this stage is per-*podcast* (see Coalescing).

Required: replace the raw `episode_id` key with a **target abstraction**
(`target_scope ∈ {episode, podcast}` + `target_id`), or add a parallel
`exclude_podcast_ids` / active-by-podcast path so the claim filter and the
in-memory active-set both key on the correct dimension per stage. Episode-scoped
behavior must be unchanged for existing stages.

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
3. **Inspect `had_error` and raise — do not swallow it.**
   `_refresh_single_podcast` catches its own exceptions, sets `had_error=True`, and
   **returns normally** ([feed_manager.py:403](../thestill/core/feed_manager.py#L403),
   [feed_manager.py:423](../thestill/core/feed_manager.py#L423)) — a batch contract,
   not a task contract. If the handler completes on that tuple, a failed fetch
   becomes a **completed** `REFRESH_FEED` task: no retry, no DLQ row, no FM-4
   signal. The handler must therefore translate `had_error` (and any exception the
   batch path would have logged) into a raised `TransientError` / `FatalError`
   **before** `complete_task`, on the success path *only* after persist below. The
   failure path must **not** persist rotated cache headers (FM-2, see Failure
   isolation).
4. **Per-feed persist** in its own short transaction: `save_refresh_batch([podcast],
   new_episode_rows)` for just this feed. This is the incremental-visibility win —
   feed 1's episodes are durable and downloadable while feed 2 is still fetching.
5. **Reconcile inserted episode ids before fan-out.** `save_refresh_batch` uses
   `INSERT OR IGNORE` ([sqlite_podcast_repository.py:2828](../thestill/repositories/sqlite_podcast_repository.py#L2828))
   to survive the concurrent-refresh race on `(podcast_id, external_id)`. So an
   in-memory `Episode.id` is **not guaranteed durable** — an inline refresh or a
   second worker may have inserted that `external_id` first under a different id.
   Enqueuing `DOWNLOAD` on the in-memory id would then hit a dangling FK or target
   the wrong row. Persist must return (or the handler must re-query) the
   **actually-resolved** `episodes.id` per `(podcast_id, external_id)`, and only
   those resolved ids feed step 6.
6. Enqueue `DOWNLOAD` for each new episode (using reconciled ids from step 5).
7. Best-effort transcript-link extraction (unchanged, still outside the txn).

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
double-fetch and race on the cache-header write). **Do not reuse
`claim_pending_for_coalescing` as-is** — despite the name it is corpus-scoped, not
feed-scoped: it takes only `stage`, marks **every** pending row of that stage
`COMPLETED`, and returns `episode_id`s
([queue_manager.py:915](../thestill/core/queue_manager.py#L915)). Called for one
feed it would silently complete *all* pending `REFRESH_FEED` tasks (and return
null ids). It is the wrong shape here.

Per-feed coalescing is simpler and needs no batch-claim at all:

- **Enqueue uniqueness guard** — `add_feed_task` skips if a non-terminal
  `REFRESH_FEED` row already exists for that `podcast_id` (one indexed lookup on
  `idx_tasks_podcast_stage`).
- **Per-podcast active mutex** — the `target_scope=podcast` active-by-podcast set
  from the worker target abstraction (above) prevents two feed tasks for the same
  podcast running at once.

Those two together are sufficient. If a batched claim is ever wanted (it is not
required here), it must be a **new** `claim_pending_for_podcast_coalescing(
podcast_id, stage)` scoped by podcast — never the corpus-wide method.

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

## Background scheduling & adaptive cadence

The scheduler above answers *what* gets enqueued; this section answers *when*, so
the queued path can run as a continuous background service that discovers episodes
soon after publication **without** polling every feed on a fixed clock (which
either starves latency or floods the queue).

### A tick is not a poll interval

Do **not** "refresh every 15 min." Run a cheap **scheduler tick** (every ~1 min)
that only asks *which feeds are due now* and enqueues those. The tick is the
scheduling *granularity*; the per-feed interval is independent and adaptive. The
due-query is an indexed range scan, so the tick is nearly free even at 10⁴ feeds.

The `podcasts` table today has **none** of these columns — it stores only
`last_processed`, cache headers, and `updated_at`
([sqlite_podcast_repository.py:1870](../thestill/repositories/sqlite_podcast_repository.py#L1870)).
All four below are new, and `save_refresh_batch` (which currently writes only
`last_processed` / `etag` / `last_modified` / `updated_at`,
[sqlite_podcast_repository.py:2818](../thestill/repositories/sqlite_podcast_repository.py#L2818))
must be extended to maintain them:

```sql
-- per-feed cadence + failure state (all new; ISO-8601 UTC text per the
-- strftime('%Y-%m-%dT%H:%M:%S+00:00') convention, never CURRENT_TIMESTAMP)
ALTER TABLE podcasts ADD COLUMN refresh_interval_seconds INTEGER;  -- adaptive
ALTER TABLE podcasts ADD COLUMN next_refresh_at TEXT;              -- precomputed, indexed
ALTER TABLE podcasts ADD COLUMN last_refresh_at TEXT;             -- last attempt (success OR error)
ALTER TABLE podcasts ADD COLUMN last_refresh_error TEXT;          -- NULL on success; set on failure
CREATE INDEX idx_podcasts_due ON podcasts(next_refresh_at) WHERE is_complete = 0;
```

`last_refresh_at` / `last_refresh_error` are the feed-scoped failure target named
in Failure isolation and the staleness signal the FM-4 scheduler reads — both are
load-bearing, so they ship in this DDL, not later. Update semantics per outcome:

| Outcome (per task) | `last_refresh_at` | `last_refresh_error` | cache headers | `next_refresh_at` |
| ------------------ | ----------------- | -------------------- | ------------- | ----------------- |
| 200, new episodes  | now               | cleared (NULL)       | rotated       | shortened (AIMD)  |
| 200, no new / 304  | now               | cleared (NULL)       | rotated       | lengthened (AIMD) |
| retryable error    | now               | error string         | **untouched** (FM-2) | backoff    |
| terminal failure   | now               | error string         | **untouched** (FM-2) | **NULL (parked)** |

The tick body: `SELECT id FROM podcasts WHERE next_refresh_at IS NOT NULL AND
next_refresh_at <= now AND is_complete = 0`, minus any feed with a non-terminal
`REFRESH_FEED` task (coalescing, above), then `add_feed_task` for each. That is the
entire background loop. *When* the tick fires (internal timer vs. cron) is the
existing out-of-scope item; this section defines what it computes.

`next_refresh_at IS NULL` is the **parked** state — a feed that is not scheduled
(never seeded, or terminally failed; see Migration & terminal pause below). The
`<= now` comparison alone is not enough: in SQLite `NULL <= now` is `NULL`, so a
parked feed is excluded either way, but stating the `IS NOT NULL` guard makes the
two distinct "not due" reasons (parked vs. future) explicit.

### Migration & seeding — existing feeds must become due

The cadence columns are nullable with **no default**, so a migration that only
adds them leaves every existing (and every newly-inserted) podcast with
`next_refresh_at = NULL` — permanently parked, and the queued path enqueues
**zero** feeds. Required:

- **Backfill** active (`is_complete = 0`) podcasts at migration time:
  `refresh_interval_seconds = default` and `next_refresh_at = now + jitter` (spread
  across the first interval window, per the jitter guardrail — not all at `now`).
- **Insert path** (`add` / podcast creation) seeds both fields so a brand-new feed
  is immediately due (or due after a small jitter).
- **`save_refresh_batch`** is the only writer that advances them thereafter (per
  the outcome table above).

### Terminal failure pause — park, don't re-enqueue

The DLQ does **not** pause scheduling on its own: a terminally-failed/dead
`REFRESH_FEED` task is no longer *non-terminal*, so the coalescing skip stops
applying and the next due tick would enqueue a fresh task — minting a new DLQ row
every interval. The pause must be on the **podcast**, not the task: on terminal
failure (handler exhausts retries / DLQ), set `next_refresh_at = NULL` to park the
feed. It then never reappears in the due query until **operator retry** re-arms it
(`clear_podcast_refresh_failure` resets `last_refresh_error` and sets a fresh
`next_refresh_at` — the same path as the DLQ operator action). `last_refresh_error`
stays set while parked, so the FM-4 staleness alarm still sees it.

### Adaptive interval (AIMD)

Recompute `next_refresh_at` at the end of every refresh, learning each feed's
rhythm instead of using a static `refresh_interval`:

- **New episodes found** → multiplicatively *decrease* the interval (a fresh drop
  may be imminent; check back sooner) and update the cadence estimate.
- **304 / no change** → *increase* toward a cap (e.g. ×1.5).
- Clamp to `[min, max]`, e.g. **5 min … 24 h**.
- Floor at the feed's RSS `<ttl>` when present — a free publisher hint; never poll
  faster than it asks.
- `is_complete = 1` → stop scheduling entirely.
- On a *retryable* fetch error → exponential backoff on `next_refresh_at` and (per
  FM-2) **do not** certify the cache headers.
- On *terminal* failure (retries exhausted → DLQ) → **park** the feed
  (`next_refresh_at = NULL`) so it stops being re-enqueued every interval; only
  operator retry re-arms it (see Terminal failure pause above).

**Predictive layer (optional, larger win).** Track the publish-time distribution
(day-of-week / hour). Most shows drop on a schedule; inside the predicted window
drop to `min` interval, outside it back off hard. This concentrates the polling
budget in the ~2-hour window where the episode actually lands and beats uniform
polling on **both** latency and load.

### Don't-choke-the-queue guardrails

`REFRESH_FEED` tasks are short (network + a tiny write) but compete for worker
slots with long heavy stages (transcription). Isolate them:

- **Reserved, capped lane.** Give `REFRESH_FEED` a small dedicated
  `parallel_jobs_per_stage` cap (e.g. 2–4) separate from the heavy-stage pool. A
  refresh backlog must never block a transcribe, and a feed storm can never
  monopolize workers.
- **Jitter the enqueue.** Never release all due feeds at `:00`; spread
  `next_refresh_at` by hashing the feed id into the interval window so refresh
  load is smooth, not a thundering herd on the minute (subsumes the #19 burst).
- **Coalescing + per-host token bucket** (above) cap per-feed and per-CDN pile-up.

### Push (deferred — needs a public callback)

WebSub / PubSubHubbub (`<atom:link rel="hub">`) and the podcast-native **Podping**
firehose give near-zero discovery latency *and* remove speculative polls — but
both require an internet-reachable HTTPS callback / consumer. A local
single-process deployment (the current default) can't host one without a tunnel,
so push is **out of scope until the deployed, multi-process #44 future**.
Adoption of `rel="hub"` among podcast feeds is low and best measured empirically
(scan tracked feeds for a hub link) rather than assumed. When deployed, push
becomes Lever 1 and polling the fallback.

### Scaling

Refresh load is `req/s = N_feeds / avg_interval`, almost all conditional-GET 304s;
the constraint is request rate and worker slots, not bandwidth. Adaptive cadence
(most feeds don't deserve 15-min polling, effective avg ≈ 1 h) collapses the heavy
case versus naïve uniform polling:

| Feeds  | Naïve 15-min (req/s) | Adaptive ~1 h (req/s) | What it takes (adaptive) |
| ------ | -------------------- | --------------------- | ------------------------ |
| 50     | 0.06                 | 0.014                 | trivial, single thread   |
| 500    | 0.6                  | 0.14                  | trivial — today's scale  |
| 5,000  | 5.6                  | 1.4                   | a few workers, one box   |
| 50,000 | 56                   | 14                    | one beefy box / small fleet + shared host limiter (#44) |

**Refresh never becomes the bottleneck.** Even 50k feeds at 14 req/s is one
well-tuned process. What forces real horizontal infrastructure is *downstream*:
50k feeds publishing ~weekly ≈ **7,000 new episodes/day (~5/min)** to download →
transcribe → clean → summarize, where transcription is minutes of compute per
episode. The queue stage earns its keep at 5k–50k for failure isolation and
cadence; the transcription backlog is the wall.

### Freshness priority (flag, downstream)

Fast discovery is wasted if a newly-published episode then sits behind a deep
backfill in `DOWNLOAD`/`TRANSCRIBE`. For true end-to-end ASAP, fresh episodes
should carry **priority** over backfill downstream. The plumbing already exists —
`get_next_task` orders by `priority DESC, created_at ASC`
([queue_manager.py:698](../thestill/core/queue_manager.py#L698)) — so the handler
just enqueues fresh `DOWNLOAD` tasks at a higher `priority` than backfill. Out of
scope to tune here, but the latency goal is unmet without using it.

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
- **Operator/DLQ actions are episode-only today and must branch.** The retry and
  list paths call `get_episode(task.episode_id)` and clear *episode* failure state
  on retry ([api_commands.py:1540](../thestill/web/routes/api_commands.py#L1540),
  [api_commands.py:1624](../thestill/web/routes/api_commands.py#L1624),
  [api_commands.py:1713](../thestill/web/routes/api_commands.py#L1713)). For a
  feed task these would dereference a null episode and clear the wrong state.
  Required: a podcast-lookup branch for display, a retry that calls
  `clear_podcast_refresh_failure` (resets `last_refresh_error` + re-arms
  `next_refresh_at`) rather than episode failure state, and stage/branch filters
  that **don't** lump `REFRESH_FEED` into the user-facing episode stages.
- Admin-gating: the task queue + failed-tasks pages are already admin-only (commit
  `7a67017`); `REFRESH_FEED` rows inherit that gate.

## Out of scope / follow-ups

- Shared cross-process host rate limiter (needed only post-#44 multi-process).
- Cron infrastructure for the scheduler tick (this spec defines *what* gets
  enqueued; *when* is the existing refresh trigger / a future cron).
- Push ingestion (WebSub / Podping) — deferred until an internet-reachable
  deployment exists (post-#44); see Background scheduling above.
- Downstream freshness priority (fresh-episode lane / `priority` column) — the
  end-to-end latency goal depends on it but it is a separate change.
- Migrating the inline path away — it stays as the single-user default until the
  queued path is proven.

## Testing (per [#04](04-testing.md))

- **Handler unit:** `handle_refresh_feed` on a feed with N new episodes →
  per-feed persist called once, N `DOWNLOAD` tasks enqueued, transcript-link work
  attempted.
- **`had_error` propagation:** `_refresh_single_podcast` returning
  `had_error=True` makes the handler **raise** (Transient/Fatal) → task retries /
  lands in DLQ; it must **not** `complete_task`. Regression guard against
  swallowing the batch-contract error flag.
- **Insert reconciliation:** when `(podcast_id, external_id)` already exists (race
  / inline path), `INSERT OR IGNORE` keeps the prior row; the handler enqueues
  `DOWNLOAD` against the **resolved** episode id, never a dangling in-memory id.
- **Nullable-`episode_id` claim:** a `REFRESH_FEED` task is still claimable while
  episode tasks are active (no `NULL NOT IN` starvation); two feed tasks don't
  collapse onto one active-set slot.
- **Operator retry:** retrying a dead `REFRESH_FEED` from the DLQ clears
  `podcasts.last_refresh_error` / re-arms `next_refresh_at` and touches no episode
  failure state; episode-stage filters exclude `REFRESH_FEED`.
- **FM-2 regression:** a fetch that errors must leave the podcast's stored
  etag/last_modified **unchanged** (next task re-fetches, no 304 self-hide). This
  is the 20VC-incident test, re-expressed per-task.
- **Coalescing:** two `REFRESH_FEED` enqueues for one podcast → second is skipped /
  coalesced; never two concurrent fetches of one feed. **Negative guard:** the
  corpus-wide `claim_pending_for_coalescing` is *not* used for this stage (one
  feed's run must not complete other podcasts' pending `REFRESH_FEED` rows).
- **Migration/seeding:** after the migration, active existing podcasts have a
  non-null jittered `next_refresh_at` (the tick enqueues them); a newly-added
  podcast is due without a manual refresh; due times are spread, not all `now`.
- **Terminal pause:** a feed driven to terminal failure is parked
  (`next_refresh_at = NULL`) and the tick does **not** re-enqueue it on subsequent
  ticks (no repeated DLQ rows); operator retry re-arms it.
- **Failure domain:** a dead `REFRESH_FEED` writes `podcasts.last_refresh_error`
  and a DLQ row, and touches **no** `episodes.failed_at_stage`.
- **Schema:** `add_feed_task` round-trips with null `episode_id`; episode-scoped
  queries/indexes ignore feed rows; the CHECK rejects rows with both/neither id.
- **Parity:** queued path and inline path discover the same episodes for a fixture
  feed (shared `_refresh_single_podcast`).
- **Due-query:** the scheduler tick enqueues only feeds with `next_refresh_at <=
  now`, skips `is_complete` feeds and feeds with a non-terminal `REFRESH_FEED`
  task, and the query is a single indexed range scan.
- **Adaptive cadence:** a refresh that finds episodes shortens `refresh_interval`;
  a 304 lengthens it toward the cap; both clamp to `[min, max]`; a fetch error
  backs off **without** certifying cache headers (FM-2).
