"""Spec #78 Phase 2 — identity-aware tool and resource handlers.

Remote branches are exercised end to end through the mounted Streamable
HTTP transport (real guard, real token rows, real SQLite); the stdio
branches through the SDK's in-memory client/server session with no request
in context, so the same handlers are proven to take the identity-None path.
"""

from __future__ import annotations

import json
import sqlite3
import uuid

import pytest
from mcp.server import Server
from mcp.shared.memory import create_connected_server_and_client_session

from tests.unit.web.test_mcp_http import USER_A, USER_B, Harness, isolated_env  # noqa: F401
from thestill.mcp.tools import setup_tools
from thestill.models.user import PodcastFollower

PIPELINE_TOOLS = {
    "refresh_feeds",
    "download_episodes",
    "downsample_audio",
    "transcribe_episodes",
    "clean_transcripts",
    "process_episode",
    "summarize_episodes",
}


@pytest.fixture
def anyio_backend():
    return "asyncio"


def _seed_podcasts(db_path, n=2):
    ids = []
    with sqlite3.connect(str(db_path)) as conn:
        for i in range(n):
            pid = str(uuid.uuid4())
            conn.execute(
                "INSERT INTO podcasts (id, rss_url, title, slug) VALUES (?, ?, ?, ?)",
                (pid, f"https://example.com/feed{i}.xml", f"Podcast {i}", f"podcast-{i}"),
            )
            ids.append(pid)
    return ids


def _payload(response):
    assert response.status_code == 200, response.content
    body = response.json()
    assert "error" not in body, body
    return json.loads(body["result"]["content"][0]["text"])


@pytest.fixture
def world(harness):
    """Two users, two podcasts: A follows p0, B (admin) follows p1."""
    p0, p1 = _seed_podcasts(harness.config.database_path)
    harness.repos.follower.add(PodcastFollower(user_id=USER_A, podcast_id=p0))
    harness.repos.follower.add(PodcastFollower(user_id=USER_B, podcast_id=p1))
    return harness, p0, p1


@pytest.fixture
def harness(isolated_env, tmp_path):  # noqa: F811
    return Harness(tmp_path)


class TestListPodcasts:
    def test_each_caller_sees_own_follows_with_global_ids_and_no_index(self, world):
        h, p0, p1 = world
        a = h.mint(USER_A)
        b = h.mint(USER_B, is_admin=True)
        with h.client() as c:
            rows_a = _payload(Harness.rpc(c, a, "tools/call", {"name": "list_podcasts", "arguments": {}}))["podcasts"]
            rows_b = _payload(Harness.rpc(c, b, "tools/call", {"name": "list_podcasts", "arguments": {}}))["podcasts"]
        assert [r["id"] for r in rows_a] == [p0]
        assert [r["id"] for r in rows_b] == [p1]
        assert rows_a[0]["slug"] == "podcast-0"
        assert "index" not in rows_a[0]

    def test_admin_all_true_lists_everything_non_admin_cannot(self, world):
        h, p0, p1 = world
        a = h.mint(USER_A)
        b = h.mint(USER_B, is_admin=True)
        with h.client() as c:
            args = {"name": "list_podcasts", "arguments": {"all": True}}
            assert {r["id"] for r in _payload(Harness.rpc(c, b, "tools/call", args))["podcasts"]} == {p0, p1}
            assert [r["id"] for r in _payload(Harness.rpc(c, a, "tools/call", args))["podcasts"]] == [p0]


class TestRemovePodcast:
    def test_remote_remove_unfollows_and_never_deletes(self, world):
        h, p0, p1 = world
        a = h.mint(USER_A, scopes=("follows",))
        with h.client() as c:
            out = _payload(Harness.rpc(c, a, "tools/call", {"name": "remove_podcast", "arguments": {"podcast_id": p0}}))
        assert out["success"] is True and "Unfollowed" in out["message"]
        assert h.repos.follower.exists(USER_A, p0) is False
        assert h.repos.podcast.get_by_id(p0) is not None  # still there

    def test_remote_remove_refused_for_non_follower(self, world):
        h, p0, p1 = world
        a = h.mint(USER_A, scopes=("follows",))
        with h.client() as c:
            out = _payload(Harness.rpc(c, a, "tools/call", {"name": "remove_podcast", "arguments": {"podcast_id": p1}}))
        assert out["success"] is False and "do not follow" in out["error"]
        assert h.repos.follower.exists(USER_B, p1) is True


class TestScopes:
    def test_read_only_token_lists_no_mutating_tools_and_is_refused_naming_scope(self, world):
        h, p0, _ = world
        a = h.mint(USER_A)  # read only
        with h.client() as c:
            names = {t["name"] for t in Harness.rpc(c, a, "tools/list").json()["result"]["tools"]}
            assert "list_podcasts" in names
            assert "add_podcast" not in names and "remove_podcast" not in names
            assert not (PIPELINE_TOOLS & names)
            out = _payload(
                Harness.rpc(c, a, "tools/call", {"name": "add_podcast", "arguments": {"url": "https://x/feed.xml"}})
            )
        assert out["success"] is False and "'follows'" in out["error"]

    def test_pipeline_dropped_for_non_admin_honoured_for_admin(self, world):
        h, _, _ = world
        a = h.mint(USER_A, scopes=("pipeline",), is_admin=False)  # service drops it at mint
        b = h.mint(USER_B, scopes=("pipeline",), is_admin=True)
        with h.client() as c:
            names_a = {t["name"] for t in Harness.rpc(c, a, "tools/list").json()["result"]["tools"]}
            names_b = {t["name"] for t in Harness.rpc(c, b, "tools/list").json()["result"]["tools"]}
        assert not (PIPELINE_TOOLS & names_a)
        assert PIPELINE_TOOLS <= names_b

    def test_demoted_admin_loses_pipeline_on_next_call_without_rotate(self, world):
        h, _, _ = world
        b = h.mint(USER_B, scopes=("pipeline",), is_admin=True)
        with h.client() as c:
            assert PIPELINE_TOOLS <= {t["name"] for t in Harness.rpc(c, b, "tools/list").json()["result"]["tools"]}
            with sqlite3.connect(str(h.config.database_path)) as conn:
                conn.execute("UPDATE users SET is_admin = 0 WHERE id = ?", (USER_B,))
            names = {t["name"] for t in Harness.rpc(c, b, "tools/list", id_=2).json()["result"]["tools"]}
            assert not (PIPELINE_TOOLS & names)
            out = _payload(Harness.rpc(c, b, "tools/call", {"name": "refresh_feeds", "arguments": {}}, id_=3))
            assert out["success"] is False and "'pipeline'" in out["error"]


class TestGetStatus:
    def test_reduced_for_non_admin_full_for_admin(self, world):
        h, _, _ = world
        a = h.mint(USER_A)
        b = h.mint(USER_B, is_admin=True)
        with h.client() as c:
            mine = _payload(Harness.rpc(c, a, "tools/call", {"name": "get_status", "arguments": {}}))
            full = _payload(Harness.rpc(c, b, "tools/call", {"name": "get_status", "arguments": {}}))
        assert mine["podcasts_followed"] == 1
        assert "storage_path" not in mine and "podcasts_tracked" not in mine
        assert full["podcasts_tracked"] == 2 and "storage_path" in full


class TestIdentifiers:
    def test_numeric_podcast_id_refused_over_http_uuid_and_slug_accepted(self, world):
        h, p0, _ = world
        a = h.mint(USER_A)
        with h.client() as c:
            num = _payload(Harness.rpc(c, a, "tools/call", {"name": "list_episodes", "arguments": {"podcast_id": "1"}}))
            assert num["success"] is False and "uuid, slug or RSS URL" in num["error"]
            by_uuid = _payload(
                Harness.rpc(c, a, "tools/call", {"name": "list_episodes", "arguments": {"podcast_id": p0}}, id_=2)
            )
            by_slug = _payload(
                Harness.rpc(
                    c, a, "tools/call", {"name": "list_episodes", "arguments": {"podcast_id": "podcast-0"}}, id_=3
                )
            )
        assert "error" not in by_uuid and "error" not in by_slug

    def test_resource_read_numeric_refused_uuid_ok_for_non_follower(self, world):
        """Resources follow the same identifier rule, and reads are
        corpus-wide for any authenticated caller (web parity)."""
        h, p0, p1 = world
        a = h.mint(USER_A)  # A does not follow p1
        with h.client() as c:
            ok = Harness.rpc(c, a, "resources/read", {"uri": f"thestill://podcasts/{p1}"})
            assert ok.status_code == 200 and "error" not in ok.json(), ok.content
            assert json.loads(ok.json()["result"]["contents"][0]["text"])["title"] == "Podcast 1"
            bad = Harness.rpc(c, a, "resources/read", {"uri": "thestill://podcasts/1"}, id_=2)
            assert bad.status_code == 200 and "uuid, slug or RSS URL" in json.dumps(bad.json())


class TestStdioBranch:
    """Same handlers, no request in context: legacy semantics everywhere."""

    @pytest.fixture
    def stdio(self, isolated_env, tmp_path):  # noqa: F811
        h = Harness(tmp_path)  # builds the DB + users the same way
        p0, p1 = _seed_podcasts(h.config.database_path)
        server = Server("thestill-mcp-test")
        setup_tools(server, str(tmp_path))
        return server, p0, p1

    @pytest.mark.anyio
    async def test_all_tools_listed_and_list_podcasts_has_index(self, stdio):
        server, p0, p1 = stdio
        async with create_connected_server_and_client_session(server) as session:
            tools = await session.list_tools()
            assert PIPELINE_TOOLS <= {t.name for t in tools.tools}
            listed = await session.call_tool("list_podcasts", {})
            rows = json.loads(listed.content[0].text)["podcasts"]
            assert [r["index"] for r in rows] == [1, 2]
            assert {r["id"] for r in rows} == {p0, p1}

    @pytest.mark.anyio
    async def test_numeric_ids_accepted_and_remove_deletes(self, stdio):
        server, p0, p1 = stdio
        async with create_connected_server_and_client_session(server) as session:
            episodes = await session.call_tool("list_episodes", {"podcast_id": "1"})
            assert "not accepted" not in episodes.content[0].text
            removed = json.loads((await session.call_tool("remove_podcast", {"podcast_id": "1"})).content[0].text)
            assert removed["success"] is True and "removed" in removed["message"]
            remaining = json.loads((await session.call_tool("list_podcasts", {})).content[0].text)["podcasts"]
            assert len(remaining) == 1
