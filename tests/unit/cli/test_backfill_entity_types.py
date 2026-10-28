"""CliRunner smoke tests for ``thestill backfill-entity-types`` (spec #92 Phase 0)."""

from __future__ import annotations

import os
import sqlite3
import uuid
from pathlib import Path

import pytest
from click.testing import CliRunner

from thestill.cli import main
from thestill.models.entities import EntityMention, EntityRecord, EntityType, ResolutionStatus
from thestill.repositories.sqlite_entity_repository import SqliteEntityRepository
from thestill.repositories.sqlite_podcast_repository import SqlitePodcastRepository


@pytest.fixture
def repo(tmp_path, monkeypatch):
    storage = tmp_path / "data"
    storage.mkdir()
    monkeypatch.setenv("STORAGE_PATH", str(storage))
    monkeypatch.setenv("THESTILL_ENV_FILE", str(tmp_path / ".no-such-env"))
    monkeypatch.delenv("DATABASE_URL", raising=False)
    db_path = str(storage / "podcasts.db")
    SqlitePodcastRepository(db_path=db_path)
    podcast_id, episode_id = str(uuid.uuid4()), str(uuid.uuid4())
    with sqlite3.connect(db_path) as conn:
        conn.execute(
            "INSERT INTO podcasts (id, rss_url, title, slug) VALUES (?, 'https://x/f.xml', 'P', 'p')", (podcast_id,)
        )
        conn.execute(
            "INSERT INTO episodes (id, podcast_id, external_id, title, slug, description, audio_url) "
            "VALUES (?, ?, 'e1', 'E', 'e', '', 'https://x/a.mp3')",
            (episode_id, podcast_id),
        )
    repo = SqliteEntityRepository(db_path=db_path)
    entities = [
        ("topic:margin-call", EntityType.TOPIC, "Margin Call", "Q624614", ["Q11424"]),
        ("company:allianz", EntityType.COMPANY, "Allianz", "Q487292", ["Q4830453", "Q891723", "Q43229"]),
        ("company:uncached", EntityType.COMPANY, "Uncached", "Q999", []),
    ]
    for entity_id, type_, name, qid, p31 in entities:
        repo.upsert_entity(
            EntityRecord(id=entity_id, type=type_, canonical_name=name, wikidata_qid=qid, wikidata_instance_of=p31)
        )
    repo.insert_mentions(
        [
            EntityMention(
                entity_id=entity_id,
                resolution_status=ResolutionStatus.RESOLVED,
                episode_id=episode_id,
                segment_id=i,
                start_ms=0,
                end_ms=1000,
                surface_form=name,
                surface_label="topic",
                quote_excerpt=name,
                confidence=0.9,
                extractor="gliner:test",
            )
            for i, (entity_id, _, name, _, _) in enumerate(entities)
        ]
    )
    return repo


def _run(*args):
    result = CliRunner().invoke(main, ["backfill-entity-types", *args], catch_exceptions=False)
    assert result.exit_code == 0, result.output
    return result.output


def test_dry_run_lists_only_the_work_and_writes_nothing(repo):
    out = _run("--in-place", "--only-works", "--cached-only", "--dry-run")
    assert "topic:margin-call (topic → product) Margin Call" in out
    assert "allianz" not in out and "uncached" not in out
    assert "1 entit(y/ies) would be reclassified" in out and "topic → product" in out
    assert repo.get_entity("topic:margin-call").type is EntityType.TOPIC


def test_in_place_only_works_changes_the_film_and_keeps_the_company(repo):
    _run("--in-place", "--only-works", "--cached-only")
    assert repo.get_entity("topic:margin-call").type is EntityType.PRODUCT
    assert repo.get_entity("company:allianz").type is EntityType.COMPANY
    assert repo.get_entity("product:margin-call") is None


@pytest.fixture
def people(repo):
    with sqlite3.connect(str(Path(os.environ["STORAGE_PATH"]) / "podcasts.db")) as conn:
        (episode_id,) = conn.execute("SELECT id FROM episodes").fetchone()
    people = (("person:bellingham", "Bellingham"), ("person:london", "London"))
    for entity_id, name in people:
        repo.upsert_entity(
            EntityRecord(
                id=entity_id,
                type=EntityType.PERSON,
                canonical_name=name,
                wikidata_qid="Q430267",
                wikidata_instance_of=["Q515"],  # city
            )
        )
    repo.insert_mentions(
        [
            EntityMention(
                entity_id=entity_id,
                resolution_status=ResolutionStatus.RESOLVED,
                episode_id=episode_id,
                segment_id=10 + i,
                start_ms=0,
                end_ms=1000,
                surface_form=name,
                surface_label="person",
                quote_excerpt=name,
                confidence=0.9,
                extractor="gliner:test",
            )
            for i, (entity_id, name) in enumerate(people)
        ]
    )
    return repo


def test_person_demotions_are_held_by_default(people):
    out = _run("--in-place", "--cached-only")
    assert "⏸ person:bellingham (person → topic) Bellingham" in out
    assert "2 person demotion(s) held" in out
    assert people.get_entity("person:bellingham").type is EntityType.PERSON
    assert people.get_entity("company:allianz").type is EntityType.COMPANY


def test_entity_id_applies_a_reviewed_person_demotion(people):
    out = _run("--in-place", "--cached-only", "--entity-id", "person:london")
    assert "person:london (person → topic) London" in out and "held" not in out
    assert people.get_entity("person:london").type is EntityType.TOPIC
    assert people.get_entity("person:bellingham").type is EntityType.PERSON


def test_entity_id_cannot_be_mixed_with_a_scope(people):
    result = CliRunner().invoke(main, ["backfill-entity-types", "--entity-id", "person:london", "--podcast-id", "p"])
    assert result.exit_code == 1 and "cannot be combined" in result.output
