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

"""PostgreSQL implementation of ``TranscriptEditRepository`` (spec #110).

Follows the ``utils.postgres_ext`` conventions: native ``uuid`` ids,
``timestamptz`` datetimes, ``jsonb`` columns written through ``Jsonb``.
Schema in ``postgres_schema.py`` and alembic ``0027_transcript_edits``.
"""

from __future__ import annotations

from datetime import datetime
from typing import List, Optional, Tuple

import psycopg
from psycopg.types.json import Jsonb
from structlog import get_logger

from ..models.transcript_edits import TranscriptEdit
from ..utils.ids import is_uuid
from ..utils.postgres_ext import as_str, connect
from .transcript_edit_repository import TranscriptEditRepository

logger = get_logger(__name__)

_COLS = (
    "id, episode_id, seq, op, raw_sha256, anchor_json, args_json, before_json, reverts, session_id,"
    " report_id, actor_user_id, actor_kind, created_at"
)


class PostgresTranscriptEditRepository(TranscriptEditRepository):
    def __init__(self, dsn: str):
        self.dsn = dsn
        logger.info("Initialized Postgres transcript edit repository")

    def append(self, edit: TranscriptEdit, *, expected_last_seq: int) -> Optional[TranscriptEdit]:
        if not is_uuid(edit.episode_id):
            return None
        seq = expected_last_seq + 1
        try:
            with connect(self.dsn) as conn:
                # The check and the insert are one statement: a stale writer
                # inserts nothing, and two writers racing on the same base
                # are serialised by the (episode_id, seq) unique constraint.
                cursor = conn.execute(
                    f"""
                    INSERT INTO transcript_edits ({_COLS})
                    SELECT %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s
                     WHERE (SELECT COALESCE(MAX(seq), 0) FROM transcript_edits WHERE episode_id = %s) = %s
                    """,
                    (
                        edit.id,
                        edit.episode_id,
                        seq,
                        edit.op,
                        edit.raw_sha256,
                        Jsonb(edit.anchor),
                        Jsonb(edit.args),
                        Jsonb(edit.before),
                        edit.reverts,
                        edit.session_id,
                        edit.report_id,
                        edit.actor_user_id if is_uuid(edit.actor_user_id) else None,
                        edit.actor_kind,
                        edit.created_at,
                        edit.episode_id,
                        expected_last_seq,
                    ),
                )
                if cursor.rowcount != 1:
                    return None
        except psycopg.errors.UniqueViolation:
            return None
        return edit.model_copy(update={"seq": seq})

    def list_for_episode(self, episode_id: str) -> List[TranscriptEdit]:
        if not is_uuid(episode_id):
            return []
        with connect(self.dsn) as conn:
            rows = conn.execute(
                f"SELECT {_COLS} FROM transcript_edits WHERE episode_id = %s ORDER BY seq",
                (episode_id,),
            ).fetchall()
            return [self._row_to_edit(r) for r in rows]

    def last_seq(self, episode_id: str) -> int:
        if not is_uuid(episode_id):
            return 0
        with connect(self.dsn) as conn:
            row = conn.execute(
                "SELECT COALESCE(MAX(seq), 0) AS last_seq FROM transcript_edits WHERE episode_id = %s",
                (episode_id,),
            ).fetchone()
            return int(row["last_seq"])

    def get(self, edit_id: str) -> Optional[TranscriptEdit]:
        if not is_uuid(edit_id):
            return None
        with connect(self.dsn) as conn:
            row = conn.execute(f"SELECT {_COLS} FROM transcript_edits WHERE id = %s", (edit_id,)).fetchone()
            return self._row_to_edit(row) if row else None

    def list_by_actor(self, actor_user_id: str, *, since: datetime) -> List[TranscriptEdit]:
        if not is_uuid(actor_user_id):
            return []
        with connect(self.dsn) as conn:
            rows = conn.execute(
                f"""
                SELECT {_COLS} FROM transcript_edits
                 WHERE actor_user_id = %s AND created_at >= %s
                 ORDER BY created_at, seq
                """,
                (actor_user_id, since),
            ).fetchall()
            return [self._row_to_edit(r) for r in rows]

    def episodes_with_edits(self) -> List[Tuple[str, int, datetime]]:
        with connect(self.dsn) as conn:
            rows = conn.execute(
                """
                SELECT episode_id, MAX(seq) AS last_seq, MAX(created_at) AS newest
                  FROM transcript_edits GROUP BY episode_id ORDER BY newest DESC
                """
            ).fetchall()
            return [(as_str(r["episode_id"]), int(r["last_seq"]), r["newest"]) for r in rows]

    def mark_refreshed(self, episode_id: str, *, seq: int, at: datetime) -> None:
        if not is_uuid(episode_id):
            return
        with connect(self.dsn) as conn:
            conn.execute(
                """
                INSERT INTO transcript_edit_refreshes (episode_id, refreshed_seq, queued_at)
                VALUES (%s, %s, %s)
                ON CONFLICT (episode_id) DO UPDATE SET
                    refreshed_seq = excluded.refreshed_seq,
                    queued_at = excluded.queued_at
                """,
                (episode_id, seq, at),
            )

    def episodes_pending_refresh(self, *, idle_before: datetime) -> List[Tuple[str, int]]:
        with connect(self.dsn) as conn:
            rows = conn.execute(
                """
                SELECT e.episode_id, MAX(e.seq) AS last_seq
                  FROM transcript_edits e
                  LEFT JOIN transcript_edit_refreshes r ON r.episode_id = e.episode_id
                 GROUP BY e.episode_id, r.refreshed_seq
                HAVING (r.refreshed_seq IS NULL OR r.refreshed_seq < MAX(e.seq))
                   AND MAX(e.created_at) <= %s
                """,
                (idle_before,),
            ).fetchall()
            return [(as_str(r["episode_id"]), int(r["last_seq"])) for r in rows]

    @staticmethod
    def _row_to_edit(row) -> TranscriptEdit:
        return TranscriptEdit(
            id=as_str(row["id"]),
            episode_id=as_str(row["episode_id"]),
            seq=row["seq"],
            op=row["op"],
            raw_sha256=row["raw_sha256"],
            anchor=row["anchor_json"],
            args=row["args_json"],
            before=row["before_json"],
            reverts=as_str(row["reverts"]),
            session_id=row["session_id"],
            report_id=row["report_id"],
            actor_user_id=as_str(row["actor_user_id"]),
            actor_kind=row["actor_kind"],
            created_at=row["created_at"],
        )
