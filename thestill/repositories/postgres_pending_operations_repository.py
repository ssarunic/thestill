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

"""PostgreSQL implementation of ``PendingOperationsRepository`` (spec #44).

Port of ``SqlitePendingOperationsRepository`` following the
``utils.postgres_ext`` conventions:

- ``payload_json`` is ``jsonb``: writes wrap the dict in ``Jsonb(...)``,
  reads come back as a dict — no ``json.dumps``/``json.loads`` round-trip.
- ``episode_id`` is a native ``uuid`` column: str params bind directly;
  reads come back as ``uuid.UUID`` and are stringified via ``as_str``.
- ``created_at``/``updated_at`` are ``timestamptz``: tz-aware ``datetime``
  objects both ways — no isoformat parsing (spec #42 FM-3 removed, not
  ported). ``operation_id`` is provider-issued and stays ``text``.

Schema in ``postgres_schema.py``; business logic identical to the SQLite
version — only the dialect differs.
"""

from __future__ import annotations

from datetime import datetime, timezone
from typing import List, Optional

from psycopg.types.json import Jsonb
from structlog import get_logger

from ..models.pending_operation import PendingOperation
from ..utils.postgres_ext import as_str, connect
from .pending_operations_repository import PendingOperationsRepository

logger = get_logger(__name__)

_COLS = "operation_id, provider, episode_id, payload_json, created_at, updated_at"


class PostgresPendingOperationsRepository(PendingOperationsRepository):
    """PostgreSQL-backed pending transcription operations.

    Thread-safety: connection-per-operation via ``postgres_ext.connect`` —
    same pattern as the other Postgres repositories in this package.
    """

    def __init__(self, dsn: str):
        self.dsn = dsn
        logger.info("Initialized Postgres pending operations repository")

    # --- Mutations -----------------------------------------------------------

    def create(
        self,
        operation_id: str,
        provider: str,
        episode_id: str,
        payload: dict,
    ) -> None:
        """Insert a new pending operation row.

        Raises ``psycopg.errors.UniqueViolation`` on duplicate
        ``operation_id`` — same contract as the SQLite implementation's
        ``sqlite3.IntegrityError`` (duplicates indicate a caller bug).
        """
        now = datetime.now(timezone.utc)
        with connect(self.dsn) as conn:
            conn.execute(
                """
                INSERT INTO pending_transcription_operations
                    (operation_id, provider, episode_id, payload_json, created_at, updated_at)
                VALUES (%s, %s, %s, %s, %s, %s)
                """,
                (operation_id, provider, episode_id, Jsonb(payload), now, now),
            )

    def update_payload(self, operation_id: str, payload: dict) -> None:
        """Replace the payload for an operation. No-op if missing."""
        with connect(self.dsn) as conn:
            conn.execute(
                """
                UPDATE pending_transcription_operations
                SET payload_json = %s, updated_at = %s
                WHERE operation_id = %s
                """,
                (Jsonb(payload), datetime.now(timezone.utc), operation_id),
            )

    def delete(self, operation_id: str) -> None:
        """Idempotent — no error if the row is missing."""
        with connect(self.dsn) as conn:
            conn.execute(
                "DELETE FROM pending_transcription_operations WHERE operation_id = %s",
                (operation_id,),
            )

    # --- Reads ---------------------------------------------------------------

    def get(self, operation_id: str) -> Optional[PendingOperation]:
        with connect(self.dsn) as conn:
            row = conn.execute(
                f"SELECT {_COLS} FROM pending_transcription_operations WHERE operation_id = %s",
                (operation_id,),
            ).fetchone()
            return _row_to_model(row) if row else None

    def list_by_provider(self, provider: str) -> List[PendingOperation]:
        """All pending ops for one provider, oldest-first."""
        with connect(self.dsn) as conn:
            rows = conn.execute(
                f"""
                SELECT {_COLS} FROM pending_transcription_operations
                WHERE provider = %s
                ORDER BY created_at ASC
                """,
                (provider,),
            ).fetchall()
            return [_row_to_model(r) for r in rows]

    def list_by_episode(self, episode_id: str) -> List[PendingOperation]:
        """All pending ops for one episode. Used by Google's chunked-resume path."""
        with connect(self.dsn) as conn:
            rows = conn.execute(
                f"""
                SELECT {_COLS} FROM pending_transcription_operations
                WHERE episode_id = %s
                ORDER BY created_at ASC
                """,
                (episode_id,),
            ).fetchall()
            return [_row_to_model(r) for r in rows]


def _row_to_model(row: dict) -> PendingOperation:
    """Convert a dict row to a PendingOperation. ``payload_json`` (jsonb) is
    already a dict and the timestamptz columns are tz-aware datetimes."""
    return PendingOperation(
        operation_id=row["operation_id"],
        provider=row["provider"],
        episode_id=as_str(row["episode_id"]),
        payload=row["payload_json"],
        created_at=row["created_at"],
        updated_at=row["updated_at"],
    )
