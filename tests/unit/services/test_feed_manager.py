"""
Unit tests for PodcastFeedManager.

Tests RSS parsing, YouTube integration, and episode discovery with mocked dependencies.
"""

import tempfile
from datetime import datetime
from pathlib import Path
from time import struct_time
from unittest.mock import MagicMock, Mock, create_autospec, patch

import pytest

from thestill.core.feed_manager import PodcastFeedManager
from thestill.core.media_source import YouTubeMediaSource
from thestill.models.podcast import Episode, Podcast
from thestill.repositories.podcast_repository import PodcastRepository
from thestill.utils.path_manager import PathManager


@pytest.fixture
def temp_storage():
    """Create temporary storage directory."""
    with tempfile.TemporaryDirectory() as tmpdir:
        yield Path(tmpdir)


@pytest.fixture
def mock_repository():
    """Create mock podcast repository."""
    mock_repo = Mock(spec=PodcastRepository)
    mock_repo.exists = Mock(return_value=False)
    mock_repo.save = Mock(return_value=True)
    mock_repo.delete = Mock(return_value=True)
    mock_repo.get_all = Mock(return_value=[])
    mock_repo.get_podcasts_for_refresh = Mock(return_value=([], {}))
    mock_repo.get_by_url = Mock(return_value=None)
    mock_repo.update = Mock(return_value=True)
    return mock_repo


@pytest.fixture
def path_manager(temp_storage):
    """Create real PathManager for testing."""
    return PathManager(str(temp_storage))


@pytest.fixture
def sample_podcasts():
    """Create sample podcasts for testing."""
    return [
        Podcast(
            title="Tech Talk",
            description="A tech podcast",
            rss_url="https://example.com/tech.xml",
            episodes=[
                Episode(
                    title="Episode 1",
                    audio_url="https://example.com/ep1.mp3",
                    external_id="ep1",
                    pub_date=datetime(2025, 1, 10),
                    description="First episode",
                ),
                Episode(
                    title="Episode 2",
                    audio_url="https://example.com/ep2.mp3",
                    external_id="ep2",
                    pub_date=datetime(2025, 1, 5),
                    description="Second episode",
                    audio_path="ep2.mp3",
                ),
            ],
        ),
        Podcast(
            title="News Daily",
            description="Daily news",
            rss_url="https://example.com/news.xml",
            episodes=[
                Episode(
                    title="News Today",
                    audio_url="https://example.com/news1.mp3",
                    external_id="news1",
                    pub_date=datetime(2025, 1, 15),
                    description="Today's news",
                ),
            ],
        ),
    ]


@pytest.fixture
def feed_manager(mock_repository, path_manager):
    """Create FeedManager with mocked dependencies."""
    manager = PodcastFeedManager(mock_repository, path_manager)
    # Mock media source factory to avoid actual network calls
    manager.media_source_factory = Mock()
    manager.media_source_factory.detect_source = Mock()
    return manager


class TestFeedManagerInitialization:
    """Test FeedManager initialization."""

    def test_init_creates_storage_directory(self, mock_repository, temp_storage):
        """Should create storage directory if it doesn't exist."""
        storage_path = temp_storage / "new_dir"
        path_manager = PathManager(str(storage_path))

        manager = PodcastFeedManager(mock_repository, path_manager)

        assert manager.repository is mock_repository
        assert manager.path_manager is path_manager
        assert storage_path.exists()

    def test_init_with_existing_directory(self, mock_repository, path_manager):
        """Should work with existing directory."""
        manager = PodcastFeedManager(mock_repository, path_manager)

        assert manager.storage_path.exists()


class TestAddPodcast:
    """Test add_podcast method."""

    @patch("thestill.core.feed_manager.feedparser.parse")
    def test_add_rss_podcast_success(self, mock_parse, feed_manager, mock_repository):
        """Should add RSS podcast successfully."""
        # Setup mock media source to return RSS metadata
        mock_source = Mock()
        mock_source.extract_metadata.return_value = {
            "title": "Test Podcast",
            "description": "A test podcast",
            "rss_url": "https://example.com/feed.xml",
        }
        feed_manager.media_source_factory.detect_source.return_value = mock_source
        mock_repository.exists.return_value = False
        mock_repository.save.return_value = True

        # Execute
        result = feed_manager.add_podcast("https://example.com/feed.xml")

        # Verify - add_podcast returns Podcast object on success, None on failure
        assert result is not None
        assert result.title == "Test Podcast"
        assert str(result.rss_url) == "https://example.com/feed.xml"
        mock_repository.save.assert_called_once()

    @patch("thestill.core.feed_manager.feedparser.parse")
    def test_add_podcast_already_exists(self, mock_parse, feed_manager, mock_repository):
        """Should return existing podcast if already exists (idempotent)."""
        # Setup
        mock_source = Mock()
        mock_source.extract_metadata.return_value = {
            "title": "Existing Podcast",
            "description": "Already in DB",
            "rss_url": "https://example.com/feed.xml",
        }
        feed_manager.media_source_factory.detect_source.return_value = mock_source

        mock_repository.exists.return_value = True
        existing_podcast = Mock()
        existing_podcast.title = "Existing Podcast"
        mock_repository.get_by_url.return_value = existing_podcast

        # Execute
        result = feed_manager.add_podcast("https://example.com/feed.xml")

        # Verify - returns existing podcast (idempotent behavior)
        assert result is not None
        assert result.title == "Existing Podcast"
        mock_repository.save.assert_not_called()

    def test_add_podcast_invalid_rss(self, feed_manager):
        """Should return None for invalid RSS feed."""
        # Setup - mock media source that returns None for invalid feed
        mock_source = Mock()
        mock_source.extract_metadata.return_value = None
        feed_manager.media_source_factory.detect_source.return_value = mock_source

        # Execute
        result = feed_manager.add_podcast("https://example.com/bad.xml")

        # Verify - returns None when metadata extraction fails
        assert result is None

    def test_add_youtube_podcast(self, feed_manager, mock_repository):
        """Should handle YouTube URLs."""
        # Setup - mock media source factory to return YouTube metadata
        mock_source = Mock()
        mock_source.extract_metadata.return_value = {
            "title": "YouTube Podcast",
            "description": "A YouTube channel",
            "rss_url": "https://youtube.com/watch?v=123",
            "language": "en",
        }
        feed_manager.media_source_factory.detect_source.return_value = mock_source
        mock_repository.exists.return_value = False
        mock_repository.save.return_value = True

        # Execute
        result = feed_manager.add_podcast("https://youtube.com/watch?v=123")

        # Verify - returns Podcast object on success
        assert result is not None
        assert result.title == "YouTube Podcast"
        mock_repository.save.assert_called_once()


class TestRemovePodcast:
    """Test remove_podcast method."""

    def test_remove_podcast_success(self, feed_manager, mock_repository):
        """Should remove podcast successfully."""
        mock_repository.delete.return_value = True

        result = feed_manager.remove_podcast("https://example.com/feed.xml")

        assert result is True
        mock_repository.delete.assert_called_once_with("https://example.com/feed.xml")

    def test_remove_podcast_not_found(self, feed_manager, mock_repository):
        """Should return False if podcast not found."""
        mock_repository.delete.return_value = False

        result = feed_manager.remove_podcast("https://example.com/notfound.xml")

        assert result is False


class TestListPodcasts:
    """Test list_podcasts method."""

    def test_list_podcasts_empty(self, feed_manager, mock_repository):
        """Should return empty list when no podcasts."""
        mock_repository.get_all.return_value = []

        result = feed_manager.list_podcasts()

        assert result == []

    def test_list_podcasts_with_data(self, feed_manager, mock_repository, sample_podcasts):
        """Should return all podcasts."""
        mock_repository.get_all.return_value = sample_podcasts

        result = feed_manager.list_podcasts()

        assert len(result) == 2
        assert result[0].title == "Tech Talk"
        assert result[1].title == "News Daily"


class TestGetNewEpisodes:
    """Test get_new_episodes method - simplified tests."""

    def test_get_new_episodes_youtube(self, feed_manager, mock_repository):
        """Should handle YouTube podcasts."""
        # Setup YouTube podcast
        youtube_podcast = Podcast(
            title="YouTube Channel",
            description="YT",
            rss_url="https://youtube.com/channel/123",
            episodes=[],
        )
        mock_repository.get_all.return_value = [youtube_podcast]
        mock_repository.get_podcasts_for_refresh.return_value = ([youtube_podcast], {})

        # Setup media source to return YouTube episodes
        mock_source = Mock()
        mock_source.fetch_episodes.return_value = [
            Episode(
                title="YT Video",
                audio_url="https://youtube.com/watch?v=abc",
                external_id="yt-abc",
                pub_date=datetime(2025, 1, 20),
                description="Video",
            )
        ]
        feed_manager.media_source_factory.detect_source.return_value = mock_source

        # Execute
        result = feed_manager.get_new_episodes()

        # Verify
        assert len(result) == 1
        podcast, episodes = result[0]
        assert len(episodes) == 1
        assert episodes[0].title == "YT Video"

    def test_refresh_feeds_reports_zero_errors_on_success(self, feed_manager, mock_repository):
        """FM-4: a clean refresh reports zero errored feeds."""
        podcast = Podcast(title="OK", description="", rss_url="https://example.com/ok.xml", episodes=[])
        mock_repository.get_podcasts_for_refresh.return_value = ([podcast], {})

        mock_source = Mock()
        mock_source.fetch_episodes.return_value = [
            Episode(
                title="New",
                audio_url="https://example.com/new.mp3",
                external_id="new-1",
                pub_date=datetime(2026, 5, 21),
                description="d",
            )
        ]
        feed_manager.media_source_factory.detect_source.return_value = mock_source

        outcome = feed_manager.refresh_feeds()

        assert outcome.podcasts_with_errors == 0
        assert outcome.total_podcasts == 1
        assert len(outcome.episodes_by_podcast) == 1

    def test_errored_feed_not_persisted(self, feed_manager, mock_repository):
        """FM-2: a feed that errors is not written to the batch.

        Its etag / last_modified / last_processed were advanced in-memory
        during fetching; persisting them would make the next refresh 304-skip
        a feed we never read (the self-perpetuating silent stall). Leaving the
        errored podcast out of the batch keeps the stored headers stale so the
        next run retries. ``refresh_feeds`` still reports the error so it is
        not silent.
        """
        podcast = Podcast(title="Boom", description="", rss_url="https://example.com/boom.xml", episodes=[])
        mock_repository.get_podcasts_for_refresh.return_value = ([podcast], {})

        mock_source = Mock()
        # The class of bug from the incident: a comparison TypeError.
        mock_source.fetch_episodes.side_effect = TypeError("can't compare offset-naive and offset-aware datetimes")
        feed_manager.media_source_factory.detect_source.return_value = mock_source

        outcome = feed_manager.refresh_feeds()

        assert outcome.podcasts_with_errors == 1
        assert outcome.episodes_by_podcast == []
        # The errored podcast must never reach the durable batch write.
        assert not mock_repository.save_refresh_batch.called

    @pytest.mark.parametrize(
        "stored_etag,response_etag,expect_persist",
        [
            ("old-etag", "new-etag", True),  # server rotated the validator → persist it
            ("same-etag", "same-etag", False),  # unchanged → skip the batch (spec #19)
        ],
        ids=["rotated", "unchanged"],
    )
    def test_304_persists_only_when_headers_rotate(
        self, feed_manager, mock_repository, stored_etag, response_etag, expect_persist
    ):
        """A 304 must persist a *rotated* ETag but skip the batch otherwise.

        RFC 7232 lets a server refresh ETag / Last-Modified on a 304. Dropping
        a rotated validator means the next refresh sends a stale one, the
        server returns a full 200, and the conditional-GET saving is lost; an
        unchanged 304 must still skip the write to keep that saving (spec #19).
        """
        from thestill.core.media_source import RSSMediaSource

        podcast = Podcast(title="P", description="", rss_url="https://example.com/p.xml", episodes=[], etag=stored_etag)
        mock_repository.get_podcasts_for_refresh.return_value = ([podcast], {})

        rss = RSSMediaSource()
        result = Mock()
        result.not_modified = True
        result.etag = response_etag
        result.last_modified = None
        rss.fetch_and_parse = Mock(return_value=result)
        feed_manager.media_source_factory.detect_source.return_value = rss

        outcome = feed_manager.refresh_feeds()

        assert outcome.conditional_get_hits == 1
        assert mock_repository.save_refresh_batch.called is expect_persist
        if expect_persist:
            changed, _rows = mock_repository.save_refresh_batch.call_args[0]
            assert podcast in changed


class TestFirstRefreshCapGate:
    """The max_episodes cap must apply ONLY on a podcast's first refresh.

    Capping an incremental refresh trims new episodes and then advances
    ``last_processed`` past them, leaving a permanent hole in the feed. The
    gate keeps the cap as an initial-backfill bound for brand-new podcasts
    while dropping it entirely once a podcast is tracked.
    """

    def _mock_source(self, feed_manager, episodes=None):
        # ``create_autospec`` (unlike a bare ``Mock()`` or even ``Mock(spec=...)``)
        # enforces the REAL ``fetch_episodes`` signature, so an RSS-only kwarg
        # leaking into a non-RSS call raises here instead of passing silently —
        # the "consistent-mock" gap that hid the known_external_ids crash
        # (issue #112, spec #42). ``YouTubeMediaSource`` carries the base
        # signature only, so it also stands in for the non-RSS path
        # (``isinstance(..., RSSMediaSource)`` is False).
        source = create_autospec(YouTubeMediaSource, instance=True)
        source.fetch_episodes.return_value = episodes or []
        feed_manager.media_source_factory.detect_source.return_value = source
        return source

    def test_first_refresh_applies_cap(self, feed_manager, mock_repository):
        """No known episodes and no checkpoint => first refresh => cap passed."""
        podcast = Podcast(title="New", description="", rss_url="https://example.com/new.xml", episodes=[])
        mock_repository.get_podcasts_for_refresh.return_value = ([podcast], {})
        source = self._mock_source(feed_manager)

        feed_manager.refresh_feeds(max_episodes_per_podcast=5)

        assert source.fetch_episodes.call_args.kwargs["max_episodes"] == 5

    def test_incremental_drops_cap_when_episodes_known(self, feed_manager, mock_repository):
        """Known external ids => already tracked => cap dropped (no hole)."""
        podcast = Podcast(title="Tracked", description="", rss_url="https://example.com/t.xml", episodes=[])
        mock_repository.get_podcasts_for_refresh.return_value = ([podcast], {podcast.id: {"existing-1"}})
        source = self._mock_source(feed_manager)

        feed_manager.refresh_feeds(max_episodes_per_podcast=5)

        assert source.fetch_episodes.call_args.kwargs["max_episodes"] is None

    def test_incremental_drops_cap_when_checkpoint_set(self, feed_manager, mock_repository):
        """A last_processed checkpoint alone marks the podcast as tracked."""
        podcast = Podcast(
            title="Checkpointed",
            description="",
            rss_url="https://example.com/c.xml",
            episodes=[],
            last_processed=datetime(2026, 5, 1),
        )
        mock_repository.get_podcasts_for_refresh.return_value = ([podcast], {})
        source = self._mock_source(feed_manager)

        feed_manager.refresh_feeds(max_episodes_per_podcast=5)

        assert source.fetch_episodes.call_args.kwargs["max_episodes"] is None

    def test_non_rss_source_not_passed_rss_only_kwargs(self, feed_manager, mock_repository):
        """``known_external_ids`` / ``podcast_slug`` are RSS-only; the YouTube
        source's signature rejects them, so they must not leak into the non-RSS
        fetch call. The autospec source enforces that contract, so a leak would
        raise (surfacing as a refresh error) instead of passing silently as it
        did with a bare ``Mock()`` (issue #112)."""
        podcast = Podcast(title="YT", description="", rss_url="https://youtube.com/@x", episodes=[])
        mock_repository.get_podcasts_for_refresh.return_value = ([podcast], {podcast.id: {"vid-1"}})
        source = self._mock_source(feed_manager)

        outcome = feed_manager.refresh_feeds(max_episodes_per_podcast=5)

        # The call went through the real YouTube signature without raising.
        assert outcome.podcasts_with_errors == 0
        called = source.fetch_episodes.call_args.kwargs
        assert set(called) <= {"url", "existing_episodes", "last_processed", "max_episodes"}

    def test_youtube_refresh_surfaces_episodes_through_real_signature(self, feed_manager, mock_repository):
        """End-to-end: a YouTube refresh against the real ``fetch_episodes``
        signature surfaces episodes. If an RSS-only kwarg leaked into the call,
        the autospec source would raise, the broad ``except`` would swallow it,
        and zero episodes would surface — so this regresses loudly."""
        podcast = Podcast(title="YT", description="", rss_url="https://youtube.com/@x", episodes=[])
        mock_repository.get_podcasts_for_refresh.return_value = ([podcast], {podcast.id: {"old"}})
        episode = Episode(
            title="YT Video",
            audio_url="https://youtube.com/watch?v=abc",
            external_id="yt-abc",
            pub_date=datetime(2026, 1, 20),
            description="v",
        )
        self._mock_source(feed_manager, episodes=[episode])

        outcome = feed_manager.refresh_feeds(max_episodes_per_podcast=5)

        assert outcome.podcasts_with_errors == 0
        assert len(outcome.episodes_by_podcast) == 1

    def test_autospec_source_rejects_rss_only_kwarg(self):
        """Guard-the-guard: the spec'd source mock must itself reject an
        RSS-only kwarg. A bare ``Mock()`` would accept it silently — proving
        the fixture now enforces the real contract (the root cause in #112)."""
        source = create_autospec(YouTubeMediaSource, instance=True)
        with pytest.raises(TypeError):
            source.fetch_episodes(url="u", existing_episodes=[], known_external_ids={"x"})


class TestEpisodeMarking:
    """Test episode marking methods - use repository.update_episode API."""

    def test_mark_episode_downloaded(self, feed_manager, mock_repository):
        """Should call repository.update_episode for downloaded episodes."""
        mock_repository.update_episode = Mock(return_value=True)

        # Execute
        feed_manager.mark_episode_downloaded("https://example.com/feed.xml", "ep1", "/path/to/audio.mp3")

        # Verify
        mock_repository.update_episode.assert_called_once_with(
            "https://example.com/feed.xml", "ep1", {"audio_path": "/path/to/audio.mp3"}
        )

    def test_mark_episode_downsampled(self, feed_manager, mock_repository):
        """Should call repository.update_episode for downsampled episodes."""
        mock_repository.update_episode = Mock(return_value=True)

        # Execute
        feed_manager.mark_episode_downsampled("https://example.com/feed.xml", "ep1", "/path/to/audio_16k.wav")

        # Verify
        mock_repository.update_episode.assert_called_once_with(
            "https://example.com/feed.xml",
            "ep1",
            {"downsampled_audio_path": "/path/to/audio_16k.wav"},
        )

    def test_mark_episode_processed(self, feed_manager, mock_repository):
        """Should call repository.update_episode for processed episodes."""
        mock_repository.update_episode = Mock(return_value=True)

        # Execute
        feed_manager.mark_episode_processed(
            "https://example.com/feed.xml",
            "ep1",
            raw_transcript_path="/path/to/transcript.json",
            summary_path="/path/to/summary.md",
        )

        # Verify
        mock_repository.update_episode.assert_called_once()
        call_args = mock_repository.update_episode.call_args[0]
        assert call_args[0] == "https://example.com/feed.xml"
        assert call_args[1] == "ep1"
        updates = call_args[2]
        # No longer sets processed=True - state is auto-computed from file paths
        assert updates["raw_transcript_path"] == "/path/to/transcript.json"
        assert updates["summary_path"] == "/path/to/summary.md"


class TestEdgeCases:
    """Test edge cases and error handling."""

    def test_add_podcast_network_error(self, feed_manager):
        """Should handle network errors gracefully."""
        # Setup - mock media source that raises an error
        mock_source = Mock()
        mock_source.extract_metadata.return_value = None  # Network error leads to None
        feed_manager.media_source_factory.detect_source.return_value = mock_source

        result = feed_manager.add_podcast("https://example.com/feed.xml")

        assert result is None

    @patch("thestill.core.feed_manager.feedparser.parse")
    def test_get_new_episodes_malformed_feed(self, mock_parse, feed_manager, mock_repository):
        """Should skip podcasts with malformed feeds."""
        podcast = Podcast(title="Bad Feed", description="", rss_url="https://example.com/bad.xml", episodes=[])
        mock_repository.get_all.return_value = [podcast]
        mock_repository.get_podcasts_for_refresh.return_value = ([podcast], {})

        # Simulate parsing error
        mock_parse.side_effect = Exception("Parse error")

        # Execute
        result = feed_manager.get_new_episodes()

        # Verify - should return empty list, not crash
        assert result == []

    def test_mark_episode_not_found(self, feed_manager, mock_repository):
        """Should handle marking non-existent episode gracefully."""
        mock_repository.update_episode = Mock(return_value=False)

        # Should not raise exception
        feed_manager.mark_episode_downloaded("https://notfound.xml", "ep1", "/path")

        # Verify - called but returned False
        mock_repository.update_episode.assert_called_once()


class TestTransactionContextManager:
    """Test transaction context manager for batch updates."""

    def test_transaction_batches_saves(self, feed_manager, mock_repository, sample_podcasts):
        """Should defer repository saves until transaction completes."""
        # Setup: Mock repository to return a podcast
        podcast = sample_podcasts[0]
        mock_repository.get_by_url.return_value = podcast

        # Execute: Multiple updates within transaction
        with feed_manager.transaction():
            feed_manager.mark_episode_downloaded(str(podcast.rss_url), "ep1", "audio.mp3")
            feed_manager.mark_episode_downsampled(str(podcast.rss_url), "ep1", "audio_16k.wav")
            feed_manager.mark_episode_processed(str(podcast.rss_url), "ep1", raw_transcript_path="transcript.json")

            # Verify: Repository save NOT called yet (still in transaction)
            mock_repository.save.assert_not_called()
            # Verify: Repository update_episode NOT called (transaction mode)
            mock_repository.update_episode.assert_not_called()

        # Verify: Repository save called ONCE after transaction completes
        from thestill.models.podcast import EpisodeState

        assert mock_repository.save.call_count == 1
        saved_podcast = mock_repository.save.call_args[0][0]
        assert saved_podcast.episodes[0].audio_path == "audio.mp3"
        assert saved_podcast.episodes[0].downsampled_audio_path == "audio_16k.wav"
        assert saved_podcast.episodes[0].raw_transcript_path == "transcript.json"
        assert saved_podcast.episodes[0].state == EpisodeState.TRANSCRIBED

    def test_transaction_single_save_per_podcast(self, feed_manager, mock_repository, sample_podcasts):
        """Should save each podcast only once even with multiple episode updates."""
        # Setup
        podcast = sample_podcasts[0]
        mock_repository.get_by_url.return_value = podcast

        # Execute: Multiple updates to different episodes
        with feed_manager.transaction():
            feed_manager.mark_episode_downloaded(str(podcast.rss_url), "ep1", "audio1.mp3")
            feed_manager.mark_episode_downloaded(str(podcast.rss_url), "ep2", "audio2.mp3")

        # Verify: Repository save called exactly once for the podcast
        assert mock_repository.save.call_count == 1

    def test_transaction_multiple_podcasts(self, feed_manager, mock_repository, sample_podcasts):
        """Should handle updates to multiple podcasts in one transaction."""
        # Setup
        podcast1 = sample_podcasts[0]
        podcast2 = sample_podcasts[1]

        def find_by_url_side_effect(url):
            if url == str(podcast1.rss_url):
                return podcast1
            elif url == str(podcast2.rss_url):
                return podcast2
            return None

        mock_repository.get_by_url.side_effect = find_by_url_side_effect

        # Execute
        with feed_manager.transaction():
            feed_manager.mark_episode_downloaded(str(podcast1.rss_url), "ep1", "audio1.mp3")
            feed_manager.mark_episode_downloaded(str(podcast2.rss_url), "news1", "news1.mp3")

        # Verify: Repository save called twice (once per podcast)
        assert mock_repository.save.call_count == 2

    def test_transaction_nested_is_noop(self, feed_manager, mock_repository, sample_podcasts):
        """Should handle nested transactions (inner is no-op)."""
        # Setup
        podcast = sample_podcasts[0]
        mock_repository.get_by_url.return_value = podcast

        # Execute: Nested transaction
        with feed_manager.transaction():
            feed_manager.mark_episode_downloaded(str(podcast.rss_url), "ep1", "audio.mp3")

            # Inner transaction (should be no-op)
            with feed_manager.transaction():
                feed_manager.mark_episode_downsampled(str(podcast.rss_url), "ep1", "audio_16k.wav")

            # Still in outer transaction
            mock_repository.save.assert_not_called()

        # Verify: Save called once after outer transaction
        assert mock_repository.save.call_count == 1

    def test_transaction_persists_on_exception(self, feed_manager, mock_repository, sample_podcasts):
        """Should still persist changes if exception occurs in transaction."""
        # Setup
        podcast = sample_podcasts[0]
        mock_repository.get_by_url.return_value = podcast

        # Execute: Exception within transaction
        try:
            with feed_manager.transaction():
                feed_manager.mark_episode_downloaded(str(podcast.rss_url), "ep1", "audio.mp3")
                raise ValueError("Test exception")
        except ValueError:
            pass

        # Verify: Save still called (no rollback in current implementation)
        assert mock_repository.save.call_count == 1

    def test_transaction_episode_not_found(self, feed_manager, mock_repository, sample_podcasts):
        """Should handle episode not found within transaction."""
        # Setup
        podcast = sample_podcasts[0]
        mock_repository.get_by_url.return_value = podcast

        # Execute: Update non-existent episode
        with feed_manager.transaction():
            feed_manager.mark_episode_downloaded(str(podcast.rss_url), "nonexistent", "audio.mp3")

        # Verify: Save called but episode not found (logged warning)
        assert mock_repository.save.call_count == 1

    def test_transaction_podcast_not_found(self, feed_manager, mock_repository):
        """Should handle podcast not found within transaction."""
        # Setup
        mock_repository.get_by_url.return_value = None

        # Execute: Update episode on non-existent podcast
        with feed_manager.transaction():
            feed_manager.mark_episode_downloaded("https://notfound.xml", "ep1", "audio.mp3")

        # Verify: No save called (no podcast to save)
        mock_repository.save.assert_not_called()

    def test_transaction_caches_podcast(self, feed_manager, mock_repository, sample_podcasts):
        """Should cache podcast on first access and reuse for subsequent updates."""
        # Setup
        podcast = sample_podcasts[0]
        mock_repository.get_by_url.return_value = podcast

        # Execute: Multiple updates within transaction
        with feed_manager.transaction():
            feed_manager.mark_episode_downloaded(str(podcast.rss_url), "ep1", "audio.mp3")
            feed_manager.mark_episode_downsampled(str(podcast.rss_url), "ep1", "audio_16k.wav")

        # Verify: Repository find_by_url called only once (cached)
        assert mock_repository.get_by_url.call_count == 1

    def test_without_transaction_uses_repository_directly(self, feed_manager, mock_repository):
        """Should use repository.update_episode when not in transaction."""
        # Setup
        mock_repository.update_episode = Mock(return_value=True)

        # Execute: Update without transaction
        feed_manager.mark_episode_downloaded("https://example.com/feed.xml", "ep1", "audio.mp3")

        # Verify: Repository update_episode called directly
        mock_repository.update_episode.assert_called_once_with(
            "https://example.com/feed.xml", "ep1", {"audio_path": "audio.mp3"}
        )
        # Verify: Repository save NOT called
        mock_repository.save.assert_not_called()
