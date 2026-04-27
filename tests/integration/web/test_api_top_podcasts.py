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

"""HTTP-level tests for ``GET /api/top-podcasts`` (spec #27).

These verify the route's query-param validation, the chain
``q -> repository.get_top_podcasts(q=...)``, and that the
``is_following`` flag is wired through end-to-end. The repo unit tests
in ``tests/unit/repositories/test_sqlite_podcast_repository.py`` cover
the SQL exhaustively — these tests own the HTTP boundary only.
"""

from __future__ import annotations

import pytest

from .conftest import seed_top_chart


@pytest.fixture
def two_pods(app_state):
    seed_top_chart(
        app_state,
        "us",
        [
            {"rank": 1, "name": "The Rest Is History", "artist": "Goalhanger", "rss_url": "https://r/1"},
            {"rank": 2, "name": "Crime Junkie", "artist": "audiochuck", "rss_url": "https://r/2"},
        ],
    )


def test_returns_chart_for_default_region(client, two_pods):
    response = client.get("/api/top-podcasts")

    assert response.status_code == 200
    body = response.json()
    assert body["region"] == "us"
    assert body["count"] == 2
    assert [row["rank"] for row in body["top_podcasts"]] == [1, 2]


def test_q_filters_by_name(client, two_pods):
    response = client.get("/api/top-podcasts?q=rest")

    assert response.status_code == 200
    body = response.json()
    assert body["count"] == 1
    assert body["top_podcasts"][0]["name"] == "The Rest Is History"


def test_q_filters_by_artist(client, two_pods):
    response = client.get("/api/top-podcasts?q=audiochuck")

    assert response.status_code == 200
    body = response.json()
    assert body["count"] == 1
    assert body["top_podcasts"][0]["artist"] == "audiochuck"


def test_q_case_insensitive(client, two_pods):
    response = client.get("/api/top-podcasts?q=REST")

    assert response.json()["count"] == 1


def test_whitespace_only_q_treated_as_unfiltered(client, two_pods):
    # FastAPI lets through `q="   "` because it has length > 0; the route
    # should strip and fall back to no filter.
    response = client.get("/api/top-podcasts?q=%20%20%20")

    assert response.status_code == 200
    assert response.json()["count"] == 2


def test_q_empty_string_rejected(client, two_pods):
    # ``min_length=1`` on the route param → 422 from FastAPI's validator.
    response = client.get("/api/top-podcasts?q=")

    assert response.status_code == 422


def test_q_too_long_rejected(client, two_pods):
    response = client.get("/api/top-podcasts?q=" + ("a" * 101))

    assert response.status_code == 422


def test_is_following_field_present_for_anonymous(client, two_pods, app_state):
    # In single-user mode (default for these tests) the auth service
    # returns the default user. That user follows nothing in this fixture
    # so every row should report ``is_following=False`` — the field's
    # presence is what matters for the contract.
    response = client.get("/api/top-podcasts")

    body = response.json()
    assert all("is_following" in row for row in body["top_podcasts"])
    assert all(row["is_following"] is False for row in body["top_podcasts"])


def test_is_following_true_when_user_follows(client, two_pods, app_state):
    """End-to-end: follow via the service, then GET, expect ``is_following=True``."""
    user = app_state.auth_service.get_or_create_default_user()

    # Materialise a Podcast row whose rss_url matches the top-podcast,
    # then follow it. ``add_podcast`` is the proper service entry point but
    # would also try to fetch the feed; for an HTTP-level test we just
    # plant the rows directly via the repository.
    from datetime import datetime, timezone

    from thestill.models.podcast import Podcast

    now = datetime.now(timezone.utc)
    podcast = Podcast(
        id="cccccccc-cccc-cccc-cccc-cccccccccccc",
        rss_url="https://r/1",
        title="The Rest Is History",
        description="",
        created_at=now,
        episodes=[],
    )
    app_state.repository.save(podcast)
    app_state.follower_service.follow(user.id, podcast.id)

    response = client.get("/api/top-podcasts")

    body = response.json()
    by_rank = {row["rank"]: row for row in body["top_podcasts"]}
    assert by_rank[1]["is_following"] is True
    assert by_rank[2]["is_following"] is False


def test_unknown_region_falls_back_to_first_available(client, two_pods):
    """Asking for a region we don't have data for returns the fallback chart."""
    response = client.get("/api/top-podcasts?region=zz")

    body = response.json()
    # Only ``us`` is seeded → that's the only available region → resolved == us.
    assert body["region"] == "us"
    assert body["count"] == 2
