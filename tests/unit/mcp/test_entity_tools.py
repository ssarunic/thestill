"""Spec #28 §1.8 — MCP entity-tool dispatcher tests.

Round-trips the JSON wire shape: feed a dict of arguments, get back
``TextContent`` whose payload is parseable JSON with ``success`` +
the documented per-tool fields. Real DB (built by the
``populated_db`` fixture) so the SQL queries get exercised end to end.
"""

from __future__ import annotations

import json
import sqlite3
import uuid
from pathlib import Path

import pytest

from thestill.mcp.entity_tools import dispatch_entity_tool, entity_tool_definitions
from thestill.models.entities import EntityMention, EntityRecord, EntityType, MentionRole
from thestill.repositories.sqlite_entity_repository import SqliteEntityRepository
from thestill.repositories.sqlite_podcast_repository import SqlitePodcastRepository


@pytest.fixture
def populated_db(tmp_path):
    db_path = tmp_path / "thestill.db"
    SqlitePodcastRepository(db_path=str(db_path))
    podcast_id = str(uuid.uuid4())
    ep1 = str(uuid.uuid4())
    ep2 = str(uuid.uuid4())
    conn = sqlite3.connect(str(db_path))
    try:
        conn.execute(
            "INSERT INTO podcasts (id, rss_url, title, slug) VALUES (?, ?, ?, ?)",
            (podcast_id, "https://example.com/feed.xml", "Prof G Markets", "prof-g-markets"),
        )
        conn.execute(
            "INSERT INTO episodes (id, podcast_id, external_id, title, audio_url, pub_date) "
            "VALUES (?, ?, ?, ?, ?, ?)",
            (ep1, podcast_id, "e1", "SpaceX IPO", "https://example.com/ep1.mp3", "2026-04-28T00:00:00"),
        )
        conn.execute(
            "INSERT INTO episodes (id, podcast_id, external_id, title, audio_url, pub_date) "
            "VALUES (?, ?, ?, ?, ?, ?)",
            (ep2, podcast_id, "e2", "AI Job Crisis", "https://example.com/ep2.mp3", "2026-04-24T00:00:00"),
        )
        conn.commit()
    finally:
        conn.close()

    repo = SqliteEntityRepository(db_path=str(db_path))
    repo.upsert_entity(
        EntityRecord(
            id="person:elon-musk",
            type=EntityType.PERSON,
            canonical_name="Elon Musk",
            wikidata_qid="Q317521",
            aliases=["Musk"],
        )
    )
    repo.upsert_entity(
        EntityRecord(
            id="company:spacex",
            type=EntityType.COMPANY,
            canonical_name="SpaceX",
            wikidata_qid="Q193701",
        )
    )

    def _mention(episode, surface, segment_id, speaker="Scott Galloway"):
        return EntityMention(
            episode_id=episode,
            segment_id=segment_id,
            start_ms=segment_id * 10_000,
            end_ms=segment_id * 10_000 + 8_000,
            speaker=speaker,
            role=MentionRole.MENTIONED,
            surface_form=surface,
            quote_excerpt=f"… {surface} …",
            confidence=0.95,
            extractor="gliner:test",
        )

    repo.insert_mentions(
        [
            _mention(ep1, "Elon Musk", 1),
            _mention(ep1, "SpaceX", 2),
            _mention(ep2, "Elon Musk", 1),
        ]
    )
    surface_to_entity = {"Elon Musk": "person:elon-musk", "SpaceX": "company:spacex"}
    for m in repo.list_pending_mentions():
        repo.resolve_mention(mention_id=m.id, entity_id=surface_to_entity[m.surface_form], status="resolved")
    repo.rebuild_cooccurrences(episode_ids=None)
    return repo, podcast_id, ep1, ep2


def _payload(text_contents) -> dict:
    """Parse the single TextContent JSON envelope tools return."""
    assert len(text_contents) == 1
    return json.loads(text_contents[0].text)


class TestToolDefinitions:
    def test_all_five_tools_present(self):
        defs = entity_tool_definitions()
        names = {t.name for t in defs}
        assert names == {
            "find_mentions",
            "list_quotes_by",
            "get_episode_clip",
            "get_entity",
            "list_episodes_by_entity",
        }

    def test_each_tool_has_input_schema(self):
        for tool in entity_tool_definitions():
            assert tool.inputSchema["type"] == "object"
            assert "required" in tool.inputSchema


class TestFindMentionsTool:
    def test_resolves_by_canonical_name(self, populated_db):
        repo, _, _, _ = populated_db
        result = _payload(dispatch_entity_tool("find_mentions", {"entity": "Elon Musk"}, repo))
        assert result["success"] is True
        assert result["matched_entity"]["id"] == "person:elon-musk"
        assert len(result["results"]) == 2  # Two episodes mention Musk
        for row in result["results"]:
            assert row["match_type"] == "entity"
            assert row["deeplink"].startswith("thestill://episode/")
            assert row["score"] == 1.0
            assert row["speaker"] == "Scott Galloway"

    def test_resolves_by_alias(self, populated_db):
        repo, _, _, _ = populated_db
        result = _payload(dispatch_entity_tool("find_mentions", {"entity": "Musk"}, repo))
        assert result["matched_entity"]["id"] == "person:elon-musk"

    def test_unknown_entity_returns_empty(self, populated_db):
        repo, _, _, _ = populated_db
        result = _payload(dispatch_entity_tool("find_mentions", {"entity": "Nobody"}, repo))
        assert result["success"] is True
        assert result["results"] == []
        assert result["matched_entity"] is None

    def test_missing_entity_arg_errors(self, populated_db):
        repo, _, _, _ = populated_db
        result = _payload(dispatch_entity_tool("find_mentions", {}, repo))
        assert result["success"] is False
        assert "required" in result["error"].lower()


class TestListQuotesByTool:
    def test_speaker_substring(self, populated_db):
        repo, _, _, _ = populated_db
        result = _payload(dispatch_entity_tool("list_quotes_by", {"speaker": "Galloway"}, repo))
        assert result["success"] is True
        assert all(r["speaker"] == "Scott Galloway" for r in result["results"])
        assert len(result["results"]) == 3  # 2 in ep1, 1 in ep2

    def test_topic_filter(self, populated_db):
        repo, _, _, _ = populated_db
        # Galloway said something on the episode that mentions SpaceX,
        # so the topic filter intersects to ep1's two mentions.
        result = _payload(dispatch_entity_tool("list_quotes_by", {"speaker": "Galloway", "topic": "SpaceX"}, repo))
        assert len(result["results"]) == 2

    def test_missing_speaker_errors(self, populated_db):
        repo, _, _, _ = populated_db
        result = _payload(dispatch_entity_tool("list_quotes_by", {}, repo))
        assert result["success"] is False


class TestGetEpisodeClipTool:
    def test_returns_citation_row(self, populated_db):
        repo, _, ep1, _ = populated_db
        result = _payload(dispatch_entity_tool("get_episode_clip", {"episode_id": ep1, "start_ms": 12000}, repo))
        assert result["success"] is True
        assert result["result"]["match_type"] == "entity"
        assert result["result"]["start_ms"] == 10000  # the straddling mention's start
        assert result["result"]["end_ms"] == 18000

    def test_plus_minus_widens_end_ms(self, populated_db):
        repo, _, ep1, _ = populated_db
        result = _payload(
            dispatch_entity_tool(
                "get_episode_clip",
                {"episode_id": ep1, "start_ms": 12000, "plus_minus_sec": 60},
                repo,
            )
        )
        # 12000 + 60_000 = 72_000 > segment end 18_000 → widened
        assert result["result"]["end_ms"] == 72000

    def test_missing_args_errors(self, populated_db):
        repo, _, _, _ = populated_db
        result = _payload(dispatch_entity_tool("get_episode_clip", {}, repo))
        assert result["success"] is False


class TestGetEntityTool:
    def test_summary_shape(self, populated_db):
        repo, _, _, _ = populated_db
        result = _payload(dispatch_entity_tool("get_entity", {"id_or_name": "Elon Musk"}, repo))
        assert result["success"] is True
        out = result["result"]
        assert out["entity"]["id"] == "person:elon-musk"
        assert out["mention_count"] == 2
        cooccur_ids = {c["entity"]["id"] for c in out["cooccurring"]}
        assert "company:spacex" in cooccur_ids
        assert len(out["recent_mentions"]) == 2

    def test_unknown_returns_null(self, populated_db):
        repo, _, _, _ = populated_db
        result = _payload(dispatch_entity_tool("get_entity", {"id_or_name": "Nobody"}, repo))
        assert result["success"] is True
        assert result["result"] is None


class TestListEpisodesByEntityTool:
    def test_intersection(self, populated_db):
        repo, _, ep1, _ = populated_db
        # Both Musk and SpaceX appear in ep1 only
        result = _payload(
            dispatch_entity_tool("list_episodes_by_entity", {"has_entity": ["Elon Musk", "SpaceX"]}, repo)
        )
        assert result["success"] is True
        assert len(result["results"]) == 1
        assert result["results"][0]["episode_id"] == ep1

    def test_unresolved_name_short_circuits(self, populated_db):
        repo, _, _, _ = populated_db
        result = _payload(
            dispatch_entity_tool("list_episodes_by_entity", {"has_entity": ["Elon Musk", "NotAnEntity"]}, repo)
        )
        assert result["success"] is True
        assert result["unresolved_names"] == ["NotAnEntity"]
        assert result["results"] == []

    def test_empty_has_entity_errors(self, populated_db):
        repo, _, _, _ = populated_db
        result = _payload(dispatch_entity_tool("list_episodes_by_entity", {"has_entity": []}, repo))
        assert result["success"] is False


class TestNonEntityToolReturnsNone:
    def test_unknown_tool_returns_none(self, populated_db):
        repo, _, _, _ = populated_db
        # Dispatcher signals "not my tool" by returning None so the
        # caller can fall through to the rest of the chain.
        assert dispatch_entity_tool("add_podcast", {"url": "x"}, repo) is None
