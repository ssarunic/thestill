"""
Contract tests for service layer interfaces.

These tests verify the public API contracts of service classes to prevent
accidental breaking changes. Contract tests focus on:
- Method signatures (arguments, return types)
- Expected exceptions
- Return value structure
- API stability across refactoring

Contract tests do NOT test business logic - use unit/integration tests for that.
"""

import tempfile
from datetime import datetime
from pathlib import Path
from unittest.mock import Mock, patch

import pytest

from thestill.models.podcast import Episode, Podcast
from thestill.repositories.sqlite_podcast_repository import SqlitePodcastRepository
from thestill.services.podcast_service import EpisodeWithIndex, PodcastService, PodcastWithIndex
from thestill.services.refresh_service import RefreshResult, RefreshService
from thestill.services.stats_service import StatsService, SystemStats
from thestill.utils.path_manager import PathManager


@pytest.fixture
def temp_storage():
    """Create temporary storage directory."""
    with tempfile.TemporaryDirectory() as tmpdir:
        storage = Path(tmpdir)
        # Create required subdirectories
        (storage / "original_audio").mkdir(parents=True)
        (storage / "downsampled_audio").mkdir(parents=True)
        (storage / "raw_transcripts").mkdir(parents=True)
        (storage / "clean_transcripts").mkdir(parents=True)
        (storage / "summaries").mkdir(parents=True)
        yield storage


@pytest.fixture
def path_manager(temp_storage):
    """Create PathManager for temporary storage."""
    return PathManager(str(temp_storage))


@pytest.fixture
def repository(temp_storage):
    """Create SqlitePodcastRepository."""
    db_path = temp_storage / "test_podcasts.db"
    return SqlitePodcastRepository(db_path=str(db_path))


@pytest.fixture
def podcast_service(temp_storage, repository, path_manager):
    """Create PodcastService."""
    return PodcastService(storage_path=temp_storage, podcast_repository=repository, path_manager=path_manager)


@pytest.fixture
def sample_podcast(repository):
    """Create and save a sample podcast."""
    podcast = Podcast(
        title="Test Podcast",
        description="A test podcast",
        rss_url="https://example.com/feed.xml",
    )
    repository.save(podcast)
    return podcast


class TestPodcastServiceContract:
    """Contract tests for PodcastService public API."""

    def test_constructor_accepts_required_parameters(self, temp_storage, repository, path_manager):
        """Contract: Constructor accepts storage_path, podcast_repository, path_manager."""
        # Should not raise
        service = PodcastService(storage_path=temp_storage, podcast_repository=repository, path_manager=path_manager)
        assert service is not None

    def test_constructor_accepts_str_or_path(self, repository, path_manager, temp_storage):
        """Contract: storage_path can be str or Path."""
        # Both should work
        service1 = PodcastService(
            storage_path=str(temp_storage), podcast_repository=repository, path_manager=path_manager
        )
        service2 = PodcastService(storage_path=temp_storage, podcast_repository=repository, path_manager=path_manager)
        assert service1 is not None
        assert service2 is not None

    def test_add_podcast_signature(self, podcast_service):
        """Contract: add_podcast(url: str) -> Optional[Podcast]."""
        # Mock to avoid actual network call
        with patch("thestill.core.feed_manager.feedparser.parse") as mock_parse:
            mock_feed = Mock()
            mock_feed.feed = {"title": "Test", "description": "Test"}
            mock_feed.entries = []
            mock_feed.bozo = False
            mock_parse.return_value = mock_feed

            result = podcast_service.add_podcast("https://example.com/feed.xml")

            # Return type contract
            assert result is None or isinstance(result, Podcast)

    def test_remove_podcast_signature(self, podcast_service, sample_podcast):
        """Contract: remove_podcast(podcast_id: Union[str, int]) -> bool."""
        # Should accept int
        result1 = podcast_service.remove_podcast(1)
        assert isinstance(result1, bool)

        # Should accept str (URL)
        result2 = podcast_service.remove_podcast("https://example.com/feed.xml")
        assert isinstance(result2, bool)

    def test_get_podcasts_signature(self, podcast_service):
        """Contract: get_podcasts() -> List[PodcastWithIndex]."""
        result = podcast_service.get_podcasts()

        # Return type contract
        assert isinstance(result, list)
        # Empty list is valid
        if result:
            assert all(isinstance(p, PodcastWithIndex) for p in result)

    def test_podcast_with_index_structure(self, podcast_service, sample_podcast):
        """Contract: PodcastWithIndex has required fields."""
        podcasts = podcast_service.get_podcasts()
        assert len(podcasts) > 0

        podcast = podcasts[0]
        # Required fields
        assert hasattr(podcast, "index")
        assert hasattr(podcast, "title")
        assert hasattr(podcast, "description")
        assert hasattr(podcast, "rss_url")
        assert hasattr(podcast, "last_processed")
        assert hasattr(podcast, "episodes_count")
        assert hasattr(podcast, "episodes_processed")

        # Field types
        assert isinstance(podcast.index, int)
        assert isinstance(podcast.title, str)
        assert isinstance(podcast.description, str)
        assert isinstance(podcast.rss_url, str)
        assert podcast.last_processed is None or isinstance(podcast.last_processed, datetime)
        assert isinstance(podcast.episodes_count, int)
        assert isinstance(podcast.episodes_processed, int)

    def test_get_podcast_signature(self, podcast_service, sample_podcast):
        """Contract: get_podcast(podcast_id: Union[str, int]) -> Optional[Podcast]."""
        # Should accept int
        result1 = podcast_service.get_podcast(1)
        assert result1 is None or isinstance(result1, Podcast)

        # Should accept str (URL)
        result2 = podcast_service.get_podcast("https://example.com/feed.xml")
        assert result2 is None or isinstance(result2, Podcast)

        # Should accept string number
        result3 = podcast_service.get_podcast("1")
        assert result3 is None or isinstance(result3, Podcast)

    def test_get_episode_signature(self, podcast_service, sample_podcast):
        """Contract: get_episode(podcast_id, episode_id) -> Optional[Episode]."""
        # Should handle non-existent episode gracefully
        result = podcast_service.get_episode(1, 1)
        assert result is None or isinstance(result, Episode)

        # Should accept "latest" keyword
        result2 = podcast_service.get_episode(1, "latest")
        assert result2 is None or isinstance(result2, Episode)

    def test_get_episodes_signature(self, podcast_service, sample_podcast):
        """Contract: get_episodes(podcast_id, limit=10, since_hours=None) -> Optional[List[EpisodeWithIndex]]."""
        # Basic call
        result1 = podcast_service.get_episodes(1)
        assert result1 is None or isinstance(result1, list)

        # With limit
        result2 = podcast_service.get_episodes(1, limit=5)
        assert result2 is None or isinstance(result2, list)

        # With since_hours
        result3 = podcast_service.get_episodes(1, since_hours=24)
        assert result3 is None or isinstance(result3, list)

    def test_episode_with_index_structure(self, podcast_service, repository, sample_podcast):
        """Contract: EpisodeWithIndex has required fields."""
        # Add an episode to the podcast
        episode = Episode(
            title="Test Episode",
            description="Test",
            pub_date=datetime.now(),
            audio_url="https://example.com/ep1.mp3",
            external_id="ep1",
        )
        sample_podcast.episodes.append(episode)
        repository.save(sample_podcast)

        episodes = podcast_service.get_episodes(1)
        assert episodes is not None
        assert len(episodes) > 0

        ep = episodes[0]
        # Required fields
        assert hasattr(ep, "podcast_index")
        assert hasattr(ep, "episode_index")
        assert hasattr(ep, "title")
        assert hasattr(ep, "description")
        assert hasattr(ep, "pub_date")
        assert hasattr(ep, "audio_url")
        assert hasattr(ep, "duration")
        assert hasattr(ep, "external_id")
        assert hasattr(ep, "state")
        assert hasattr(ep, "transcript_available")
        assert hasattr(ep, "summary_available")

        # Field types
        assert isinstance(ep.podcast_index, int)
        assert isinstance(ep.episode_index, int)
        assert isinstance(ep.title, str)
        assert isinstance(ep.description, str)
        assert isinstance(ep.audio_url, str)
        assert isinstance(ep.external_id, str)
        assert isinstance(ep.state, str)
        assert ep.state in ["discovered", "downloaded", "downsampled", "transcribed", "cleaned"]
        assert isinstance(ep.transcript_available, bool)
        assert isinstance(ep.summary_available, bool)

    def test_get_transcript_signature(self, podcast_service, sample_podcast):
        """Contract: get_transcript(podcast_id, episode_id) -> Optional[str]."""
        result = podcast_service.get_transcript(1, 1)
        assert result is None or isinstance(result, str)


class TestRefreshServiceContract:
    """Contract tests for RefreshService public API."""

    def test_constructor_accepts_required_parameters(self, podcast_service):
        """Contract: Constructor accepts feed_manager and podcast_service."""
        from thestill.core.feed_manager import PodcastFeedManager

        feed_manager = podcast_service.feed_manager
        service = RefreshService(feed_manager=feed_manager, podcast_service=podcast_service)
        assert service is not None

    def test_refresh_signature_no_args(self, podcast_service):
        """Contract: refresh() -> RefreshResult (no arguments)."""
        from thestill.core.feed_manager import PodcastFeedManager

        feed_manager = podcast_service.feed_manager
        service = RefreshService(feed_manager=feed_manager, podcast_service=podcast_service)

        result = service.refresh()
        assert isinstance(result, RefreshResult)

    def test_refresh_signature_with_podcast_id(self, podcast_service, sample_podcast):
        """Contract: refresh(podcast_id) -> RefreshResult."""
        from thestill.core.feed_manager import PodcastFeedManager

        feed_manager = podcast_service.feed_manager
        service = RefreshService(feed_manager=feed_manager, podcast_service=podcast_service)

        # Should accept int
        try:
            result1 = service.refresh(podcast_id=1)
            assert isinstance(result1, RefreshResult)
        except ValueError:
            # Expected if podcast has no episodes
            pass

        # Should accept str
        try:
            result2 = service.refresh(podcast_id="https://example.com/feed.xml")
            assert isinstance(result2, RefreshResult)
        except ValueError:
            # Expected if podcast not found
            pass

    def test_refresh_signature_with_max_episodes(self, podcast_service):
        """Contract: refresh(max_episodes) -> RefreshResult."""
        from thestill.core.feed_manager import PodcastFeedManager

        feed_manager = podcast_service.feed_manager
        service = RefreshService(feed_manager=feed_manager, podcast_service=podcast_service)

        result = service.refresh(max_episodes=5)
        assert isinstance(result, RefreshResult)

    def test_refresh_signature_with_max_episodes_per_podcast(self, podcast_service):
        """Contract: refresh(max_episodes_per_podcast) -> RefreshResult."""
        from thestill.core.feed_manager import PodcastFeedManager

        feed_manager = podcast_service.feed_manager
        service = RefreshService(feed_manager=feed_manager, podcast_service=podcast_service)

        result = service.refresh(max_episodes_per_podcast=10)
        assert isinstance(result, RefreshResult)

    def test_refresh_signature_with_dry_run(self, podcast_service):
        """Contract: refresh(dry_run=True) -> RefreshResult."""
        from thestill.core.feed_manager import PodcastFeedManager

        feed_manager = podcast_service.feed_manager
        service = RefreshService(feed_manager=feed_manager, podcast_service=podcast_service)

        result = service.refresh(dry_run=True)
        assert isinstance(result, RefreshResult)

    def test_refresh_result_structure(self, podcast_service):
        """Contract: RefreshResult has required fields."""
        from thestill.core.feed_manager import PodcastFeedManager

        feed_manager = podcast_service.feed_manager
        service = RefreshService(feed_manager=feed_manager, podcast_service=podcast_service)

        result = service.refresh()

        # Required fields
        assert hasattr(result, "total_episodes")
        assert hasattr(result, "episodes_by_podcast")
        assert hasattr(result, "podcast_filter_applied")

        # Field types
        assert isinstance(result.total_episodes, int)
        assert isinstance(result.episodes_by_podcast, list)
        assert result.podcast_filter_applied is None or isinstance(result.podcast_filter_applied, str)

    def test_refresh_handles_invalid_podcast_gracefully(self, podcast_service, sample_podcast):
        """Contract: refresh() returns empty result for invalid podcast_id."""
        from thestill.core.feed_manager import PodcastFeedManager
        from thestill.models.podcast import Episode

        feed_manager = podcast_service.feed_manager
        service = RefreshService(feed_manager=feed_manager, podcast_service=podcast_service)

        # Mock media source to return episodes
        mock_source = Mock()
        mock_source.fetch_episodes.return_value = [
            Episode(
                title="Episode 1",
                description="Test",
                external_id="ep1",
                pub_date=datetime.now(),
                audio_url="https://example.com/ep1.mp3",
            )
        ]
        feed_manager.media_source_factory.detect_source = Mock(return_value=mock_source)

        # Discover episodes first
        feed_manager.get_new_episodes()

        # Test with invalid podcast_id - should return empty result (graceful handling)
        result = service.refresh(podcast_id=999)  # Non-existent podcast
        assert isinstance(result, RefreshResult)
        assert result.total_episodes == 0
        assert result.episodes_by_podcast == []


class TestStatsServiceContract:
    """Contract tests for StatsService public API."""

    def test_constructor_accepts_required_parameters(self, temp_storage, repository, path_manager):
        """Contract: Constructor accepts storage_path, podcast_repository, path_manager."""
        service = StatsService(storage_path=temp_storage, podcast_repository=repository, path_manager=path_manager)
        assert service is not None

    def test_constructor_accepts_str_or_path(self, repository, path_manager, temp_storage):
        """Contract: storage_path can be str or Path."""
        service1 = StatsService(
            storage_path=str(temp_storage), podcast_repository=repository, path_manager=path_manager
        )
        service2 = StatsService(storage_path=temp_storage, podcast_repository=repository, path_manager=path_manager)
        assert service1 is not None
        assert service2 is not None

    def test_get_stats_signature(self, temp_storage, repository, path_manager):
        """Contract: get_stats() -> SystemStats."""
        service = StatsService(storage_path=temp_storage, podcast_repository=repository, path_manager=path_manager)

        result = service.get_stats()
        assert isinstance(result, SystemStats)

    def test_system_stats_structure(self, temp_storage, repository, path_manager):
        """Contract: SystemStats has required fields."""
        service = StatsService(storage_path=temp_storage, podcast_repository=repository, path_manager=path_manager)

        stats = service.get_stats()

        # Required fields
        assert hasattr(stats, "podcasts_tracked")
        assert hasattr(stats, "episodes_total")
        # State breakdown fields
        assert hasattr(stats, "episodes_discovered")
        assert hasattr(stats, "episodes_downloaded")
        assert hasattr(stats, "episodes_downsampled")
        assert hasattr(stats, "episodes_transcribed")
        assert hasattr(stats, "episodes_cleaned")
        # Legacy fields
        assert hasattr(stats, "episodes_processed")
        assert hasattr(stats, "episodes_unprocessed")
        assert hasattr(stats, "transcripts_available")
        assert hasattr(stats, "audio_files_count")
        assert hasattr(stats, "storage_path")
        assert hasattr(stats, "last_updated")

        # Field types
        assert isinstance(stats.podcasts_tracked, int)
        assert isinstance(stats.episodes_total, int)
        assert isinstance(stats.episodes_discovered, int)
        assert isinstance(stats.episodes_downloaded, int)
        assert isinstance(stats.episodes_downsampled, int)
        assert isinstance(stats.episodes_transcribed, int)
        assert isinstance(stats.episodes_cleaned, int)
        assert isinstance(stats.episodes_processed, int)
        assert isinstance(stats.episodes_unprocessed, int)
        assert isinstance(stats.transcripts_available, int)
        assert isinstance(stats.audio_files_count, int)
        assert isinstance(stats.storage_path, str)
        assert isinstance(stats.last_updated, datetime)

    def test_system_stats_values_non_negative(self, temp_storage, repository, path_manager):
        """Contract: SystemStats numeric fields are non-negative."""
        service = StatsService(storage_path=temp_storage, podcast_repository=repository, path_manager=path_manager)

        stats = service.get_stats()

        # All counts should be non-negative
        assert stats.podcasts_tracked >= 0
        assert stats.episodes_total >= 0
        assert stats.episodes_discovered >= 0
        assert stats.episodes_downloaded >= 0
        assert stats.episodes_downsampled >= 0
        assert stats.episodes_transcribed >= 0
        assert stats.episodes_cleaned >= 0
        assert stats.episodes_processed >= 0
        assert stats.episodes_unprocessed >= 0
        assert stats.transcripts_available >= 0
        assert stats.audio_files_count >= 0

    def test_system_stats_consistency(self, temp_storage, repository, path_manager):
        """Contract: SystemStats maintains logical consistency."""
        service = StatsService(storage_path=temp_storage, podcast_repository=repository, path_manager=path_manager)

        stats = service.get_stats()

        # Sum of all states should equal total
        state_sum = (
            stats.episodes_discovered
            + stats.episodes_downloaded
            + stats.episodes_downsampled
            + stats.episodes_transcribed
            + stats.episodes_cleaned
        )
        assert state_sum == stats.episodes_total

        # Legacy fields should be consistent
        assert stats.episodes_processed == stats.episodes_cleaned
        assert stats.episodes_unprocessed == stats.episodes_total - stats.episodes_cleaned


class TestServiceContractStability:
    """Tests that verify service contracts remain stable across changes."""

    def test_podcast_service_has_stable_public_methods(self):
        """Contract: PodcastService maintains its public method names."""
        expected_methods = [
            "add_podcast",
            "remove_podcast",
            "get_podcasts",
            "get_podcast",
            "get_episode",
            "get_episodes",
            "get_transcript",
        ]

        for method_name in expected_methods:
            assert hasattr(PodcastService, method_name), f"Missing public method: {method_name}"

    def test_refresh_service_has_stable_public_methods(self):
        """Contract: RefreshService maintains its public method names."""
        expected_methods = ["refresh"]

        for method_name in expected_methods:
            assert hasattr(RefreshService, method_name), f"Missing public method: {method_name}"

    def test_stats_service_has_stable_public_methods(self):
        """Contract: StatsService maintains its public method names."""
        expected_methods = ["get_stats"]

        for method_name in expected_methods:
            assert hasattr(StatsService, method_name), f"Missing public method: {method_name}"

    def test_podcast_with_index_model_stability(self):
        """Contract: PodcastWithIndex maintains its field names."""
        expected_fields = [
            "index",
            "title",
            "description",
            "rss_url",
            "last_processed",
            "episodes_count",
            "episodes_processed",
        ]

        # Check model fields (use model_fields for Pydantic v2)
        model_fields = (
            PodcastWithIndex.model_fields if hasattr(PodcastWithIndex, "model_fields") else PodcastWithIndex.__fields__
        )
        field_names = set(model_fields.keys())
        for field_name in expected_fields:
            assert field_name in field_names, f"Missing field: {field_name}"

    def test_episode_with_index_model_stability(self):
        """Contract: EpisodeWithIndex maintains its field names."""
        expected_fields = [
            "podcast_index",
            "episode_index",
            "title",
            "description",
            "pub_date",
            "audio_url",
            "duration",
            "external_id",
            "state",  # Changed from "processed" (bool) to "state" (str)
            "transcript_available",
            "summary_available",
        ]

        model_fields = (
            EpisodeWithIndex.model_fields if hasattr(EpisodeWithIndex, "model_fields") else EpisodeWithIndex.__fields__
        )
        field_names = set(model_fields.keys())
        for field_name in expected_fields:
            assert field_name in field_names, f"Missing field: {field_name}"

    def test_refresh_result_model_stability(self):
        """Contract: RefreshResult maintains its field names."""
        expected_fields = ["total_episodes", "episodes_by_podcast", "podcast_filter_applied"]

        model_fields = (
            RefreshResult.model_fields if hasattr(RefreshResult, "model_fields") else RefreshResult.__fields__
        )
        field_names = set(model_fields.keys())
        for field_name in expected_fields:
            assert field_name in field_names, f"Missing field: {field_name}"

    def test_system_stats_model_stability(self):
        """Contract: SystemStats maintains its field names."""
        expected_fields = [
            "podcasts_tracked",
            "episodes_total",
            "episodes_processed",
            "episodes_unprocessed",
            "transcripts_available",
            "audio_files_count",
            "storage_path",
            "last_updated",
        ]

        model_fields = SystemStats.model_fields if hasattr(SystemStats, "model_fields") else SystemStats.__fields__
        field_names = set(model_fields.keys())
        for field_name in expected_fields:
            assert field_name in field_names, f"Missing field: {field_name}"
