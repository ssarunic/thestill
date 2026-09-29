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

"""Spec #28 §2.10.4 — in-process SQLite + sqlite-vec search backend.

Implements the ``SearchBackend`` Protocol with three modes:

- ``LEXICAL`` — FTS5 BM25 over ``chunks_fts``.
- ``SEMANTIC`` — k-NN over ``chunks_vec`` via ``vec_distance_cosine``.
- ``HYBRID`` — reciprocal-rank-fusion of the two top-K lists, K=50,
  weighted 0.5/0.5.

All three push the same ``SearchFilters`` (podcast_id, date_range,
has_entity[]) into the WHERE clause; no fetch-then-filter in Python.
The metadata join (episodes + podcasts) happens in the same query
that picks the candidates — one round-trip for lexical/semantic, two
for hybrid (one per leg) plus a final metadata fetch.
"""

from __future__ import annotations

import sqlite3
from contextlib import contextmanager
from dataclasses import replace
from datetime import datetime
from pathlib import Path
from typing import Dict, List, Optional, Sequence, Tuple

from structlog import get_logger

from ..models.entities import MatchType
from ..utils.sqlite_ext import load_vec_extension
from .base import ResolvedHit, SearchFilters, SearchMode, cap_per_episode
from .query_translator import translate_lexical_query

if False:  # TYPE_CHECKING
    from ..core.embedding_model import EmbeddingModel
    from .reranker import Reranker

logger = get_logger(__name__)


# Reciprocal-rank-fusion constant. 60 is the value popularised by the
# Cormack-Clarke-Buettcher paper; lower values weight top ranks more
# aggressively, higher values flatten the curve. K=60 is a robust
# default across text retrieval benchmarks.
_RRF_K = 60

# How many candidates to fetch from each hybrid leg before fusion.
# Spec §2.10.4 default.
_HYBRID_FETCH = 50

# Default RRF leg weights (lex, sem). Sum doesn't need to be 1; ratios
# are what matter. 0.5/0.5 is the spec default.
_HYBRID_WEIGHT_LEX = 0.5
_HYBRID_WEIGHT_SEM = 0.5

# Over-fetch factor for semantic queries. sqlite-vec's vec0 applies the
# ``k`` cap during the ANN scan, BEFORE any JOINed filters run — so a
# filter on top of ``k=N`` can yield far fewer than N rows. Over-fetching
# by 10× and post-filtering keeps the result set close to the requested
# ``limit``. Every semantic query is filtered now (the text-length floor
# below), so this always applies.
_SEMANTIC_FILTER_OVERFETCH = 10

# Maximum cosine distance admitted as a real semantic match. Without
# this, hybrid mode renders pure noise as legitimate matches when the
# corpus has nothing relevant. The May 2026 calibration (0.85) predates
# the multilingual MiniLM index; re-measured 2026-09-29 on it, genuine
# hits sit at d≈0.11-0.35 at rank 1 and ≤0.54 at rank 50, gibberish
# starts at d≈0.51. Same value as pgvector_client (lockstep).
_SEMANTIC_MAX_DISTANCE = 0.55

# A chunk the lexical leg did NOT find needs to be at least this close to
# enter the hybrid fusion. RRF gives semantic rank n the same weight as
# lexical rank n, so without this gate a query with 50 exact lexical hits
# still interleaves them with the semantic leg's nearest "hub" fragments
# (the Legora search of 2026-09-29: every even row was "Ew.", "Was",
# "Comment."). Chunks both legs found are never gated — agreement is the
# strongest signal we have.
_HYBRID_SEMANTIC_ONLY_MAX_DISTANCE = 0.5

# Query-time floor on chunk text length for the semantic leg. The chunk
# writers now skip segments under ``MIN_CHUNK_WORDS`` words, but rows
# written before that rule stay in the index until a ``chunks backfill
# --force``; this keeps them out of k-NN results meanwhile. Measured on the
# full text (speaker prefix included).
_MIN_SEMANTIC_TEXT_CHARS = 30

# Keyword queries (at most this many words) get NO semantic-only rows in the
# hybrid fusion. A one- or two-word query is a
# name lookup, and the embedding of a rare name is tokenizer noise: the
# multilingual MiniLM splits "Legora" into "Lego"+"ra" but "legora" into
# "le"+"gora" ("mountain" in Croatian), which lands 0.37 from short Croatian
# sentences and 0.95 from the real Legora quotes (2026-09-29 incident: every
# other row of a lowercase "legora" search was Netokracija filler, and a typo
# like "legoraaaa" returned nothing BUT filler). Semantic still re-ranks the
# chunks the lexical leg found; a keyword that matches nothing literally
# returns nothing, which is the honest answer.
_SHORT_QUERY_MAX_WORDS = 2


def _cap(items, filters: Optional[SearchFilters], *, episode_of=lambda row: row["episode_id"]):
    """``cap_per_episode`` with the cap read off ``filters``."""
    return cap_per_episode(items, filters.max_per_episode if filters else None, episode_of)


def _ranked_with_episode_cap(sql: str, params: list, filters: Optional[SearchFilters], limit: int) -> Tuple[str, list]:
    """Order ``sql`` (a SELECT with a ``score`` column, higher is better) best-
    first and LIMIT it, keeping at most ``filters.max_per_episode`` rows per
    episode when that's set. Same SQL shape as pgvector_client's twin."""
    cap = filters.max_per_episode if filters else None
    if cap is None:
        return f"{sql} ORDER BY score DESC LIMIT ?", [*params, limit]
    wrapped = f"""
        SELECT * FROM (
            SELECT hit.*, ROW_NUMBER() OVER (PARTITION BY episode_id ORDER BY score DESC) AS episode_rank
            FROM ({sql}) AS hit
        ) AS ranked
        WHERE episode_rank <= ?
        ORDER BY score DESC
        LIMIT ?
    """
    return wrapped, [*params, cap, limit]


class SqliteVecBackend:
    """In-process SearchBackend over the ``chunks`` index.

    Constructor is cheap (stores db_path + holds the embedding-model
    wrapper). The wrapper itself defers loading sentence-transformers
    until the first ``encode_one`` call, so LEXICAL-only callers
    never pay the model load cost even though the wrapper is held.
    """

    def __init__(
        self,
        *,
        db_path: str,
        embedding_model: "EmbeddingModel",
        reranker: Optional["Reranker"] = None,
        rerank_pool: int = 20,
        rerank_min_score: float = 0.01,
        rerank_semantic_min_score: float = 0.05,
        entity_leg: bool = False,
    ):
        self.db_path = Path(db_path)
        self.embedding_model = embedding_model
        # Spec #89 — same contract as PgVectorBackend (lockstep).
        self.reranker = reranker
        self.rerank_pool = rerank_pool
        self.rerank_min_score = rerank_min_score
        self.rerank_semantic_min_score = rerank_semantic_min_score
        self.entity_leg = entity_leg

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
        """Run a search and return ranked hits.

        The query is run through ``translate_lexical_query`` so that
        spec #28 §O2 operators (``-term``, ``speaker:foo``, ``AND``,
        ``OR``, quoted phrases) are honoured without raising FTS5
        ``OperationalError``.
        """
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
            rows = _cap(self._semantic(query_embedding, limit=limit, filters=effective_filters), effective_filters)
            return [self._row_to_hit(r, MatchType.SEMANTIC) for r in rows][:limit]
        if mode == SearchMode.HYBRID:
            query_embedding = self.embedding_model.encode_one(translated.embedding_text)
            if self.reranker is not None:
                return self._reranked(translated, query_embedding, limit=limit, filters=effective_filters)
            return self._hybrid(
                translated.fts_match,
                query_embedding,
                limit=limit,
                filters=effective_filters,
                keyword_query=len(translated.embedding_text.split()) <= _SHORT_QUERY_MAX_WORDS,
            )
        raise ValueError(f"unknown SearchMode: {mode!r}")

    def count_lexical_matches(
        self,
        query: str,
        episode_ids: Sequence[str],
        filters: Optional[SearchFilters],
    ) -> Dict[str, int]:
        translated = translate_lexical_query(query)
        if not translated.fts_match or not episode_ids:
            return {}
        effective_filters = filters or SearchFilters()
        if translated.speaker:
            effective_filters = replace(effective_filters, speaker=translated.speaker)
        filter_sql, filter_params = self._filter_clauses(replace(effective_filters, max_per_episode=None))
        placeholders = ", ".join("?" for _ in episode_ids)
        sql = f"""
            SELECT c.episode_id AS episode_id, COUNT(*) AS n
            FROM chunks_fts
            JOIN chunks   c ON c.id = chunks_fts.rowid
            JOIN episodes e ON e.id = c.episode_id
            JOIN podcasts p ON p.id = e.podcast_id
            WHERE chunks_fts MATCH ?
              AND +c.embedding_model = ?
              AND c.episode_id IN ({placeholders})
              {filter_sql}
            GROUP BY c.episode_id
        """
        params = [translated.fts_match, self.embedding_model_name, *episode_ids, *filter_params]
        with self._get_connection() as conn:
            return {row["episode_id"]: int(row["n"]) for row in conn.execute(sql, params).fetchall()}

    # ------------------------------------------------------------------
    # Internals
    # ------------------------------------------------------------------

    @contextmanager
    def _get_connection(self):
        conn = sqlite3.connect(str(self.db_path))
        conn.row_factory = sqlite3.Row
        load_vec_extension(conn)
        conn.execute("PRAGMA foreign_keys = ON")
        conn.execute("PRAGMA journal_mode = WAL")
        conn.execute("PRAGMA busy_timeout = 5000")
        try:
            yield conn
        finally:
            conn.close()

    def _filter_clauses(self, filters: Optional[SearchFilters]) -> Tuple[str, list]:
        """Build the AND-prefixed WHERE fragment + params for a filter."""
        if filters is None:
            return "", []
        parts: List[str] = []
        params: List = []
        if filters.podcast_id:
            parts.append("p.id = ?")
            params.append(filters.podcast_id)
        if filters.episode_id:
            parts.append("c.episode_id = ?")
            params.append(filters.episode_id)
        if filters.date_from:
            parts.append("e.pub_date >= ?")
            params.append(filters.date_from)
        if filters.date_to:
            parts.append("e.pub_date <= ?")
            params.append(filters.date_to)
        for entity_id in filters.has_entity:
            parts.append(
                "EXISTS (SELECT 1 FROM entity_mentions em " "WHERE em.episode_id = c.episode_id AND em.entity_id = ?)"
            )
            params.append(entity_id)
        if filters.speaker:
            # Diarisation labels vary per podcast — a substring match
            # is the right shape (matches "Friedberg", "David Friedberg",
            # "DAVID FRIEDBERG"). Indexed scan is fine; chunks per
            # episode is small.
            parts.append("LOWER(c.speaker) LIKE ?")
            params.append(f"%{filters.speaker.lower()}%")
        if not parts:
            return "", []
        return " AND " + " AND ".join(parts), params

    def _lexical(self, query: str, *, limit: int, filters: Optional[SearchFilters]) -> List[sqlite3.Row]:
        # The leading ``+`` on c.embedding_model deopts ``idx_chunks_model``.
        # Without it SQLite drives the join from chunks (filtered by model,
        # which today matches every row) and probes FTS by rowid — a 100k+
        # row scan that takes 2-36s. The deopt forces FTS-first; ~2ms.
        filter_sql, filter_params = self._filter_clauses(filters)
        sql = f"""
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
                   -bm25(chunks_fts) AS score
            FROM chunks_fts
            JOIN chunks   c ON c.id = chunks_fts.rowid
            JOIN episodes e ON e.id = c.episode_id
            JOIN podcasts p ON p.id = e.podcast_id
            WHERE chunks_fts MATCH ?
              AND +c.embedding_model = ?
              {filter_sql}
        """
        params = [query, self.embedding_model_name, *filter_params]
        sql, params = _ranked_with_episode_cap(sql, params, filters, limit)
        with self._get_connection() as conn:
            return list(conn.execute(sql, params).fetchall())

    def _semantic(self, query_embedding: bytes, *, limit: int, filters: Optional[SearchFilters]) -> List[sqlite3.Row]:
        filter_sql, filter_params = self._filter_clauses(filters)
        # vec0 applies ``k`` during the ANN scan, before the JOINed
        # filters run. The text-length floor is always a filter now, so
        # always over-fetch to keep the post-filtered result set close
        # to ``limit``. Caller truncates back to ``limit``.
        knn_k = limit * _SEMANTIC_FILTER_OVERFETCH
        sql = f"""
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
                   v.distance      AS score
            FROM chunks_vec v
            JOIN chunks   c ON c.id = v.rowid
            JOIN episodes e ON e.id = c.episode_id
            JOIN podcasts p ON p.id = e.podcast_id
            WHERE v.embedding MATCH ?
              AND k = ?
              AND +c.embedding_model = ?
              AND length(c.text) >= ?
              {filter_sql}
            ORDER BY v.distance ASC
        """
        params = [query_embedding, knn_k, self.embedding_model_name, _MIN_SEMANTIC_TEXT_CHARS, *filter_params]
        with self._get_connection() as conn:
            rows = conn.execute(sql, params).fetchall()
        # Drop noise hits before returning. Filtering here (not in SQL
        # via ``v.distance < ?``) is intentional: vec0 doesn't push
        # range predicates into the kNN scan, so the WHERE form would
        # still fetch ``k`` rows then filter — same result, slightly
        # less obvious. Doing it in Python keeps the SQL minimal.
        return [r for r in rows if r["score"] <= _SEMANTIC_MAX_DISTANCE]

    def _hybrid(
        self,
        query: str,
        query_embedding: bytes,
        *,
        limit: int,
        filters: Optional[SearchFilters],
        keyword_query: bool = False,
    ) -> List[ResolvedHit]:
        # Operator-only or speaker-only inputs leave the FTS expression
        # empty — semantic still has the cleaned text to work with.
        lex_rows = self._lexical(query, limit=_HYBRID_FETCH, filters=filters) if query else []
        sem_rows = self._semantic(query_embedding, limit=_HYBRID_FETCH, filters=filters)

        lex_ids = {row["chunk_id"] for row in lex_rows}
        semantic_only_allowed = not keyword_query
        sem_rows = [
            row
            for row in sem_rows
            if row["chunk_id"] in lex_ids
            or (semantic_only_allowed and float(row["score"]) <= _HYBRID_SEMANTIC_ONLY_MAX_DISTANCE)
        ]
        sem_rows = _cap(sem_rows, filters)

        scores: dict[int, float] = {}
        rows_by_id: dict[int, sqlite3.Row] = {}
        for rank, row in enumerate(lex_rows):
            cid = row["chunk_id"]
            scores[cid] = scores.get(cid, 0.0) + _HYBRID_WEIGHT_LEX / (_RRF_K + rank + 1)
            rows_by_id[cid] = row
        for rank, row in enumerate(sem_rows):
            cid = row["chunk_id"]
            scores[cid] = scores.get(cid, 0.0) + _HYBRID_WEIGHT_SEM / (_RRF_K + rank + 1)
            rows_by_id.setdefault(cid, row)  # only fall back if lex didn't have it

        ranked_ids = sorted(scores, key=scores.__getitem__, reverse=True)
        ranked_ids = _cap(ranked_ids, filters, episode_of=lambda cid: rows_by_id[cid]["episode_id"])[:limit]
        return [self._row_to_hit(rows_by_id[cid], MatchType.HYBRID, override_score=scores[cid]) for cid in ranked_ids]

    def _reranked(self, translated, query_embedding: bytes, *, limit: int, filters) -> List[ResolvedHit]:
        from .reranker import pool_candidates, rerank

        pool = self.rerank_pool
        lex = self._lexical(translated.fts_match, limit=pool, filters=filters) if translated.fts_match else []
        ent = self._entity_rows(translated.embedding_text, limit=pool, filters=filters) if self.entity_leg else []
        sem = _cap(self._semantic(query_embedding, limit=pool, filters=filters), filters)[:pool]
        candidates = pool_candidates([("lexical", lex), ("entity", _cap(ent, filters)), ("semantic", sem)])
        ranked = rerank(
            self.reranker,
            translated.embedding_text,
            candidates,
            limit=len(candidates),
            min_score=self.rerank_min_score,
            semantic_min_score=self.rerank_semantic_min_score,
        )
        ranked = _cap(ranked, filters, episode_of=lambda t: t[0]["episode_id"])[:limit]
        return [
            replace(self._row_to_hit(row, MatchType.HYBRID, override_score=score), origin=origin)
            for row, score, origin in ranked
        ]

    def _entity_rows(self, name: str, *, limit: int, filters: Optional[SearchFilters]) -> List[sqlite3.Row]:
        """Same contract as PgVectorBackend._entity_rows."""
        name = name.strip()
        if not name:
            return []
        filter_sql, filter_params = self._filter_clauses(filters)
        sql = f"""
            WITH hit AS (
                SELECT episode_id, segment_id FROM entity_mentions WHERE lower(surface_form) = lower(?)
                UNION
                SELECT m.episode_id, m.segment_id FROM entity_mentions m
                WHERE m.entity_id IN (SELECT id FROM entities WHERE lower(canonical_name) = lower(?))
            )
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
                   0.0             AS score
            FROM hit
            JOIN chunks   c ON c.episode_id = hit.episode_id AND c.segment_id = hit.segment_id
            JOIN episodes e ON e.id = c.episode_id
            JOIN podcasts p ON p.id = e.podcast_id
            WHERE c.embedding_model = ?
              {filter_sql}
            ORDER BY e.pub_date DESC
            LIMIT ?
        """
        params = [name, name, self.embedding_model_name, *filter_params, limit]
        with self._get_connection() as conn:
            return list(conn.execute(sql, params).fetchall())

    def _row_to_hit(
        self,
        row: sqlite3.Row,
        match_type: MatchType,
        *,
        override_score: Optional[float] = None,
    ) -> ResolvedHit:
        score = override_score if override_score is not None else float(row["score"])
        return ResolvedHit(
            episode_id=row["episode_id"],
            podcast_id=row["podcast_id"],
            podcast_title=row["podcast_title"],
            episode_title=row["episode_title"],
            published_at=_parse_datetime(row["pub_date"]),
            segment_id=row["segment_id"],
            start_ms=row["start_ms"],
            end_ms=row["end_ms"],
            speaker=row["speaker"],
            text=row["text"],
            score=score,
            match_type=match_type,
        )


def _parse_datetime(value) -> Optional[datetime]:
    """Parse the ``episodes.pub_date`` column to a tz-aware datetime.

    The column is ``TIMESTAMP`` in SQLite (stored as ISO-8601 string).
    Returns ``None`` for nulls or unparseable values rather than
    raising — search results without a usable date still surface, just
    without a timestamp filter handle.
    """
    if value is None or value == "":
        return None
    if isinstance(value, datetime):
        return value
    try:
        return datetime.fromisoformat(str(value))
    except (TypeError, ValueError):
        return None
