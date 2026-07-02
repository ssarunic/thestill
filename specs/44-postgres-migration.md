# SQLite → Postgres Migration

> **Status:** 🚧 Implemented on branch `feat/postgres-migration-phase0` (2026-07-02) —
> all phases 0–6 code-complete: typed native schema (uuid/timestamptz/boolean/
> jsonb/vector + HNSW), all 8 repos ported (EntityRepository +
> PendingOperationsRepository ABCs extracted), PostgresQueueManager with a
> single-statement `FOR UPDATE SKIP LOCKED` claim, pgvector search backend +
> chunk writer + related-builder, `make_repositories` factory wired through
> cli/web/mcp/task-handlers, and a two-step data migration (text mirror +
> parity oracle → in-database typed promotion). Dual-backend contract suites
> run every port against a real Postgres. Remaining: alembic adoption and a
> CI Postgres service (follow-ups).
> **Created:** 2026-05-22
> **Updated:** 2026-07-02
> **Author:** Engineering
> **Priority:** High — prerequisite for [43-aws-hosting.md](43-aws-hosting.md)
> **Related:** [43-aws-hosting.md](43-aws-hosting.md), [01-architecture.md](01-architecture.md) (repository pattern), [04-testing.md](04-testing.md), [28-corpus-search-and-entities.md](28-corpus-search-and-entities.md) (sqlite-vec / entities), [16-full-pipeline-and-failure-handling.md](16-full-pipeline-and-failure-handling.md) (task queue + DLQ), [20-parallel-task-queues.md](20-parallel-task-queues.md), [42-robustness-and-failure-mode-hardening.md](42-robustness-and-failure-mode-hardening.md) (FM-3 datetime boundary, FM-5 test fidelity)

---

## Executive Summary

Move the persistence layer from SQLite to PostgreSQL so the hosted deployment
([#43](43-aws-hosting.md)) runs on a managed DB from day one and the eventual
HA move is a Multi-AZ checkbox rather than a second migration. The strategic
insight: **the risk here is the *code* port, not the infrastructure** — and
the cheapest, lowest-risk time to do it is *now, against a near-empty
database*, before six months of `chunks`/embeddings growth turns it into a
data-migration project.

**Readiness verdict from the code audit: ready to *add* Postgres cleanly for
~75% of the data layer; not ready to *run* on it.** The hard, hard-to-retrofit
part — a repository seam keeping `services/` engine-agnostic — already exists
for the core entities. What's missing is every Postgres implementation, a
driver, DSN/pool config, a backend selector, interfaces for two repos, and
direct ports of the two subsystems that bypass the seam (task queue, vector
search).

Because the seam keeps business logic untouched, this is **additive, not a
rewrite**. It is a well-bounded project, executed locally first, then
[#43](43-aws-hosting.md) deploys "the same app, different `DATABASE_URL`."

---

## Table of Contents

1. [Goals & Non-Goals](#goals--non-goals)
2. [Current-State Audit](#current-state-audit)
3. [Target Design](#target-design)
4. [Work Breakdown](#work-breakdown)
5. [Dialect Gotchas Checklist](#dialect-gotchas-checklist)
6. [Dual-Backend & Test Fidelity](#dual-backend--test-fidelity)
7. [Sequencing](#sequencing)
8. [Risks](#risks)
9. [Cross-References](#cross-references)

---

## Goals & Non-Goals

### Goals

- A working PostgreSQL backend behind the existing repository interfaces.
- `pgvector` (HNSW) replacing `sqlite-vec` for semantic search.
- A Postgres-native task-queue claim (`FOR UPDATE SKIP LOCKED`).
- Backend selection by config (`DATABASE_URL`), threaded through all entry
  points.
- Repository contract tests that run against a real Postgres (testcontainers),
  honoring [#42](42-robustness-and-failure-mode-hardening.md) FM-5 (no
  mock-only fidelity).

### Non-Goals

- Switching to a full ORM. Keep the raw-SQL repository pattern; port dialect,
  don't re-architect. (psycopg3 is the driver; a thin query layer is fine.)
- Async DB I/O. The worker already runs sync work via `asyncio.to_thread`;
  sync psycopg3 + a connection pool is the minimal change.
- Multi-AZ / HA (that's [#43](43-aws-hosting.md) Phase 2 — a checkbox once
  this lands).
- Data migration tooling beyond what a near-empty DB needs (see
  [Sequencing](#sequencing)).

---

## Current-State Audit

**What exists (the valuable part).** A genuine repository seam: 6 of 8 repos
have ABC interfaces, and their SQLite classes subclass them. The interface
docstring explicitly names PostgreSQL as the intended second backend
([repositories/podcast_repository.py:5](../thestill/repositories/podcast_repository.py#L5)),
and [search/base.py:19](../thestill/search/base.py#L19) is written in
anticipation of a Postgres move.

| Repo | Interface (ABC) | SQLite impl |
|---|:---:|:---:|
| podcast / episode | ✓ | ✓ `SqlitePodcastRepository(PodcastRepository, EpisodeRepository)` (~227 KB) |
| digest | ✓ | ✓ |
| briefing | ✓ | ✓ |
| inbox | ✓ | ✓ |
| podcast_follower | ✓ | ✓ |
| user | ✓ | ✓ |
| **entity** | **✗ (no base class)** | ✓ `SqliteEntityRepository` (~77 KB) |
| **pending_operations** | **✗ (no base class)** | ✓ `SqlitePendingOperationsRepository` |

**What's missing (why it can't run on PG yet).**

1. **Zero Postgres implementations.** Every concrete class is `Sqlite*`.
2. **No backend selection.** All four entry points hardcode the SQLite class:
   [cli.py:187](../thestill/cli.py#L187),
   [web/app.py:159](../thestill/web/app.py#L159),
   [mcp/tools.py:97](../thestill/mcp/tools.py#L97), and `mcp/resources.py`.
   ~21 instantiation sites total. No factory.
3. **Config is SQLite-shaped end to end.** Only `database_path` (a *file path*)
   exists — no `DATABASE_URL`/DSN
   ([utils/config.py:169](../thestill/utils/config.py#L169),
   [:348](../thestill/utils/config.py#L348)). Constructors take `db_path=…`,
   not a connection/pool. No psycopg/asyncpg dependency (the `sqlalchemy` /
   `alembic` in `uv.lock` are transitive via **optuna**, not wired in).
4. **The two un-interfaced repos are the ones most needed in the cloud.**
   `SqliteEntityRepository` (~77 KB, no base class) is the heart of the
   entity/search feature [#43](43-aws-hosting.md) runs;
   `SqlitePendingOperationsRepository` tracks async Dalston/ElevenLabs jobs.
   Both need an interface extracted before a PG impl can slot in.
5. **The trickiest subsystems bypass the seam.** The task queue
   ([core/queue_manager.py](../thestill/core/queue_manager.py): `sqlite3.connect`
   - WAL + `busy_timeout`) and vector search
   ([search/sqlite_vec_client.py](../thestill/search/sqlite_vec_client.py) k-NN
   over `vec0` / `vec_distance_cosine`, plus the write path in
   [core/chunk_writer.py](../thestill/core/chunk_writer.py)) sit outside any
   repository interface — direct ports.

---

## Target Design

- **Driver/connection.** psycopg3 + its built-in connection pool
  (`psycopg_pool.ConnectionPool`). Construct once at startup; hand pooled
  connections to repositories. Worker keeps running sync DB work inside
  `asyncio.to_thread`.
- **Config.** Add `DATABASE_URL` (DSN). When set → Postgres; else fall back to
  the existing `database_path` (SQLite) for local/test. A single
  `make_repositories(config)` factory returns the right concrete set,
  replacing the ~21 hardcoded `Sqlite*Repository(db_path=…)` call sites with
  one wiring point per entry surface (cli, web, mcp).
- **Schema.** Postgres DDL for every table the SQLite schema creates today.
  Adopt **alembic** for migrations (already present transitively; make it a
  real dev dependency) — gives versioned, reviewable schema changes for both
  this cutover and future work.
- **Vector search.** `pgvector` extension; `vector` column on the chunks
  table; **HNSW** index. Port the embed/write path
  ([core/chunk_writer.py](../thestill/core/chunk_writer.py)) and the search
  queries (`search/`) from `vec0`/`vec_distance` to pgvector operators
  (`<=>` cosine). The `sqlite-vec` dependency drops from the `[entities]`
  extra for the cloud image.
- **Task queue.** Replace the SQLite `busy_timeout` claim dance with
  `SELECT … FOR UPDATE SKIP LOCKED LIMIT n` in
  [core/queue_manager.py](../thestill/core/queue_manager.py). This is strictly
  more correct under concurrency and unblocks a *separate* worker process
  later ([#43](43-aws-hosting.md) Phase 3). Drop WAL pragmas.

---

## Work Breakdown

Ordered roughly by dependency and effort.

**Phase 0 — Plumbing (small, do first).**

- Add `psycopg[binary,pool]` dependency; make `alembic` a real dev/runtime dep.
- Add `DATABASE_URL` to [config.py](../thestill/utils/config.py); keep
  `database_path` as the SQLite fallback.
- Build the connection-pool/engine factory and a `make_repositories(config)`
  selector; wire it through `cli.py`, `web/app.py`, `mcp/tools.py`,
  `mcp/resources.py` (one wiring point each).

**Phase 1 — Port the 6 interfaced repos (bulk).**

- Write `Postgres*Repository` for podcast/episode, digest, briefing, inbox,
  podcast_follower, user. Dominated by the ~227 KB
  [sqlite_podcast_repository.py](../thestill/repositories/sqlite_podcast_repository.py).
- Apply the [dialect checklist](#dialect-gotchas-checklist) per file.

**Phase 2 — Extract interfaces, then port (entity + pending_ops).**

- Extract `EntityRepository` and `PendingOperationsRepository` ABCs from the
  existing SQLite classes (define the contract from current behavior).
- Write their Postgres implementations. Entity is the large one and is on
  [#43](43-aws-hosting.md)'s critical path (search/entities in cloud).

**Phase 3 — Task queue.** Port the claim to `FOR UPDATE SKIP LOCKED`; preserve
the PENDING→PROCESSING→COMPLETED/FAILED state machine and retry/DLQ semantics
([#16](16-full-pipeline-and-failure-handling.md),
[#20](20-parallel-task-queues.md)).

**Phase 4 — Vector search.** sqlite-vec → pgvector + HNSW; port write + query
paths; verify recall against the existing corpus
([#28](28-corpus-search-and-entities.md)).

**Phase 5 — Schema + data migration.** Author alembic migrations for the full
schema. Because the DB is near-empty at cutover, a one-shot copy (or simply
re-running discovery) suffices — no online dual-write needed.

**Phase 6 — Tests + CI.** Stand up a Postgres test fixture (testcontainers or
CI service); run the repository **contract suite against both engines**;
extend coverage to the queue and vector paths.

---

## Dialect Gotchas Checklist

Apply per ported file (each is a known SQLite→Postgres trap):

- **Placeholders:** `?` → `%s` (psycopg) — every parameterized query.
- **Upserts:** `INSERT OR REPLACE` / `INSERT OR IGNORE` →
  `INSERT … ON CONFLICT … DO UPDATE/NOTHING`.
- **Returning ids:** `cursor.lastrowid` / `last_insert_rowid()` →
  `INSERT … RETURNING id`.
- **Datetimes:** route through the existing tz-aware UTC boundary
  ([utils/datetime_utils.py](../thestill/utils/datetime_utils.py)); use
  `timestamptz`. This *removes* the SQLite text-timestamp foot-gun rather than
  porting it — aligns with [#42](42-robustness-and-failure-mode-hardening.md)
  FM-3.
- **Booleans:** SQLite `0/1` → Postgres `boolean`.
- **Autoincrement:** `INTEGER PRIMARY KEY AUTOINCREMENT` → `bigint generated
  always as identity` (or keep UUID PKs where already used).
- **JSON columns:** SQLite text-JSON → `jsonb`.
- **Case / collation:** Postgres is case-sensitive on identifiers and string
  comparisons; audit any `COLLATE NOCASE` / `LIKE` assumptions.
- **Concurrency:** drop `PRAGMA journal_mode=WAL` / `busy_timeout`; rely on
  Postgres MVCC + pooled connections.

---

## Dual-Backend & Test Fidelity

The repository seam was deliberately built for pluggable backends, so
**keeping SQLite for local/tests and Postgres for prod is consistent with the
existing design** — per-repo dialect differences hide behind the interface.
Dual-backend gets expensive only in the two un-interfaced repos and the
queue/vector code that sit outside the seam.

Decision to make explicitly: do the contract tests run against **both** engines
(higher fidelity, recommended — and the queue/vector ports must be tested on
Postgres regardless), or do we **cut SQLite over** once Postgres lands to avoid
a permanent dual-dialect tax? Either way, the data-layer tests must exercise
real Postgres, not mocks ([#42](42-robustness-and-failure-mode-hardening.md)
FM-5).

---

## Sequencing

**Port locally first, then deploy already-on-Postgres.** Do the entire port
against a local Docker Postgres (the [docker-compose.yml](../docker-compose.yml)
pattern), get tests green, *then* [#43](43-aws-hosting.md) is "same app, point
`DATABASE_URL` at RDS." Do **not** debug a DB migration and a brand-new cloud
environment simultaneously — that's the lowest combined risk and the whole
reason to do this before the AWS cutover.

---

## Risks

- **Scope creep on the 227 KB podcast repo** — port mechanically against the
  checklist; resist re-design mid-port.
- **pgvector index RAM** grows with the corpus; size RDS accordingly
  ([#43](43-aws-hosting.md) sizing) and verify search recall post-port.
- **Queue semantics regressions** — the claim/retry/DLQ behavior is load-
  bearing; cover with Postgres-backed concurrency tests before cutover.
- **Hidden SQLite coupling** outside `repositories/` (e.g., ad-hoc
  `sqlite3.connect` in scripts/tests) — grep and account for it in Phase 0.

---

## Cross-References

- [43-aws-hosting.md](43-aws-hosting.md) — the deployment this unblocks;
  assumes Postgres from day one.
- [01-architecture.md](01-architecture.md) — repository pattern this builds on.
- [28-corpus-search-and-entities.md](28-corpus-search-and-entities.md) —
  sqlite-vec / entity data layer being ported to pgvector.
- [16-full-pipeline-and-failure-handling.md](16-full-pipeline-and-failure-handling.md)
  / [20-parallel-task-queues.md](20-parallel-task-queues.md) — task-queue
  semantics to preserve.
- [42-robustness-and-failure-mode-hardening.md](42-robustness-and-failure-mode-hardening.md)
  — FM-3 (datetime boundary) and FM-5 (test fidelity) constraints.
- [04-testing.md](04-testing.md) — coverage standards for the contract suite.
