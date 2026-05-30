# Copyright 2025-2026 Thestill
#
# Licensed under the Apache License, Version 2.0 (the "License");
# you may not use this file except in compliance with the License.
# You may obtain a copy of the License at
#
#     http://www.apache.org/licenses/LICENSE-2.0
#
# Unless required by applicable law or agreed to in writing, software
# distributed under the License is distributed on an "AS IS" BASIS,
# WITHOUT WARRANTIES OR CONDITIONS OF ANY KIND, either express or implied.
# See the License for the specific language governing permissions and
# limitations under the License.

"""Spec #28 §5.2 — precompute the "Related episodes" rail.

Dense chunk vectors alone rank related episodes badly: the embedding
model (``paraphrase-multilingual-MiniLM``) encodes conversational
*register* far more strongly than *topic*, so every long-form interview
episode sits ~0.6 cosine from every other one. Measured on the live
corpus (May 2026): a genuinely-related fitness episode scores ~0.69
while the *global corpus average* scores 0.59 — almost no dynamic range.
A model swap to ``bge-small`` was tested and did **not** help (its
cosines are even more compressed).

What does separate topics is the distinctive vocabulary — "biceps,
lunge, cardio" vs "roadmap, GTM, valuation" — which TF-IDF captures and
dense pooling washes out. So relevance here is a blend:

    score = w_tfidf · tfidf_cos + w_vector · vector_cos + w_entity · jaccard

with each signal min-max-normalised across the eligible candidates for a
given source episode (the three raw scales are wildly different). TF-IDF
is also used as a *gate*: a candidate must clear ``tfidf_floor`` to be
eligible at all, which is what drops cross-domain intruders (a product
podcast next to a muscle-building episode) entirely rather than ranking
them low. Episodes with few topical neighbours therefore surface fewer
than ``top_n`` rows — by design.

This is a corpus-global computation (every episode vs every other), too
expensive for the request path, so it's run as a batch step after
reindex/backfill and the results land in the ``episode_related`` table.
"""

from __future__ import annotations

import sqlite3
from collections import defaultdict
from contextlib import contextmanager
from typing import Dict, List, Tuple

from structlog import get_logger

from ..utils.sqlite_ext import load_vec_extension
from .base import embedding_dim_for

logger = get_logger(__name__)

# A candidate must reach this TF-IDF cosine with the source to be
# eligible. Calibrated on the live corpus (May 2026): genuinely-related
# episodes land ≳ 0.17 and clear cross-domain noise sits ≲ 0.13. 0.12
# is a deliberately conservative gate — its job isn't to rank (the blend
# + top-N cap do that) but to make genuinely-isolated episodes surface
# *fewer than N* rows instead of padding the rail with weak matches.
# Topically-dense episodes still fill to top_n; only true outliers trim.
DEFAULT_TFIDF_FLOOR = 0.12

# Blend weights. TF-IDF leads because it's the only signal that reliably
# separates topic from conversational register; the dense vector adds
# recall for topically-related episodes that use different wording; the
# entity overlap is a precision nudge (shared people/companies).
DEFAULT_W_TFIDF = 0.55
DEFAULT_W_VECTOR = 0.30
DEFAULT_W_ENTITY = 0.15

# Candidate-pool cap (spec #46 Tier 2). The effective pool per leg is
# ``min(N, cap)``, so below ``cap`` episodes the pool is the whole corpus
# and the build is *exact* — bit-identical to all-pairs (verified: full
# recall ⇒ the per-source min-max sees the same eligible set, so ranking
# is preserved). Above ``cap`` the pool is bounded and the build becomes
# sub-quadratic candidate-approximate, with the strongest (head) matches
# the most stable. Raise the cap to trade build time for tail parity.
#
# The two legs are complementary: the vector leg (ANN over centroids)
# finds dense neighbours; the lexical leg (BM25 over the source's most
# distinctive IDF-weighted terms) finds topical neighbours the dense
# model ranks low — the recall safety net. Both are queried at the cap.
DEFAULT_CANDIDATE_CAP = 2000
DEFAULT_LEXICAL_TERMS = 25

# TF-IDF configuration — module-level so the full build and any
# incremental transform agree on vocabulary and normalisation. ``min_df=2``
# drops corpus-singleton terms (a term in one episode can't link two), which
# also keeps the vocabulary — and the persisted IDF table — from ballooning.
_TFIDF_KWARGS = dict(stop_words="english", max_features=50_000, ngram_range=(1, 2), sublinear_tf=True, min_df=2)


def build_related_episodes(
    db_path: str,
    *,
    embedding_model_name: str,
    top_n: int = 5,
    tfidf_floor: float = DEFAULT_TFIDF_FLOOR,
    w_tfidf: float = DEFAULT_W_TFIDF,
    w_vector: float = DEFAULT_W_VECTOR,
    w_entity: float = DEFAULT_W_ENTITY,
    candidate_cap: int = DEFAULT_CANDIDATE_CAP,
) -> Dict[str, int]:
    """Recompute the whole ``episode_related`` table from the corpus.

    Returns a summary dict: ``{"episodes": n, "pairs": m}`` where
    ``episodes`` is how many source episodes got at least one related
    row and ``pairs`` is the total rows written. The table is rebuilt
    transactionally — readers see the old contents until commit.
    """
    import numpy as np  # local: heavy, only needed for the batch build
    from sklearn.feature_extraction.text import TfidfVectorizer

    dim = embedding_dim_for(embedding_model_name)
    # Spec #46 Tier 0 — make sure every chunked episode has a materialised
    # centroid (ChunkWriter writes them going forward; this backfills any
    # pre-existing episodes), then read centroids from episode_vectors
    # instead of reloading every chunk embedding.
    _ensure_episode_vectors(db_path, embedding_model_name, dim, np)
    docs, centroids, eids = _load_corpus(db_path, embedding_model_name, dim, np)
    if len(eids) < 2:
        _write_pairs(db_path, [])  # clear stale rows on a now-tiny corpus
        logger.info("related_build_skipped_small_corpus", episodes=len(eids))
        return {"episodes": 0, "pairs": 0}

    # Fit the TF-IDF topical model over the whole corpus once and persist
    # it (Tier 2) so incremental updates can transform new text without
    # refitting. ``tfidf`` is a sparse N×V matrix — we never densify it to
    # N×N; per-source similarities are computed against a candidate subset.
    vectorizer = TfidfVectorizer(**_TFIDF_KWARGS)
    tfidf = vectorizer.fit_transform([docs[e] for e in eids])
    feature_names = vectorizer.get_feature_names_out()
    _persist_idf(db_path, vectorizer)

    entity_sets = _load_entity_sets(db_path)
    idx = {e: i for i, e in enumerate(eids)}

    # Effective pool per leg: the whole corpus until it exceeds the cap
    # (exact build), bounded above it (sub-quadratic, approximate tail).
    k = min(len(eids), candidate_cap)
    rows: List[Tuple[str, str, int, float]] = []
    episodes_with_related = 0
    with _candidate_conn(db_path) as conn:
        rowid_to_eid = _episode_vec_rowmap(conn, embedding_model_name)
        for i, src in enumerate(eids):
            cand_ids = _candidate_ids(
                conn, src, centroids[i], tfidf[i], feature_names, rowid_to_eid, np, k_vec=k, k_lex=k
            )
            ranked = _rerank(
                src,
                i,
                cand_ids,
                idx,
                tfidf,
                centroids,
                entity_sets,
                eids,
                top_n,
                tfidf_floor,
                w_tfidf,
                w_vector,
                w_entity,
                np,
            )
            if ranked:
                episodes_with_related += 1
            for rank, (rel_id, score) in enumerate(ranked):
                rows.append((src, rel_id, rank, score))

    _write_pairs(db_path, rows)
    logger.info(
        "related_build_complete",
        episodes_total=len(eids),
        episodes_with_related=episodes_with_related,
        pairs=len(rows),
    )
    return {"episodes": episodes_with_related, "pairs": len(rows)}


@contextmanager
def _candidate_conn(db_path):
    """Read connection with sqlite-vec loaded (for the episode_vec ANN)."""
    conn = sqlite3.connect(db_path)
    conn.row_factory = sqlite3.Row
    load_vec_extension(conn)
    try:
        yield conn
    finally:
        conn.close()


def _episode_vec_rowmap(conn, embedding_model_name) -> Dict[int, str]:
    """episode_vec rowid → episode_id (the ANN returns rowids)."""
    return {
        r["rowid"]: r["episode_id"]
        for r in conn.execute(
            "SELECT rowid, episode_id FROM episode_vectors WHERE embedding_model = ?",
            (embedding_model_name,),
        )
    }


def _candidate_ids(conn, src, centroid, tfidf_row, feature_names, rowid_to_eid, np, *, k_vec, k_lex) -> set:
    """Union of dense (ANN) and lexical (BM25) candidate episode ids, minus self."""
    cands: set = set()
    cands.update(_vector_candidates(conn, centroid, rowid_to_eid, k_vec, np))
    cands.update(_lexical_candidates(conn, src, tfidf_row, feature_names, k_lex))
    cands.discard(src)
    return cands


def _vector_candidates(conn, centroid, rowid_to_eid, k, np) -> List[str]:
    """k nearest episodes by centroid cosine via the episode_vec ANN index."""
    blob = np.asarray(centroid, dtype=np.float32).tobytes()
    try:
        rows = conn.execute(
            "SELECT rowid FROM episode_vec WHERE embedding MATCH ? AND k = ?",
            (blob, k + 1),  # +1: the source itself is its own nearest neighbour
        ).fetchall()
    except sqlite3.OperationalError:
        return []  # episode_vec absent (extension/migration skipped) — degrade to lexical only
    return [rowid_to_eid[r["rowid"]] for r in rows if r["rowid"] in rowid_to_eid]


def _lexical_candidates(conn, src, tfidf_row, feature_names, k) -> List[str]:
    """Episodes whose chunks best BM25-match the source's most distinctive terms.

    Term selection uses the *fitted IDF* (the source's top TF-IDF terms),
    so it picks "biceps, hypertrophy" not "the key thing" — the recall
    leg that surfaces topical neighbours the dense vector ranks low.
    """
    if tfidf_row is None:
        return []  # episode has a centroid but no text (no chunks) — nothing to match on
    terms = _top_terms(tfidf_row, feature_names, DEFAULT_LEXICAL_TERMS)
    if not terms:
        return []
    # Phrase-quote each term: neutralises FTS operators and matches bigrams
    # ("internal rotation") as phrases. TfidfVectorizer tokens are [a-z0-9 ],
    # so double-quote wrapping is safe.
    query = " OR ".join(f'"{t}"' for t in terms)
    try:
        rows = conn.execute(
            """
            SELECT c.episode_id AS eid, SUM(-bm25(chunks_fts)) AS s
            FROM chunks_fts JOIN chunks c ON c.id = chunks_fts.rowid
            WHERE chunks_fts MATCH ? AND c.episode_id != ?
            GROUP BY c.episode_id ORDER BY s DESC LIMIT ?
            """,
            (query, src, k),
        ).fetchall()
    except sqlite3.OperationalError:
        return []
    return [r["eid"] for r in rows]


def _top_terms(tfidf_row, feature_names, n) -> List[str]:
    """The n highest-weighted terms of a sparse TF-IDF row."""
    row = tfidf_row.tocoo()
    if row.nnz == 0:
        return []
    order = row.data.argsort()[::-1][:n]
    return [feature_names[row.col[k]] for k in order]


def _rerank(
    src, i, cand_ids, idx, tfidf, centroids, entity_sets, eids, top_n, floor, w_tfidf, w_vector, w_entity, np
) -> List[Tuple[str, float]]:
    """Blend the three signals over the candidate pool → ranked (id, score).

    Same blend as the all-pairs build (TF-IDF gate + min-max-normalised
    weighted sum), but tfidf/vector similarities are computed only against
    the candidate subset, so there's no N×N matrix.
    """
    cand_idx = [idx[e] for e in cand_ids if e in idx]
    if not cand_idx:
        return []
    tfidf_sim = np.asarray((tfidf[i] @ tfidf[cand_idx].T).todense()).ravel()
    keep = [p for p in range(len(cand_idx)) if tfidf_sim[p] >= floor]
    if not keep:
        return []
    sub = [cand_idx[p] for p in keep]
    t = tfidf_sim[keep]
    v = centroids[i] @ centroids[sub].T
    src_entities = entity_sets.get(src, frozenset())
    ent = np.array([_jaccard(src_entities, entity_sets.get(eids[j], frozenset())) for j in sub])
    score = w_tfidf * _minmax(t, np) + w_vector * _minmax(v, np) + w_entity * _minmax(ent, np)
    order = np.argsort(-score)[:top_n]
    return [(eids[sub[p]], round(float(score[p]), 6)) for p in order]


def _persist_idf(db_path, vectorizer) -> None:
    """Replace ``related_idf`` with the fitted vocabulary + idf weights (Tier 3 reuse)."""
    terms = vectorizer.get_feature_names_out().tolist()
    idfs = vectorizer.idf_.tolist()
    conn = sqlite3.connect(db_path)
    try:
        conn.execute("BEGIN")
        conn.execute("DELETE FROM related_idf")
        conn.executemany("INSERT INTO related_idf (term, idf) VALUES (?, ?)", zip(terms, idfs))
        conn.commit()
    except Exception:
        conn.rollback()
        raise
    finally:
        conn.close()


# ---------------------------------------------------------------------------
# Spec #46 Tier 3 — incremental update for a handful of episodes.
# ---------------------------------------------------------------------------


def update_related_for_episodes(
    db_path: str,
    *,
    embedding_model_name: str,
    episode_ids,
    top_n: int = 5,
    tfidf_floor: float = DEFAULT_TFIDF_FLOOR,
    w_tfidf: float = DEFAULT_W_TFIDF,
    w_vector: float = DEFAULT_W_VECTOR,
    w_entity: float = DEFAULT_W_ENTITY,
    candidate_cap: int = DEFAULT_CANDIDATE_CAP,
) -> Dict[str, int]:
    """Recompute ``episode_related`` for a few episodes without a full rebuild.

    Reuses the **persisted** IDF model (``related_idf``) so no corpus-wide
    refit happens — a new episode's novel terms are IDF-invisible until the
    next full build (acceptable drift, spec #46). Updates two sets:

    - **forward:** each episode in ``episode_ids`` gets its own rail;
    - **reverse:** the episodes near each input (its candidate pool) get
      *their* rails recomputed too, so the newcomer surfaces in them.

    Falls back to a full build if no IDF model exists yet (first run).
    """
    import numpy as np

    model = _load_idf_model(db_path, np)
    if model is None:
        return build_related_episodes(
            db_path,
            embedding_model_name=embedding_model_name,
            top_n=top_n,
            tfidf_floor=tfidf_floor,
            w_tfidf=w_tfidf,
            w_vector=w_vector,
            w_entity=w_entity,
            candidate_cap=candidate_cap,
        )
    vocab, idf, feature_names = model
    transform = _make_doc_transform(vocab, idf, np)
    dim = embedding_dim_for(embedding_model_name)
    _ensure_episode_vectors(db_path, embedding_model_name, dim, np)

    with _candidate_conn(db_path) as conn:
        n_corpus = conn.execute(
            "SELECT COUNT(*) FROM episode_vectors WHERE embedding_model = ?", (embedding_model_name,)
        ).fetchone()[0]
        k = min(n_corpus, candidate_cap)
        rowid_to_eid = _episode_vec_rowmap(conn, embedding_model_name)
        cache = _EpisodeCache(conn, embedding_model_name, dim, transform, np)

        seed = [e for e in dict.fromkeys(episode_ids) if cache.has(e)]
        # Forward pools + reverse expansion: every candidate of a seed
        # episode is a reverse target (its rail may now include the seed).
        pools: Dict[str, set] = {}
        affected: set = set(seed)
        for a in seed:
            pool = _candidate_ids(
                conn, a, cache.centroid(a), cache.tfidf(a), feature_names, rowid_to_eid, np, k_vec=k, k_lex=k
            )
            pools[a] = pool
            affected |= pool

        entity_sets = _load_entity_sets(db_path, affected)  # ``affected`` already includes every pool member
        out: Dict[str, List[Tuple[str, float]]] = {}
        for a in affected:
            if not cache.has(a):
                continue
            pool = pools.get(a)
            if pool is None:
                pool = _candidate_ids(
                    conn, a, cache.centroid(a), cache.tfidf(a), feature_names, rowid_to_eid, np, k_vec=k, k_lex=k
                )
            out[a] = _rerank_incremental(
                a, pool, cache, entity_sets, top_n, tfidf_floor, w_tfidf, w_vector, w_entity, np
            )

    # Only episodes we actually scored are rewritten — never delete a rail we
    # couldn't recompute (e.g. a pool member that lost its centroid mid-flight).
    pairs = _write_pairs_scoped(db_path, out)
    logger.info(
        "related_incremental_complete",
        seed=len(seed),
        affected=len(affected),
        pairs=pairs,
    )
    return {"episodes": sum(1 for v in out.values() if v), "pairs": pairs}


class _EpisodeCache:
    """Lazily fetch + cache an episode's centroid and TF-IDF vector."""

    def __init__(self, conn, embedding_model_name, dim, transform, np):
        self._conn = conn
        self._model = embedding_model_name
        self._dim = dim
        self._transform = transform
        self._np = np
        self._cent: Dict[str, object] = {}
        self._tfidf: Dict[str, object] = {}

    def has(self, eid) -> bool:
        return self.centroid(eid) is not None

    def centroid(self, eid):
        if eid not in self._cent:
            row = self._conn.execute(
                "SELECT centroid FROM episode_vectors WHERE episode_id = ? AND embedding_model = ?",
                (eid, self._model),
            ).fetchone()
            self._cent[eid] = (
                self._np.frombuffer(row["centroid"], dtype=self._np.float32, count=self._dim) if row else None
            )
        return self._cent[eid]

    def tfidf(self, eid):
        if eid not in self._tfidf:
            row = self._conn.execute(
                "SELECT group_concat(text, ' ') AS doc FROM chunks WHERE episode_id = ? AND embedding_model = ?",
                (eid, self._model),
            ).fetchone()
            self._tfidf[eid] = self._transform([row["doc"] or ""]) if row and row["doc"] else None
        return self._tfidf[eid]


def _rerank_incremental(src, pool, cache, entity_sets, top_n, floor, w_tfidf, w_vector, w_entity, np):
    """Blend over a candidate pool using per-episode vectors (no corpus matrix)."""
    from scipy.sparse import vstack

    src_t = cache.tfidf(src)
    cands = [c for c in pool if cache.tfidf(c) is not None and cache.centroid(c) is not None]
    if src_t is None or not cands:
        return []
    # TF-IDF cosine = dot product (rows are L2-normalised).
    cand_tfidf = vstack([cache.tfidf(c) for c in cands])
    tfidf_sim = np.asarray((src_t @ cand_tfidf.T).todense()).ravel()
    keep = [p for p in range(len(cands)) if tfidf_sim[p] >= floor]
    if not keep:
        return []
    kept = [cands[p] for p in keep]
    t = tfidf_sim[keep]
    src_c = cache.centroid(src)
    v = np.array([float(src_c @ cache.centroid(c)) for c in kept])
    src_ent = entity_sets.get(src, frozenset())
    ent = np.array([_jaccard(src_ent, entity_sets.get(c, frozenset())) for c in kept])
    score = w_tfidf * _minmax(t, np) + w_vector * _minmax(v, np) + w_entity * _minmax(ent, np)
    order = np.argsort(-score)[:top_n]
    return [(kept[p], round(float(score[p]), 6)) for p in order]


def _load_idf_model(db_path, np):
    """Load persisted IDF as ``(vocab, idf, feature_names)`` or ``None`` if unbuilt."""
    conn = sqlite3.connect(db_path)
    try:
        rows = conn.execute("SELECT term, idf FROM related_idf ORDER BY term").fetchall()
    except sqlite3.OperationalError:
        return None
    finally:
        conn.close()
    if not rows:
        return None
    terms = [r[0] for r in rows]
    vocab = {t: i for i, t in enumerate(terms)}
    idf = np.array([r[1] for r in rows], dtype=np.float64)
    return vocab, idf, np.array(terms, dtype=object)


def _make_doc_transform(vocab, idf, np):
    """A callable ``docs -> L2-normalised TF-IDF matrix`` using a fixed vocab+IDF.

    Replicates ``TfidfVectorizer(sublinear_tf=True, norm='l2', ngram_range=(1,2),
    stop_words='english')`` transform without refitting, so incremental updates
    place a new doc in the same vector space as the last full build.
    """
    from sklearn.feature_extraction.text import CountVectorizer
    from sklearn.preprocessing import normalize

    cv = CountVectorizer(vocabulary=vocab, ngram_range=(1, 2), stop_words="english")

    def transform(docs):
        counts = cv.transform(docs).astype(np.float64)
        counts.data = 1.0 + np.log(counts.data)  # sublinear tf
        scaled = counts.multiply(idf).tocsr()  # column-scale by idf
        return normalize(scaled, norm="l2", axis=1)

    return transform


def _write_pairs_scoped(db_path: str, out: Dict[str, List[Tuple[str, float]]]) -> int:
    """Replace ``episode_related`` rows for the scored episodes only, in one txn.

    Deletes and rewrites exactly the keys of ``out`` — an episode we didn't
    score keeps its prior rail rather than being silently emptied.
    """
    flat = [(src, rel, rank, score) for src, ranked in out.items() for rank, (rel, score) in enumerate(ranked)]
    conn = sqlite3.connect(db_path)
    try:
        conn.execute("BEGIN")
        conn.executemany("DELETE FROM episode_related WHERE episode_id = ?", [(a,) for a in out])
        if flat:
            conn.executemany(
                "INSERT INTO episode_related (episode_id, related_episode_id, rank, score) VALUES (?, ?, ?, ?)",
                flat,
            )
        conn.commit()
    except Exception:
        conn.rollback()
        raise
    finally:
        conn.close()
    return len(flat)


def _minmax(a, np):
    """Scale to [0, 1]; a single (or flat) value maps to 1.0."""
    span = float(np.ptp(a))
    if len(a) <= 1 or span == 0.0:
        return np.ones_like(a)
    return (a - a.min()) / span


def _jaccard(a: frozenset, b: frozenset) -> float:
    if not a or not b:
        return 0.0
    union = len(a | b)
    return len(a & b) / union if union else 0.0


def _ensure_episode_vectors(db_path, embedding_model_name, dim, np) -> int:
    """Materialise centroids for chunked episodes missing/stale in episode_vectors.

    ChunkWriter writes the centroid at chunk-write time, so this only has
    work to do for episodes chunked before spec #46 (or whose chunk_count
    drifted). Returns the number of episodes (re)computed. Self-healing —
    one full build after deploy backfills the whole corpus.
    """
    from ..core.embedding_model import centroid_blob

    conn = sqlite3.connect(db_path)
    conn.row_factory = sqlite3.Row
    try:
        # Episodes whose live chunk count doesn't match a stored centroid row.
        stale = conn.execute(
            """
            SELECT c.episode_id AS eid, COUNT(*) AS n
            FROM chunks c
            WHERE c.embedding_model = ?
            GROUP BY c.episode_id
            HAVING n != COALESCE(
                (SELECT v.chunk_count FROM episode_vectors v
                  WHERE v.episode_id = c.episode_id AND v.embedding_model = ?), -1)
            """,
            (embedding_model_name, embedding_model_name),
        ).fetchall()
        recomputed = 0
        for row in stale:
            eid = row["eid"]
            embeddings = [
                r["embedding"]
                for r in conn.execute(
                    "SELECT embedding FROM chunks WHERE episode_id = ? AND embedding_model = ?",
                    (eid, embedding_model_name),
                )
            ]
            centroid = centroid_blob(embeddings, dim)
            if centroid is None:
                continue
            conn.execute(
                """
                INSERT INTO episode_vectors (episode_id, embedding_model, chunk_count, centroid, computed_at)
                VALUES (?, ?, ?, ?, strftime('%Y-%m-%dT%H:%M:%f+00:00','now'))
                ON CONFLICT(episode_id, embedding_model)
                DO UPDATE SET chunk_count = excluded.chunk_count,
                              centroid    = excluded.centroid,
                              computed_at = excluded.computed_at
                """,
                (eid, embedding_model_name, len(embeddings), centroid),
            )
            recomputed += 1
        conn.commit()
    finally:
        conn.close()
    if recomputed:
        logger.info("episode_vectors_backfilled", episodes=recomputed)
    return recomputed


def _load_corpus(db_path, embedding_model_name, dim, np):
    """Return ``(docs, centroids, eids)`` for episodes with a materialised centroid.

    Spec #46 Tier 0: centroids come from ``episode_vectors`` (one row per
    episode) rather than re-summing every chunk embedding; ``docs`` (the
    concatenated chunk text TF-IDF needs) is read with ``group_concat`` so
    the per-chunk vectors are never loaded into memory.
    """
    conn = sqlite3.connect(db_path)
    conn.row_factory = sqlite3.Row
    try:
        vec_rows = conn.execute(
            "SELECT episode_id, centroid FROM episode_vectors WHERE embedding_model = ?",
            (embedding_model_name,),
        ).fetchall()
        doc_rows = conn.execute(
            """
            SELECT episode_id AS eid, group_concat(text, ' ') AS doc
            FROM chunks WHERE embedding_model = ? GROUP BY episode_id
            """,
            (embedding_model_name,),
        ).fetchall()
    finally:
        conn.close()
    docs_by_id = {r["eid"]: (r["doc"] or "") for r in doc_rows}
    eids: List[str] = []
    centroid_list = []
    for row in vec_rows:
        eid = row["episode_id"]
        if eid not in docs_by_id:
            continue  # centroid without chunks (shouldn't happen) — skip
        eids.append(eid)
        centroid_list.append(np.frombuffer(row["centroid"], dtype=np.float32, count=dim))
    docs = {e: docs_by_id[e] for e in eids}
    centroids = np.array(centroid_list, dtype=np.float32) if centroid_list else np.zeros((0, dim), dtype=np.float32)
    return docs, centroids, eids


def _load_entity_sets(db_path: str, episode_ids=None) -> Dict[str, frozenset]:
    """episode_id → frozenset of resolved entity ids it mentions.

    Pass ``episode_ids`` to restrict the scan (bounded incremental cost);
    ``None`` loads the whole corpus (full build).
    """
    where = "WHERE entity_id IS NOT NULL"
    params: list = []
    if episode_ids is not None:
        ids = list(episode_ids)
        if not ids:
            return {}
        where += f" AND episode_id IN ({','.join('?' for _ in ids)})"
        params = ids
    sets: Dict[str, set] = defaultdict(set)
    conn = sqlite3.connect(db_path)
    try:
        for episode_id, entity_id in conn.execute(f"SELECT episode_id, entity_id FROM entity_mentions {where}", params):
            sets[episode_id].add(entity_id)
    finally:
        conn.close()
    return {k: frozenset(v) for k, v in sets.items()}


def _write_pairs(db_path: str, rows: List[Tuple[str, str, int, float]]) -> None:
    """Replace the whole ``episode_related`` table in one transaction."""
    conn = sqlite3.connect(db_path)
    try:
        conn.execute("BEGIN")
        conn.execute("DELETE FROM episode_related")
        if rows:
            conn.executemany(
                "INSERT INTO episode_related (episode_id, related_episode_id, rank, score) VALUES (?, ?, ?, ?)",
                rows,
            )
        conn.commit()
    except Exception:
        conn.rollback()
        raise
    finally:
        conn.close()
