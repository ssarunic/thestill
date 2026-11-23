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
"""PostgreSQL implementation of ``SyncTrackRepository`` (spec #116).

Schema in ``postgres_schema.py`` and alembic ``0028_episode_sync_tracks``.
"""

from __future__ import annotations

from typing import List, Optional

from structlog import get_logger

from ..utils.ids import is_uuid
from ..utils.postgres_ext import connect
from .sync_track_repository import STATE_CURRENT, STATE_PENDING, SyncTrackRecord, SyncTrackRepository

logger = get_logger(__name__)

_COLS = "episode_id, generation, state, path, source, frames, anchors, audio_bytes, audio_duration_s, created_at"


def _record(row) -> SyncTrackRecord:
    return SyncTrackRecord(
        episode_id=str(row["episode_id"]),
        generation=row["generation"],
        state=row["state"],
        path=row["path"],
        source=row["source"],
        frames=row["frames"],
        anchors=row["anchors"],
        audio_bytes=row["audio_bytes"],
        audio_duration_s=row["audio_duration_s"],
        created_at=row["created_at"],
    )


class PostgresSyncTrackRepository(SyncTrackRepository):
    """PostgreSQL-backed sync track index."""

    def __init__(self, dsn: str):
        self.dsn = dsn
        logger.info("Initialized Postgres sync track repository")

    def put_pending(self, record: SyncTrackRecord) -> Optional[str]:
        with connect(self.dsn) as conn:
            old = conn.execute(
                "DELETE FROM episode_sync_tracks WHERE episode_id = %s AND state = %s RETURNING path",
                (record.episode_id, STATE_PENDING),
            ).fetchone()
            conn.execute(
                """
                INSERT INTO episode_sync_tracks
                    (episode_id, generation, state, path, source, frames, anchors, audio_bytes, audio_duration_s)
                VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s)
                """,
                (
                    record.episode_id,
                    record.generation,
                    STATE_PENDING,
                    record.path,
                    record.source,
                    record.frames,
                    record.anchors,
                    record.audio_bytes,
                    record.audio_duration_s,
                ),
            )
            return old["path"] if old else None

    def promote(self, episode_id: str, generation: str, *, require_no_current: bool = False) -> Optional[List[str]]:
        if not is_uuid(episode_id):
            return None
        with connect(self.dsn) as conn:
            # One promotion per episode at a time: the checks below and the
            # switch then form one atomic step.
            conn.execute("SELECT pg_advisory_xact_lock(hashtext(%s))", (f"sync_track:{episode_id}",))
            # Lock the pending row so two promotions of one episode serialize.
            pending = conn.execute(
                """
                SELECT 1 FROM episode_sync_tracks
                 WHERE episode_id = %s AND generation = %s AND state = %s
                   FOR UPDATE
                """,
                (episode_id, generation, STATE_PENDING),
            ).fetchone()
            if pending is None:
                return None
            if require_no_current:
                existing = conn.execute(
                    "SELECT 1 FROM episode_sync_tracks WHERE episode_id = %s AND state = %s",
                    (episode_id, STATE_CURRENT),
                ).fetchone()
                if existing is not None:
                    return None
            retired = [
                row["path"]
                for row in conn.execute(
                    "DELETE FROM episode_sync_tracks WHERE episode_id = %s AND state = %s RETURNING path",
                    (episode_id, STATE_CURRENT),
                ).fetchall()
            ]
            conn.execute(
                "UPDATE episode_sync_tracks SET state = %s WHERE episode_id = %s AND generation = %s",
                (STATE_CURRENT, episode_id, generation),
            )
            return retired

    def retire(self, episode_id: str, state: Optional[str] = None) -> List[str]:
        if not is_uuid(episode_id):
            return []
        where = "episode_id = %s" + (" AND state = %s" if state else "")
        params = (episode_id, state) if state else (episode_id,)
        with connect(self.dsn) as conn:
            rows = conn.execute(f"DELETE FROM episode_sync_tracks WHERE {where} RETURNING path", params).fetchall()
            return [row["path"] for row in rows]

    def get(self, episode_id: str, state: str = STATE_CURRENT) -> Optional[SyncTrackRecord]:
        if not is_uuid(episode_id):
            return None
        with connect(self.dsn) as conn:
            row = conn.execute(
                f"SELECT {_COLS} FROM episode_sync_tracks WHERE episode_id = %s AND state = %s",
                (episode_id, state),
            ).fetchone()
            return _record(row) if row else None
