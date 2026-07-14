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

"""Unit tests for the podcasts list API endpoint (GET /api/podcasts)."""

from typing import Optional
from unittest.mock import MagicMock

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient

from thestill.models.podcast import Episode, Podcast
from thestill.models.user import User
from thestill.services.podcast_service import PodcastWithIndex
from thestill.web.routes import api_podcasts


@pytest.fixture
def mock_user():
    return User(id="user-1", email="alice@example.com", name="Alice")


def _podcast(podcast_id: str, title: str, author: Optional[str] = None) -> PodcastWithIndex:
    return PodcastWithIndex(
        id=podcast_id,
        index=1,
        title=title,
        description="d",
        rss_url=f"https://example.com/{podcast_id}.rss",
        slug=podcast_id,
        author=author,
    )


@pytest.fixture
def mock_app_state():
    state = MagicMock()
    return state


@pytest.fixture
def test_app(mock_app_state, mock_user):
    app = FastAPI()
    app.include_router(api_podcasts.router, prefix="/api/podcasts")
    app.dependency_overrides[api_podcasts.get_app_state] = lambda: mock_app_state
    app.dependency_overrides[api_podcasts.require_auth] = lambda: mock_user
    return app


@pytest.fixture
def client(test_app):
    return TestClient(test_app)


def _setup_followed(mock_app_state, podcasts):
    mock_app_state.follower_repository.get_followed_podcast_ids.return_value = [p.id for p in podcasts]
    mock_app_state.podcast_service.get_podcasts.return_value = podcasts


class TestListPodcasts:
    def test_returns_followed_podcasts(self, client, mock_app_state):
        _setup_followed(mock_app_state, [_podcast("pod-1", "Lex Fridman"), _podcast("pod-2", "Hard Fork")])

        response = client.get("/api/podcasts")

        assert response.status_code == 200
        data = response.json()
        assert data["total"] == 2
        assert [p["title"] for p in data["podcasts"]] == ["Lex Fridman", "Hard Fork"]
        assert all(p["is_following"] for p in data["podcasts"])

    def test_excludes_unfollowed_podcasts(self, client, mock_app_state):
        followed = _podcast("pod-1", "Lex Fridman")
        unfollowed = _podcast("pod-2", "Hard Fork")
        mock_app_state.follower_repository.get_followed_podcast_ids.return_value = ["pod-1"]
        mock_app_state.podcast_service.get_podcasts.return_value = [followed, unfollowed]

        response = client.get("/api/podcasts")

        data = response.json()
        assert data["total"] == 1
        assert data["podcasts"][0]["id"] == "pod-1"


class TestListPodcastsQueryFilter:
    def test_filters_by_title_case_insensitive(self, client, mock_app_state):
        _setup_followed(
            mock_app_state,
            [_podcast("pod-1", "Hard Fork"), _podcast("pod-2", "The Daily"), _podcast("pod-3", "Hardcore History")],
        )

        response = client.get("/api/podcasts?q=hard")

        data = response.json()
        assert data["total"] == 2
        assert [p["title"] for p in data["podcasts"]] == ["Hard Fork", "Hardcore History"]

    def test_filters_by_author(self, client, mock_app_state):
        _setup_followed(
            mock_app_state,
            [_podcast("pod-1", "Hard Fork", author="NYT"), _podcast("pod-2", "The Daily", author="Spotify")],
        )

        response = client.get("/api/podcasts?q=nyt")

        data = response.json()
        assert data["total"] == 1
        assert data["podcasts"][0]["title"] == "Hard Fork"

    def test_handles_none_author(self, client, mock_app_state):
        _setup_followed(mock_app_state, [_podcast("pod-1", "Hard Fork", author=None)])

        response = client.get("/api/podcasts?q=fork")

        assert response.status_code == 200
        assert response.json()["total"] == 1

    def test_total_and_pagination_reflect_filtered_set(self, client, mock_app_state):
        _setup_followed(mock_app_state, [_podcast(f"pod-{i}", f"Tech Show {i}") for i in range(5)])

        response = client.get("/api/podcasts?q=tech&limit=2&offset=2")

        data = response.json()
        assert data["total"] == 5
        assert [p["title"] for p in data["podcasts"]] == ["Tech Show 2", "Tech Show 3"]

    def test_blank_query_returns_all(self, client, mock_app_state):
        _setup_followed(mock_app_state, [_podcast("pod-1", "Hard Fork"), _podcast("pod-2", "The Daily")])

        response = client.get("/api/podcasts?q=%20%20")

        assert response.json()["total"] == 2

    def test_no_match_returns_empty(self, client, mock_app_state):
        _setup_followed(mock_app_state, [_podcast("pod-1", "Hard Fork")])

        response = client.get("/api/podcasts?q=zzz")

        data = response.json()
        assert data["total"] == 0
        assert data["podcasts"] == []


class TestEpisodeSummaryLanguages:
    @staticmethod
    def _episode_result():
        episode = Episode(
            title="Croatian episode",
            description="Description",
            audio_url="https://example.com/episode.mp3",
            external_id="episode-1",
            slug="croatian-episode",
            summary_path="show/episode_summary.md",
        )
        podcast = Podcast(
            title="Croatian show",
            description="Description",
            rss_url="https://example.com/show.rss",
            slug="croatian-show",
            language="hr",
            episodes=[episode],
        )
        return podcast, episode

    def test_original_response_exposes_language_metadata(self, client, mock_app_state):
        podcast, episode = self._episode_result()
        mock_app_state.repository.get_episode_by_slug.return_value = (podcast, episode)
        mock_app_state.podcast_service.get_recorded_summary_language.return_value = "hr"
        mock_app_state.podcast_service.get_summary_for_episode.return_value = "## Sažetak"
        mock_app_state.podcast_service.get_summary_citations_for_episode.return_value = []
        mock_app_state.podcast_service.get_available_summary_languages.return_value = ["hr"]

        response = client.get("/api/podcasts/croatian-show/episodes/croatian-episode/summary")

        assert response.status_code == 200
        data = response.json()
        assert data["language"] == "hr"
        assert data["podcast_language"] == "hr"
        assert data["canonical_language"] == "hr"
        assert data["available_languages"] == ["hr"]
        mock_app_state.podcast_service.get_summary_for_episode.assert_called_once_with(
            episode,
            language="hr",
            canonical_language="hr",
        )

    def test_uncached_requested_language_is_generated(self, client, mock_app_state, monkeypatch):
        podcast, episode = self._episode_result()
        mock_app_state.repository.get_episode_by_slug.return_value = (podcast, episode)
        mock_app_state.podcast_service.get_recorded_summary_language.return_value = "hr"
        mock_app_state.podcast_service.get_summary_for_episode.return_value = None
        mock_app_state.podcast_service.get_or_create_summary_translation.return_value = "## Summary"
        mock_app_state.podcast_service.get_summary_citations_for_episode.return_value = []
        mock_app_state.podcast_service.get_available_summary_languages.return_value = ["hr", "en"]
        provider = object()
        monkeypatch.setattr(
            "thestill.core.llm_provider.create_llm_provider_from_config",
            lambda _config: provider,
        )

        response = client.get("/api/podcasts/croatian-show/episodes/croatian-episode/summary?lang=en")

        assert response.status_code == 200
        assert response.json()["content"] == "## Summary"
        assert response.json()["language"] == "en"
        mock_app_state.podcast_service.get_or_create_summary_translation.assert_called_once_with(
            episode,
            source_language="hr",
            target_language="en",
            provider=provider,
        )

    def test_legacy_english_is_canonical_and_croatian_is_the_translation(self, client, mock_app_state, monkeypatch):
        podcast, episode = self._episode_result()
        mock_app_state.repository.get_episode_by_slug.return_value = (podcast, episode)
        mock_app_state.podcast_service.get_recorded_summary_language.return_value = "en"
        mock_app_state.podcast_service.get_summary_for_episode.side_effect = ["## The Gist", None]
        mock_app_state.podcast_service.get_or_create_summary_translation.return_value = "## Ukratko"
        mock_app_state.podcast_service.get_summary_citations_for_episode.return_value = []
        mock_app_state.podcast_service.get_available_summary_languages.return_value = ["en", "hr"]
        provider = object()
        monkeypatch.setattr(
            "thestill.core.llm_provider.create_llm_provider_from_config",
            lambda _config: provider,
        )

        english = client.get("/api/podcasts/croatian-show/episodes/croatian-episode/summary")
        croatian = client.get("/api/podcasts/croatian-show/episodes/croatian-episode/summary?lang=hr")

        assert english.json()["content"] == "## The Gist"
        assert english.json()["language"] == "en"
        assert croatian.json()["content"] == "## Ukratko"
        assert croatian.json()["language"] == "hr"
        mock_app_state.podcast_service.get_or_create_summary_translation.assert_called_once_with(
            episode,
            source_language="en",
            target_language="hr",
            provider=provider,
        )
