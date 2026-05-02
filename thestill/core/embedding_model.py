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
from typing import List, Optional

from structlog import get_logger

from ..search.base import DEFAULT_EMBEDDING_MODEL, embedding_dim_for

logger = get_logger(__name__)


class EmbeddingModel:
    """Lazy sentence-transformers wrapper.

    The underlying model is constructed on first ``encode_*`` call —
    instantiation is essentially free, the cost is in the first
    forward pass. Construction is not thread-safe; the caller is
    expected to wrap creation in a lock at the AppState layer (the
    same pattern as ``_get_or_create_entity_extractor``).
    """

    def __init__(self, model_name: str = DEFAULT_EMBEDDING_MODEL):
        self.model_name = model_name
        self.dim = embedding_dim_for(model_name)
        self._model: Optional[object] = None

    def _get_model(self):
        if self._model is None:
            from sentence_transformers import SentenceTransformer  # type: ignore[import-not-found]

            logger.info("embedding_model_loading", model=self.model_name)
            self._model = SentenceTransformer(self.model_name)
            logger.info("embedding_model_loaded", model=self.model_name, dim=self.dim)
        return self._model

    def encode_one(self, text: str) -> bytes:
        """Embed one string, return packed float32 little-endian bytes."""
        model = self._get_model()
        vec = model.encode([text], normalize_embeddings=True)[0]
        return struct.pack(f"<{self.dim}f", *vec)

    def encode_batch(self, texts: List[str], *, batch_size: int = 64) -> List[bytes]:
        """Embed many strings, return one packed-bytes blob per input."""
        if not texts:
            return []
        model = self._get_model()
        vecs = model.encode(texts, normalize_embeddings=True, batch_size=batch_size)
        return [struct.pack(f"<{self.dim}f", *v) for v in vecs]
