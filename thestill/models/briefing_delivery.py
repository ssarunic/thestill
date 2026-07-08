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
Briefing delivery record (spec #51).

A *delivery* is the fact "this briefing was (or is being) pushed to the
user over a channel" — decoupled from the briefing row itself, because
"generated" and "delivered" are different facts. One row per
``(briefing_id, channel)`` (the send-once anchor); the row carries its own
retry state so a failed send retries on its own cadence without touching
the briefing or the schedule cursor.

``next_attempt_at`` doubles as the claim lease while ``status='sending'``:
a crashed send becomes claimable again once the lease expires, so an email
is never silently lost to a mid-send restart (FM-4). ``NULL`` once the row
is terminal (``sent`` / ``failed``).
"""

import uuid
from datetime import datetime, timezone
from enum import Enum
from typing import Optional

from pydantic import BaseModel, Field


class DeliveryChannel(str, Enum):
    """Delivery channel. Only ``email`` ships in v1; the column exists so
    push/Slack/Telegram slot in later without a schema change."""

    EMAIL = "email"


class DeliveryStatus(str, Enum):
    """Delivery state machine: pending → sending → sent | failed."""

    PENDING = "pending"
    SENDING = "sending"
    SENT = "sent"
    FAILED = "failed"


class BriefingDelivery(BaseModel):
    """One channel-delivery of one briefing (spec #51)."""

    id: str = Field(default_factory=lambda: str(uuid.uuid4()))
    briefing_id: str
    channel: DeliveryChannel = DeliveryChannel.EMAIL
    status: DeliveryStatus = DeliveryStatus.PENDING
    attempts: int = Field(default=0, ge=0)
    next_attempt_at: Optional[datetime] = None
    sent_at: Optional[datetime] = None
    last_error: Optional[str] = None
    created_at: datetime = Field(default_factory=lambda: datetime.now(timezone.utc))
