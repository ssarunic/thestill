"""
Unit tests for JsonPodcastRepository.

Tests cover:
- CRUD operations (create, read, update, delete)
- Episode-specific operations
- Error handling and edge cases
- Data persistence across instances
- Atomic file operations
"""

import json
import tempfile
from datetime import datetime
from pathlib import Path

import pytest

from thestill.models.podcast import Episode, Podcast
from thestill.repositories.json_podcast_repository import JsonPodcastRepository


@pytest.fixture
def temp_storage():
    """Create temporary storage directory for testing."""
    with tempfile.TemporaryDirectory() as tmpdir:
        yield tmpdir


@pytest.fixture
def repository(temp_storage):
    """Create repository instance with temporary storage."""
    return JsonPodcastRepository(temp_storage)


@pytest.fixture
def sample_podcast():
    """Create sample podcast for testing."""
    return Podcast(
        title="Test Podcast",
        description="A test podcast for unit testing",
        rss_url="https://example.com/feed.xml",
        episodes=[
            Episode(
                title="Episode 1",
                audio_url="https://example.com/ep1.mp3",
                external_id="ep1-guid",
                pub_date=datetime(2024, 1, 1),
                description="First episode",
            ),
            Episode(
                title="Episode 2",
                audio_url="https://example.com/ep2.mp3",
                external_id="ep2-guid",
                pub_date=datetime(2024, 1, 2),
                description="Second episode",
            ),
        ],
    )


@pytest.fixture
def another_podcast():
    """Create another sample podcast for multi-podcast tests."""
    return Podcast(
        title="Another Podcast",
        description="Another test podcast",
        rss_url="https://example.com/another-feed.xml",
        episodes=[
            Episode(
                title="Episode A",
                audio_url="https://example.com/epA.mp3",
                external_id="epA-guid",
                pub_date=datetime(2024, 2, 1),
                description="Episode A",
            )
        ],
    )


class TestJsonPodcastRepositoryBasics:
    """Test basic repository functionality."""

    def test_initialization_creates_directory(self, temp_storage):
        """Should create storage directory and feeds file on initialization."""
        storage_path = Path(temp_storage) / "subdir"
        assert not storage_path.exists()

        repository = JsonPodcastRepository(str(storage_path))

        assert storage_path.exists()
        assert repository.feeds_file.exists()

    def test_find_all_empty(self, repository):
        """Should return empty list when no podcasts exist."""
        podcasts = repository.find_all()
        assert podcasts == []
        assert isinstance(podcasts, list)

    def test_feeds_file_format(self, repository, sample_podcast):
        """Should write properly formatted JSON file."""
        repository.save(sample_podcast)

        # Read file directly and verify format
        with open(repository.feeds_file, "r", encoding="utf-8") as f:
            data = json.load(f)

        assert isinstance(data, list)
        assert len(data) == 1
        assert data[0]["title"] == "Test Podcast"
        assert data[0]["rss_url"] == "https://example.com/feed.xml"
        assert len(data[0]["episodes"]) == 2


class TestPodcastCRUD:
    """Test Create, Read, Update, Delete operations for podcasts."""

    def test_save_new_podcast(self, repository, sample_podcast):
        """Should save new podcast and return it."""
        result = repository.save(sample_podcast)

        assert result is not None
        assert result.title == sample_podcast.title
        assert result.rss_url == sample_podcast.rss_url
        assert len(result.episodes) == 2

        # Verify persistence
        podcasts = repository.find_all()
        assert len(podcasts) == 1
        assert podcasts[0].title == sample_podcast.title

    def test_save_multiple_podcasts(self, repository, sample_podcast, another_podcast):
        """Should save multiple podcasts in order."""
        repository.save(sample_podcast)
        repository.save(another_podcast)

        podcasts = repository.find_all()
        assert len(podcasts) == 2
        assert podcasts[0].title == "Test Podcast"
        assert podcasts[1].title == "Another Podcast"

    def test_save_updates_existing(self, repository, sample_podcast):
        """Should update existing podcast with same URL."""
        # Save initial version
        repository.save(sample_podcast)

        # Modify and save again
        sample_podcast.title = "Updated Title"
        sample_podcast.description = "Updated description"
        repository.save(sample_podcast)

        # Should have only one podcast
        podcasts = repository.find_all()
        assert len(podcasts) == 1
        assert podcasts[0].title == "Updated Title"
        assert podcasts[0].description == "Updated description"

    def test_find_by_url_success(self, repository, sample_podcast):
        """Should find podcast by URL."""
        repository.save(sample_podcast)

        found = repository.find_by_url(str(sample_podcast.rss_url))

        assert found is not None
        assert found.title == sample_podcast.title
        assert found.rss_url == sample_podcast.rss_url

    def test_find_by_url_not_found(self, repository):
        """Should return None when URL not found."""
        found = repository.find_by_url("https://nonexistent.com/feed.xml")
        assert found is None

    def test_find_by_index_success(self, repository, sample_podcast, another_podcast):
        """Should find podcast by 1-based index."""
        repository.save(sample_podcast)
        repository.save(another_podcast)

        # Test 1-based indexing
        found_first = repository.find_by_index(1)
        assert found_first is not None
        assert found_first.title == "Test Podcast"

        found_second = repository.find_by_index(2)
        assert found_second is not None
        assert found_second.title == "Another Podcast"

    def test_find_by_index_out_of_range(self, repository, sample_podcast):
        """Should return None for invalid IDs."""
        repository.save(sample_podcast)

        # Test various invalid IDs
        assert repository.find_by_index(0) is None
        assert repository.find_by_index(-1) is None
        assert repository.find_by_index(999) is None
        assert repository.find_by_index(2) is None  # Only 1 podcast exists

    def test_exists_true(self, repository, sample_podcast):
        """Should return True when podcast exists."""
        repository.save(sample_podcast)

        assert repository.exists(str(sample_podcast.rss_url)) is True

    def test_exists_false(self, repository):
        """Should return False when podcast doesn't exist."""
        assert repository.exists("https://nonexistent.com/feed.xml") is False

    def test_delete_success(self, repository, sample_podcast):
        """Should delete podcast by URL."""
        repository.save(sample_podcast)
        assert len(repository.find_all()) == 1

        result = repository.delete(str(sample_podcast.rss_url))

        assert result is True
        assert len(repository.find_all()) == 0

    def test_delete_nonexistent(self, repository):
        """Should return False when deleting nonexistent podcast."""
        result = repository.delete("https://nonexistent.com/feed.xml")
        assert result is False

    def test_delete_preserves_other_podcasts(self, repository, sample_podcast, another_podcast):
        """Should only delete specified podcast."""
        repository.save(sample_podcast)
        repository.save(another_podcast)

        repository.delete(str(sample_podcast.rss_url))

        podcasts = repository.find_all()
        assert len(podcasts) == 1
        assert podcasts[0].title == "Another Podcast"


class TestEpisodeOperations:
    """Test episode-specific operations."""

    def test_update_episode_single_field(self, repository, sample_podcast):
        """Should update single episode field."""
        repository.save(sample_podcast)

        result = repository.update_episode(
            str(sample_podcast.rss_url), "ep1-guid", {"audio_path": "/path/to/audio.mp3"}
        )

        assert result is True

        # Verify update persisted
        podcast = repository.find_by_url(str(sample_podcast.rss_url))
        episode = podcast.episodes[0]
        assert episode.audio_path == "/path/to/audio.mp3"
        # Other fields unchanged
        assert episode.title == "Episode 1"
        assert str(episode.audio_url) == "https://example.com/ep1.mp3"

    def test_update_episode_multiple_fields(self, repository, sample_podcast):
        """Should update multiple episode fields at once."""
        repository.save(sample_podcast)

        result = repository.update_episode(
            str(sample_podcast.rss_url),
            "ep2-guid",
            {
                "audio_path": "/path/to/audio2.mp3",
                "downsampled_audio_path": "/path/to/downsampled2.wav",
                "raw_transcript_path": "/path/to/transcript2.json",
            },
        )

        assert result is True

        # Verify all updates persisted
        podcast = repository.find_by_url(str(sample_podcast.rss_url))
        episode = podcast.episodes[1]
        assert episode.audio_path == "/path/to/audio2.mp3"
        assert episode.downsampled_audio_path == "/path/to/downsampled2.wav"
        assert episode.raw_transcript_path == "/path/to/transcript2.json"

    def test_update_episode_not_found(self, repository, sample_podcast):
        """Should return False when episode not found."""
        repository.save(sample_podcast)

        result = repository.update_episode(
            str(sample_podcast.rss_url), "nonexistent-guid", {"audio_path": "/path/to/audio.mp3"}
        )

        assert result is False

    def test_update_episode_podcast_not_found(self, repository):
        """Should return False when podcast not found."""
        result = repository.update_episode(
            "https://nonexistent.com/feed.xml", "ep1-guid", {"audio_path": "/path/to/audio.mp3"}
        )

        assert result is False

    def test_find_by_podcast(self, repository, sample_podcast):
        """Should return all episodes for a podcast."""
        repository.save(sample_podcast)

        episodes = repository.find_by_podcast(str(sample_podcast.rss_url))

        assert len(episodes) == 2
        assert episodes[0].title == "Episode 1"
        assert episodes[1].title == "Episode 2"

    def test_find_by_podcast_not_found(self, repository):
        """Should return empty list when podcast not found."""
        episodes = repository.find_by_podcast("https://nonexistent.com/feed.xml")
        assert episodes == []

    def test_find_by_external_id_success(self, repository, sample_podcast):
        """Should find episode by GUID."""
        repository.save(sample_podcast)

        episode = repository.find_by_external_id(str(sample_podcast.rss_url), "ep1-guid")

        assert episode is not None
        assert episode.title == "Episode 1"
        assert episode.external_id == "ep1-guid"

    def test_find_by_external_id_not_found(self, repository, sample_podcast):
        """Should return None when episode not found."""
        repository.save(sample_podcast)

        episode = repository.find_by_external_id(str(sample_podcast.rss_url), "nonexistent-guid")

        assert episode is None


class TestUnprocessedEpisodes:
    """Test finding episodes in various processing states."""

    def test_find_unprocessed_discovered(self, repository, sample_podcast):
        """Should find episodes in 'discovered' state."""
        # Episodes have audio_url but no audio_path (discovered state)
        repository.save(sample_podcast)

        results = repository.find_unprocessed("discovered")

        assert len(results) == 2
        podcast, episode = results[0]
        assert episode.audio_url is not None
        assert episode.audio_path is None

    def test_find_unprocessed_downloaded(self, repository, sample_podcast):
        """Should find episodes in 'downloaded' state."""
        # Mark first episode as downloaded
        sample_podcast.episodes[0].audio_path = "/path/to/audio1.mp3"
        repository.save(sample_podcast)

        results = repository.find_unprocessed("downloaded")

        assert len(results) == 1
        podcast, episode = results[0]
        assert episode.audio_path is not None
        assert episode.downsampled_audio_path is None

    def test_find_unprocessed_downsampled(self, repository, sample_podcast):
        """Should find episodes in 'downsampled' state."""
        # Mark episode as downsampled
        sample_podcast.episodes[0].audio_path = "/path/to/audio1.mp3"
        sample_podcast.episodes[0].downsampled_audio_path = "/path/to/downsampled1.wav"
        repository.save(sample_podcast)

        results = repository.find_unprocessed("downsampled")

        assert len(results) == 1
        podcast, episode = results[0]
        assert episode.downsampled_audio_path is not None
        assert episode.raw_transcript_path is None

    def test_find_unprocessed_transcribed(self, repository, sample_podcast):
        """Should find episodes in 'transcribed' state."""
        # Mark episode as transcribed
        ep = sample_podcast.episodes[0]
        ep.audio_path = "/path/to/audio1.mp3"
        ep.downsampled_audio_path = "/path/to/downsampled1.wav"
        ep.raw_transcript_path = "/path/to/transcript1.json"
        repository.save(sample_podcast)

        results = repository.find_unprocessed("transcribed")

        assert len(results) == 1
        podcast, episode = results[0]
        assert episode.raw_transcript_path is not None
        assert episode.clean_transcript_path is None

    def test_find_unprocessed_multiple_podcasts(self, repository, sample_podcast, another_podcast):
        """Should find unprocessed episodes across multiple podcasts."""
        repository.save(sample_podcast)
        repository.save(another_podcast)

        results = repository.find_unprocessed("discovered")

        # 2 episodes from sample_podcast + 1 from another_podcast
        assert len(results) == 3

    def test_find_unprocessed_returns_tuples(self, repository, sample_podcast):
        """Should return (Podcast, Episode) tuples."""
        repository.save(sample_podcast)

        results = repository.find_unprocessed("discovered")

        assert len(results) == 2
        for item in results:
            assert isinstance(item, tuple)
            assert len(item) == 2
            podcast, episode = item
            assert isinstance(podcast, Podcast)
            assert isinstance(episode, Episode)

    def test_find_unprocessed_empty(self, repository, sample_podcast):
        """Should return empty list when no episodes match state."""
        # Mark all episodes as fully processed
        for ep in sample_podcast.episodes:
            ep.audio_path = "/path/audio.mp3"
            ep.downsampled_audio_path = "/path/downsampled.wav"
            ep.raw_transcript_path = "/path/transcript.json"
            ep.clean_transcript_path = "/path/clean.md"
        repository.save(sample_podcast)

        # No episodes should be in 'discovered' state
        results = repository.find_unprocessed("discovered")
        assert results == []


class TestPersistence:
    """Test data persistence and file operations."""

    def test_persistence_across_instances(self, temp_storage, sample_podcast):
        """Should persist data across repository instances."""
        # Save with first instance
        repo1 = JsonPodcastRepository(temp_storage)
        repo1.save(sample_podcast)

        # Load with second instance
        repo2 = JsonPodcastRepository(temp_storage)
        podcasts = repo2.find_all()

        assert len(podcasts) == 1
        assert podcasts[0].title == sample_podcast.title
        assert len(podcasts[0].episodes) == 2

    def test_atomic_write_creates_temp_file(self, repository, sample_podcast):
        """Should use atomic write strategy with temp file."""
        repository.save(sample_podcast)

        # Verify temp file doesn't exist after successful write
        temp_file = repository.feeds_file.with_suffix(".tmp")
        assert not temp_file.exists()

        # Verify main file exists
        assert repository.feeds_file.exists()

    def test_handles_corrupted_json(self, repository, temp_storage):
        """Should handle corrupted JSON gracefully."""
        # Write invalid JSON
        with open(repository.feeds_file, "w") as f:
            f.write("{ invalid json }")

        # Should return empty list, not raise exception
        podcasts = repository.find_all()
        assert podcasts == []

    def test_handles_missing_file(self, temp_storage):
        """Should handle missing feeds file gracefully."""
        # Create repository but delete feeds file
        repo = JsonPodcastRepository(temp_storage)
        repo.feeds_file.unlink()

        # Should return empty list
        podcasts = repo.find_all()
        assert podcasts == []


class TestEdgeCases:
    """Test edge cases and error conditions."""

    def test_save_podcast_with_no_episodes(self, repository):
        """Should handle podcast with empty episodes list."""
        podcast = Podcast(
            title="Empty Podcast", description="No episodes yet", rss_url="https://example.com/empty.xml", episodes=[]
        )

        result = repository.save(podcast)
        assert result is not None

        found = repository.find_by_url(str(podcast.rss_url))
        assert found is not None
        assert len(found.episodes) == 0

    def test_update_episode_with_empty_updates(self, repository, sample_podcast):
        """Should handle empty updates dictionary."""
        repository.save(sample_podcast)

        result = repository.update_episode(str(sample_podcast.rss_url), "ep1-guid", {})

        # Should return False since no fields were actually updated
        assert result is False

    def test_find_unprocessed_with_invalid_state(self, repository, sample_podcast):
        """Should handle invalid state gracefully."""
        repository.save(sample_podcast)

        results = repository.find_unprocessed("invalid-state")
        assert results == []

    def test_concurrent_reads(self, repository, sample_podcast):
        """Should allow concurrent reads without issues."""
        repository.save(sample_podcast)

        # Simulate multiple concurrent reads
        results = []
        for _ in range(10):
            podcasts = repository.find_all()
            results.append(len(podcasts))

        # All reads should return same result
        assert all(count == 1 for count in results)
