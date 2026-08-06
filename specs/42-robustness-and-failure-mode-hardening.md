# Robustness & Failure-Mode Hardening

> **Status:** 🚧 In Progress — Phases 1, 2 & 4 implemented; Phase 3 (liveness/canary + DB schema) deferred to a follow-up
> **Created:** 2026-05-21
> **Updated:** 2026-07-02 — added FM-7 (unsanitized LLM output persisted as
> trusted data) from the Gemini control-byte incident; checklist extended.
> **Author:** Engineering (incident retro)
> **Related:** [#00 constitution](00-constitution.md) (principles #4, #5, #7), [#03 error-handling](03-error-handling.md), [#16 full-pipeline-and-failure-handling](16-full-pipeline-and-failure-handling.md), [#19 refresh-performance](19-refresh-performance.md), [#37 substack-import-resolver](37-substack-import-resolver.md) (introduced the triggering regression)

---

## Executive Summary

On 2026-05-21 we discovered that the 20VC feed had silently stopped
discovering new episodes (the Karpathy/Anthropic episode, published ~4h
earlier, never landed). Root cause was a single tz-naive-vs-aware datetime
comparison that raised `TypeError` mid-refresh — but the *system never
reported a failure*. The exception was swallowed by a broad
`except Exception: return []`, the empty result was indistinguishable from
"this feed has nothing new," and the HTTP ETag had already been advanced, so
every subsequent refresh got a `304 Not Modified` and skipped the feed
entirely. The bug was invisible, self-perpetuating, and had silently affected
**18+ podcasts** by the time it was noticed.

The code fix was three lines. The interesting part is *why a three-line bug
survived to production and stayed hidden for ~13 days*. This spec turns that
post-mortem into a small set of named failure modes, an enforcement plan, and
a review/recognition checklist so we (a) stop reintroducing these patterns and
(b) catch them during development instead of in prod.

**Mental model:** the bug wasn't the `TypeError`. The bug was that *a crash
got laundered into a normal-looking success*, and then a cache made the lie
permanent. Most of what follows generalizes exactly that.

**Key principle:** Robustness here comes from *removing foot-guns* (broad
excepts, naive datetimes, premature checkpoint writes) and *adding cheap loud
signals* — not from more code or more process. Loud, fast failure is what
keeps the system both reliable and agile.

---

## Table of Contents

1. [The Incident](#the-incident)
2. [Non-Goals](#non-goals)
3. [Failure-Mode Catalogue](#failure-mode-catalogue)
4. [Remediations & Implementation Phases](#remediations--implementation-phases)
5. [Enforcement & Recognition](#enforcement--recognition)
6. [Relationship to the Constitution](#relationship-to-the-constitution)
7. [Testing](#testing)
8. [Open Items](#open-items)
9. [Cross-References](#cross-references)

---

## The Incident

**Symptom:** `thestill refresh` reported success and "0 new episodes" for
20VC, while the live libsyn feed already contained the new episode.

**Chain of events:**

1. `RSSMediaSource._parse_date` returned a **tz-naive** `datetime`
   (`datetime(*date_tuple[:6])`).
2. `last_processed` is stored **tz-aware** (UTC) — it became aware after
   spec #37 work (commit `fbdcb29`, 2026-05-08) normalized it in
   [feed_manager.py](../thestill/core/feed_manager.py) (the `most_recent_date`
   block, ~L334-342).
3. In `RSSMediaSource.fetch_episodes`, the filter `episode_date > last_processed`
   raised `TypeError: can't compare offset-naive and offset-aware datetimes`
   on the *newest* entry.
4. The function's outer `except Exception: return []` swallowed it and returned
   an empty list — **identical to the "nothing new" result**.
5. But the HTTP ETag / `Last-Modified` were already advanced on the in-memory
   podcast *before* `fetch_episodes` ran
   ([feed_manager.py](../thestill/core/feed_manager.py) `_refresh_single_podcast`,
   ~L288-291), and the batch writer persists every 200-response podcast
   unconditionally (`changed_podcasts.append(podcast)`, ~L480).
6. From then on the stored ETag matched the live feed's ETag, so every refresh
   got `304 Not Modified` and **short-circuited before parsing** — the failure
   became permanent and self-hiding.

**Blast radius:** 36 podcasts carried a tz-aware `last_processed`; a full
refresh after the fix recovered **64 dropped episodes across 18 podcasts** with
0 errors.

**What made it dangerous, in one line each:**

- It threw, but *looked like success*.
- The cache *certified* the broken state, so it never retried.
- *Nothing watched* for "a publishing podcast went quiet."
- The tests *passed* because their mocks used internally-consistent (naive)
  datetimes that the real DB never produces.
- It was introduced as a *side effect* of an unrelated feature (#37).

The point-fix shipped already (`_parse_date` → tz-aware UTC, plus a coercion
guard in `fetch_episodes`, plus a parametrized regression test in
[tests/unit/models/test_media_source.py](../tests/unit/models/test_media_source.py)).
This spec addresses the *generators* of the bug, not the bug.

## Non-Goals

- Not a rewrite of the refresh pipeline (#19 owns refresh internals).
- Not "handle every possible error." The goal is to make failures **loud and
  attributable**, not to make every operation infallible.
- Not adding heavyweight process (sign-offs, gates) that slows iteration. Every
  remediation below must be cheap to adopt and hard to regress.

---

## Failure-Mode Catalogue

Each entry is a named, recognizable pattern. The names are deliberately memorable
so they can be cited in review ("this is FM-1") and recalled during development.

### FM-1 — Errors masquerading as empty / neutral results

> A caught exception returns a "neutral" value (`[]`, `None`, `{}`, `0`) that
> the caller cannot distinguish from a legitimate empty outcome.

- **Why it hides:** the failure path and the success-with-nothing path produce
  the same value. No error propagates, no metric increments, nothing looks wrong.
- **Rule:** an `except` that returns a neutral value is a place bugs hide.
  Either (a) re-raise / convert to a domain exception, or (b) return a value
  that *records* that a failure happened. Catch *expected* exceptions
  (network, malformed XML) narrowly; let programming errors (`TypeError`,
  `AttributeError`, `KeyError`) propagate so they surface as errors.
- **Cheap enforcement:** narrow the `except`; split "fetch/parse the feed"
  (whole-feed failure → mark podcast errored) from "process one entry"
  (per-entry failure → skip + count, don't abort the feed).
- **Heavier:** make the return type carry outcome, e.g.
  `DiscoveryResult(episodes, parse_ok, entries_skipped, error)` instead of a
  bare `List[Episode]`.
- **This codebase:** `RSSMediaSource.fetch_episodes` (the original `return []`);
  audit every `except Exception` that returns `[]/None/{}` in `core/` and
  `services/`. This is constitution **#7** — already a non-negotiable, violated
  here.

### FM-2 — Checkpoints / caches advanced before the work they certify is durable

> A "progress marker" (ETag, `Last-Modified`, `last_processed`, queue cursor,
> "last synced at") moves forward before the data it implies has been committed.

- **Why it hides:** the marker now *asserts* a state that isn't true. A cache
  hit (304), an incremental cursor, or a "skip, already done" check then trusts
  the false marker and never reprocesses — turning a one-time failure permanent.
- **Rule:** advancing a checkpoint is the **last** step, gated on the success of
  everything behind it. A 304 means "you already have everything in this
  version"; that must only be true after you durably stored everything.
- **Cheap enforcement:** gate the cache-header / `last_processed` persistence on
  `had_error == False` (the flag already exists per podcast). Don't put an
  errored podcast in `changed_podcasts`.
- **Heavier:** persist data and its checkpoint in one transaction, or write the
  checkpoint from a position derived from *what was actually committed*, not
  from what was *fetched*.
- **This codebase:** `feed_manager._refresh_single_podcast` advances
  `etag`/`last_modified` before `fetch_episodes`; the batch writer persists
  200-response podcasts unconditionally. Same shape exists wherever
  `last_processed`, download cursors, or "already processed" guards are written.

### FM-3 — Mixed datetime tz-awareness (no single normalization boundary)

> Naive and tz-aware `datetime`s coexist in the system; comparisons between
> them raise, and the awareness of a value depends on which code path produced it.

- **Why it hides:** the same field is naive from one ingestion path (feedparser)
  and aware from another (importer / repository). It only blows up at the
  comparison site, far from the source, and only for the data shapes that mix.
- **Rule:** normalize to **tz-aware UTC at the edge** (parse/ingest), so the
  entire interior speaks one dialect. Make a naive datetime impossible to *hold*,
  not just impossible to *compare*.
- **Cheap enforcement:** Pydantic validator on `Episode.pub_date` (and any
  `last_processed`-style field) that coerces/rejects naive → UTC; CI lint
  banning `datetime.now()` without `tz=` and `datetime.utcnow()` (there is still
  a live `datetime.utcnow()` in
  [queue_manager.py](../thestill/core/queue_manager.py), ~L603).
- **Heavier:** a single shared `now_utc()` / `to_utc()` helper used everywhere;
  ban direct `datetime(...)` construction in business code via lint.
- **This codebase:** `_parse_date` (fixed), `feed_manager` mixed-tz guard,
  `podcast_service` `cutoff_time` comparisons. This is constitution **#5**
  (Pydantic at the boundary) applied to time.

### FM-4 — Silent fleet degradation (no health signal for "went quiet")

> A unit of work stops producing output and nobody notices, because "produced
> nothing" is a normal state and there is no signal that distinguishes "nothing
> happened because nothing should" from "nothing happened because it's broken."

- **Why it hides:** logs contain the error (we logged it!) but nothing *consumes*
  the log. Humans don't watch logs at fleet scale; absence of output is not an
  alert by default.
- **Rule:** the system must tell you when it goes quiet. Track liveness signals,
  not just error logs, and surface aggregate error counts where a human or a
  canary will see them.
- **Cheap enforcement:** surface `podcasts_with_errors > 0` from the refresh
  batch summary into `thestill status` / the briefing / a non-zero exit signal
  (the number is already computed in
  [feed_manager.py](../thestill/core/feed_manager.py) `feed_refresh_batch_summary`).
- **Heavier:** track `last_new_episode_at` per podcast + expected cadence; flag
  an actively-publishing show that has gone N refreshes / D days silent (catches
  failures that *don't even throw*). A nightly canary asserting "fleet discovered
  ≥1 episode and error count == 0."
- **This codebase:** refresh, download, transcribe, and the briefing pipeline all
  have a "produced nothing" state worth a liveness check.

### FM-5 — Tests that pass because the mocks are internally consistent

> A test fabricates inputs that agree with each other but not with production,
> so a real-world mismatch is never exercised.

- **Why it hides:** the green suite creates false confidence. The 20VC bug's
  unit tests passed throughout the outage because they fed *naive*
  `last_processed` alongside *naive* parsed dates — the DB stores **aware**.
- **Rule:** tests must use production-shaped data at the seams, and at least one
  test must round-trip through the real adapter (repository, parser) rather than
  a hand-built mock.
- **Cheap enforcement:** use the real stored shape in fixtures (tz-aware
  `last_processed`); add a repository round-trip test (store → read → feed to
  consumer).
- **Heavier:** keep a few saved *real* feed XML fixtures (e.g. a real libsyn
  body) and run discovery against them; property test: "any entry newer than
  `last_processed`, regardless of tz, is discovered."
- **This codebase:** `tests/unit/models/test_media_source.py` (now parametrized
  over aware+naive `last_processed`); apply the same realism audit to other
  mock-heavy parse/persist tests.

### FM-6 — Drift between parallel code paths doing "the same" thing

> Two code paths compute or persist the same concept differently; a change to
> one silently breaks an assumption the other relies on.

- **Why it hides:** the change looks local and correct in isolation. The break
  is in the *other* path, often shipped by an unrelated feature.
- **Rule:** one concept, one canonical implementation. When two paths must
  produce the same kind of value (a normalized datetime, a slug, a checkpoint),
  route both through one helper.
- **Cheap enforcement:** extract the shared step; in review, flag "this is the
  second place that does X."
- **This codebase:** `insert_imported_episode` (aware) vs the feedparser path
  (naive) diverged on datetime awareness — the literal trigger for this incident
  (introduced by #37). Candidates: datetime normalization, `last_processed`
  advancement, enclosure extraction.

### FM-7 — Unsanitized LLM output persisted as trusted data

> Text generated by an LLM is written to storage verbatim, as if it obeyed the
> invariants of hand-written or parser-validated data. Models occasionally emit
> byte-level garbage — the 2026-07-02 incident: Gemini's clean stage replaced a
> non-ASCII character's UTF-8 bytes with control bytes (``Pok␀␀mon`` for
> *Pokémon*, one U+0000 per byte of ``é``; also U+0003/U+0004 for £/ä).

- **Why it hides:** the permissive store masks it. SQLite `TEXT` accepts
  embedded NUL silently, and the UI renders it as a near-invisible glyph — the
  corruption sat in `chunks.text` and `entity_mentions.quote_excerpt` across 8
  episodes until the Postgres migration (which *forbids* NUL in `text`) was the
  first thing strict enough to reject it. A schema-validated LLM response
  (Pydantic structured output) does **not** protect you: the payload was valid
  JSON with valid string fields — the garbage was *inside* the strings.
- **Rule:** LLM output is untrusted input. Sanitize it at the schema boundary
  where the response is parsed — not at each consumer — so every downstream
  artefact is clean by construction. Never strip silently: log a count (FM-4).
- **Cheap enforcement:** `utils.text_sanitizer.sanitize_text` (strips C0/C1
  control ranges, keeps tab/newline/CR, returns a removed-count for logging).
  Wired as a `field_validator` on `CleanupPatch.cleaned_text`/`sponsor`; any
  new LLM response model with free-text fields gets the same validator.
- **Heavier:** defense-in-depth at later boundaries for data that predates the
  validator: `ChunkWriter` re-sanitizes on write; the SQLite→Postgres migration
  tool strips NUL identically on COPY and in its parity hash so a stray char
  degrades to a 1-char loss instead of aborting a table.
- **This codebase:** fixed at the source in `segmented_transcript_cleaner`
  (PR #144); 43 DB rows + 16 artefact files backfilled via
  `scripts/backfill_control_chars.py`; verified against the live corpus
  (1.32M values — the sanitizer flags exactly the corrupted rows, nothing
  natural). Candidates for the same validator: any future LLM surface that
  persists free text (summaries, narration scripts, entity enrichment).

### FM-8 — Edge-triggered state sync (a poll that latches itself off)

> A consumer syncs by detecting a *transition* — "there was work in flight,
> now there isn't" — and acts only on that edge. It is correct only if it
> observes every transition. Timers throttle, components unmount, tabs sleep,
> and the producer's intermediate states are not atomic, so eventually it
> misses one and stops syncing entirely. Discovered 2026-08-03 in the episode
> reader (spec #68): the transcript and summary panes showed "not yet
> available" indefinitely after the pipeline had finished.

- **Why it hides:** the *first* stage usually works, so the mechanism looks
  sound in manual testing and in any test whose fixture keeps exactly one task
  active until the pipeline ends (FM-5 again — that fixture never produces the
  empty intermediate state the real producer does). The failure needs a real
  multi-stage run to appear, and then it looks like a caching bug rather than a
  sync bug.
- **The specific trap:** a self-referential poll predicate — *keep polling only
  if the last poll said busy* — is a latch that can only fall open. In spec #68,
  `TaskWorker` completes a task and only *then* enqueues its successor
  ([task_worker.py:671-704](../thestill/core/task_worker.py#L671-L704)); a poll
  landing in that non-atomic window saw an empty list, returned `false`, and
  never ran again for the rest of the pipeline.
- **Rule:** sync on a level the producer **re-asserts on every tick**, not on a
  transition the consumer must witness. Then a missed observation costs latency,
  never correctness. If you must stop polling, make every path back to activity
  restart it, and bound the stop with consecutive-quiet-tick counting rather
  than a single quiet observation.
- **Corollary:** when two views of the same entity refresh at different
  cadences, the slower one is not "cached", it is **wrong** — something must
  reconcile them. Freezing one (`refetchInterval: false`) is only safe if
  something else invalidates it.
- **This codebase:** fixed in the reader by `useEpisodeLiveRefresh`
  ([useApi.ts](../thestill/web/frontend/src/hooks/useApi.ts)), which diffs
  `(state, has_transcript, has_summary)` off the already-live 5 s episode poll.
  Known remaining instances, same shape, not yet converted: `useRefreshStatus`,
  `useAddPodcastStatus`, `usePipelineTaskStatus`.

---

### FM-9 — Constraints and indexes that depend on collation

> A `CHECK` or index whose truth depends on **string ordering** means
> different things on different machines. Two databases can report the same
> locale name and still disagree, because the ordering comes from the host's
> C library, not from Postgres. Discovered 2026-08-04 migrating to AWS
> (spec #66): `entity_cooccurrences` has `CHECK (entity_a_id < entity_b_id)`
> as a canonical-pair guard, and one row of 1,089,070 flipped ordering
> between macOS and Debian — `pg_restore` aborted the whole `COPY` and the
> table arrived **empty**.

- **The specific trap:** source and destination both declared `en_US.UTF-8`.
  macOS libc and Debian glibc disagree on punctuation: glibc ignores the
  hyphen at the primary level, so `'company:s-club-7' < 'company:saudi-arabia'`
  is **true** on macOS and **false** on Debian. A constraint that was
  satisfied at source was violated on arrival.
- **Why it hides:** it is invisible until a *cross-platform* restore, which
  happens rarely and usually under deadline. `pg_restore` reports it as one
  line among many and exits non-zero only if you let it — and the failure is
  per-`COPY`, so a single bad row discards the entire table rather than
  degrading proportionally. Row counts look fine for every *other* table,
  which makes the gap easy to miss.
- **Compounding (FM-1):** the restore script piped `pg_restore` through
  `| tail -20 || true`, so the non-zero exit was swallowed. The loss was
  caught only by comparing row counts against source afterwards — not by
  anything in the automation. An error path that ends in `|| true` is a
  decision to not know.
- **Rule:** prefer collation-independent invariants. Canonical ordering is
  better expressed with an explicit collation (`CHECK (entity_a_id COLLATE "C"
  < entity_b_id COLLATE "C")`) so the guarantee travels with the schema.
  Where the data is derivable, rebuilding on the destination is safer than
  transporting it: the rebuild uses the destination's own collation and is
  self-correcting.
- **Rule (migration):** always diff row counts source-vs-destination after a
  restore, per table. Never infer success from a clean-looking log.
- **This codebase:** recovered by re-deriving `entity_cooccurrences` from
  `entity_mentions` via `rebuild_cooccurrences`
  ([postgres_entity_repository.py](../thestill/repositories/postgres_entity_repository.py)),
  which produced 1,088,975 rows — 95 *fewer* than the source table, because
  the incremental per-episode rebuild had drifted stale against its own
  mentions. The destination is the more correct of the two.

---

### FM-10 — Config that defaults to a working-but-wrong value

> A setting absent from the environment falls back to a code default. The
> system starts, every stage succeeds, and the output is structurally valid
> and quietly wrong. Nothing raises, nothing logs, no alert fires — the only
> signal is a human eventually noticing the *content* is off. Discovered
> 2026-08-05, one day after the AWS cutover (spec #66): every transcript
> produced on the new host had `speaker: null` on every segment.

- **The specific trap:** `ENABLE_DIARIZATION` was not migrated to SSM.
  `config.py` reads it as `os.getenv("ENABLE_DIARIZATION", "false")`, so the
  deployment stopped asking Dalston for diarization. Dalston obediently
  returned plain transcription. Facts extraction and the speaker-mapping
  code were both working perfectly — there was simply nothing to map onto.
  Seven episodes were affected before it was caught by eye.
- **Why the migration missed it:** the variables were **hand-picked** into a
  carry-over list rather than diffed. That list omitted 28 of them.
  `ENABLE_DIARIZATION` was merely the first whose absence was visible;
  `DELETE_AUDIO_AFTER_PROCESSING`, `NARRATION_ENABLED` and several model
  pins (`GEMINI_MODEL`, `DALSTON_MODEL`) were also missing and would have
  changed behaviour and cost silently.
- **Why it hides:** a missing *credential* fails loudly on first use, so we
  reason about config as if all of it behaves that way. A missing *boolean*
  does the opposite: `false` is a legal value, the code path it selects is a
  legal path, and the result is a well-formed artifact. The blast radius is
  bounded only by how long it takes someone to look closely.
- **Rule:** treat config drift as a **check**, not a judgement call. Diff
  what the code reads against what the environment provides, and fail the
  deploy on a gap. `thestill-aws secrets diff` does this: it reports what
  SSM lacks, what differs, and what is deployment-only; exits non-zero when
  something is missing; and never prints a value. Variables that are
  *supposed* to differ are excluded by name so the section that matters
  stays readable.
- **Corollary:** a default is a policy decision, not a convenience. Where
  the wrong default produces silent degradation rather than a crash, prefer
  no default — fail at config load with the remedy, the way
  `DATABASE_URL`-without-psycopg now does.
- **Corollary (migration):** never copy an env file wholesale either.
  `DEBUG_CLIP_DURATION=300` was live in the source `.env`; copying it would
  have truncated every production transcript to five minutes, turning a
  config gap into data loss. The diff's ignore-list is the record of which
  variables are deliberately local-only.

---

## Remediations & Implementation Phases

Ordered by robustness-per-effort. Phase 1 is the high-leverage, low-cost set —
do it first; it would have prevented *and* surfaced this class of bug.

### Phase 1 — Highest leverage, do first (the "top 3") ✅ done

| # | Change | Failure mode | Status |
|---|---|---|---|
| 1 | Gate ETag / `Last-Modified` / `last_processed` persistence on `had_error == False` in `feed_manager` (don't certify state on a failed refresh). | FM-2 | ✅ `_record_outcome` returns early on `had_error`, excluding the podcast from `changed_podcasts`. |
| 2 | Narrow `RSSMediaSource.fetch_episodes`' `except`: expected errors (network/parse) → log + mark errored; programming errors propagate; per-entry errors skip+count, never abort the feed. | FM-1 | ✅ broad `except → []` removed; per-entry `(ValidationError, ValueError)` skip+count; programming errors propagate to the worker's `had_error`. |
| 3 | Pydantic validator coercing `Episode.pub_date` / `last_processed` to tz-aware UTC at the model boundary; the `fetch_episodes` coercion guard becomes belt-and-suspenders. | FM-3 | ✅ `Episode.ensure_pub_date_aware` (pre-existing) + new `Podcast.ensure_last_processed_aware`, both via `ensure_utc`. |

### Phase 2 — Loud signals ✅ done (with one carve-out)

- ✅ Surface `podcasts_with_errors` from refresh: `refresh_feeds` returns a
  `RefreshOutcome`, `RefreshResult.podcasts_with_errors` threads it to the CLI,
  `thestill refresh` prints it **and exits non-zero**, and the `digest` briefing
  prints it. (FM-4)
  - **Carve-out:** surfacing the count in `thestill status` needs a *persisted*
    per-podcast error field (status reads the DB, it doesn't refresh). That
    requires a schema change, so it moves to Phase 3 alongside the liveness
    signal.
- ✅ CI lint rule: ruff `DTZ001/003/004/005/006` ban tz-naive datetime
  construction (`pyproject.toml`), gated in CI (`uv run ruff check thestill/`);
  all ~30 live offenders (queue_manager, task_manager, feed_manager,
  import/youtube date parsing, etc.) fixed. (FM-3)

### Phase 3 — Liveness & realism ⏳ deferred (follow-up PR)

- Per-podcast `last_new_episode_at` + a "gone quiet" flag for
  actively-publishing shows; optional nightly canary. (FM-4) — *needs a DB
  schema change; deferred.* Also carries the `thestill status` error-count line.
- ✅ (partial) Repository round-trip tests for discovery landed now
  (`test_last_processed_round_trips_tz_aware`,
  `test_legacy_naive_last_processed_normalised_on_load`). Real-feed-fixture
  tests + a wider FM-5 audit of mock-heavy suites remain. (FM-5)

### Phase 4 — De-duplicate paths ✅ done

- ✅ Single canonical datetime helper `thestill/utils/datetime_utils.py`
  (`now_utc`, `ensure_utc`, `parse_struct_time_utc`); the feed manager's naive
  duplicate `_parse_date` now delegates to it, and the importer / feedparser
  paths share the same normalization. (FM-6)

---

## Enforcement & Recognition

The catalogue is only useful if it's applied. Two layers:

1. **Prevention (don't reintroduce):** lint rules (Phase 2) make FM-3 a build
   error. The review checklist below makes FM-1/FM-2 a review reflex. Phase 1
   removes the live instances.
2. **Recognition (catch during dev, before prod):** the named failure modes are
   recorded in the assistant's persistent memory so they're recalled when
   working on error handling, caching/checkpoints, datetimes, or discovery — and
   surfaced in review when the pattern appears.

**Review checklist (paste into PR template / use in review):**

- [ ] FM-1: Does any `except` return `[]/None/{}/0`? Can the caller tell failure
      from a legitimate empty result?
- [ ] FM-2: Does this advance a checkpoint/cache/cursor (ETag, `last_processed`,
      "already done")? Is it gated on the guarded work having committed?
- [ ] FM-3: Any `datetime` crossing a boundary? Is it tz-aware UTC? Any
      `datetime.now()`/`utcnow()` without tz?
- [ ] FM-4: Does "produced nothing" have a way to be distinguished from "broke
      silently"? Is the error count surfaced anywhere a human/canary sees it?
- [ ] FM-5: Do the tests use production-shaped data at the seam? Is there a
      round-trip through the real adapter?
- [ ] FM-6: Is this the second place that computes/persists this concept?
- [ ] FM-7: Does LLM-generated text reach storage? Is it routed through
      `sanitize_text` (or an equivalent validator) at the response schema,
      with stripped counts logged?
- [ ] FM-8: Does this act on a *transition* rather than a re-asserted level?
      Can the poll/watch that feeds it switch itself off based on its own last
      result? If two views of the same entity refresh at different cadences,
      what reconciles the slower one?

---

## Relationship to the Constitution

This spec **enforces** existing non-negotiables that were violated, and proposes
two clarifying amendments:

- **#7 (No silent failures):** FM-1 is a direct violation. The constitution bans
  `except Exception: pass`; it should also call out the subtler
  `except Exception: return <neutral>` form.
- **#5 (Pydantic at every external boundary):** FM-3 is this principle applied to
  time. Proposed clarification: "boundary values include timestamps; normalize to
  tz-aware UTC at ingest."
- **#4 (Repositories own the database / timestamp format):** FM-2 and FM-3 are
  adjacent — checkpoints and timestamps are repository-owned state.
- **Proposed new principle (FM-2):** "Checkpoints are downstream of durability —
  never advance a progress marker before the work it certifies is committed."

Amending [00-constitution.md](00-constitution.md) is out of scope for this draft
(constitution edits require their own PR with rationale) and is tracked in Open
Items.

## Testing

- Phase 1.3 validator: unit tests asserting naive input → aware UTC stored;
  round-trip through the repository.
- Phase 1.2: a test that a programming error inside `fetch_episodes` does **not**
  return `[]` silently (it sets `had_error` / propagates).
- Phase 1.1: a test that a refresh which errored does **not** persist an advanced
  ETag (so the next refresh retries instead of 304-skipping).
- Regression already in place:
  `test_fetch_episodes_mixed_tz_last_processed[aware|naive]` in
  [tests/unit/models/test_media_source.py](../tests/unit/models/test_media_source.py).

## Open Items

- [ ] Decide whether to amend [00-constitution.md](00-constitution.md) with the
      FM-2 principle and the #5/#7 clarifications (separate PR).
- [x] **Lint mechanism (decided):** ruff `DTZ` rules
      (`DTZ001/003/004/005/006`) over a grep-based check — it understands
      aliased imports and rides the existing `make lint` / CI `ruff` invocation.
      `DTZ007` (strptime `%z`) was left off to avoid `# noqa` churn on
      fixed-format `%Y%m%d` parses; those two sites were fixed by hand.
- [ ] **FM-4 liveness scope (partially decided):** the *minimal* signal shipped
      (refresh error count surfaced + non-zero exit). The `thestill status`
      line and the *full* signal (`last_new_episode_at` + cadence + canary) need
      a persisted per-podcast error/health field and are deferred to Phase 3.
- [x] **FM-1/FM-2 audit (this PR):** the two live instances named in the
      catalogue were the only ones fixed under Phase 1 —
      `RSSMediaSource.fetch_episodes`' broad `except → []` (FM-1) and
      `feed_manager._record_outcome`'s unconditional `changed_podcasts.append`
      (FM-2). A broader sweep of every `except → neutral` in `core/`/`services/`
      remains a standing follow-up (tracked, not blocking).

## Cross-References

- [00-constitution.md](00-constitution.md) — principles #4, #5, #7
- [03-error-handling.md](03-error-handling.md) — exception hierarchy, fail-fast,
  classification (`TransientError`/`FatalError`, `error_classifier.py`)
- [16-full-pipeline-and-failure-handling.md](16-full-pipeline-and-failure-handling.md)
  — transient/fatal split + DLQ
- [19-refresh-performance.md](19-refresh-performance.md) — refresh internals,
  conditional GET (where FM-2 lives)
- [37-substack-import-resolver.md](37-substack-import-resolver.md) — the feature
  whose `last_processed` normalization introduced the FM-3/FM-6 trigger
- Code: [media_source.py](../thestill/core/media_source.py),
  [feed_manager.py](../thestill/core/feed_manager.py),
  [queue_manager.py](../thestill/core/queue_manager.py)
