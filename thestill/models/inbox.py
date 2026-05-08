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
Per-user inbox models.

The inbox decouples *what exists* (episodes in the system) from *what each
user sees* (rows in their inbox). A row is created by one of:

- ``follow_new``: an episode the user follows just published (spec #29).
- ``follow_seed``: a few recent episodes pulled in when the user follows
  a podcast for the first time (spec #29).
- ``ad_hoc``: the user actively saved an existing episode to their inbox
  (spec #31).
- ``import``: the user pasted an external URL and we materialised a brand
  new episode for it (spec #31).

Per-user state (unread / read / saved / dismissed) lives on the row, not on
the episode — two users following the same podcast can have completely
independent state on the same episode.
"""

import uuid
from datetime import datetime, timezone
from typing import Literal, Optional, get_args

from pydantic import BaseModel, Field

from .podcast import Episode

InboxSource = Literal["follow_new", "follow_seed", "ad_hoc", "import"]
InboxState = Literal["unread", "read", "saved", "dismissed"]

# Tuples derived from the Literal types so the runtime values can't drift
# from the type-checker view.
INBOX_SOURCES: tuple[str, ...] = get_args(InboxSource)
INBOX_STATES: tuple[str, ...] = get_args(InboxState)


class InboxEntry(BaseModel):
    """
    A single inbox row — one episode delivered to one user.

    Identified by ``(user_id, episode_id)`` (unique). Re-delivery is a no-op:
    inserting an existing pair is silently skipped at the repository layer.
    """

    id: str = Field(default_factory=lambda: str(uuid.uuid4()))
    user_id: str
    episode_id: str
    source: InboxSource
    state: InboxState = "unread"
    delivered_at: datetime = Field(default_factory=lambda: datetime.now(timezone.utc))
    state_changed_at: Optional[datetime] = None


class PodcastInboxSummary(BaseModel):
    """
    The minimum subset of podcast fields needed to render an inbox row.

    Kept narrow on purpose: the inbox view is high-traffic and we don't
    want to pull the full ``Podcast`` model (with category lookups, etc.)
    for every list response.
    """

    id: str
    title: str
    slug: str = ""
    image_url: Optional[str] = None


class InboxItem(BaseModel):
    """Composed read view: an inbox row plus the episode + podcast it points to."""

    entry: InboxEntry
    episode: Episode
    podcast: PodcastInboxSummary
