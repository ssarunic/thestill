# Copyright 2025-2026 Thestill
#
# Licensed under the Apache License, Version 2.0 (the "License");
# you may not use this file except in compliance with the License.
# You may obtain a copy of the License at
#
#     http://www.apache.org/licenses/LICENSE-2.0

"""Spec #28 Phase 3.3 — fixture infra for the latency budget gate.

Builds a small (~10 episode, ~500 chunk, ~200 mention) reproducible
SQLite corpus once per session. Embeddings come from a deterministic
stub model — encoder runtime is model-fixed and not what spec #28's
latency budget targets; we want to gate sqlite-vec / SQL regressions.
"""

from __future__ import annotations

import sqlite3
import struct
import uuid
from typing import List

import pytest

pytest.importorskip("sqlite_vec", reason="sqlite-vec extension required")
np = pytest.importorskip("numpy", reason="numpy required for embedding tests")

from thestill.core.embedding_model import EmbeddingModel
from thestill.repositories.sqlite_podcast_repository import SqlitePodcastRepository
from thestill.search.base import DEFAULT_EMBEDDING_MODEL, embedding_dim_for
from thestill.utils.sqlite_ext import maybe_load_vec_extension

_DIM = embedding_dim_for(DEFAULT_EMBEDDING_MODEL)


def _vec(seed: int) -> bytes:
    """Deterministic L2-normalised embedding from a seed."""
    rng = np.random.default_rng(seed)
    v = rng.standard_normal(_DIM).astype(np.float32)
    v /= np.linalg.norm(v) or 1.0
    return struct.pack(f"<{_DIM}f", *v)


class StubEmbeddingModel(EmbeddingModel):
    """Hash-seeded fake embedding model.

    ``encode_one`` returns a deterministic vector for the same text on
    every call across the test session, so semantic matches are
    repeatable without needing the real sentence-transformers weights.
    """

    def __init__(self):
        self.model_name = DEFAULT_EMBEDDING_MODEL
        self.dim = _DIM

    def encode_one(self, text: str) -> bytes:  # type: ignore[override]
        return _vec(hash(text) % 2**31)

    def encode_batch(self, texts, *, batch_size: int = 64):  # type: ignore[override]
        return [self.encode_one(t) for t in texts]


# Fixture corpus shape — matches the spec #28 §1316 latency-budget
# context (10-episode fixture). Five entities, four podcasts, sentence
# templates that produce realistic FTS5 / cosine-similarity workloads.
_PODCASTS = [
    ("Prof G Markets", "prof-g-markets"),
    ("All-In Podcast", "all-in-podcast"),
    ("Dwarkesh Patel", "dwarkesh-patel"),
    ("Acquired", "acquired"),
]

_ENTITIES = [
    ("person:elon-musk", "person", "Elon Musk", "Q317521"),
    ("person:scott-galloway", "person", "Scott Galloway", None),
    ("company:spacex", "company", "SpaceX", "Q193701"),
    ("company:openai", "company", "OpenAI", "Q21708200"),
    ("topic:ai-infrastructure", "topic", "AI infrastructure", None),
]

_TEXT_TEMPLATES = [
    "We were talking about {entity} and the implications for the broader market this quarter.",
    "{entity} announced a new initiative that could reshape the competitive landscape.",
    "The conversation turned to {entity}, with both hosts agreeing on the trajectory.",
    "Looking back at {entity}'s history, the pattern is hard to miss.",
    "If you're investing in this space, {entity} is the bellwether you should watch.",
    "What does {entity} mean for the next twelve months? That's the question on everyone's mind.",
    "I disagree with the consensus view on {entity}, and here's why.",
    "{entity} represents a structural shift, not a cyclical one.",
]


def _build_fixture_corpus(db_path: str, *, episodes: int = 10, chunks_per_episode: int = 50) -> dict:
    """Populate ``db_path`` with a reproducible spec #28 fixture.

    Returns metadata describing the fixture (entity ids, episode ids, a
    podcast id) so tests can address rows directly.
    """
    SqlitePodcastRepository(db_path=db_path)  # runs migrations

    rng_seed = 42
    rng = np.random.default_rng(rng_seed)

    podcast_ids = [str(uuid.uuid4()) for _ in _PODCASTS]
    episode_ids = [str(uuid.uuid4()) for _ in range(episodes)]

    with sqlite3.connect(db_path) as conn:
        maybe_load_vec_extension(conn)
        conn.execute("PRAGMA foreign_keys = ON")

        for (title, slug), pid in zip(_PODCASTS, podcast_ids):
            conn.execute(
                "INSERT INTO podcasts (id, rss_url, title, slug) VALUES (?, ?, ?, ?)",
                (pid, f"https://example.com/{slug}.xml", title, slug),
            )

        # Round-robin episodes across podcasts so podcast_id filters
        # have something to bite on.
        for idx, eid in enumerate(episode_ids):
            podcast_id = podcast_ids[idx % len(podcast_ids)]
            pub_date = f"2026-{(idx % 12) + 1:02d}-{(idx % 27) + 1:02d}T00:00:00+00:00"
            conn.execute(
                "INSERT INTO episodes "
                "(id, podcast_id, external_id, title, audio_url, pub_date) "
                "VALUES (?, ?, ?, ?, ?, ?)",
                (eid, podcast_id, f"ext-{idx}", f"Episode {idx + 1}", f"https://example.com/e{idx}.mp3", pub_date),
            )

        # Entities — corpus-global rows so MCP tools have something to
        # return. Aliases stored as JSON string per the migration's
        # canonical form.
        for ent_id, ent_type, name, qid in _ENTITIES:
            conn.execute(
                "INSERT INTO entities (id, type, canonical_name, wikidata_qid, aliases, created_at, updated_at) "
                "VALUES (?, ?, ?, ?, ?, datetime('now'), datetime('now'))",
                (ent_id, ent_type, name, qid, "[]"),
            )

        # Chunks — text + embedding per chunk. The triggers fan out
        # into chunks_vec / chunks_fts automatically.
        chunk_seed = 0
        for ep_idx, eid in enumerate(episode_ids):
            for seg_idx in range(chunks_per_episode):
                ent_id, _ent_type, ent_name, _qid = _ENTITIES[seg_idx % len(_ENTITIES)]
                template = _TEXT_TEMPLATES[seg_idx % len(_TEXT_TEMPLATES)]
                text = template.format(entity=ent_name)
                start_ms = seg_idx * 30_000
                end_ms = start_ms + 25_000
                conn.execute(
                    "INSERT INTO chunks "
                    "(episode_id, segment_id, start_ms, end_ms, speaker, text, embedding_model, embedding) "
                    "VALUES (?, ?, ?, ?, ?, ?, ?, ?)",
                    (
                        eid,
                        seg_idx,
                        start_ms,
                        end_ms,
                        f"speaker-{seg_idx % 2}",
                        text,
                        DEFAULT_EMBEDDING_MODEL,
                        _vec(chunk_seed),
                    ),
                )
                chunk_seed += 1

        # Mentions — ~4 per episode = 40 total, all resolved (so the
        # find_mentions / list_quotes_by paths return rows and are not
        # short-circuited by the WHERE resolution_status='resolved' clause).
        # ``id`` is INTEGER PRIMARY KEY AUTOINCREMENT, so we omit it and
        # let SQLite assign one. Confidence/extractor/quote_excerpt are
        # NOT NULL.
        for ep_idx, eid in enumerate(episode_ids):
            for k in range(4):
                ent_id, _ent_type, ent_name, _qid = _ENTITIES[(ep_idx + k) % len(_ENTITIES)]
                template = _TEXT_TEMPLATES[k % len(_TEXT_TEMPLATES)]
                conn.execute(
                    "INSERT INTO entity_mentions "
                    "(episode_id, segment_id, surface_form, start_ms, end_ms, "
                    "entity_id, resolution_status, speaker, role, "
                    "quote_excerpt, confidence, extractor) "
                    "VALUES (?, ?, ?, ?, ?, ?, 'resolved', ?, 'mentioned', ?, ?, 'gliner-perf-stub')",
                    (
                        eid,
                        k,
                        ent_name,
                        k * 1000,
                        k * 1000 + 500,
                        ent_id,
                        f"speaker-{k % 2}",
                        template.format(entity=ent_name),
                        0.9,
                    ),
                )

        conn.commit()

    # Help SQLite plan queries well. The ANALYZE here is what production
    # would have after thousands of inserts — without it the lexical
    # plan can pick a worse path on small fixtures and inflate timings.
    with sqlite3.connect(db_path) as conn:
        conn.execute("ANALYZE")
        conn.commit()

    return {
        "podcast_id": podcast_ids[0],
        "episode_ids": episode_ids,
        "entity_ids": [e[0] for e in _ENTITIES],
        "chunks": episodes * chunks_per_episode,
    }


@pytest.fixture(scope="session")
def fixture_corpus_db(tmp_path_factory) -> dict:
    """Build the spec #28 latency fixture once per session.

    Returns a dict with keys: ``db_path``, ``podcast_id``,
    ``episode_ids``, ``entity_ids``, ``chunks``.
    """
    db_path = str(tmp_path_factory.mktemp("perf") / "fixture_corpus.db")
    meta = _build_fixture_corpus(db_path)
    meta["db_path"] = db_path
    return meta


@pytest.fixture(scope="session")
def stub_embedding_model() -> StubEmbeddingModel:
    return StubEmbeddingModel()
