"""Regression tests for the refresh discovery-watermark poisoning bug.

Bug: ``last_processed`` was overloaded as BOTH the incremental-refresh
discovery watermark (newest episode pub_date, compared via
``episode_date > last_processed``) AND a wall-clock "we just processed an
episode" timestamp written by ``mark_episode_processed``. The wall-clock
writes pushed the watermark ahead of every real episode, so a newly-published
episode whose pub_date fell *before* the last processing run was silently
skipped by refresh — even though its GUID had never been seen.

Fix: ``last_processed`` is the watermark only; the processing time moved to a
separate ``last_processed_at`` column written by ``touch_last_processed_at``.
"""

from __future__ import annotations

import tempfile
from datetime import datetime, timedelta, timezone
from pathlib import Path

import feedparser
import pytest

from thestill.core.feed_manager import PodcastFeedManager
from thestill.core.media_source import RSSMediaSource
from thestill.models.podcast import Episode, Podcast
from thestill.repositories.sqlite_podcast_repository import SqlitePodcastRepository
from thestill.utils.path_manager import PathManager


@pytest.fixture
def repo():
    with tempfile.TemporaryDirectory() as d:
        yield SqlitePodcastRepository(db_path=str(Path(d) / "t.db"))


@pytest.fixture
def feed_manager(repo):
    with tempfile.TemporaryDirectory() as storage:
        yield PodcastFeedManager(repo, PathManager(storage))


def _rss(*items: tuple[str, str, str]) -> "feedparser.FeedParserDict":
    """Build a parsed feed from (guid, title, RFC822-pubdate) tuples."""
    entries = "".join(
        f"""<item>
              <title>{title}</title>
              <guid>{guid}</guid>
              <pubDate>{pub}</pubDate>
              <enclosure url="https://example.com/{guid}.mp3" type="audio/mpeg" length="1"/>
            </item>"""
        for guid, title, pub in items
    )
    xml = f"""<?xml version="1.0"?><rss version="2.0"><channel>
        <title>Test</title>{entries}</channel></rss>"""
    return feedparser.parse(xml)


class TestMarkProcessedDoesNotPoisonWatermark:
    def test_mark_processed_leaves_watermark_but_sets_processing_time(self, repo, feed_manager):
        # A podcast whose discovery watermark sits at its newest episode.
        watermark = datetime(2026, 6, 6, 15, 0, tzinfo=timezone.utc)
        ep = Episode(
            title="Ep A",
            description="",
            pub_date=watermark,
            audio_url="https://example.com/a.mp3",
            external_id="guid-a",
        )
        podcast = Podcast(
            rss_url="https://example.com/feed.xml",
            title="Pod",
            description="",
            last_processed=watermark,
            episodes=[ep],
        )
        repo.save(podcast)

        # Processing an episode happens "now" — hours after publish.
        feed_manager.mark_episode_processed(
            "https://example.com/feed.xml", "guid-a", raw_transcript_path="raw/a.json"
        )

        reloaded = repo.get_by_url("https://example.com/feed.xml")
        # The watermark must NOT have moved to a wall clock (the bug).
        assert reloaded.last_processed == watermark
        # The processing time is recorded separately, and is recent.
        assert reloaded.last_processed_at is not None
        assert reloaded.last_processed_at > watermark


class TestDiscoveryUsesWatermarkNotWallClock:
    def test_episode_published_before_last_processing_run_is_discovered(self):
        # The exact failure shape: watermark at an OLD episode, a NEW episode
        # published later than the watermark but *earlier* than the wall clock
        # of a past processing run. With the fix, discovery compares against the
        # watermark (not the wall clock), so the new GUID is found.
        watermark = datetime(2026, 6, 6, 15, 0, tzinfo=timezone.utc)
        new_pub = datetime(2026, 6, 8, 18, 30, tzinfo=timezone.utc)  # > watermark, < "now"
        assert new_pub > watermark
        assert new_pub < datetime.now(timezone.utc)

        existing = Episode(
            title="Old",
            description="",
            pub_date=watermark,
            audio_url="https://example.com/old.mp3",
            external_id="guid-old",
        )
        feed = _rss(
            ("guid-new", "New EP #263", "Mon, 08 Jun 2026 18:30:00 GMT"),
            ("guid-old", "Old", "Sat, 06 Jun 2026 15:00:00 GMT"),
        )

        new_eps = RSSMediaSource().fetch_episodes(
            url="https://example.com/feed.xml",
            existing_episodes=[existing],
            last_processed=watermark,
            podcast_slug="pod",
            parsed_feed=feed,
            known_external_ids={"guid-old"},
        )

        found = {e.external_id for e in new_eps}
        assert "guid-new" in found, "newly-published episode must be discovered via the watermark"
        assert "guid-old" not in found, "already-known GUID must not be re-added"

    def test_known_guid_never_redicovered_even_if_newer_than_watermark(self):
        # GUID dedup is authoritative: an episode already tracked is not
        # re-added regardless of the watermark.
        watermark = datetime(2026, 6, 6, 15, 0, tzinfo=timezone.utc)
        feed = _rss(("guid-old", "Old", "Sat, 06 Jun 2026 15:00:00 GMT"))
        new_eps = RSSMediaSource().fetch_episodes(
            url="https://example.com/feed.xml",
            existing_episodes=[],
            last_processed=watermark - timedelta(days=30),
            podcast_slug="pod",
            parsed_feed=feed,
            known_external_ids={"guid-old"},
        )
        assert [e.external_id for e in new_eps] == []
