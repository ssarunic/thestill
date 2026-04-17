# Refresh Performance — Profiling and Parallelization

**Status**: 💡 Proposal
**Created**: 2026-04-17
**Updated**: 2026-04-17
**Priority**: Medium (scales with feed count; not urgent at single-user scale today)

## Overview

`thestill refresh` currently processes podcast feeds serially, with no HTTP
caching and a per-feed double network fetch. As the subscriber count grows (and
especially if multi-user hosting materializes via spec #07), the refresh step
becomes a linear-in-N wall-clock cost that blocks the rest of the pipeline. This
spec plans the work to (a) get hard profiling data, (b) land the obvious wins,
and (c) design an automated-refresh path that scales.

## Goals

1. Produce per-feed timing data (network vs parse vs persist) that survives as
   permanent observability, not one-off scripts.
2. Eliminate known inefficiencies that are visible in the code today without
   waiting on profiling data.
3. Parallelize feed refresh in-process, bounded per-host, with a config knob.
4. Add conditional-GET support so unchanged feeds cost near-zero.
5. Design (but not necessarily ship) an automated refresh scheduler with
   adaptive per-feed cadence.
6. Keep the single-user default behavior unchanged until each phase opts in.

## Non-goals

- Replacing SQLite with Postgres. Covered separately if needed.
- Shipping Redis/RQ for this workload today. Deferred until Tier 1+2 proves
  insufficient.
- Changing the download / downsample / transcribe / clean / summarize steps.
  This spec is strictly about `thestill refresh`.
- WebSub push subscription infrastructure (noted as future work).

## Background findings

Traced the refresh flow:
[cli.py:231](../thestill/cli.py#L231) →
[RefreshService.refresh](../thestill/services/refresh_service.py#L65) →
[PodcastFeedManager.get_new_episodes](../thestill/core/feed_manager.py#L186).

### Current behavior

- **Serial loop.** `for idx, podcast in enumerate(podcasts):` at
  [feed_manager.py:222](../thestill/core/feed_manager.py#L222). No threading,
  no async. The web layer runs refresh on a background thread
  ([api_commands.py](../thestill/web/routes/api_commands.py)), but the work
  inside that thread is still single-file.
- **Double HTTP fetch per RSS feed.** For every podcast on every refresh,
  `extract_metadata()` is called at
  [feed_manager.py:244](../thestill/core/feed_manager.py#L244), then
  `fetch_episodes()` at
  [feed_manager.py:283](../thestill/core/feed_manager.py#L283). Both go through
  `_fetch_rss_content` at
  [media_source.py:416](../thestill/core/media_source.py#L416), which does a
  fresh `requests.get(url, timeout=30)`. Same body, fetched and parsed twice.
- **No HTTP caching.** No `ETag`, no `If-Modified-Since`, no conditional GET.
  Feeds with zero new episodes still pay a full round trip + body download +
  `feedparser.parse`.
- **No connection pooling.** Plain `requests.get`, no `Session`; TLS handshake
  repeats for every request.
- **Fixed 30 s timeout.** One hung host blocks the whole pipeline for 30 s.
- **DB N+1 on load.** `repository.get_all()` hydrates episodes with one query
  per podcast via `_row_to_podcast` in
  [sqlite_podcast_repository.py](../thestill/repositories/sqlite_podcast_repository.py).
  Then `save_episodes` + `save_podcast` commit per podcast inside the loop.
- **No scheduler / queue.** Refresh is manual-only via CLI or web button.
  `task_manager` guards against concurrent web-triggered runs, nothing else.

### Unified feed pipeline

RSS, Apple Podcasts, and YouTube all converge through
`MediaSourceFactory.detect_source()` →  `source.fetch_episodes()`. Apple URLs
resolve to RSS first; YouTube goes through `yt-dlp`. The optimizations in
this spec target the RSS path first because that is where the known
inefficiencies live and where most feeds sit; YouTube refresh timing should
be measured separately and may warrant its own follow-up.

## Profiling strategy

### Phase 0 — Measurement (land first, always)

**Permanent structured timing.** Instrument `_fetch_rss_content` and
`get_new_episodes` with `time.perf_counter()` around each phase and emit
`structlog` events:

```python
logger.info("feed_phase_timing",
    podcast_slug=podcast.slug,
    phase="http_fetch",   # or "parse", "dedup", "persist"
    duration_ms=ms,
    bytes=len(rss_content),
    status_code=response.status_code,
    episodes_new=len(episodes))
```

Aggregate with `LOG_FORMAT=json thestill refresh 2>&1 | jq` → pandas. This
stays in the tree as observability, not just a one-shot script.

**One-shot wall-clock profile.** Use `pyinstrument` (better than `cProfile`
for I/O-heavy code, which undercounts wait time):

```bash
./venv/bin/pip install pyinstrument
./venv/bin/python -m pyinstrument -o refresh.html --renderer html \
    -m thestill refresh
```

Confirms whether time lives in `socket.recv` (→ concurrency + caching),
`feedparser.parse` (→ parse-once), or SQLite (→ batching).

**HTTP-level breakdown (optional).** Toggle `urllib3` debug logging once to
separate DNS, connect, TTFB, and download per request. Use only if phase-0
data points at something surprising.

Success criterion: a short doc or notebook that, given a refresh run, reports
p50/p95 per-phase duration across feeds, feed size distribution, and the
slowest individual feeds.

## Solution phases

Each phase is independently shippable. Phase 0 is a prerequisite for all
others. Phases 1 and 2 are independent and can land in any order.

### Phase 1 — Quick wins (no new infra)

1. **Eliminate the double RSS fetch.** Refactor so the RSS body is fetched
   once per podcast per refresh and both metadata extraction and episode
   extraction work off the same parsed object. Expected: ~40–50% wall-clock
   cut on the RSS path even before concurrency.
2. **`requests.Session` with `HTTPAdapter(pool_maxsize=N)`.** Keep-alive and
   connection reuse across feeds on the same host.
3. **Tighter timeouts + retry.** `(connect=5, read=15)` + `urllib3.Retry` with
   jitter. Avoids 30 s stalls on a single bad host.
4. **`ThreadPoolExecutor(max_workers=N)` over podcasts.** Gated behind a
   config flag (default `1` = current behavior) until we have phase-0 data.
   Per-host concurrency cap to avoid hammering shared hosts (Megaphone,
   Libsyn, Transistor host many feeds each). Feed I/O releases the GIL so
   threads are sufficient.
5. **Conditional GET.** Store `etag` and `last_modified` on the `podcasts`
   table (schema migration). Send `If-None-Match` / `If-Modified-Since`. 304
   responses are near-zero cost. Combined with phase 1, unchanged feeds
   become essentially free.

### Phase 2 — Structural

6. **Batch DB writes.** Collect results, commit once at the end of the
   refresh in a single transaction. Eliminate the `get_all()` N+1 by loading
   `(podcast_id, external_id)` pairs upfront into a set for dedup.
7. **In-process scheduler (`APScheduler`).** Before reaching for Redis, a
   single-process scheduler with per-feed `next_refresh_at` is enough for
   one user and a few hundred feeds. Scheduler wakes every minute, picks
   ready feeds, enqueues them into the same thread pool.
8. **Adaptive cadence per feed.** Track observed publish interval; poll
   daily feeds every 15–30 min, weekly every 6 h, dormant ones once a day.
   Makes refresh cost scale with publish velocity, not feed count.
9. **Stagger starts.** Hash podcast ID into the refresh window so feeds
   don't all fire at `:00`. Protects hosts and egress.

### Phase 3 — Scaling infra (defer until needed)

10. **Async with `httpx.AsyncClient`.** Cleaner than threads past a few
    hundred feeds; per-host connection limits and timeouts are easier to
    express. Requires `feedparser`/repository calls to run via
    `asyncio.to_thread`.
11. **Redis + RQ (or Arq).** Per-podcast jobs on a shared queue, N worker
    processes. Isolation, horizontal scale. The operational cost of Redis
    is real for a single-user tool; earn it with data before adopting.
12. **WebSub / PubSubHubbub detection.** Subscribe to push where feeds
    advertise a hub. Fewer feeds honor this than expected, but free when
    they do.

### Phase 4 — Reliability for automated refresh

Required before switching the scheduler on by default:

- **Per-host circuit breaker.** Exponential backoff after N consecutive
  failures; surface in UI.
- **Re-entrancy guard.** Extend `task_manager` beyond a 409 to a per-podcast
  lock so scheduler + manual runs can't double-refresh the same feed.
- **SQLite WAL mode.** Required for real write concurrency. Verify + enable.
- **Graceful shutdown.** On SIGTERM, finish in-flight feed, persist, exit.
  Don't lose partial batches.
- **Per-feed failure metrics.** `last_success_at`, `last_error`,
  consecutive-failure count. Feeds dark for 30 d auto-downgrade cadence.
- **Feed migration handling.** RSS extraction already detects
  `itunes:new-feed-url` but doesn't act on it. Auto-migrate or alert.

## Suggested PR sequence

1. **PR 1 — Profiling.** Phase 0 instrumentation + a short
   `docs/profiling-refresh.md` playbook. One refresh run produces data.
2. **PR 2 — Obvious wins.** Eliminate double-fetch, add `requests.Session`,
   tighten timeouts, introduce `ThreadPoolExecutor` behind a config flag
   defaulting to `1`. No behavior change until the flag is flipped.
3. **PR 3 — Conditional GET + DB batching.** Schema migration for
   `etag`/`last_modified`; move persistence to end-of-run.
4. **PR 4+ — Scheduler + adaptive cadence.** Only after PR 1–3 numbers
   justify the added complexity.

## Open questions

- Should YouTube refresh share the thread pool, or run in a separate pool
  (since `yt-dlp` has different characteristics and sometimes spawns
  subprocesses)?
- What is the right default thread-pool size? Phase 0 data + a per-host
  concurrency cap will answer this; best to leave as a config knob initially.
- Does the existing `task_manager` 409 behavior need to relax to per-podcast
  locking sooner rather than later, or can that wait until the scheduler
  lands?
- For multi-user (spec #07), should conditional-GET state be per-podcast
  (shared) or per-user? Per-podcast seems obviously correct; flagged for
  review against the multi-user model.

## Related specs

- [07-multi-user-web-app.md](07-multi-user-web-app.md) — scaling driver
- [10-queue-viewer.md](10-queue-viewer.md) — existing task queue surface
- [11-task-queue-monitor.md](11-task-queue-monitor.md) — may host scheduler
  state surface
- [13-multi-user-shared-podcasts.md](13-multi-user-shared-podcasts.md) —
  already established that podcast refresh is a shared, once-per-podcast
  operation
