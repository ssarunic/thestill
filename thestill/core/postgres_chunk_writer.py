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

"""Spec #44 Phase 4 — Postgres chunk writer (pgvector).

Port of ``ChunkWriter`` (core/chunk_writer.py — the behavioural reference;
keep in lockstep, FM-6). The SQLite version relies on the ``chunks_ai``
trigger to fan writes into the ``chunks_vec``/``chunks_fts`` mirror tables;
in Postgres there is nothing to fan out — ``chunks.embedding`` IS the vector
(HNSW-indexed) and ``text_tsv`` is a generated column. Inserts are therefore
plain rows.

Same idempotence contract: UNIQUE (episode_id, segment_id, embedding_model)
with ``ON CONFLICT DO NOTHING``; ``force=True`` deletes the (episode, model)
rows first. Same sanitization defense (FM-7) and same centroid materialisation
(spec #46 Tier 0).
"""

from __future__ import annotations

import numpy as np
from structlog import get_logger

from ..models.annotated_transcript import AnnotatedTranscript
from ..utils.postgres_ext import connect
from ..utils.text_sanitizer import sanitize_text
from .chunk_writer import _segment_text
from .embedding_model import EmbeddingModel, centroid_blob

logger = get_logger(__name__)


class PostgresChunkWriter:
    """Embed a transcript's content segments into the Postgres ``chunks`` table."""

    def __init__(self, *, dsn: str, embedding_model: EmbeddingModel):
        self.dsn = dsn
        self.embedding_model = embedding_model

    def write_episode(
        self,
        episode_id: str,
        transcript: AnnotatedTranscript,
        *,
        force: bool = False,
    ) -> int:
        """Embed and insert chunks for one episode. Same contract as ChunkWriter."""
        content_segs = [s for s in transcript.segments if s.kind == "content" and s.text.strip()]
        if not content_segs:
            logger.info("chunk_write_no_content_segments", episode_id=episode_id)
            return 0

        model_name = self.embedding_model.model_name

        with connect(self.dsn) as conn:
            existing = conn.execute(
                "SELECT COUNT(*) AS n FROM chunks WHERE episode_id = %s AND embedding_model = %s",
                (episode_id, model_name),
            ).fetchone()["n"]
        if existing and not force:
            logger.info(
                "chunk_write_skipped_exists",
                episode_id=episode_id,
                model=model_name,
                existing_rows=existing,
            )
            return 0

        # FM-7 defense-in-depth: same sanitization as the SQLite writer.
        texts = []
        stripped_total = 0
        for seg in content_segs:
            clean, removed = sanitize_text(_segment_text(seg.speaker, seg.text))
            stripped_total += removed
            texts.append(clean)
        if stripped_total:
            logger.warning(
                "chunk_text_control_chars_stripped",
                episode_id=episode_id,
                removed=stripped_total,
            )

        # Embedding compute stays OUTSIDE the transaction (dominates wall
        # time; identical to the SQLite writer's rationale).
        embeddings = self.embedding_model.encode_batch(texts)

        with connect(self.dsn, vector=True) as conn:
            if force and existing:
                conn.execute(
                    "DELETE FROM chunks WHERE episode_id = %s AND embedding_model = %s",
                    (episode_id, model_name),
                )
            inserted = 0
            with conn.cursor() as cur:
                for seg, text, embedding in zip(content_segs, texts, embeddings):
                    cur.execute(
                        """
                        INSERT INTO chunks
                          (episode_id, segment_id, start_ms, end_ms,
                           speaker, text, embedding_model, embedding)
                        VALUES (%s,%s,%s,%s,%s,%s,%s,%s)
                        ON CONFLICT (episode_id, segment_id, embedding_model) DO NOTHING
                        """,
                        (
                            episode_id,
                            seg.id,
                            int(round(seg.start * 1000)),
                            int(round(seg.end * 1000)),
                            seg.speaker,
                            text,
                            model_name,
                            np.frombuffer(embedding, dtype=np.float32),
                        ),
                    )
                    inserted += cur.rowcount or 0
            # Spec #46 Tier 0 — centroid in the same transaction, defensively
            # wrapped exactly like the SQLite writer.
            try:
                self._write_centroid(conn, episode_id, model_name, embeddings)
            except Exception as exc:  # pragma: no cover - defensive
                logger.warning(
                    "episode_centroid_write_failed",
                    episode_id=episode_id,
                    error=str(exc),
                )

        logger.info(
            "chunk_write_completed",
            episode_id=episode_id,
            model=model_name,
            inserted=inserted,
            content_segments=len(content_segs),
            forced=force,
        )
        return inserted

    def _write_centroid(self, conn, episode_id: str, model_name: str, embeddings: list) -> None:
        """Upsert the L2-normalised centroid for this episode (spec #46)."""
        centroid = centroid_blob(embeddings, self.embedding_model.dim)
        if centroid is None:
            return
        conn.execute(
            """
            INSERT INTO episode_vectors (episode_id, embedding_model, chunk_count, centroid, computed_at)
            VALUES (%s, %s, %s, %s, now())
            ON CONFLICT (episode_id, embedding_model)
            DO UPDATE SET chunk_count = EXCLUDED.chunk_count,
                          centroid    = EXCLUDED.centroid,
                          computed_at = EXCLUDED.computed_at
            """,
            (episode_id, model_name, len(embeddings), np.frombuffer(centroid, dtype=np.float32)),
        )
