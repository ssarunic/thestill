# Performance Medium Backlog — Deferred Findings

**Status**: 📝 Draft (deliberately deferred — no scheduling implied)
**Created**: 2026-08-06
**Updated**: 2026-08-06
**Priority**: Medium (measurable waste, not user-visible breakage; each item carries an explicit trigger for when it becomes worth doing)

## Overview

The 2026-08-06 full-stack performance review (see
[69-performance-hardening.md](69-performance-hardening.md) for method,
scale assumptions, and the Critical/High execution plan) also produced a
Medium tier: findings that are real, evidenced waste but not wrong at current
scale. This spec records them with evidence and remedies **so they are not
re-discovered from scratch**, and attaches a trigger to each — the observable
condition under which it should be promoted into scheduled work.

Nothing in this spec needs to be addressed immediately. When a trigger fires,
lift that item into its own plan (or append a phase to #69 if it is still
open).

Findings marked *(EXPLAIN-verified)* were confirmed on a seeded Postgres 16
instance (300 podcasts / 30k episodes / 50 users / 40k inbox rows).

## Findings

### M1 — Redundant indices on hot-write tables

**Evidence**: `idx_episodes_external_id` exactly duplicates the
`UNIQUE(podcast_id, external_id)` constraint index, and
`idx_episodes_podcast_id` is a prefix of the same unique index
([postgres_schema.py:176-180](../thestill/repositories/postgres_schema.py#L176-L180));
`idx_chunks_episode` is a prefix of `UNIQUE(episode_id, segment_id,
embedding_model)` (`:447-449`) — maintained on every one of the 10k+ chunk
inserts per transcript; `idx_users_email` duplicates the `email UNIQUE`
constraint (`:53`, `:63`); `idx_followers_user` is a prefix of
`UNIQUE(user_id, podcast_id)` (`:229-231`).

**Remedy**: drop all five in one migration (also from `postgres_schema.py` so
fresh installs converge). Pure win, zero query-plan risk — every query served
by a dropped index is served by the surviving unique index.

**Trigger**: fold into whichever migration touches these tables next
(cheapest possible ride-along), or immediately if transcript-ingest write
throughput ever becomes a measured bottleneck.

### M2 — Per-request auth query, uncached; double auth on top-podcasts

**Evidence**: in multi-user mode `require_auth` runs
`user_repository.get_by_id(payload.sub)` on a fresh connection for **every**
authenticated request
([dependencies.py:236](../thestill/web/dependencies.py#L236),
[auth_service.py:345](../thestill/services/auth_service.py#L345));
single-user mode memoizes (`auth_service.py:117-139`) but multi-user does not.
Separately, [api_top_podcasts.py:105](../thestill/web/routes/api_top_podcasts.py#L105)
calls `get_current_user` manually although the router already runs
`require_auth`, bypassing FastAPI's per-request dependency cache — so JWT
decode + user fetch happen twice per request.

**Remedy**: short-TTL (30–60s) in-process user cache keyed by user id,
invalidated on user mutation; delete the manual `get_current_user` call and
take the user from the dependency.

**Trigger**: multi-user hosted deployment with >10 active users, or when
the #44 pool lands (the fix is trivial to ride along); the double-auth
deletion is a one-liner worth taking opportunistically.

### M3 — Query-cache hygiene (frontend)

**Evidence**: mutations invalidate the `['episodes']` prefix, which also
matches every cached transcript, summary, word-timestamp sidecar, and
entity query — `useBulkProcess`
([useApi.ts:668](../thestill/web/frontend/src/hooks/useApi.ts#L668)),
`useRetryDLQTask` (`:695`), `useRetryAllDLQTasks` (`:721`),
`useRetryFailedEpisode` (`:815`), `useCancelPipeline` (`:852`);
`useMarkInboxRead` (`:983`) invalidates all `['inbox']` variants, refetching
the whole inbox every time a user opens an episode. `useQueueTasks`
(`:731-744`) backs off 5s→15s but never stops, and is mounted during ordinary
browsing via `useProcessingStageByEpisodeId`
([Episodes.tsx:83](../thestill/web/frontend/src/pages/Episodes.tsx#L83),
[PodcastDetail.tsx:64](../thestill/web/frontend/src/pages/PodcastDetail.tsx#L64));
that hook also builds a new `Map` on every render with no `useMemo`
(`useApi.ts:751-762`).

**Remedy**: precise query keys (invalidate `['episodes','list']` vs
`['episodes', id, 'transcript']` families); targeted `setQueryData` for the
read-marking case; stop the queue poll when no episode on screen is
mid-pipeline; memoise the Map.

**Trigger**: after #69 Phase 3 lands (removing the global poll changes the
refetch economics — re-measure first); or sooner if users report list flicker
/ redundant refetch storms after mutations.

### M4 — Inbox rows carry show-notes HTML the list never renders

**Evidence**: `_EPISODE_COLUMNS` includes `description` and
`description_html` in the inbox list JOIN
([postgres_inbox_repository.py:47-80](../thestill/repositories/postgres_inbox_repository.py#L47-L80),
used at `:300-322`) — 50 rows × multi-KB TOASTed HTML per inbox open. The
same over-width exists via `e.*` in `_PODCAST_TUPLE_COLS`
([postgres_podcast_repository_episodes.py:145-152](../thestill/repositories/postgres_podcast_repository_episodes.py#L145-L152))
feeding every episode list query.

**Remedy**: split column sets: list queries project metadata only; detail
queries fetch the full row. Requires a light "episode list item" model (or
`exclude`d fields) so Pydantic hydration doesn't force the wide shape.

**Trigger**: when #69 Phase 4/6 touches these queries anyway, or when inbox
p95 latency is measured >100ms server-side.

### M5 — Publish fan-out: 4 connections, non-atomic follower read → insert

**Evidence**: on summarize-completion the fan-out spans four separate
connections/transactions: `mark_episode_published`
([task_handlers.py:751](../thestill/core/task_handlers.py#L751)),
`get_follower_user_ids`, `insert_many`, `_ensure_pipeline`
([inbox_service.py:130-160](../thestill/services/inbox_service.py#L130-L160)).
The follower read and the inbox insert are not atomic: a follow/unfollow
landing between them delivers or drops inconsistently. (Connection-per-op
itself is spec #44's documented trade-off; the atomicity split is the finding.)

**Remedy**: one transaction covering follower read + inbox insert (a single
`INSERT … SELECT` from `podcast_followers` does both and removes the Python
round-trip entirely).

**Trigger**: first observed inconsistent delivery, or when #44's pool work
touches this code path. Note the `INSERT … SELECT` form is also the cheapest
fix for the connection count, so it may be worth taking with #69 Phase 8 if
that PR is nearby.

### M6 — Inbox backfill window-function sorts the whole episodes table *(EXPLAIN-verified: 36ms full sort at 30k rows, linear growth)*

**Evidence**:
[postgres_inbox_repository.py:412-428](../thestill/repositories/postgres_inbox_repository.py#L412-L428)
runs `ROW_NUMBER() OVER (PARTITION BY podcast_id ORDER BY COALESCE(pub_date,
published_at) DESC)` over **all** episodes with `published_at IS NOT NULL`,
regardless of which podcasts the backfill targets.

**Remedy**: scope the CTE to the followed podcast id(s) being backfilled —
the per-podcast variant of the same query *(EXPLAIN-verified)* runs off
`idx_episodes_podcast_id` with a 100-row sort.

**Trigger**: episodes table >100k rows, or backfill latency visible in the
follow/seed flow (it runs on-follow, so it is user-facing).

### M7 — Briefing scheduler runs LLM narrations serially in one tick

**Evidence**: per due user the tick thread runs generation then
`_chain_narration` — a synchronous LLM call — back-to-back
([briefing_scheduler.py:145-223](../thestill/core/briefing_scheduler.py#L145-L223),
`:342-374`), with `max_per_tick=50`. User 50's "ready by morning" briefing
trails by 50 × LLM latency; one slow narration delays everyone behind it.

**Remedy**: decouple narration from the claim loop — enqueue narration as
tasks on the existing queue (spec #20's per-stage pools fit), or a small
worker pool inside the scheduler. Keep the advance-before-generate claim
semantics from #48/#50 untouched.

**Trigger**: >10 users sharing the same briefing slot hour, or a measured
tick duration exceeding the tick interval.

### M8 — No caching of derived data

**Evidence**: recomputed from source on every request: dashboard stats
(with the per-episode `stat()` storm —
[stats_service.py:124](../thestill/services/stats_service.py#L124); the storm
itself is fixed by #69 Phase 4.1, the *caching* is this item), recent
activity, the narration roll-up that globs and reads every narration JSON
ever generated
([api_dashboard.py:157-170](../thestill/web/routes/api_dashboard.py#L157-L170)),
top-podcasts regions/categories (static seeded data,
[api_top_podcasts.py:120](../thestill/web/routes/api_top_podcasts.py#L120)),
and the polled unread badge ([api_inbox.py:78](../thestill/web/routes/api_inbox.py#L78)).

**Remedy**: small in-process TTL cache (30–60s) for stats/activity/top-podcast
lookups; incremental narration roll-up (persist the aggregate, update on
narration completion) instead of the glob-and-read.

**Trigger**: after #69 Phases 3–4 land, re-measure — the polling removal and
SQL aggregates may make caching unnecessary. Promote only if these endpoints
still register in request-latency logs. The narration glob is the exception:
its cost grows linearly forever with history, so promote it once narration
count >500.

### M9 — Same-request double fetches

**Evidence**: the podcast page fetches the podcast by slug, then rescans the
whole corpus to find the same podcast again by rss_url
([api_podcasts.py:209-216](../thestill/web/routes/api_podcasts.py#L209-L216)
— the corpus half is fixed by #69 Phase 4.4; the double-fetch structure is
this item); `mark_briefing_listened` reads the briefing row twice
([api_briefings.py:394-410](../thestill/web/routes/api_briefings.py#L394-L410));
the summary endpoint's helper chain re-derives paths and re-touches the same
artifacts several times per request
([api_podcasts.py:427-495](../thestill/web/routes/api_podcasts.py#L427-L495)).

**Remedy**: fetch once, pass the object down; for mark-listened a single
conditional UPDATE returning the row.

**Trigger**: opportunistic — take each one when its file is next edited.

### M10 — Narration still occupies a request thread

**Evidence**: `POST /api/briefings/{id}/narrate` runs a minutes-long
synchronous LLM narration inline and returns `201`
([api_briefings.py:306-323](../thestill/web/routes/api_briefings.py#L306-L323)).
Spec #69 Phase 2.2 called for `202` + task id; the route was converted to
sync `def` (so it is off the event loop) but the contract change did not
ship, so each concurrent narration still holds a FastAPI worker slot for its
whole duration and can exhaust the pool.

**Blocker**: the task manager is singleton-per-`TaskType` and cannot key
per-briefing tasks — that keying is the actual prerequisite, which is
why #69 deferred rather than skipped this.

**Remedy**: add per-entity task keying to the task manager, then move the
narration behind it and return `202` + task id, mirroring
[api_commands.py:307](../thestill/web/routes/api_commands.py#L307). The CLI
`thestill narrate` path is unaffected.

**Trigger**: before briefing narration is exposed to concurrent users, or
the first time the worker pool is observed saturating.

### M11 — `summary_preview` has no offline backfill

**Evidence**: migration 0008 leaves every existing `summary_preview` NULL,
so #69 Phase 6's "episode list issues zero file reads" gate holds only for
episodes summarized after the migration. A corpus predating 0008 pays one
sequential FileStorage/S3 read per summarized episode, spread across first
renders ([api_episodes.py:138-148](../thestill/web/routes/api_episodes.py#L138-L148)).
Worse for missing files: the `FileNotFoundError` branch does not persist the
empty sentinel that the found-but-empty branch does, so those rows re-read on
every list request indefinitely.

**Remedy**: a backfill (management command or one-shot task) that populates
the column for pre-0008 rows, plus persisting the sentinel on
`FileNotFoundError` so a missing file is recorded once rather than retried
forever. With both, the Phase 6 gate becomes unconditional and the lazy path
can be deleted.

**Trigger**: take the sentinel fix opportunistically (it is a two-line
change); the backfill before the next large import, or if list-page latency
on the pre-0008 corpus is ever measured as a problem.

### M12 — Transcript rows are contained, not virtualized

**Evidence**: #69 Phase 7 shipped `content-visibility: auto` +
`contain-intrinsic-size` ([index.css:98-112](../thestill/web/frontend/src/index.css#L98-L112))
instead of JS windowing. That bounds layout and paint to the viewport, but
React still constructs every row and all rows stay mounted: node count,
initial reconciliation, and memory remain O(n), and filter/search re-renders
remain O(n). The spec's original "<2k DOM nodes on a 10k-segment transcript"
gate is therefore withdrawn rather than met.

**Remedy**: real row windowing for both `SegmentedTranscriptViewer` and the
fallback `TranscriptViewer`. The reason this was deferred is genuine and
still applies: five scroll features (follow-playback, citation deep links,
in-transcript search, `[`/`]` mention jumps, resume) share one element-based
`scrollIntoView` mechanism across two scroll parents, and windowing unmounts
the elements it depends on. Any attempt needs index-based `scrollToIndex`
equivalents for all five plus visual QA.

**Trigger**: only if node *memory* (not render cost) is the measured
bottleneck, or a transcript materially larger than 10k segments appears.

## Appendix — Low tier (revisit opportunistically, no triggers)

- `EpisodeCard` lacks `React.memo` and `allEpisodes` is re-flattened without
  `useMemo`, so every poll tick / selection toggle re-renders the whole loaded
  list ([Episodes.tsx:80](../thestill/web/frontend/src/pages/Episodes.tsx#L80),
  [EpisodeCard.tsx:65](../thestill/web/frontend/src/components/EpisodeCard.tsx#L65)).
- `PlayerScopedTimeline` subscribes to `usePlayerTime()` without using the
  value, re-rendering the un-memoised `MentionDensityTimeline` 4×/s
  ([EpisodeReader.tsx:807-818](../thestill/web/frontend/src/pages/EpisodeReader.tsx#L807-L818)).
- Unconditional 60fps `requestAnimationFrame` + `getBoundingClientRect` loop
  whenever a video surface is visible
  ([PlayerContext.tsx:777-804](../thestill/web/frontend/src/contexts/PlayerContext.tsx#L777-L804)).
- Small-table FK gaps: `podcasts.primary/secondary_category_id`,
  `top_podcasts.category_id`, and `top_podcast_rankings.top_podcast_id` not
  leading any index ([postgres_schema.py:104](../thestill/repositories/postgres_schema.py#L104),
  `:488`, `:499`).
- `list_pending_mentions` sorts by `id` outside its partial index
  ([postgres_entity_repository.py:282](../thestill/repositories/postgres_entity_repository.py#L282)).

## Explicitly not findings (documented trade-offs, recorded to prevent re-flagging)

- **Connection-per-operation** — spec #44's documented interim design
  ([postgres_ext.py:28](../thestill/utils/postgres_ext.py#L28)). Measured
  cost at review time: ~2 connections per inbox open, ~5 per search
  keystroke, ~34 per entity page (reduced by #69 Phase 5), O(tasks) per queue
  page (reduced by #69 Phase 8).
- **Refresh dedup full pair fetch** — spec #19 design; *(EXPLAIN-verified)*
  index-only scan; cost is the 30k-entry Python map per cycle.
- **`useEpisode` background polling** (`refetchIntervalInBackground`) — spec
  #68's deliberate live-reader behavior; bounded by episode settling.
- **Two-phase `rebuild_cooccurrences`**
  ([postgres_entity_repository.py:963](../thestill/repositories/postgres_entity_repository.py#L963))
  — deliberate and documented; its torn-read window is mitigated by #69
  Phase 5.1 collapsing `get_entity_summary` into fewer transactions.

## Related specs

- [69-performance-hardening.md](69-performance-hardening.md) — Critical/High
  execution plan from the same review; several items above are partially
  subsumed by its phases (noted inline).
- [44-postgres-migration.md](44-postgres-migration.md) — connection pooling.
- [19-refresh-performance.md](19-refresh-performance.md),
  [68-live-episode-reader-refresh.md](68-live-episode-reader-refresh.md) —
  owners of the documented trade-offs above.
