# Copyright 2025-2026 Thestill
#
# Licensed under the Apache License, Version 2.0 (the "License");
# you may not use this file except in compliance with the License.
# You may obtain a copy of the License at
#
#     http://www.apache.org/licenses/LICENSE-2.0
#
# Unless required by applicable law or agreed to in writing, software
# distributed under the License is distributed on an "AS IS" BASIS,
# WITHOUT WARRANTIES OR CONDITIONS OF ANY KIND, either express or implied.
# See the License for the specific language governing permissions and
# limitations under the License.

"""CliRunner smoke tests for ``thestill reindex``.

Targets the post-summarize indexing gap surfaced after #36 landed: 104 of
612 summarized episodes had no chunks. Most were unfixable (no JSON
sidecar — they need re-cleaning). The fixable subset is what
``thestill reindex`` covers — it enqueues ``extract-entities`` for
episodes that are summarized + have a sidecar but lack chunks/mentions.
"""

from __future__ import annotations

import sqlite3
import uuid

import pytest
from click.testing import CliRunner

from thestill.cli import main
from thestill.core.queue_manager import QueueManager
from thestill.repositories.sqlite_podcast_repository import SqlitePodcastRepository
from thestill.utils.sqlite_ext import maybe_load_vec_extension

# Inserts into ``chunks`` fire ``chunks_ai`` AFTER INSERT triggers that
# fan out into the vec0 virtual table — without the extension loaded the
# inserts fail with ``no such module: vec0``.
pytest.importorskip("sqlite_vec", reason="sqlite-vec extension required")


@pytest.fixture
def cli_db(tmp_path, monkeypatch):
    storage = tmp_path / "data"
    storage.mkdir()
    monkeypatch.setenv("STORAGE_PATH", str(storage))
    monkeypatch.setenv("THESTILL_ENV_FILE", str(tmp_path / ".no-such-env"))

    db_path = storage / "podcasts.db"
    SqlitePodcastRepository(db_path=str(db_path))
    # QueueManager creates the ``tasks`` table on init.
    QueueManager(str(db_path))
    return str(db_path)


def _make_episode(
    db_path: str,
    *,
    title: str,
    summary_path: str | None = "/tmp/summary.md",
    sidecar: bool = True,
    chunks: int = 0,
    mentions: int = 0,
) -> str:
    """Insert an episode + its podcast and optionally seed chunks/mentions."""
    podcast_id = str(uuid.uuid4())
    ep_id = str(uuid.uuid4())
    conn = sqlite3.connect(db_path)
    maybe_load_vec_extension(conn)
    try:
        conn.execute(
            "INSERT INTO podcasts (id, rss_url, title, slug) VALUES (?, ?, ?, ?)",
            (podcast_id, f"https://example.com/{podcast_id}.xml", title, podcast_id[:8]),
        )
        conn.execute(
            """
            INSERT INTO episodes (
                id, podcast_id, external_id, title, slug, description,
                description_html, audio_url, summary_path, clean_transcript_json_path
            ) VALUES (?, ?, ?, ?, '', '', '', ?, ?, ?)
            """,
            (
                ep_id,
                podcast_id,
                f"ext-{title}",
                title,
                f"https://cdn.example.com/{title}.mp3",
                summary_path,
                f"sidecar/{title}.json" if sidecar else None,
            ),
        )
        # ``chunks.id`` is INTEGER PRIMARY KEY AUTOINCREMENT — let the
        # DB assign it. ``embedding`` is a 384-dim float32 vec0 cell;
        # the AFTER INSERT trigger validates the size, so a zero-filled
        # blob of the right length is enough for these tests.
        zero_embedding = b"\x00" * (384 * 4)
        for i in range(chunks):
            conn.execute(
                """
                INSERT INTO chunks (episode_id, segment_id, start_ms, end_ms, text, embedding_model, embedding)
                VALUES (?, ?, 0, 1000, ?, ?, ?)
                """,
                (
                    ep_id,
                    i,
                    f"chunk-{i}",
                    "sentence-transformers/paraphrase-multilingual-MiniLM-L12-v2",
                    zero_embedding,
                ),
            )
        for i in range(mentions):
            conn.execute(
                """
                INSERT INTO entity_mentions (
                    episode_id, segment_id, start_ms, end_ms, surface_form, quote_excerpt, confidence, extractor
                ) VALUES (?, ?, 0, 1000, ?, ?, 0.9, 'gliner')
                """,
                (ep_id, i, f"surface-{i}", f"quote-{i}"),
            )
        conn.commit()
    finally:
        conn.close()
    return ep_id


def _enqueued_extract_episode_ids(db_path: str) -> set[str]:
    conn = sqlite3.connect(db_path)
    try:
        rows = conn.execute("SELECT episode_id FROM tasks WHERE stage = 'extract-entities'").fetchall()
    finally:
        conn.close()
    return {r[0] for r in rows}


def test_reindex_dry_run_writes_nothing(cli_db):
    ep_id = _make_episode(cli_db, title="needs-indexing")

    result = CliRunner().invoke(main, ["reindex", "--dry-run"])

    assert result.exit_code == 0, result.output
    assert "Fixable (has JSON sidecar): 1" in result.output
    assert _enqueued_extract_episode_ids(cli_db) == set()


def test_reindex_enqueues_extract_entities_for_fixable_episodes(cli_db):
    ep_indexed = _make_episode(cli_db, title="indexed", chunks=2, mentions=1)
    ep_missing = _make_episode(cli_db, title="missing-both")

    result = CliRunner().invoke(main, ["reindex"])

    assert result.exit_code == 0, result.output
    enqueued = _enqueued_extract_episode_ids(cli_db)
    assert ep_missing in enqueued
    assert ep_indexed not in enqueued


def test_reindex_skips_legacy_episodes_without_sidecar(cli_db):
    """Legacy episodes (no JSON sidecar) are reported but not enqueued —
    re-enqueueing would just hit the ``skipped_legacy`` no-op in the
    handler."""
    legacy = _make_episode(cli_db, title="legacy", sidecar=False)

    result = CliRunner().invoke(main, ["reindex"])

    assert result.exit_code == 0, result.output
    assert "Legacy (no sidecar — needs re-cleaning): 1" in result.output
    assert legacy not in _enqueued_extract_episode_ids(cli_db)


def test_reindex_status_missing_chunks_only_targets_chunkless(cli_db):
    has_chunks_no_mentions = _make_episode(cli_db, title="chunks-only", chunks=2)
    has_mentions_no_chunks = _make_episode(cli_db, title="mentions-only", mentions=1)

    result = CliRunner().invoke(main, ["reindex", "--status", "missing-chunks"])

    assert result.exit_code == 0, result.output
    enqueued = _enqueued_extract_episode_ids(cli_db)
    # Only the episode without chunks gets enqueued, regardless of mentions.
    assert has_mentions_no_chunks in enqueued
    assert has_chunks_no_mentions not in enqueued


def test_reindex_status_missing_mentions_only_targets_mentionless(cli_db):
    has_chunks_no_mentions = _make_episode(cli_db, title="chunks-only", chunks=2)
    has_mentions_no_chunks = _make_episode(cli_db, title="mentions-only", mentions=1)

    result = CliRunner().invoke(main, ["reindex", "--status", "missing-mentions"])

    assert result.exit_code == 0, result.output
    enqueued = _enqueued_extract_episode_ids(cli_db)
    assert has_chunks_no_mentions in enqueued
    assert has_mentions_no_chunks not in enqueued


def test_reindex_excludes_unsummarized_episodes(cli_db):
    """An episode without ``summary_path`` is not yet user-chain-complete;
    reindex should leave it alone."""
    pending = _make_episode(cli_db, title="pending", summary_path=None)

    result = CliRunner().invoke(main, ["reindex"])

    assert result.exit_code == 0, result.output
    assert pending not in _enqueued_extract_episode_ids(cli_db)


def test_reindex_max_episodes_caps_enqueue_count(cli_db):
    eps = [_make_episode(cli_db, title=f"ep-{i}") for i in range(3)]

    result = CliRunner().invoke(main, ["reindex", "--max-episodes", "2"])

    assert result.exit_code == 0, result.output
    enqueued = _enqueued_extract_episode_ids(cli_db)
    assert len(enqueued) == 2
    assert all(ep in {*eps} for ep in enqueued)
