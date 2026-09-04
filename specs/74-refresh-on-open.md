# Refresh on Open — Demand-Driven Feed Discovery

**Status**: 🚧 Implemented on `feat/74-refresh-on-open` (2026-09-04)
**Created**: 2026-09-04
**Updated**: 2026-09-04
**Priority**: High (Top-chart podcasts have shown no episodes newer than ~2026-07-24 in production)

> **Related:** [#63 universal-follower-gate](63-universal-follower-gate.md) (the gate that froze unfollowed feeds; this spec keeps its invariant and moves its enforcement point), [#48 refresh-feed-stage](48-refresh-feed-stage.md) (the `REFRESH_FEED` task, per-feed coalescing guard and AIMD bookkeeping this spec reuses), [#60 refresh-network-failure-classification](60-refresh-network-failure-classification.md) (quarantine reasons an open must never re-probe), [#57 eea-top-podcast-regions](57-eea-top-podcast-regions.md) (the Top Podcasts page whose lazy-import resolve endpoint now enqueues its first discovery through this service), [#68 live-episode-reader-refresh](68-live-episode-reader-refresh.md) (level-gated polling pattern the detail page now follows)

## Executive summary

Spec #63 made the refresh scheduler skip every podcast nobody follows. That
is correct for the paid pipeline and wrong for browsing: a Top-chart show
opened from the chart is lazily imported, refreshed once, and then never
polled again, so its episode list froze on the day the gate shipped
(2026-07-23) or on the day it was first opened. Production Top podcasts
showed nothing newer than late July.

This spec adds the one demand signal the gate cannot see — **a reader
opening the podcast** — and turns it into at most one `REFRESH_FEED` task.
Three coordinated changes:

1. **Refresh on open.** `GET /api/podcasts/{slug}` enqueues a `REFRESH_FEED`
   for that feed unless a guard says otherwise, and reports
   `refresh_pending` so the detail page polls until the refresh lands.
2. **First discovery is the same task.** The lazy-import resolve endpoint
   no longer spawns a daemon thread; it calls the same service. Initial
   discovery is therefore a durable queue row: observable
   (`refresh_pending`), retried by the worker, and re-triggerable by a
   later open if it failed. `last_processed` (an episode-date watermark)
   is never used as a lifecycle lock.
3. **Follower gate moves to the enqueue point.** The shared
   `enqueue_discovered_episodes` helper (both queue managers) now returns 0
   for a feed with no followers. Discovery still runs; transcription does
   not. "Processed = followed" (spec #63) holds everywhere, including the
   resolve endpoint's first discovery and CLI `--podcast-id` refreshes.

Deliberately **not** in scope: scheduled polling of unfollowed feeds. We
do not care about feeds nobody opens; the scheduler predicate is unchanged.

## 1. Behaviour

| Surface | Before | After |
|---|---|---|
| Open an imported, unfollowed podcast | Episode list frozen at last refresh | One `REFRESH_FEED` enqueued (priority 10); new episodes appear within seconds; nothing is transcribed |
| Open a followed podcast | Scheduler cadence only (hourly → daily AIMD) | Same, plus an on-open refresh when the last attempt is older than `REFRESH_MIN_INTERVAL_SECONDS`; new episodes are processed as before |
| Reload while the refresh runs | n/a | Coalesced by the queue's per-feed uniqueness guard; still reported `refresh_pending` |
| Open within the minimum interval | n/a | Throttled — no task; `refresh_pending=false` |
| Lazy import (resolve) of an unimported chart entry | Fire-and-forget daemon thread; detail page stopped polling once spec #69 removed the app-wide 5 s refetch, so episodes stayed hidden until reload; a failed first discovery left `last_processed` NULL forever | `REFRESH_FEED` task via the same service; `refresh_pending` on both the resolve and detail responses; the worker retries; a later open re-triggers a discovery that never completed |
| Open while the scheduler already queued the feed at priority 0 | n/a | Coalesced **and promoted** to priority 10 (queued / retry-scheduled rows only; a running task is untouched) |
| Open a feed whose last failure carried `Retry-After` | n/a | Suppressed until `refresh_retry_after_at` expires, regardless of the 15-minute floor |
| Open a quarantined feed (`refresh_disabled_reason` set) | Parked | Still parked; opens never re-probe |
| First lazy import of an unfollowed chart entry (multi-user) | Discovered the catalog **and transcribed the 2 newest episodes** | Discovers only; the 2-episode backfill now starts on the first follow (`seed_on_follow`, unchanged) |
| Follow later | Scheduler picks the feed up; the 2 newest episodes are queued | Unchanged. Episodes discovered while unfollowed and older than the 2-day orphan window stay metadata-only, by design |

## 2. Guards (`RefreshOnOpenService.maybe_trigger`)

Evaluated in order; the first match wins and is logged as the outcome.

1. `REFRESH_ON_OPEN_ENABLED=false` → `disabled`
2. Podcast row missing → `not_found`
3. `refresh_disabled_reason IS NOT NULL` → `quarantined`
4. A non-terminal `REFRESH_FEED` already exists for the feed → `coalesced` (pending); a `pending` / `retry_scheduled` row is promoted to priority 10
5. `refresh_retry_after_at > now` → `backing_off` (server-directed cooldown from the last 429/5xx outranks the floor)
6. `now − last_refresh_at < REFRESH_MIN_INTERVAL_SECONDS` → `throttled`. `last_refresh_at` is written on success **and** failure by the handler, so a dead feed opened repeatedly is retried at most once per interval
7. Otherwise `add_feed_task(REFRESH_FEED, priority=10, metadata.initiated_by="open")` → `enqueued` (pending); a lost race with a concurrent open or scheduler tick → `coalesced` (+ promote)

The same method serves `POST /api/podcasts/resolve`: a brand-new row has no
bookkeeping, so it passes straight to step 7.

The task is processed by the existing `handle_refresh_feed` (spec #48):
conditional GET, per-feed persist, orphan repair, AIMD bookkeeping. Only its
enqueue step changes, via the shared helper's follower gate.

## 3. API

`GET /api/podcasts/{slug}` gains one field:

```json
{ "podcast": { "...": "...", "refresh_pending": true } }
```

`true` while any `REFRESH_FEED` for the podcast is queued or running. The
frontend's `usePodcast` polls every 5 s while it is `true` (level-gated, per
spec #68) and invalidates the episode list once it flips to `false`. The
detail header shows "Loading episodes…" while pending with no episodes yet
(the lazy-import case) and "Checking for new episodes…" otherwise.

`POST /api/podcasts/resolve` gains the same `refresh_pending` field; `is_new`
stays as an informational flag.

## 4. Configuration

| Variable | Description | Default |
|---|---|---|
| `REFRESH_ON_OPEN_ENABLED` | Enqueue a throttled `REFRESH_FEED` when a podcast detail page is opened | `true` |

The throttle reuses `REFRESH_MIN_INTERVAL_SECONDS` (default 900). Scratch
and E2E servers should set the flag to `false` alongside
`REFRESH_SCHEDULER_ENABLED=false`, or opening any podcast in the UI hits the
real feed.

## 5. Side effects considered

- **Polling load** is proportional to opens, not to the chart size, and
  bounded per feed by the minimum interval. No scheduled work is added.
- **No follow-time backlog storm.** The orphan sweep only enqueues episodes
  discovered in the last 2 days (max 25) and `seed_on_follow` only the 2
  newest, so weeks of unfollowed discovery never transcribe at once.
- **Inbox, briefings, search** are follower-driven or artifact-driven; pure
  discovery touches none of them.
- **AIMD drift for followed feeds.** An on-open refresh that finds nothing
  lengthens the scheduler interval (×1.5), the same as a scheduled miss.
- **Health counts** are unchanged; the scheduler predicate is untouched.
- **Legacy full-scan CLI stages** (`thestill download` with no filter)
  would pick up metadata-only rows of unfollowed feeds. Prod runs the queue
  worker; this only matters if someone runs those stages by hand.

## 6. Tests

- `tests/unit/services/test_refresh_on_open.py` — guard matrix against a real SQLite repository + queue (half-imported rows, Retry-After cooldown, promotion of queued vs running tasks).
- `tests/integration/test_postgres_queue_manager.py` — `promote_pending_feed_task` on Postgres.
- `tests/integration/web/test_api_resolve_podcast.py` — lazy import enqueues a durable discovery task and coalesces on re-resolve.
- `tests/unit/services/test_refresh_service_autoenqueue.py` — unfollowed feed is discover-only until followed.
- `tests/integration/test_podcast_repository_podcasts_contract.py` — `get_refresh_on_open_state` / `has_followers` on both backends.
- `tests/integration/web/test_api_podcast_refresh_on_open.py` — HTTP contract for `refresh_pending` and coalescing.
- `src/hooks/useInvalidateEpisodesWhenRefreshSettles.test.tsx` — episode list invalidates exactly once when pending settles.

## 7. Follow-ups

- If browsing analytics show many opens of never-followed feeds, add a
  slow (daily) scheduled discovery for unfollowed feeds behind a flag; the
  enqueue-point gate already makes that safe.
- `refresh_pending` could carry the outcome (`throttled` / `backing_off` vs
  `enqueued`) so the UI can say "up to date as of 5 minutes ago".
