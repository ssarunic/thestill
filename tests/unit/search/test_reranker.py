"""Spec #89 Phase 2 — reranker pooling, provenance floors, and the reranked hybrid path."""

from __future__ import annotations

import sys
import types
from datetime import datetime, timezone

import pytest

from thestill.search import pgvector_client
from thestill.search.base import SearchMode
from thestill.search.pgvector_client import PgVectorBackend
from thestill.search.reranker import Reranker, pool_candidates, rerank


def _row(cid: int, text: str, speaker: str = "Host", score: float = 0.0) -> dict:
    return {
        "chunk_id": cid,
        "episode_id": "ep",
        "segment_id": cid,
        "start_ms": 0,
        "end_ms": 1000,
        "speaker": speaker,
        "text": f"{speaker}: {text}" if speaker else text,
        "episode_title": "E",
        "pub_date": datetime(2026, 1, 1, tzinfo=timezone.utc),
        "podcast_id": "p",
        "podcast_title": "P",
        "score": score,
    }


class _FixedReranker:
    """Scores by a text→score table; unknown passages score 0."""

    def __init__(self, table: dict):
        self.table = table
        self.calls = []

    def score(self, query, passages):
        self.calls.append((query, list(passages)))
        return [self.table.get(p, 0.0) for p in passages]


class TestPoolCandidates:
    def test_first_leg_names_the_origin(self):
        a = _row(1, "Legora is the platform lawyers use")
        pooled = pool_candidates([("lexical", [a]), ("semantic", [a, _row(2, "another passage with enough words")])])
        assert [(r["chunk_id"], o) for r, o in pooled] == [(1, "lexical"), (2, "semantic")]

    def test_legacy_short_rows_are_dropped_from_every_leg(self):
        # "Gary Stevenson: Yeah." scored 0.999 on the name query; the writer's
        # four-word rule applies to the bare text on every leg.
        pooled = pool_candidates(
            [
                (
                    "lexical",
                    [_row(1, "Yeah.", speaker="Gary Stevenson"), _row(2, "couple of weeks.", speaker="Gary Stevenson")],
                ),
                ("entity", [_row(3, "I think the economy is broken for young people", speaker="Gary Stevenson")]),
            ]
        )
        assert [r["chunk_id"] for r, _ in pooled] == [3]


class TestRerankFloors:
    def test_semantic_only_rows_need_the_higher_floor(self):
        lit = _row(1, "NVIDIA announced a long-term partnership today")
        sem = _row(2, "Nikolina, drago mi je da si ovdje danas")
        rr = _FixedReranker({lit["text"]: 0.16, sem["text"]: 0.20})
        out = rerank(
            rr, "nvidia", [(lit, "lexical"), (sem, "semantic")], limit=10, min_score=0.01, semantic_min_score=0.3
        )
        assert [(r["chunk_id"], o) for r, _, o in out] == [(1, "lexical")]

    def test_orders_by_score_and_truncates(self):
        rows = [_row(i, f"passage number {i} with words") for i in range(5)]
        rr = _FixedReranker({r["text"]: 0.1 * i for i, r in enumerate(rows)})
        out = rerank(rr, "q", [(r, "lexical") for r in rows], limit=2, min_score=0.0, semantic_min_score=0.0)
        assert [r["chunk_id"] for r, _, _ in out] == [4, 3]

    def test_entity_origin_counts_as_literal(self):
        e = _row(7, "the Legora product manifesto outlined three things")
        rr = _FixedReranker({e["text"]: 0.05})
        out = rerank(rr, "legora", [(e, "entity")], limit=5, min_score=0.01, semantic_min_score=0.3)
        assert [o for _, _, o in out] == ["entity"]


class TestRerankerModel:
    def test_lazy_load_and_sigmoid_scores(self, monkeypatch):
        seen = {}

        class _CE:
            def __init__(self, name, max_length, device):
                seen["init"] = (name, max_length, device)

            def predict(self, pairs, batch_size, activation_fn, convert_to_numpy):
                seen["pairs"] = pairs
                seen["activation"] = type(activation_fn).__name__
                return [0.25 for _ in pairs]

        fake = types.ModuleType("sentence_transformers")
        fake.CrossEncoder = _CE  # type: ignore[attr-defined]
        monkeypatch.setitem(sys.modules, "sentence_transformers", fake)
        # torch is only needed for the activation object; CI has no torch.
        fake_torch = types.ModuleType("torch")
        fake_torch.nn = types.SimpleNamespace(Sigmoid=type("Sigmoid", (), {}))  # type: ignore[attr-defined]
        monkeypatch.setitem(sys.modules, "torch", fake_torch)
        rr = Reranker("some/cross-encoder", max_length=128)
        assert rr._model is None
        assert rr.score("q", []) == []
        assert rr._model is None  # empty input never loads the model
        assert rr.score("legora", ["a", "b"]) == [0.25, 0.25]
        assert seen["init"] == ("some/cross-encoder", 128, "cpu")
        assert seen["pairs"] == [("legora", "a"), ("legora", "b")]
        assert seen["activation"] == "Sigmoid"


class _Model:
    model_name = "fake"

    def encode_one(self, text: str) -> bytes:
        return b"\x00" * 4


def _backend(monkeypatch, *, lex, sem, ent=None, reranker, entity_leg=False) -> PgVectorBackend:
    backend = PgVectorBackend(
        dsn="postgresql://unused/never-connected",
        embedding_model=_Model(),  # type: ignore[arg-type]
        reranker=reranker,
        rerank_pool=20,
        rerank_min_score=0.01,
        rerank_semantic_min_score=0.3,
        entity_leg=entity_leg,
    )
    monkeypatch.setattr(backend, "_lexical", lambda *a, **k: lex)
    monkeypatch.setattr(backend, "_semantic", lambda *a, **k: sem)
    calls = []

    def _ent(name, **k):
        calls.append(name)
        return ent or []

    monkeypatch.setattr(backend, "_entity_rows", _ent)
    backend._entity_calls = calls  # type: ignore[attr-defined]
    return backend


class TestRerankedHybrid:
    def test_lowercase_legora_keeps_literal_hits_and_drops_croatian_filler(self, monkeypatch):
        lex = [
            _row(1, "Legora is the platform where lawyers do the work"),
            _row(2, "we compete with Legora on quality"),
        ]
        sem = [_row(30, "Ovaj je s kraćom kosom, rekla je.", speaker="Mia Biberović")]
        rr = _FixedReranker({lex[0]["text"]: 0.9, lex[1]["text"]: 0.4, sem[0]["text"]: 0.25})
        hits = _backend(monkeypatch, lex=lex, sem=sem, reranker=rr).search(
            "legora", mode=SearchMode.HYBRID, limit=10, filters=None
        )
        assert [(h.segment_id, h.origin) for h in hits] == [(1, "lexical"), (2, "lexical")]
        assert hits[0].score == pytest.approx(0.9)
        assert rr.calls[0][0] == "legora"  # the plain query text, not the FTS expression

    def test_close_semantic_only_rows_are_kept_for_longer_questions(self, monkeypatch):
        sem = [_row(20, "the legal AI vendor the big firms all adopted last year")]
        rr = _FixedReranker({sem[0]["text"]: 0.7})
        hits = _backend(monkeypatch, lex=[], sem=sem, reranker=rr).search(
            "which legal AI vendor did firms adopt", mode=SearchMode.HYBRID, limit=10, filters=None
        )
        assert [(h.segment_id, h.origin) for h in hits] == [(20, "semantic")]

    def test_entity_leg_runs_only_when_enabled(self, monkeypatch):
        ent = [_row(9, "the Legora product manifesto outlined three things")]
        rr = _FixedReranker({ent[0]["text"]: 0.05})
        off = _backend(monkeypatch, lex=[], sem=[], ent=ent, reranker=rr)
        assert off.search("legora", mode=SearchMode.HYBRID, limit=5, filters=None) == []
        assert off._entity_calls == []  # type: ignore[attr-defined]
        on = _backend(monkeypatch, lex=[], sem=[], ent=ent, reranker=rr, entity_leg=True)
        hits = on.search("legora", mode=SearchMode.HYBRID, limit=5, filters=None)
        assert [(h.segment_id, h.origin) for h in hits] == [(9, "entity")]
        assert on._entity_calls == ["legora"]  # type: ignore[attr-defined]

    def test_without_reranker_the_gated_path_is_unchanged(self, monkeypatch):
        lex = [_row(1, "Legora is the platform where lawyers do the work", score=0.3)]
        sem = [_row(30, "Ovaj je s kraćom kosom, rekla je.", speaker="Mia Biberović", score=0.37)]
        hits = _backend(monkeypatch, lex=lex, sem=sem, reranker=None).search(
            "legora", mode=SearchMode.HYBRID, limit=10, filters=None
        )
        assert [h.segment_id for h in hits] == [1]
        assert hits[0].origin is None


def test_factory_builds_reranker_only_when_a_model_is_named():
    from types import SimpleNamespace

    from thestill.repositories.factory import _rerank_options

    off = _rerank_options(SimpleNamespace(search_reranker_model=""))
    assert off["reranker"] is None
    on = _rerank_options(
        SimpleNamespace(
            search_reranker_model="cross-encoder/mmarco-mMiniLMv2-L12-H384-v1",
            search_rerank_pool=15,
            search_rerank_min_score=0.02,
            search_rerank_semantic_min_score=0.05,
            search_entity_leg=True,
        )
    )
    assert isinstance(on["reranker"], Reranker) and on["reranker"]._model is None  # lazy: nothing loaded
    assert (on["rerank_pool"], on["rerank_min_score"], on["rerank_semantic_min_score"], on["entity_leg"]) == (
        15,
        0.02,
        0.05,
        True,
    )


def test_backends_share_rerank_defaults():
    import inspect

    from thestill.search.sqlite_vec_client import SqliteVecBackend

    pg = inspect.signature(pgvector_client.PgVectorBackend.__init__).parameters
    sq = inspect.signature(SqliteVecBackend.__init__).parameters
    for name in ("rerank_pool", "rerank_min_score", "rerank_semantic_min_score", "entity_leg"):
        assert pg[name].default == sq[name].default, name
