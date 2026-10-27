"""MCP episode tools hand back the episode page and the publisher's audio.

``episode_url`` is what Claude should link a reader to; it needs
``PUBLIC_BASE_URL``, so a local stdio server (no public host) leaves it
``None`` and keeps only ``audio_url``.
"""

from __future__ import annotations

import json
import sqlite3
import uuid

import pytest
from mcp.client import Client
from mcp.server import Server

from tests.unit.mcp.test_tools_identity import _payload
from tests.unit.web.test_mcp_http import USER_A, Harness, isolated_env  # noqa: F401
from thestill.mcp.tools import setup_tools
from thestill.models.user import PodcastFollower

BASE = "https://thestill.example"
AUDIO = "https://cdn.example.com/ep.mp3"
PAGE = f"{BASE}/podcasts/the-show/episodes/the-episode"


@pytest.fixture
def anyio_backend():
    return "asyncio"


def _seed(storage_path, db_path):
    podcast_id, episode_id = str(uuid.uuid4()), str(uuid.uuid4())
    with sqlite3.connect(str(db_path)) as conn:
        conn.execute(
            "INSERT INTO podcasts (id, rss_url, title, slug) VALUES (?, ?, ?, ?)",
            (podcast_id, "https://example.com/feed.xml", "The Show", "the-show"),
        )
        conn.execute(
            "INSERT INTO episodes (id, podcast_id, external_id, title, slug, audio_url, summary_path, pub_date)"
            " VALUES (?, ?, ?, ?, ?, ?, ?, ?)",
            (
                episode_id,
                podcast_id,
                "guid-1",
                "The Episode",
                "the-episode",
                AUDIO,
                "the-show/the-episode.md",
                "2026-10-01T00:00:00+00:00",
            ),
        )
    summary = storage_path / "summaries" / "the-show" / "the-episode.md"
    summary.parent.mkdir(parents=True)
    summary.write_text("# Gist\n\nCapex is up.")
    return podcast_id, episode_id


class TestRemote:
    @pytest.fixture
    def world(self, isolated_env, tmp_path):  # noqa: F811
        h = Harness(tmp_path, public_base_url=BASE)
        podcast_id, episode_id = _seed(tmp_path, h.config.database_path)
        h.repos.follower.add(PodcastFollower(user_id=USER_A, podcast_id=podcast_id))
        return h, podcast_id, episode_id

    def test_list_episodes_carries_page_and_audio(self, world):
        h, podcast_id, _ = world
        with h.client() as c:
            out = _payload(
                Harness.rpc(
                    c, h.mint(USER_A), "tools/call", {"name": "list_episodes", "arguments": {"podcast_id": podcast_id}}
                )
            )
        [episode] = out["episodes"]
        assert episode["episode_url"] == PAGE
        assert episode["audio_url"] == AUDIO

    def test_summary_ends_with_the_links(self, world):
        h, podcast_id, episode_id = world
        args = {"name": "get_summary", "arguments": {"podcast_id": podcast_id, "episode_id": episode_id}}
        with h.client() as c:
            response = Harness.rpc(c, h.mint(USER_A), "tools/call", args)
        text = response.json()["result"]["content"][0]["text"]
        assert text.startswith("# Gist")
        assert text.endswith(f"---\nEpisode page: {PAGE}\nAudio: {AUDIO}")


class TestStdioWithoutPublicHost:
    @pytest.fixture
    def stdio(self, isolated_env, tmp_path):  # noqa: F811
        h = Harness(tmp_path)  # builds the DB; stdio loads its own config (no PUBLIC_BASE_URL)
        podcast_id, episode_id = _seed(tmp_path, h.config.database_path)
        server = Server("thestill-mcp-test")
        setup_tools(server, str(tmp_path))
        return server, podcast_id, episode_id

    @pytest.mark.anyio
    async def test_audio_only_no_relative_page_link(self, stdio):
        server, podcast_id, episode_id = stdio
        async with Client(server) as session:
            listed = json.loads((await session.call_tool("list_episodes", {"podcast_id": podcast_id})).content[0].text)
            summary = (
                (await session.call_tool("get_summary", {"podcast_id": podcast_id, "episode_id": episode_id}))
                .content[0]
                .text
            )
        assert listed["episodes"][0]["episode_url"] is None
        assert listed["episodes"][0]["audio_url"] == AUDIO
        assert "Episode page:" not in summary
        assert summary.endswith(f"---\nAudio: {AUDIO}")
