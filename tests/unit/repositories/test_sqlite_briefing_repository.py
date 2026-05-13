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
Unit tests for SQLite briefing repository.

Tests CRUD operations and episode tracking for THES-153.
"""

from datetime import datetime, timedelta, timezone

import pytest

from thestill.models.briefing import Briefing, BriefingStatus
from thestill.repositories.sqlite_briefing_repository import SqliteBriefingRepository
from thestill.repositories.sqlite_podcast_repository import SqlitePodcastRepository

# Test user ID constant (matches user created in temp_db fixture)
TEST_USER_ID = "aaaaaaaa-aaaa-aaaa-aaaa-aaaaaaaaaaaa"


@pytest.fixture
def temp_db(tmp_path):
    """Create temporary SQLite database with schema and test user."""
    import sqlite3

    db_path = tmp_path / "test.db"
    # SqlitePodcastRepository creates the schema including briefings tables
    SqlitePodcastRepository(str(db_path))

    # Insert test user (required for user_id FK constraint)
    conn = sqlite3.connect(str(db_path))
    conn.execute(
        "INSERT INTO users (id, email) VALUES (?, ?)",
        (TEST_USER_ID, "test@example.com"),
    )
    conn.commit()
    conn.close()

    return SqliteBriefingRepository(str(db_path))


@pytest.fixture
def sample_briefing():
    """Create sample briefing for testing."""
    now = datetime.now(timezone.utc)
    return Briefing(
        id="550e8400-e29b-41d4-a716-446655440000",
        user_id=TEST_USER_ID,
        created_at=now,
        updated_at=now,
        period_start=now - timedelta(days=7),
        period_end=now,
        status=BriefingStatus.PENDING,
        episode_ids=["ep-1", "ep-2", "ep-3"],
        episodes_total=3,
    )


@pytest.fixture
def completed_briefing():
    """Create a completed briefing for testing."""
    now = datetime.now(timezone.utc)
    return Briefing(
        id="660e8400-e29b-41d4-a716-446655440001",
        user_id=TEST_USER_ID,
        created_at=now - timedelta(hours=1),
        updated_at=now,
        period_start=now - timedelta(days=7),
        period_end=now,
        status=BriefingStatus.COMPLETED,
        file_path="briefing_2025-01-26_120000.md",
        episode_ids=["ep-4", "ep-5"],
        episodes_total=2,
        episodes_completed=2,
        episodes_failed=0,
        processing_time_seconds=754.5,
    )


@pytest.fixture
def make_briefing():
    """Factory fixture to create briefings with custom parameters."""
    import uuid

    def _make_briefing(
        briefing_id: str = None,
        user_id: str = TEST_USER_ID,
        status: BriefingStatus = BriefingStatus.PENDING,
    ) -> Briefing:
        now = datetime.now(timezone.utc)
        return Briefing(
            id=briefing_id or str(uuid.uuid4()),
            user_id=user_id,
            created_at=now,
            updated_at=now,
            period_start=now - timedelta(days=7),
            period_end=now,
            status=status,
            episode_ids=[],
            episodes_total=0,
        )

    return _make_briefing


# ============================================================================
# Basic CRUD Tests
# ============================================================================


class TestBriefingRepositorySave:
    """Tests for save operations."""

    def test_save_creates_new_briefing(self, temp_db, sample_briefing):
        """Test saving a new briefing."""
        saved = temp_db.save(sample_briefing)

        assert saved.id == sample_briefing.id
        assert saved.status == BriefingStatus.PENDING

    def test_save_persists_episode_ids(self, temp_db, sample_briefing):
        """Test that episode IDs are persisted."""
        temp_db.save(sample_briefing)

        found = temp_db.get_by_id(sample_briefing.id)
        assert found is not None
        assert set(found.episode_ids) == {"ep-1", "ep-2", "ep-3"}

    def test_save_upsert_updates_existing(self, temp_db, sample_briefing):
        """Test that save() updates existing briefing on conflict."""
        temp_db.save(sample_briefing)

        # Update and save again
        sample_briefing.status = BriefingStatus.COMPLETED
        sample_briefing.episodes_completed = 3
        sample_briefing.file_path = "new_path.md"
        temp_db.save(sample_briefing)

        found = temp_db.get_by_id(sample_briefing.id)
        assert found.status == BriefingStatus.COMPLETED
        assert found.episodes_completed == 3
        assert found.file_path == "new_path.md"

    def test_save_updates_episode_ids(self, temp_db, sample_briefing):
        """Test that episode IDs are replaced on update."""
        temp_db.save(sample_briefing)

        # Change episode IDs
        sample_briefing.episode_ids = ["ep-new-1", "ep-new-2"]
        temp_db.save(sample_briefing)

        found = temp_db.get_by_id(sample_briefing.id)
        assert set(found.episode_ids) == {"ep-new-1", "ep-new-2"}


class TestBriefingRepositoryGet:
    """Tests for get operations."""

    def test_get_by_id_returns_briefing(self, temp_db, sample_briefing):
        """Test retrieving briefing by ID."""
        temp_db.save(sample_briefing)

        found = temp_db.get_by_id(sample_briefing.id)

        assert found is not None
        assert found.id == sample_briefing.id
        assert found.status == sample_briefing.status

    def test_get_by_id_returns_none_for_missing(self, temp_db):
        """Test that get_by_id returns None for non-existent briefing."""
        found = temp_db.get_by_id("non-existent-id")
        assert found is None

    def test_get_all_returns_briefings_ordered_by_created_at(self, temp_db, sample_briefing, completed_briefing):
        """Test that get_all returns briefings in descending order."""
        temp_db.save(completed_briefing)  # Created 1 hour ago
        temp_db.save(sample_briefing)  # Created now

        all_briefings = temp_db.get_all()

        assert len(all_briefings) == 2
        assert all_briefings[0].id == sample_briefing.id  # More recent first

    def test_get_all_with_status_filter(self, temp_db, sample_briefing, completed_briefing):
        """Test filtering by status."""
        temp_db.save(sample_briefing)  # PENDING
        temp_db.save(completed_briefing)  # COMPLETED

        pending = temp_db.get_all(status=BriefingStatus.PENDING)
        completed = temp_db.get_all(status=BriefingStatus.COMPLETED)

        assert len(pending) == 1
        assert pending[0].id == sample_briefing.id
        assert len(completed) == 1
        assert completed[0].id == completed_briefing.id

    def test_get_all_with_limit_and_offset(self, temp_db):
        """Test pagination with limit and offset."""
        # Create 5 briefings
        now = datetime.now(timezone.utc)
        for i in range(5):
            briefing = Briefing(
                id=f"00000000-0000-0000-0000-00000000000{i}",
                user_id=TEST_USER_ID,
                period_start=now - timedelta(days=7),
                period_end=now,
                created_at=now - timedelta(hours=i),  # Different creation times
            )
            temp_db.save(briefing)

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

    def test_get_latest(self, temp_db, sample_briefing, completed_briefing):
        """Test getting the most recent briefing."""
        temp_db.save(completed_briefing)  # Created 1 hour ago
        temp_db.save(sample_briefing)  # Created now

        latest = temp_db.get_latest()

        assert latest is not None
        assert latest.id == sample_briefing.id

    def test_get_latest_returns_none_when_empty(self, temp_db):
        """Test that get_latest returns None when no briefings exist."""
        latest = temp_db.get_latest()
        assert latest is None


class TestBriefingRepositoryDelete:
    """Tests for delete operations."""

    def test_delete_removes_briefing(self, temp_db, sample_briefing):
        """Test deleting a briefing."""
        temp_db.save(sample_briefing)

        deleted = temp_db.delete(sample_briefing.id)

        assert deleted is True
        assert temp_db.get_by_id(sample_briefing.id) is None

    def test_delete_returns_false_for_missing(self, temp_db):
        """Test that delete returns False for non-existent briefing."""
        deleted = temp_db.delete("non-existent-id")
        assert deleted is False

    def test_delete_removes_episode_associations(self, temp_db, sample_briefing):
        """Test that deleting briefing removes episode associations."""
        temp_db.save(sample_briefing)

        temp_db.delete(sample_briefing.id)

        # Verify episode associations are gone
        episode_ids = temp_db.get_episodes_in_briefing(sample_briefing.id)
        assert len(episode_ids) == 0


# ============================================================================
# Count Tests
# ============================================================================


class TestBriefingRepositoryCount:
    """Tests for count operations."""

    def test_count_returns_zero_when_empty(self, temp_db):
        """Test that count returns 0 when no briefings exist."""
        count = temp_db.count()
        assert count == 0

    def test_count_returns_total(self, temp_db, sample_briefing, make_briefing):
        """Test that count returns total number of briefings."""
        temp_db.save(sample_briefing)
        temp_db.save(make_briefing())

        count = temp_db.count()
        assert count == 2

    def test_count_filters_by_status(self, temp_db, make_briefing):
        """Test that count filters by status."""
        d1 = make_briefing(status=BriefingStatus.COMPLETED)
        d2 = make_briefing(status=BriefingStatus.PENDING)
        d3 = make_briefing(status=BriefingStatus.COMPLETED)
        temp_db.save(d1)
        temp_db.save(d2)
        temp_db.save(d3)

        completed_count = temp_db.count(status=BriefingStatus.COMPLETED)
        pending_count = temp_db.count(status=BriefingStatus.PENDING)

        assert completed_count == 2
        assert pending_count == 1

    def test_count_filters_by_user_id(self, temp_db, make_briefing):
        """Test that count filters by user_id."""
        # Create briefings for the test user
        d1 = make_briefing()
        d2 = make_briefing()
        temp_db.save(d1)
        temp_db.save(d2)

        # Count for the test user should return 2
        user_count = temp_db.count(user_id=TEST_USER_ID)
        assert user_count == 2

        # Count for a non-existent user should return 0
        other_count = temp_db.count(user_id="non-existent-user-id")
        assert other_count == 0

    def test_count_filters_by_both_status_and_user(self, temp_db, make_briefing):
        """Test that count filters by both status and user_id."""
        d1 = make_briefing(status=BriefingStatus.COMPLETED)
        d2 = make_briefing(status=BriefingStatus.PENDING)
        d3 = make_briefing(status=BriefingStatus.COMPLETED)
        temp_db.save(d1)
        temp_db.save(d2)
        temp_db.save(d3)

        # Count completed briefings for the test user
        count = temp_db.count(status=BriefingStatus.COMPLETED, user_id=TEST_USER_ID)
        assert count == 2

        # Count pending briefings for the test user
        pending_count = temp_db.count(status=BriefingStatus.PENDING, user_id=TEST_USER_ID)
        assert pending_count == 1


# ============================================================================
# Episode Tracking Tests
# ============================================================================


class TestBriefingRepositoryEpisodeTracking:
    """Tests for episode tracking operations."""

    def test_get_episodes_in_briefing(self, temp_db, sample_briefing):
        """Test getting episode IDs from a briefing."""
        temp_db.save(sample_briefing)

        episode_ids = temp_db.get_episodes_in_briefing(sample_briefing.id)

        assert set(episode_ids) == {"ep-1", "ep-2", "ep-3"}

    def test_get_episodes_in_briefing_empty_for_missing(self, temp_db):
        """Test that missing briefing returns empty list."""
        episode_ids = temp_db.get_episodes_in_briefing("non-existent")
        assert episode_ids == []

    def test_is_episode_in_any_briefing_true(self, temp_db, sample_briefing):
        """Test detecting episode in briefing."""
        temp_db.save(sample_briefing)

        assert temp_db.is_episode_in_any_briefing("ep-1") is True
        assert temp_db.is_episode_in_any_briefing("ep-2") is True

    def test_is_episode_in_any_briefing_false(self, temp_db, sample_briefing):
        """Test episode not in any briefing."""
        temp_db.save(sample_briefing)

        assert temp_db.is_episode_in_any_briefing("not-included") is False

    def test_is_episode_in_any_briefing_empty_db(self, temp_db):
        """Test with empty database."""
        assert temp_db.is_episode_in_any_briefing("any-episode") is False

    def test_get_briefings_containing_episode(self, temp_db):
        """Test finding briefings containing a specific episode."""
        now = datetime.now(timezone.utc)

        # Create two briefings with overlapping episodes
        briefing1 = Briefing(
            id="11111111-1111-1111-1111-111111111111",
            user_id=TEST_USER_ID,
            period_start=now - timedelta(days=7),
            period_end=now,
            episode_ids=["shared-ep", "ep-1"],
        )
        briefing2 = Briefing(
            id="22222222-2222-2222-2222-222222222222",
            user_id=TEST_USER_ID,
            period_start=now - timedelta(days=7),
            period_end=now,
            episode_ids=["shared-ep", "ep-2"],
        )
        temp_db.save(briefing1)
        temp_db.save(briefing2)

        # Find briefings containing shared episode
        containing = temp_db.get_briefings_containing_episode("shared-ep")

        assert len(containing) == 2
        ids = {d.id for d in containing}
        assert ids == {"11111111-1111-1111-1111-111111111111", "22222222-2222-2222-2222-222222222222"}

    def test_get_briefings_containing_episode_none(self, temp_db, sample_briefing):
        """Test no briefings containing episode."""
        temp_db.save(sample_briefing)

        containing = temp_db.get_briefings_containing_episode("not-in-any")

        assert len(containing) == 0


# ============================================================================
# Period Query Tests
# ============================================================================


class TestBriefingRepositoryPeriodQueries:
    """Tests for period-based queries."""

    def test_get_briefings_in_period_overlap(self, temp_db):
        """Test finding briefings with overlapping periods."""
        now = datetime.now(timezone.utc)

        # Briefing covering last 7 days
        briefing = Briefing(
            id="bbbbbbbb-bbbb-bbbb-bbbb-bbbbbbbbbbbb",
            user_id=TEST_USER_ID,
            period_start=now - timedelta(days=7),
            period_end=now,
        )
        temp_db.save(briefing)

        # Query overlapping period
        results = temp_db.get_briefings_in_period(
            start=now - timedelta(days=3),
            end=now + timedelta(days=1),
        )

        assert len(results) == 1
        assert results[0].id == briefing.id

    def test_get_briefings_in_period_no_overlap(self, temp_db):
        """Test no briefings found for non-overlapping period."""
        now = datetime.now(timezone.utc)

        # Briefing covering old period (14-7 days ago)
        briefing = Briefing(
            id="cccccccc-cccc-cccc-cccc-cccccccccccc",
            user_id=TEST_USER_ID,
            period_start=now - timedelta(days=14),
            period_end=now - timedelta(days=7),
        )
        temp_db.save(briefing)

        # Query period that doesn't overlap
        results = temp_db.get_briefings_in_period(
            start=now - timedelta(days=3),
            end=now,
        )

        assert len(results) == 0


# ============================================================================
# User Filtering Tests
# ============================================================================


class TestBriefingRepositoryUserFiltering:
    """Tests for user_id filtering."""

    @pytest.fixture
    def db_with_users(self, tmp_path):
        """Create database with test users."""
        import sqlite3

        db_path = tmp_path / "test_users.db"
        # Create schema via SqlitePodcastRepository
        SqlitePodcastRepository(str(db_path))
        briefing_repo = SqliteBriefingRepository(str(db_path))

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

        return briefing_repo

    def test_get_all_filters_by_user_id(self, db_with_users):
        """Test filtering briefings by user_id."""
        now = datetime.now(timezone.utc)
        temp_db = db_with_users

        # Create briefings for two different users
        user1_briefing = Briefing(
            id="11111111-1111-1111-1111-111111111111",
            user_id="aaaaaaaa-aaaa-aaaa-aaaa-aaaaaaaaaaaa",
            period_start=now - timedelta(days=7),
            period_end=now,
        )
        user2_briefing = Briefing(
            id="22222222-2222-2222-2222-222222222222",
            user_id="bbbbbbbb-bbbb-bbbb-bbbb-bbbbbbbbbbbb",
            period_start=now - timedelta(days=7),
            period_end=now,
        )
        temp_db.save(user1_briefing)
        temp_db.save(user2_briefing)

        # Filter by user 1
        user1_briefings = temp_db.get_all(user_id="aaaaaaaa-aaaa-aaaa-aaaa-aaaaaaaaaaaa")
        assert len(user1_briefings) == 1
        assert user1_briefings[0].id == user1_briefing.id

        # Filter by user 2
        user2_briefings = temp_db.get_all(user_id="bbbbbbbb-bbbb-bbbb-bbbb-bbbbbbbbbbbb")
        assert len(user2_briefings) == 1
        assert user2_briefings[0].id == user2_briefing.id

        # Get all (no filter)
        all_briefings = temp_db.get_all()
        assert len(all_briefings) == 2

    def test_save_and_retrieve_preserves_user_id(self, db_with_users):
        """Test that user_id is preserved through save and retrieve."""
        now = datetime.now(timezone.utc)
        temp_db = db_with_users

        briefing = Briefing(
            id="dddddddd-dddd-dddd-dddd-dddddddddddd",
            user_id="cccccccc-cccc-cccc-cccc-cccccccccccc",
            period_start=now - timedelta(days=7),
            period_end=now,
        )
        temp_db.save(briefing)

        retrieved = temp_db.get_by_id(briefing.id)
        assert retrieved is not None
        assert retrieved.user_id == "cccccccc-cccc-cccc-cccc-cccccccccccc"


# ============================================================================
# Model Property Tests
# ============================================================================


class TestBriefingModel:
    """Tests for Briefing model properties and methods."""

    def test_success_rate_calculation(self):
        """Test success rate calculation."""
        briefing = Briefing(
            user_id=TEST_USER_ID,
            period_start=datetime.now(timezone.utc),
            period_end=datetime.now(timezone.utc),
            episodes_total=10,
            episodes_completed=8,
            episodes_failed=2,
        )

        assert briefing.success_rate == 80.0

    def test_success_rate_zero_episodes(self):
        """Test success rate with no episodes."""
        briefing = Briefing(
            user_id=TEST_USER_ID,
            period_start=datetime.now(timezone.utc),
            period_end=datetime.now(timezone.utc),
            episodes_total=0,
        )

        assert briefing.success_rate == 0.0

    def test_is_complete_for_terminal_states(self):
        """Test is_complete for terminal states."""
        now = datetime.now(timezone.utc)

        for status in [BriefingStatus.COMPLETED, BriefingStatus.PARTIAL, BriefingStatus.FAILED]:
            briefing = Briefing(
                user_id=TEST_USER_ID,
                period_start=now,
                period_end=now,
                status=status,
            )
            assert briefing.is_complete is True

    def test_is_complete_for_non_terminal_states(self):
        """Test is_complete for non-terminal states."""
        now = datetime.now(timezone.utc)

        for status in [BriefingStatus.PENDING, BriefingStatus.IN_PROGRESS]:
            briefing = Briefing(
                user_id=TEST_USER_ID,
                period_start=now,
                period_end=now,
                status=status,
            )
            assert briefing.is_complete is False

    def test_mark_completed_sets_status(self):
        """Test mark_completed method."""
        now = datetime.now(timezone.utc)
        briefing = Briefing(
            user_id=TEST_USER_ID,
            period_start=now,
            period_end=now,
            episodes_total=5,
        )

        briefing.mark_completed(
            file_path="output.md",
            episodes_completed=5,
            episodes_failed=0,
            processing_time_seconds=100.0,
        )

        assert briefing.status == BriefingStatus.COMPLETED
        assert briefing.file_path == "output.md"
        assert briefing.episodes_completed == 5

    def test_mark_completed_partial_sets_partial_status(self):
        """Test mark_completed with failures sets PARTIAL status."""
        now = datetime.now(timezone.utc)
        briefing = Briefing(
            user_id=TEST_USER_ID,
            period_start=now,
            period_end=now,
            episodes_total=5,
        )

        briefing.mark_completed(
            file_path="output.md",
            episodes_completed=3,
            episodes_failed=2,
            processing_time_seconds=100.0,
        )

        assert briefing.status == BriefingStatus.PARTIAL

    def test_mark_failed_sets_error(self):
        """Test mark_failed method."""
        now = datetime.now(timezone.utc)
        briefing = Briefing(
            user_id=TEST_USER_ID,
            period_start=now,
            period_end=now,
        )

        briefing.mark_failed("Something went wrong")

        assert briefing.status == BriefingStatus.FAILED
        assert briefing.error_message == "Something went wrong"
