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

"""Shared spec #104 fixtures: a real SQLite install with the abuse services
wired exactly as ``build_abuse_services`` wires them in production."""

from __future__ import annotations

import uuid
from datetime import datetime, timezone
from types import SimpleNamespace

import pytest

from thestill.models.podcast import Episode, Podcast
from thestill.models.user import PodcastFollower, User
from thestill.repositories.factory import make_repositories
from thestill.services.abuse.wiring import build_abuse_services
from thestill.services.follower_service import FollowerService
from thestill.utils.config import Config

NOW = datetime(2026, 10, 18, 12, 0, tzinfo=timezone.utc)


def make_world(tmp_path, *, multi_user: bool = True, clock=None, **config_overrides) -> SimpleNamespace:
    config = Config(
        storage_path=tmp_path,
        multi_user=multi_user,
        jwt_secret_key="k" * 32,
        cookie_secure=False,
        **config_overrides,
    )
    repos = make_repositories(config)
    abuse = build_abuse_services(config, repos, **({"clock": clock} if clock else {}))
    world = SimpleNamespace(config=config, repos=repos, abuse=abuse, clock=clock)

    def add_user(email: str, *, is_admin: bool = False) -> User:
        repos.user.save(User(email=email, is_admin=is_admin, created_at=NOW))
        return repos.user.get_by_email(email)

    def add_podcast(slug: str, episodes: int = 0) -> tuple[str, list[str]]:
        podcast_id = str(uuid.uuid4())
        episode_ids = [str(uuid.uuid4()) for _ in range(episodes)]
        repos.podcast.save(
            Podcast(
                id=podcast_id,
                rss_url=f"https://example.com/{slug}.xml",
                title=slug,
                description="",
                episodes=[
                    Episode(
                        id=eid,
                        external_id=f"{slug}-{i}",
                        title=f"{slug} {i}",
                        description="",
                        pub_date=NOW,
                        audio_url=f"https://example.com/{slug}/{i}.mp3",
                        duration=60,
                    )
                    for i, eid in enumerate(episode_ids)
                ],
            )
        )
        return podcast_id, episode_ids

    def follow(user_id: str, podcast_id: str, *, at: datetime = NOW) -> None:
        repos.follower.add(PodcastFollower(user_id=user_id, podcast_id=podcast_id, created_at=at))

    world.add_user = add_user
    world.add_podcast = add_podcast
    world.follow = follow
    world.follower_service = FollowerService(repos.follower, repos.podcast, follow_policy=abuse.follow_policy)
    return world


@pytest.fixture
def world(tmp_path):
    return make_world(tmp_path)
