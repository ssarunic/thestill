"""Spec #28 §5.2 — HTTP-level tests for ``GET /api/search/related``.

The endpoint reads the precomputed ``episode_related`` table (built
offline by ``search.related_builder``; unit-tested separately). These
tests seed that table directly and assert the wire shape: rank order,
slug hydration for deep links, skipping rows that aren't deep-linkable,
the ``limit`` cap, and the empty case when nothing is precomputed.
"""

from __future__ import annotations

import sqlite3
import uuid

PODCAST_ID = str(uuid.uuid4())
SRC = "11111111-2222-3333-4444-555555555555"
REL1 = "22222222-3333-4444-5555-666666666666"
REL2 = "33333333-4444-5555-6666-777777777777"


def _seed_episodes(db_path: str) -> None:
    with sqlite3.connect(db_path) as conn:
        conn.execute(
            "INSERT INTO podcasts (id, rss_url, title, slug, image_url) VALUES (?, ?, ?, ?, ?)",
            (PODCAST_ID, "https://example.com/p.xml", "Prof G Markets", "prof-g-markets", "https://img/p.jpg"),
        )
        for eid, title, slug in [
            (SRC, "Source Episode", "source-episode"),
            (REL1, "AI Capex Cliff", "ai-capex-cliff"),
            (REL2, "Bond Investors Panic", "bond-investors-panic"),
        ]:
            conn.execute(
                "INSERT INTO episodes (id, podcast_id, external_id, title, slug, audio_url, pub_date) "
                "VALUES (?, ?, ?, ?, ?, ?, ?)",
                (eid, PODCAST_ID, f"ext-{slug}", title, slug, f"https://e/{slug}.mp3", "2026-03-14T00:00:00"),
            )
        conn.commit()


def _seed_related(db_path: str, pairs) -> None:
    """pairs: list of (related_episode_id, rank, score)."""
    with sqlite3.connect(db_path) as conn:
        conn.executemany(
            "INSERT INTO episode_related (episode_id, related_episode_id, rank, score) VALUES (?, ?, ?, ?)",
            [(SRC, rel, rank, score) for rel, rank, score in pairs],
        )
        conn.commit()


class TestRelatedEndpoint:
    def test_missing_episode_id_rejected(self, client, app_state):
        r = client.get("/api/search/related")
        assert r.status_code == 422

    def test_empty_when_no_precomputed_rows(self, client, app_state):
        _seed_episodes(str(app_state.repository.db_path))
        r = client.get("/api/search/related", params={"episode_id": SRC})
        assert r.status_code == 200
        assert r.json() == {"episode_id": SRC, "episodes": []}

    def test_returns_related_in_rank_order_with_slugs(self, client, app_state):
        db = str(app_state.repository.db_path)
        _seed_episodes(db)
        _seed_related(db, [(REL2, 0, 0.91), (REL1, 1, 0.54)])
        r = client.get("/api/search/related", params={"episode_id": SRC})
        assert r.status_code == 200, r.text
        body = r.json()
        assert body["episode_id"] == SRC
        assert [e["episode_id"] for e in body["episodes"]] == [REL2, REL1]  # rank order, not score insertion order
        card = body["episodes"][0]
        assert card["podcast_slug"] == "prof-g-markets"
        assert card["episode_slug"] == "bond-investors-panic"
        assert card["episode_title"] == "Bond Investors Panic"
        assert card["podcast_title"] == "Prof G Markets"
        assert card["score"] == 0.91
        # Artwork falls back to podcast image when the episode has none.
        assert card["image_url"] == "https://img/p.jpg"

    def test_skips_related_without_slug(self, client, app_state):
        db = str(app_state.repository.db_path)
        _seed_episodes(db)
        # A related episode without a usable slug isn't deep-linkable and
        # must be dropped rather than rendered broken (empty-string slug
        # is the realizable form — the column is NOT NULL).
        noslug = str(uuid.uuid4())
        with sqlite3.connect(db) as conn:
            conn.execute(
                "INSERT INTO episodes (id, podcast_id, external_id, title, slug, audio_url, pub_date) "
                "VALUES (?, ?, 'ext-noslug', 'Legacy', '', 'https://e/legacy.mp3', '2026-01-01T00:00:00')",
                (noslug, PODCAST_ID),
            )
            conn.commit()
        _seed_related(db, [(noslug, 0, 0.9), (REL1, 1, 0.5)])
        r = client.get("/api/search/related", params={"episode_id": SRC})
        ids = [e["episode_id"] for e in r.json()["episodes"]]
        assert ids == [REL1]

    def test_limit_caps_results(self, client, app_state):
        db = str(app_state.repository.db_path)
        _seed_episodes(db)
        _seed_related(db, [(REL2, 0, 0.9), (REL1, 1, 0.5)])
        r = client.get("/api/search/related", params={"episode_id": SRC, "limit": 1})
        assert len(r.json()["episodes"]) == 1
        assert r.json()["episodes"][0]["episode_id"] == REL2
