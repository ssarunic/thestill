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
Lifecycle message records (spec #118).

A *user message* is the fact "this lifecycle message was (or is being)
sent to this user over a channel". One row per ``(user_id, message_key,
channel)`` is the send-once anchor: a restart, a second worker or a retry
can never send the same message twice. The row carries its own retry state
like ``BriefingDelivery`` (spec #51), whose state machine it copies.

``next_attempt_at`` doubles as the claim lease while ``status='sending'``,
so a crash mid-send makes the row claimable again once the lease expires.
``NULL`` once the row is terminal (``sent`` / ``failed`` / ``skipped``).

``LifecycleFacts`` is what the planner knows about one candidate user,
read from product tables in one query: enough to decide which message is
due and whether it still applies.
"""

import uuid
from dataclasses import dataclass, field
from datetime import datetime, timezone
from enum import Enum
from typing import FrozenSet, Optional

from pydantic import BaseModel, Field


class MessageChannel(str, Enum):
    """Only ``email`` ships; Slack and in-app slot in without a schema
    change beyond widening the CHECK constraint."""

    EMAIL = "email"


class MessageStatus(str, Enum):
    """pending → sending → sent | failed; ``skipped`` when the message no
    longer applied at send time (the user turned tips off, was suspended,
    or already did what the message asks)."""

    PENDING = "pending"
    SENDING = "sending"
    SENT = "sent"
    FAILED = "failed"
    SKIPPED = "skipped"


class UserMessage(BaseModel):
    """One channel-send of one lifecycle message to one user."""

    id: str = Field(default_factory=lambda: str(uuid.uuid4()))
    user_id: str
    message_key: str
    channel: MessageChannel = MessageChannel.EMAIL
    status: MessageStatus = MessageStatus.PENDING
    attempts: int = Field(default=0, ge=0)
    next_attempt_at: Optional[datetime] = None
    sent_at: Optional[datetime] = None
    last_error: Optional[str] = None
    created_at: datetime = Field(default_factory=lambda: datetime.now(timezone.utc))


@dataclass(frozen=True)
class LifecycleFacts:
    """One candidate user, as the lifecycle planner sees them.

    Every fact comes from service state (follows, inbox, connector grants,
    briefing schedule and deliveries), never from measurement-only tables,
    so readers who turned usage analytics off are judged on the same facts.
    """

    user_id: str
    email: str
    name: Optional[str]
    created_at: datetime
    #: The briefing schedule's IANA zone; None without a schedule (UTC).
    timezone_name: Optional[str] = None
    briefing_enabled: bool = False
    briefing_email_enabled: bool = False
    briefing_hour_local: Optional[int] = None
    follow_count: int = 0
    #: At least one inbox episode marked read.
    has_read: bool = False
    #: An active Claude connector (OAuth grant or personal MCP token).
    connector_connected: bool = False
    last_briefing_email_at: Optional[datetime] = None
    #: Newest lifecycle message that is queued, being sent or sent (any
    #: channel): the frequency cap counts what is on its way too.
    last_message_at: Optional[datetime] = None
    #: Keys that already have a row on the email channel, in any status.
    message_keys: FrozenSet[str] = field(default_factory=frozenset)
