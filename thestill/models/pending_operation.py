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

"""Spec #40 — pending transcription operation model.

DB-shaped state for in-flight async transcriptions (Google long-running
operations, ElevenLabs jobs). Previously lived as JSON files under
``data/pending_operations/``; spec #40 moves them to SQLite because they're
UUID-keyed, queried by status, and have a lifecycle measured in minutes —
the wrong shape for the FileStorage abstraction.

``payload`` is the provider-shaped state (dict round-tripped from JSON).
The schema is intentionally opaque at this layer: each transcriber owns
its own payload shape (see ``elevenlabs_transcriber._save_pending_operation``
and ``google_transcriber._save_operation``). Keeping payloads as a single
opaque blob avoids fragmenting per-provider tables and makes the
file-to-DB backfill a one-liner.
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime
from typing import Any, Dict


@dataclass(frozen=True)
class PendingOperation:
    """A single in-flight async transcription job.

    The repository constructs these from SELECT rows; transcribers
    construct dicts and let the repository serialise.
    """

    operation_id: str
    """Provider-issued operation handle (Google operation ID; ElevenLabs
    transcription_id). Primary key — both providers issue unique values."""

    provider: str
    """``"google"`` or ``"elevenlabs"`` — matches the table CHECK constraint."""

    episode_id: str
    """Episode UUID this operation transcribes. NOT NULL: both transcribers
    already guard persistence on episode_id being present, so this column
    enforces the invariant at the DB layer."""

    payload: Dict[str, Any]
    """Provider-shaped state — the full JSON the file-based version
    persisted. Round-tripped through ``json.dumps`` / ``json.loads``."""

    created_at: datetime
    """UTC, tz-aware."""

    updated_at: datetime
    """UTC, tz-aware. Touched on ``update_payload``."""
