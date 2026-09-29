"""HTTP-level tests for ``GET /api/search/corpus`` grouped by episode.

The results page asks for ``per_episode`` so one episode that says the term
thirty times can't fill the page, and for per-episode ``match_counts`` so each
card can say how many times it came up; ``episode_id`` loads one episode's
moments when a card is expanded.
"""

from __future__ import annotations

import sqlite3
import struct
import uuid

import pytest

pytest.importorskip("sqlite_vec", reason="sqlite-vec extension required")
np = pytest.importorskip("numpy", reason="numpy required for embedding tests")

from thestill.core.chunk_writer import ChunkWriter
from thestill.core.embedding_model import EmbeddingModel
from thestill.models.annotated_transcript import AnnotatedSegment, AnnotatedTranscript
from thestill.search.base import DEFAULT_EMBEDDING_MODEL, embedding_dim_for
from thestill.search.sqlite_vec_client import SqliteVecBackend

_DIM = embedding_dim_for(DEFAULT_EMBEDDING_MODEL)

FLOOD = "11111111-2222-3333-4444-555555555555"
ONCE = "33333333-4444-5555-6666-777777777777"


class _StubEmbeddingModel(EmbeddingModel):
    def __init__(self):
        self.model_name = DEFAULT_EMBEDDING_MODEL
        self.dim = _DIM

    def encode_one(self, text: str) -> bytes:  # type: ignore[override]
        rng = np.random.default_rng(hash(text) % 2**31)
        v = rng.standard_normal(_DIM).astype(np.float32)
        v /= np.linalg.norm(v) or 1.0
        return struct.pack(f"<{_DIM}f", *v)

    def encode_batch(self, texts, *, batch_size: int = 64):  # type: ignore[override]
        return [self.encode_one(t) for t in texts]


def _seed(app_state) -> None:
    db_path = str(app_state.repository.db_path)
    podcast_id = str(uuid.uuid4())
    with sqlite3.connect(db_path) as conn:
        conn.execute(
            "INSERT INTO podcasts (id, rss_url, title, slug) VALUES (?, ?, ?, ?)",
            (podcast_id, "https://example.com/show.xml", "The Show", "the-show"),
        )
        for eid, title in [(FLOOD, "All About Legora"), (ONCE, "Other Things")]:
            conn.execute(
                """INSERT INTO episodes (id, podcast_id, external_id, title, slug, audio_url, pub_date)
                   VALUES (?, ?, ?, ?, ?, ?, '2026-04-01T00:00:00')""",
                (eid, podcast_id, f"ext-{eid[:8]}", title, title.lower().replace(" ", "-"), "https://e.com/a.mp3"),
            )
        conn.commit()
    model = _StubEmbeddingModel()
    writer = ChunkWriter(db_path=db_path, embedding_model=model)
    segments = {
        FLOOD: [f"legora legora legora again in part {i}" for i in range(8)],
        ONCE: ["they also mentioned legora once in passing today"],
    }
    for eid, texts in segments.items():
        writer.write_episode(
            eid,
            AnnotatedTranscript(
                episode_id=eid,
                segments=[
                    AnnotatedSegment(id=i, start=float(i), end=i + 1.0, text=t, speaker="Host", kind="content")
                    for i, t in enumerate(texts)
                ],
            ),
        )
    app_state.search_backend = SqliteVecBackend(db_path=db_path, embedding_model=model)


class TestCorpusGroupedByEpisode:
    def test_ungrouped_request_is_unchanged(self, client, app_state):
        _seed(app_state)
        body = client.get("/api/search/corpus", params={"q": "legora", "limit": 3}).json()
        assert {r["episode_id"] for r in body["results"]} == {FLOOD}
        assert body["match_counts"] is None

    def test_per_episode_caps_hits_and_counts_every_mention(self, client, app_state):
        _seed(app_state)
        r = client.get("/api/search/corpus", params={"q": "legora", "limit": 3, "per_episode": 2})
        assert r.status_code == 200, r.text
        body = r.json()
        episodes = [row["episode_id"] for row in body["results"]]
        assert episodes.count(FLOOD) == 2
        assert ONCE in episodes
        assert body["match_counts"] == {FLOOD: 8, ONCE: 1}
        assert body["results"][0]["episode_slug"] == "all-about-legora"

    def test_episode_id_returns_that_episodes_moments(self, client, app_state):
        _seed(app_state)
        body = client.get("/api/search/corpus", params={"q": "legora", "limit": 50, "episode_id": FLOOD}).json()
        assert len(body["results"]) == 8
        assert {row["episode_id"] for row in body["results"]} == {FLOOD}

    def test_bad_episode_id_and_cap_are_rejected(self, client, app_state):
        _seed(app_state)
        assert client.get("/api/search/corpus", params={"q": "legora", "episode_id": "nope"}).status_code == 422
        assert client.get("/api/search/corpus", params={"q": "legora", "per_episode": 0}).status_code == 422
