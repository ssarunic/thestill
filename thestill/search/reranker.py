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

"""Spec #89 Phase 2 — cross-encoder reranking for hybrid search.

The bi-encoder legs compare two vectors computed apart; a cross-encoder reads
the query and the passage together and scores the pair. That is what tells
"legora" (tokenised le+gora) from a Croatian sentence, which no distance
threshold could (2026-09-29).

Hybrid-with-reranker pools candidates from every leg, has the cross-encoder
score them, drops those under a floor, and returns the rest by score. The
legs only have to be broad; the reranker has to be right.
"""

from __future__ import annotations

import threading
from typing import Iterable, List, Optional, Sequence, Tuple, TypeVar

from structlog import get_logger

logger = get_logger(__name__)

Row = TypeVar("Row")


class Reranker:
    """Lazy, lock-guarded ``sentence_transformers.CrossEncoder`` wrapper.

    Scores are sigmoid-activated, so they are comparable to a fixed floor.
    One lock around load and predict: inference on one shared instance is
    serialised, as for the embedding model (#277).
    """

    def __init__(self, model_name: str, *, max_length: int = 256, batch_size: int = 32):
        self.model_name = model_name
        self.max_length = max_length
        self.batch_size = batch_size
        self._model: Optional[object] = None
        self._lock = threading.Lock()

    def _get_model(self):
        if self._model is None:
            from sentence_transformers import CrossEncoder  # type: ignore[import-not-found]

            logger.info("reranker_loading", model=self.model_name)
            self._model = CrossEncoder(self.model_name, max_length=self.max_length, device="cpu")
            logger.info("reranker_loaded", model=self.model_name)
        return self._model

    def warmup(self) -> None:
        with self._lock:
            self._get_model()

    def score(self, query: str, passages: Sequence[str]) -> List[float]:
        if not passages:
            return []
        import torch

        with self._lock:
            model = self._get_model()
            scores = model.predict(
                [(query, p) for p in passages],
                batch_size=self.batch_size,
                activation_fn=torch.nn.Sigmoid(),
                convert_to_numpy=True,
            )
        return [float(s) for s in scores]


# Provenance is evidence. A candidate that literally contains the query
# (lexical leg) or was said as a mention of it (entity leg) only has to avoid
# being clearly unrelated; one found only by vector similarity has to earn its
# place. On the golden set the multilingual cross-encoder scores genuine name
# mentions as low as 0.16 while semantic-only filler reached 0.20, so one
# floor could not separate them.
LITERAL_ORIGINS = ("lexical", "entity")


def _bare_text(row) -> str:
    """Chunk text without its ``Speaker: `` prefix."""
    text, speaker = row["text"], row["speaker"]
    prefix = f"{speaker}: " if speaker else ""
    return text[len(prefix) :] if prefix and text.startswith(prefix) else text


def pool_candidates(legs: Sequence[Tuple[str, Iterable[Row]]]) -> List[Tuple[Row, str]]:
    """Union of ``(origin, rows)`` legs as ``(row, origin)``; the first leg to
    produce a chunk names its origin, so pass the literal legs first. Rows
    whose bare text is under the writer's word minimum are dropped: legacy
    one-word chunks ("Gary Stevenson: Yeah.") reach the pool through the
    lexical leg until a forced backfill removes them."""
    from ..core.chunk_writer import is_indexable_segment_text

    seen = set()
    out: List[Tuple[Row, str]] = []
    for origin, rows in legs:
        for row in rows:
            k = row["chunk_id"]
            if k in seen or not is_indexable_segment_text(_bare_text(row)):
                continue
            seen.add(k)
            out.append((row, origin))
    return out


def rerank(
    reranker: Reranker,
    query: str,
    candidates: Sequence[Tuple[Row, str]],
    *,
    limit: int,
    min_score: float,
    semantic_min_score: float,
) -> List[Tuple[Row, float, str]]:
    """Score every candidate; keep literal-origin rows at ``min_score`` and
    semantic-only rows at ``semantic_min_score``; best first, top ``limit``."""
    scores = reranker.score(query, [row["text"] for row, _ in candidates])
    kept = [
        (row, score, origin)
        for (row, origin), score in zip(candidates, scores)
        if score >= (min_score if origin in LITERAL_ORIGINS else semantic_min_score)
    ]
    kept.sort(key=lambda t: t[1], reverse=True)
    return kept[:limit]
