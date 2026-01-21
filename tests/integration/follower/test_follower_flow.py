# Copyright 2025 thestill.me
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
Integration tests for the follow/unfollow functionality.

Tests cover:
- THES-121: End-to-end follow/unfollow flow
- THES-122: Single-user mode compatibility
"""

import uuid
from pathlib import Path

import pytest

from thestill.models.podcast import Podcast
from thestill.models.user import PodcastFollower, User
from thestill.repositories.sqlite_podcast_follower_repository import SqlitePodcastFollowerRepository
from thestill.repositories.sqlite_podcast_repository import SqlitePodcastRepository
from thestill.repositories.sqlite_user_repository import SqliteUserRepository
from thestill.services.auth_service import DEFAULT_USER_EMAIL, AuthService
from thestill.services.follower_service import (
    AlreadyFollowingError,
    FollowerService,
    NotFollowingError,
    PodcastNotFoundError,
)
from thestill.utils.config import Config


@pytest.fixture
def temp_db(tmp_path):
    """Create temporary SQLite database with all tables initialized."""
    db_path = tmp_path / "test.db"
    # SqlitePodcastRepository._ensure_database_exists() creates all tables including podcast_followers
    repo = SqlitePodcastRepository(str(db_path))
    return str(db_path)


@pytest.fixture
def podcast_repo(temp_db):
    """Create podcast repository."""
    return SqlitePodcastRepository(temp_db)


@pytest.fixture
def follower_repo(temp_db):
    """Create follower repository."""
    return SqlitePodcastFollowerRepository(temp_db)


@pytest.fixture
def user_repo(temp_db):
    """Create user repository."""
    return SqliteUserRepository(temp_db)


@pytest.fixture
def follower_service(follower_repo, podcast_repo):
    """Create follower service."""
    return FollowerService(follower_repo, podcast_repo)


@pytest.fixture
def test_user(user_repo):
    """Create and save a test user."""
    user = User(
        id=str(uuid.uuid4()),
        email="test@example.com",
        name="Test User",
    )
    user_repo.save(user)
    return user


@pytest.fixture
def test_podcast(podcast_repo):
    """Create and save a test podcast."""
    podcast = Podcast(
        id=str(uuid.uuid4()),
        title="Test Podcast",
        slug="test-podcast",
        rss_url="https://example.com/feed.xml",
        description="A test podcast",
    )
    podcast_repo.save(podcast)
    return podcast


class TestFollowerServiceFlow:
    """End-to-end tests for the follow/unfollow flow (THES-121)."""

    def test_follow_podcast(self, follower_service, test_user, test_podcast):
        """User can follow a podcast."""
        result = follower_service.follow(test_user.id, test_podcast.id)

        assert result is not None
        assert result.user_id == test_user.id
        assert result.podcast_id == test_podcast.id
        assert result.created_at is not None

    def test_follow_by_slug(self, follower_service, test_user, test_podcast):
        """User can follow a podcast by slug."""
        result = follower_service.follow_by_slug(test_user.id, test_podcast.slug)

        assert result is not None
        assert result.user_id == test_user.id
        assert result.podcast_id == test_podcast.id

    def test_is_following_returns_true(self, follower_service, test_user, test_podcast):
        """is_following returns True after follow."""
        follower_service.follow(test_user.id, test_podcast.id)

        assert follower_service.is_following(test_user.id, test_podcast.id) is True

    def test_is_following_returns_false(self, follower_service, test_user, test_podcast):
        """is_following returns False before follow."""
        assert follower_service.is_following(test_user.id, test_podcast.id) is False

    def test_is_following_by_slug(self, follower_service, test_user, test_podcast):
        """is_following_by_slug works correctly."""
        assert follower_service.is_following_by_slug(test_user.id, test_podcast.slug) is False

        follower_service.follow_by_slug(test_user.id, test_podcast.slug)

        assert follower_service.is_following_by_slug(test_user.id, test_podcast.slug) is True

    def test_unfollow_podcast(self, follower_service, test_user, test_podcast):
        """User can unfollow a podcast."""
        follower_service.follow(test_user.id, test_podcast.id)

        result = follower_service.unfollow(test_user.id, test_podcast.id)

        assert result is True
        assert follower_service.is_following(test_user.id, test_podcast.id) is False

    def test_unfollow_by_slug(self, follower_service, test_user, test_podcast):
        """User can unfollow a podcast by slug."""
        follower_service.follow_by_slug(test_user.id, test_podcast.slug)

        result = follower_service.unfollow_by_slug(test_user.id, test_podcast.slug)

        assert result is True
        assert follower_service.is_following_by_slug(test_user.id, test_podcast.slug) is False

    def test_follow_already_following_raises_error(self, follower_service, test_user, test_podcast):
        """Following an already-followed podcast raises AlreadyFollowingError."""
        follower_service.follow(test_user.id, test_podcast.id)

        with pytest.raises(AlreadyFollowingError):
            follower_service.follow(test_user.id, test_podcast.id)

    def test_unfollow_not_following_raises_error(self, follower_service, test_user, test_podcast):
        """Unfollowing a non-followed podcast raises NotFollowingError."""
        with pytest.raises(NotFollowingError):
            follower_service.unfollow(test_user.id, test_podcast.id)

    def test_follow_nonexistent_podcast_raises_error(self, follower_service, test_user):
        """Following a non-existent podcast raises PodcastNotFoundError."""
        with pytest.raises(PodcastNotFoundError):
            follower_service.follow(test_user.id, "nonexistent-id")

    def test_follow_nonexistent_podcast_by_slug_raises_error(self, follower_service, test_user):
        """Following a non-existent podcast by slug raises PodcastNotFoundError."""
        with pytest.raises(PodcastNotFoundError):
            follower_service.follow_by_slug(test_user.id, "nonexistent-slug")

    def test_get_followed_podcasts(self, follower_service, podcast_repo, test_user):
        """get_followed_podcasts returns all followed podcasts."""
        # Create multiple podcasts
        podcast1 = Podcast(
            id=str(uuid.uuid4()),
            title="Podcast 1",
            slug="podcast-1",
            rss_url="https://test.com/1.xml",
            description="Podcast 1",
        )
        podcast2 = Podcast(
            id=str(uuid.uuid4()),
            title="Podcast 2",
            slug="podcast-2",
            rss_url="https://test.com/2.xml",
            description="Podcast 2",
        )
        podcast3 = Podcast(
            id=str(uuid.uuid4()),
            title="Podcast 3",
            slug="podcast-3",
            rss_url="https://test.com/3.xml",
            description="Podcast 3",
        )
        podcast_repo.save(podcast1)
        podcast_repo.save(podcast2)
        podcast_repo.save(podcast3)

        # Follow only two
        follower_service.follow(test_user.id, podcast1.id)
        follower_service.follow(test_user.id, podcast2.id)

        followed = follower_service.get_followed_podcasts(test_user.id)

        assert len(followed) == 2
        followed_ids = {p.id for p in followed}
        assert podcast1.id in followed_ids
        assert podcast2.id in followed_ids
        assert podcast3.id not in followed_ids

    def test_get_followed_podcasts_empty(self, follower_service, test_user):
        """get_followed_podcasts returns empty list when not following any."""
        followed = follower_service.get_followed_podcasts(test_user.id)

        assert followed == []

    def test_get_follower_count(self, follower_service, user_repo, test_podcast):
        """get_follower_count returns correct count."""
        # Create multiple users
        user1 = User(id=str(uuid.uuid4()), email="user1@example.com")
        user2 = User(id=str(uuid.uuid4()), email="user2@example.com")
        user_repo.save(user1)
        user_repo.save(user2)

        # Both follow the podcast
        follower_service.follow(user1.id, test_podcast.id)
        follower_service.follow(user2.id, test_podcast.id)

        count = follower_service.get_follower_count(test_podcast.id)

        assert count == 2

    def test_get_follower_count_by_slug(self, follower_service, user_repo, test_podcast):
        """get_follower_count_by_slug returns correct count."""
        user = User(id=str(uuid.uuid4()), email="count@example.com")
        user_repo.save(user)
        follower_service.follow(user.id, test_podcast.id)

        count = follower_service.get_follower_count_by_slug(test_podcast.slug)

        assert count == 1

    def test_refollow_after_unfollow(self, follower_service, test_user, test_podcast):
        """User can re-follow a podcast after unfollowing."""
        # Follow
        follower_service.follow(test_user.id, test_podcast.id)
        assert follower_service.is_following(test_user.id, test_podcast.id) is True

        # Unfollow
        follower_service.unfollow(test_user.id, test_podcast.id)
        assert follower_service.is_following(test_user.id, test_podcast.id) is False

        # Re-follow
        result = follower_service.follow(test_user.id, test_podcast.id)
        assert result is not None
        assert follower_service.is_following(test_user.id, test_podcast.id) is True


class TestSingleUserModeCompatibility:
    """Tests for single-user mode compatibility (THES-122)."""

    @pytest.fixture
    def single_user_config(self, temp_db):
        """Create config for single-user mode."""
        return Config(
            storage_path=Path(temp_db).parent,
            database_path=temp_db,
            multi_user=False,
            jwt_secret_key="",  # Will be auto-generated
        )

    @pytest.fixture
    def auth_service(self, single_user_config, user_repo):
        """Create AuthService in single-user mode."""
        return AuthService(single_user_config, user_repo)

    def test_single_user_mode_returns_default_user(self, auth_service):
        """In single-user mode, get_current_user returns default user without token."""
        user = auth_service.get_current_user(token=None)

        assert user is not None
        assert user.email == DEFAULT_USER_EMAIL
        assert user.id is not None  # ID is dynamically generated

    def test_single_user_mode_works_without_login(self, auth_service):
        """In single-user mode, user is returned without authentication."""
        # No token provided
        user = auth_service.get_current_user(token=None)

        assert user is not None
        # User should be able to use the app

    def test_single_user_can_follow_podcasts(self, auth_service, follower_service, podcast_repo):
        """Single-user mode user can follow podcasts."""
        # Get default user
        user = auth_service.get_or_create_default_user()

        # Create a podcast
        podcast = Podcast(
            id=str(uuid.uuid4()),
            title="Single User Podcast",
            slug="single-user-podcast",
            rss_url="https://test.com/single-user.xml",
            description="Single user test podcast",
        )
        podcast_repo.save(podcast)

        # Follow the podcast
        result = follower_service.follow(user.id, podcast.id)

        assert result is not None
        assert follower_service.is_following(user.id, podcast.id) is True

    def test_single_user_can_unfollow_podcasts(self, auth_service, follower_service, podcast_repo):
        """Single-user mode user can unfollow podcasts."""
        user = auth_service.get_or_create_default_user()

        podcast = Podcast(
            id=str(uuid.uuid4()),
            title="Unfollow Test Podcast",
            slug="unfollow-test-podcast",
            rss_url="https://test.com/unfollow-test.xml",
            description="Unfollow test podcast",
        )
        podcast_repo.save(podcast)

        # Follow then unfollow
        follower_service.follow(user.id, podcast.id)
        follower_service.unfollow(user.id, podcast.id)

        assert follower_service.is_following(user.id, podcast.id) is False

    def test_default_user_id_is_consistent(self, auth_service):
        """Default user ID is consistent across calls."""
        user1 = auth_service.get_or_create_default_user()
        user2 = auth_service.get_or_create_default_user()

        assert user1.id == user2.id
        assert user1.id is not None

    def test_single_user_get_followed_podcasts(self, auth_service, follower_service, podcast_repo):
        """Single-user can retrieve their followed podcasts."""
        user = auth_service.get_or_create_default_user()

        # Create and follow podcasts
        podcast1 = Podcast(
            id=str(uuid.uuid4()), title="P1", slug="p1", rss_url="https://test.com/p1.xml", description="P1"
        )
        podcast2 = Podcast(
            id=str(uuid.uuid4()), title="P2", slug="p2", rss_url="https://test.com/p2.xml", description="P2"
        )
        podcast_repo.save(podcast1)
        podcast_repo.save(podcast2)

        follower_service.follow(user.id, podcast1.id)
        follower_service.follow(user.id, podcast2.id)

        # Get followed podcasts
        followed = follower_service.get_followed_podcasts(user.id)

        assert len(followed) == 2


class TestPodcastFollowerRepository:
    """Unit tests for PodcastFollowerRepository."""

    @pytest.fixture
    def repo_user(self, user_repo):
        """Create a user for repository tests."""
        user = User(id=str(uuid.uuid4()), email="repo-user@test.com")
        user_repo.save(user)
        return user

    @pytest.fixture
    def repo_podcast(self, podcast_repo):
        """Create a podcast for repository tests."""
        podcast = Podcast(
            id=str(uuid.uuid4()),
            title="Repo Test",
            slug="repo-test",
            rss_url="https://example.com/repo-test.xml",
            description="Repo test podcast",
        )
        podcast_repo.save(podcast)
        return podcast

    def test_add_follower(self, follower_repo, repo_user, repo_podcast):
        """Can add a follower relationship."""
        follower = PodcastFollower(
            user_id=repo_user.id,
            podcast_id=repo_podcast.id,
        )

        saved = follower_repo.add(follower)

        assert saved.id is not None
        assert saved.user_id == repo_user.id
        assert saved.podcast_id == repo_podcast.id
        assert saved.created_at is not None

    def test_exists_returns_true(self, follower_repo, repo_user, repo_podcast):
        """exists returns True when relationship exists."""
        follower = PodcastFollower(user_id=repo_user.id, podcast_id=repo_podcast.id)
        follower_repo.add(follower)

        assert follower_repo.exists(repo_user.id, repo_podcast.id) is True

    def test_exists_returns_false(self, follower_repo, repo_user, repo_podcast):
        """exists returns False when relationship doesn't exist."""
        assert follower_repo.exists(repo_user.id, repo_podcast.id) is False

    def test_remove_follower(self, follower_repo, repo_user, repo_podcast):
        """Can remove a follower relationship."""
        follower = PodcastFollower(user_id=repo_user.id, podcast_id=repo_podcast.id)
        follower_repo.add(follower)

        removed = follower_repo.remove(repo_user.id, repo_podcast.id)

        assert removed is True
        assert follower_repo.exists(repo_user.id, repo_podcast.id) is False

    def test_remove_nonexistent_returns_false(self, follower_repo, repo_user, repo_podcast):
        """Removing non-existent relationship returns False."""
        removed = follower_repo.remove(repo_user.id, repo_podcast.id)

        assert removed is False

    def test_get_followed_podcast_ids(self, follower_repo, user_repo, podcast_repo):
        """get_followed_podcast_ids returns correct IDs."""
        # Create users
        user1 = User(id=str(uuid.uuid4()), email="u1@test.com")
        user2 = User(id=str(uuid.uuid4()), email="u2@test.com")
        user_repo.save(user1)
        user_repo.save(user2)

        # Create podcasts
        p1 = Podcast(id=str(uuid.uuid4()), title="P1", slug="p1", rss_url="https://test.com/p1.xml", description="P1")
        p2 = Podcast(id=str(uuid.uuid4()), title="P2", slug="p2", rss_url="https://test.com/p2.xml", description="P2")
        p3 = Podcast(id=str(uuid.uuid4()), title="P3", slug="p3", rss_url="https://test.com/p3.xml", description="P3")
        podcast_repo.save(p1)
        podcast_repo.save(p2)
        podcast_repo.save(p3)

        # User1 follows P1 and P2, User2 follows P3
        follower_repo.add(PodcastFollower(user_id=user1.id, podcast_id=p1.id))
        follower_repo.add(PodcastFollower(user_id=user1.id, podcast_id=p2.id))
        follower_repo.add(PodcastFollower(user_id=user2.id, podcast_id=p3.id))

        ids = follower_repo.get_followed_podcast_ids(user1.id)

        assert set(ids) == {p1.id, p2.id}

    def test_count_by_podcast(self, follower_repo, user_repo, podcast_repo):
        """count_by_podcast returns correct count."""
        # Create users
        user1 = User(id=str(uuid.uuid4()), email="c1@test.com")
        user2 = User(id=str(uuid.uuid4()), email="c2@test.com")
        user3 = User(id=str(uuid.uuid4()), email="c3@test.com")
        user_repo.save(user1)
        user_repo.save(user2)
        user_repo.save(user3)

        # Create podcasts
        p1 = Podcast(
            id=str(uuid.uuid4()), title="CP1", slug="cp1", rss_url="https://test.com/cp1.xml", description="CP1"
        )
        p2 = Podcast(
            id=str(uuid.uuid4()), title="CP2", slug="cp2", rss_url="https://test.com/cp2.xml", description="CP2"
        )
        podcast_repo.save(p1)
        podcast_repo.save(p2)

        # Two users follow P1, one follows P2
        follower_repo.add(PodcastFollower(user_id=user1.id, podcast_id=p1.id))
        follower_repo.add(PodcastFollower(user_id=user2.id, podcast_id=p1.id))
        follower_repo.add(PodcastFollower(user_id=user3.id, podcast_id=p2.id))

        count = follower_repo.count_by_podcast(p1.id)

        assert count == 2

    def test_get_by_user(self, follower_repo, user_repo, podcast_repo):
        """get_by_user returns all follower relationships for user."""
        # Create users
        user1 = User(id=str(uuid.uuid4()), email="bu1@test.com")
        user2 = User(id=str(uuid.uuid4()), email="bu2@test.com")
        user_repo.save(user1)
        user_repo.save(user2)

        # Create podcasts
        p1 = Podcast(
            id=str(uuid.uuid4()), title="BP1", slug="bp1", rss_url="https://test.com/bp1.xml", description="BP1"
        )
        p2 = Podcast(
            id=str(uuid.uuid4()), title="BP2", slug="bp2", rss_url="https://test.com/bp2.xml", description="BP2"
        )
        p3 = Podcast(
            id=str(uuid.uuid4()), title="BP3", slug="bp3", rss_url="https://test.com/bp3.xml", description="BP3"
        )
        podcast_repo.save(p1)
        podcast_repo.save(p2)
        podcast_repo.save(p3)

        # Add followers
        follower_repo.add(PodcastFollower(user_id=user1.id, podcast_id=p1.id))
        follower_repo.add(PodcastFollower(user_id=user1.id, podcast_id=p2.id))
        follower_repo.add(PodcastFollower(user_id=user2.id, podcast_id=p3.id))

        followers = follower_repo.get_by_user(user1.id)

        assert len(followers) == 2
        podcast_ids = {f.podcast_id for f in followers}
        assert podcast_ids == {p1.id, p2.id}

    def test_get_by_podcast(self, follower_repo, user_repo, podcast_repo):
        """get_by_podcast returns all followers for a podcast."""
        # Create users
        user1 = User(id=str(uuid.uuid4()), email="bp1@test.com")
        user2 = User(id=str(uuid.uuid4()), email="bp2@test.com")
        user3 = User(id=str(uuid.uuid4()), email="bp3@test.com")
        user_repo.save(user1)
        user_repo.save(user2)
        user_repo.save(user3)

        # Create podcasts
        p1 = Podcast(
            id=str(uuid.uuid4()), title="FP1", slug="fp1", rss_url="https://test.com/fp1.xml", description="FP1"
        )
        p2 = Podcast(
            id=str(uuid.uuid4()), title="FP2", slug="fp2", rss_url="https://test.com/fp2.xml", description="FP2"
        )
        podcast_repo.save(p1)
        podcast_repo.save(p2)

        # Add followers
        follower_repo.add(PodcastFollower(user_id=user1.id, podcast_id=p1.id))
        follower_repo.add(PodcastFollower(user_id=user2.id, podcast_id=p1.id))
        follower_repo.add(PodcastFollower(user_id=user3.id, podcast_id=p2.id))

        followers = follower_repo.get_by_podcast(p1.id)

        assert len(followers) == 2
        user_ids = {f.user_id for f in followers}
        assert user_ids == {user1.id, user2.id}
