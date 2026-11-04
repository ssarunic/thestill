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

"""Spec #112 — ``list_inbox``, ``import_episode`` and ``send_to_inbox``.

The dispatcher is unit-tested with fakes; the scope gate, real
``ImportService`` / ``InboxService`` wiring and idempotency end to end
through the mounted Streamable HTTP transport, using a bare audio URL
(that resolver needs no network). stdio through the in-memory session.
"""

from __future__ import annotations

import json
from datetime import datetime, timezone
from types import SimpleNamespace

import pytest
from mcp.client import Client
from mcp.server import Server

from tests.unit.web.test_mcp_http import USER_A, USER_B, Harness, isolated_env  # noqa: F401
from thestill.mcp.inbox_tools import dispatch_inbox_tool
from thestill.mcp.tools import setup_tools
from thestill.services.import_service import ResolverError
from thestill.services.inbox_service import InvalidInboxStateError
from thestill.utils.datetime_utils import now_utc

AUDIO_URL = "https://cdn.example.com/shows/ep-1.mp3"
BASE = "https://thestill.example"


@pytest.fixture
def anyio_backend():
    return "asyncio"


def _text(response):
    return json.loads(response[0].text)


# ---------------------------------------------------------------------------
# Dispatcher (fakes)
# ---------------------------------------------------------------------------


class _Import:
    def __init__(self, *, outcome="new_episode", failed=False, raises=None):
        self.outcome, self.failed, self.raises, self.calls = outcome, failed, raises, []

    def import_url(self, *, user_id, url):
        self.calls.append((user_id, url))
        if self.raises:
            raise self.raises
        return SimpleNamespace(
            episode_id="ep-1",
            title="Resolver title",
            outcome=self.outcome,
            episode_failed=self.failed,
            episode_state="discovered",
            podcast_slug="show",
            episode_slug="ep",
            parent_title=None,
        )


class _Inbox:
    def __init__(self, *, exists=True, created=True):
        self.exists, self.created, self.delivered = exists, created, []

    def episode_exists(self, episode_id):
        return self.exists

    def deliver_to_user(self, user_id, episode_id, *, source):
        self.delivered.append((user_id, episode_id, source))
        return object(), self.created


_PODCAST = SimpleNamespace(id="p-1", title="The Show", slug="show")
_EPISODE = SimpleNamespace(
    id="ep-1", title="Stored title", slug="ep", audio_url="https://cdn/x.mp3", state="discovered", is_failed=False
)


class _InboxRepo:
    """``get_item`` is how the tools describe an episode (never a full Podcast)."""

    def get_item(self, user_id, episode_id):
        return SimpleNamespace(podcast=_PODCAST, episode=_EPISODE) if episode_id == "ep-1" else None


class _Repo:
    def __init__(self):
        self.slug_lookups = []

    def get_episode_by_slug(self, podcast_slug, episode_slug):
        self.slug_lookups.append((podcast_slug, episode_slug))
        return (_PODCAST, _EPISODE) if (podcast_slug, episode_slug) == ("show", "ep") else None


def _dispatch(name, arguments, *, imp=None, inbox=None, repo=None):
    return dispatch_inbox_tool(
        name,
        arguments,
        user_id="u1",
        import_service=imp or _Import(),
        inbox_service=inbox or _Inbox(),
        inbox_repository=_InboxRepo(),
        podcast_repository=repo or _Repo(),
        base_url=BASE,
    )


class TestImportEpisodeDispatch:
    @pytest.mark.parametrize(
        "outcome, phrase",
        [
            ("new_episode", "Imported “Stored title” into your inbox"),
            ("added_existing", "Added “Stored title” to your inbox"),
            ("already_in_inbox", "“Stored title” is already in your inbox"),
        ],
    )
    def test_each_outcome_has_its_message_and_links(self, outcome, phrase):
        out = _text(_dispatch("import_episode", {"url": "https://x"}, imp=_Import(outcome=outcome)))
        assert out["success"] is True and out["outcome"] == outcome
        assert phrase in out["message"]
        assert out["episode"]["podcast_title"] == "The Show"
        assert out["episode"]["episode_url"] == f"{BASE}/podcasts/show/episodes/ep"
        assert out["episode"]["audio_url"] == "https://cdn/x.mp3"

    def test_failed_episode_says_so(self):
        out = _text(_dispatch("import_episode", {"url": "https://x"}, imp=_Import(failed=True)))
        assert out["episode"]["failed"] is True and "failed" in out["message"]

    def test_import_error_text_is_relayed(self):
        imp = _Import(raises=ResolverError("Apple Podcasts link does not point to a single episode"))
        out = _text(_dispatch("import_episode", {"url": "https://x"}, imp=imp))
        assert out == {"success": False, "error": "Apple Podcasts link does not point to a single episode"}

    def test_missing_url(self):
        imp = _Import()
        out = _text(_dispatch("import_episode", {"url": "  "}, imp=imp))
        assert out["success"] is False and "url" in out["error"]
        assert imp.calls == []

    def test_other_names_are_not_ours(self):
        assert _dispatch("search_corpus", {}) is None


class TestSendToInboxDispatch:
    @pytest.mark.parametrize("args", [{}, {"episode_id": "ep-1", "episode_url": f"{BASE}/podcasts/show/episodes/ep"}])
    def test_exactly_one_identifier(self, args):
        out = _text(_dispatch("send_to_inbox", args))
        assert out["success"] is False and "exactly one" in out["error"]

    def test_by_id_delivers_ad_hoc(self):
        inbox = _Inbox()
        out = _text(_dispatch("send_to_inbox", {"episode_id": "ep-1"}, inbox=inbox))
        assert out["success"] is True and out["created"] is True
        assert inbox.delivered == [("u1", "ep-1", "ad_hoc")]
        assert out["episode"]["episode_url"] == f"{BASE}/podcasts/show/episodes/ep"

    def test_by_url_any_host_and_subpath(self):
        repo, inbox = _Repo(), _Inbox(created=False)
        url = "https://other.host/podcasts/show/episodes/ep/summary?x=1#t=30"
        out = _text(_dispatch("send_to_inbox", {"episode_url": url}, inbox=inbox, repo=repo))
        assert out["created"] is False and "already in your inbox" in out["message"]
        assert repo.slug_lookups == [("show", "ep")]

    def test_url_that_is_not_an_episode_page(self):
        out = _text(_dispatch("send_to_inbox", {"episode_url": f"{BASE}/podcasts/show"}))
        assert out["success"] is False and "Thestill episode page" in out["error"]

    def test_unknown_id_is_not_found_and_never_delivers(self):
        inbox = _Inbox(exists=False)
        out = _text(_dispatch("send_to_inbox", {"episode_id": "nope"}, inbox=inbox))
        assert out == {"success": False, "error": "Episode not found: nope"}
        assert inbox.delivered == []


_DELIVERED = datetime(2026, 11, 4, 9, 30, tzinfo=timezone.utc)


def _item(n, *, state="unread", summary=True):
    episode = SimpleNamespace(
        id=f"ep-{n}",
        title=f"Episode {n}",
        slug=f"ep-{n}",
        audio_url=None,
        state="summarized",
        is_failed=False,
        pub_date=datetime(2026, 10, n, tzinfo=timezone.utc),
        duration=3600,
        summary_path="s.md" if summary else None,
    )
    entry = SimpleNamespace(state=state, source="follow_new", delivered_at=_DELIVERED.replace(minute=n))
    return SimpleNamespace(entry=entry, episode=episode, podcast=_PODCAST)


class _ListInbox(_Inbox):
    def __init__(self, items=(), *, raises=None):
        super().__init__()
        self.items, self.raises, self.calls = list(items), raises, []

    def list(self, user_id, *, state=None, limit=50, before=None, q=None):
        self.calls.append({"user_id": user_id, "state": state, "limit": limit, "before": before, "q": q})
        if self.raises:
            raise self.raises
        return self.items[:limit]

    def unread_count(self, user_id):
        return 7


class TestListInboxDispatch:
    def test_rows_carry_inbox_fields_links_and_unread_count(self):
        inbox = _ListInbox([_item(3), _item(2, state="saved", summary=False)])
        out = _text(_dispatch("list_inbox", {}, inbox=inbox))
        assert out["success"] is True and out["unread_count"] == 7 and out["count"] == 2
        first, second = out["episodes"]
        assert first["id"] == "ep-3" and first["podcast_title"] == "The Show"
        assert first["inbox_state"] == "unread" and first["source"] == "follow_new"
        assert first["delivered_at"] == "2026-11-04T09:03:00+00:00"
        assert first["published_at"] == "2026-10-03T00:00:00+00:00"
        assert first["summary_ready"] is True and first["duration_seconds"] == 3600
        assert first["episode_url"] == f"{BASE}/podcasts/show/episodes/ep-3"
        assert second["inbox_state"] == "saved" and second["summary_ready"] is False
        assert out["next_before"] is None
        assert inbox.calls == [{"user_id": "u1", "state": None, "limit": 10, "before": None, "q": None}]

    def test_full_page_returns_a_cursor_that_round_trips(self):
        inbox = _ListInbox([_item(5), _item(4)])
        out = _text(_dispatch("list_inbox", {"limit": 2}, inbox=inbox))
        assert out["next_before"] == "2026-11-04T09:04:00+00:00"
        _dispatch("list_inbox", {"before": "2026-11-04T09:04:00Z", "state": "unread", "q": " ai "}, inbox=inbox)
        call = inbox.calls[-1]
        assert call["before"] == datetime(2026, 11, 4, 9, 4, tzinfo=timezone.utc)
        assert call["state"] == "unread" and call["q"] == "ai"

    @pytest.mark.parametrize("limit", [0, 51, "lots"])
    def test_bad_limit(self, limit):
        inbox = _ListInbox()
        out = _text(_dispatch("list_inbox", {"limit": limit}, inbox=inbox))
        assert out["success"] is False and "limit" in out["error"]
        assert inbox.calls == []

    def test_bad_cursor(self):
        out = _text(_dispatch("list_inbox", {"before": "yesterday"}, inbox=_ListInbox()))
        assert out["success"] is False and "next_before" in out["error"]

    def test_service_validation_text_is_relayed(self):
        inbox = _ListInbox(raises=InvalidInboxStateError("Invalid state filter: 'new'"))
        out = _text(_dispatch("list_inbox", {"state": "new"}, inbox=inbox))
        assert out == {"success": False, "error": "Invalid state filter: 'new'"}


# ---------------------------------------------------------------------------
# End to end over the remote connector
# ---------------------------------------------------------------------------


def _payload(response):
    assert response.status_code == 200, response.content
    body = response.json()
    assert "error" not in body, body
    return json.loads(body["result"]["content"][0]["text"])


def _call(c, token, name, arguments):
    return Harness.rpc(c, token, "tools/call", {"name": name, "arguments": arguments})


@pytest.fixture
def harness(isolated_env, tmp_path):  # noqa: F811
    return Harness(tmp_path)


class TestRemote:
    def test_read_token_lists_its_own_inbox_only(self, harness):
        # One token per user (minting again rotates): A imports, B only reads.
        a_writer, b_reader = harness.mint(USER_A, scopes=("follows",)), harness.mint(USER_B)
        with harness.client() as c:
            names = {t["name"] for t in Harness.rpc(c, b_reader, "tools/list").json()["result"]["tools"]}
            assert "list_inbox" in names and "import_episode" not in names
            episode_id = _payload(_call(c, a_writer, "import_episode", {"url": AUDIO_URL}))["episode"]["id"]
            mine = _payload(_call(c, a_writer, "list_inbox", {}))
            theirs = _payload(_call(c, b_reader, "list_inbox", {}))
        assert mine["success"] is True and mine["unread_count"] == 1
        assert [(e["id"], e["source"], e["inbox_state"]) for e in mine["episodes"]] == [
            (episode_id, "import", "unread")
        ]
        assert theirs["episodes"] == [] and theirs["unread_count"] == 0
        # Listing never reads a row (the inbox only changes on the reader's say).
        assert harness.repos.inbox.get(USER_A, episode_id).state == "unread"

    def test_list_inbox_still_works_in_read_only_mode(self, harness):
        from thestill.repositories.sqlite_settings_repository import SqliteSettingsRepository

        token = harness.mint(USER_A)
        SqliteSettingsRepository(harness.config.database_path).upsert(
            "read_only", True, updated_by=None, updated_at=now_utc()
        )
        with harness.client() as c:
            out = _payload(_call(c, token, "list_inbox", {}))
        assert out["success"] is True and out["episodes"] == []

    def test_read_token_neither_lists_nor_calls(self, harness):
        token = harness.mint(USER_A)
        with harness.client() as c:
            names = {t["name"] for t in Harness.rpc(c, token, "tools/list").json()["result"]["tools"]}
            assert {"import_episode", "send_to_inbox"}.isdisjoint(names)
            out = _payload(_call(c, token, "import_episode", {"url": AUDIO_URL}))
        assert out["success"] is False and "'follows'" in out["error"]

    def test_import_is_idempotent_one_row_one_pipeline(self, harness):
        token = harness.mint(USER_A, scopes=("follows",))
        with harness.client() as c:
            names = {t["name"] for t in Harness.rpc(c, token, "tools/list").json()["result"]["tools"]}
            assert {"import_episode", "send_to_inbox"} <= names
            first = _payload(_call(c, token, "import_episode", {"url": AUDIO_URL}))
            second = _payload(_call(c, token, "import_episode", {"url": AUDIO_URL}))
        assert first["success"] is True and first["outcome"] == "new_episode"
        assert second["outcome"] == "already_in_inbox"
        episode_id = first["episode"]["id"]
        assert second["episode"]["id"] == episode_id
        assert harness.repos.inbox.get(USER_A, episode_id) is not None
        assert len(harness.repos.queue_manager.get_tasks_for_episode(episode_id)) == 1

    def test_send_to_inbox_creates_once_for_another_user(self, harness):
        a = harness.mint(USER_A, scopes=("follows",))
        b = harness.mint(USER_B, scopes=("follows",), is_admin=True)
        with harness.client() as c:
            episode_id = _payload(_call(c, a, "import_episode", {"url": AUDIO_URL}))["episode"]["id"]
            first = _payload(_call(c, b, "send_to_inbox", {"episode_id": episode_id}))
            again = _payload(_call(c, b, "send_to_inbox", {"episode_id": episode_id}))
        assert first["success"] is True and first["created"] is True
        assert again["success"] is True and again["created"] is False
        assert harness.repos.inbox.get(USER_B, episode_id).source == "ad_hoc"
        assert harness.repos.inbox.get(USER_A, episode_id).source == "import"

    def test_unknown_episode_id_is_not_found(self, harness):
        token = harness.mint(USER_A, scopes=("follows",))
        with harness.client() as c:
            out = _payload(_call(c, token, "send_to_inbox", {"episode_id": "not-a-uuid"}))
        assert out == {"success": False, "error": "Episode not found: not-a-uuid"}

    def test_read_only_mode_refuses_non_admin_import(self, harness):
        from thestill.repositories.sqlite_settings_repository import SqliteSettingsRepository

        token = harness.mint(USER_A, scopes=("follows",))
        SqliteSettingsRepository(harness.config.database_path).upsert(
            "read_only", True, updated_by=None, updated_at=now_utc()
        )
        with harness.client() as c:
            out = _payload(_call(c, token, "import_episode", {"url": AUDIO_URL}))
        assert out["success"] is False and "read-only" in out["error"]


# ---------------------------------------------------------------------------
# stdio
# ---------------------------------------------------------------------------


class TestStdio:
    def _server(self, tmp_path):
        Harness(tmp_path)  # builds the DB the same way
        server = Server("thestill-mcp-test")
        setup_tools(server, str(tmp_path))
        return server

    @pytest.mark.anyio
    async def test_single_user_imports_into_the_default_user(self, isolated_env, tmp_path):  # noqa: F811
        server = self._server(tmp_path)
        async with Client(server) as session:
            out = json.loads((await session.call_tool("import_episode", {"url": AUDIO_URL})).content[0].text)
        assert out["success"] is True and out["outcome"] == "new_episode"

    @pytest.mark.anyio
    async def test_single_user_lists_the_default_users_inbox(self, isolated_env, tmp_path):  # noqa: F811
        server = self._server(tmp_path)
        async with Client(server) as session:
            await session.call_tool("import_episode", {"url": AUDIO_URL})
            out = json.loads((await session.call_tool("list_inbox", {})).content[0].text)
        assert out["success"] is True and out["count"] == 1 and out["episodes"][0]["source"] == "import"

    @pytest.mark.anyio
    async def test_multi_user_stdio_refuses(self, isolated_env, tmp_path):  # noqa: F811
        isolated_env.setenv("MULTI_USER", "true")
        isolated_env.setenv("PUBLIC_BASE_URL", BASE)
        server = self._server(tmp_path)
        async with Client(server) as session:
            out = json.loads((await session.call_tool("import_episode", {"url": AUDIO_URL})).content[0].text)
        assert out["success"] is False and "remote connector" in out["error"]
