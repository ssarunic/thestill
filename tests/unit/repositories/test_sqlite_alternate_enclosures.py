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

"""Repository tests for episode_alternate_enclosures (Podcasting 2.0)."""

from datetime import datetime

import pytest

from thestill.models.podcast import AlternateEnclosure, Episode, Podcast
from thestill.repositories.sqlite_podcast_repository import SqlitePodcastRepository


@pytest.fixture
def repo(tmp_path) -> SqlitePodcastRepository:
    return SqlitePodcastRepository(str(tmp_path / "alt.db"))


@pytest.fixture
def saved_episode(repo: SqlitePodcastRepository) -> Episode:
    episode = Episode(
        id="660e8400-e29b-41d4-a716-446655440010",
        external_id="ep-alt-001",
        title="Alt Test",
        description="d",
        pub_date=datetime(2026, 5, 1, 12, 0),
        audio_url="https://example.com/a.mp3",
    )
    podcast = Podcast(
        id="550e8400-e29b-41d4-a716-446655440010",
        rss_url="https://example.com/feed.xml",
        title="Alt Podcast",
        description="x",
        episodes=[episode],
    )
    repo.save(podcast)
    return episode


def _hls(uri: str = "https://cdn.x.com/master.m3u8", **kw) -> AlternateEnclosure:
    base = dict(source_uri=uri, mime_type="application/x-mpegURL", height=1080,
                bitrate=3_500_000.0, is_default=True)
    base.update(kw)
    return AlternateEnclosure(**base)


def test_add_and_get_round_trip(repo, saved_episode):
    inserted = repo.add_alternate_enclosures(saved_episode.id, [_hls()])
    assert inserted == 1

    rows = repo.get_alternate_enclosures(saved_episode.id)
    assert len(rows) == 1
    row = rows[0]
    assert row.source_uri == "https://cdn.x.com/master.m3u8"
    assert row.mime_type == "application/x-mpegURL"
    assert row.height == 1080
    assert row.bitrate == 3_500_000.0
    assert row.is_default is True
    assert row.episode_id == saved_episode.id
    assert row.id is not None


def test_empty_list_is_noop(repo, saved_episode):
    assert repo.add_alternate_enclosures(saved_episode.id, []) == 0
    assert repo.get_alternate_enclosures(saved_episode.id) == []


def test_duplicate_source_uri_skipped(repo, saved_episode):
    entry = _hls()
    assert repo.add_alternate_enclosures(saved_episode.id, [entry]) == 1
    # Re-running with the same URI is a no-op (UNIQUE(episode_id, source_uri)).
    assert repo.add_alternate_enclosures(saved_episode.id, [entry]) == 0
    assert len(repo.get_alternate_enclosures(saved_episode.id)) == 1


def test_distinct_source_uris_all_inserted(repo, saved_episode):
    entries = [
        _hls(uri="https://cdn.x.com/a.mp4", mime_type="video/mp4", is_default=False),
        _hls(uri="https://cdn.x.com/b.webm", mime_type="video/webm", is_default=False),
        _hls(uri="https://cdn.x.com/c.m3u8", is_default=True),
    ]
    assert repo.add_alternate_enclosures(saved_episode.id, entries) == 3
    rows = repo.get_alternate_enclosures(saved_episode.id)
    assert {r.source_uri for r in rows} == {e.source_uri for e in entries}
    defaults = [r for r in rows if r.is_default]
    assert len(defaults) == 1
    assert defaults[0].source_uri == "https://cdn.x.com/c.m3u8"


def test_optional_fields_nullable(repo, saved_episode):
    entry = AlternateEnclosure(
        source_uri="https://x.com/min.m3u8",
        mime_type="application/x-mpegURL",
    )
    assert repo.add_alternate_enclosures(saved_episode.id, [entry]) == 1
    row = repo.get_alternate_enclosures(saved_episode.id)[0]
    assert row.height is None
    assert row.bitrate is None
    assert row.length is None
    assert row.is_default is False


def test_cascade_on_episode_delete(repo, saved_episode):
    repo.add_alternate_enclosures(saved_episode.id, [_hls()])
    assert len(repo.get_alternate_enclosures(saved_episode.id)) == 1

    # Cascade is enforced by the FK ON DELETE CASCADE clause.
    with repo._get_connection() as conn:
        conn.execute("PRAGMA foreign_keys = ON")
        conn.execute("DELETE FROM episodes WHERE id = ?", (saved_episode.id,))

    assert repo.get_alternate_enclosures(saved_episode.id) == []
