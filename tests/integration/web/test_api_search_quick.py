"""Spec #28 §4.1 — HTTP-level tests for ``GET /api/search/quick``.

Tests the wire shape, group ordering, the lexical-mode pin (Strategy
§2 — never silently upgrade), filter pushdown, and 503 when the
backend isn't initialised. Backend search uses a stub embedding model
because this endpoint never goes near vectors.
"""

from __future__ import annotations

import sqlite3
import struct
import uuid
from typing import List

import pytest

pytest.importorskip("sqlite_vec", reason="sqlite-vec extension required")
np = pytest.importorskip("numpy", reason="numpy required for embedding tests")

from thestill.core.chunk_writer import ChunkWriter
from thestill.core.embedding_model import EmbeddingModel
from thestill.models.annotated_transcript import AnnotatedSegment, AnnotatedTranscript
from thestill.models.entities import EntityMention, EntityRecord, EntityType, MentionRole
from thestill.search.base import DEFAULT_EMBEDDING_MODEL, embedding_dim_for
from thestill.search.sqlite_vec_client import SqliteVecBackend

_DIM = embedding_dim_for(DEFAULT_EMBEDDING_MODEL)


def _vec(seed: int) -> bytes:
    rng = np.random.default_rng(seed)
    v = rng.standard_normal(_DIM).astype(np.float32)
    v /= np.linalg.norm(v) or 1.0
    return struct.pack(f"<{_DIM}f", *v)


class _StubEmbeddingModel(EmbeddingModel):
    """Records every encode_one call so tests can assert that the
    lexical path never reaches the model (Strategy §2)."""

    def __init__(self):
        self.model_name = DEFAULT_EMBEDDING_MODEL
        self.dim = _DIM
        self.calls: List[str] = []

    def encode_one(self, text: str) -> bytes:  # type: ignore[override]
        self.calls.append(text)
        return _vec(hash(text) % 2**31)

    def encode_batch(self, texts, *, batch_size: int = 64):  # type: ignore[override]
        return [self.encode_one(t) for t in texts]


def _seed(app_state) -> dict:
    """Seed two podcasts, three episodes, three entities, mentions,
    and chunks so the lexical search returns rows. Returns a dict of
    handy ids the tests reference.
    """
    db_path = str(app_state.repository.db_path)
    podcasts = {
        "p1": {"id": str(uuid.uuid4()), "title": "Prof G Markets", "slug": "prof-g-markets"},
        "p2": {"id": str(uuid.uuid4()), "title": "All-In", "slug": "all-in"},
    }
    episodes = {
        "e1": {
            "id": "11111111-2222-3333-4444-555555555555",
            "podcast_key": "p1",
            "title": "Musk and SpaceX IPO",
            "slug": "musk-and-spacex-ipo",
            "pub_date": "2026-04-28T00:00:00",
        },
        "e2": {
            "id": "22222222-3333-4444-5555-666666666666",
            "podcast_key": "p1",
            "title": "AI Capex Cliff",
            "slug": "ai-capex-cliff",
            "pub_date": "2026-03-14T00:00:00",
        },
        "e3": {
            "id": "33333333-4444-5555-6666-777777777777",
            "podcast_key": "p2",
            "title": "Tesla Earnings",
            "slug": "tesla-earnings",
            "pub_date": "2026-02-02T00:00:00",
        },
    }
    with sqlite3.connect(db_path) as conn:
        for p in podcasts.values():
            conn.execute(
                "INSERT INTO podcasts (id, rss_url, title, slug) VALUES (?, ?, ?, ?)",
                (p["id"], f"https://example.com/{p['slug']}.xml", p["title"], p["slug"]),
            )
        for e in episodes.values():
            conn.execute(
                """
                INSERT INTO episodes (id, podcast_id, external_id, title, slug, audio_url, pub_date)
                VALUES (?, ?, ?, ?, ?, ?, ?)
                """,
                (
                    e["id"],
                    podcasts[e["podcast_key"]]["id"],
                    f"ext-{e['id'][:8]}",
                    e["title"],
                    e["slug"],
                    f"https://example.com/{e['slug']}.mp3",
                    e["pub_date"],
                ),
            )
        conn.commit()

    repo = app_state.entity_repository
    repo.upsert_entity(
        EntityRecord(
            id="person:elon-musk",
            type=EntityType.PERSON,
            canonical_name="Elon Musk",
            aliases=["Musk"],
        )
    )
    repo.upsert_entity(EntityRecord(id="company:spacex", type=EntityType.COMPANY, canonical_name="SpaceX"))
    repo.upsert_entity(EntityRecord(id="company:tesla", type=EntityType.COMPANY, canonical_name="Tesla"))
    repo.upsert_entity(EntityRecord(id="topic:ai-capex", type=EntityType.TOPIC, canonical_name="AI Capex"))
    # Give Musk a couple of resolved mentions so mention_count is > 0.
    repo.insert_mentions(
        [
            EntityMention(
                episode_id=episodes["e1"]["id"],
                segment_id=1,
                start_ms=1000,
                end_ms=2000,
                speaker="Scott Galloway",
                role=MentionRole.MENTIONED,
                surface_form="Elon Musk",
                quote_excerpt="… Elon Musk …",
                confidence=0.95,
                extractor="gliner:test",
            )
        ]
    )
    pending = repo.list_pending_mentions()
    for m in pending:
        repo.resolve_mention(mention_id=m.id, entity_id="person:elon-musk", status="resolved")

    # Populate chunks so lexical search returns at least one row.
    embedding_model = _StubEmbeddingModel()
    writer = ChunkWriter(db_path=db_path, embedding_model=embedding_model)
    writer.write_episode(
        episodes["e1"]["id"],
        AnnotatedTranscript(
            episode_id=episodes["e1"]["id"],
            segments=[
                AnnotatedSegment(
                    id=1,
                    start=1.0,
                    end=2.0,
                    text="Elon Musk on the SpaceX IPO and the road ahead.",
                    speaker="Scott Galloway",
                    kind="content",
                ),
                AnnotatedSegment(
                    id=2,
                    start=2.0,
                    end=3.0,
                    text="Cooking dinner is fun.",
                    speaker="Scott Galloway",
                    kind="content",
                ),
            ],
        ),
    )

    # Backend wired onto AppState so the route picks it up.
    backend = SqliteVecBackend(db_path=db_path, embedding_model=embedding_model)
    app_state.search_backend = backend
    app_state.embedding_model = embedding_model
    return {"podcasts": podcasts, "episodes": episodes, "embedding_model": embedding_model}


class TestQuickEndpoint:
    def test_503_when_backend_missing(self, client, app_state):
        # No backend wired — endpoint should return 503.
        app_state.search_backend = None
        r = client.get("/api/search/quick", params={"q": "musk"})
        assert r.status_code == 503

    def test_empty_query_rejected(self, client, app_state):
        _seed(app_state)
        r = client.get("/api/search/quick", params={"q": ""})
        assert r.status_code == 422

    def test_groups_in_canonical_order(self, client, app_state):
        _seed(app_state)
        r = client.get("/api/search/quick", params={"q": "musk"})
        assert r.status_code == 200, r.text
        body = r.json()
        assert [g["type"] for g in body["groups"]] == ["episode", "person", "company", "topic", "quote"]
        assert body["query"] == "musk"
        assert body["see_all_url"] == "/search?q=musk"

    def test_finds_episode_by_title(self, client, app_state):
        seed = _seed(app_state)
        r = client.get("/api/search/quick", params={"q": "musk"})
        body = r.json()
        ep_group = next(g for g in body["groups"] if g["type"] == "episode")
        assert any(item["title"] == "Musk and SpaceX IPO" for item in ep_group["items"])
        # Episode rows expose enough to build a real /podcasts/:slug/episodes/:slug URL.
        item = next(i for i in ep_group["items"] if i["title"] == "Musk and SpaceX IPO")
        assert item["podcast_slug"] == "prof-g-markets"
        assert item["episode_slug"] == seed["episodes"]["e1"]["slug"]
        assert item["kind"] == "episode"

    def test_finds_person_with_alias_metadata(self, client, app_state):
        _seed(app_state)
        r = client.get("/api/search/quick", params={"q": "musk"})
        body = r.json()
        person_group = next(g for g in body["groups"] if g["type"] == "person")
        assert len(person_group["items"]) == 1
        item = person_group["items"][0]
        assert item["id"] == "person:elon-musk"
        assert item["name"] == "Elon Musk"
        assert item["entity_type"] == "person"
        assert item["mention_count"] == 1
        assert item["kind"] == "entity"

    def test_quote_group_includes_slug_for_deep_link(self, client, app_state):
        _seed(app_state)
        r = client.get("/api/search/quick", params={"q": "elon"})
        body = r.json()
        quote_group = next(g for g in body["groups"] if g["type"] == "quote")
        assert quote_group["items"], "lexical search should return at least one chunk for 'elon'"
        item = quote_group["items"][0]
        # Quote rows must carry slugs so the client never needs a follow-up fetch.
        assert item["podcast_slug"] == "prof-g-markets"
        assert item["episode_slug"] == "musk-and-spacex-ipo"
        assert item["start_ms"] == 1000
        assert item["kind"] == "quote"

    def test_quote_group_carries_audio_url_for_floating_player(self, client, app_state):
        """Spec #28 §4.1 — selecting a quote from ⌘K plays it inline
        through the FloatingPlayer. The row must carry ``audio_url`` so
        the CommandBar can hand it straight to ``player.play()`` without
        a second round-trip; missing audio_url silently falls back to a
        full navigation, which loses the user's current page.
        """
        _seed(app_state)
        r = client.get("/api/search/quick", params={"q": "elon"})
        body = r.json()
        quote_group = next(g for g in body["groups"] if g["type"] == "quote")
        assert quote_group["items"], "expected at least one quote hit"
        item = quote_group["items"][0]
        assert item["audio_url"], f"quote row missing audio_url: {item}"
        # Optional companions are still present in the wire shape so the
        # frontend can hand the full track to player.play() in one go.
        assert "image_url" in item
        assert "duration" in item

    def test_lexical_path_never_loads_embedding(self, client, app_state):
        seed = _seed(app_state)
        r = client.get("/api/search/quick", params={"q": "elon"})
        assert r.status_code == 200
        # Strategy §2: ⌘K is pinned to lexical and must never touch
        # the embedding model. Stub records every encode_one call —
        # only the chunk-write phase should appear, never the search.
        # The chunk_writer.write_episode call in _seed produced 2 calls
        # (one per segment); the search itself must not bump it.
        assert len(seed["embedding_model"].calls) == 2

    def test_podcast_id_filter_pushed_down(self, client, app_state):
        seed = _seed(app_state)
        # Filter to podcast p2 (All-In) — only Tesla Earnings should
        # surface in episodes; the entity hits are unaffected.
        r = client.get(
            "/api/search/quick",
            params={"q": "tesla", "podcast_id": seed["podcasts"]["p2"]["id"]},
        )
        body = r.json()
        ep_group = next(g for g in body["groups"] if g["type"] == "episode")
        assert all(i["podcast_id"] == seed["podcasts"]["p2"]["id"] for i in ep_group["items"])

    def test_limit_per_group_caps_results(self, client, app_state):
        _seed(app_state)
        r = client.get("/api/search/quick", params={"q": "tesla", "limit_per_group": 1})
        body = r.json()
        for group in body["groups"]:
            assert len(group["items"]) <= 1
