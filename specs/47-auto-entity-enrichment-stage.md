# Auto Entity Enrichment — Pipeline Stage

> **Status:** 🚧 In progress (2026-05-30)
> **Created:** 2026-05-30
> **Updated:** 2026-05-30
> **Author:** Engineering
> **Related:** [#45 entity-page-enrichment](45-entity-page-enrichment.md) (owns the `entity_enrichment` table, the `EntityEnricher`, and the `thestill enrich-entities` CLI; this spec wires it into the pipeline), [#28 corpus-search-and-entities](28-corpus-search-and-entities.md) (owns the entity branch this stage extends), [#46 related-episodes-scaling](46-related-episodes-scaling.md) (the `COMPUTE_RELATED` coalesced-stage pattern this mirrors), [#42 robustness](42-robustness-and-failure-mode-hardening.md) (FM-1 per-item failure isolation; transient ≠ "no data")

---

## Executive Summary

Tier-0 entity enrichment ([#45](45-entity-page-enrichment.md)) — the Wikidata
photo/logo, headline, Wikipedia lead, and vital-stats facts shown on
`/entities/person/<slug>` — is implemented and working, but it only runs when an
operator manually invokes `thestill enrich-entities`. It is **not** wired into
the automated pipeline. Result: a freshly-resolved entity (e.g. `person:ronald-coase`,
QID `Q188113`) has a fully-populated page *except* the photo and bio, because its
`entity_enrichment` row was never created. Corpus-wide, only ~57% of QID-bearing
entities are enriched.

This spec closes that gap by adding **`ENRICH_ENTITIES`** as the new terminal
stage of the entity branch ([#28](28-corpus-search-and-entities.md)), so newly
resolved entities get their display data shortly after first appearing — without
an operator in the loop.

## Why a stage, not an inline fetch

Enrichment is the only **network-bound** step in the entity branch (≈3 sequential
Wikimedia GETs per entity) and is pure display data. The two rejected alternatives:

- **Inline into `resolve-entities`.** The resolve worker runs at
  `PARALLEL_JOBS=1`, already lock-contended on the single SQLite writer. An
  episode resolves ~33 entities on average; ~100 sequential Wikimedia calls inline
  would add minutes to each resolve task, hold the writer slot across 5 s timeouts,
  and push back `REINDEX`/`COMPUTE_RELATED` — the search index and related rail
  users actually consume. It would also couple resolve throughput to Wikipedia's
  uptime.
- **N concurrent workers each enriching.** Defeats Wikimedia politeness and risks
  rate-limiting; no HTTP-layer retry exists in the clients.

## Design

`ENRICH_ENTITIES` is appended after `COMPUTE_RELATED`, modelled on
`handle_compute_related` / `handle_rebuild_cooccurrences` ([#46](46-related-episodes-scaling.md)):

- **Terminal + last.** Chain is `… → REINDEX → REBUILD_COOCCURRENCES →
  COMPUTE_RELATED → ENRICH_ENTITIES`. Running last means its latency/flakiness
  never delays user-visible derived data.
- **Coalesced, not concurrent.** Sibling pending rows are claimed under
  `_enrichment_lock`; one task enriches the union of the batch's episodes'
  entities. The lock also paces outbound traffic so the per-request politeness
  delay actually bounds total Wikimedia load.
- **Scoped selection, reused.** Candidates come from the existing
  `entity_ids_needing_enrichment(episode_id=…)` — which already gates on
  never-enriched / older schema / failed-past-`retry_after` / `>max_age_days`.
- **No long transactions.** The network fetch happens outside any DB transaction;
  each `upsert_enrichment` is its own short write.
- **Bounded burst.** `enrichment_max_per_task` (default 200) caps attempts per
  task; overflow is left for the scheduled sweep.
- **Failure isolation (FM-1, [#42](42-robustness-and-failure-mode-hardening.md)).**
  A single entity's error is swallowed and logged; the batch continues. A hard
  error flips `entity_extraction_status`, never `failed_at_stage` (entity-branch
  contract).

## The scheduled batch stays

`ENRICH_ENTITIES` fires only for **freshly-processed episodes**. It does *not*
cover: transient-`FAILED` retries (the 6 h `retry_after` needs polling), 30-day
staleness on entities not re-mentioned, or re-enrichment after an admin QID
correction. So `thestill enrich-entities` (corpus-wide, no `episode_id` scope)
remains the owner of retries + staleness. The stage **supplements** it, never
replaces it; both share the same `entity_ids_needing_enrichment` selection, so
they cannot diverge.

## Touch points

- [queue_manager.py](../thestill/core/queue_manager.py) — `TaskStage.ENRICH_ENTITIES`,
  `STAGE_SUCCESSORS`, `_NON_USER_FAILING_STAGES`, `_ENTITY_BRANCH_ORDER`. The
  `tasks.stage` CHECK constraint auto-widens on next startup (existing migration).
- [task_handlers.py](../thestill/core/task_handlers.py) — `handle_enrich_entities`,
  `_get_or_create_entity_enricher`, `_enrichment_lock`, `HANDLERS` registration.
- [config.py](../thestill/utils/config.py) — `enrichment_max_per_task`
  (`ENRICHMENT_MAX_PER_TASK`).
- [dependencies.py](../thestill/web/dependencies.py) — `AppState.entity_enricher`
  (lazy-cached).

The frontend `PipelineStage` type and `stages.ts` deliberately do not model the
terminal coalesced stages (`rebuild-cooccurrences`, `compute-related`); for the
same reason `enrich-entities` is backend-only.
