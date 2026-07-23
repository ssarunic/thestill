# Universal Follower Gate

> **Status:** 🚧 Implemented on `feat/63-universal-follower-gate` (2026-07-23) — predicate centralized per backend (`_active_feed_sql`), auto-follow-on-add orchestration wired into CLI / MCP / web resolve, dual-backend contract tests green (SQLite + Postgres).
> **Created:** 2026-07-23
> **Updated:** 2026-07-23
> **Author:** Engineering (pipeline gating)
> **Related:** [#13 multi-user-shared-podcasts](13-multi-user-shared-podcasts.md) (introduced `podcast_followers` and the shared-podcast model), [#31 import-arbitrary-episodes](31-import-arbitrary-episodes.md) (introduced the `auto_added`-only follower gate this spec generalizes), [#48 background-refresh-scheduling](48-refresh-scheduler.md) (the due/seed queries this gate now filters), [#60 refresh-network-failure-classification](60-refresh-network-failure-classification.md) (the `_ACTIVE_FEED_SQL` literal this spec replaces), [#64 legacy-account-claim](64-legacy-account-claim.md) (companion: migrates the legacy local account's follows so this gate reflects real intent)

---

## Executive summary

Before this spec, the refresh pipeline polled — and therefore downloaded,
transcribed, and summarized — every non-synthetic podcast that was added
manually, regardless of whether anyone follows it. Only `auto_added`
podcasts (spec #31 import side-effects) required a follower. This spec
makes the follower requirement **universal**: a podcast receives recurring
background work only while at least one user follows it. "Processed =
followed" becomes the single pipeline-demand invariant, with
`podcast_followers` as its one source of truth.

Two coordinated changes:

1. **Predicate** — the refresh-eligibility SQL drops its `auto_added`
   disjunct and is centralized into one parameterized helper per backend
   (`_active_feed_sql`), used by all five bulk refresh surfaces.
2. **Add paths** — in single-user mode, every add path now auto-follows
   the default user at add time (via a shared orchestration function), so
   the gate can never starve a single-user install.

## 1. Behavior change

| Surface | Before | After |
|---|---|---|
| `get_podcasts_for_refresh` (spec #19 bulk loader) | synthetic excluded; `auto_added` needs follower | synthetic excluded; **everything** needs follower |
| `get_due_podcasts`, `seed_unscheduled_feeds` (spec #48) | same two-tier rule | universal follower rule |
| `get_quarantine_probe_due`, `get_refresh_health_counts` (spec #60) | same two-tier rule via `_ACTIVE_FEED_SQL` | universal follower rule via `_active_feed_sql()` |
| CLI `thestill add` / MCP `add_podcast` / web `POST /api/podcasts/resolve` | created zero-follower rows | single-user mode: auto-follow the default user; multi-user mode: unchanged (resolve keeps its browse-without-committing UX) |
| Web `POST /api/commands/add` | followed the authenticated caller | unchanged (now via `follow_best_effort`) |

**Deliberately ungated** (unchanged):

- Explicit single-podcast operations — `get_podcast_for_refresh`,
  refresh with a concrete `podcast_id` (CLI `--podcast-id`, the post-add
  background refresh in the resolve endpoint, queued `REFRESH_FEED`
  tasks). A just-added feed must be able to run its first discovery
  before anyone follows it.
- The legacy full-scan CLI stages (`download`, `downsample`,
  `transcribe`, …) which iterate `repository.get_all()`. They only see
  episode rows that already exist; with the gate in place, unfollowed
  feeds stop producing new episode rows, so the scanners drain naturally.
  A truly airtight per-stage gate is out of scope (see Open questions).
- The one-time SQLite `_run_migrations` cadence backfill retains the
  historical two-tier predicate — it is dead code on every migrated DB
  and editing history adds risk with no runtime effect.

**Health-count semantics** (operator-visible): `get_refresh_health_counts`
`active`/`due_now`/`backing_off` now exclude unfollowed podcasts. On a
deployment with manually-added-but-unfollowed feeds, these numbers drop on
upgrade day. That is the gate working as intended, not a regression.

## 2. Design

### 2.1 Predicate helper (per backend)

`SqlitePodcastRepository._active_feed_sql(alias="podcasts", *,
require_incomplete=True)` (and the identically-shaped Postgres method on
`PodcastsMixin`) replaces the spec #60 `_ACTIVE_FEED_SQL` string literal:

```sql
COALESCE({alias}.synthetic, 0) = 0
[AND COALESCE({alias}.is_complete, 0) = 0]          -- require_incomplete
AND EXISTS (SELECT 1 FROM podcast_followers pf WHERE pf.podcast_id = {alias}.id)
```

Parameterization exists because the five call sites genuinely differ:
`get_podcasts_for_refresh` aliases `FROM podcasts p` and historically does
NOT filter `is_complete`; the other four use the bare table name and do.
Those differences are preserved exactly — this spec changes only the
`auto_added` disjunct.

### 2.2 Add-time auto-follow

New module [`thestill/services/podcast_add.py`](../thestill/services/podcast_add.py):

```python
add_podcast_and_auto_follow(podcast_service, follower_service, auth_service, config, url)
```

Composes the three existing services; in single-user mode
(`not config.multi_user`) it follows the default user via the new
`FollowerService.follow_best_effort` (tolerates already-following, logs —
never raises — anything else; a follow failure never turns a successful
add into a failure). `PodcastService` itself stays dependency-free of
auth/followers.

Call sites: CLI `add`, MCP `add_podcast` (which gained
`FollowerService`/`InboxService` wiring for parity), web resolve
endpoint. `POST /api/commands/add` keeps its own explicit
authenticated-caller follow, now through `follow_best_effort` (replacing a
hand-rolled try/except).

The single-user boot-time auto-follow hook
([`web/app.py`](../thestill/web/app.py) `single_user_auto_follow_complete`)
stays as the reconciliation safety net for rows created before this spec
or through any path that slips the net.

## 3. Testing

- [`tests/unit/repositories/test_sqlite_refresh_predicate.py`](../tests/unit/repositories/test_sqlite_refresh_predicate.py)
  — rewritten for the universal rule (`test_refresh_excludes_unfollowed_podcast`).
- [`tests/integration/test_podcast_repository_podcasts_contract.py`](../tests/integration/test_podcast_repository_podcasts_contract.py)
  — dual-backend (SQLite + real Postgres): new
  `test_refresh_excludes_unfollowed_everywhere` covers all four gated
  surfaces flipping on with a single follow; existing refresh fixtures
  seed followers.
- [`tests/unit/core/test_spec48_refresh_feed.py`](../tests/unit/core/test_spec48_refresh_feed.py)
  — fixture podcast now followed; `test_due_query_excludes_unfollowed_podcast`
  proves follower removal disables and re-follow re-enables scheduling.
- [`tests/unit/services/test_podcast_add.py`](../tests/unit/services/test_podcast_add.py)
  — orchestration: single-user follows, multi-user never, failed add
  never, idempotent re-add.
- Pipeline/incident integration suites updated to seed followers
  (mirroring the production auto-follow) rather than relying on the old
  loophole.

Full suite: 2433 unit + 351 integration passing; Postgres contract legs
run against a real `TEST_DATABASE_URL`.

## 4. Rollout notes

- **Prod (Postgres, multi-user)**: every podcast currently has ≥1
  follower only because the legacy local account blanket-follows all of
  them (single-user-era boot hook). After spec #64's claim/discard is
  run, feeds no real user follows go dormant — the desired outcome.
  Before merge, snapshot `get_refresh_health_counts` so the post-deploy
  drop in `active` is explainable.
- **CLI add against a multi-user DB** creates an unfollowed (dormant)
  podcast — same as before this spec for `auto_added` rows; now uniform.
  This is the intended "tracked but dormant" state.

## Open questions

- Should the legacy full-scan CLI stages (`download`, `transcribe`, …)
  also filter by follower, for episodes persisted before the gate (or via
  the import path)? Deferred — the queue-based pipeline is the production
  path; the scanners are operator tools.
- Spec #31's open item "does `auto_added` stay set after first follow"
  is now moot for refresh eligibility (the flag no longer participates);
  it remains a UI/provenance label only.

## Revision history

- **v1 (2026-07-23)** — Initial implementation: universal predicate via
  `_active_feed_sql` (both backends), `follow_best_effort`,
  `add_podcast_and_auto_follow` orchestration wired into CLI/MCP/resolve,
  commands/add cleanup, full test-fixture migration.
