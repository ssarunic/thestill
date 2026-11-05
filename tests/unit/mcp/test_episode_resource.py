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

"""The episode metadata resource (``thestill://podcasts/<id>/episodes/<id>``)."""

import json
from datetime import datetime, timezone
from unittest.mock import MagicMock, patch

import anyio

from thestill.mcp import resources
from thestill.mcp.identity import McpIdentity
from thestill.models.podcast import Episode, Podcast

STDIO = McpIdentity(user=None, is_admin=False, scopes=frozenset(), remote=False)


def _episode(external_id: str, pub_date):
    return Episode(
        external_id=external_id,
        title=external_id,
        description="",
        pub_date=pub_date,
        audio_url=f"https://example.com/{external_id}.mp3",
    )


def _read_resource(podcast: Podcast, episode: Episode):
    """Build the resource handler over a stub PodcastService and return ``read_resource``."""
    captured = {}
    service = MagicMock()
    service.get_podcast.return_value = podcast
    service.get_podcasts.return_value = []
    service.get_episode.return_value = episode
    service.storage_path = "/nonexistent"
    config = MagicMock(environment="development")

    def capture(_server, *, list_resources, read_resource):
        captured["read"] = read_resource

    with (
        patch.object(resources, "register_resources", capture),
        patch.object(resources, "PodcastService", return_value=service),
        patch("thestill.repositories.factory.make_repositories"),
    ):
        resources.setup_resources(MagicMock(), "/nonexistent", config=config)
    return captured["read"]


class TestEpisodeIndexWithUndatedEpisodes:
    def test_mixed_dated_and_undated_episodes_still_resolve(self):
        # An RSS entry with no <pubDate> is stored with pub_date=None
        # (PR #486); sorting it next to dated episodes must not raise.
        dated_new = _episode("new", datetime(2026, 11, 1, tzinfo=timezone.utc))
        dated_old = _episode("old", datetime(2026, 1, 1, tzinfo=timezone.utc))
        undated = _episode("undated", None)
        podcast = Podcast(
            rss_url="https://example.com/feed.xml",
            title="Show",
            description="",
            episodes=[dated_old, undated, dated_new],
        )
        read = _read_resource(podcast, dated_old)

        result = json.loads(anyio.run(read, "thestill://podcasts/show/episodes/old", STDIO))

        # Newest first, undated last: new=1, old=2, undated=3.
        assert result["episode_index"] == 2
        assert result["pub_date"] == "2026-01-01T00:00:00+00:00"
