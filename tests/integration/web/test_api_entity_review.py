# Copyright 2025-2026 Thestill
#
# Licensed under the Apache License, Version 2.0 (the "License").

"""Integration tests for the admin detection-queue endpoints.

Seeds the real Anthropic bug (mentions of "Anthropic" resolved to the
cosmology entity "Anthropic principle") and drives both routes through the
shared TestClient + AppState fixtures. ``require_auth`` is a no-op in the
fixture's single-user config, so no token is needed.
"""

from __future__ import annotations

import sqlite3
import uuid
from pathlib import Path

from thestill.core.queue_manager import TaskStage
from thestill.models.entities import EntityMention, EntityRecord, EntityType, ResolutionStatus
from thestill.repositories.sqlite_entity_repository import SqliteEntityRepository


def _seed_wrong_resolution(db_path: Path, *, n_mentions: int = 3) -> tuple[str, str]:
    """Seed a podcast/episode + the wrong ``Anthropic principle`` entity with
    ``n_mentions`` resolved "Anthropic" mentions. Returns ``(podcast_id,
    episode_id)``.
    """
    podcast_id = str(uuid.uuid4())
    episode_id = str(uuid.uuid4())
    conn = sqlite3.connect(str(db_path))
    try:
        conn.execute(
            "INSERT INTO podcasts (id, rss_url, title, slug) VALUES (?, ?, ?, ?)",
            (podcast_id, "https://example.com/rss", "Fixture Pod", "fixture-pod"),
        )
        conn.execute(
            "INSERT INTO episodes (id, podcast_id, external_id, title, audio_url, slug, pub_date)"
            " VALUES (?, ?, ?, ?, ?, ?, ?)",
            (episode_id, podcast_id, "ext1", "Ep", "https://example.com/1.mp3", "ep", "2026-04-01T00:00:00+00:00"),
        )
        conn.commit()
    finally:
        conn.close()

    repo = SqliteEntityRepository(db_path=str(db_path))
    repo.upsert_entity(
        EntityRecord(
            id="company:anthropic-principle",
            type=EntityType.COMPANY,
            canonical_name="Anthropic principle",
            wikidata_qid="Q240581",
            wikidata_instance_of=["Q211364"],
        )
    )
    repo.insert_mentions(
        [
            EntityMention(
                entity_id="company:anthropic-principle",
                resolution_status=ResolutionStatus.RESOLVED,
                episode_id=episode_id,
                segment_id=i,
                start_ms=i * 1000,
                end_ms=i * 1000 + 500,
                surface_form="Anthropic",
                quote_excerpt="We use Claude from Anthropic.",
                confidence=0.95,
                extractor="gliner:test",
            )
            for i in range(n_mentions)
        ]
    )
    return podcast_id, episode_id


class TestReviewQueueEndpoint:
    def test_surfaces_the_wrong_resolution_at_the_top(self, client, app_config):
        _seed_wrong_resolution(app_config.database_path)
        resp = client.get("/api/entities/review-queue")
        assert resp.status_code == 200, resp.text
        body = resp.json()
        assert body["count"] >= 1
        top = body["flags"][0]
        assert top["entity_id"] == "company:anthropic-principle"
        assert top["suggested_action"] == "blacklist"
        assert top["suggested_qid"] == "Q240581"
        assert top["evidence"]["affected_mentions"] == 3

    def test_empty_corpus_returns_empty_queue(self, client):
        resp = client.get("/api/entities/review-queue")
        assert resp.status_code == 200
        assert resp.json() == {"count": 0, "flags": []}


class TestCorrectionEndpoint:
    def test_blacklist_correction_resets_and_enqueues(self, client, app_config, app_state):
        _, episode_id = _seed_wrong_resolution(app_config.database_path)
        resp = client.post(
            "/api/entities/corrections",
            json={"action": "blacklist", "surface_form": "Anthropic", "wrong_qid": "Q240581", "reason": "cosmology"},
        )
        assert resp.status_code == 200, resp.text
        body = resp.json()
        assert body["status"] == "ok"
        assert body["affected_mentions"] == 3
        assert body["episodes_enqueued"] == 1
        assert "Q240581" in body["golden_snippet"]

        repo = app_state.entity_repository
        assert repo.is_blacklisted("Anthropic", "Q240581")
        # All three mentions are back to pending for re-resolution...
        assert len(repo.list_pending_mentions(episode_id=episode_id)) == 3
        # ...and a resolve-entities task was queued for the episode.
        assert app_state.queue_manager.has_pending_task(episode_id, TaskStage.RESOLVE_ENTITIES)

    def test_missing_wrong_qid_is_a_400(self, client):
        resp = client.post(
            "/api/entities/corrections",
            json={"action": "blacklist", "surface_form": "Anthropic"},
        )
        assert resp.status_code == 400

    def test_force_entity_unknown_target_is_a_400(self, client, app_config):
        _seed_wrong_resolution(app_config.database_path)
        resp = client.post(
            "/api/entities/corrections",
            json={"action": "force_entity", "surface_form": "Anthropic", "target_entity_id": "company:nope"},
        )
        assert resp.status_code == 400
