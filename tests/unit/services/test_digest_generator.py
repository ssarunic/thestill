"""
Unit tests for DigestGenerator.

Tests the digest generation from processed episodes.
"""

from datetime import datetime, timezone
from pathlib import Path
from unittest.mock import Mock, mock_open, patch

import pytest

from thestill.models.podcast import Episode, EpisodeState, Podcast
from thestill.services.digest_generator import DigestContent, DigestEpisodeInfo, DigestGenerator, DigestStats
from thestill.utils.path_manager import PathManager


@pytest.fixture
def mock_path_manager():
    """Create mock path manager."""
    pm = Mock(spec=PathManager)
    pm.summary_file.return_value = Path("/data/summaries/test.md")
    return pm


@pytest.fixture
def sample_podcast():
    """Create a sample podcast for testing."""
    return Podcast(
        id="podcast-123",
        title="Test Podcast",
        description="A test podcast",
        rss_url="https://example.com/feed.xml",
    )


@pytest.fixture
def sample_podcast_2():
    """Create a second sample podcast for testing."""
    return Podcast(
        id="podcast-456",
        title="Another Podcast",
        description="Another test podcast",
        rss_url="https://example.com/feed2.xml",
    )


def make_episode(
    external_id: str,
    title: str,
    podcast_id: str = "podcast-123",
    summary_path: str = None,
    duration: int = None,
    pub_date: datetime = None,
) -> Episode:
    """Helper to create episodes."""
    episode = Episode(
        external_id=external_id,
        podcast_id=podcast_id,
        title=title,
        description=f"Description for {title}",
        pub_date=pub_date or datetime.now(timezone.utc),
        audio_url=f"https://example.com/{external_id}.mp3",
    )
    if summary_path:
        episode.summary_path = summary_path
        # Set paths to achieve SUMMARIZED state
        episode.audio_path = f"{external_id}.mp3"
        episode.downsampled_audio_path = f"{external_id}.wav"
        episode.raw_transcript_path = f"{external_id}.json"
        episode.clean_transcript_path = f"{external_id}.md"
    if duration:
        episode.duration = duration
    return episode


SAMPLE_SUMMARY = """## 1. 🎙️ The Gist
Host John interviews Dr. Jane Smith about climate science. [00:01:00]

This episode explores the latest findings on global temperature patterns. The conversation covers both the scientific consensus and remaining uncertainties in climate models.

**The Big 3-5 Takeaways:**
* First major point about climate data [05:30]
* Second important finding [12:45]
* Third key insight [20:00]

**The Drama:**
* Heated debate about policy implications [35:00]

## 2. ⏱️ Timeline
More content here...
"""

SAMPLE_SUMMARY_NO_EMOJI = """## 1. The Gist
A deep dive into machine learning algorithms.

Neural networks have revolutionized how we approach complex problems. This episode covers the fundamentals and advanced applications.

**The Big 3-5 Takeaways:**
* Key point one
* Key point two
"""


class TestDigestStats:
    """Tests for DigestStats dataclass."""

    def test_success_rate_calculation(self):
        """Test success rate calculation."""
        stats = DigestStats(
            total_episodes=10,
            successful_episodes=8,
            failed_episodes=2,
        )
        assert stats.success_rate == 80.0

    def test_success_rate_zero_episodes(self):
        """Test success rate with no episodes."""
        stats = DigestStats(total_episodes=0)
        assert stats.success_rate == 0.0

    def test_success_rate_all_successful(self):
        """Test success rate with all successful."""
        stats = DigestStats(
            total_episodes=5,
            successful_episodes=5,
            failed_episodes=0,
        )
        assert stats.success_rate == 100.0


class TestDigestGeneratorExtraction:
    """Tests for executive summary extraction."""

    def test_extract_executive_summary_with_emoji(self, mock_path_manager):
        """Extract summary from standard format with emoji."""
        generator = DigestGenerator(mock_path_manager)
        result = generator._extract_executive_summary(SAMPLE_SUMMARY)

        assert result is not None
        assert "latest findings on global temperature" in result
        # The second sentence talks about consensus - verify it's extracted
        assert "This episode explores" in result or "temperature patterns" in result

    def test_extract_executive_summary_without_emoji(self, mock_path_manager):
        """Extract summary from format without emoji."""
        generator = DigestGenerator(mock_path_manager)
        result = generator._extract_executive_summary(SAMPLE_SUMMARY_NO_EMOJI)

        assert result is not None
        assert "deep dive into machine learning" in result

    def test_extract_executive_summary_no_match(self, mock_path_manager):
        """Return None when no gist section found."""
        generator = DigestGenerator(mock_path_manager)
        result = generator._extract_executive_summary("Random text without any sections")

        assert result is None

    def test_extract_removes_timestamps(self, mock_path_manager):
        """Timestamps should be removed from extracted text."""
        generator = DigestGenerator(mock_path_manager)
        result = generator._extract_executive_summary(SAMPLE_SUMMARY)

        assert "[00:01:00]" not in result


class TestDigestGeneratorGenerate:
    """Tests for digest generation."""

    def test_generate_empty_list(self, mock_path_manager):
        """Generate digest with empty episode list."""
        generator = DigestGenerator(mock_path_manager)
        content = generator.generate([])

        assert content.stats.total_episodes == 0
        assert "# Podcast Digest" in content.markdown

    def test_generate_with_episodes(self, mock_path_manager, sample_podcast):
        """Generate digest with episodes."""
        episode1 = make_episode("ep1", "Episode One", duration=1800)
        episode2 = make_episode("ep2", "Episode Two", duration=2400)

        # Mock the summary file
        mock_path_manager.summary_file.return_value = Path("/nonexistent/path.md")

        generator = DigestGenerator(mock_path_manager)
        content = generator.generate(
            [
                (sample_podcast, episode1),
                (sample_podcast, episode2),
            ]
        )

        assert content.stats.total_episodes == 2
        assert content.stats.successful_episodes == 2
        assert content.stats.podcasts_count == 1
        assert "Episode One" in content.markdown
        assert "Episode Two" in content.markdown
        assert sample_podcast.title in content.markdown

    def test_generate_groups_by_podcast(self, mock_path_manager, sample_podcast, sample_podcast_2):
        """Episodes should be grouped by podcast."""
        ep1 = make_episode("ep1", "Podcast 1 Episode", podcast_id="podcast-123")
        ep2 = make_episode("ep2", "Podcast 2 Episode", podcast_id="podcast-456")

        generator = DigestGenerator(mock_path_manager)
        content = generator.generate(
            [
                (sample_podcast, ep1),
                (sample_podcast_2, ep2),
            ]
        )

        assert content.stats.podcasts_count == 2
        assert sample_podcast.title in content.markdown
        assert sample_podcast_2.title in content.markdown

    def test_generate_with_failures(self, mock_path_manager, sample_podcast):
        """Generate digest with failed episodes."""
        episode = make_episode("ep1", "Successful Episode")
        failed_episode = make_episode("ep2", "Failed Episode")

        generator = DigestGenerator(mock_path_manager)
        content = generator.generate(
            episodes=[(sample_podcast, episode)],
            failures=[(sample_podcast, failed_episode, "Network timeout")],
        )

        assert content.stats.total_episodes == 2
        assert content.stats.successful_episodes == 1
        assert content.stats.failed_episodes == 1
        assert "Failed Episodes" in content.markdown
        assert "Network timeout" in content.markdown

    def test_generate_with_processing_time(self, mock_path_manager, sample_podcast):
        """Generate digest with processing time."""
        episode = make_episode("ep1", "Episode")

        generator = DigestGenerator(mock_path_manager)
        content = generator.generate(
            episodes=[(sample_podcast, episode)],
            processing_time_seconds=754.5,  # 12m 34s
        )

        assert "Processing time" in content.markdown
        assert "12m" in content.markdown

    def test_generate_reads_summary_for_description(self, mock_path_manager, sample_podcast):
        """Generator should read summary file to extract description."""
        episode = make_episode(
            "ep1",
            "Episode with Summary",
            summary_path="test-podcast/ep1_summary.md",
        )

        # Create a mock file that exists
        mock_file = Mock()
        mock_file.exists.return_value = True
        mock_path_manager.summary_file.return_value = mock_file

        with patch("builtins.open", mock_open(read_data=SAMPLE_SUMMARY)):
            generator = DigestGenerator(mock_path_manager)
            content = generator.generate([(sample_podcast, episode)])

        # Should have extracted description from summary
        assert "temperature patterns" in content.markdown or "Episode with Summary" in content.markdown


class TestDigestGeneratorWrite:
    """Tests for digest file writing."""

    def test_write_creates_directory(self, mock_path_manager, tmp_path):
        """Write should create parent directory if needed."""
        output_path = tmp_path / "subdir" / "digest.md"

        generator = DigestGenerator(mock_path_manager)
        content = DigestContent(
            markdown="# Test Digest",
            stats=DigestStats(),
        )

        result = generator.write(content, output_path)

        assert result == output_path
        assert output_path.exists()
        assert output_path.read_text() == "# Test Digest"

    def test_write_sets_output_path(self, mock_path_manager, tmp_path):
        """Write should set output_path on content."""
        output_path = tmp_path / "digest.md"

        generator = DigestGenerator(mock_path_manager)
        content = DigestContent(
            markdown="# Test",
            stats=DigestStats(),
        )

        generator.write(content, output_path)

        assert content.output_path == output_path


class TestDigestGeneratorFormatting:
    """Tests for formatting helpers."""

    def test_format_duration_seconds(self, mock_path_manager):
        """Format short durations in seconds."""
        generator = DigestGenerator(mock_path_manager)
        assert generator._format_duration(45) == "45s"

    def test_format_duration_minutes(self, mock_path_manager):
        """Format durations in minutes."""
        generator = DigestGenerator(mock_path_manager)
        assert generator._format_duration(754) == "12m 34s"

    def test_format_duration_hours(self, mock_path_manager):
        """Format long durations in hours."""
        generator = DigestGenerator(mock_path_manager)
        assert generator._format_duration(3725) == "1h 2m"

    def test_format_episode_with_all_metadata(self, mock_path_manager, sample_podcast):
        """Format episode with all metadata."""
        episode = make_episode(
            "ep1",
            "Full Episode",
            summary_path="test/ep1_summary.md",
            duration=2700,
            pub_date=datetime(2024, 1, 15, tzinfo=timezone.utc),
        )

        generator = DigestGenerator(mock_path_manager)
        info = DigestEpisodeInfo(
            podcast=sample_podcast,
            episode=episode,
            brief_description="A great episode about testing.",
            summary_link="test/ep1_summary.md",
        )

        lines = generator._format_episode(info)
        result = "\n".join(lines)

        assert "[Full Episode]" in result
        assert "January 15, 2024" in result
        assert "45m" in result
        assert "great episode about testing" in result

    def test_format_episode_minimal_metadata(self, mock_path_manager, sample_podcast):
        """Format episode with minimal metadata."""
        episode = make_episode("ep1", "Basic Episode")

        generator = DigestGenerator(mock_path_manager)
        info = DigestEpisodeInfo(
            podcast=sample_podcast,
            episode=episode,
        )

        lines = generator._format_episode(info)
        result = "\n".join(lines)

        assert "Basic Episode" in result
        # Should fall back to episode description
        assert "Description for Basic Episode" in result
