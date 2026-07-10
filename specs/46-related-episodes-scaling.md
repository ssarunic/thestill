# Related Episodes — Scaling to a Large Corpus

> **Status:** 🚧 In progress (2026-05-30) — Tier 3's incremental path is superseded by [#56 related-episodes-constant-time](56-related-episodes-constant-time.md) (2026-07-10: at corpus 1,455 < candidate cap 2,000, "incremental" degenerates to a ~19-min near-full rebuild per summarize batch)
> **Created:** 2026-05-30
> **Updated:** 2026-07-10
> **Author:** Engineering
> **Related:** [#28 corpus-search-and-entities](28-corpus-search-and-entities.md) (owns the "Related episodes" rail, §5.2; this spec scales it), [#43 aws-hosting](43-aws-hosting.md) / [#44 postgres-migration](44-postgres-migration.md) (the hosted, multi-thousand-episode future this unblocks), [#42 robustness](42-robustness-and-failure-mode-hardening.md) (staleness ≠ failure; degrade visibly)

---

## Executive Summary

The "Related episodes" rail ([related_builder.py](../thestill/search/related_builder.py),
served by `GET /api/search/related` in [api_search.py](../thestill/web/routes/api_search.py))
works well at today's scale (764 episodes, ~14s full build) but is built on an
**all-pairs O(N²)** computation with an **O(M)-chunk in-memory load**. Both wall
out as the corpus grows toward the thousands-to-100K episodes that the hosted
story ([#43](43-aws-hosting.md)) targets:

- The builder materialises dense `tfidf @ tfidf.T` and `centroids @ centroids.T`,
  both N×N. At 50K episodes that's a 10 GB dense matrix — out of RAM.
- `_load_corpus` pulls **every chunk** (163K rows ≈ 250 MB of float32 embeddings
  today) into numpy on every build. At 100K episodes (~21M chunks) that load
  alone is ~32 GB.

The fix is a reframe: **you never need the full N×N matrix — you need top-5
neighbours per episode.** That makes this a *top-K retrieval* problem, which the
search backend's existing indexes (`chunks_vec` ANN, `chunks_fts` BM25) already
solve sub-quadratically. The work lands in three tiers (Tier 1 from the original
roadmap — block/stream the dense matrix — is **deliberately skipped**: it only
shrinks the O(N²) constant and would be thrown away once Tier 2 lands).

This spec scales relevance **without changing it** — the blend
(`0.55·TF-IDF + 0.30·dense-vector + 0.15·entity-overlap`, gated by a TF-IDF
floor, cap 5) and its rationale are owned by [#28 §5.2](28-corpus-search-and-entities.md)
and unchanged here. A `bge-small` embedding-model swap was already tested and
**rejected** (it compressed cosines further; see #28 §5.2) — this is why the
topical TF-IDF signal, not a better dense model, is the load-bearing part.

---

## Why now / why this shape

Relevance is a **cache of a corpus-global computation** stored in
`episode_related`. Two properties force the design:

1. **TF-IDF is corpus-relative.** A term's weight depends on its rarity across
   *all* episodes (IDF) — that's exactly what makes "biceps" strong and "the key
   thing is" weak. Add episodes and every weight shifts slightly.
2. **Relevance is pairwise.** A new episode must be scored against all others to
   find its neighbours, *and* it is a candidate neighbour for every existing
   episode.

So the table is a derived cache that goes stale on corpus change, and the naïve
refresh is all-pairs. The tiers below keep the cache correct while making both
the full rebuild and incremental refresh sub-quadratic.

---

## The tiers

### Tier 0 — Materialise episode-level vectors *(removes the chunk-load wall)*

Stop re-summing M chunk embeddings on every build. `ChunkWriter.write_episode`
([chunk_writer.py:80](../thestill/core/chunk_writer.py#L80)) already holds an
episode's chunk embeddings in memory the moment it writes them, so it computes
the **L2-normalised centroid** for free and upserts it into a new
`episode_vectors` table (+ an `episode_vec` `vec0` ANN index mirroring it). The
builder then reads **N** episode centroids instead of **M** chunk embeddings —
the memory wall changes from "total chunks" to "episode count" (~200× fewer
rows). TF-IDF still needs text, but text is read on demand (`group_concat` over
a single episode's chunks) and is cheap relative to embeddings.

Decision: **centroid computed at write time in `ChunkWriter`** (zero extra cost,
keeps it fresh per episode — which Tier 3 needs). A backfill populates existing
episodes; the builder falls back to computing a missing centroid from `chunks`.

### Tier 1 — *(skipped)* block/stream the dense matrix

Would bound memory by computing top-K for a block of rows at a time. Still
O(N²) compute, and entirely superseded by Tier 2. Not built.

### Tier 2 — Candidate generation + rerank *(removes the N² wall)*

For each source episode, gather a small candidate pool, then run the full blend
**only on that pool**. O(N²) → O(N·K).

Candidate pool = **union of two retrieval legs** (decision: union, not ANN-only,
for recall):

- **Vector ANN** over `episode_vec` (centroid kNN, ~O(log N)) — dense neighbours.
- **BM25 lexical** over `chunks_fts`, queried with the source episode's top
  **IDF-weighted** terms (now that we fit a global IDF, term selection is
  distinctive, not raw-frequency — the failure mode from the first BM25 attempt).
  This leg guarantees topically-related episodes the dense model ranks low still
  enter the pool.

The blend, floor, and cap are unchanged, so output is relevance-equivalent to the
all-pairs build (verified against the current `episode_related` as an acceptance
gate). Decision: **the full `thestill related build` also uses this path** — one
code path for full and incremental; the O(N²) matrix is retired entirely.

A global IDF model (vocabulary + idf weights) is fit once per full build and
**persisted** (decision below) so incremental runs don't refit.

### Tier 3 — Incremental updates on episode add *(removes the full-rebuild-per-add cost)*

A new terminal pipeline stage **`COMPUTE_RELATED`**, modelled exactly on
`REBUILD_COOCCURRENCES` ([task_handlers.py:741](../thestill/core/task_handlers.py#L741)):
it runs after `REBUILD_COOCCURRENCES`, coalesces sibling pending tasks via
`claim_pending_for_coalescing` under a process lock, and does a **scoped** update
over the union of affected episodes:

- **Forward:** compute each affected episode's own rail (Tier 2 candidate+rerank).
- **Reverse (bounded):** for the episodes near each newcomer (its candidate
  pool), recompute *their* top-5 too, so the new episode appears in others' rails
  immediately rather than at the next full build. Cost is O(K) per added episode,
  not O(N).

Incremental runs **reuse the persisted IDF** (decision below) — they do not
refit. The `tasks.stage` CHECK auto-widens from the `TaskStage` enum
([queue_manager.py:364](../thestill/core/queue_manager.py#L364)), so adding the
stage needs no manual migration.

---

## Decisions (and why)

| Decision | Choice | Why |
|---|---|---|
| **IDF for incremental** | Persist fitted vocabulary+IDF; refresh on full `related build` | Incremental adds stay O(K) and scale to 100K+. A newcomer's novel terms are ignored until the next full build refreshes IDF — acceptable drift for a recommendation surface; full builds are cheap (Tier 2) and can run on a schedule. Refitting per add would make trickle ingestion O(N) each. |
| **Reverse-update on add** | Yes, bounded via ANN candidates | A new episode should surface in related rails of episodes it's close to, immediately — not only after the next full build. Bounded to ~K episodes, mirroring how cooccurrence's scoped rebuild covers both directions of a pair. |
| **Candidate sources** | Vector ANN **∪** BM25(top-IDF terms) | The BM25 leg is the recall safety net: it guarantees topically-related episodes (the dense model ranks low) enter the pool — the exact failure the TF-IDF signal exists to fix. ANN-only would risk dropping them outside the candidate window. |
| **Full build** | Also candidate-gen | One code path for full + incremental; retires O(N²) so even the full rebuild scales. Gated on relevance parity vs. the current all-pairs output. |
| **Centroid location** | Computed in `ChunkWriter` at write time | Embeddings already in hand → free; keeps centroids fresh per episode for Tier 3. |
| **Tier 1** | Skipped | Only shrinks the O(N²) constant; thrown away once Tier 2 lands. |
| **Model swap (bge)** | Rejected (in #28) | Compressed cosines further; the topical TF-IDF signal carries relevance, so a denser-vector model doesn't help. |

---

## Data model

```sql
-- Tier 0: one row per (episode, embedding_model); the embedding-derived
-- part of an episode, materialised so the builder never reloads chunks.
CREATE TABLE episode_vectors (
    episode_id      TEXT NOT NULL REFERENCES episodes(id) ON DELETE CASCADE,
    embedding_model TEXT NOT NULL,
    chunk_count     INTEGER NOT NULL,
    centroid        BLOB NOT NULL,          -- L2-normalised float32[dim]
    computed_at     TIMESTAMP NOT NULL,
    PRIMARY KEY (episode_id, embedding_model)
);
CREATE VIRTUAL TABLE episode_vec USING vec0(embedding float[<dim>]);  -- ANN over centroids

-- Tier 2: persisted IDF model (vocabulary + idf weight per term), one
-- active row-set per build so incremental runs reuse it without refit.
CREATE TABLE related_idf (
    term  TEXT PRIMARY KEY,
    idf   REAL NOT NULL
);
-- episode_related (existing, #28 §5.2) is unchanged in shape.
```

`episode_related` (the output, owned by #28) is untouched in schema; only how it
is *populated* changes.

---

## Failure & staleness model (per [#42](42-robustness-and-failure-mode-hardening.md))

- **Staleness ≠ failure.** Between full builds, a newcomer's novel vocabulary is
  IDF-invisible and reverse-update is best-effort. This is acceptable drift, not
  an error — never block ingestion on it.
- **Missing `episode_vec`/IDF degrade, don't crash.** If `episode_vectors` is
  empty (pre-backfill) or `related_idf` unbuilt, the builder computes from
  `chunks` and the endpoint returns `[]` (rail hides) rather than erroring.
- **`COMPUTE_RELATED` is a non-user-failing stage** (entity branch): a failure
  there must not fail the episode for the user, matching `REBUILD_COOCCURRENCES`.

---

## Testing

- **Tier 0:** centroid parity (materialised == on-the-fly mean); builder output
  byte-identical to pre-Tier-0 on the test corpus; `ChunkWriter` upserts a
  centroid row.
- **Tier 2:** relevance parity — the candidate-gen full build reproduces the
  all-pairs `episode_related` top-5 for the live corpus on a sample of episodes
  (incl. the muscle / 20VC / Elon cases from #28); candidate pool includes the
  known-good neighbours; perf (no N×N materialisation).
- **Tier 3:** `COMPUTE_RELATED` coalesces siblings; forward + bounded reverse
  update; reuses persisted IDF; a newly-added episode appears in a near
  episode's rail without a full rebuild.

---

## Status

| Item | Status | Notes |
|---|---|---|
| Spec | ✅ Done | This doc |
| Tier 0 — episode_vectors + ChunkWriter + builder read | ✅ Done | 763/763 byte-parity vs all-pairs; centroid written in `ChunkWriter`, self-healing backfill in builder |
| Tier 2 — candidate-gen rerank (ANN ∪ BM25) + persisted IDF | ✅ Done | Full build uses this path. Pool = `min(N, cap)` (cap 2000): **exact** ≤ cap (verified 100% parity on the live 763-corpus), sub-quadratic candidate-approximate above. `episode_vec` ANN + `related_idf` migrations added |
| Tier 3 — `COMPUTE_RELATED` stage (forward + reverse, coalesced) | ✅ Done | Mirrors `REBUILD_COOCCURRENCES` (lock + `claim_pending_for_coalescing`). Incremental update reuses persisted IDF via a hand-rolled transform that's **bit-identical** to `TfidfVectorizer` (verified on prod) |
| Thresholds at scale | 📝 Open | Revisit `candidate_cap` / `tfidf_floor` once the corpus exceeds the cap and approximate-tail behaviour can be measured on real data |
