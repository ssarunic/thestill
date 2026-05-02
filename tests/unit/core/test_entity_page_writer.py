"""Spec #28 §2.10 — EntityPageWriter tests.

The Obsidian-friendly per-entity Markdown surface. Smaller scope than
the deleted ``corpus_writer.py`` tests — only entity pages survive
the Phase 2.10 rip-out; episode pages were a qmd-specific projection
that's gone.
"""

from __future__ import annotations

from thestill.core.entity_page_writer import EntityPageWriter
from thestill.models.entities import EntityRecord, EntityType
from thestill.repositories.sqlite_entity_repository import SqliteEntityRepository
from thestill.repositories.sqlite_podcast_repository import SqlitePodcastRepository
from thestill.utils.path_manager import PathManager


def _setup(tmp_path) -> tuple[EntityPageWriter, SqliteEntityRepository, PathManager]:
    db_path = str(tmp_path / "podcasts.db")
    SqlitePodcastRepository(db_path=db_path)
    repo = SqliteEntityRepository(db_path=db_path)
    pm = PathManager(storage_path=str(tmp_path))
    return EntityPageWriter(path_manager=pm, entity_repository=repo), repo, pm


class TestEntityPages:
    def test_person_entity_page_rendered(self, tmp_path):
        writer, repo, pm = _setup(tmp_path)
        repo.upsert_entity(
            EntityRecord(
                id="person:elon-musk",
                type=EntityType.PERSON,
                canonical_name="Elon Musk",
                wikidata_qid="Q317521",
                aliases=["Musk"],
            )
        )
        result = writer.write_entity_page(repo.get_entity("person:elon-musk"))
        page = pm.corpus_entity_file("person", "elon-musk")
        assert page.exists()
        body = page.read_text()
        assert "Elon Musk" in body
        assert "Q317521" in body
        assert "Musk" in body
        assert page in result.written

    def test_product_entities_skipped(self, tmp_path):
        writer, repo, _ = _setup(tmp_path)
        repo.upsert_entity(
            EntityRecord(
                id="product:tesla-model-3",
                type=EntityType.PRODUCT,
                canonical_name="Tesla Model 3",
            )
        )
        result = writer.write_entity_page(repo.get_entity("product:tesla-model-3"))
        assert result.written == []
        assert result.skipped_unchanged == []

    def test_unchanged_rerun_does_not_rewrite(self, tmp_path):
        writer, repo, pm = _setup(tmp_path)
        repo.upsert_entity(
            EntityRecord(
                id="company:openai",
                type=EntityType.COMPANY,
                canonical_name="OpenAI",
            )
        )
        first = writer.write_entity_page(repo.get_entity("company:openai"))
        assert len(first.written) == 1
        second = writer.write_entity_page(repo.get_entity("company:openai"))
        assert second.written == []
        assert len(second.skipped_unchanged) == 1

    def test_write_all_renders_every_supported_type(self, tmp_path):
        writer, repo, pm = _setup(tmp_path)
        repo.upsert_entity(EntityRecord(id="person:a", type=EntityType.PERSON, canonical_name="A"))
        repo.upsert_entity(EntityRecord(id="company:b", type=EntityType.COMPANY, canonical_name="B"))
        repo.upsert_entity(EntityRecord(id="topic:c", type=EntityType.TOPIC, canonical_name="C"))
        repo.upsert_entity(EntityRecord(id="product:d", type=EntityType.PRODUCT, canonical_name="D"))
        result = writer.write_all()
        assert len(result.written) == 3  # product skipped

    def test_write_all_filtered_by_type(self, tmp_path):
        writer, repo, _ = _setup(tmp_path)
        repo.upsert_entity(EntityRecord(id="person:x", type=EntityType.PERSON, canonical_name="X"))
        repo.upsert_entity(EntityRecord(id="company:y", type=EntityType.COMPANY, canonical_name="Y"))
        result = writer.write_all(entity_type=EntityType.PERSON)
        assert len(result.written) == 1
