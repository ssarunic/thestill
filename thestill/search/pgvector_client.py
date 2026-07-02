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

"""Spec #44 Phase 4 — PostgreSQL + pgvector search backend.

Implements the ``SearchBackend`` Protocol with the same three modes as
``SqliteVecBackend`` (the behavioural reference — keep the two in lockstep,
spec #42 FM-6):

- ``LEXICAL``  — Postgres full-text search over the generated ``text_tsv``
  column (``websearch_to_tsquery``), ranked by ``ts_rank_cd``. Replaces
  FTS5/BM25.
- ``SEMANTIC`` — k-NN over ``chunks.embedding vector({dim})`` with the
  pgvector cosine operator ``<=>`` and the HNSW index. Replaces sqlite-vec
  ``vec0``.
- ``HYBRID``   — identical reciprocal-rank-fusion of the two legs (pure
  Python, shared constants).

Filters push into the WHERE clause exactly as in the SQLite backend. The
query translator emits FTS5 ``MATCH`` syntax; ``_fts5_to_websearch`` maps it
onto ``websearch_to_tsquery`` operators (quoted phrases and ``OR`` pass
through; ``NOT term`` becomes ``-term``).
"""

from __future__ import annotations

from dataclasses import replace
from datetime import datetime
from typing import List, Optional, Tuple

import numpy as np
from structlog import get_logger

from ..models.entities import MatchType
from ..utils.postgres_ext import as_str, connect
from .base import ResolvedHit, SearchFilters, SearchMode
from .query_translator import translate_lexical_query

if False:  # TYPE_CHECKING
    from ..core.embedding_model import EmbeddingModel

logger = get_logger(__name__)

# Shared ranking constants — SAME values as sqlite_vec_client (FM-6: one
# concept, one number; import would create a heavier coupling than repeating
# three literals with a lockstep note).
_RRF_K = 60
_HYBRID_FETCH = 50
_HYBRID_WEIGHT_LEX = 0.5
_HYBRID_WEIGHT_SEM = 0.5
_SEMANTIC_FILTER_OVERFETCH = 10
_SEMANTIC_MAX_DISTANCE = 0.85

_SELECT = """
    SELECT c.id            AS chunk_id,
           c.episode_id    AS episode_id,
           c.segment_id    AS segment_id,
           c.start_ms      AS start_ms,
           c.end_ms        AS end_ms,
           c.speaker       AS speaker,
           c.text          AS text,
           e.title         AS episode_title,
           e.pub_date      AS pub_date,
           p.id            AS podcast_id,
           p.title         AS podcast_title,
"""


def _fts5_to_websearch(fts_match: str) -> str:
    """Map the translator's FTS5 MATCH string onto websearch_to_tsquery syntax.

    FTS5 and websearch agree on quoted phrases and ``OR``; implicit AND is
    the default in both. The one divergence is negation: FTS5 spells it
    ``NOT term`` (the translator appends ``NOT (a OR b)`` tails), websearch
    spells it ``-term``. ``AND`` tokens are dropped (implicit).
    """
    out: List[str] = []
    tokens = fts_match.replace("(", " ").replace(")", " ").split()
    negate_rest = False
    for tok in tokens:
        if tok == "AND":
            continue
        if tok == "NOT":
            negate_rest = True
            continue
        if tok == "OR":
            out.append("OR" if not negate_rest else "")
            continue
        if negate_rest:
            out.append(f"-{tok.lstrip('-')}")
        else:
            out.append(tok)
    return " ".join(t for t in out if t)


def _to_vec(embedding: bytes) -> np.ndarray:
    """float32 blob (EmbeddingModel.encode_one) → numpy vector for pgvector."""
    return np.frombuffer(embedding, dtype=np.float32)


class PgVectorBackend:
    """In-process SearchBackend over the Postgres ``chunks`` index."""

    def __init__(self, *, dsn: str, embedding_model: "EmbeddingModel"):
        self.dsn = dsn
        self.embedding_model = embedding_model

    @property
    def embedding_model_name(self) -> str:
        return self.embedding_model.model_name

    # ------------------------------------------------------------------
    # SearchBackend
    # ------------------------------------------------------------------

    def search(
        self,
        query: str,
        *,
        mode: SearchMode,
        limit: int,
        filters: Optional[SearchFilters],
    ) -> List[ResolvedHit]:
        """Run a search and return ranked hits (same contract as SqliteVecBackend)."""
        translated = translate_lexical_query(query)
        effective_filters = filters or SearchFilters()
        if translated.speaker:
            effective_filters = replace(effective_filters, speaker=translated.speaker)
        if mode == SearchMode.LEXICAL:
            if not translated.fts_match:
                return []
            rows = self._lexical(translated.fts_match, limit=limit, filters=effective_filters)
            return [self._row_to_hit(r, MatchType.LEXICAL) for r in rows]
        if mode == SearchMode.SEMANTIC:
            query_embedding = self.embedding_model.encode_one(translated.embedding_text)
            rows = self._semantic(query_embedding, limit=limit, filters=effective_filters)
            return [self._row_to_hit(r, MatchType.SEMANTIC) for r in rows][:limit]
        if mode == SearchMode.HYBRID:
            query_embedding = self.embedding_model.encode_one(translated.embedding_text)
            return self._hybrid(
                translated.fts_match,
                query_embedding,
                limit=limit,
                filters=effective_filters,
            )
        raise ValueError(f"unknown SearchMode: {mode!r}")

    # ------------------------------------------------------------------
    # Internals
    # ------------------------------------------------------------------

    def _filter_clauses(self, filters: Optional[SearchFilters]) -> Tuple[str, list]:
        """AND-prefixed WHERE fragment + params. Mirrors the SQLite backend."""
        if filters is None:
            return "", []
        parts: List[str] = []
        params: List = []
        if filters.podcast_id:
            parts.append("p.id = %s")
            params.append(filters.podcast_id)
        if filters.date_from:
            parts.append("e.pub_date >= %s")
            params.append(filters.date_from)
        if filters.date_to:
            parts.append("e.pub_date <= %s")
            params.append(filters.date_to)
        for entity_id in filters.has_entity:
            parts.append(
                "EXISTS (SELECT 1 FROM entity_mentions em WHERE em.episode_id = c.episode_id AND em.entity_id = %s)"
            )
            params.append(entity_id)
        if filters.speaker:
            # Same substring semantics as SQLite's LOWER(...) LIKE.
            parts.append("c.speaker ILIKE %s")
            params.append(f"%{filters.speaker}%")
        if not parts:
            return "", []
        return " AND " + " AND ".join(parts), params

    def _lexical(self, query: str, *, limit: int, filters: Optional[SearchFilters]) -> List[dict]:
        filter_sql, filter_params = self._filter_clauses(filters)
        websearch = _fts5_to_websearch(query)
        if not websearch.strip():
            return []
        sql = f"""
            {_SELECT}
                   ts_rank_cd(c.text_tsv, websearch_to_tsquery('english', %s)) AS score
            FROM chunks c
            JOIN episodes e ON e.id = c.episode_id
            JOIN podcasts p ON p.id = e.podcast_id
            WHERE c.text_tsv @@ websearch_to_tsquery('english', %s)
              AND c.embedding_model = %s
              {filter_sql}
            ORDER BY score DESC
            LIMIT %s
        """
        params = [websearch, websearch, self.embedding_model_name, *filter_params, limit]
        with connect(self.dsn) as conn:
            return conn.execute(sql, params).fetchall()

    def _semantic(self, query_embedding: bytes, *, limit: int, filters: Optional[SearchFilters]) -> List[dict]:
        filter_sql, filter_params = self._filter_clauses(filters)
        # HNSW scans apply WHERE during traversal (pgvector iterative scans),
        # but keep the SQLite backend's over-fetch so tightly-filtered result
        # sets still fill ``limit`` — identical downstream behaviour.
        knn_k = limit * _SEMANTIC_FILTER_OVERFETCH if filter_sql else limit
        sql = f"""
            {_SELECT}
                   (c.embedding <=> %s) AS score
            FROM chunks c
            JOIN episodes e ON e.id = c.episode_id
            JOIN podcasts p ON p.id = e.podcast_id
            WHERE c.embedding_model = %s
              {filter_sql}
            ORDER BY c.embedding <=> %s
            LIMIT %s
        """
        qvec = _to_vec(query_embedding)
        params = [qvec, self.embedding_model_name, *filter_params, qvec, knn_k]
        with connect(self.dsn, vector=True) as conn:
            rows = conn.execute(sql, params).fetchall()
        # Same noise cutoff as the SQLite backend (cosine distance).
        return [r for r in rows if r["score"] <= _SEMANTIC_MAX_DISTANCE]

    def _hybrid(
        self,
        query: str,
        query_embedding: bytes,
        *,
        limit: int,
        filters: Optional[SearchFilters],
    ) -> List[ResolvedHit]:
        lex_rows = self._lexical(query, limit=_HYBRID_FETCH, filters=filters) if query else []
        sem_rows = self._semantic(query_embedding, limit=_HYBRID_FETCH, filters=filters)

        scores: dict[int, float] = {}
        rows_by_id: dict[int, dict] = {}
        for rank, row in enumerate(lex_rows):
            cid = row["chunk_id"]
            scores[cid] = scores.get(cid, 0.0) + _HYBRID_WEIGHT_LEX / (_RRF_K + rank + 1)
            rows_by_id[cid] = row
        for rank, row in enumerate(sem_rows):
            cid = row["chunk_id"]
            scores[cid] = scores.get(cid, 0.0) + _HYBRID_WEIGHT_SEM / (_RRF_K + rank + 1)
            rows_by_id.setdefault(cid, row)

        ranked_ids = sorted(scores, key=scores.__getitem__, reverse=True)[:limit]
        return [self._row_to_hit(rows_by_id[cid], MatchType.HYBRID, override_score=scores[cid]) for cid in ranked_ids]

    def _row_to_hit(
        self,
        row: dict,
        match_type: MatchType,
        *,
        override_score: Optional[float] = None,
    ) -> ResolvedHit:
        score = override_score if override_score is not None else float(row["score"])
        pub_date = row["pub_date"]
        return ResolvedHit(
            episode_id=as_str(row["episode_id"]),
            podcast_id=as_str(row["podcast_id"]),
            podcast_title=row["podcast_title"],
            episode_title=row["episode_title"],
            published_at=pub_date if isinstance(pub_date, datetime) else None,
            segment_id=row["segment_id"],
            start_ms=row["start_ms"],
            end_ms=row["end_ms"],
            speaker=row["speaker"],
            text=row["text"],
            score=score,
            match_type=match_type,
        )
