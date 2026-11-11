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
"""SQLite implementation of ``SyncTrackRepository`` (spec #116).

Table created in ``sqlite_podcast_repository`` schema setup.
"""

from datetime import datetime, timezone
from pathlib import Path
from typing import List, Optional

from structlog import get_logger

from ..utils.sqlite_ext import connect
from .sync_track_repository import STATE_CURRENT, STATE_PENDING, SyncTrackRecord, SyncTrackRepository

logger = get_logger(__name__)

_COLS = "episode_id, generation, state, path, source, frames, anchors, audio_bytes, audio_duration_s, created_at"


def _record(row) -> SyncTrackRecord:
    return SyncTrackRecord(
        episode_id=row["episode_id"],
        generation=row["generation"],
        state=row["state"],
        path=row["path"],
        source=row["source"],
        frames=row["frames"],
        anchors=row["anchors"],
        audio_bytes=row["audio_bytes"],
        audio_duration_s=row["audio_duration_s"],
        created_at=datetime.fromisoformat(row["created_at"]) if row["created_at"] else None,
    )


class SqliteSyncTrackRepository(SyncTrackRepository):
    """SQLite-backed sync track index."""

    def __init__(self, db_path: str):
        self.db_path = Path(db_path)
        logger.info("Initialized SQLite sync track repository", db_path=str(self.db_path))

    def put_pending(self, record: SyncTrackRecord) -> Optional[str]:
        created = (record.created_at or datetime.now(timezone.utc)).isoformat()
        with connect(self.db_path) as conn:
            old = conn.execute(
                "SELECT path FROM episode_sync_tracks WHERE episode_id = ? AND state = ?",
                (record.episode_id, STATE_PENDING),
            ).fetchone()
            conn.execute(
                "DELETE FROM episode_sync_tracks WHERE episode_id = ? AND state = ?",
                (record.episode_id, STATE_PENDING),
            )
            conn.execute(
                f"INSERT INTO episode_sync_tracks ({_COLS}) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)",
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
                    created,
                ),
            )
            return old["path"] if old else None

    def promote(self, episode_id: str, generation: str, *, require_no_current: bool = False) -> Optional[List[str]]:
        with connect(self.db_path) as conn:
            # Take the write lock before reading, so the checks below and the
            # switch are one atomic step against other writers.
            conn.execute("BEGIN IMMEDIATE")
            pending = conn.execute(
                "SELECT 1 FROM episode_sync_tracks WHERE episode_id = ? AND generation = ? AND state = ?",
                (episode_id, generation, STATE_PENDING),
            ).fetchone()
            if pending is None:
                return None
            retired = [
                row["path"]
                for row in conn.execute(
                    "SELECT path FROM episode_sync_tracks WHERE episode_id = ? AND state = ?",
                    (episode_id, STATE_CURRENT),
                ).fetchall()
            ]
            if require_no_current and retired:
                return None
            conn.execute(
                "DELETE FROM episode_sync_tracks WHERE episode_id = ? AND state = ?",
                (episode_id, STATE_CURRENT),
            )
            conn.execute(
                "UPDATE episode_sync_tracks SET state = ? WHERE episode_id = ? AND generation = ?",
                (STATE_CURRENT, episode_id, generation),
            )
            return retired

    def retire(self, episode_id: str, state: Optional[str] = None) -> List[str]:
        where = "episode_id = ?" + (" AND state = ?" if state else "")
        params = (episode_id, state) if state else (episode_id,)
        with connect(self.db_path) as conn:
            paths = [
                row["path"]
                for row in conn.execute(f"SELECT path FROM episode_sync_tracks WHERE {where}", params).fetchall()
            ]
            conn.execute(f"DELETE FROM episode_sync_tracks WHERE {where}", params)
            return paths

    def get(self, episode_id: str, state: str = STATE_CURRENT) -> Optional[SyncTrackRecord]:
        with connect(self.db_path) as conn:
            row = conn.execute(
                f"SELECT {_COLS} FROM episode_sync_tracks WHERE episode_id = ? AND state = ?",
                (episode_id, state),
            ).fetchone()
            return _record(row) if row else None
