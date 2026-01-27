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
Unit tests for SQLite digest repository.

Tests CRUD operations and episode tracking for THES-153.
"""

from datetime import datetime, timedelta, timezone

import pytest

from thestill.models.digest import Digest, DigestStatus
from thestill.repositories.sqlite_digest_repository import SqliteDigestRepository
from thestill.repositories.sqlite_podcast_repository import SqlitePodcastRepository

# Test user ID constant (matches user created in temp_db fixture)
TEST_USER_ID = "aaaaaaaa-aaaa-aaaa-aaaa-aaaaaaaaaaaa"


@pytest.fixture
def temp_db(tmp_path):
    """Create temporary SQLite database with schema and test user."""
    import sqlite3

    db_path = tmp_path / "test.db"
    # SqlitePodcastRepository creates the schema including digests tables
    SqlitePodcastRepository(str(db_path))

    # Insert test user (required for user_id FK constraint)
    conn = sqlite3.connect(str(db_path))
    conn.execute(
        "INSERT INTO users (id, email) VALUES (?, ?)",
        (TEST_USER_ID, "test@example.com"),
    )
    conn.commit()
    conn.close()

    return SqliteDigestRepository(str(db_path))


@pytest.fixture
def sample_digest():
    """Create sample digest for testing."""
    now = datetime.now(timezone.utc)
    return Digest(
        id="550e8400-e29b-41d4-a716-446655440000",
        user_id=TEST_USER_ID,
        created_at=now,
        updated_at=now,
        period_start=now - timedelta(days=7),
        period_end=now,
        status=DigestStatus.PENDING,
        episode_ids=["ep-1", "ep-2", "ep-3"],
        episodes_total=3,
    )


@pytest.fixture
def completed_digest():
    """Create a completed digest for testing."""
    now = datetime.now(timezone.utc)
    return Digest(
        id="660e8400-e29b-41d4-a716-446655440001",
        user_id=TEST_USER_ID,
        created_at=now - timedelta(hours=1),
        updated_at=now,
        period_start=now - timedelta(days=7),
        period_end=now,
        status=DigestStatus.COMPLETED,
        file_path="digest_2025-01-26_120000.md",
        episode_ids=["ep-4", "ep-5"],
        episodes_total=2,
        episodes_completed=2,
        episodes_failed=0,
        processing_time_seconds=754.5,
    )


@pytest.fixture
def make_digest():
    """Factory fixture to create digests with custom parameters."""
    import uuid

    def _make_digest(
        digest_id: str = None,
        user_id: str = TEST_USER_ID,
        status: DigestStatus = DigestStatus.PENDING,
    ) -> Digest:
        now = datetime.now(timezone.utc)
        return Digest(
            id=digest_id or str(uuid.uuid4()),
            user_id=user_id,
            created_at=now,
            updated_at=now,
            period_start=now - timedelta(days=7),
            period_end=now,
            status=status,
            episode_ids=[],
            episodes_total=0,
        )

    return _make_digest


# ============================================================================
# Basic CRUD Tests
# ============================================================================


class TestDigestRepositorySave:
    """Tests for save operations."""

    def test_save_creates_new_digest(self, temp_db, sample_digest):
        """Test saving a new digest."""
        saved = temp_db.save(sample_digest)

        assert saved.id == sample_digest.id
        assert saved.status == DigestStatus.PENDING

    def test_save_persists_episode_ids(self, temp_db, sample_digest):
        """Test that episode IDs are persisted."""
        temp_db.save(sample_digest)

        found = temp_db.get_by_id(sample_digest.id)
        assert found is not None
        assert set(found.episode_ids) == {"ep-1", "ep-2", "ep-3"}

    def test_save_upsert_updates_existing(self, temp_db, sample_digest):
        """Test that save() updates existing digest on conflict."""
        temp_db.save(sample_digest)

        # Update and save again
        sample_digest.status = DigestStatus.COMPLETED
        sample_digest.episodes_completed = 3
        sample_digest.file_path = "new_path.md"
        temp_db.save(sample_digest)

        found = temp_db.get_by_id(sample_digest.id)
        assert found.status == DigestStatus.COMPLETED
        assert found.episodes_completed == 3
        assert found.file_path == "new_path.md"

    def test_save_updates_episode_ids(self, temp_db, sample_digest):
        """Test that episode IDs are replaced on update."""
        temp_db.save(sample_digest)

        # Change episode IDs
        sample_digest.episode_ids = ["ep-new-1", "ep-new-2"]
        temp_db.save(sample_digest)

        found = temp_db.get_by_id(sample_digest.id)
        assert set(found.episode_ids) == {"ep-new-1", "ep-new-2"}


class TestDigestRepositoryGet:
    """Tests for get operations."""

    def test_get_by_id_returns_digest(self, temp_db, sample_digest):
        """Test retrieving digest by ID."""
        temp_db.save(sample_digest)

        found = temp_db.get_by_id(sample_digest.id)

        assert found is not None
        assert found.id == sample_digest.id
        assert found.status == sample_digest.status

    def test_get_by_id_returns_none_for_missing(self, temp_db):
        """Test that get_by_id returns None for non-existent digest."""
        found = temp_db.get_by_id("non-existent-id")
        assert found is None

    def test_get_all_returns_digests_ordered_by_created_at(self, temp_db, sample_digest, completed_digest):
        """Test that get_all returns digests in descending order."""
        temp_db.save(completed_digest)  # Created 1 hour ago
        temp_db.save(sample_digest)  # Created now

        all_digests = temp_db.get_all()

        assert len(all_digests) == 2
        assert all_digests[0].id == sample_digest.id  # More recent first

    def test_get_all_with_status_filter(self, temp_db, sample_digest, completed_digest):
        """Test filtering by status."""
        temp_db.save(sample_digest)  # PENDING
        temp_db.save(completed_digest)  # COMPLETED

        pending = temp_db.get_all(status=DigestStatus.PENDING)
        completed = temp_db.get_all(status=DigestStatus.COMPLETED)

        assert len(pending) == 1
        assert pending[0].id == sample_digest.id
        assert len(completed) == 1
        assert completed[0].id == completed_digest.id

    def test_get_all_with_limit_and_offset(self, temp_db):
        """Test pagination with limit and offset."""
        # Create 5 digests
        now = datetime.now(timezone.utc)
        for i in range(5):
            digest = Digest(
                id=f"00000000-0000-0000-0000-00000000000{i}",
                user_id=TEST_USER_ID,
                period_start=now - timedelta(days=7),
                period_end=now,
                created_at=now - timedelta(hours=i),  # Different creation times
            )
            temp_db.save(digest)

        # Get first 2
        first_page = temp_db.get_all(limit=2, offset=0)
        assert len(first_page) == 2

        # Get next 2
        second_page = temp_db.get_all(limit=2, offset=2)
        assert len(second_page) == 2

        # Verify no overlap
        first_ids = {d.id for d in first_page}
        second_ids = {d.id for d in second_page}
        assert first_ids.isdisjoint(second_ids)

    def test_get_latest(self, temp_db, sample_digest, completed_digest):
        """Test getting the most recent digest."""
        temp_db.save(completed_digest)  # Created 1 hour ago
        temp_db.save(sample_digest)  # Created now

        latest = temp_db.get_latest()

        assert latest is not None
        assert latest.id == sample_digest.id

    def test_get_latest_returns_none_when_empty(self, temp_db):
        """Test that get_latest returns None when no digests exist."""
        latest = temp_db.get_latest()
        assert latest is None


class TestDigestRepositoryDelete:
    """Tests for delete operations."""

    def test_delete_removes_digest(self, temp_db, sample_digest):
        """Test deleting a digest."""
        temp_db.save(sample_digest)

        deleted = temp_db.delete(sample_digest.id)

        assert deleted is True
        assert temp_db.get_by_id(sample_digest.id) is None

    def test_delete_returns_false_for_missing(self, temp_db):
        """Test that delete returns False for non-existent digest."""
        deleted = temp_db.delete("non-existent-id")
        assert deleted is False

    def test_delete_removes_episode_associations(self, temp_db, sample_digest):
        """Test that deleting digest removes episode associations."""
        temp_db.save(sample_digest)

        temp_db.delete(sample_digest.id)

        # Verify episode associations are gone
        episode_ids = temp_db.get_episodes_in_digest(sample_digest.id)
        assert len(episode_ids) == 0


# ============================================================================
# Count Tests
# ============================================================================


class TestDigestRepositoryCount:
    """Tests for count operations."""

    def test_count_returns_zero_when_empty(self, temp_db):
        """Test that count returns 0 when no digests exist."""
        count = temp_db.count()
        assert count == 0

    def test_count_returns_total(self, temp_db, sample_digest, make_digest):
        """Test that count returns total number of digests."""
        temp_db.save(sample_digest)
        temp_db.save(make_digest())

        count = temp_db.count()
        assert count == 2

    def test_count_filters_by_status(self, temp_db, make_digest):
        """Test that count filters by status."""
        d1 = make_digest(status=DigestStatus.COMPLETED)
        d2 = make_digest(status=DigestStatus.PENDING)
        d3 = make_digest(status=DigestStatus.COMPLETED)
        temp_db.save(d1)
        temp_db.save(d2)
        temp_db.save(d3)

        completed_count = temp_db.count(status=DigestStatus.COMPLETED)
        pending_count = temp_db.count(status=DigestStatus.PENDING)

        assert completed_count == 2
        assert pending_count == 1

    def test_count_filters_by_user_id(self, temp_db, make_digest):
        """Test that count filters by user_id."""
        # Create digests for the test user
        d1 = make_digest()
        d2 = make_digest()
        temp_db.save(d1)
        temp_db.save(d2)

        # Count for the test user should return 2
        user_count = temp_db.count(user_id=TEST_USER_ID)
        assert user_count == 2

        # Count for a non-existent user should return 0
        other_count = temp_db.count(user_id="non-existent-user-id")
        assert other_count == 0

    def test_count_filters_by_both_status_and_user(self, temp_db, make_digest):
        """Test that count filters by both status and user_id."""
        d1 = make_digest(status=DigestStatus.COMPLETED)
        d2 = make_digest(status=DigestStatus.PENDING)
        d3 = make_digest(status=DigestStatus.COMPLETED)
        temp_db.save(d1)
        temp_db.save(d2)
        temp_db.save(d3)

        # Count completed digests for the test user
        count = temp_db.count(status=DigestStatus.COMPLETED, user_id=TEST_USER_ID)
        assert count == 2

        # Count pending digests for the test user
        pending_count = temp_db.count(status=DigestStatus.PENDING, user_id=TEST_USER_ID)
        assert pending_count == 1


# ============================================================================
# Episode Tracking Tests
# ============================================================================


class TestDigestRepositoryEpisodeTracking:
    """Tests for episode tracking operations."""

    def test_get_episodes_in_digest(self, temp_db, sample_digest):
        """Test getting episode IDs from a digest."""
        temp_db.save(sample_digest)

        episode_ids = temp_db.get_episodes_in_digest(sample_digest.id)

        assert set(episode_ids) == {"ep-1", "ep-2", "ep-3"}

    def test_get_episodes_in_digest_empty_for_missing(self, temp_db):
        """Test that missing digest returns empty list."""
        episode_ids = temp_db.get_episodes_in_digest("non-existent")
        assert episode_ids == []

    def test_is_episode_in_any_digest_true(self, temp_db, sample_digest):
        """Test detecting episode in digest."""
        temp_db.save(sample_digest)

        assert temp_db.is_episode_in_any_digest("ep-1") is True
        assert temp_db.is_episode_in_any_digest("ep-2") is True

    def test_is_episode_in_any_digest_false(self, temp_db, sample_digest):
        """Test episode not in any digest."""
        temp_db.save(sample_digest)

        assert temp_db.is_episode_in_any_digest("not-included") is False

    def test_is_episode_in_any_digest_empty_db(self, temp_db):
        """Test with empty database."""
        assert temp_db.is_episode_in_any_digest("any-episode") is False

    def test_get_digests_containing_episode(self, temp_db):
        """Test finding digests containing a specific episode."""
        now = datetime.now(timezone.utc)

        # Create two digests with overlapping episodes
        digest1 = Digest(
            id="11111111-1111-1111-1111-111111111111",
            user_id=TEST_USER_ID,
            period_start=now - timedelta(days=7),
            period_end=now,
            episode_ids=["shared-ep", "ep-1"],
        )
        digest2 = Digest(
            id="22222222-2222-2222-2222-222222222222",
            user_id=TEST_USER_ID,
            period_start=now - timedelta(days=7),
            period_end=now,
            episode_ids=["shared-ep", "ep-2"],
        )
        temp_db.save(digest1)
        temp_db.save(digest2)

        # Find digests containing shared episode
        containing = temp_db.get_digests_containing_episode("shared-ep")

        assert len(containing) == 2
        ids = {d.id for d in containing}
        assert ids == {"11111111-1111-1111-1111-111111111111", "22222222-2222-2222-2222-222222222222"}

    def test_get_digests_containing_episode_none(self, temp_db, sample_digest):
        """Test no digests containing episode."""
        temp_db.save(sample_digest)

        containing = temp_db.get_digests_containing_episode("not-in-any")

        assert len(containing) == 0


# ============================================================================
# Period Query Tests
# ============================================================================


class TestDigestRepositoryPeriodQueries:
    """Tests for period-based queries."""

    def test_get_digests_in_period_overlap(self, temp_db):
        """Test finding digests with overlapping periods."""
        now = datetime.now(timezone.utc)

        # Digest covering last 7 days
        digest = Digest(
            id="bbbbbbbb-bbbb-bbbb-bbbb-bbbbbbbbbbbb",
            user_id=TEST_USER_ID,
            period_start=now - timedelta(days=7),
            period_end=now,
        )
        temp_db.save(digest)

        # Query overlapping period
        results = temp_db.get_digests_in_period(
            start=now - timedelta(days=3),
            end=now + timedelta(days=1),
        )

        assert len(results) == 1
        assert results[0].id == digest.id

    def test_get_digests_in_period_no_overlap(self, temp_db):
        """Test no digests found for non-overlapping period."""
        now = datetime.now(timezone.utc)

        # Digest covering old period (14-7 days ago)
        digest = Digest(
            id="cccccccc-cccc-cccc-cccc-cccccccccccc",
            user_id=TEST_USER_ID,
            period_start=now - timedelta(days=14),
            period_end=now - timedelta(days=7),
        )
        temp_db.save(digest)

        # Query period that doesn't overlap
        results = temp_db.get_digests_in_period(
            start=now - timedelta(days=3),
            end=now,
        )

        assert len(results) == 0


# ============================================================================
# User Filtering Tests
# ============================================================================


class TestDigestRepositoryUserFiltering:
    """Tests for user_id filtering."""

    @pytest.fixture
    def db_with_users(self, tmp_path):
        """Create database with test users."""
        import sqlite3

        db_path = tmp_path / "test_users.db"
        # Create schema via SqlitePodcastRepository
        SqlitePodcastRepository(str(db_path))
        digest_repo = SqliteDigestRepository(str(db_path))

        # Insert test users directly (must be valid 36-char UUIDs)
        conn = sqlite3.connect(str(db_path))
        conn.execute(
            "INSERT INTO users (id, email) VALUES (?, ?)",
            ("aaaaaaaa-aaaa-aaaa-aaaa-aaaaaaaaaaaa", "user1@test.com"),
        )
        conn.execute(
            "INSERT INTO users (id, email) VALUES (?, ?)",
            ("bbbbbbbb-bbbb-bbbb-bbbb-bbbbbbbbbbbb", "user2@test.com"),
        )
        conn.execute(
            "INSERT INTO users (id, email) VALUES (?, ?)",
            ("cccccccc-cccc-cccc-cccc-cccccccccccc", "user3@test.com"),
        )
        conn.commit()
        conn.close()

        return digest_repo

    def test_get_all_filters_by_user_id(self, db_with_users):
        """Test filtering digests by user_id."""
        now = datetime.now(timezone.utc)
        temp_db = db_with_users

        # Create digests for two different users
        user1_digest = Digest(
            id="11111111-1111-1111-1111-111111111111",
            user_id="aaaaaaaa-aaaa-aaaa-aaaa-aaaaaaaaaaaa",
            period_start=now - timedelta(days=7),
            period_end=now,
        )
        user2_digest = Digest(
            id="22222222-2222-2222-2222-222222222222",
            user_id="bbbbbbbb-bbbb-bbbb-bbbb-bbbbbbbbbbbb",
            period_start=now - timedelta(days=7),
            period_end=now,
        )
        temp_db.save(user1_digest)
        temp_db.save(user2_digest)

        # Filter by user 1
        user1_digests = temp_db.get_all(user_id="aaaaaaaa-aaaa-aaaa-aaaa-aaaaaaaaaaaa")
        assert len(user1_digests) == 1
        assert user1_digests[0].id == user1_digest.id

        # Filter by user 2
        user2_digests = temp_db.get_all(user_id="bbbbbbbb-bbbb-bbbb-bbbb-bbbbbbbbbbbb")
        assert len(user2_digests) == 1
        assert user2_digests[0].id == user2_digest.id

        # Get all (no filter)
        all_digests = temp_db.get_all()
        assert len(all_digests) == 2

    def test_save_and_retrieve_preserves_user_id(self, db_with_users):
        """Test that user_id is preserved through save and retrieve."""
        now = datetime.now(timezone.utc)
        temp_db = db_with_users

        digest = Digest(
            id="dddddddd-dddd-dddd-dddd-dddddddddddd",
            user_id="cccccccc-cccc-cccc-cccc-cccccccccccc",
            period_start=now - timedelta(days=7),
            period_end=now,
        )
        temp_db.save(digest)

        retrieved = temp_db.get_by_id(digest.id)
        assert retrieved is not None
        assert retrieved.user_id == "cccccccc-cccc-cccc-cccc-cccccccccccc"


# ============================================================================
# Model Property Tests
# ============================================================================


class TestDigestModel:
    """Tests for Digest model properties and methods."""

    def test_success_rate_calculation(self):
        """Test success rate calculation."""
        digest = Digest(
            user_id=TEST_USER_ID,
            period_start=datetime.now(timezone.utc),
            period_end=datetime.now(timezone.utc),
            episodes_total=10,
            episodes_completed=8,
            episodes_failed=2,
        )

        assert digest.success_rate == 80.0

    def test_success_rate_zero_episodes(self):
        """Test success rate with no episodes."""
        digest = Digest(
            user_id=TEST_USER_ID,
            period_start=datetime.now(timezone.utc),
            period_end=datetime.now(timezone.utc),
            episodes_total=0,
        )

        assert digest.success_rate == 0.0

    def test_is_complete_for_terminal_states(self):
        """Test is_complete for terminal states."""
        now = datetime.now(timezone.utc)

        for status in [DigestStatus.COMPLETED, DigestStatus.PARTIAL, DigestStatus.FAILED]:
            digest = Digest(
                user_id=TEST_USER_ID,
                period_start=now,
                period_end=now,
                status=status,
            )
            assert digest.is_complete is True

    def test_is_complete_for_non_terminal_states(self):
        """Test is_complete for non-terminal states."""
        now = datetime.now(timezone.utc)

        for status in [DigestStatus.PENDING, DigestStatus.IN_PROGRESS]:
            digest = Digest(
                user_id=TEST_USER_ID,
                period_start=now,
                period_end=now,
                status=status,
            )
            assert digest.is_complete is False

    def test_mark_completed_sets_status(self):
        """Test mark_completed method."""
        now = datetime.now(timezone.utc)
        digest = Digest(
            user_id=TEST_USER_ID,
            period_start=now,
            period_end=now,
            episodes_total=5,
        )

        digest.mark_completed(
            file_path="output.md",
            episodes_completed=5,
            episodes_failed=0,
            processing_time_seconds=100.0,
        )

        assert digest.status == DigestStatus.COMPLETED
        assert digest.file_path == "output.md"
        assert digest.episodes_completed == 5

    def test_mark_completed_partial_sets_partial_status(self):
        """Test mark_completed with failures sets PARTIAL status."""
        now = datetime.now(timezone.utc)
        digest = Digest(
            user_id=TEST_USER_ID,
            period_start=now,
            period_end=now,
            episodes_total=5,
        )

        digest.mark_completed(
            file_path="output.md",
            episodes_completed=3,
            episodes_failed=2,
            processing_time_seconds=100.0,
        )

        assert digest.status == DigestStatus.PARTIAL

    def test_mark_failed_sets_error(self):
        """Test mark_failed method."""
        now = datetime.now(timezone.utc)
        digest = Digest(
            user_id=TEST_USER_ID,
            period_start=now,
            period_end=now,
        )

        digest.mark_failed("Something went wrong")

        assert digest.status == DigestStatus.FAILED
        assert digest.error_message == "Something went wrong"
