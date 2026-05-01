"""Spec #28 §1.12 — harness-eval grader logic tests.

The grader walks each harness question through the entity tools and
returns one of three statuses: pass, fail, skip. These tests exercise
the grader's decision boundary without involving the live DB.
"""

from __future__ import annotations

import json
import sqlite3
import uuid
from pathlib import Path

import pytest
from click.testing import CliRunner

from thestill.cli import _grade_one, main
from thestill.models.entities import EntityMention, EntityRecord, EntityType, MentionRole
from thestill.repositories.sqlite_entity_repository import SqliteEntityRepository
from thestill.repositories.sqlite_podcast_repository import SqlitePodcastRepository


@pytest.fixture
def graded_repo(tmp_path):
    """DB with one resolved entity + two episodes mentioning it,
    quote excerpts contain the keyword 'IPO'."""
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
        for eid, ext in ((ep1, "e1"), (ep2, "e2")):
            conn.execute(
                "INSERT INTO episodes (id, podcast_id, external_id, title, audio_url, pub_date) "
                "VALUES (?, ?, ?, ?, ?, ?)",
                (eid, podcast_id, ext, f"Ep {ext}", f"https://example.com/{ext}.mp3", "2026-04-28T00:00:00"),
            )
        conn.commit()
    finally:
        conn.close()

    repo = SqliteEntityRepository(db_path=str(db_path))
    repo.upsert_entity(
        EntityRecord(
            id="company:spacex",
            type=EntityType.COMPANY,
            canonical_name="SpaceX",
            wikidata_qid="Q193701",
        )
    )

    def _mention(episode_id, segment_id, excerpt):
        return EntityMention(
            episode_id=episode_id,
            segment_id=segment_id,
            start_ms=segment_id * 1000,
            end_ms=segment_id * 1000 + 5000,
            speaker="Scott Galloway",
            role=MentionRole.MENTIONED,
            surface_form="SpaceX",
            quote_excerpt=excerpt,
            confidence=0.95,
            extractor="gliner:test",
        )

    repo.insert_mentions(
        [
            _mention(ep1, 1, "The SpaceX IPO is the biggest news this year"),
            _mention(ep2, 1, "Here's the thing about SpaceX — they spend like crazy"),
        ]
    )
    for m in repo.list_pending_mentions():
        repo.resolve_mention(mention_id=m.id, entity_id="company:spacex", status="resolved")
    return repo, ep1, ep2


class TestGradeOne:
    def test_pass_when_all_signals_met(self, graded_repo):
        repo, _, _ = graded_repo
        question = {
            "id": "qX",
            "question": "What has been said about SpaceX?",
            "expected_entities": ["company:spacex"],
            "expected_quote_fragments": ["spacex"],
            "min_distinct_episodes": 2,
        }
        result = _grade_one(repo, question, limit=20)
        assert result["status"] == "pass"

    def test_fail_when_min_episodes_not_met(self, graded_repo):
        repo, _, _ = graded_repo
        question = {
            "id": "qX",
            "question": "What has been said about SpaceX?",
            "expected_entities": ["company:spacex"],
            "expected_quote_fragments": [],
            "min_distinct_episodes": 5,  # corpus only has 2
        }
        result = _grade_one(repo, question, limit=20)
        assert result["status"] == "fail"
        assert any("min_distinct_episodes not met" in n for n in result["notes"])

    def test_fail_when_fragment_missing(self, graded_repo):
        repo, _, _ = graded_repo
        question = {
            "id": "qX",
            "question": "What has been said about SpaceX?",
            "expected_entities": ["company:spacex"],
            "expected_quote_fragments": ["nonexistent-phrase"],
            "min_distinct_episodes": 1,
        }
        result = _grade_one(repo, question, limit=20)
        assert result["status"] == "fail"
        assert any("fragments not found" in n for n in result["notes"])

    def test_skip_when_entity_unresolved(self, graded_repo):
        repo, _, _ = graded_repo
        question = {
            "id": "qX",
            "question": "What has X said?",
            "expected_entities": ["person:not-a-real-person"],
            "expected_quote_fragments": [],
            "min_distinct_episodes": 1,
        }
        result = _grade_one(repo, question, limit=20)
        assert result["status"] == "skip"
        assert any("unresolved entity" in n for n in result["notes"])

    def test_skip_when_entity_exists_but_no_mentions(self, tmp_path):
        # Bare DB with an entity but no mentions yet — represents the
        # "resolver hasn't run" state.
        SqlitePodcastRepository(db_path=str(tmp_path / "thestill.db"))
        repo = SqliteEntityRepository(db_path=str(tmp_path / "thestill.db"))
        repo.upsert_entity(
            EntityRecord(
                id="topic:lonely",
                type=EntityType.TOPIC,
                canonical_name="Lonely",
            )
        )
        question = {
            "id": "qX",
            "question": "What about Lonely?",
            "expected_entities": ["topic:lonely"],
            "expected_quote_fragments": [],
            "min_distinct_episodes": 1,
        }
        result = _grade_one(repo, question, limit=20)
        assert result["status"] == "skip"
        assert any("no resolved mentions" in n for n in result["notes"])


class TestHarnessEvalCli:
    def test_skip_summary_for_unresolved_corpus(self, tmp_path, monkeypatch):
        # Build a tmp DB with no resolved mentions but a complete
        # podcast-repo schema. All harness questions should skip.
        storage = tmp_path / "data"
        storage.mkdir()
        monkeypatch.setenv("STORAGE_PATH", str(storage))
        monkeypatch.setenv("THESTILL_ENV_FILE", str(tmp_path / ".no-such-env"))
        SqlitePodcastRepository(db_path=str(storage / "podcasts.db"))

        # Tiny harness file — one question only.
        harness = tmp_path / "harness.json"
        harness.write_text(
            json.dumps(
                {
                    "questions": [
                        {
                            "id": "q01",
                            "question": "What has X said?",
                            "expected_entities": ["person:nobody"],
                            "expected_quote_fragments": [],
                            "min_distinct_episodes": 1,
                        }
                    ]
                }
            )
        )

        runner = CliRunner()
        result = runner.invoke(main, ["harness-eval", "--questions-file", str(harness)], catch_exceptions=False)
        assert result.exit_code == 0
        assert "0/1 pass" in result.output
        assert "1 skip" in result.output

    def test_json_output_emits_summary(self, tmp_path, monkeypatch):
        storage = tmp_path / "data"
        storage.mkdir()
        monkeypatch.setenv("STORAGE_PATH", str(storage))
        monkeypatch.setenv("THESTILL_ENV_FILE", str(tmp_path / ".no-such-env"))
        SqlitePodcastRepository(db_path=str(storage / "podcasts.db"))

        harness = tmp_path / "harness.json"
        harness.write_text(
            json.dumps(
                {
                    "questions": [
                        {
                            "id": "q01",
                            "question": "X?",
                            "expected_entities": ["person:nobody"],
                            "expected_quote_fragments": [],
                            "min_distinct_episodes": 1,
                        }
                    ]
                }
            )
        )

        runner = CliRunner()
        result = runner.invoke(
            main,
            ["harness-eval", "--questions-file", str(harness), "--json"],
            catch_exceptions=False,
        )
        assert result.exit_code == 0
        # Output begins with config-load logs, then the JSON object.
        idx = result.output.find('{\n  "results":')
        assert idx >= 0
        decoder = json.JSONDecoder()
        payload, _ = decoder.raw_decode(result.output[idx:])
        assert payload["summary"]["skip"] == 1
        assert payload["summary"]["total"] == 1
