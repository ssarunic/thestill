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

"""Spec #40 — SQLite-backed repository for pending transcription operations.

Replaces ``data/pending_operations/{operation_id}.json`` per provider.
Schema is owned by ``SqlitePodcastRepository._run_migrations`` (see the
``pending_transcription_operations`` migration block there).

Connection-per-operation, raw SQL, ISO-8601 timestamps — mirrors the
patterns in ``sqlite_digest_repository`` and ``sqlite_briefing_repository``.
"""

from __future__ import annotations

import json
import sqlite3
from contextlib import contextmanager
from datetime import datetime, timezone
from pathlib import Path
from typing import Iterator, List, Optional

from structlog import get_logger

from ..models.pending_operation import PendingOperation

logger = get_logger(__name__)


def _now_iso() -> str:
    """ISO-8601 UTC string matching the ``DEFAULT`` clause in the schema.

    The schema's DEFAULT uses ``strftime('%Y-%m-%dT%H:%M:%f+00:00','now')``
    which emits millisecond precision; truncate Python's 6-digit microseconds
    to match so eyeballing the table doesn't show two different formats.
    """
    return datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%S.%f")[:-3] + "+00:00"


def _parse_iso(value: str) -> datetime:
    """Inverse of ``_now_iso``. Accepts the column default's format AND the
    ``strftime('%Y-%m-%dT%H:%M:%fZ', 'now')`` produced by the SQLite ``DEFAULT``
    in case a future schema bump simplifies that side."""
    # Normalise the two forms we might see — both end with either ``+00:00``
    # or ``Z``; ``fromisoformat`` accepts the former in 3.11+ but the latter
    # is the older-style suffix used in some migrations.
    if value.endswith("Z"):
        value = value[:-1] + "+00:00"
    return datetime.fromisoformat(value)


class SqlitePendingOperationsRepository:
    """SQLite-backed pending transcription operations.

    Thread-safety: connection-per-operation via the ``_get_connection``
    context manager — same pattern as the rest of the SQLite repositories
    in this package.
    """

    def __init__(self, db_path: str):
        self.db_path = Path(db_path)
        logger.info("Initialized SQLite pending operations repository", db_path=str(self.db_path))

    @contextmanager
    def _get_connection(self) -> Iterator[sqlite3.Connection]:
        conn = sqlite3.connect(str(self.db_path))
        conn.row_factory = sqlite3.Row
        conn.execute("PRAGMA foreign_keys = ON")
        try:
            yield conn
            conn.commit()
        except Exception:
            conn.rollback()
            raise
        finally:
            conn.close()

    # --- Mutations -----------------------------------------------------------

    def create(
        self,
        operation_id: str,
        provider: str,
        episode_id: str,
        payload: dict,
    ) -> None:
        """Insert a new pending operation row.

        Raises ``sqlite3.IntegrityError`` on duplicate ``operation_id``.
        Both Google and ElevenLabs issue unique operation handles, so the
        unique constraint is a real invariant — duplicates indicate a bug
        in the caller, not a race the repo should paper over.
        """
        now = _now_iso()
        with self._get_connection() as conn:
            conn.execute(
                """
                INSERT INTO pending_transcription_operations
                    (operation_id, provider, episode_id, payload_json, created_at, updated_at)
                VALUES (?, ?, ?, ?, ?, ?)
                """,
                (operation_id, provider, episode_id, json.dumps(payload), now, now),
            )

    def update_payload(self, operation_id: str, payload: dict) -> None:
        """Replace the payload JSON for an operation. No-op if missing.

        Used when a transcriber needs to record state-change without
        deleting+recreating (e.g. updating poll counters between waits).
        """
        with self._get_connection() as conn:
            conn.execute(
                """
                UPDATE pending_transcription_operations
                SET payload_json = ?, updated_at = ?
                WHERE operation_id = ?
                """,
                (json.dumps(payload), _now_iso(), operation_id),
            )

    def delete(self, operation_id: str) -> None:
        """Idempotent — no error if the row is missing.

        Mirrors the existing JSON-file delete semantics (the old code used
        ``Path.unlink(missing_ok=True)``-style guards).
        """
        with self._get_connection() as conn:
            conn.execute(
                "DELETE FROM pending_transcription_operations WHERE operation_id = ?",
                (operation_id,),
            )

    # --- Reads ---------------------------------------------------------------

    def get(self, operation_id: str) -> Optional[PendingOperation]:
        with self._get_connection() as conn:
            row = conn.execute(
                "SELECT * FROM pending_transcription_operations WHERE operation_id = ?",
                (operation_id,),
            ).fetchone()
        return _row_to_model(row) if row else None

    def list_by_provider(self, provider: str) -> List[PendingOperation]:
        """All pending ops for one provider, oldest-first.

        Replaces the old ``glob('elevenlabs_*.json')`` / ``glob('*.json')``
        directory scans. Both transcribers' resume paths call this.
        """
        with self._get_connection() as conn:
            rows = conn.execute(
                """
                SELECT * FROM pending_transcription_operations
                WHERE provider = ?
                ORDER BY created_at ASC
                """,
                (provider,),
            ).fetchall()
        return [_row_to_model(r) for r in rows]

    def list_by_episode(self, episode_id: str) -> List[PendingOperation]:
        """All pending ops for one episode. Used by Google's chunked-resume path."""
        with self._get_connection() as conn:
            rows = conn.execute(
                """
                SELECT * FROM pending_transcription_operations
                WHERE episode_id = ?
                ORDER BY created_at ASC
                """,
                (episode_id,),
            ).fetchall()
        return [_row_to_model(r) for r in rows]


def _row_to_model(row: sqlite3.Row) -> PendingOperation:
    return PendingOperation(
        operation_id=row["operation_id"],
        provider=row["provider"],
        episode_id=row["episode_id"],
        payload=json.loads(row["payload_json"]),
        created_at=_parse_iso(row["created_at"]),
        updated_at=_parse_iso(row["updated_at"]),
    )
