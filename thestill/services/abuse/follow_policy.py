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

"""Spec #104 §1.3 — the follow gate.

A follow costs money only when it takes a feed from zero followers to one:
that follow seeds the newest ``INBOX_SEED_ON_FOLLOW`` episodes through the
pipeline and turns on processing for every future one (spec #63). So
``new_feeds_enabled`` gates exactly that step, whatever route makes it —
inside ``follow_with_gate``'s locked transaction. Admins are not exempt: a
tripped breaker should stop the operator's own clicks too.

``NewFeedPrecheck`` is the fast, non-authoritative check the add paths run
*before* creating a podcast row, so a paused add doesn't leave one behind.
"""

from dataclasses import dataclass
from datetime import datetime
from typing import TYPE_CHECKING, Optional

from structlog import get_logger

from ...repositories.podcast_follower_repository import (
    ALLOW_FOLLOW,
    FollowDecider,
    FollowDecision,
    FollowFacts,
    allow_all,
)
from ...repositories.usage_event_repository import UsageRecord

if TYPE_CHECKING:
    from ...models.podcast import Podcast
    from ...repositories.podcast_follower_repository import PodcastFollowerRepository
    from .quota_service import QuotaService
    from .settings_service import SettingsService

logger = get_logger(__name__)


@dataclass(frozen=True)
class FollowGate:
    """What ``follow_with_gate`` needs for one follow: the decider, the
    start of the usage window to count (None: no ledger, no follow quotas),
    and the instant the limits were measured at, which the follow and its
    ledger rows are stamped with so they fall in the same window."""

    decide: FollowDecider
    usage_since: Optional[datetime] = None
    now: Optional[datetime] = None


UNGATED = FollowGate(allow_all)


class FollowPolicy:
    def __init__(self, settings: "SettingsService", quota: Optional["QuotaService"] = None) -> None:
        self._settings = settings
        self._quota = quota

    def bind(self, user_id: str, podcast_id: str) -> FollowGate:
        """The gate for one follow. Settings and limits are read here,
        outside the repository's transaction; the decider does no I/O."""
        paused = self._settings.refusal("new_feeds")
        limits = self._quota.follow_limits(user_id) if self._quota is not None else None

        def decide(facts: FollowFacts) -> FollowDecision:
            introducing = facts.podcast_followers == 0
            if introducing and paused is not None:
                return FollowDecision(refusal=paused)
            if limits is None:
                return ALLOW_FOLLOW
            breach = limits.breach(facts, introducing=introducing)
            if breach is not None:
                if limits.enforce:
                    return FollowDecision(
                        refusal=breach, refusal_records=(UsageRecord("quota_exceeded", subject_id=breach.quota),)
                    )
                logger.warning("quota_would_refuse", user_id=user_id, quota=breach.quota, limit=breach.limit)
            records = [UsageRecord("follow", subject_id=podcast_id)]
            if introducing:
                records.append(UsageRecord("feed_introduced", subject_id=podcast_id))
            return FollowDecision(usage_records=tuple(records))

        if limits is None:
            return FollowGate(decide)
        return FollowGate(decide, usage_since=limits.window_start, now=limits.now)


class NewFeedPrecheck:
    def __init__(
        self,
        settings: "SettingsService",
        followers: "PodcastFollowerRepository",
        quota: Optional["QuotaService"] = None,
    ) -> None:
        self._settings = settings
        self._followers = followers
        self._quota = quota

    def check(self, existing: Optional["Podcast"], *, will_follow: bool, user_id: Optional[str] = None) -> None:
        """Called once the URL is canonical and before any row is written.

        Refuses an unknown feed (the add would create it), and — when the
        caller is about to follow — a known feed nobody follows. Browsing a
        known dormant feed (``will_follow=False``) stays allowed. With a
        ``user_id`` about to follow, also refuses what the follow quotas
        would (read-only: the follow's own check stays authoritative), so
        an over-quota add writes no row and fetches no feed.
        """
        followers = self._followers.count_by_podcast(existing.id) if existing is not None else 0
        refusal = self._settings.refusal("new_feeds")
        if refusal is not None and (existing is None or (will_follow and followers == 0)):
            raise refusal
        if not will_follow or user_id is None or self._quota is None:
            return
        if existing is not None and self._followers.exists(user_id, existing.id):
            return  # re-adding a followed feed adds nothing
        self._quota.check_follow(
            user_id,
            follows_now=len(self._followers.get_followed_podcast_ids(user_id)),
            introducing=followers == 0,
        )
