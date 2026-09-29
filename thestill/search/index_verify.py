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

"""Index consistency check: do the stored chunk vectors still match the model?

``thestill chunks verify`` samples rows from the chunk index, re-embeds their
text with the configured model and reports the cosine distance between the
stored and the fresh vector. A healthy index reads ~0.0. Anything larger
means the rows were embedded by a different model (or a different runtime)
than the one answering queries, and every semantic search degrades into
"hub" noise: short, generic fragments outrank exact matches — the Legora
search of 2026-09-29.

Two fresh encodings are compared: ``encode_one`` (the query path) and
``encode_batch`` (the writer path). A gap between THOSE two points at the
embedding runtime on this machine rather than at stale rows, and a backfill
would only re-write the same disagreement.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import TYPE_CHECKING, Any, List, Tuple

import numpy as np

from ..repositories.factory import uses_postgres

if TYPE_CHECKING:
    from ..utils.config import Config

# Above this cosine distance, two vectors were not produced by the same
# model. The same model on a different CPU or BLAS differs by < 1e-4.
DISAGREEMENT_THRESHOLD = 0.05


@dataclass
class VerifyReport:
    model: str
    sampled: int
    stored_vs_query_mean: float = 0.0
    stored_vs_query_max: float = 0.0
    stored_vs_batch_mean: float = 0.0
    stored_vs_batch_max: float = 0.0
    batch_vs_query_max: float = 0.0
    # (text prefix, distance) for the rows that disagree most with the query path.
    worst: List[Tuple[str, float]] = field(default_factory=list)

    @property
    def runtime_disagrees(self) -> bool:
        return self.batch_vs_query_max > DISAGREEMENT_THRESHOLD

    @property
    def index_disagrees(self) -> bool:
        return self.stored_vs_query_max > DISAGREEMENT_THRESHOLD

    @property
    def healthy(self) -> bool:
        return self.sampled > 0 and not self.runtime_disagrees and not self.index_disagrees

    @property
    def verdict(self) -> str:
        if self.sampled == 0:
            return "no rows at this model — nothing to verify"
        if self.runtime_disagrees:
            return (
                "batched and single embeddings disagree on this machine: embedding runtime bug, "
                "a backfill would not help"
            )
        if self.index_disagrees:
            return "stored vectors disagree with the model: re-embed with `thestill chunks backfill --force`"
        return "consistent"


def _sample_rows(config: "Config", model_name: str, sample: int) -> List[Tuple[str, np.ndarray]]:
    if uses_postgres(config):
        from ..utils.postgres_ext import connect as pg_connect

        with pg_connect(config.database_url, vector=True) as conn:
            rows = conn.execute(
                "SELECT text, embedding FROM chunks WHERE embedding_model = %s ORDER BY random() LIMIT %s",
                (model_name, sample),
            ).fetchall()
        return [(r["text"], np.asarray(r["embedding"], dtype=np.float32)) for r in rows]

    from ..utils.sqlite_ext import connect as sqlite_connect

    with sqlite_connect(str(config.database_path)) as conn:
        rows = conn.execute(
            "SELECT text, embedding FROM chunks WHERE embedding_model = ? ORDER BY RANDOM() LIMIT ?",
            (model_name, sample),
        ).fetchall()
    return [(r["text"], np.frombuffer(r["embedding"], dtype=np.float32)) for r in rows]


def _unit(m: np.ndarray) -> np.ndarray:
    norms = np.linalg.norm(m, axis=1, keepdims=True)
    norms[norms == 0] = 1.0
    return m / norms


def _cosine_distance(a: np.ndarray, b: np.ndarray) -> np.ndarray:
    return 1.0 - (_unit(a) * _unit(b)).sum(axis=1)


def verify_index(config: "Config", embedding_model: Any, *, sample: int = 50) -> VerifyReport:
    """Re-embed a random sample of indexed chunks and compare with what is stored."""
    rows = _sample_rows(config, embedding_model.model_name, sample)
    report = VerifyReport(model=embedding_model.model_name, sampled=len(rows))
    if not rows:
        return report
    texts = [t for t, _ in rows]
    stored = np.stack([v for _, v in rows])
    query_path = np.stack([np.frombuffer(embedding_model.encode_one(t), dtype=np.float32) for t in texts])
    writer_path = np.stack([np.frombuffer(b, dtype=np.float32) for b in embedding_model.encode_batch(texts)])

    d_stored_query = _cosine_distance(stored, query_path)
    d_stored_batch = _cosine_distance(stored, writer_path)
    d_batch_query = _cosine_distance(writer_path, query_path)

    report.stored_vs_query_mean = float(d_stored_query.mean())
    report.stored_vs_query_max = float(d_stored_query.max())
    report.stored_vs_batch_mean = float(d_stored_batch.mean())
    report.stored_vs_batch_max = float(d_stored_batch.max())
    report.batch_vs_query_max = float(d_batch_query.max())
    worst = np.argsort(-d_stored_query)[:3]
    report.worst = [(texts[i][:80], float(d_stored_query[i])) for i in worst]
    return report
