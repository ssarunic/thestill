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

"""Abstract repository interface for pending transcription operations.

Extracted from ``SqlitePendingOperationsRepository`` (spec #44 Phase 2) so the
SQLite and PostgreSQL implementations share one contract. The contract is
expressed in Python objects: ``payload`` is a plain ``dict`` on the way in and
out — how it is persisted (JSON text in SQLite, ``jsonb`` in Postgres) is an
implementation detail invisible to callers.
"""

from __future__ import annotations

from abc import ABC, abstractmethod
from typing import List, Optional

from ..models.pending_operation import PendingOperation


class PendingOperationsRepository(ABC):
    """Abstract repository for in-flight async transcription operations.

    Rows track provider-issued operation handles (Google long-running
    operations, ElevenLabs jobs) between submit and completion (spec #40).
    Implementations must be thread-safe; the existing ones use a
    connection-per-operation pattern.
    """

    # --- Mutations -----------------------------------------------------------

    @abstractmethod
    def create(
        self,
        operation_id: str,
        provider: str,
        episode_id: str,
        payload: dict,
    ) -> None:
        """Insert a new pending operation row.

        Raises the backend's integrity error on duplicate ``operation_id``
        (``sqlite3.IntegrityError`` / ``psycopg.errors.UniqueViolation``).
        Both Google and ElevenLabs issue unique operation handles, so the
        unique constraint is a real invariant — duplicates indicate a bug
        in the caller, not a race the repo should paper over.
        """

    @abstractmethod
    def update_payload(self, operation_id: str, payload: dict) -> None:
        """Replace the payload for an operation and touch ``updated_at``.

        No-op if the row is missing. Used when a transcriber needs to record
        state-change without deleting+recreating (e.g. updating poll counters
        between waits).
        """

    @abstractmethod
    def delete(self, operation_id: str) -> None:
        """Delete a pending operation. Idempotent — no error if missing.

        Mirrors the legacy JSON-file delete semantics
        (``Path.unlink(missing_ok=True)``-style guards).
        """

    # --- Reads ---------------------------------------------------------------

    @abstractmethod
    def get(self, operation_id: str) -> Optional[PendingOperation]:
        """Fetch one operation by its provider-issued id, or None if missing."""

    @abstractmethod
    def list_by_provider(self, provider: str) -> List[PendingOperation]:
        """All pending ops for one provider, oldest-first (``created_at`` ASC).

        Replaces the old ``glob('elevenlabs_*.json')`` / ``glob('*.json')``
        directory scans. Both transcribers' resume paths call this.
        """

    @abstractmethod
    def list_by_episode(self, episode_id: str) -> List[PendingOperation]:
        """All pending ops for one episode, oldest-first (``created_at`` ASC).

        Used by Google's chunked-resume path, where one episode fans out to
        multiple chunk operations.
        """
