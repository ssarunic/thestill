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
