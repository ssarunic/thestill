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

"""Regression: two methods projected partial column sets their row mappers
could not hydrate, raising IndexError the first time a REAL row (not a mock)
flowed through — found during the spec #44 Postgres port (FM-5: these paths
had only ever been exercised with hand-built mocks)."""

from __future__ import annotations

from datetime import datetime, timezone

import pytest

from thestill.models.podcast import Episode, Podcast, TranscriptLink
from thestill.repositories.sqlite_podcast_repository import SqlitePodcastRepository

EPISODE_ID = "00000000-0000-0000-0000-0000000000f1"


@pytest.fixture
def repo(tmp_path):
    r = SqlitePodcastRepository(db_path=str(tmp_path / "hydration.db"))
    r.save(
        Podcast(
            id="00000000-0000-0000-0000-0000000000a1",
            rss_url="https://example.com/feed.xml",
            title="Hydration Test Pod",
            description="",
            author="A. Author",
            explicit=True,
            episodes=[
                Episode(
                    id=EPISODE_ID,
                    external_id="ep-1",
                    title="Ep One",
                    description="d",
                    pub_date=datetime(2026, 1, 1, tzinfo=timezone.utc),
                    audio_url="https://example.com/1.mp3",
                    duration=60,
                )
            ],
        )
    )
    return r


def test_get_podcast_for_episode_hydrates_full_row(repo):
    """Previously raised IndexError: partial SELECT vs full _row_to_podcast."""
    podcast = repo.get_podcast_for_episode(EPISODE_ID)
    assert podcast is not None
    assert podcast.title == "Hydration Test Pod"
    assert podcast.author == "A. Author"  # column the old SELECT omitted
    assert podcast.explicit is True


def test_get_episodes_with_undownloaded_transcript_links_hydrates(repo):
    """Previously raised IndexError: partial DISTINCT projection vs episode_from_row."""
    repo.add_transcript_links(
        EPISODE_ID,
        [TranscriptLink(url="https://example.com/t.vtt", mime_type="text/vtt")],
    )
    rows = repo.get_episodes_with_undownloaded_transcript_links()
    assert len(rows) == 1
    episode, links = rows[0]
    assert episode.id == EPISODE_ID
    assert episode.title == "Ep One"
    assert len(links) == 1 and links[0].downloaded_path is None
    # Marking downloaded empties the pending list.
    repo.mark_transcript_downloaded(links[0].id, "/tmp/t.vtt")
    assert repo.get_episodes_with_undownloaded_transcript_links() == []
