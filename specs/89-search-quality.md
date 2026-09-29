# 89 — Search quality: measured, reranked, entity-aware

Status: Phases 1-3 built (2026-09-29, PR pending); Phase 4 paused

## Problem

Corpus search ranks with two vector-free signals glued together by hand-set
thresholds. Nothing reads the query and a passage together, and nothing
measures quality, so every fix so far was tuned against one anecdote.

The Legora incident (2026-09-29) is the canonical failure. The embedding model
(`paraphrase-multilingual-MiniLM-L12-v2`, a 2020 paraphrase model, not a
retrieval model) tokenises `Legora` as `Lego`+`ra` but `legora` as
`le`+`gora` — Croatian for "mountain" — so the lowercase query embedded
0.37 from short Croatian sentences and 0.95 from the real Legora quotes.
Distance gates cannot tell the two apart. #272, #277 and #286 hardened the
pipeline; #286 (short queries are lexical-only in hybrid) is a stopgap.

## Goals

- A golden query set and an evaluator that turns any search change into
  numbers: nDCG@10, junk@10, latency.
- A cross-encoder reranker that scores (query, passage) pairs, replacing the
  hand-set gates and the short-query rule.
- An entity leg: a query that names a known entity pulls that entity's
  mentions into the candidate pool.
- A measured recommendation on the embedding model, made on a scratch copy.

## Non-goals

- Changing the production embedding model in this spec (needs a full
  re-embed; decided from Phase 4's numbers, executed separately).
- Query autocomplete / typeahead (`/api/search/quick`).

## Phase 1 — golden set and evaluator

- `thestill/evals/data/search_golden.json`: ~60 queries across kinds —
  `name` (both cases), `concept` (English questions and short topics),
  `croatian`, `nonsense` (typos and gibberish that should return little or
  nothing).
- `thestill eval search`: runs each query through the configured backend in
  hybrid mode, judges unseen (query, passage) pairs with the pinned eval
  judge in one batched call per query, caches grades in
  `data/evals/search_judgments.jsonl` keyed by query + episode + segment +
  text hash, and reports per-kind nDCG@10, junk@10 (grade-0 results shown),
  and p50/p95 latency. Reruns only judge new pairs.
- Grades: 2 = about the query's subject or answers it; 1 = substantively
  related; 0 = unrelated (including namesakes: Lego toys for "Legora").

## Phase 2 — reranker

- `thestill/search/reranker.py`: lazy, lock-guarded `CrossEncoder`, sigmoid
  scores in [0, 1].
- Hybrid with the reranker: candidates are the lexical top-N and the semantic
  top-N (noise cutoff only, no gate, no short-query rule) plus the entity
  leg; the reranker orders them and drops those under a calibrated floor.
- Candidate models benchmarked on the golden set for quality and CPU latency.
- Config: `SEARCH_RERANKER_MODEL` (empty = off), `SEARCH_RERANK_MIN_SCORE`.

## Phase 3 — entity leg

- Resolve the query against entity names and aliases (case-insensitive);
  add chunks joined from `entity_mentions` on (episode_id, segment_id) to the
  candidate pool. Candidates only: entity links are known to be noisy
  ("Anthropic" → "Anthropic principle"), the reranker decides.

## Phase 4 — embedding model comparison

- Re-embed the prod snapshot's chunks with candidate retrieval models into a
  scratch database and compare on the golden set. Recommendation only.

## Rollout

Reranker ships default-off behind `SEARCH_RERANKER_MODEL`, is turned on in
prod after a latency check on the box, then the stopgap rules are removed.

## Results (2026-09-29, prod snapshot of 2026-09-22, judge gemini-3-flash-preview)

Pooled over all runs; nDCG@10 / junk@10 (unrelated results shown per query).

| Variant | all | name | concept | croatian | nonsense junk |
|---|---|---|---|---|---|
| gated RRF, pre-#286 | 0.767 / 2.12 | 0.817 / 1.71 | 0.748 / 1.91 | 0.671 / 3.33 | 3.60 |
| #286 short-query rule | 0.826 / 1.05 | 0.984 / 0.08 | 0.767 / 1.73 | 0.623 / 2.33 | 1.20 |
| mmarco reranker + entity leg, floors 0.01 / 0.05 | 0.828 / 0.47 | 0.943 / 0.17 | 0.805 / 0.82 | 0.569 / 0.83 | 0.00 |
| ms-marco (English) reranker, floors 0 / 0.1 | 0.832 / 0.46 | 0.959 / 0.17 | 0.832 / 0.59 | 0.447 / 1.50 | 0.00 |

- bge-reranker-v2-m3: 5.4 s per 60 pairs on 2 threads — rejected on latency.
- The reranker halves junk at equal nDCG and fixes typos and nonsense;
  #286 is still better on name queries, the English model loses Croatian.
- Remaining limit is candidate recall for concept and Croatian queries: the
  semantic leg is a 2020 paraphrase model. Phase 4 (multilingual-e5-small,
  ~27 min re-embed on the Mac GPU) stopped when the dev machine's disk filled;
  resume with >= 5 GB free.
- The results page searched once per keystroke; now debounced (300 ms).
