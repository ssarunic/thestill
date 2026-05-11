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

"""Spec #28 §2.10.3 — write per-segment ``chunks`` rows for one episode.

In-process write: read the cleaned-transcript JSON, embed each
content segment, INSERT into ``chunks``. The ``chunks_ai`` AFTER
INSERT trigger fans out into ``chunks_vec`` (k-NN) and ``chunks_fts``
(BM25), so the writer never touches those mirror tables directly.

Idempotence: ``UNIQUE (episode_id, segment_id, embedding_model)``
prevents duplicate rows on plain re-runs. ``force=True`` deletes the
existing rows for the (episode, model) pair first — used when an
embedding model is upgraded or a transcript is re-cleaned.
"""

from __future__ import annotations

import sqlite3
from contextlib import contextmanager
from pathlib import Path
from typing import Optional

from structlog import get_logger

from ..models.annotated_transcript import AnnotatedTranscript
from ..utils.sqlite_ext import load_vec_extension
from .embedding_model import EmbeddingModel

logger = get_logger(__name__)


class ChunkWriter:
    """Embed a transcript's content segments into the ``chunks`` table.

    The writer is stateless beyond the (db_path, embedding_model)
    pair; create one per REINDEX task or one per backfill batch.
    """

    def __init__(self, *, db_path: str, embedding_model: EmbeddingModel):
        self.db_path = Path(db_path)
        self.embedding_model = embedding_model

    @contextmanager
    def _get_connection(self):
        """Connection with sqlite-vec loaded (the AI trigger needs it).

        Raises ``SqliteVecNotInstalledError`` from
        ``load_vec_extension`` if the extension isn't installed —
        chunk writes are impossible without it, so a hard error is
        the right surface (vs. the soft-load used by repositories
        that only need it for cascade triggers).
        """
        conn = sqlite3.connect(str(self.db_path))
        conn.row_factory = sqlite3.Row
        load_vec_extension(conn)
        conn.execute("PRAGMA foreign_keys = ON")
        conn.execute("PRAGMA journal_mode = WAL")
        conn.execute("PRAGMA busy_timeout = 5000")
        try:
            yield conn
            conn.commit()
        except Exception:
            conn.rollback()
            raise
        finally:
            conn.close()

    def write_episode(
        self,
        episode_id: str,
        transcript: AnnotatedTranscript,
        *,
        force: bool = False,
    ) -> int:
        """Embed and insert chunks for one episode.

        Idempotent: skips when chunks already exist at the current
        model unless ``force=True``, in which case existing rows are
        deleted first. Returns the inserted row count.

        The encode_batch call runs *outside* any DB transaction so
        parallel reindex workers don't serialize on the SQLite writer
        lock during the embedding compute (which dominates wall time).
        Only the DELETE+INSERT phase opens a write connection.
        """
        content_segs = [s for s in transcript.segments if s.kind == "content" and s.text.strip()]
        if not content_segs:
            logger.info("chunk_write_no_content_segments", episode_id=episode_id)
            return 0

        model_name = self.embedding_model.model_name

        with self._get_connection() as conn:
            existing = conn.execute(
                "SELECT COUNT(*) FROM chunks WHERE episode_id = ? AND embedding_model = ?",
                (episode_id, model_name),
            ).fetchone()[0]
        if existing and not force:
            logger.info(
                "chunk_write_skipped_exists",
                episode_id=episode_id,
                model=model_name,
                existing_rows=existing,
            )
            return 0

        texts = [_segment_text(seg.speaker, seg.text) for seg in content_segs]
        embeddings = self.embedding_model.encode_batch(texts)
        rows = [
            (
                episode_id,
                seg.id,
                int(round(seg.start * 1000)),
                int(round(seg.end * 1000)),
                seg.speaker,
                text,
                model_name,
                embedding,
            )
            for seg, text, embedding in zip(content_segs, texts, embeddings)
        ]

        with self._get_connection() as conn:
            if force and existing:
                conn.execute(
                    "DELETE FROM chunks WHERE episode_id = ? AND embedding_model = ?",
                    (episode_id, model_name),
                )
            cur = conn.executemany(
                """
                INSERT OR IGNORE INTO chunks
                  (episode_id, segment_id, start_ms, end_ms,
                   speaker, text, embedding_model, embedding)
                VALUES (?,?,?,?,?,?,?,?)
                """,
                rows,
            )
            inserted = cur.rowcount if cur.rowcount is not None else 0

        logger.info(
            "chunk_write_completed",
            episode_id=episode_id,
            model=model_name,
            inserted=inserted,
            content_segments=len(content_segs),
            forced=force,
        )
        return inserted


def _segment_text(speaker: Optional[str], text: str) -> str:
    """Compose the embeddable text for one segment.

    Prefixing with the speaker biases the embedding toward
    ``speaker:topic`` queries — useful for "what did X say about Y"
    style searches. Empty speaker just falls through to bare text.
    """
    text = text.strip()
    return f"{speaker}: {text}" if speaker else text
