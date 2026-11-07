"""Spec #115 — ``entity_page_path``: the URL that actually shows an entity.

The admin traffic report links each entity page. A re-typed entity lives at
its current-type URL unless another row owns that URL, in which case it
keeps the URL of the type its id was minted with (the public site's
``current_entity_path`` rule).
"""

from __future__ import annotations

from thestill.models.entities import EntityMention, EntityRecord, EntityType, MentionRole
from thestill.web.routes.api_entities import entity_page_path, entity_path_resolver


def _mention(entity_id: str) -> EntityMention:
    return EntityMention(
        entity_id=entity_id,
        episode_id="00000000-0000-0000-0000-000000000001",
        segment_id=9,
        start_ms=540_000,
        end_ms=544_000,
        role=MentionRole.MENTIONED,
        surface_form="x",
        quote_excerpt="x",
        confidence=0.9,
        extractor="gliner:test",
    )


def test_plain_entity_lives_at_its_type(app_state):
    repo = app_state.entity_repository
    repo.upsert_entity(EntityRecord(id="person:ada-lovelace", type=EntityType.PERSON, canonical_name="Ada"))
    assert entity_page_path(repo, "person:ada-lovelace") == "/entities/person/ada-lovelace"


def test_retyped_entity_moves_to_its_current_type(app_state):
    repo = app_state.entity_repository
    repo.upsert_entity(EntityRecord(id="company:suno", type=EntityType.PRODUCT, canonical_name="Suno"))
    assert entity_page_path(repo, "company:suno") == "/entities/product/suno"


def test_retyped_entity_keeps_its_old_url_when_another_row_owns_the_current_one(app_state):
    from tests.integration.web.test_public_api import _seed_people

    _seed_people(app_state)  # the episode the mention points at
    repo = app_state.entity_repository
    repo.upsert_entity(EntityRecord(id="company:nvidia", type=EntityType.PRODUCT, canonical_name="Nvidia"))
    repo.upsert_entity(EntityRecord(id="product:nvidia", type=EntityType.PRODUCT, canonical_name="Nvidia Shield"))
    repo.insert_mentions([_mention("product:nvidia")])
    assert entity_page_path(repo, "company:nvidia") == "/entities/company/nvidia"
    assert entity_page_path(repo, "product:nvidia") == "/entities/product/nvidia"


def test_missing_entity_has_no_path(app_state):
    resolve = entity_path_resolver(app_state.entity_repository)
    assert resolve("person:nobody") is None
