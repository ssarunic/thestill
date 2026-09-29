"""Hybrid fusion gate — a chunk only the semantic leg found must be close.

Pure unit test: both legs are stubbed, no database. The 2026-09-29 "Legora"
search interleaved 50 exact lexical hits with one-word fragments ("Ew.",
"Was", "Comment.") that the k-NN leg surfaced; RRF weights semantic rank n
like lexical rank n, so without the gate every even row was noise.
"""

from __future__ import annotations

from datetime import datetime, timezone

from thestill.models.entities import MatchType
from thestill.search import pgvector_client, sqlite_vec_client
from thestill.search.base import SearchMode
from thestill.search.pgvector_client import PgVectorBackend


class _Model:
    model_name = "fake"

    def encode_one(self, text: str) -> bytes:
        return b"\x00" * 4


def _row(cid: int, text: str, score: float) -> dict:
    return {
        "chunk_id": cid,
        "episode_id": "ep",
        "segment_id": cid,
        "start_ms": 0,
        "end_ms": 1000,
        "speaker": "Host",
        "text": text,
        "episode_title": "E",
        "pub_date": datetime(2026, 1, 1, tzinfo=timezone.utc),
        "podcast_id": "p",
        "podcast_title": "P",
        "score": score,
    }


def _backend(monkeypatch, lex_rows, sem_rows) -> PgVectorBackend:
    backend = PgVectorBackend(dsn="postgresql://unused/never-connected", embedding_model=_Model())  # type: ignore[arg-type]
    monkeypatch.setattr(backend, "_lexical", lambda *a, **k: lex_rows)
    monkeypatch.setattr(backend, "_semantic", lambda *a, **k: sem_rows)
    return backend


def test_far_semantic_only_rows_never_enter_the_fusion(monkeypatch):
    lex = [
        _row(1, "Host: Legora is the platform where lawyers do the work", 0.3),
        _row(2, "Guest: we compete with Legora on quality, not price", 0.2),
    ]
    # Hub fragments at the distances observed on the live corpus, plus one
    # chunk both legs agree on — agreement is never gated.
    sem = [
        _row(10, "Host: Ew.", 0.62),
        _row(11, "Guest: Was", 0.66),
        _row(2, "Guest: we compete with Legora on quality, not price", 0.45),
    ]
    hits = _backend(monkeypatch, lex, sem).search("Legora", mode=SearchMode.HYBRID, limit=10, filters=None)
    assert [h.segment_id for h in hits] == [2, 1]
    assert all(h.match_type == MatchType.HYBRID for h in hits)


def test_close_semantic_only_rows_are_kept(monkeypatch):
    lex = [_row(1, "Host: Legora is the platform where lawyers do the work", 0.3)]
    sem = [_row(20, "Guest: the legal AI vendor the firms all adopted last year", 0.35)]
    hits = _backend(monkeypatch, lex, sem).search("Legora", mode=SearchMode.HYBRID, limit=10, filters=None)
    assert {h.segment_id for h in hits} == {1, 20}


def test_gate_sits_at_or_below_the_semantic_cutoff():
    assert pgvector_client._HYBRID_SEMANTIC_ONLY_MAX_DISTANCE <= pgvector_client._SEMANTIC_MAX_DISTANCE


def test_backends_share_ranking_constants():
    """FM-6 lockstep: one concept, one number, in both backends."""
    for name in (
        "_RRF_K",
        "_HYBRID_FETCH",
        "_SEMANTIC_MAX_DISTANCE",
        "_HYBRID_SEMANTIC_ONLY_MAX_DISTANCE",
        "_MIN_SEMANTIC_TEXT_CHARS",
    ):
        assert getattr(pgvector_client, name) == getattr(sqlite_vec_client, name), name
