"""Spec #28 §1.9 — CLI peers for the entity layer.

Click ``CliRunner`` smoke tests against a tmp-DB-backed CLIContext.
The commands themselves delegate to the same ``SqliteEntityRepository``
methods exercised by the MCP-tool tests; this file only asserts the
CLI plumbing (argument parsing, error messages, output formatting,
``--json`` switch).
"""

from __future__ import annotations

import json
import sqlite3
import uuid
from pathlib import Path

import pytest
from click.testing import CliRunner

from thestill.cli import main
from thestill.models.entities import EntityMention, EntityRecord, EntityType, MentionRole
from thestill.repositories.sqlite_entity_repository import SqliteEntityRepository
from thestill.repositories.sqlite_podcast_repository import SqlitePodcastRepository


@pytest.fixture
def tmp_db_with_entities(tmp_path, monkeypatch):
    """Stand up a tmp DB and point the CLI at it via env vars.

    The CLI's ``main`` entrypoint runs ``load_dotenv()`` which would
    overwrite our test env vars from the repo's real ``.env``. We
    no-op it so the test's ``THESTILL_STORAGE_PATH`` survives.
    """
    storage = tmp_path / "data"
    storage.mkdir()
    # Config reads STORAGE_PATH (not THESTILL_STORAGE_PATH); set it
    # before load_dotenv runs. Also point THESTILL_ENV_FILE at a
    # nonexistent path so the CLI's dotenv load is a no-op and our
    # test env vars win.
    monkeypatch.setenv("STORAGE_PATH", str(storage))
    monkeypatch.setenv("THESTILL_ENV_FILE", str(tmp_path / ".no-such-env"))

    db_path = storage / "podcasts.db"
    SqlitePodcastRepository(db_path=str(db_path))
    podcast_id = str(uuid.uuid4())
    ep1 = str(uuid.uuid4())
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
    repo.upsert_entity(
        EntityRecord(
            id="company:tesla",
            type=EntityType.COMPANY,
            canonical_name="Tesla",
            wikidata_qid="Q478214",
        )
    )

    def _mention(surface, segment_id, speaker="Scott Galloway"):
        return EntityMention(
            episode_id=ep1,
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

    repo.insert_mentions([_mention("Elon Musk", 1), _mention("SpaceX", 2)])
    surface_to_entity = {"Elon Musk": "person:elon-musk", "SpaceX": "company:spacex"}
    for m in repo.list_pending_mentions():
        repo.resolve_mention(mention_id=m.id, entity_id=surface_to_entity[m.surface_form], status="resolved")
    repo.rebuild_cooccurrences(episode_ids=None)
    return storage


def _run(args):
    runner = CliRunner()
    return runner.invoke(main, args, catch_exceptions=False)


class TestFindMentionsCli:
    def test_canonical_name_lookup(self, tmp_db_with_entities):
        result = _run(["find-mentions", "Elon Musk"])
        assert result.exit_code == 0
        assert "person:elon-musk" in result.output
        assert "Elon Musk" in result.output

    def test_alias_lookup(self, tmp_db_with_entities):
        result = _run(["find-mentions", "Musk"])
        assert result.exit_code == 0
        assert "person:elon-musk" in result.output

    def test_unknown_entity_exits_nonzero(self, tmp_db_with_entities):
        result = _run(["find-mentions", "Nobody"])
        assert result.exit_code == 1
        assert "No entity matched" in result.output

    def test_json_output_one_line_per_row(self, tmp_db_with_entities):
        result = _run(["find-mentions", "Musk", "--json"])
        assert result.exit_code == 0
        # Pluck out only the lines that parse as a self-contained JSON
        # object with our expected key set — log lines also start with
        # ``{`` so we filter on shape.
        rows = []
        for line in result.output.splitlines():
            if not line.startswith("{"):
                continue
            try:
                payload = json.loads(line)
            except json.JSONDecodeError:
                continue
            if "match_type" in payload:
                rows.append(payload)
        assert rows, "expected at least one citation-row JSON line"
        for row in rows:
            assert row["match_type"] == "entity"
            assert row["deeplink"].startswith("thestill://")


class TestQuotesByCli:
    def test_speaker_substring(self, tmp_db_with_entities):
        result = _run(["quotes-by", "Galloway"])
        assert result.exit_code == 0
        assert "2 mention(s)" in result.output
        assert "Scott Galloway" in result.output

    def test_topic_filter_resolves_then_intersects(self, tmp_db_with_entities):
        result = _run(["quotes-by", "Galloway", "--topic", "SpaceX"])
        assert result.exit_code == 0
        assert "company:spacex" in result.output

    def test_unknown_topic_exits_nonzero(self, tmp_db_with_entities):
        result = _run(["quotes-by", "Galloway", "--topic", "Nobody"])
        assert result.exit_code == 1
        assert "No topic matched" in result.output


class TestEntityGetCli:
    def test_summary_shape(self, tmp_db_with_entities):
        result = _run(["entity", "get", "Elon Musk"])
        assert result.exit_code == 0
        assert "person:elon-musk" in result.output
        assert "Q317521" in result.output
        assert "mention_count:" in result.output

    def test_unknown_exits_nonzero(self, tmp_db_with_entities):
        result = _run(["entity", "get", "Nobody"])
        assert result.exit_code == 1

    def test_json_output(self, tmp_db_with_entities):
        result = _run(["entity", "get", "Elon Musk", "--json"])
        assert result.exit_code == 0
        # The CLI prints the multi-line JSON body via ``json.dumps(..., indent=2)``.
        # Walk forward from the first ``{`` and use ``raw_decode`` to peel off
        # one object — log noise that follows on subsequent lines is ignored.
        idx = result.output.find('{\n  "entity":')
        assert idx >= 0, f"could not locate entity JSON in output: {result.output[:300]}"
        decoder = json.JSONDecoder()
        payload, _ = decoder.raw_decode(result.output[idx:])
        assert payload["entity"]["id"] == "person:elon-musk"
        assert payload["mention_count"] >= 1


class TestEntityMergeCli:
    def test_dry_run(self, tmp_db_with_entities):
        # SpaceX vs Tesla — both companies, no actual write.
        result = _run(["entity", "merge", "company:spacex", "company:tesla", "--dry-run"])
        assert result.exit_code == 0
        assert "would repoint" in result.output
        assert "would merge" in result.output

    def test_real_merge_repoints_and_deletes(self, tmp_db_with_entities):
        result = _run(["entity", "merge", "company:spacex", "company:tesla"])
        assert result.exit_code == 0
        assert "deleted" in result.output

        repo = SqliteEntityRepository(db_path=str(tmp_db_with_entities / "podcasts.db"))
        assert repo.get_entity("company:tesla") is None
        assert repo.get_entity("company:spacex") is not None

    def test_refuses_cross_type(self, tmp_db_with_entities):
        result = _run(["entity", "merge", "person:elon-musk", "company:tesla"])
        assert result.exit_code == 1
        assert "across types" in result.output

    def test_unknown_keeper_exits(self, tmp_db_with_entities):
        result = _run(["entity", "merge", "person:nobody", "person:elon-musk"])
        assert result.exit_code == 1
        assert "Keeper" in result.output

    def test_unknown_loser_exits(self, tmp_db_with_entities):
        result = _run(["entity", "merge", "person:elon-musk", "person:nobody"])
        assert result.exit_code == 1
        assert "Loser" in result.output
