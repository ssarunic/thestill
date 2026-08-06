# Performance Hardening — Critical and High Findings

**Status**: 🚧 Active development (Phases 1–2 shipped 2026-08-06; Phases 3–8 open)
**Created**: 2026-08-06
**Updated**: 2026-08-06 (Phase 1: migration 0007 + `SCHEMA_SQL` + SQLite parity, all five EXPLAIN gates green; Phase 2: 65-route sync sweep + webhook threadpool + AST/behavioral guard — see Outcome)
**Priority**: High (Critical tier is wrong at current scale; High tier is wrong at target scale)

## Overview

A full-stack performance review (UI → routes → services → repositories →
Postgres) audited the codebase against target scale: hundreds of podcasts,
tens of thousands of episodes, 10k+ segment transcripts, per-user inbox
fan-out. The hottest queries were **EXPLAIN ANALYZE-verified** on a Postgres 16
instance loaded with the real `postgres_schema.py` schema, seeded to 300
podcasts / 30,000 episodes / 50 users / 40,000 inbox rows. Findings marked
*(EXPLAIN-verified)* below were confirmed on that instance, not inferred.

This spec is the execution plan for the **Critical** and **High** findings.
The Medium tier is deliberately deferred to
[70-performance-medium-backlog.md](70-performance-medium-backlog.md).

The three Critical problems multiply each other: blocking routes (C1) are
polled every 5 seconds per open tab (C2), and several of the polled endpoints
hydrate the entire corpus per request (C3). Fixing any one relieves the other
two, which is why the phases below are safe to land independently.

## Goals

1. No synchronous DB / file / LLM / network work on the FastAPI event loop.
2. Every hot query served by an index whose column order matches the query's
   filter-then-sort shape; verified by `EXPLAIN` before merge.
3. No endpoint loads the full corpus to serve a bounded page.
4. Frontend polls only where live data is genuinely needed, with explicit
   per-hook intervals (preserving spec #68's live-reader contract).
5. All list endpoints capped server-side; large immutable payloads compressed
   and cacheable.
6. Transcript rendering cost bounded by viewport, not transcript length.

## Non-goals

- **Connection pooling.** `psycopg_pool.ConnectionPool` is spec #44's deferred
  scope ([postgres_ext.py:28](../thestill/utils/postgres_ext.py#L28)). This
  plan reduces *query counts*; the pool reduces *handshake cost*. Both are
  needed; only the former is in scope here.
- **Medium/Low findings** — tracked in
  [70-performance-medium-backlog.md](70-performance-medium-backlog.md).
- **Refresh dedup map redesign** — the full `(podcast_id, external_id)` fetch
  at [postgres_podcast_repository_podcasts.py:448](../thestill/repositories/postgres_podcast_repository_podcasts.py#L448)
  is spec #19's documented design and *(EXPLAIN-verified)* an index-only scan;
  revisit under #19 if the per-cycle Python map build starts showing up.
- **SQLite planner parity.** SQLite repositories receive the same index DDL
  where the syntax ports (partial and expression indexes port; `pg_trgm` and
  jsonb GIN do not); SQLite-only query-plan tuning is out of scope since
  Postgres is the production target.

## Findings recap

| ID | Sev | Finding |
|---|---|---|
| C1 | Critical | `async def` routes run sync DB/file/LLM I/O on the event loop app-wide |
| C2 | Critical | Global `refetchInterval: 5000` QueryClient default polls ~20 endpoints every 5s |
| C3 | Critical | Full-corpus `get_all()` N+1 (1+P queries, every episode hydrated) feeds hot endpoints |
| C4 | Critical | Entity page ≈34 connections/view; ⌘K typeahead aggregates the whole corpus per keystroke |
| H1 | High | Episode listing `ORDER BY pub_date DESC NULLS LAST` defeats `idx_episodes_pub_date` *(EXPLAIN-verified: 19.3ms seq scan+sort vs 0.03ms index scan at 30k rows)* |
| H2 | High | Inbox default view (`state != 'dismissed'` + sort) not served by `idx_inbox_user_state` *(EXPLAIN-verified: per-user fetch-all + top-N sort)* |
| H3 | High | Missing FK indices: `tasks.podcast_id`, `episode_related.related_episode_id`, `entity_cooccurrences.entity_b_id`, `mention_overrides.{episode_id,entity_id}` |
| H4 | High | Unindexed text lookups: title ILIKE *(EXPLAIN-verified seq scan)*, entity name/alias ILIKE, `LOWER(surface_form)` equality, jsonb `@>` without GIN |
| H5 | High | No CLEANED-stage partial index; failed-episodes view seq-scans *(EXPLAIN-verified)* |
| H6 | High | ~1 MB word-timestamp payloads; no gzip middleware; no ETag/Cache-Control; transcript double-shipped |
| H7 | High | Uncapped `?limit=`; unpaged entity-mentions endpoint; per-row summary file reads in the episode list |
| H8 | High | No transcript virtualisation (10k segments ≈ 50–100k DOM nodes); O(n) work per 4Hz playback tick |
| H9 | High | Queue/DLQ pages: one connection+query per task; briefing render N+1 per window episode |
| H10 | High | Row-at-a-time writes: image repair, feed-import save, 2-RTT episode upserts |

Adjacent functional bug noticed during the review (not perf, fix
opportunistically with Phase 3): the Inbox page never passes `before`/`limit`,
so with >50 items only the newest 50 are reachable and the
"N delivered" count is wrong
([Inbox.tsx:204](../thestill/web/frontend/src/pages/Inbox.tsx#L204) vs the
50-cap at [api_inbox.py:51](../thestill/web/routes/api_inbox.py#L51)).

## Solution phases

Phases are independently landable. Suggested order: 1 → 2 → 3 → 4 → 5, with
6–8 schedulable in any order after 2. Phase 1 first because it is the lowest
risk and other phases' acceptance criteria depend on its indices.

### Phase 1 — Index and schema migration (H1, H2, H3, H4, H5)

One alembic migration + matching `postgres_schema.py` DDL (so fresh installs
and migrated installs converge), plus SQLite parity where the syntax ports.

1.1 **Recreate `idx_episodes_pub_date` as `(pub_date DESC NULLS LAST)`**
    ([postgres_schema.py:181](../thestill/repositories/postgres_schema.py#L181)).
    Every hot listing orders `DESC NULLS LAST`
    ([postgres_podcast_repository_episodes.py:1126](../thestill/repositories/postgres_podcast_repository_episodes.py#L1126),
    `:1217`, `:1341`, `:1510`); the current index's implicit `NULLS FIRST`
    makes it unusable for them.

1.2 **Inbox indices**: add `(user_id, delivered_at DESC)` — optionally partial
    `WHERE state != 'dismissed'` — for the default view
    ([postgres_inbox_repository.py:283](../thestill/repositories/postgres_inbox_repository.py#L283)),
    and `(user_id, source, delivered_at)` for the import rate-limit check
    (`:327`). Keep `idx_inbox_user_state` for state-filtered views.

1.3 **FK indices**: `tasks(podcast_id)`,
    `episode_related(related_episode_id)`,
    `entity_cooccurrences(entity_b_id)`,
    `mention_overrides(episode_id)`, `mention_overrides(entity_id)`.
    Postgres does not auto-index FK columns; cascade deletes and the
    `OR entity_b_id = %s` query leg
    ([postgres_entity_repository.py:504](../thestill/repositories/postgres_entity_repository.py#L504))
    currently seq-scan.

1.4 **Pipeline-state partials** matching the existing four-stage pattern
    ([postgres_schema.py:184](../thestill/repositories/postgres_schema.py#L184)):
    CLEANED (`clean_transcript_path IS NOT NULL AND summary_path IS NULL`) and
    failed (`(failed_at DESC) WHERE failed_at_stage IS NOT NULL`).

1.5 **Text-search indices** (Postgres-only):
    `CREATE EXTENSION IF NOT EXISTS pg_trgm`; trgm GIN on `episodes.title` and
    `entities.canonical_name`; `LOWER(surface_form)` expression indices on
    `entity_mentions` (per-mention resolution path,
    [postgres_entity_repository.py:335](../thestill/repositories/postgres_entity_repository.py#L335)),
    `mention_overrides` (`:1206`), and `resolution_blacklist` (`:1262` —
    or change the query to match the existing unique index's case); jsonb GIN
    on `episodes.guest_entity_ids` for the `@>` containment probe (`:606`).

**Gate**: `EXPLAIN (ANALYZE, BUFFERS)` before/after on the seeded scratch DB
for: episode listing page 1, inbox default view, title search, DLQ view,
summarize-stage poll. All five must switch from seq-scan/sort to index plans.
Alembic upgrade+downgrade round-trips clean; dual-backend contract tests green.

### Phase 2 — Get sync work off the event loop (C1)

The repositories are synchronous psycopg3; FastAPI only threadpools **sync
`def`** routes, but the route layer is almost uniformly `async def`.

2.1 **Route conversion sweep.** For every route that awaits nothing: convert
    to plain `def`. For routes that must stay `async` (they await something),
    wrap sync repository/file calls in `run_in_threadpool`. Affected files
    (verified route-by-route):
    [api_inbox.py](../thestill/web/routes/api_inbox.py) (all four routes),
    [api_episodes.py](../thestill/web/routes/api_episodes.py),
    [api_dashboard.py](../thestill/web/routes/api_dashboard.py),
    [api_transcript_words.py](../thestill/web/routes/api_transcript_words.py),
    [api_podcasts.py](../thestill/web/routes/api_podcasts.py),
    [api_briefings.py](../thestill/web/routes/api_briefings.py),
    [api_top_podcasts.py](../thestill/web/routes/api_top_podcasts.py),
    [api_status.py](../thestill/web/routes/api_status.py),
    [webhooks.py](../thestill/web/routes/webhooks.py).
    `api_search.py` and `api_entities.py` are already sync `def` — they are
    the reference pattern.

2.2 **Evict long-running work from request handlers entirely** (threadpool is
    not enough — it still holds a slot for minutes):
    - `POST /api/briefings/{id}/narrate` runs a synchronous LLM narration
      inline ([api_briefings.py:342](../thestill/web/routes/api_briefings.py#L342)).
      Move to the task manager; return `202` + task id (the pattern
      [api_commands.py:307](../thestill/web/routes/api_commands.py#L307)
      already uses for refresh/add).
    - `GET /api/briefings/latest` lazy generation
      ([api_briefings.py:185](../thestill/web/routes/api_briefings.py#L185))
      → threadpool at minimum; spec #55's `202 briefing_pending` shape is the
      end state.
    - `POST /api/podcasts/resolve` 1–2s RSS fetch
      ([api_podcasts.py:86](../thestill/web/routes/api_podcasts.py#L86)) —
      the comment at `:68` already assumes threadpool execution; make it true.

2.3 Fix the stale comment at
    [api_podcasts.py:68](../thestill/web/routes/api_podcasts.py#L68) and add a
    lint guard (simple AST check in CI or a pylint plugin) rejecting
    `async def` routes that call repository methods without
    `run_in_threadpool`, so the pattern doesn't regress.

**Gate**: under a synthetic slow query (e.g. `pg_sleep` injected in a test
repo), a concurrent request to `/api/health` must still answer <100ms.
Existing route tests green.

### Phase 3 — Remove the global polling default (C2)

3.1 Delete `refetchInterval: 5000` from the QueryClient defaults
    ([main.tsx:14](../thestill/web/frontend/src/main.tsx#L14)). Raise the
    default `staleTime` (60s is reasonable for this app's mostly-static data).

3.2 Re-add **explicit** intervals only where live data is required:
    - `useEpisode` keeps its 5s live poll — **spec #68's reader reconciliation
      depends on it** ([useApi.ts:216](../thestill/web/frontend/src/hooks/useApi.ts#L216)
      and the `useEpisodeLiveRefresh` tick). This must be preserved verbatim.
    - The existing self-terminating pollers are already correct and keep their
      behavior: `useRefreshStatus` / `useAddPodcastStatus` /
      `usePipelineTaskStatus` (stop on terminal status), `useEpisodeTasks`
      (2s → 15s → 60s → stop), `useInbox`'s conditional interval.
    - Everything else (dashboard, podcasts, top-podcasts, searches, entities,
      briefings, related episodes, infinite queries) becomes fetch-on-mount +
      invalidate-on-mutation.

3.3 Verify no hook regresses to interval polling via a unit test that mounts
    the QueryClient and asserts `defaultOptions.queries.refetchInterval` is
    unset.

**Gate**: Network tab on an idle dashboard tab for 60s shows zero interval
refetches; the spec #68 live-reader Playwright/manual scenario still converges
without reload; `make test` + frontend tests green.

### Phase 4 — Retire full-corpus hydration (C3)

`get_all()` runs 1+P queries and hydrates every episode (with `description` /
`description_html`) into Pydantic models
([postgres_podcast_repository_podcasts.py:667](../thestill/repositories/postgres_podcast_repository_podcasts.py#L667),
[:1452](../thestill/repositories/postgres_podcast_repository_podcasts.py#L1452)).
Replace its hot callers with purpose-built queries; `get_all()` itself remains
for the CLI/feed-manager paths that genuinely need full hydration.

4.1 **Stats via SQL aggregates.** `stats_service.get_stats()`
    ([stats_service.py:124](../thestill/services/stats_service.py#L124))
    currently counts pipeline states in Python and calls `md_path.exists()`
    per summarized episode (tens of thousands of `stat()` syscalls per
    request). Add a repository `count_episodes_by_state()` using
    `COUNT(*) FILTER (WHERE …)` over the same predicates the Phase 1.4
    partials encode; drop the per-episode filesystem existence probes (the
    `summary_path` column is the source of truth).

4.2 **Followed-podcasts listing in SQL.**
    `GET /api/podcasts` filters/searches/paginates the corpus in Python
    ([api_podcasts.py:157](../thestill/web/routes/api_podcasts.py#L157)).
    Replace with one JOIN on `podcast_followers` + `ILIKE` + `LIMIT/OFFSET`
    and a grouped episode-count subquery.

4.3 **Dashboard activity in SQL.**
    [api_dashboard.py:116](../thestill/web/routes/api_dashboard.py#L116)
    flattens and sorts every episode for a 10-row page; the repository already
    has `get_all_episodes(sort_by=…)` — use it.

4.4 **Single-podcast lookups use indexed methods.**
    `podcast_service.get_podcast`
    ([podcast_service.py:408](../thestill/services/podcast_service.py#L408))
    loads the corpus and linear-scans for one podcast; route by identifier
    type to the existing `get_by_id` / `get_by_slug` / `get_by_url`.
    Also fix the double fetch at
    [api_podcasts.py:209](../thestill/web/routes/api_podcasts.py#L209).

4.5 **Episode-free podcast fetches.** `get_by_slug` / `get_by_id` hydrate the
    podcast's entire episode list even for slug→id resolution
    ([postgres_podcast_repository_podcasts.py:725](../thestill/repositories/postgres_podcast_repository_podcasts.py#L725));
    add a light variant (the repo already has the shape in
    `get_real_parent_podcast_for_episode`, `:1636`) and use it in
    [api_episodes.py:101](../thestill/web/routes/api_episodes.py#L101),
    [api_search.py:375](../thestill/web/routes/api_search.py#L375),
    [follower_service.py:262](../thestill/services/follower_service.py#L262)
    and friends.

4.6 **Counts are counts.** `len(get_all())` twice per add-podcast request
    ([api_commands.py:133](../thestill/web/routes/api_commands.py#L133),
    `:146`) → `SELECT COUNT(*)`.

**Gate**: request-scoped query counting (log-based) shows `/api/podcasts`,
`/api/dashboard/stats`, `/api/dashboard/activity`, `/api/status` each issue a
constant number of queries independent of corpus size. Responses byte-identical
(or field-identical) to before, verified by existing route tests plus
golden-response comparisons.

### Phase 5 — Entity pages and typeahead (C4; depends on 1.5)

5.1 **Batch the entity page.**
    `get_entity_summary` opens a connection per sub-query and per
    co-occurrence row
    ([postgres_entity_repository.py:532](../thestill/repositories/postgres_entity_repository.py#L532));
    the route adds `get_episode` per recent mention
    ([api_entities.py:475](../thestill/web/routes/api_entities.py#L475)) and
    `get_entity` per distinct entity (`:364`). Collapse to: one connection for
    the summary (co-occurring entities via JOIN, not per-row `get_entity`),
    episode slug/audio/duration projected in `_MENTION_CONTEXT_SELECT`'s
    existing JOIN
    ([postgres_entity_repository.py:57](../thestill/repositories/postgres_entity_repository.py#L57)),
    and `id = ANY(%s)` for entity backfill. Target ≤4 connections per page
    (spec #44's pool later makes even that cheap).

5.2 **Stop rebuilding the role index per keystroke.** The typeahead CTE
    lateral-expands jsonb across all episodes/podcasts and aggregates all
    resolved mentions on every ⌘K keystroke
    ([postgres_entity_repository.py:848](../thestill/repositories/postgres_entity_repository.py#L848)).
    Precompute it — either a materialized view refreshed on entity-extraction
    completion, or a maintained side table written by the extraction handler.
    The prefix match itself uses Phase 1.5's trgm index.

5.3 **Page the mentions endpoint.** `get_episode_entities` returns up to 5000
    mentions with quote excerpts in one body
    ([api_entities.py:288](../thestill/web/routes/api_entities.py#L288));
    add `limit`/`offset` with a server cap, and let the reader fetch
    incrementally.

**Gate**: typeahead p50 <50ms on the seeded scratch DB; entity page issues a
bounded query/connection count (measured via the request-scoped counter from
Phase 4's gate).

### Phase 6 — Payload and endpoint hygiene (H6, H7)

6.1 Add `GZipMiddleware` to the stack
    ([app.py:570](../thestill/web/app.py#L570), `minimum_size≈1KB`).
6.2 ETag (content-hash) + `Cache-Control` on write-once resources: transcript,
    transcript-words, summary, narration script responses. The words payload's
    own comment ([api_transcript_words.py:98](../thestill/web/routes/api_transcript_words.py#L98))
    budgets "100–150 KB gzipped" — 6.1 + 6.2 deliver exactly that budget
    without needing ranged windows yet.
6.3 Stop double-shipping the transcript: `get_episode_transcript_by_slugs`
    returns full markdown **plus** the full segmented structure
    ([api_podcasts.py:365](../thestill/web/routes/api_podcasts.py#L365));
    the reader uses the segmented form — drop the redundant representation
    behind a response-shape version check with the frontend.
6.4 Clamp `limit` on `GET /api/episodes`
    ([api_episodes.py:70](../thestill/web/routes/api_episodes.py#L70)) —
    mirror `_MAX_LIMIT` from api_inbox.
6.5 Precompute `summary_preview` at summarize time (store on the episode row)
    instead of reading each episode's full summary file per list request
    ([api_episodes.py:132](../thestill/web/routes/api_episodes.py#L132)).

**Gate**: `Content-Length` of transcript-words for a 2-hour episode ≤200KB on
the wire; episode list issues zero file reads; `limit=1000000` returns 422.

### Phase 7 — Transcript rendering bounded by viewport (H8)

7.1 Virtualise `SegmentedTranscriptViewer`'s row list
    ([SegmentedTranscriptViewer.tsx:1004](../thestill/web/frontend/src/components/SegmentedTranscriptViewer.tsx#L1004))
    with react-virtuoso (or equivalent). Constraints that shape the work:
    auto-scroll-follow (`useAutoScrollFollow`), deep-link
    `scrollToSegmentId` (spec #54), and in-transcript search jumps must keep
    working against unmounted rows — virtuoso's index-based `scrollToIndex`
    covers all three.
7.2 Confine the 4Hz `usePlayerTime()` subscription: today the whole viewer
    re-renders per tick, allocating `new Set(renderedSegments.map(...))` each
    time ([:640](../thestill/web/frontend/src/components/SegmentedTranscriptViewer.tsx#L640));
    move active-segment derivation into a leaf hook so only the outgoing and
    incoming active segments re-render (the karaoke layer already works this
    way — extend its pattern outward).
7.3 Fix the keydown listener re-registration per tick
    ([:783](../thestill/web/frontend/src/components/SegmentedTranscriptViewer.tsx#L783))
    — read `currentTime` from a ref inside the handler instead of putting it
    in the effect deps.
7.4 Same treatment (or minimum: windowing) for the fallback
    [TranscriptViewer.tsx](../thestill/web/frontend/src/components/TranscriptViewer.tsx).

**Gate**: DOM node count on a 10k-segment transcript <2k; a 30s playback
Performance profile shows no continuous scripting from the viewer; karaoke
(spec #38), follow-playback (spec #23), and citation deep-links (spec #54)
manual scenarios pass.

### Phase 8 — Batch the remaining N+1s and writes (H9, H10)

8.1 Queue/DLQ enrichment: replace per-task `get_episode` /
    `get_podcast_for_refresh`
    ([api_commands.py:1150](../thestill/web/routes/api_commands.py#L1150),
    `:1183`, `:1554`) with two batch lookups (`id = ANY(%s)`), and give
    `get_podcast_for_refresh` a title-only variant (it currently fetches all
    the podcast's episode external_ids for a title,
    [postgres_podcast_repository_podcasts.py:468](../thestill/repositories/postgres_podcast_repository_podcasts.py#L468)).
8.2 Briefing render/narration: `get_episodes_by_ids(ANY(%s))` replacing the
    per-episode loop in
    [briefing_renderer.py:62](../thestill/services/briefing_renderer.py#L62)
    and [narration_runner.py:169](../thestill/services/narration/narration_runner.py#L169).
8.3 Cache `_category_maps` in `EpisodesMixin` (the podcasts mixin already
    caches; the asymmetry is flagged in the module's own note at
    [postgres_podcast_repository_episodes.py:40](../thestill/repositories/postgres_podcast_repository_episodes.py#L40)).
8.4 Batch writes: `update_episode_image_urls` → `executemany`
    ([postgres_podcast_repository_episodes.py:538](../thestill/repositories/postgres_podcast_repository_episodes.py#L538);
    `save_refresh_batch` at `:452` is the in-repo reference shape);
    `save()`'s destructive episode re-insert loop → `executemany`
    ([postgres_podcast_repository_podcasts.py:826](../thestill/repositories/postgres_podcast_repository_podcasts.py#L826));
    `_save_episode_idempotent`'s SELECT-then-write → single upsert
    (`:306`, `:725`); `seed_unscheduled_feeds` per-row UPDATE → one statement
    (`:1188`).
8.5 Per-episode transcript-link N+1 → one JOIN
    ([postgres_podcast_repository_episodes.py:1525](../thestill/repositories/postgres_podcast_repository_episodes.py#L1525)).

**Gate**: queue page with 200 seeded tasks issues ≤5 queries; briefing
generation for a 20-episode window issues ≤4 queries (plus N summary-file
reads, which stay); import of a 1000-episode feed completes in bounded
statement count (measured via `pg_stat_statements` calls delta).

## Suggested PR sequence

1. **PR 1** — Phase 1 (migration + schema + EXPLAIN gate evidence in the PR
   description). Smallest risk, unblocks acceptance criteria elsewhere.
2. **PR 2** — Phase 2 (route sweep + narration/RSS eviction + CI guard).
3. **PR 3** — Phase 3 (QueryClient default + per-hook intervals; include the
   Inbox pagination functional fix).
4. **PR 4** — Phase 4 (corpus-hydration retirement; largest diff, mostly
   repository + service layer, dual-backend).
5. **PR 5** — Phase 5 (entity batch + role-index precompute + mention paging).
6. **PR 6–8** — Phases 6, 7, 8 in any order.

## Design decisions

- **`def` routes over `run_in_threadpool` where possible** — less code, same
  effect, and matches the existing healthy pattern in `api_search.py` /
  `api_entities.py`. `run_in_threadpool` is reserved for routes that must
  await something else.
- **Keep `get_all()`** for CLI/batch paths; retire its *web* callers rather
  than redesigning the repository interface in this spec (spec #32 touches
  that surface).
- **Index changes ship as both alembic migration and `postgres_schema.py`
  DDL** — the schema module is the fresh-install path and the two must not
  drift (same convention spec #57 hit with the missing Postgres seeder).
- **No ranged/windowed word-timestamp API yet** — gzip + ETag hits the
  documented size budget; windowing is deferred until a measured need.

## Open questions

- Should the Phase 5.2 role index be a materialized view (simpler, refresh
  cost on a timer) or a maintained table (more code, always fresh)? Leaning
  maintained table written from the entity-extraction completion handler,
  since extraction is already the only writer.
- Phase 6.3's response-shape change needs a frontend/backend compatibility
  window — decide whether to version the endpoint or ship both sides in one
  deploy (single-deploy is fine today; #66 is a single-box host).

## Outcome (Phase 1, 2026-08-06)

Shipped as alembic migration
[0007_performance_indices.py](../thestill/migrations/versions/0007_performance_indices.py)
plus the matching `SCHEMA_SQL` DDL and a SQLite parity block at the end of
`_run_migrations` (portable subset only — SQLite's `DESC` already sorts nulls
last, so the pub_date rebuild is Postgres-only; trgm/jsonb-GIN don't port).
Two deviations from the plan as written: the Postgres tasks index landed as
`idx_tasks_podcast_stage(podcast_id, stage)` to mirror the SQLite queue's
existing index name and shape, and the inbox composite reuses SQLite's
established `idx_inbox_user_all` name.

Gate results on the seeded 30k-episode / 40k-inbox-row scratch instance:

| Gate | Before | After |
|---|---|---|
| Episode listing (`pub_date DESC NULLS LAST LIMIT 50`) | seq scan + top-N sort, 19.3ms | `idx_episodes_pub_date_nulls_last` index scan, 0.12ms |
| Inbox default view | all user rows fetched + sort | `idx_inbox_user_all` ordered scan, stops after 85 rows read, 0.17ms |
| Title `ILIKE '%…%'` | seq scan, 12.1ms | `idx_episodes_title_trgm` bitmap scan, 1.4ms |
| Failed-episodes DLQ view | full seq scan | `idx_episodes_failed` index scan |
| Summarize-stage (CLEANED) poll | full seq scan | `idx_episodes_state_cleaned` index scan |

Convergence verified three ways: fresh `SCHEMA_SQL` install, migration 0007
applied over the pre-existing schema, and a live `alembic upgrade head`
(0001→0007) all produce byte-identical index sets. Downgrade DDL
round-trips. Tests: 307 SQLite repository unit tests, 192 dual-backend
contract tests (Postgres side included via `TEST_DATABASE_URL`), 2
migrate-on-startup tests, full unit suite 2644 passed (one pre-existing
`test_entity_tools` MCP failure, present on the base commit, unrelated).

## Outcome (Phase 2, 2026-08-06)

**Route sweep (2.1)** — 65 route handlers converted from `async def` to sync
`def` (threadpooled by FastAPI), verified await-free by AST scan before each
conversion. Beyond the planned file list, the sweep also covered
`api_commands.py` (25 routes — the queue/DLQ/status endpoints the frontend
polls), `api_narrations.py`, `api_imports.py`, and the three non-awaiting
`auth.py` routes (`logout`, `get_current_user`, `update_current_user`,
plus `google_login`, which awaited nothing). Remaining async routes, each
with a reason: `stream_task_progress` (SSE), `get_episode_summary_by_slugs`
(awaits its `run_in_threadpool` LLM wraps), `auth_status` /
`google_callback` (await async OAuth), `health_check` (zero I/O — staying on
the loop keeps liveness responsive even with a saturated threadpool, same
rationale as the pre-existing sync `readiness_check`), and
`elevenlabs_webhook` (awaits `request.body()`, then hands the sync
signature-verify/DB/file work to a threadpooled `_process_elevenlabs_webhook`
helper).

**Long-running work (2.2)** — `resolve_podcast`'s 1–2s RSS fetch and
`get_latest_briefing`'s lazy generation now run in the threadpool via the
sync conversion (the resolve docstring's threadpool claim is finally true).
`narrate_briefing` also converted — the minutes-long LLM call no longer
touches the event loop — but the **202 + task-id contract is deferred**: the
task manager is singleton-per-`TaskType` and cannot key per-briefing tasks,
so the conversion needs a small task-manager extension plus frontend polling
changes in `useNarrateBriefing`/`NarrationView`. Documented in the route's
docstring; pairs naturally with backlog item M7 (scheduler narration
parallelism) in [70-performance-medium-backlog.md](70-performance-medium-backlog.md).

**Regression guard (2.3)** — `tests/unit/web/test_route_event_loop_guard.py`:
(a) an AST guard pinning the exact allowlist of async route handlers — any
new `async def` route fails until consciously allowlisted; (b) an
await-necessity check (an async route that never awaits has no reason to be
async); (c) a behavioral gate — with `get_status` stubbed to block for 1s,
a concurrent `/health` must answer in <0.5s (measured ~0.1s; an `async def`
regression would push it to ~1s). This replaces the plan's "AST check in CI
or pylint plugin" idea — a plain pytest is simpler and runs everywhere.

Tests: guard suite 3/3; web unit suite 232 passed; full unit suite 2647
passed; integration 359 passed (with `TEST_DATABASE_URL`). Three failures are
pre-existing on the base commit (verified by stash-rerun): the
`test_entity_tools` MCP schema test and two `test_default_deny_auth`
route-registration tests. Formatting drift in `api_search.py`/`api_imports.py`
also pre-exists and was left untouched.

## Related specs

- [44-postgres-migration.md](44-postgres-migration.md) — connection pool is
  its deferred scope; this plan's query-count reductions compose with it.
- [19-refresh-performance.md](19-refresh-performance.md) — refresh dedup map
  design; out of scope here.
- [68-live-episode-reader-refresh.md](68-live-episode-reader-refresh.md) —
  depends on `useEpisode`'s 5s poll; Phase 3 preserves it explicitly.
- [55-briefing-readiness-gate.md](55-briefing-readiness-gate.md) — its
  `202 briefing_pending` shape is the end state for Phase 2.2's lazy path.
- [70-performance-medium-backlog.md](70-performance-medium-backlog.md) — the
  deferred Medium/Low tier from the same review.
