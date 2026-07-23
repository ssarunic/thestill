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

"""Spec #63 — ``add_podcast_and_auto_follow`` orchestration.

Single-user mode must follow the default user on add (or the universal
follower gate would leave the new podcast permanently unrefreshed);
multi-user mode must not follow anyone.
"""

from types import SimpleNamespace
from unittest.mock import MagicMock

from thestill.services.podcast_add import add_podcast_and_auto_follow


def _services(multi_user: bool, podcast):
    podcast_service = MagicMock()
    podcast_service.add_podcast.return_value = podcast
    follower_service = MagicMock()
    auth_service = MagicMock()
    auth_service.get_or_create_default_user.return_value = SimpleNamespace(id="default-user")
    config = SimpleNamespace(multi_user=multi_user)
    return podcast_service, follower_service, auth_service, config


def test_single_user_mode_follows_default_user():
    podcast = SimpleNamespace(id="pod-1")
    podcast_service, follower_service, auth_service, config = _services(False, podcast)

    result = add_podcast_and_auto_follow(podcast_service, follower_service, auth_service, config, "https://x/rss")

    assert result is podcast
    follower_service.follow_best_effort.assert_called_once_with("default-user", "pod-1")


def test_multi_user_mode_never_follows():
    podcast = SimpleNamespace(id="pod-1")
    podcast_service, follower_service, auth_service, config = _services(True, podcast)

    result = add_podcast_and_auto_follow(podcast_service, follower_service, auth_service, config, "https://x/rss")

    assert result is podcast
    follower_service.follow_best_effort.assert_not_called()
    auth_service.get_or_create_default_user.assert_not_called()


def test_failed_add_never_follows():
    podcast_service, follower_service, auth_service, config = _services(False, None)

    result = add_podcast_and_auto_follow(podcast_service, follower_service, auth_service, config, "https://x/rss")

    assert result is None
    follower_service.follow_best_effort.assert_not_called()


def test_idempotent_re_add_still_returns_podcast():
    """add_podcast returns the existing row on re-add; follow_best_effort
    tolerates already-following, so the orchestration must simply succeed."""
    podcast = SimpleNamespace(id="pod-1")
    podcast_service, follower_service, auth_service, config = _services(False, podcast)
    follower_service.follow_best_effort.return_value = True

    first = add_podcast_and_auto_follow(podcast_service, follower_service, auth_service, config, "https://x/rss")
    second = add_podcast_and_auto_follow(podcast_service, follower_service, auth_service, config, "https://x/rss")

    assert first is podcast and second is podcast
    assert follower_service.follow_best_effort.call_count == 2
