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

"""Spec #104 §1.3 — the follow gate through ``FollowerService``."""

from __future__ import annotations

import pytest

from thestill.services.abuse.actor import Actor
from thestill.services.abuse.errors import FeaturePausedError, ReadOnlyError
from thestill.services.follower_service import AlreadyFollowingError


def _pause_new_feeds(world):
    world.abuse.settings.set("new_feeds_enabled", False, actor=Actor.cli())


def test_follow_of_a_dormant_feed_is_refused_when_paused(world):
    user = world.add_user("u@example.com")
    pid, _ = world.add_podcast("dormant")
    _pause_new_feeds(world)

    with pytest.raises(FeaturePausedError):
        world.follower_service.follow(user.id, pid)
    assert world.repos.follower.count_by_podcast(pid) == 0


def test_follow_of_a_followed_feed_still_works_when_paused(world):
    first, second = world.add_user("a@example.com"), world.add_user("b@example.com")
    pid, _ = world.add_podcast("popular")
    world.follow(first.id, pid)
    _pause_new_feeds(world)

    world.follower_service.follow(second.id, pid)
    assert world.repos.follower.count_by_podcast(pid) == 2
    with pytest.raises(AlreadyFollowingError):
        world.follower_service.follow(second.id, pid)


def test_admins_are_not_exempt(world):
    admin = world.add_user("op@example.com", is_admin=True)
    pid, _ = world.add_podcast("dormant")
    world.abuse.settings.set("read_only", True, actor=Actor.cli())
    with pytest.raises(ReadOnlyError):
        world.follower_service.follow(admin.id, pid)


def test_best_effort_follow_surfaces_a_refusal(world):
    user = world.add_user("u@example.com")
    pid, _ = world.add_podcast("dormant")
    _pause_new_feeds(world)
    with pytest.raises(FeaturePausedError):
        world.follower_service.follow_best_effort(user.id, pid)


def test_ungated_follow_restores_existing_state(world):
    user = world.add_user("u@example.com")
    pid, _ = world.add_podcast("dormant")
    _pause_new_feeds(world)
    world.follower_service.follow(user.id, pid, gated=False)
    assert world.repos.follower.count_by_podcast(pid) == 1


def test_new_feed_precheck(world):
    pid, _ = world.add_podcast("dormant")
    followed, _ = world.add_podcast("followed")
    world.follow(world.add_user("f@example.com").id, followed)
    podcasts = world.repos.podcast
    check = world.abuse.new_feed_precheck.check

    check(None, will_follow=True)  # allowed while new feeds are on
    _pause_new_feeds(world)
    with pytest.raises(FeaturePausedError):
        check(None, will_follow=False)  # an add would create the row
    with pytest.raises(FeaturePausedError):
        check(podcasts.get_by_id(pid), will_follow=True)
    check(podcasts.get_by_id(pid), will_follow=False)  # browsing a known feed
    check(podcasts.get_by_id(followed), will_follow=True)
