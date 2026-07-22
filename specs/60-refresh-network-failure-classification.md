# Refresh Failure Classification — Network vs Feed-Gone

> **Status:** 📝 Draft v2 (2026-07-22 — substantially revised after design review; see [Revision History](#revision-history))
> **Created:** 2026-07-22
> **Author:** Engineering (failure-recovery design)
> **Related:** [#49 queue-auto-healing-and-recovery](49-queue-auto-healing-and-recovery.md) (owns the `infra`/`item` attribution + circuit breaker + healer this spec **feeds into** rather than duplicates), [#48 refresh-feed-stage](48-refresh-feed-stage.md) (introduced the feed-park mechanism this revises), [#42 robustness-and-failure-mode-hardening](42-robustness-and-failure-mode-hardening.md) ("transient-treated-as-terminal" + "silent-degradation" + FM-6 parallel-path drift, all live here), [#03 error-handling](03-error-handling.md) (exception hierarchy + `error_classifier` this extends), [#25 security-audit](25-security-audit-and-hardening.md) / [url_guard.py](../thestill/utils/url_guard.py) (SSRF policy that must **not** be softened into a retry loop), [#19 refresh-performance](19-refresh-performance.md) (owns the scheduler + conditional-GET fetch), [#00 constitution](00-constitution.md) (principle #4 loud failure, #5 self-healing)

---

## Executive Summary

On **2026-07-15/16** a laptop-hosted instance had **all 90 followed feeds
parked** (`next_refresh_at = NULL`, `last_refresh_error` set) and stopped
discovering new episodes for **7 days**. The machine had simply been **closed /
offline** for a couple of days — not one of the 90 feeds was down.

The defect is a **loss of failure meaning before the queue can apply the right
recovery policy**:

1. **The fetch boundary flattens the error.** `fetch_rss_content`
   ([media_source.py:613](../thestill/core/media_source.py#L613)) catches *every*
   `requests.RequestException` and collapses it to
   `FetchRSSResult(status_code=0, error=str(e))`. A DNS failure and an HTTP
   `410 Gone` become indistinguishable the moment they leave the fetch call.

2. **The handler raises a generic, mis-classifying error.** `had_error=True`
   surfaces as `TransientError("Feed refresh failed for <title>")`
   ([task_handlers.py:1404-1410](../thestill/core/task_handlers.py#L1404-L1410)).
   Its message matches no infrastructure pattern, so `classify_error_class`
   ([error_classifier.py:248-276](../thestill/core/error_classifier.py#L248-L276))
   labels it **`item`** — "this work item is bad" — not **`infra`**.

3. **Retry-exhaustion parks unconditionally.** Because the error is `item`, spec
   #49's circuit breaker and healer **never see it**. The 3 retries burn inside
   the outage window ([queue_manager.py:345](../thestill/core/queue_manager.py#L345)),
   the task lands in the DLQ, and the worker parks the feed via
   `record_refresh_error(terminal=True)`
   ([task_worker.py:843-855](../thestill/core/task_worker.py#L843-L855)). No
   automatic path re-arms it.

**This spec's correction to its own v1:** spec [#49](49-queue-auto-healing-and-recovery.md)
is **not** episode-specific. Its healer selects `WHERE status='failed' AND
error_class='infra'` ([queue_manager.py:2010](../thestill/core/queue_manager.py#L2010))
and will re-drive *any* infra task, `refresh-feed` included; on success
`record_refresh_success` clears the podcast park. **The machinery to recover this
incident already exists** — the only reason it didn't fire is that the refresh
failure was mislabelled `item`. So the fix is far smaller than v1 proposed: **stop
destroying the failure kind, map connectivity to `infra` so #49 recovers the
queue, and give the podcast a durable per-kind policy so it is never parked for a
transient environmental fault.** No bespoke connectivity probe, no wake-gap clock
detector.

**Preference bias (explicit, unchanged):** in ambiguous cases (5xx, timeout) lean
toward *keep trying*, never *park forever*. Classification is separated from
parking policy so this bias lives in one place.

---

## Table of Contents

1. [The Incident](#the-incident)
2. [How a Feed Gets Parked Today](#how-a-feed-gets-parked-today)
3. [What #49 Already Provides — and the Exact Seam](#what-49-already-provides--and-the-exact-seam)
4. [Failure Taxonomy — Classification vs Policy](#failure-taxonomy--classification-vs-policy)
5. [Proposed Design — Three Parts](#proposed-design--three-parts)
6. [Security: `UnsafeURLError` Must Split](#security-unsafeurlerror-must-split)
7. [Transport Details That Bite](#transport-details-that-bite)
8. [Phased Implementation Plan](#phased-implementation-plan)
9. [Testing](#testing)
10. [Rollout & Recovery](#rollout--recovery)
11. [Open Questions](#open-questions)
12. [Revision History](#revision-history)

---

## The Incident

Reconstructed from the live Postgres instance on 2026-07-22:

| Signal | Value |
|---|---|
| Followed (non-synthetic) feeds | 90 |
| Feeds parked (`next_refresh_at IS NULL`) | **90 (100%)** |
| Feeds with `last_refresh_error` | 90 (all generic `Feed refresh failed for …`) |
| Feeds due now | 0 |
| Newest `episodes.created_at` | 2026-07-15 09:57 |
| `refresh-feed` tasks `failed` | 86, all between **2026-07-15 12:31 → 2026-07-16 12:45** |

The server never stopped (up since 2026-07-14). It refreshed normally for ~1 day,
then every feed failed inside a ~24h window coinciding exactly with the laptop
being closed, every feed exhausted its retry budget against the dead network, and
every feed parked. The scheduler kept ticking against an empty due-set — **loud in
cadence, silent in output.** This is [#42](42-robustness-and-failure-mode-hardening.md)'s
**"transient treated as terminal"** + **"silent degradation"**, in a path #42's
catalogue predates.

The stored errors are **already generic**, which is decisive for the design: the
original failure kinds **cannot be re-derived** from `last_refresh_error` text.
Any legacy-recovery step must therefore treat the whole cohort as
"unknown → assume environmental" rather than pretending to reconstruct categories.

---

## How a Feed Gets Parked Today

```
scheduler.tick()                       # every 60s (refresh_scheduler.py:97)
  └─ get_due_podcasts()                # next_refresh_at NOT NULL AND <= now
       └─ enqueue REFRESH_FEED
            └─ handle_refresh_feed()   # task_handlers.py:1363
                 └─ _refresh_single_podcast()   # feed_manager.py — 8-tuple return
                      └─ fetch_rss_content()     # media_source.py:557
                           except requests.RequestException:
                             return FetchRSSResult(status_code=0, error=str(e))    # ← kind lost
                 had_error=True → raise TransientError("Feed refresh failed …")    # task_handlers.py:1410
       classify_error_class(msg) → "item"        # no infra pattern match → invisible to #49
       worker retries 3× (~5s,30s,…) inside the outage → DLQ
       _mark_episode_failed → is_feed_scoped_stage → record_refresh_error(terminal=True)   # task_worker.py:850
         UPDATE podcasts SET next_refresh_at = NULL, last_refresh_error = …               # pg repo :1183
  ── feed invisible to get_due_podcasts forever; only MANUAL DLQ retry re-arms ──
```

Two independent facts, both required for the stall:

- **`status_code=0` erases the one discriminating bit.** `raise_for_status()`
  raises `HTTPError` for 404/410 — itself a `RequestException` — so it shares the
  `except` branch with `ConnectionError`/`Timeout` and the real status is dropped.
- **`item` mislabelling hides it from auto-healing** (next section).

---

## What #49 Already Provides — and the Exact Seam

Spec #49 built exactly the recovery this incident needs, for the **task queue**:

- `classify_error_class(exc)` → `fatal` | `infra` | `item`
  ([error_classifier.py:248](../thestill/core/error_classifier.py#L248)), infra
  detected by message signature (`is_infrastructure_error`).
- A **per-stage circuit breaker** that pauses a stage fleet-wide when infra
  failures correlate, with half-open recovery probes.
- A **healer loop** that re-requeues `failed` + `error_class='infra'` tasks after
  a cooldown ([queue_manager.py:2010](../thestill/core/queue_manager.py#L2010)),
  resetting the retry budget.

A recovered `refresh-feed` task calls `record_refresh_success`, which clears
`last_refresh_error` and reschedules ([pg repo :1121](../thestill/repositories/postgres_podcast_repository_podcasts.py#L1121)).

**The seam is one line of meaning:** the refresh handler raises a generic
`TransientError` whose message matches no infra pattern, so it classifies as
`item`. Fix the classification and #49's breaker + healer engage automatically —
the feed's park is cleared on the healed success, and during a broad outage the
breaker pauses the whole refresh stage instead of letting 90 feeds each burn their
budget. **A separate connectivity probe is therefore not part of v1.** (It remains
a possible enhancement — see Open Questions — but recovery must not depend on it.)

---

## Failure Taxonomy — Classification vs Policy

v1's taxonomy was too coarse and self-contradictory (it forbade parking on
anything but "feed gone", then allowed ambiguous failures to park). Split the two
concerns.

### Classification — what kind of failure this was (`RefreshFailureKind`)

```text
connectivity      # host never reached: ConnectionError, ConnectTimeout, DNS-resolution failure
remote_transient  # host reached, unhappy, likely temporary: 429, 5xx, read timeout after connect
remote_gone       # host says the feed is not there: 410 (definitive); 404 (probable, needs corroboration)
authentication    # 401 / 403: private-feed token or auth problem — user action
invalid_content   # reachable + parseable HTTP but bad body: bozo/malformed feed, empty
security_policy   # SSRF refusal from url_guard (forbidden destination) — NOT connectivity
internal          # programming error in our code — must be loud, must NOT condemn the feed
```

### Policy — what to do about it (decoupled, one place for the bias)

| Kind | `error_class` (queue) | Feed action |
|---|---|---|
| `connectivity` | **`infra`** | Never park. Keep `next_refresh_at` set; exponential backoff. #49 breaker/healer own queue recovery. |
| `remote_transient` | `infra` (5xx) / `item` (429) | Never park. Respect `Retry-After` → `next_refresh_at`. |
| `remote_gone` (410) | `fatal` | Quarantine with reason `feed_gone`. Decisive. |
| `remote_gone` (404) | `item` | Quarantine **only after failures span a configured horizon** (not 3 retries in minutes). |
| `authentication` | `item` | Pause with reason `auth_required` — distinct, actionable, surfaced to the user, not silently parked. |
| `invalid_content` | `item` | Backoff + retry a bounded number of times; quarantine as `invalid_content` only if persistent across the horizon. |
| `security_policy` | `fatal` | Quarantine with reason `blocked_unsafe`; **do not retry** — a security refusal must draw operator attention, not a retry loop. |
| `internal` | `fatal` | Fail the task loudly (raise); **never** mark the feed gone. |

Key rules that fix the reported contradictions:

- **Only `410` and `security_policy` park decisively.** `404` and
  `invalid_content` require persistence across *wall-clock time while online*, not
  a rapid retry burst.
- **Quarantine ≠ permanent death.** A quarantined feed still receives a
  very-low-frequency probe (weekly/monthly) so a temporary CDN/deploy mistake
  behind a `404` self-heals without an operator.

---

## Proposed Design — Three Parts

### Part 1 — Structured failure preservation (replaces the brittle 8-tuple)

`_refresh_single_podcast` already returns an **eight-element tuple** and catches
*every* downstream exception — source detection, YouTube refresh, extraction,
programming errors — flattening all to `had_error=True`
([feed_manager.py:476](../thestill/core/feed_manager.py#L476)). Adding a ninth
element for the category would deepen that brittleness.

Replace the tuple with a structured result:

```python
@dataclass(frozen=True)
class RefreshFailure:
    kind: RefreshFailureKind
    http_status: int | None          # real status, never coerced to 0
    retry_after: datetime | None     # parsed from Retry-After when present
    exception: str                   # original repr, for the DLQ / logs
    is_internal: bool = False        # True → raise loudly, never condemn feed

@dataclass(frozen=True)
class RefreshAttemptResult:
    podcast: Podcast
    new_episodes: list[Episode]
    conditional_hit: bool
    headers_rotated: bool
    image_rows: ...
    audio_rows: ...
    source: MediaSource | None
    failure: RefreshFailure | None   # None == success
```

This carries the kind through **every** failure path, not just the HTTP one, and
makes the `internal` case explicit so a bug in our code raises rather than parking
a healthy feed. The fetch layer (`FetchRSSResult`/`FetchAndParseResult`) grows the
same `kind` + `http_status` + `retry_after` fields, and — critically — the parse
path stops returning `error=None` after a `bozo`/parse failure
([media_source.py:707](../thestill/core/media_source.py#L707)); it emits
`invalid_content`.

The handler maps `failure.kind` → the raised exception type and the `error_class`
attribution from the policy table, so `connectivity` reaches the queue as `infra`.

### Part 2 — Reuse #49 recovery (no new probe)

- **Attribution:** the handler raises a connectivity failure as an error that
  `classify_error_class` labels `infra` (add the connectivity signatures to
  `is_infrastructure_error`, or pass `error_class` explicitly through the failure
  path rather than relying on message matching — preferred, since message matching
  is itself fragile).
- **Breaker + healer:** with `infra` attribution, the refresh stage's breaker
  pauses fleet-wide during an outage and half-open probes recovery; the healer
  re-drives DLQ'd infra `refresh-feed` tasks after cooldown; success clears the
  park. No code in this spec duplicates that.

### Part 3 — Durable feed policy (persisted, not string-encoded)

v1 hand-waved "encode the category in the error string." That cannot back the
by-category queries Parts 5/rollout need, and the incident's rows are already
generic. Persist explicit columns on `podcasts` (additive migration; forward-
compatible per #44):

| Column | Purpose |
|---|---|
| `last_refresh_failure_kind` | `RefreshFailureKind` of the most recent failure |
| `last_refresh_status_code` | real HTTP status (nullable) |
| `consecutive_refresh_failures` | drives the 404/invalid-content time-horizon gate |
| `refresh_disabled_reason` | why quarantined: `feed_gone` / `blocked_unsafe` / `auth_required` / `invalid_content` (NULL = active) |
| `refresh_retry_after_at` | server-directed next attempt (from `Retry-After`) |

`record_refresh_error` takes the kind + status + retry-after and applies the
**policy table**, not a bare `terminal` boolean. Parking becomes a *reason-tagged
quarantine*, queryable and reversible by kind.

### What is explicitly removed vs v1

- **Layer 3 wake-gap detector — deleted.** Python's `time.monotonic()` excludes
  suspend on both macOS (`mach_absolute_time`) and Linux (`CLOCK_MONOTONIC`), so a
  two-day sleep shows ~no gap; the detector would not fire. And it is unnecessary:
  once connectivity failures keep `next_refresh_at` non-NULL, a resumed laptop's
  overdue feeds are **naturally due** on the next tick. If resume *telemetry* is
  ever wanted, compare wall-clock elapsed to monotonic elapsed — but recovery must
  never depend on it.
- **Bespoke connectivity probe — deferred.** Superseded by Part 2.

---

## Security: `UnsafeURLError` Must Split

`url_guard` raises a single `UnsafeURLError` for two very different situations
([url_guard.py](../thestill/utils/url_guard.py)):

- **DNS resolution failed** (`_resolve`: `raise UnsafeURLError("DNS lookup
  failed …")`) — environmental, retryable.
- **Resolution succeeded → forbidden destination** (private/loopback/metadata IP,
  invalid scheme, unsafe redirect target) — a **security-policy** refusal.

v1 wrongly bucketed "UnsafeURLError on a previously-valid host" under
`connectivity`, which would **retry an SSRF refusal** and could erode intended
operator attention. Fix at the source: split into `URLResolutionError`
(→ `connectivity`) and `UnsafeDestinationError` (→ `security_policy`, `fatal`, no
retry). Until the split lands, classify the base `UnsafeURLError` **conservatively
as `security_policy`** — never as connectivity.

---

## Transport Details That Bite

1. **Exhausted 5xx does not raise `HTTPError`.** The RSS session runs urllib3
   `Retry(total=2, status_forcelist=(500,502,503,504))` with no
   `raise_on_status=False` ([media_source.py:207](../thestill/core/media_source.py#L207)).
   On exhaustion, `requests` raises `RetryError`/`MaxRetryError`, which carries no
   final response — so "catch `HTTPError`, read `e.response.status_code`" **misses
   every retried 5xx**. Set `raise_on_status=False` (return the final response so
   we read its status cleanly) or explicitly unwrap the nested retry reason.
2. **`Retry-After` must propagate to the scheduler.** The adapter already honors
   it for its 2 in-request retries (`respect_retry_after_header=True`), but a feed
   that says "come back in an hour" should set `refresh_retry_after_at`, not be
   re-polled on the normal AIMD cadence.
3. **Test at the adapter layer, not just `session.get`.** A mocked `session.get`
   cannot reproduce `RetryError`; the 5xx-exhaustion test needs a real
   adapter-level fake server.

---

## Phased Implementation Plan

| Phase | Scope | Gate |
|---|---|---|
| **0** | Incident-scoped re-arm of the 90 parked feeds (see Rollout) **and** add the missing Postgres `clear_podcast_refresh_failures` so the recovery path works on the live DB. | Discovery resumes on the affected instance; PG contract test green. |
| **1** | Split `UnsafeURLError` (or conservative base-class handling); add `RefreshFailureKind`. Security-first, tiny. | SSRF refusal classifies `security_policy`, never retried. |
| **2** | `RefreshAttemptResult`/`RefreshFailure` replacing the 8-tuple; fetch + parse + feed-manager catch-all all populate `kind`/`http_status`/`retry_after`; parse failure emits `invalid_content`. | Classification unit tests per exception/status/parse; existing refresh tests green. |
| **3** | Handler maps kind → `error_class` per policy table; **connectivity → `infra`**. | Simulated `ConnectionError` reaches the queue as `infra`; #49 breaker opens. |
| **4** | Durable podcast policy columns + reason-tagged quarantine in `record_refresh_error`; connectivity/transient never NULL `next_refresh_at`. | `ConnectionError` leaves feed scheduled; `410`/`security_policy` quarantine with reason. |
| **5** | Time-horizon gate for `404`/`invalid_content`; `Retry-After` → `refresh_retry_after_at`; low-frequency probe for quarantined feeds. | 404 parks only after horizon; quarantined feed still re-probed. |
| **6** | Observability: parked-by-reason counter in `thestill status`/web; `refresh_park_suppressed` / `refresh_infra_attributed` events. | A mass environmental failure is visible, not silent. |

Phases 0–4 fix and future-proof the incident. 5–6 finish the durability and close
the "silent" half.

---

## Testing

Per [#04 testing](04-testing.md) and #42's **consistent-mock** warning (a mock
that always succeeds hides exactly this bug — tests must inject *typed failures*):

- **Classification units:** each of `ConnectionError`, `ConnectTimeout`,
  DNS-`UnsafeURLError`, destination-`UnsafeURLError`, `HTTPError(401)`,
  `HTTPError(404)`, `HTTPError(410)`, `HTTPError(503)`, `RetryError` (exhausted
  5xx), read-timeout, `bozo` parse, and a raised `KeyError` (internal) → asserts
  `RefreshFailureKind`, preserved `http_status`, and `error_class`.
- **`UnsafeDestinationError` is never `connectivity`** and never retried.
- **Adapter-level exhausted-503** preserves status (real fake server, not mocked
  `session.get`).
- **Full path**, fetch → handler → worker → **real repository**: a connectivity
  failure opens the #49 breaker, is attributed `infra`, and **leaves
  `next_refresh_at` non-NULL** (feed never parked).
- **404 vs 410 over elapsed time:** 410 quarantines immediately; 404 only after
  the configured horizon of online failures — *not* a rapid retry burst.
- **PostgreSQL contract tests** for `record_refresh_error` (per-kind) and the new
  bulk `clear_podcast_refresh_failures`.
- **Restart recovery with overdue, non-parked feeds** — replaces v1's wake-gap
  test: after a simulated offline window, feeds remain due and self-refresh on the
  next tick with no operator action.
- **Incident regression:** 90 feeds, all `ConnectionError` for a 24h simulated
  offline window → **zero** parked, discovery self-resumes when fetch succeeds.

---

## Rollout & Recovery

**Immediate unstick (Phase 0) — incident-scoped, not a general migration.** The
90 rows are known to be environmental (the whole fleet failed in one window). Re-arm
them explicitly, with a backup and an affected-ID review first:

```sql
-- 1. snapshot the affected rows first (backup / audit trail)
CREATE TABLE podcasts_park_backup_20260722 AS
SELECT id, title, next_refresh_at, last_refresh_error
FROM podcasts
WHERE next_refresh_at IS NULL AND last_refresh_error IS NOT NULL AND synthetic IS NOT TRUE;

-- 2. review, then re-arm
UPDATE podcasts
SET last_refresh_error = NULL, next_refresh_at = now()
WHERE id IN (SELECT id FROM podcasts_park_backup_20260722);
```

> This re-arms **every** parked feed, including any genuinely dead one. That is
> acceptable *for this known incident* (all 90 parked in a single offline window),
> but it is **not** a safe general operation — hence the snapshot + review. Once
> Part 3/4 land, dead feeds carry a `feed_gone`/`blocked_unsafe` reason and this
> blunt re-arm is replaced by "re-arm where `refresh_disabled_reason` is
> environmental."

**Do not use "DLQ → Retry all" as the recovery path here.** It retries *all*
terminal queue work, not just refresh feeds ([api_commands.py:1725](../thestill/web/routes/api_commands.py#L1725)),
so it would restart unrelated failed episode work — and it calls the bulk
`clear_podcast_refresh_failures`, which **has no PostgreSQL implementation** (only
[sqlite_podcast_repository.py:3888](../thestill/repositories/sqlite_podcast_repository.py#L3888)).
On the affected Postgres instance that endpoint would fail. Phase 0 adds the
missing PG method before any UI recovery is advertised.

**Ships dark → on.** Parts 1–4 change failure attribution + feed policy only and
are safe to enable immediately behind no flag. The quarantine-probe cadence
(Phase 5) is config-driven.

---

## Open Questions

1. **Explicit `error_class` vs message matching.** Passing `error_class` through
   the failure struct is more robust than extending `is_infrastructure_error`'s
   regex list, but touches the classifier contract shared with other stages.
   Prefer explicit; confirm no stage relies on re-classifying refresh messages.
2. **404 horizon.** How long online before a `404` quarantines? Proposal: reuse
   the AIMD max interval — quarantine only once the feed has backed off to
   `REFRESH_MAX_INTERVAL_SECONDS` and still 404s (i.e. "quietly dead", never
   "briefly missing during a deploy").
3. **Quarantine probe cadence.** Weekly vs monthly for `feed_gone`/`invalid_content`;
   `blocked_unsafe`/`auth_required` should probably **not** auto-probe (they need
   human action). Decide in Phase 5.
4. **Optional connectivity probe.** Worth adding later as a cheap tick-level
   short-circuit (skip fanning out doomed fetches while offline), but strictly an
   optimization on top of the #49 breaker — never load-bearing for recovery.

---

## Revision History

- **v2 (2026-07-22)** — Substantially revised after design review. Corrected the
  #49 relationship (its healer is infra-attribution-based, not episode-specific;
  the real seam is `item` mislabelling). **Removed** the monotonic wake-gap
  detector (non-portable across suspend; unnecessary once `next_refresh_at` stays
  set) and the bespoke connectivity probe (reuse #49's breaker). Split
  `UnsafeURLError` handling so an SSRF refusal is `security_policy`/`fatal`, never
  a retried `connectivity` failure. Replaced the coarse 3-way taxonomy with a
  7-kind classification **decoupled from** parking policy. Replaced the brittle
  8-tuple with `RefreshAttemptResult`/`RefreshFailure` covering parse, catch-all,
  and internal-error paths. Added durable `podcasts` policy columns (no string-
  encoded control flow). Documented the urllib3 `RetryError` / `raise_on_status`
  and `Retry-After` transport issues. Reframed rollout as incident-scoped (backup +
  review) and flagged the missing Postgres `clear_podcast_refresh_failures` that
  breaks the DLQ recovery path on the live DB.
- **v1 (2026-07-22)** — Initial draft: three-layer design (classify / connectivity
  gate / wake-gap re-arm). Superseded.
