"""Spec #28 §2.10 — EmbeddingModel wrapper unit tests.

The real model is ~470 MB so we inject a stub SentenceTransformer
into ``sys.modules`` for the lazy-load tests. The integration smoke
test exercises the real model.
"""

from __future__ import annotations

import sys
import types
from unittest.mock import MagicMock

import pytest

np = pytest.importorskip("numpy", reason="numpy required for embedding tests")

from thestill.core.embedding_model import EmbeddingModel
from thestill.search.base import DEFAULT_EMBEDDING_MODEL


@pytest.fixture
def stub_sentence_transformers(monkeypatch):
    """Inject a fake ``sentence_transformers`` module that returns
    deterministic zero-vectors. Captures the constructor call so tests
    can verify lazy-load semantics.
    """
    inst = MagicMock()
    inst.encode.return_value = np.zeros((1, 384), dtype=np.float32)
    SentenceTransformer = MagicMock(return_value=inst)
    fake = types.ModuleType("sentence_transformers")
    fake.SentenceTransformer = SentenceTransformer  # type: ignore[attr-defined]
    monkeypatch.setitem(sys.modules, "sentence_transformers", fake)
    return SentenceTransformer, inst


class TestEmbeddingModel:
    def test_dim_resolved_from_registry(self):
        model = EmbeddingModel(DEFAULT_EMBEDDING_MODEL)
        assert model.dim == 384

    def test_unknown_model_rejected_at_construction(self):
        with pytest.raises(KeyError, match="Unknown embedding model"):
            EmbeddingModel("nonsense/no-such-model-v0")

    def test_lazy_load_first_call(self, stub_sentence_transformers):
        SentenceTransformer, inst = stub_sentence_transformers
        model = EmbeddingModel(DEFAULT_EMBEDDING_MODEL)
        assert model._model is None
        model.encode_one("hello")
        assert model._model is inst
        SentenceTransformer.assert_called_once_with(DEFAULT_EMBEDDING_MODEL)

    def test_encode_one_returns_packed_float32_bytes(self, stub_sentence_transformers):
        _, inst = stub_sentence_transformers
        inst.encode.return_value = np.array([np.arange(384, dtype=np.float32)])
        model = EmbeddingModel(DEFAULT_EMBEDDING_MODEL)
        blob = model.encode_one("hello")
        assert isinstance(blob, bytes)
        assert len(blob) == 384 * 4

    def test_encode_batch_empty_short_circuits(self, stub_sentence_transformers):
        SentenceTransformer, _ = stub_sentence_transformers
        model = EmbeddingModel(DEFAULT_EMBEDDING_MODEL)
        assert model.encode_batch([]) == []
        SentenceTransformer.assert_not_called()

    def test_encode_batch_packs_each_vector(self, stub_sentence_transformers):
        _, inst = stub_sentence_transformers
        inst.encode.return_value = np.zeros((3, 384), dtype=np.float32)
        model = EmbeddingModel(DEFAULT_EMBEDDING_MODEL)
        blobs = model.encode_batch(["a", "b", "c"])
        assert len(blobs) == 3
        assert all(len(b) == 384 * 4 for b in blobs)


def _unit(seed: int) -> np.ndarray:
    rng = np.random.default_rng(seed)
    v = rng.standard_normal(384).astype(np.float32)
    return v / np.linalg.norm(v)


@pytest.fixture
def switchable_model(monkeypatch):
    """A fake SentenceTransformer whose output can be flipped mid-life to
    simulate a live instance that no longer computes what it did at load."""
    state = {"seed": 1, "constructions": 0}

    class _Inst:
        def encode(self, texts, normalize_embeddings=True, batch_size=64):
            return np.stack([_unit(state["seed"] + hash(t) % 1000) for t in texts])

    def _ctor(name):
        state["constructions"] += 1
        return _Inst()

    fake = types.ModuleType("sentence_transformers")
    fake.SentenceTransformer = _ctor  # type: ignore[attr-defined]
    monkeypatch.setitem(sys.modules, "sentence_transformers", fake)
    return state


class TestProbeSelfCheck:
    """2026-09-29 Legora incident: a live web process produced query vectors a
    fresh process did not. The probe catches that and reloads."""

    def test_reference_taken_at_load(self, switchable_model):
        model = EmbeddingModel(DEFAULT_EMBEDDING_MODEL)
        model.encode_one("hello there")
        assert model._probe_reference is not None and model._probe_reference.shape == (384,)
        assert model.reloads == 0

    def test_consistent_instance_is_not_reloaded(self, switchable_model):
        model = EmbeddingModel(DEFAULT_EMBEDDING_MODEL)
        model.encode_one("hello there")
        assert model.self_check(force=True) == pytest.approx(0.0, abs=1e-6)
        assert model.reloads == 0
        assert switchable_model["constructions"] == 1

    def test_drift_reloads_and_takes_a_new_reference(self, switchable_model):
        from thestill.core.embedding_model import PROBE_DRIFT_THRESHOLD

        model = EmbeddingModel(DEFAULT_EMBEDDING_MODEL)
        model.encode_one("hello there")
        switchable_model["seed"] = 99  # the live instance now computes something else
        distance = model.self_check(force=True)
        assert distance > PROBE_DRIFT_THRESHOLD
        assert model.reloads == 1
        assert switchable_model["constructions"] == 2
        # The fresh instance is the new baseline: a follow-up check is clean.
        assert model.self_check(force=True) == pytest.approx(0.0, abs=1e-6)
        assert model.reloads == 1

    def test_encode_runs_the_check_only_after_the_interval(self, switchable_model):
        from thestill.core import embedding_model as em

        model = EmbeddingModel(DEFAULT_EMBEDDING_MODEL)
        model.encode_one("hello there")
        switchable_model["seed"] = 99
        model.encode_one("still inside the interval")
        assert model.reloads == 0  # not re-checked yet
        model._probe_checked_at -= em.PROBE_INTERVAL_S + 1
        model.encode_one("interval elapsed")
        assert model.reloads == 1
        assert model.last_probe_distance > em.PROBE_DRIFT_THRESHOLD

    def test_zero_vectors_never_flag(self, stub_sentence_transformers):
        # The MagicMock stub returns zeros; a zero reference is "unknown", not drift.
        model = EmbeddingModel(DEFAULT_EMBEDDING_MODEL)
        model.encode_one("hello")
        assert model.self_check(force=True) == 0.0
        assert model.reloads == 0


class TestEncodeSerialisation:
    def test_concurrent_encodes_never_overlap(self, monkeypatch):
        import threading
        import time

        overlap = {"max_inside": 0, "inside": 0}
        guard = threading.Lock()

        class _Inst:
            def encode(self, texts, normalize_embeddings=True, batch_size=64):
                with guard:
                    overlap["inside"] += 1
                    overlap["max_inside"] = max(overlap["max_inside"], overlap["inside"])
                time.sleep(0.002)
                with guard:
                    overlap["inside"] -= 1
                return np.ones((len(texts), 384), dtype=np.float32)

        fake = types.ModuleType("sentence_transformers")
        fake.SentenceTransformer = lambda name: _Inst()  # type: ignore[attr-defined]
        monkeypatch.setitem(sys.modules, "sentence_transformers", fake)

        model = EmbeddingModel(DEFAULT_EMBEDDING_MODEL)
        threads = [
            threading.Thread(
                target=lambda i=i: (model.encode_batch([f"t{i}"] * 8) if i % 2 else model.encode_one(f"q{i}"))
            )
            for i in range(16)
        ]
        for t in threads:
            t.start()
        for t in threads:
            t.join()
        assert overlap["max_inside"] == 1
