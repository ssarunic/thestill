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

"""
Per-user briefing model (spec #36).

A briefing is a recurring read-out of the part of a user's inbox that
hasn't been read out yet. ``cursor_from`` / ``cursor_to`` define the
inclusive-exclusive window of inbox deliveries this briefing covers, so
"what episodes did this briefing cover" is reproducible without joining
back to the inbox at the same moment in time.

Phase 1 (this scope) persists the briefing row + cursor + episode count.
Script and audio rendering land in a follow-up: ``script_path`` and
``audio_path`` are nullable here and populated by a later phase.
"""

import uuid
from datetime import datetime, timezone
from typing import Optional

from pydantic import BaseModel, Field


class Briefing(BaseModel):
    """
    A single per-user briefing covering inbox deliveries in a time window.
    """

    id: str = Field(default_factory=lambda: str(uuid.uuid4()))
    user_id: str
    cursor_from: datetime
    cursor_to: datetime
    episode_count: int
    script_path: Optional[str] = None
    audio_path: Optional[str] = None
    created_at: datetime = Field(default_factory=lambda: datetime.now(timezone.utc))
    listened_at: Optional[datetime] = None
