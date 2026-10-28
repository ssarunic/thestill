"""Entity curation through the admin API (``/api/admin/entities``): the same
``EntityCurationService`` as ``thestill entity ...``, admin-only and audited."""

from __future__ import annotations

from datetime import datetime, timezone

import pytest

from tests.integration.web.test_abuse_controls_api import (  # noqa: F401 — fixtures
    _fresh_buckets,
    _podcast,
    _sign_in,
    _user,
    app_config,
    app_state,
    client,
)
from thestill.models.entities import EntityMention, EntityRecord, EntityType, MentionRole
from thestill.models.podcast import Episode


def _admin(client, app_state):
    admin = _user(app_state, "op@example.com", is_admin=True)
    _sign_in(client, app_state, admin)
    return admin


@pytest.fixture
def corpus(app_state):
    """``topic:islam`` holds the religion plus two fighter mentions;
    ``person:islam`` (a given name, re-typed to topic) holds one more."""
    podcast = _podcast(app_state, "mma-show")
    app_state.repository.save_episodes(
        [
            Episode(
                id="11111111-1111-4111-8111-111111111111",
                podcast_id=podcast.id,
                external_id="e1",
                title="MMA Show",
                description="",
                audio_url="https://example.com/1.mp3",
                pub_date=datetime(2026, 7, 9, tzinfo=timezone.utc),
            )
        ]
    )
    repo = app_state.entity_repository
    repo.upsert_entity(
        EntityRecord(id="topic:islam", type=EntityType.TOPIC, canonical_name="Islam", wikidata_qid="Q432")
    )
    repo.upsert_entity(
        EntityRecord(id="person:islam", type=EntityType.TOPIC, canonical_name="Islam", wikidata_qid="Q3802541")
    )
    repo.insert_mentions(
        [
            EntityMention(
                episode_id="11111111-1111-4111-8111-111111111111",
                segment_id=seg,
                start_ms=seg * 1000,
                end_ms=seg * 1000 + 500,
                role=MentionRole.MENTIONED,
                surface_form="Islam",
                quote_excerpt=quote,
                confidence=0.9,
                extractor="test",
            )
            for seg, quote in ((1, "Islam is a religion."), (2, "Islam smushed him."), (3, "Islam kept growing."))
        ]
    )
    ids = sorted(m.id for m in repo.list_pending_mentions())
    for mention_id, entity_id in zip(ids, ("topic:islam", "topic:islam", "person:islam")):
        repo.resolve_mention(mention_id=mention_id, entity_id=entity_id, status="resolved")
    return ids


def test_entity_curation_needs_an_admin(client, app_state):
    _sign_in(client, app_state, _user(app_state, "reader@example.com"))
    for path in (
        "/api/admin/entities/merge",
        "/api/admin/entities/move-mentions",
        "/api/admin/entities/split",
        "/api/admin/entities/topic:x/relink",
        "/api/admin/entities/blacklist",
        "/api/admin/entities/prune-orphans",
    ):
        assert client.post(path, json={}).status_code == 403, path


def test_split_then_move_untangles_one_name_used_for_two_things(client, app_state, corpus):
    _admin(client, app_state)
    split = client.post(
        "/api/admin/entities/split",
        json={
            "from_entity_id": "person:islam",
            "name": "Islam Makhachev",
            "type": "person",
            "qid": "Q18637676",
            "description": "Russian mixed martial arts fighter",
            "reason": "given-name link held the fighter's quotes",
        },
    ).json()
    assert split["entity"]["id"] == "person:islam-makhachev"
    assert split["moved"] == 1 and split["deleted"] == ["person:islam"]

    moved = client.post(
        "/api/admin/entities/move-mentions",
        json={
            "from_entity_id": "topic:islam",
            "to_entity_id": "person:islam-makhachev",
            "mention_ids": [corpus[1]],
        },
    ).json()
    assert moved["moved"] == 1

    repo = app_state.entity_repository
    assert repo.get_entity("person:islam") is None
    assert [repo.get_mention(i).entity_id for i in corpus] == [
        "topic:islam",
        "person:islam-makhachev",
        "person:islam-makhachev",
    ]
    actions = [e["action"] for e in client.get("/api/admin/audit").json()["entries"]]
    assert actions[:2] == ["entity.move_mentions", "entity.split"]


def test_relink_fixes_the_link_and_refuses_the_old_one(client, app_state):
    _admin(client, app_state)
    repo = app_state.entity_repository
    repo.upsert_entity(
        EntityRecord(
            id="company:perplexity",
            type=EntityType.COMPANY,
            canonical_name="Perplexity",
            wikidata_qid="Q372193",
            description="the exponentiation of the information entropy",
        )
    )
    body = client.post(
        "/api/admin/entities/company:perplexity/relink",
        json={"qid": "Q123403392", "description": "Chatbot search engine"},
    ).json()
    assert (body["old_qid"], body["new_qid"], body["blacklisted"]) == ("Q372193", "Q123403392", ["Perplexity"])
    got = repo.get_entity("company:perplexity")
    assert (got.wikidata_qid, got.description) == ("Q123403392", "Chatbot search engine")
    assert repo.is_blacklisted("Perplexity", "Q372193")


def test_set_type_moves_the_bucket_in_place_and_audits(client, app_state):
    _admin(client, app_state)
    repo = app_state.entity_repository
    repo.upsert_entity(
        EntityRecord(id="product:android-17", type=EntityType.PERSON, canonical_name="Android 17", wikidata_qid=None)
    )
    body = client.post(
        "/api/admin/entities/product:android-17/set-type", json={"type": "product", "reason": "the OS"}
    ).json()
    assert (body["old_type"], body["new_type"]) == ("person", "product")
    assert repo.get_entity("product:android-17").type is EntityType.PRODUCT
    assert client.get("/api/admin/audit").json()["entries"][0]["action"] == "entity.set_type"
    again = client.post("/api/admin/entities/product:android-17/set-type", json={"type": "product"})
    assert again.status_code == 409


def test_refusals_are_409_with_a_reason(client, app_state, corpus):
    _admin(client, app_state)
    across = client.post("/api/admin/entities/merge", json={"keeper_id": "topic:islam", "loser_id": "person:nobody"})
    assert across.status_code == 409 and across.json()["detail"]["error"] == "not_found"
    taken = client.post("/api/admin/entities/person:islam/relink", json={"qid": "Q432"})
    assert taken.status_code == 409 and taken.json()["detail"]["error"] == "qid_taken"
    bad = client.post("/api/admin/entities/blacklist", json={"surface_form": "Islam", "wrong_qid": "not-a-qid"})
    assert bad.status_code == 409 and bad.json()["detail"]["error"] == "invalid_qid"


def test_merge_moves_everything_and_prune_reports_before_deleting(client, app_state, corpus):
    _admin(client, app_state)
    repo = app_state.entity_repository
    repo.upsert_entity(EntityRecord(id="topic:doximity", type=EntityType.COMPANY, canonical_name="Doximity"))
    repo.upsert_entity(EntityRecord(id="company:doximity", type=EntityType.COMPANY, canonical_name="Doximity"))
    merged = client.post(
        "/api/admin/entities/merge", json={"keeper_id": "company:doximity", "loser_id": "topic:doximity"}
    ).json()
    assert merged["loser_id"] == "topic:doximity" and repo.get_entity("topic:doximity") is None

    report = client.post("/api/admin/entities/prune-orphans", json={}).json()
    assert "company:doximity" in report["sample"] and report["applied"] is False
    assert repo.get_entity("company:doximity") is not None
    applied = client.post("/api/admin/entities/prune-orphans", json={"apply": True}).json()
    assert applied["deleted"] == report["orphans"] and repo.get_entity("company:doximity") is None
