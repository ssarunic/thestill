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

"""Spec #28 §2.10 — sentence-transformers wrapper for chunk embedding.

Loaded lazily on the ``AppState`` mirroring ``entity_extractor`` and
``entity_resolver`` because the underlying torch model is ~470 MB on
disk + ~250 MB resident. Two callers share the warm instance:

- ``ChunkWriter.write_episode`` — embeds segments at REINDEX time.
- ``SqliteVecBackend._embed_query`` — embeds the user's query text
  at search time.

Embeddings are L2-normalised and packed as little-endian float32
bytes. The packed shape matches sqlite-vec's ``vec0`` BLOB format —
the bytes go straight into the ``chunks.embedding`` column and from
there into ``chunks_vec`` via the ``chunks_ai`` trigger.
"""

from __future__ import annotations

import struct
import threading
import time
from typing import List, Optional, Sequence

from structlog import get_logger

from ..search.base import DEFAULT_EMBEDDING_MODEL, embedding_dim_for

logger = get_logger(__name__)

# Process-side integrity guard (2026-09-29 Legora incident). Twice, a web
# process that had been embedding chunks in its worker thread while serving
# searches produced a query vector for "Legora" whose nearest neighbours
# were generic fragments at cosine distance < 0.5 — the same text embedded
# by a fresh process in the same container sat at 0.88, and every stored
# vector matched its text (``thestill chunks verify``). The model file was
# fine; the live instance was not. So: one lock around every forward pass,
# and a fixed probe sentence re-embedded at most once a minute and compared
# with the vector taken at load. Drift beyond the threshold logs an error
# and reloads the model. A healthy model reproduces the probe to ~1e-6.
PROBE_TEXT = "The quick brown fox jumps over the lazy dog while the podcast host asks why."
PROBE_INTERVAL_S = 60.0
PROBE_DRIFT_THRESHOLD = 0.01


def _cosine_distance(a, b) -> float:
    import numpy as np

    a = np.asarray(a, dtype=np.float64)
    b = np.asarray(b, dtype=np.float64)
    na, nb = float(np.linalg.norm(a)), float(np.linalg.norm(b))
    if na == 0.0 or nb == 0.0:
        return 0.0  # nothing to compare against (stub vectors); never flag
    return float(1.0 - np.dot(a, b) / (na * nb))


def centroid_blob(embeddings: Sequence[bytes], dim: int) -> Optional[bytes]:
    """Mean of packed-float32 embeddings, L2-normalised and repacked.

    ``embeddings`` are the per-chunk blobs in the ``chunks.embedding``
    layout (little-endian float32, length ``dim``). Returns the episode
    centroid in the same layout, or ``None`` if there are no embeddings
    or the mean is a zero vector (can't be normalised). Spec #46 Tier 0
    materialises this once per episode so the related-episodes builder
    never reloads per-chunk vectors.
    """
    import numpy as np

    if not embeddings:
        return None
    arr = np.frombuffer(b"".join(embeddings), dtype=np.float32).reshape(len(embeddings), dim)
    mean = arr.mean(axis=0, dtype=np.float64)  # accumulate in float64 to avoid drift
    norm = float(np.linalg.norm(mean))
    if norm == 0.0:
        return None
    return (mean / norm).astype(np.float32).tobytes()


class EmbeddingModel:
    """Lazy sentence-transformers wrapper.

    The underlying model is constructed on first ``encode_*`` call —
    instantiation is essentially free, the cost is in the first
    forward pass. ``_get_model`` is guarded by an internal lock so
    background warmup at app startup can race with an early search
    request without double-loading or corrupting the model.
    """

    def __init__(self, model_name: str = DEFAULT_EMBEDDING_MODEL):
        self.model_name = model_name
        self.dim = embedding_dim_for(model_name)
        self._model: Optional[object] = None
        self._load_lock = threading.Lock()
        # Re-entrant: ``encode_*`` holds it while ``self_check`` (which may
        # ``reload``) runs inside. Lock order is always encode → load, never
        # the reverse, so warmup + a concurrent encode cannot deadlock.
        self._encode_lock = threading.RLock()
        self._probe_reference = None
        self._probe_checked_at = 0.0
        self._loaded_at = 0.0
        self.reloads = 0
        self.last_probe_distance: Optional[float] = None

    def _get_model(self):
        # Double-checked locking: the hot path (model already loaded)
        # never touches the lock. Cold path serializes loaders so a
        # search that arrives mid-warmup waits for the warmup to
        # finish instead of kicking off a second SentenceTransformer.
        if self._model is not None:
            return self._model
        with self._load_lock:
            if self._model is None:
                from sentence_transformers import SentenceTransformer  # type: ignore[import-not-found]

                logger.info("embedding_model_loading", model=self.model_name)
                model = SentenceTransformer(self.model_name)
                # Reference taken from the freshly loaded instance, before
                # anything else has touched it.
                self._probe_reference = self._probe_vector(model)
                self._loaded_at = self._probe_checked_at = time.monotonic()
                self._model = model
                logger.info("embedding_model_loaded", model=self.model_name, dim=self.dim, reloads=self.reloads)
            return self._model

    @staticmethod
    def _probe_vector(model):
        import numpy as np

        return np.asarray(model.encode([PROBE_TEXT], normalize_embeddings=True)[0], dtype=np.float32)

    def self_check(self, *, force: bool = False) -> Optional[float]:
        """Re-embed the probe and compare with the vector taken at load.

        Returns the cosine distance (``None`` before the first check). Runs
        at most every ``PROBE_INTERVAL_S`` unless ``force``. Drift beyond
        ``PROBE_DRIFT_THRESHOLD`` logs an error and reloads the model so the
        next encode comes from a fresh instance.
        """
        with self._encode_lock:
            model = self._get_model()
            now = time.monotonic()
            if not force and now - self._probe_checked_at < PROBE_INTERVAL_S:
                return self.last_probe_distance
            distance = _cosine_distance(self._probe_vector(model), self._probe_reference)
            self._probe_checked_at = now
            self.last_probe_distance = distance
            if distance > PROBE_DRIFT_THRESHOLD:
                logger.error(
                    "embedding_model_drift_detected",
                    model=self.model_name,
                    distance=round(distance, 5),
                    threshold=PROBE_DRIFT_THRESHOLD,
                    loaded_s_ago=round(now - self._loaded_at, 1),
                    reloads=self.reloads,
                )
                self.reload()
            else:
                logger.debug("embedding_model_self_check_ok", model=self.model_name, distance=round(distance, 6))
            return distance

    def reload(self) -> None:
        """Drop the live instance and load a fresh one (new probe reference)."""
        with self._encode_lock:
            with self._load_lock:
                self._model = None
                self.reloads += 1
            self._get_model()
            logger.warning("embedding_model_reloaded", model=self.model_name, reloads=self.reloads)

    def warmup(self) -> None:
        """Force the underlying model to load now.

        Safe to call from a background thread at app startup; the
        ``_load_lock`` keeps a concurrent ``encode_one`` from racing
        the load. No-op if the model is already resident.
        """
        self._get_model()

    def encode_one(self, text: str) -> bytes:
        """Embed one string, return packed float32 little-endian bytes."""
        with self._encode_lock:
            self.self_check()
            model = self._get_model()
            vec = model.encode([text], normalize_embeddings=True)[0]
            return struct.pack(f"<{self.dim}f", *vec)

    def encode_batch(self, texts: List[str], *, batch_size: int = 64) -> List[bytes]:
        """Embed many strings, return one packed-bytes blob per input."""
        if not texts:
            return []
        with self._encode_lock:
            self.self_check()
            model = self._get_model()
            vecs = model.encode(texts, normalize_embeddings=True, batch_size=batch_size)
            return [struct.pack(f"<{self.dim}f", *v) for v in vecs]
