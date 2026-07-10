# Related Episodes — Constant-Time Updates (Tier 4)

> **Status:** 📝 Draft
> **Created:** 2026-07-10
> **Author:** Product & Engineering
> **Related:** [#46 related-episodes-scaling](46-related-episodes-scaling.md) (Tiers 0–3; this spec is Tier 4 and replaces its incremental path), [#28 corpus-search-and-entities](28-corpus-search-and-entities.md) (owns the rail UX), [#44 postgres-migration](44-postgres-migration.md), [#42 robustness](42-robustness-and-failure-mode-hardening.md) (FM-6 parallel-path drift governs the dual-backend builders)

---

## Executive Summary

`compute-related` is the slowest stage in the pipeline: **~19 minutes average,
35 minutes worst case** over the last week — slower than transcribing the
audio. The 2026-07-10 production log shows why: `seed=1, affected=1344`. One
newly summarized episode triggers a rail recompute for **92% of the 1,455-episode
corpus**, because the incremental path sizes its candidate pools with
`k = min(n_corpus, candidate_cap)` and the cap (2,000) hasn't been reached —
so "incremental" degenerates into a full O(n²) rebuild on every summarize
batch. Growth makes it strictly worse: cost rises quadratically until n=2,000,
then plateaus at a level that is still ~2,000 rail recomputes per batch.

This spec re-architects the update path around one invariant:

> **Per-episode work depends only on a fixed pool size k — never on corpus
> size n.**

With the four changes below, adding an episode costs tens of milliseconds at
today's corpus and stays there at 1,000× (1.5M episodes / 300M chunks). The
full rebuild survives only as a rare, explicitly-invoked maintenance action.

| Per new episode | Today (n=1,455) | Tier 4 (any n) |
|---|---|---|
| Forward pool | k-NN with k=n (full scan) | k-NN with k=150 |
| Reverse rails | ~1,350 full pool recomputes | ~150 O(1) merge checks |
| Feature access | `string_agg` + TF-IDF transform per candidate | precomputed row fetches |
| Lexical leg | `ts_rank_cd` over all 309k chunk rows | bounded lookup over per-episode features |
| Total | ~19 min, O(n²) | tens of ms, O(k) |

---

## Table of Contents

1. [Motivation](#motivation)
2. [Design Invariant](#design-invariant)
3. [Change 1 — Pool-independent score calibration](#change-1--pool-independent-score-calibration)
4. [Change 2 — Fixed-k recall pools](#change-2--fixed-k-recall-pools)
5. [Change 3 — Reverse rails as merge, not recompute](#change-3--reverse-rails-as-merge-not-recompute)
6. [Change 4 — Precomputed per-episode features](#change-4--precomputed-per-episode-features)
7. [IDF Lifecycle](#idf-lifecycle)
8. [Freshness Without Full Rebuilds](#freshness-without-full-rebuilds)
9. [Alternative Considered: Query-Time Rails](#alternative-considered-query-time-rails)
10. [Migration & Rollout](#migration--rollout)
11. [Implementation Phases](#implementation-phases)
12. [Testing](#testing)
13. [Open Questions](#open-questions)
14. [Non-Goals](#non-goals)

---

## Motivation

Measured on production (week of 2026-07-04 → 07-10, Postgres backend):

- `compute-related`: 51 runs, **avg 1,161 s, max 2,107 s** — the top stage by
  a 2.4× margin over `transcribe` (481 s avg), for a *derived cache*.
- Live event: `related_incremental_complete seed=1 affected=1344 pairs=6717`.
- Corpus: 1,455 episodes with centroids, 308,962 chunks, 596,935 entity
  mentions.

The root cause is by-design behavior from #46 Tier 3 that has aged out: the
incremental update reuses the *full-build* candidate cap
(`DEFAULT_CANDIDATE_CAP = 2000`) as its per-seed pool size. Below the cap the
seed's pool ≈ the whole corpus, `affected = seed ∪ pool` ≈ everything, and
each affected episode then re-runs its own full-corpus candidate query
(pgvector k-NN with `LIMIT n`, `ts_rank_cd` aggregation over every chunk row)
plus a rerank whose features (`string_agg` of the full transcript + TF-IDF
transform) are computed on demand per candidate.

The stage is non-user-failing and coalesced, and since the 2026-07-10 wait-set
fix (spec #55) it can no longer delay briefings — but it monopolizes a worker
for ~20 minutes per summarize batch, contends on the DB, and grows
quadratically.

---

## Design Invariant

Every step of the update path must be **O(k)** with k a fixed constant
(default 150), regardless of corpus size:

1. Finding candidates: one ANN query with `LIMIT k`, one bounded lexical
   lookup. Never `LIMIT n`.
2. Scoring a pair: O(1) — all features precomputed and fetched by key.
3. Updating other episodes' rails: O(k) single-pair comparisons against
   stored scores. Never a second candidate query.
4. Anything that inherently needs O(n) (IDF refit, corpus-wide freshness) is
   moved off the episode-add path into scheduled, budgeted maintenance.

Phase 1 is an explicitly temporary exception: it removes dependence on n but
does O(k²) pair evaluations until calibrated scores make the O(k) merge safe
in Phase 2. The Tier 4 invariant is achieved at Phase 2 cutover.

---

## Change 1 — Pool-independent score calibration

**The prerequisite for everything else.** Today's blend
([related_builder.py](../thestill/search/related_builder.py), shared by both
backends per FM-6):

```
score = 0.55·minmax(tfidf_cos) + 0.30·minmax(centroid_cos) + 0.15·minmax(entity_jaccard)
```

`_minmax` normalizes each signal **across the current candidate pool**, which
means a stored score is only meaningful relative to the pool it was computed
in. Two consequences: stored rails can't be compared against a new pairwise
score (blocks the merge in Change 3), and the same pair can score differently
depending on which batch computed it.

Replace per-pool min-max with a **fixed calibration**:

```
score = w_t·tfidf_cos + w_v·squash(centroid_cos) + w_e·entity_jaccard
```

- `tfidf_cos` and `entity_jaccard` are already absolute in [0, 1] — use raw.
- `centroid_cos` over L2-normalized MiniLM centroids empirically occupies a
  compressed band (~[0.2, 0.9]); `squash` is a fixed affine clamp
  (`(x - lo) / (hi - lo)` clipped to [0, 1]) with `lo`/`hi` chosen **once**
  from the corpus-wide similarity distribution and pinned as constants —
  not recomputed per pool.
- The TF-IDF gate (`tfidf_cos ≥ 0.12`) already uses the raw value and stays
  unchanged.
- Weights re-tuned once against the current rails (see Testing: rank-parity
  harness) so the new blend reproduces today's orderings as closely as
  possible; deviations are reviewed by eye on a sample before cutover.

Scores become globally comparable: comparable across pools, across batches,
and against stored `episode_related.score` values. One number, one meaning.

---

## Change 2 — Fixed-k recall pools

Introduce `RELATED_INCREMENTAL_POOL_K` (default **150**, env-tunable),
decoupled from `DEFAULT_CANDIDATE_CAP` (which remains the *full rebuild's*
exactness knob):

- Forward pool for a seed = top-150 by pgvector HNSW ∪ top-150 lexical,
  self excluded. The rail keeps 5; a 150-candidate rerank pool is ~30× oversampled
  — recall loss is negligible and bounded, not corpus-dependent.
- The k-NN query goes from `LIMIT 1456` (an exhaustive scan through the HNSW
  graph) to `LIMIT 151` — the regime HNSW is actually built for.

---

## Change 3 — Reverse rails as merge, not recompute

Today, every member of the seed's pool gets its **entire rail recomputed**
(own candidate query + own rerank). Tier 4 never recomputes a neighbor's
pool. For each neighbor X of the new episode E:

1. Compute `score(X, E)` — O(1) with precomputed features (Change 4).
2. If it fails the TF-IDF gate → skip.
3. Compare against X's stored rail (≤ 5 rows, fetched in one query for all
   150 neighbors):
   - rail not full → insert;
   - `score(X, E)` > X's worst kept score → insert E, evict rank 5;
   - otherwise → done, X untouched.

Properties:

- ~150 single-pair scores + at most 150 tiny writes per new episode —
  **independent of n**. Valid *only* because Change 1 made stored scores
  comparable.
- Approximation: E can only enter X's rail if X is in E's neighbor pool. The
  blend's signals are symmetric (cosines, Jaccard), so if E deserves a slot in
  X's top-5, X is overwhelmingly likely inside E's top-150. The miss rate is
  measured by the parity harness (Testing) and backstopped by the freshness
  sweep.
- Coalesced batches (m seeds) do m independent merge passes over the union of
  neighbors — still O(m·k).

`_rerank_incremental` and the full-pool reverse expansion in
`update_related_for_episodes` (both backends) are deleted once this lands.

---

## Change 4 — Precomputed per-episode features

Rail-time work must never touch the `chunks` table (309k rows today, 300M at
1,000×). Materialize every scorer input at ingest, keyed by episode:

| Feature | Storage | Written by | Exists today? |
|---|---|---|---|
| Dense centroid | `episode_vectors.centroid` | ChunkWriter at chunk-write | ✅ (#46 Tier 0) |
| Sparse TF-IDF vector | `episode_features.tfidf_terms` — top ~50 `(term_id, weight)` pairs, L2-renormalized after truncation and model-versioned | REINDEX handler, after chunks commit | ❌ new |
| Entity id set | `episode_features.entity_ids` (int/uuid array) | RESOLVE_ENTITIES handler | ❌ new (currently scanned from `entity_mentions` per run) |

- After selecting the top-50 terms, their retained weights are L2-normalized
  again. `tfidf_cos` is then the dot product of two unit-length sparse vectors
  from the **same model version**. It approximates the full cosine for
  episode-length documents (validated in the parity harness) and replaces
  today's on-demand `string_agg` + transform, the largest constant cost in the
  current path. `term_id` is scoped by model version: the physical key is
  `(model_version, term_id)`, so IDs are never interpreted across vocabularies.
- Three new tables (`episode_features`, `related_term_postings`, and
  `episode_rail_state`), both backends, in additive migrations.
  `episode_features` is UNIQUE on `(episode_id, model_version)`. Backfill uses
  a one-shot job that walks episodes at a bounded rate.

### Lexical candidates without the chunks table

Replace the `ts_rank_cd`-over-all-chunks recall leg with **bounded per-term
postings**. A new `related_term_postings(model_version, term_id, episode_id,
weight)` table keeps only the `RELATED_TERM_POSTINGS_CAP` highest-weight
episodes for each term (default 500), maintained when episode features are
written. Maintenance is a bounded merge against the stored list: insert into
an under-full list, or replace its minimum only when the new weight is higher.
Episode removal deletes its postings but does not scan the corpus to refill a
vacancy; scheduled postings rebuilds restore exact top-cap membership. For the
seed's top 25 terms, fetch at most 25 × 500 postings, retain
episodes sharing at least two terms, rank them by idf-weighted overlap, and
take `LIMIT k`. The cap makes work independent of corpus size; changing it is
an explicit quality/cost tradeoff and triggers a postings rebuild. Indexes on
`(model_version, term_id, weight DESC)` and `(episode_id, model_version)` serve
the lookup and replacement paths. SQLite uses the same ordinary junction
table and caps, avoiding backend-specific FTS semantics. The chunk-level
`text_tsv` column remains for *search* (spec #28) — only the rail stops using
it.

---

## IDF Lifecycle

The IDF model (today: `related_idf`, refit only on full rebuild) becomes
versioned and sampled:

- **Refit trigger:** scheduled (monthly) or when corpus doubles since last
  fit — never on the episode-add path.
- **Sampling:** fit on a uniform random sample capped at ~100k episodes; IDF
  over a large sample is statistically indistinguishable from the full corpus
  for ranking purposes.
- **Versioning:** `episode_features.model_version` marks which fit produced a
  row, and `episode_rail_state.scoring_version` records the calibration + IDF
  version used by the stored rail. Merge comparisons are allowed only when
  both feature rows and the target rail use the active scoring version.
  Otherwise the target gets a bounded forward refresh under the active
  version before the pair is compared; if either feature row is unavailable,
  the merge is skipped without deleting existing data. A refit therefore
  causes lazy, budgeted migration without ever comparing scores from different
  meanings.

---

## Freshness Without Full Rebuilds

Full rebuilds stop being routine. Two mechanisms replace them:

1. **Merge-on-add** (Change 3) keeps rails current for the dominant event —
   new episodes.
2. **Budgeted staleness sweep:** a scheduled batch (modeled on the
   `enrich-entities` cadence) re-runs the *forward* computation for the N
   episodes with the oldest state in a new one-row-per-episode table,
   `episode_rail_state(episode_id PRIMARY KEY, scoring_version,
   rail_refreshed_at)`, default N=200/day, env-tunable. State exists even for
   an empty rail and is updated transactionally with its `episode_related`
   rows. This heals merge-approximation misses and post-refit drift at
   a fixed, corpus-independent cost. At 1.5M episodes a full pass takes ~20
   years at default budget — which is fine, because merge-on-add handles
   everything except pathological drift; the budget is a knob, not a promise
   of full-corpus recency.

The exact full rebuild (`build_related_episodes`) remains available behind an
explicit CLI/admin action for disaster recovery and the calibration cutover.

---

## Alternative Considered: Query-Time Rails

With fixed-k ANN + precomputed features, computing a rail at request time
costs ~20 ms (one k-NN + 150 cheap pair scores) — no materialized table, no
reverse-rail problem, no staleness sweep; freshness for free via cache TTL.

Not chosen for v1 because: (a) the rail renders on every episode page and the
briefing/inbox surfaces batch-read it — read amplification is real; (b) the
materialized path is already built and its artifacts (table, API) are consumed
by #28's UI; (c) Tier 4 makes the write side so cheap that the maintenance
argument mostly evaporates. Revisit if the staleness sweep or merge complexity
proves annoying in practice — the feature store built here is exactly what the
query-time design needs, so nothing is wasted (recorded as an open question).

---

## Migration & Rollout

1. Ship schema (`episode_features`, `related_term_postings`,
   `episode_rail_state`) + backfill job.
   Additive; old path keeps running.
2. Land the new blend behind `RELATED_SCORING=calibrated|legacy` (default
   `legacy`), with the parity harness comparing both on the live corpus.
3. Cutover: one final full rebuild with `calibrated` (one ~19-min run — the
   last of its kind), flip the default, enable merge-on-add.
4. Delete the legacy incremental path (both backends, FM-6: same commit).
5. Rollback at any step: rails are a derived cache — `RELATED_SCORING=legacy`
   plus one full rebuild restores the status quo exactly.

---

## Implementation Phases

### Phase 1 — Stop the bleeding (no schema change)

- [ ] `RELATED_INCREMENTAL_POOL_K=150` for seed pools in
      `update_related_for_episodes` (both backends).
- [ ] Bound reverse expansion to the seed's pool *without* per-member candidate
      queries: rescore member rails only against `pool ∪ existing rail
      members` (interim, pool-relative scoring kept). This phase performs
      O(k²) pair evaluations per seed, though it remains independent of n;
      Phase 2 reduces it to O(k).
- [ ] Perf assertion in CI-adjacent test: incremental update on a 1k-episode
      synthetic corpus completes in seconds, touching O(k) episodes and no
      more than O(k²) pairs in this interim phase.
- Expected effect: ~19 min → well under a minute at today's corpus, without
  waiting for calibration. (Interim approximation: pool-relative min-max over
  a 150-pool instead of 1,455-pool — rank drift is small and measured.)

### Phase 2 — Calibrated scoring + true merge

- [ ] Fixed calibration constants (`squash` lo/hi from corpus distribution),
      weights re-tuned via parity harness; `RELATED_SCORING` flag.
- [ ] Reverse-rail merge (Change 3); delete pool-recompute reverse path.
- [ ] Cutover rebuild + flag flip + legacy deletion (both backends).

### Phase 3 — Feature store

- [ ] `episode_features`, `related_term_postings`, and `episode_rail_state`
      tables + writes in REINDEX / RESOLVE_ENTITIES handlers + backfill.
- [ ] Scorer reads features; chunk-table access removed from the rail path.
- [ ] Lexical leg via capped per-term postings; `ts_rank_cd` leg removed from
      rail.

### Phase 4 — Maintenance loop

- [ ] Versioned, sampled IDF refit (scheduled).
- [ ] Budgeted staleness sweep + `rail_refreshed_at`.
- [ ] Ops doc: knobs (`POOL_K`, sweep budget, refit cadence) and expected
      costs at 10×/100×/1000× corpus.

---

## Testing

- **Rank-parity harness (the workhorse):** for a frozen sample of episodes,
  compare rails from (a) legacy full rebuild, (b) calibrated full rebuild,
  (c) calibrated merge-on-add replaying the corpus episode-by-episode.
  Metrics: top-5 Jaccard overlap and rank correlation; gates: (b) vs (a)
  ≥ 0.8 mean overlap (calibration faithfulness), (c) vs (b) ≥ 0.95
  (merge correctness). Runs against both backends (FM-6).
- **Truncated-TF-IDF fidelity:** top-50 sparse cosine vs full cosine on real
  transcripts; assert error stays below the gate/ranking noise floor.
- **Complexity guard:** synthetic-corpus test asserting episodes touched,
  queries issued, postings inspected, and pair scores evaluated stay within
  their phase-specific bounds (Phase 1 O(k²), Phase 2+ O(k)) and never scale
  with n. Counters, not wall-clock limits, keep CI noise from making it flaky.
- **Merge edge cases:** rail not full; tie scores; seed already present in
  rail (re-run idempotency); neighbor without features (skip, never delete —
  same never-delete-what-you-can't-recompute rule the current `_write_pairs_scoped`
  follows).
- Existing `test_pg_related_builder.py` / SQLite builder tests keep passing
  through Phase 1–2 flags.

---

## Open Questions

1. **Calibration constants governance.** `squash` lo/hi and re-tuned weights
   are pinned corpus-derived constants; when (if ever) do they get revisited,
   and does changing them force a full rebuild? Proposal: treat like the
   embedding model choice — a versioned config constant, rebuild on change.
2. **Merge miss-rate budget.** What top-5 overlap deviation vs the exact
   build is acceptable before the sweep budget must rise? Proposal: alert if
   the parity harness (run monthly on a sample) drops below 0.9.
3. **Query-time flip criteria.** If page-view rail reads × cache-miss cost
   drops below episode-add write cost (unlikely but measurable once the
   feature store exists), flip per the Alternative section.
4. **Lexical postings cap governance.** Measure recall and worst-case postings
   inspected at the default cap of 500. Changing the cap requires a postings
   rebuild and the same value in both backends.

---

## Non-Goals

- No change to the rail UX, API shape, or `episode_related` consumers (#28).
- No change to chunk-level search (`text_tsv` / FTS5 stay for spec #28
  search).
- No new ANN infrastructure — pgvector HNSW is comfortable to ~50M vectors;
  swapping the ANN store is a future concern this design is deliberately
  agnostic to.
- No real-time freshness guarantee for *old* rails beyond the sweep budget.

---

## Decision Log

| Date | Decision |
|------|----------|
| 2026-07-10 | Spec created from the production finding `seed=1, affected=1344, avg 1161s`. Invariant adopted: per-episode work is O(k), k fixed. Materialized rails kept for v1; query-time computation documented as the fallback architecture. Calibrated (pool-independent) scoring accepted as the prerequisite for reverse-rail merging. |
