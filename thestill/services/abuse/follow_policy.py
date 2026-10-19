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

from typing import TYPE_CHECKING, Optional

from ...repositories.podcast_follower_repository import ALLOW_FOLLOW, FollowDecider, FollowDecision, FollowFacts

if TYPE_CHECKING:
    from ...models.podcast import Podcast
    from ...repositories.podcast_follower_repository import PodcastFollowerRepository
    from .settings_service import SettingsService


class FollowPolicy:
    def __init__(self, settings: "SettingsService") -> None:
        self._settings = settings

    def bind(self, user_id: str) -> FollowDecider:
        """The decider for one follow by ``user_id``. Settings are read here,
        outside the repository's transaction; the returned callback does no
        I/O. Phase 2's per-user follow quotas key on ``user_id``; the Phase 1
        gate depends only on the feed."""
        refusal = self._settings.refusal("new_feeds")

        def decide(facts: FollowFacts) -> FollowDecision:
            if facts.podcast_followers == 0 and refusal is not None:
                return FollowDecision(refusal=refusal)
            return ALLOW_FOLLOW

        return decide


class NewFeedPrecheck:
    def __init__(self, settings: "SettingsService", followers: "PodcastFollowerRepository") -> None:
        self._settings = settings
        self._followers = followers

    def check(self, existing: Optional["Podcast"], *, will_follow: bool) -> None:
        """Called once the URL is canonical and before any row is written.

        Refuses an unknown feed (the add would create it), and — when the
        caller is about to follow — a known feed nobody follows. Browsing a
        known dormant feed (``will_follow=False``) stays allowed.
        """
        refusal = self._settings.refusal("new_feeds")
        if refusal is None:
            return
        if existing is None or (will_follow and self._followers.count_by_podcast(existing.id) == 0):
            raise refusal
