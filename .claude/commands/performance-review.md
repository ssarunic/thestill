# Full-stack performance review

Analyse the UI, service layer, data access layer, and the Postgres database for performance problems caused by sloppy DB calls, missing indices, absent paging, and other deviations from standard practice in a project of this shape. This is an **analysis task, not a fix task**: the deliverable is a ranked findings report with evidence. Do not change code unless explicitly asked afterwards.

## Ground rules

- Every finding must cite concrete evidence: `file.py:line` references and, where relevant, the SQL text or query plan. No hand-waving ("this could be slow") — show the loop, the query, the missing index.
- Judge Postgres as the production target. The SQLite repositories (`sqlite_*`) matter only where the shared service code forces a pattern on both backends.
- Distinguish **deliberate, documented trade-offs** from sloppiness. Example: `thestill/utils/postgres_ext.py` documents connection-per-operation as an interim design with pooling deferred to spec #44 — report the *cost* of that trade-off (connections per request on hot endpoints), but label it as a known decision, not a defect.
- Scale assumptions: hundreds of podcasts, tens of thousands of episodes, transcripts of 10k+ segments/words each, multi-user inbox fan-out (one inbox row per user per episode). Evaluate every query as if tables are at that size, not at dev-database size.

## Where to look

| Layer | Location |
|---|---|
| UI | `thestill/web/frontend/src/` — React + TypeScript, TanStack Query, react-router |
| API routes | `thestill/web/routes/` — FastAPI (`api_inbox.py`, `api_episodes.py`, `api_search.py`, `api_dashboard.py`, `api_transcript_words.py`, …) |
| Web services | `thestill/web/services/` and middleware in `thestill/web/middleware/` |
| Domain services | `thestill/services/` (`inbox_service.py`, `briefing_service.py`, `refresh_service.py`, `stats_service.py`, `follower_service.py`, …) |
| Repositories | `thestill/repositories/postgres_*.py`, factory in `factory.py` |
| Schema & indices | `thestill/repositories/postgres_schema.py`, `thestill/migrations/` |
| Search | `thestill/search/` (pgvector client, related-content builder) |
| Connection handling | `thestill/utils/postgres_ext.py`, `thestill/utils/sqlite_ext.py` |

## Layer 1 — Postgres schema and indices

Read `postgres_schema.py` and the migrations in full. Build a table → (columns, indices, FKs) inventory, then check every query in the `postgres_*` repositories against it:

- **Missing indices**: every column used in `WHERE`, `JOIN … ON`, `ORDER BY`, or `DISTINCT` on a growing table needs index coverage. Pay special attention to: foreign-key columns (Postgres does **not** auto-index FKs), status/state columns used by pipeline polling, timestamp columns used for "latest N" ordering, and the per-user inbox table (user_id + episode/read-state lookups).
- **Composite-index order**: multi-column filters (`user_id AND read = false ORDER BY published_at DESC`) want a composite index matching filter-then-sort order; flag places where only single-column indices exist and Postgres would have to sort.
- **Partial indices**: high-selectivity boolean/status filters (`WHERE archived = false`, `WHERE status = 'pending'`) that scan mostly-cold rows are candidates.
- **Index waste**: duplicate or unused indices that slow writes on hot tables (inbox fan-out inserts).
- **Column types**: text-typed IDs joined against other text IDs are fine; flag mismatched types that defeat index use, and unbounded `text` columns fetched in list queries (full transcript bodies in a listing SELECT).
- **pgvector**: check `thestill/search/pgvector_client.py` — is there an appropriate ANN index (HNSW/IVFFlat) for similarity queries, or are they sequential scans? Are embedding columns excluded from ordinary row fetches?

If a scratch Postgres instance is available (check `docker compose` config / test fixtures), load the schema and run `EXPLAIN` on the 5–10 hottest queries to confirm suspected sequential scans. If not, reason from the schema and say so.

## Layer 2 — Data access layer (repositories)

Read every `postgres_*.py` repository. Hunt for:

- **N+1 queries**: a query returning a list, followed by a per-row query — in the repository itself, or induced by a service looping over repository calls (`for episode in episodes: repo.get_podcast(episode.podcast_id)`). Grep services for repository calls inside loops.
- **`SELECT *` / over-wide fetches**: list endpoints pulling full transcript/summary/embedding blobs when only titles and IDs are rendered.
- **Missing LIMIT / unpaged reads**: any `SELECT` on a growing table without `LIMIT`, and any repository method named `get_all_*` or `list_*` with no paging parameters — then trace who calls it and whether the caller truly needs the full set.
- **Row-at-a-time writes**: loops issuing single-row `INSERT`/`UPDATE` where `executemany`, multi-row `VALUES`, or `COPY` belongs — the inbox fan-out (one row per user per new episode) and refresh pipeline are prime suspects.
- **Transaction scope**: connection-per-operation means each `with connect(dsn)` is its own transaction. Flag multi-step operations that open several connections where one transaction is both faster and more correct; conversely flag long-lived transactions holding locks across network calls (LLM/HTTP calls inside a `with connect(...)` block).
- **Connection churn**: count how many `connect()` calls a single hot HTTP request triggers (route → service → several repository methods). Report the worst offenders as concrete request → connection-count traces.
- **In-Python work that belongs in SQL**: fetching whole tables to filter/sort/aggregate in Python (`sorted(rows, ...)[:10]`, counting by iterating), and existence checks done via full fetch instead of `SELECT 1 … LIMIT 1` / `COUNT(*)`.
- **Search paths**: LIKE `'%term%'` scans where a trigram/FTS index is warranted; `ORDER BY embedding <=> …` without an ANN index.

## Layer 3 — Service layer

Read the domain services and `thestill/web/services/`:

- **Chatty orchestration**: services making sequential repository calls that could be one joined query or batched call; aggregation endpoints (`stats_service.py`, dashboard) issuing one query per podcast/user instead of a `GROUP BY`.
- **Fan-out costs**: per-user inbox fan-out and briefing scheduling — per-user work that is O(users × episodes); is it batched, and does anything re-scan the full history on every run instead of using a watermark/cursor?
- **Blocking calls in async context**: FastAPI routes declared `async def` that call synchronous psycopg/repository code directly, blocking the event loop — versus sync `def` routes that FastAPI correctly threads. Check what the route signatures actually are and whether slow work (LLM calls, transcription) is offloaded to the task manager.
- **Missing caching**: repeatedly recomputed derived data (stats, top-podcasts, entity aggregates) with no cache or materialisation; identical config/lookup queries re-issued per request.
- **Redundant re-reads**: the same entity fetched multiple times within one request across route → service → repository hops.

## Layer 4 — API routes and response shaping

- **Unpaged endpoints**: every list endpoint in `thestill/web/routes/` must accept and enforce paging (limit/offset or cursor) with a server-side cap. List endpoints that return unbounded collections, and which UI screens consume them.
- **Over-fetching in responses**: endpoints serialising full transcripts/summaries into list responses; word-level data (`api_transcript_words.py`) returned wholesale instead of ranged/windowed.
- **Payload hygiene**: no compression consideration, huge JSON bodies, missing `Cache-Control`/ETag on immutable resources (finished transcripts, audio metadata).
- **Per-request middleware cost**: anything in `thestill/web/middleware/` doing DB work on every request (auth/session lookups) without caching.

## Layer 5 — UI (React frontend)

Read `thestill/web/frontend/src/` (`api/`, `hooks/`, `pages/`, `components/`):

- **Request waterfalls**: sequential dependent fetches (page → list → per-item detail) that should be parallel or server-joined; per-row queries inside rendered lists (a `useQuery` per list item = client-side N+1).
- **TanStack Query hygiene**: `staleTime`/`gcTime` left at defaults causing aggressive refetch of stable data; aggressive `refetchInterval` polling (task/status monitors — what's the interval, does it back off, does it stop when the tab is hidden?); missing query-key granularity causing broad invalidation.
- **Unbounded rendering**: long lists (episodes, inbox, transcript segments) rendered without pagination, infinite-scroll, or virtualisation; transcript views mounting tens of thousands of DOM nodes — check the transcript playback/word-highlighting components especially (specs #23/#24), where per-word spans and per-tick re-renders are the classic failure.
- **Re-render storms**: media-player time updates or context values propagating through broad component trees on every tick; missing memoisation on expensive derived computations over large transcript arrays.
- **Bundle**: single unsplit bundle (no route-level lazy loading) — check `vite.config.ts` and router setup.

## Method

1. Inventory schema + indices first (Layer 1) — it's the reference for everything else.
2. Trace the 5 hottest user flows end-to-end (UI hook → route → service → repository → SQL): inbox open, episode/transcript view, search, dashboard, and the refresh/fan-out pipeline. Most real findings will fall out of these traces.
3. Then sweep each layer's checklist for what the traces missed.
4. For each candidate finding, verify before reporting: re-read the code, check the index inventory, confirm the call is actually on a hot path. Discard anything that dissolves under scrutiny.

## Report format

Produce a single Markdown report:

1. **Summary** — one paragraph: overall health, the 3 worst problems.
2. **Findings table** — ranked by severity:
   - **Critical**: wrong at current scale (unbounded query on a fan-out table, N+1 on a hot endpoint, event-loop blocking).
   - **High**: wrong at target scale (missing index on a growing table, unpaged endpoint, full-blob list fetches).
   - **Medium**: measurable waste (connection churn, refetch storms, missing cache headers).
   - **Low**: hygiene (bundle splitting, minor memoisation).
3. **Finding detail** — per finding: layer, evidence (`file:line` + code/SQL excerpt), why it's slow at the stated scale, and the standard remedy in one or two sentences (name the technique — composite index, keyset pagination, `executemany`, virtualised list — do not write the patch).
4. **Deliberate trade-offs observed** — documented interim designs (e.g. pooling deferred per spec #44) with their current cost, kept separate from defects.
5. **Suggested measurement** — the handful of `EXPLAIN ANALYZE` runs, log fields, or browser-profiler checks that would confirm the top findings in production.
