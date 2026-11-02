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

"""SQLite implementation of ``TranscriptEditRepository`` (spec #110)."""

import json
import sqlite3
from contextlib import contextmanager
from datetime import datetime
from pathlib import Path
from typing import Iterator, List, Optional, Tuple

from structlog import get_logger

from ..models.transcript_edits import TranscriptEdit
from ..utils.sqlite_ext import connect
from .transcript_edit_repository import TranscriptEditRepository

logger = get_logger(__name__)

_COLS = (
    "id, episode_id, seq, op, raw_sha256, anchor_json, args_json, before_json, reverts, session_id,"
    " report_id, actor_user_id, actor_kind, created_at"
)


class SqliteTranscriptEditRepository(TranscriptEditRepository):
    """Timestamps are tz-aware ISO-8601 strings (``+00:00``), the project
    convention, so ``ORDER BY created_at`` is a plain string sort."""

    def __init__(self, db_path: str):
        self.db_path = Path(db_path)
        logger.info("Initialized SQLite transcript edit repository", db_path=str(self.db_path))

    @contextmanager
    def _get_connection(self) -> Iterator[sqlite3.Connection]:
        with connect(self.db_path) as conn:
            yield conn

    def append(self, edit: TranscriptEdit, *, expected_last_seq: int) -> Optional[TranscriptEdit]:
        with self._get_connection() as conn:
            # BEGIN IMMEDIATE takes the writer lock before the check, so two
            # appends against the same base can't both pass it.
            conn.execute("BEGIN IMMEDIATE")
            row = conn.execute(
                "SELECT COALESCE(MAX(seq), 0) AS last_seq FROM transcript_edits WHERE episode_id = ?",
                (edit.episode_id,),
            ).fetchone()
            if int(row["last_seq"]) != expected_last_seq:
                return None
            seq = expected_last_seq + 1
            conn.execute(
                f"INSERT INTO transcript_edits ({_COLS}) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)",
                (
                    edit.id,
                    edit.episode_id,
                    seq,
                    edit.op,
                    edit.raw_sha256,
                    json.dumps(edit.anchor),
                    json.dumps(edit.args),
                    json.dumps(edit.before),
                    edit.reverts,
                    edit.session_id,
                    edit.report_id,
                    edit.actor_user_id,
                    edit.actor_kind,
                    edit.created_at.isoformat(),
                ),
            )
            return edit.model_copy(update={"seq": seq})

    def list_for_episode(self, episode_id: str) -> List[TranscriptEdit]:
        with self._get_connection() as conn:
            rows = conn.execute(
                f"SELECT {_COLS} FROM transcript_edits WHERE episode_id = ? ORDER BY seq",
                (episode_id,),
            ).fetchall()
            return [self._row_to_edit(r) for r in rows]

    def last_seq(self, episode_id: str) -> int:
        with self._get_connection() as conn:
            row = conn.execute(
                "SELECT COALESCE(MAX(seq), 0) AS last_seq FROM transcript_edits WHERE episode_id = ?",
                (episode_id,),
            ).fetchone()
            return int(row["last_seq"])

    def get(self, edit_id: str) -> Optional[TranscriptEdit]:
        with self._get_connection() as conn:
            row = conn.execute(f"SELECT {_COLS} FROM transcript_edits WHERE id = ?", (edit_id,)).fetchone()
            return self._row_to_edit(row) if row else None

    def list_by_actor(self, actor_user_id: str, *, since: datetime) -> List[TranscriptEdit]:
        with self._get_connection() as conn:
            rows = conn.execute(
                f"""
                SELECT {_COLS} FROM transcript_edits
                 WHERE actor_user_id = ? AND created_at >= ?
                 ORDER BY created_at, seq
                """,
                (actor_user_id, since.isoformat()),
            ).fetchall()
            return [self._row_to_edit(r) for r in rows]

    def episodes_with_edits(self) -> List[Tuple[str, int, datetime]]:
        with self._get_connection() as conn:
            rows = conn.execute(
                """
                SELECT episode_id, MAX(seq) AS last_seq, MAX(created_at) AS newest
                  FROM transcript_edits GROUP BY episode_id ORDER BY newest DESC
                """
            ).fetchall()
            return [(r["episode_id"], int(r["last_seq"]), datetime.fromisoformat(r["newest"])) for r in rows]

    def mark_refreshed(self, episode_id: str, *, seq: int, at: datetime) -> None:
        with self._get_connection() as conn:
            conn.execute(
                """
                INSERT INTO transcript_edit_refreshes (episode_id, refreshed_seq, queued_at)
                VALUES (?, ?, ?)
                ON CONFLICT(episode_id) DO UPDATE SET
                    refreshed_seq = excluded.refreshed_seq,
                    queued_at = excluded.queued_at
                """,
                (episode_id, seq, at.isoformat()),
            )

    def episodes_pending_refresh(self, *, idle_before: datetime) -> List[Tuple[str, int]]:
        with self._get_connection() as conn:
            rows = conn.execute(
                """
                SELECT e.episode_id, MAX(e.seq) AS last_seq
                  FROM transcript_edits e
                  LEFT JOIN transcript_edit_refreshes r ON r.episode_id = e.episode_id
                 GROUP BY e.episode_id, r.refreshed_seq
                HAVING (r.refreshed_seq IS NULL OR r.refreshed_seq < MAX(e.seq))
                   AND MAX(e.created_at) <= ?
                """,
                (idle_before.isoformat(),),
            ).fetchall()
            return [(r["episode_id"], int(r["last_seq"])) for r in rows]

    @staticmethod
    def _row_to_edit(row: sqlite3.Row) -> TranscriptEdit:
        return TranscriptEdit(
            id=row["id"],
            episode_id=row["episode_id"],
            seq=row["seq"],
            op=row["op"],
            raw_sha256=row["raw_sha256"],
            anchor=json.loads(row["anchor_json"]),
            args=json.loads(row["args_json"]),
            before=json.loads(row["before_json"]),
            reverts=row["reverts"],
            session_id=row["session_id"],
            report_id=row["report_id"],
            actor_user_id=row["actor_user_id"],
            actor_kind=row["actor_kind"],
            created_at=datetime.fromisoformat(row["created_at"]),
        )
