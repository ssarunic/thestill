# Refresh Performance — Profiling and Parallelization

**Status**: 🚧 Active development
**Created**: 2026-04-17
**Updated**: 2026-04-17 (phases 0 and 1.1–1.5 shipped; phase 2.1 shipped; scheduler / adaptive cadence / reliability layer still open)
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

1. **Eliminate the double RSS fetch.** ✅ Shipped. `RSSMediaSource.fetch_and_parse`
   fetches + parses once; `extract_metadata` and `fetch_episodes` accept
   pre-parsed input. `PodcastFeedManager._refresh_single_podcast` calls
   `fetch_and_parse` once and threads the result through both extractors.
2. **`requests.Session` with `HTTPAdapter(pool_maxsize=N)`.** ✅ Shipped.
   `RSSMediaSource` owns a shared session with keep-alive + connection pool.
3. **Tighter timeouts + retry.** ✅ Shipped. `DEFAULT_TIMEOUT = (5, 15)` and
   a `urllib3.Retry` policy (2 retries on 500/502/503/504 with backoff) on
   the session.
4. **`ThreadPoolExecutor(max_workers=N)` over podcasts.** ✅ Shipped.
   `REFRESH_MAX_WORKERS` (default `1` = historical serial behavior) and
   `REFRESH_MAX_PER_HOST` (default `2`) config knobs. Per-host
   `threading.Semaphore` guards the HTTP fetch block to prevent hammering
   shared hosts. SQLite already runs in WAL mode, so concurrent writes
   serialize safely.
5. **Conditional GET.** ✅ Shipped (PR 3). `etag` / `last_modified` columns
   on `podcasts` via idempotent migration. `RSSMediaSource.fetch_rss_content`
   echoes them as `If-None-Match` / `If-Modified-Since`; on 304, the phase
   event is relabelled to `conditional_get_hit`, no body is read, and
   `feed_manager._refresh_single_podcast` returns immediately without
   parsing or metadata extraction. The batch summary carries a
   `conditional_get_hits` counter. `requests.Session` already advertises
   `Accept-Encoding: gzip, deflate` by default — no extra work needed to
   get compressed transfers on the remaining 200 responses.

### Phase 2 — Structural

1. **Batch DB writes.** ✅ Shipped (PR 3). `SqlitePodcastRepository.save_refresh_batch`
   does one transaction per refresh: `executemany` UPDATE for changed
   podcasts + `INSERT OR IGNORE` for new episodes. `_refresh_single_podcast`
   no longer touches the DB — it mutates in-memory state and the caller
   flushes at the end. `get_podcasts_for_refresh` replaces the `get_all()`
   N+1 with two queries: one for podcasts (no episode hydration), one for
   every `(podcast_id, external_id)` pair used as the dedup source.
2. **In-process scheduler (`APScheduler`).** Before reaching for Redis, a
   single-process scheduler with per-feed `next_refresh_at` is enough for
   one user and a few hundred feeds. Scheduler wakes every minute, picks
   ready feeds, enqueues them into the same thread pool.
3. **Adaptive cadence per feed.** Track observed publish interval; poll
   daily feeds every 15–30 min, weekly every 6 h, dormant ones once a day.
   Makes refresh cost scale with publish velocity, not feed count.
4. **Stagger starts.** Hash podcast ID into the refresh window so feeds
   don't all fire at `:00`. Protects hosts and egress.

### Phase 3 — Scaling infra (defer until needed)

1. **Async with `httpx.AsyncClient`.** Cleaner than threads past a few
    hundred feeds; per-host connection limits and timeouts are easier to
    express. Requires `feedparser`/repository calls to run via
    `asyncio.to_thread`.
2. **Redis + RQ (or Arq).** Per-podcast jobs on a shared queue, N worker
    processes. Isolation, horizontal scale. The operational cost of Redis
    is real for a single-user tool; earn it with data before adopting.
3. **WebSub / PubSubHubbub detection.** Subscribe to push where feeds
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
3. **PR 3 — Conditional GET + DB batching.** ✅ Shipped. Schema migration
   for `etag`/`last_modified`; persistence moved to one end-of-run
   transaction; `get_all()` N+1 replaced by `get_podcasts_for_refresh`.
   See Outcome section below for measured results.
4. **PR 4+ — Scheduler + adaptive cadence.** Only after PR 1–3 numbers
   justify the added complexity. (PR 1–3 numbers are in; PR 4 is still
   optional — the steady-state refresh is now fast enough that automating
   it earns its complexity only when multi-user / many-feed hosting lands.)

## Design decisions

- **Thread pool scope (RSS vs YouTube).** Single shared pool with a
  semaphore per source type, not separate pools. `yt-dlp` is slow and
  spawns subprocesses, so one blocked worker can stall several RSS
  fetches behind it, but managing two pool lifecycles is over-engineered
  for the current scale. Cap concurrent `yt-dlp` calls at 2 while leaving
  the overall pool at its configured size. Re-evaluate if phase-0 data
  shows YouTube dominating wall time.
- **Default pool size.** PR 2 ships with the pool size config flag
  defaulting to `1` (current behavior, opt-in). Once phase-0 data is in,
  bump the default to `8` with a per-host cap of `2`. The per-host cap
  matters more than the total — Megaphone, Libsyn, and Transistor each
  host dozens of feeds, and hammering one origin with 8 concurrent
  requests is both rude and sometimes rate-limited. Both values stay as
  config knobs.
- **`task_manager` locking granularity.** Keep the current global 409
  behavior for now. Per-podcast locking only becomes necessary when the
  scheduler (phase 2, item 7) lands, because that is the first point
  where a scheduled run and a user-triggered run can legitimately
  coexist for different podcasts. Land per-podcast locking in the same
  PR as the scheduler, informed by the scheduler's actual needs.
- **Conditional-GET state scope (multi-user).** Per-podcast, shared
  across users. The `ETag` / `Last-Modified` describe the origin feed
  state and have no per-user dimension. Consistent with spec #13's
  "process once, deliver to many" model: if a shared refresh was fresh
  enough for user A five minutes ago, it's fresh enough for user B
  following the same podcast. No forced refresh on new follow.

## Open questions

None remaining at spec time. New questions may surface during phase-0
data review or PR implementation; track them inline with the relevant PR.

## Related specs

- [07-multi-user-web-app.md](07-multi-user-web-app.md) — scaling driver
- [10-queue-viewer.md](10-queue-viewer.md) — existing task queue surface
- [11-task-queue-monitor.md](11-task-queue-monitor.md) — may host scheduler
  state surface
- [13-multi-user-shared-podcasts.md](13-multi-user-shared-podcasts.md) —
  already established that podcast refresh is a shared, once-per-podcast
  operation

## Outcome (PR 1–3, as of 2026-04-17)

Measured against the author's real 33-feed subscription list. Same machine,
same network, same time-of-day; `max_workers=1` on all runs (serial).

### Phase 0 — baseline (pre-work)

- Batch wall time: **44,920 ms**
- `http_fetch` sum: **33,890 ms** (75% of wall time), one event per podcast
- `parse` sum: **10,230 ms** (23% of wall time)
- `persist` events: 0 (no new episodes in this run)
- Dominant outlier: `the-twenty-minute-vc-20vc…` at **30,300 ms** for an
  11.9 MB / 1444-entry body — a single feed responsible for 67% of the
  entire refresh wall time.
- Distribution was sharply bimodal: 32 feeds finished in <500 ms each;
  one feed burned 30 seconds.

Takeaway at phase 0: literal p50 `http_fetch` was only 37 ms. The
pathology was one outlier, not a general network slowness. Conditional
GET was the obvious lever — on a repeat refresh with zero new episodes,
every byte of that 11.9 MB body was wasted.

### Phase 1 + 3 — shipped

- Batch wall time: **2,030 ms** (≈**22× faster** than baseline)
- `conditional_get_hit` events: **31 / 33** (94% hit rate)
- `http_fetch` events: **2** — only the feeds whose origins emit neither
  `ETag` nor `Last-Modified` (currently just one Cloudflare Workers
  origin and one transient miss that resolved on next refresh).
- `parse` events: **2** (matches non-cacheable feeds; the 31 304-hits
  skip parse entirely).
- `persist_batch`: **1** event at 7.56 ms for the whole refresh.
- 20vc outlier: **894 ms** on a 304 response (down from 30,300 ms on a
  200). The server still needs to validate the conditional headers, but
  the 11.9 MB body is no longer transferred and the 2 s parse is skipped.
- Errors: 0.

### What the remaining 2 "misses" tell us

- **`dwarkesh-podcast`** is served from `apple.dwarkesh-podcast.workers.dev`
  (a Cloudflare Workers origin). The origin emits neither `ETag` nor
  `Last-Modified`, so conditional GET has nothing to echo. Every
  refresh costs a full ~130 ms gzipped fetch + parse of a 494 kB body.
  Not worth engineering a workaround.
- **`bg2pod-with-brad-gerstner-and-bill-gurley`** *does* emit a weak
  ETag; observed misses are transient and resolve on the next refresh.
  Nothing to fix in code.

### What was NOT worth doing (investigated, rejected)

- **Body-hash fallback** ("DIY ETag when server doesn't send one").
  Only helps the single Dwarkesh case and saves the parse step only
  (~30 ms on 494 kB). Complexity not justified for a single feed.
- **Enabling gzip explicitly.** `requests.Session` already advertises
  `Accept-Encoding: gzip, deflate` by default and all observed origins
  honor it. No-op.
- **RFC 5005 feed paging / archiving.** Zero of the 33 tracked feeds
  implement it. It is a real standard but effectively dead in practice
  for podcast RSS. A client-side implementation would exercise on nothing.
- **PodcastIndex API.** Would replace direct RSS polling with a
  third-party aggregator; trades source-of-truth + zero external
  dependencies for an API key and rate limits. Wrong fit for a
  self-hosted tool that already has the feeds it cares about.
- **Bumping `REFRESH_MAX_WORKERS` default from 1 → 8.** Phase-0 data
  showed a single outlier dominating wall time; parallelising 8 ways
  would have dropped batch to ~30 s (capped by the outlier). Conditional
  GET solved the outlier directly, so the parallelism bump is now worth
  much less and the default stays at 1. The knob remains for users
  with hundreds of feeds.

### Files touched by PR 3

- Model: [thestill/models/podcast.py](../thestill/models/podcast.py) —
  added `etag`, `last_modified` fields.
- Schema: [thestill/repositories/sqlite_podcast_repository.py](../thestill/repositories/sqlite_podcast_repository.py)
  — idempotent migration, new `save_refresh_batch`, new
  `get_podcasts_for_refresh`, SELECT lists extended.
- Abstract repo: [thestill/repositories/podcast_repository.py](../thestill/repositories/podcast_repository.py)
  — two new abstract methods.
- HTTP: [thestill/core/media_source.py](../thestill/core/media_source.py)
  — `FetchRSSResult` / `FetchAndParseResult` NamedTuples, conditional
  headers, 304 short-circuit, `known_external_ids` dedup parameter on
  `fetch_episodes`.
- Refresh loop: [thestill/core/feed_manager.py](../thestill/core/feed_manager.py)
  — 4-tuple (now 5-tuple) return threading `source` through, batch
  accumulator + one-transaction flush, no per-podcast DB writes.
- Timing: [thestill/utils/timing.py](../thestill/utils/timing.py) —
  allow callers to override `phase` via `ctx` so `conditional_get_hit`
  can emit from the same context manager.
- Tests: [tests/unit/models/test_media_source.py](../tests/unit/models/test_media_source.py)
  and [tests/unit/services/test_feed_manager.py](../tests/unit/services/test_feed_manager.py)
  — updated mocks for the new return shapes.

### Open after PR 3 (kept in this spec, not scoped as a follow-up yet)

- Phase 2.2 — in-process scheduler
- Phase 2.3 — adaptive cadence per feed
- Phase 2.4 — stagger starts
- Phase 3 — async / Redis / WebSub (deferred)
- Phase 4 — reliability layer required before automating refresh

The practical recommendation is to hold these until multi-user hosting
(spec #07) materialises, since the single-user refresh is now cheap
enough that the added complexity would not earn its keep.
