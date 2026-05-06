"""Spec #28 §5.2 — REST endpoints for the episode-page entity UX.

Two endpoints, both wrap existing repository methods:

- ``GET /api/episodes/{episode_id}/entities`` — episode-scoped mention list
- ``GET /api/entities/{type}/{slug}`` — entity summary

These tests seed a small fixture corpus (1 podcast, 2 episodes, a handful
of resolved mentions across 2 entities) and exercise the routes through
the same TestClient + AppState fixtures the rest of the integration
suite uses.
"""

from __future__ import annotations

import sqlite3
import uuid
from pathlib import Path

from thestill.models.entities import EntityMention, EntityRecord, EntityType, MentionRole
from thestill.repositories.sqlite_entity_repository import SqliteEntityRepository


def _seed_corpus(db_path: Path) -> tuple[str, str, str]:
    """Insert a podcast + episode + two entities + a few resolved mentions.

    Returns ``(podcast_id, episode_id, second_episode_id)``.
    """
    podcast_id = str(uuid.uuid4())
    episode_id = str(uuid.uuid4())
    other_episode_id = str(uuid.uuid4())
    conn = sqlite3.connect(str(db_path))
    try:
        conn.execute(
            "INSERT INTO podcasts (id, rss_url, title, slug) VALUES (?, ?, ?, ?)",
            (podcast_id, "https://example.com/rss", "Fixture Pod", "fixture-pod"),
        )
        conn.execute(
            "INSERT INTO episodes (id, podcast_id, external_id, title, audio_url, slug, pub_date)"
            " VALUES (?, ?, ?, ?, ?, ?, ?)",
            (
                episode_id,
                podcast_id,
                "ext1",
                "First Episode",
                "https://example.com/ep1.mp3",
                "first-episode",
                "2026-04-01T00:00:00+00:00",
            ),
        )
        conn.execute(
            "INSERT INTO episodes (id, podcast_id, external_id, title, audio_url, slug, pub_date)"
            " VALUES (?, ?, ?, ?, ?, ?, ?)",
            (
                other_episode_id,
                podcast_id,
                "ext2",
                "Second Episode",
                "https://example.com/ep2.mp3",
                "second-episode",
                "2026-04-15T00:00:00+00:00",
            ),
        )
        conn.commit()
    finally:
        conn.close()

    repo = SqliteEntityRepository(db_path=str(db_path))
    musk = EntityRecord(
        id="person:elon-musk",
        type=EntityType.PERSON,
        canonical_name="Elon Musk",
        wikidata_qid="Q317521",
        aliases=["Musk"],
        description="Founder of multiple companies.",
    )
    spacex = EntityRecord(
        id="company:spacex",
        type=EntityType.COMPANY,
        canonical_name="SpaceX",
        wikidata_qid="Q193701",
        aliases=[],
    )
    repo.upsert_entity(musk)
    repo.upsert_entity(spacex)

    mentions = [
        # Episode 1 — Musk gets mentioned twice, SpaceX once
        EntityMention(
            entity_id=musk.id,
            episode_id=episode_id,
            segment_id=5,
            start_ms=15_000,
            end_ms=18_000,
            speaker="Scott Galloway",
            role=MentionRole.MENTIONED,
            surface_form="Elon Musk",
            quote_excerpt="Elon Musk is back in the news.",
            confidence=0.95,
            extractor="gliner:test",
        ),
        EntityMention(
            entity_id=musk.id,
            episode_id=episode_id,
            segment_id=12,
            start_ms=60_000,
            end_ms=63_000,
            speaker="Scott Galloway",
            role=MentionRole.MENTIONED,
            surface_form="Musk",
            quote_excerpt="...and Musk said today...",
            confidence=0.40,  # below the default 0.5 floor
            extractor="gliner:test",
        ),
        EntityMention(
            entity_id=spacex.id,
            episode_id=episode_id,
            segment_id=8,
            start_ms=30_000,
            end_ms=33_000,
            speaker="Ed Elson",
            role=MentionRole.MENTIONED,
            surface_form="SpaceX",
            quote_excerpt="SpaceX launched today.",
            confidence=0.88,
            extractor="gliner:test",
        ),
        # Episode 2 — only SpaceX
        EntityMention(
            entity_id=spacex.id,
            episode_id=other_episode_id,
            segment_id=3,
            start_ms=10_000,
            end_ms=12_000,
            speaker="Scott Galloway",
            role=MentionRole.MENTIONED,
            surface_form="SpaceX",
            quote_excerpt="SpaceX again.",
            confidence=0.90,
            extractor="gliner:test",
        ),
    ]
    repo.insert_mentions(mentions)
    # Mark all four mentions resolved so find_mentions returns them
    # (the repo only returns rows with resolution_status='resolved').
    conn = sqlite3.connect(str(db_path))
    try:
        conn.execute(
            "UPDATE entity_mentions SET resolution_status = 'resolved' WHERE episode_id IN (?, ?)",
            (episode_id, other_episode_id),
        )
        conn.commit()
    finally:
        conn.close()
    return podcast_id, episode_id, other_episode_id


class TestEpisodeEntitiesEndpoint:
    def test_returns_grouped_entities_with_mentions(self, client, app_state):
        _, episode_id, _ = _seed_corpus(Path(app_state.repository.db_path))

        resp = client.get(f"/api/episodes/{episode_id}/entities")
        assert resp.status_code == 200
        body = resp.json()
        assert body["episode_id"] == episode_id

        entities = body["entities"]
        assert len(entities) == 2  # Musk + SpaceX
        by_id = {e["entity"]["id"]: e for e in entities}

        musk = by_id["person:elon-musk"]
        assert musk["entity"]["canonical_name"] == "Elon Musk"
        assert musk["entity"]["wikidata_qid"] == "Q317521"
        # Both Musk mentions land here; the confidence floor is 0.0 by default
        assert musk["mention_count"] == 2
        assert musk["first_mention_ms"] == 15_000
        assert musk["speaker_kind"] == "unknown"
        assert len(musk["mentions"]) == 2
        # Mentions are ordered by start_ms
        assert musk["mentions"][0]["start_ms"] == 15_000
        assert musk["mentions"][1]["start_ms"] == 60_000

        spacex = by_id["company:spacex"]
        assert spacex["mention_count"] == 1  # only the ep_test_1 mention
        assert spacex["entity"]["type"] == "company"

    def test_min_confidence_drops_low_confidence_mentions(self, client, app_state):
        _, episode_id, _ = _seed_corpus(Path(app_state.repository.db_path))

        resp = client.get(f"/api/episodes/{episode_id}/entities", params={"min_confidence": "0.5"})
        assert resp.status_code == 200
        body = resp.json()
        by_id = {e["entity"]["id"]: e for e in body["entities"]}

        # The 0.40 Musk mention is dropped, the 0.95 one stays.
        musk = by_id["person:elon-musk"]
        assert musk["mention_count"] == 1
        assert musk["mentions"][0]["confidence"] == 0.95

    def test_returns_404_for_unknown_episode(self, client):
        resp = client.get("/api/episodes/no-such-episode/entities")
        assert resp.status_code == 404


class TestEntitySummaryEndpoint:
    def test_returns_full_summary_with_recent_mentions(self, client, app_state):
        _seed_corpus(Path(app_state.repository.db_path))

        resp = client.get("/api/entities/person/elon-musk")
        assert resp.status_code == 200
        body = resp.json()

        assert body["entity"]["id"] == "person:elon-musk"
        assert body["entity"]["canonical_name"] == "Elon Musk"
        assert body["entity"]["wikidata_qid"] == "Q317521"
        assert "Musk" in body["aliases"]
        assert body["description"]
        assert body["mention_count"] == 2
        assert len(body["recent_mentions"]) == 2
        # Recent mentions are citation-shaped; check one row
        first = body["recent_mentions"][0]
        assert first["episode_id"]
        assert first["podcast_title"] == "Fixture Pod"
        assert first["episode_title"] == "First Episode"
        assert first["surface_form"] in {"Elon Musk", "Musk"}

    def test_accepts_full_id_form_in_url_path(self, client, app_state):
        _seed_corpus(Path(app_state.repository.db_path))

        # /api/entities/person/person:elon-musk should also resolve.
        resp = client.get("/api/entities/person/person:elon-musk")
        assert resp.status_code == 200
        assert resp.json()["entity"]["id"] == "person:elon-musk"

    def test_rejects_invalid_entity_type(self, client):
        resp = client.get("/api/entities/celebrity/foo")
        assert resp.status_code == 400

    def test_returns_404_for_unknown_entity(self, client, app_state):
        _seed_corpus(Path(app_state.repository.db_path))
        resp = client.get("/api/entities/person/nobody")
        assert resp.status_code == 404

    def test_surfaces_host_anchor_with_zero_mentions(self, client, app_state):
        """A host who never gets named in a transcript still has 0 mentions
        in ``entity_mentions``; the entity page must surface their podcast
        anchor so the page doesn't render as an empty shell.
        """
        db_path = Path(app_state.repository.db_path)
        _seed_corpus(db_path)

        # Register Elon as host of the seeded podcast — no extra mentions.
        conn = sqlite3.connect(str(db_path))
        try:
            podcast_row = conn.execute("SELECT id FROM podcasts LIMIT 1").fetchone()
            conn.execute(
                "UPDATE podcasts SET host_entity_ids = ? WHERE id = ?",
                ('["person:elon-musk"]', podcast_row[0]),
            )
            conn.commit()
        finally:
            conn.close()

        resp = client.get("/api/entities/person/elon-musk")
        assert resp.status_code == 200
        body = resp.json()
        assert len(body["hosts_podcasts"]) == 1
        host = body["hosts_podcasts"][0]
        assert host["podcast_title"] == "Fixture Pod"
        assert host["podcast_slug"] == "fixture-pod"
        assert host["episode_count"] == 2
        assert body["recurring_podcasts"] == []
        assert body["guest_episodes"] == []

    def test_surfaces_guest_episode_anchors(self, client, app_state):
        """Guest anchors (``episodes.guest_entity_ids``) should be listed
        even when the entity has no transcript mentions.
        """
        db_path = Path(app_state.repository.db_path)
        _seed_corpus(db_path)

        conn = sqlite3.connect(str(db_path))
        try:
            ep_rows = conn.execute("SELECT id FROM episodes ORDER BY pub_date").fetchall()
            for (ep_id,) in ep_rows:
                conn.execute(
                    "UPDATE episodes SET guest_entity_ids = ? WHERE id = ?",
                    ('["person:elon-musk"]', ep_id),
                )
            conn.commit()
        finally:
            conn.close()

        resp = client.get("/api/entities/person/elon-musk")
        assert resp.status_code == 200
        body = resp.json()
        assert len(body["guest_episodes"]) == 2
        # Newest first (Second Episode pub_date 2026-04-15 > First 2026-04-01)
        titles = [ep["episode_title"] for ep in body["guest_episodes"]]
        assert titles == ["Second Episode", "First Episode"]
        assert body["guest_episodes"][0]["podcast_slug"] == "fixture-pod"
        assert body["guest_episodes"][0]["episode_slug"] == "second-episode"
