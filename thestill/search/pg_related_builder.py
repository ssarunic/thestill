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

"""Spec #44 — Postgres port of the related-episodes builder (spec #46).

Port of ``search.related_builder`` (the behavioural reference — keep in
lockstep, FM-6). All scoring math — the TF-IDF/vector/entity blend, min-max
normalisation, floors, weights, and TF-IDF configuration — is IMPORTED from
the SQLite module, so the two backends cannot drift numerically. Only the
storage access differs:

- episode centroids live in ``episode_vectors.centroid vector({dim})``
  (pgvector); with ``connect(dsn, vector=True)`` they read back as numpy
  float32 arrays directly — no ``np.frombuffer``;
- the dense candidate leg is a pgvector ``<=>`` k-NN over the HNSW index
  (replaces the sqlite-vec ``episode_vec`` virtual table; episode ids come
  straight off the rows, no rowid map);
- the lexical candidate leg ranks chunks with ``ts_rank_cd`` over the
  generated ``text_tsv`` column via ``websearch_to_tsquery`` (replaces
  FTS5/BM25 — websearch agrees with FTS5 on quoted phrases and ``OR``,
  which is all the term query uses);
- ``episode_related`` / ``related_idf`` writes are delete-then-insert in a
  single transaction (psycopg's connection context commits on clean exit),
  matching the SQLite transactional-replace semantics.
"""

from __future__ import annotations

from collections import defaultdict
from typing import Dict, List, Tuple

from structlog import get_logger

from ..utils.postgres_ext import as_str, connect
from .base import embedding_dim_for
from .related_builder import (  # shared behaviour — one concept, one number (FM-6)
    _TFIDF_KWARGS,
    DEFAULT_CANDIDATE_CAP,
    DEFAULT_LEXICAL_TERMS,
    DEFAULT_TFIDF_FLOOR,
    DEFAULT_W_ENTITY,
    DEFAULT_W_TFIDF,
    DEFAULT_W_VECTOR,
    _make_doc_transform,
    _rerank,
    _rerank_incremental,
    _top_terms,
)

logger = get_logger(__name__)


def build_related_episodes(
    dsn: str,
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

    Same contract as the SQLite ``build_related_episodes``: returns
    ``{"episodes": n, "pairs": m}`` and rebuilds the table transactionally.
    """
    import numpy as np  # local: heavy, only needed for the batch build
    from sklearn.feature_extraction.text import TfidfVectorizer

    dim = embedding_dim_for(embedding_model_name)
    # Spec #46 Tier 0 — make sure every chunked episode has a materialised
    # centroid (PostgresChunkWriter writes them going forward; this backfills
    # any pre-existing episodes), then read centroids from episode_vectors.
    _ensure_episode_vectors(dsn, embedding_model_name, dim, np)
    docs, centroids, eids = _load_corpus(dsn, embedding_model_name, dim, np)
    if len(eids) < 2:
        _write_pairs(dsn, [])  # clear stale rows on a now-tiny corpus
        logger.info("related_build_skipped_small_corpus", episodes=len(eids))
        return {"episodes": 0, "pairs": 0}

    vectorizer = TfidfVectorizer(**_TFIDF_KWARGS)
    tfidf = vectorizer.fit_transform([docs[e] for e in eids])
    feature_names = vectorizer.get_feature_names_out()
    _persist_idf(dsn, vectorizer)

    entity_sets = _load_entity_sets(dsn)
    idx = {e: i for i, e in enumerate(eids)}

    # Effective pool per leg: the whole corpus until it exceeds the cap
    # (exact build), bounded above it (sub-quadratic, approximate tail).
    k = min(len(eids), candidate_cap)
    rows: List[Tuple[str, str, int, float]] = []
    episodes_with_related = 0
    with connect(dsn, vector=True) as conn:
        for i, src in enumerate(eids):
            cand_ids = _candidate_ids(
                conn, embedding_model_name, src, centroids[i], tfidf[i], feature_names, np, k_vec=k, k_lex=k
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

    _write_pairs(dsn, rows)
    logger.info(
        "related_build_complete",
        episodes_total=len(eids),
        episodes_with_related=episodes_with_related,
        pairs=len(rows),
    )
    return {"episodes": episodes_with_related, "pairs": len(rows)}


def _candidate_ids(conn, embedding_model_name, src, centroid, tfidf_row, feature_names, np, *, k_vec, k_lex) -> set:
    """Union of dense (pgvector k-NN) and lexical (tsquery) candidates, minus self."""
    cands: set = set()
    cands.update(_vector_candidates(conn, embedding_model_name, centroid, k_vec, np))
    cands.update(_lexical_candidates(conn, src, tfidf_row, feature_names, k_lex))
    cands.discard(src)
    return cands


def _vector_candidates(conn, embedding_model_name, centroid, k, np) -> List[str]:
    """k nearest episodes by centroid cosine via the pgvector HNSW index."""
    qvec = np.asarray(centroid, dtype=np.float32)
    rows = conn.execute(
        """
        SELECT episode_id FROM episode_vectors
        WHERE embedding_model = %s
        ORDER BY centroid <=> %s
        LIMIT %s
        """,
        (embedding_model_name, qvec, k + 1),  # +1: the source itself is its own nearest neighbour
    ).fetchall()
    return [as_str(r["episode_id"]) for r in rows]


def _lexical_candidates(conn, src, tfidf_row, feature_names, k) -> List[str]:
    """Episodes whose chunks best match the source's most distinctive terms.

    Term selection uses the *fitted IDF* (the source's top TF-IDF terms) —
    same recall leg as the SQLite BM25 version, ranked with ``ts_rank_cd``
    over the generated ``text_tsv`` column. Phrase-quoting each term matches
    FTS5 semantics: bigrams ("internal rotation") match as phrases and the
    terms (TfidfVectorizer tokens, [a-z0-9 ]) can't inject query operators.
    """
    if tfidf_row is None:
        return []  # episode has a centroid but no text (no chunks) — nothing to match on
    terms = _top_terms(tfidf_row, feature_names, DEFAULT_LEXICAL_TERMS)
    if not terms:
        return []
    query = " OR ".join(f'"{t}"' for t in terms)
    rows = conn.execute(
        """
        SELECT c.episode_id AS eid, SUM(ts_rank_cd(c.text_tsv, q)) AS s
        FROM chunks c, websearch_to_tsquery('english', %s) q
        WHERE c.text_tsv @@ q AND c.episode_id != %s
        GROUP BY c.episode_id ORDER BY s DESC LIMIT %s
        """,
        (query, src, k),
    ).fetchall()
    return [as_str(r["eid"]) for r in rows]


def _persist_idf(dsn: str, vectorizer) -> None:
    """Replace ``related_idf`` with the fitted vocabulary + idf weights (Tier 3 reuse)."""
    terms = vectorizer.get_feature_names_out().tolist()
    idfs = vectorizer.idf_.tolist()
    with connect(dsn) as conn:
        conn.execute("DELETE FROM related_idf")
        with conn.cursor() as cur:
            cur.executemany("INSERT INTO related_idf (term, idf) VALUES (%s, %s)", list(zip(terms, idfs)))


# ---------------------------------------------------------------------------
# Spec #46 Tier 3 — incremental update for a handful of episodes.
# ---------------------------------------------------------------------------


def update_related_for_episodes(
    dsn: str,
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

    Same contract as the SQLite version: reuses the persisted IDF model
    (``related_idf``), updates the forward rails for ``episode_ids`` plus the
    reverse rails of their candidate pools, and falls back to a full build if
    no IDF model exists yet (first run).
    """
    import numpy as np

    model = _load_idf_model(dsn, np)
    if model is None:
        return build_related_episodes(
            dsn,
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
    _ensure_episode_vectors(dsn, embedding_model_name, dim, np)

    with connect(dsn, vector=True) as conn:
        n_corpus = conn.execute(
            "SELECT COUNT(*) AS n FROM episode_vectors WHERE embedding_model = %s", (embedding_model_name,)
        ).fetchone()["n"]
        k = min(n_corpus, candidate_cap)
        cache = _EpisodeCache(conn, embedding_model_name, transform)

        seed = [e for e in dict.fromkeys(episode_ids) if cache.has(e)]
        # Forward pools + reverse expansion: every candidate of a seed
        # episode is a reverse target (its rail may now include the seed).
        pools: Dict[str, set] = {}
        affected: set = set(seed)
        for a in seed:
            pool = _candidate_ids(
                conn, embedding_model_name, a, cache.centroid(a), cache.tfidf(a), feature_names, np, k_vec=k, k_lex=k
            )
            pools[a] = pool
            affected |= pool

        entity_sets = _load_entity_sets(dsn, affected)  # ``affected`` already includes every pool member
        out: Dict[str, List[Tuple[str, float]]] = {}
        for a in affected:
            if not cache.has(a):
                continue
            pool = pools.get(a)
            if pool is None:
                pool = _candidate_ids(
                    conn,
                    embedding_model_name,
                    a,
                    cache.centroid(a),
                    cache.tfidf(a),
                    feature_names,
                    np,
                    k_vec=k,
                    k_lex=k,
                )
            out[a] = _rerank_incremental(
                a, pool, cache, entity_sets, top_n, tfidf_floor, w_tfidf, w_vector, w_entity, np
            )

    # Only episodes we actually scored are rewritten — never delete a rail we
    # couldn't recompute (e.g. a pool member that lost its centroid mid-flight).
    pairs = _write_pairs_scoped(dsn, out)
    logger.info(
        "related_incremental_complete",
        seed=len(seed),
        affected=len(affected),
        pairs=pairs,
    )
    return {"episodes": sum(1 for v in out.values() if v), "pairs": pairs}


class _EpisodeCache:
    """Lazily fetch + cache an episode's centroid and TF-IDF vector.

    Same interface as the SQLite ``_EpisodeCache`` (``_rerank_incremental``
    duck-types over it); pgvector centroids come back as numpy arrays
    directly, so no dtype/frombuffer handling is needed.
    """

    def __init__(self, conn, embedding_model_name, transform):
        self._conn = conn
        self._model = embedding_model_name
        self._transform = transform
        self._cent: Dict[str, object] = {}
        self._tfidf: Dict[str, object] = {}

    def has(self, eid) -> bool:
        return self.centroid(eid) is not None

    def centroid(self, eid):
        if eid not in self._cent:
            row = self._conn.execute(
                "SELECT centroid FROM episode_vectors WHERE episode_id = %s AND embedding_model = %s",
                (eid, self._model),
            ).fetchone()
            self._cent[eid] = row["centroid"] if row else None
        return self._cent[eid]

    def tfidf(self, eid):
        if eid not in self._tfidf:
            row = self._conn.execute(
                """
                SELECT string_agg(text, ' ' ORDER BY segment_id) AS doc
                FROM chunks WHERE episode_id = %s AND embedding_model = %s
                """,
                (eid, self._model),
            ).fetchone()
            self._tfidf[eid] = self._transform([row["doc"] or ""]) if row and row["doc"] else None
        return self._tfidf[eid]


def _load_idf_model(dsn: str, np):
    """Load persisted IDF as ``(vocab, idf, feature_names)`` or ``None`` if unbuilt."""
    with connect(dsn) as conn:
        rows = conn.execute("SELECT term, idf FROM related_idf ORDER BY term").fetchall()
    if not rows:
        return None
    terms = [r["term"] for r in rows]
    vocab = {t: i for i, t in enumerate(terms)}
    idf = np.array([r["idf"] for r in rows], dtype=np.float64)
    return vocab, idf, np.array(terms, dtype=object)


def _write_pairs_scoped(dsn: str, out: Dict[str, List[Tuple[str, float]]]) -> int:
    """Replace ``episode_related`` rows for the scored episodes only, in one txn."""
    flat = [(src, rel, rank, score) for src, ranked in out.items() for rank, (rel, score) in enumerate(ranked)]
    with connect(dsn) as conn:
        with conn.cursor() as cur:
            cur.executemany("DELETE FROM episode_related WHERE episode_id = %s", [(a,) for a in out])
            if flat:
                cur.executemany(
                    "INSERT INTO episode_related (episode_id, related_episode_id, rank, score)"
                    " VALUES (%s, %s, %s, %s)",
                    flat,
                )
    return len(flat)


def _ensure_episode_vectors(dsn: str, embedding_model_name: str, dim: int, np) -> int:
    """Materialise centroids for chunked episodes missing/stale in episode_vectors.

    PostgresChunkWriter writes the centroid at chunk-write time, so this only
    has work to do for episodes whose ``chunk_count`` drifted or that were
    migrated without a centroid. Missing centroids are computed by averaging
    the episode's ``chunks.embedding`` vectors — the exact ``centroid_blob``
    math (float64 mean, L2-normalise, float32) the SQLite builder uses.
    """
    from ..core.embedding_model import centroid_blob

    with connect(dsn, vector=True) as conn:
        # Episodes whose live chunk count doesn't match a stored centroid row.
        stale = conn.execute(
            """
            SELECT c.episode_id AS eid, COUNT(*) AS n
            FROM chunks c
            WHERE c.embedding_model = %s
            GROUP BY c.episode_id
            HAVING COUNT(*) != COALESCE(
                (SELECT v.chunk_count FROM episode_vectors v
                  WHERE v.episode_id = c.episode_id AND v.embedding_model = %s), -1)
            """,
            (embedding_model_name, embedding_model_name),
        ).fetchall()
        recomputed = 0
        for row in stale:
            eid = as_str(row["eid"])
            embeddings = [
                np.asarray(r["embedding"], dtype=np.float32).tobytes()
                for r in conn.execute(
                    "SELECT embedding FROM chunks WHERE episode_id = %s AND embedding_model = %s",
                    (eid, embedding_model_name),
                )
            ]
            centroid = centroid_blob(embeddings, dim)
            if centroid is None:
                continue
            conn.execute(
                """
                INSERT INTO episode_vectors (episode_id, embedding_model, chunk_count, centroid, computed_at)
                VALUES (%s, %s, %s, %s, now())
                ON CONFLICT (episode_id, embedding_model)
                DO UPDATE SET chunk_count = EXCLUDED.chunk_count,
                              centroid    = EXCLUDED.centroid,
                              computed_at = EXCLUDED.computed_at
                """,
                (eid, embedding_model_name, len(embeddings), np.frombuffer(centroid, dtype=np.float32)),
            )
            recomputed += 1
    if recomputed:
        logger.info("episode_vectors_backfilled", episodes=recomputed)
    return recomputed


def _load_corpus(dsn: str, embedding_model_name: str, dim: int, np):
    """Return ``(docs, centroids, eids)`` for episodes with a materialised centroid.

    Same shape as the SQLite loader: centroids come from ``episode_vectors``
    (one numpy row per episode); ``docs`` is the concatenated chunk text
    (``string_agg`` ordered by segment for a deterministic bigram stream).
    """
    with connect(dsn, vector=True) as conn:
        vec_rows = conn.execute(
            "SELECT episode_id, centroid FROM episode_vectors WHERE embedding_model = %s",
            (embedding_model_name,),
        ).fetchall()
        doc_rows = conn.execute(
            """
            SELECT episode_id AS eid, string_agg(text, ' ' ORDER BY segment_id) AS doc
            FROM chunks WHERE embedding_model = %s GROUP BY episode_id
            """,
            (embedding_model_name,),
        ).fetchall()
    docs_by_id = {as_str(r["eid"]): (r["doc"] or "") for r in doc_rows}
    eids: List[str] = []
    centroid_list = []
    for row in vec_rows:
        eid = as_str(row["episode_id"])
        if eid not in docs_by_id:
            continue  # centroid without chunks (shouldn't happen) — skip
        eids.append(eid)
        centroid_list.append(np.asarray(row["centroid"], dtype=np.float32))
    docs = {e: docs_by_id[e] for e in eids}
    centroids = np.array(centroid_list, dtype=np.float32) if centroid_list else np.zeros((0, dim), dtype=np.float32)
    return docs, centroids, eids


def _load_entity_sets(dsn: str, episode_ids=None) -> Dict[str, frozenset]:
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
        where += " AND episode_id = ANY(%s::uuid[])"
        params = [ids]
    sets: Dict[str, set] = defaultdict(set)
    with connect(dsn) as conn:
        for row in conn.execute(f"SELECT episode_id, entity_id FROM entity_mentions {where}", params):
            sets[as_str(row["episode_id"])].add(row["entity_id"])
    return {k: frozenset(v) for k, v in sets.items()}


def _write_pairs(dsn: str, rows: List[Tuple[str, str, int, float]]) -> None:
    """Replace the whole ``episode_related`` table in one transaction."""
    with connect(dsn) as conn:
        conn.execute("DELETE FROM episode_related")
        if rows:
            with conn.cursor() as cur:
                cur.executemany(
                    "INSERT INTO episode_related (episode_id, related_episode_id, rank, score)"
                    " VALUES (%s, %s, %s, %s)",
                    rows,
                )
