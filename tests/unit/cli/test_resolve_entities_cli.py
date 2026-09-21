"""Spec #81 - ``thestill resolve-entities`` runs the worker's code.

The command used to hard-code ReFinED and skip overrides, the blacklist,
coreference and the recorded method. It now calls the same core as the
``resolve-entities`` stage, with the linker ``ENTITY_LINKER`` picks.
"""

from __future__ import annotations

import sqlite3
import uuid

import pytest
from click.testing import CliRunner

from thestill.cli import main
from thestill.core.entity_linking.live_linker import LinkerUnavailableError
from thestill.core.entity_linking.shared import ResolutionResult, unresolvable_result
from thestill.models.entities import EntityMention, EntityRecord, EntityType, ResolutionMethod
from thestill.repositories.sqlite_entity_repository import SqliteEntityRepository
from thestill.repositories.sqlite_podcast_repository import SqlitePodcastRepository

MUSK = EntityRecord(id="person:elon-musk", type=EntityType.PERSON, canonical_name="Elon Musk", wikidata_qid="Q317521")


class FakeLinker:
    """Links "Elon Musk"; fails for the rest when told to."""

    def __init__(self, unavailable=False):
        self.unavailable = unavailable
        self.calls = []

    def resolve(self, mentions, *, is_blacklisted=None, context=None):
        self.calls.append((mentions, is_blacklisted, context))
        results = []
        for m in mentions:
            if m.surface_form == "Elon Musk":
                results.append(ResolutionResult(m.id, MUSK, "resolved", ResolutionMethod.LLM_LINKED))
            elif not self.unavailable:
                results.append(unresolvable_result(m))
        if self.unavailable:
            raise LinkerUnavailableError("wikidata down", results=results, unanswered_names=1)
        return results


@pytest.fixture
def cli_db(tmp_path, monkeypatch):
    storage = tmp_path / "data"
    storage.mkdir()
    monkeypatch.setenv("STORAGE_PATH", str(storage))
    monkeypatch.setenv("THESTILL_ENV_FILE", str(tmp_path / ".no-such-env"))
    monkeypatch.delenv("ENTITY_LINKER", raising=False)
    db_path = str(storage / "podcasts.db")
    SqlitePodcastRepository(db_path=db_path)
    podcast_id, episode_id = str(uuid.uuid4()), str(uuid.uuid4())
    with sqlite3.connect(db_path) as conn:
        conn.execute(
            "INSERT INTO podcasts (id, rss_url, title, slug) VALUES (?, 'https://example.com/f.xml', 'Show', 'show')",
            (podcast_id,),
        )
        conn.execute(
            "INSERT INTO episodes (id, podcast_id, external_id, title, audio_url) VALUES (?, ?, 'e1', 'Ep', 'https://x/e.mp3')",
            (episode_id, podcast_id),
        )
    repo = SqliteEntityRepository(db_path=db_path)
    repo.insert_mentions(
        [
            EntityMention(
                episode_id=episode_id,
                segment_id=i,
                start_ms=i,
                end_ms=i + 1,
                surface_form=name,
                surface_label="person",
                quote_excerpt=f"... {name} ...",
                confidence=0.9,
                extractor="gliner:test",
            )
            for i, name in enumerate(["Elon Musk", "Ed Zitron", "Dropped Name"])
        ]
    )
    return repo, episode_id, db_path


def _run(monkeypatch, linker, *args):
    monkeypatch.setattr("thestill.core.entity_linking.factory.build_linker", lambda config, decisions, **kw: linker)
    return CliRunner().invoke(main, ["resolve-entities", *args])


def _rows(db_path):
    with sqlite3.connect(db_path) as conn:
        return {
            surface: (status, method, entity_id)
            for surface, status, method, entity_id in conn.execute(
                "SELECT surface_form, resolution_status, resolution_method, entity_id FROM entity_mentions"
            )
        }


def test_it_records_the_method_and_honours_a_human_override(cli_db, monkeypatch):
    repo, episode_id, db_path = cli_db
    repo.add_override(surface_form="Dropped Name", episode_id=episode_id, kind="drop", reason="not an entity")
    linker = FakeLinker()
    result = _run(monkeypatch, linker, "--episode-id", episode_id)
    assert result.exit_code == 0, result.output
    rows = _rows(db_path)
    assert rows["Elon Musk"] == ("resolved", "llm_linked", "person:elon-musk")
    assert rows["Ed Zitron"][:2] == ("unresolvable", "unresolvable")
    assert rows["Dropped Name"][:2] == ("dropped", "override")
    (mentions, is_blacklisted, context) = linker.calls[0]
    assert sorted(m.surface_form for m in mentions) == ["Ed Zitron", "Elon Musk"]  # the override never reached it
    assert is_blacklisted is not None
    assert (context.episode_id, context.podcast_title, context.episode_title) == (episode_id, "Show", "Ep")


def test_an_outage_keeps_what_was_decided_leaves_the_rest_pending_and_exits_non_zero(cli_db, monkeypatch):
    _repo, episode_id, db_path = cli_db
    result = _run(monkeypatch, FakeLinker(unavailable=True), "--episode-id", episode_id)
    assert result.exit_code == 1
    rows = _rows(db_path)
    assert rows["Elon Musk"][0] == "resolved"
    assert rows["Ed Zitron"][0] == "pending"  # not written off as unresolvable
    assert "incomplete" in result.output


def test_dry_run_builds_no_linker(cli_db, monkeypatch):
    _repo, _episode_id, db_path = cli_db

    def explode(*_a, **_k):
        raise AssertionError("dry run must not build a linker")

    monkeypatch.setattr("thestill.core.entity_linking.factory.build_linker", explode)
    result = CliRunner().invoke(main, ["resolve-entities", "--dry-run"])
    assert result.exit_code == 0, result.output
    assert "would resolve" in result.output
    assert {status for status, _m, _e in _rows(db_path).values()} == {"pending"}
