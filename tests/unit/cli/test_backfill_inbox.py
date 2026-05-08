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

"""CliRunner smoke tests for ``thestill backfill-inbox``."""

from __future__ import annotations

import sqlite3
import uuid
from datetime import datetime, timezone

import pytest
from click.testing import CliRunner

from thestill.cli import main
from thestill.repositories.sqlite_inbox_repository import SqliteInboxRepository
from thestill.repositories.sqlite_podcast_follower_repository import (
    SqlitePodcastFollowerRepository,
)
from thestill.repositories.sqlite_podcast_repository import SqlitePodcastRepository


@pytest.fixture
def cli_db(tmp_path, monkeypatch):
    """Stand up a tmp DB and point the CLI at it via env vars.

    Mirrors the pattern in test_entity_cli.py: STORAGE_PATH is read by
    ``load_config`` and THESTILL_ENV_FILE is poisoned so the CLI's
    ``load_dotenv()`` call doesn't overwrite our test env from a
    developer ``.env``.
    """
    storage = tmp_path / "data"
    storage.mkdir()
    monkeypatch.setenv("STORAGE_PATH", str(storage))
    monkeypatch.setenv("THESTILL_ENV_FILE", str(tmp_path / ".no-such-env"))
    monkeypatch.setenv("INBOX_SEED_ON_FOLLOW", "2")

    db_path = storage / "podcasts.db"
    SqlitePodcastRepository(db_path=str(db_path))
    return str(db_path)


def _seed_user_and_podcast(db_path):
    user_id = str(uuid.uuid4())
    podcast_id = str(uuid.uuid4())
    conn = sqlite3.connect(db_path)
    try:
        conn.execute(
            "INSERT INTO users (id, email, name) VALUES (?, ?, ?)",
            (user_id, "alice@example.com", "Alice"),
        )
        conn.execute(
            "INSERT INTO podcasts (id, rss_url, title, slug) VALUES (?, ?, ?, ?)",
            (podcast_id, "https://example.com/feed.xml", "P", "p"),
        )
        conn.commit()
    finally:
        conn.close()
    return user_id, podcast_id


def _publish_episode(db_path, podcast_id, title):
    ep_id = str(uuid.uuid4())
    now = datetime.now(timezone.utc).isoformat()
    conn = sqlite3.connect(db_path)
    try:
        conn.execute(
            """
            INSERT INTO episodes (
                id, podcast_id, external_id, title, slug, description,
                description_html, audio_url, published_at
            ) VALUES (?, ?, ?, ?, '', '', '', ?, ?)
            """,
            (
                ep_id,
                podcast_id,
                f"ext-{title}",
                title,
                f"https://cdn.example.com/{title}.mp3",
                now,
            ),
        )
        conn.commit()
    finally:
        conn.close()
    return ep_id


def test_backfill_inbox_dry_run_writes_nothing(cli_db):
    user_id, podcast_id = _seed_user_and_podcast(cli_db)
    _publish_episode(cli_db, podcast_id, "ep1")
    SqlitePodcastFollowerRepository(cli_db)._get_connection  # eager wiring sanity
    conn = sqlite3.connect(cli_db)
    conn.execute(
        "INSERT INTO podcast_followers (id, user_id, podcast_id) VALUES (?, ?, ?)",
        (str(uuid.uuid4()), user_id, podcast_id),
    )
    conn.commit()
    conn.close()

    result = CliRunner().invoke(main, ["backfill-inbox", "--dry-run"])

    assert result.exit_code == 0, result.output
    assert "would seed" in result.output

    inbox = SqliteInboxRepository(cli_db)
    assert inbox.list_items(user_id) == []


def test_backfill_inbox_delivers_to_existing_followers(cli_db):
    user_id, podcast_id = _seed_user_and_podcast(cli_db)
    ep1 = _publish_episode(cli_db, podcast_id, "ep1")
    ep2 = _publish_episode(cli_db, podcast_id, "ep2")

    conn = sqlite3.connect(cli_db)
    conn.execute(
        "INSERT INTO podcast_followers (id, user_id, podcast_id) VALUES (?, ?, ?)",
        (str(uuid.uuid4()), user_id, podcast_id),
    )
    conn.commit()
    conn.close()

    result = CliRunner().invoke(main, ["backfill-inbox"])

    assert result.exit_code == 0, result.output
    assert "Backfill complete" in result.output

    inbox = SqliteInboxRepository(cli_db)
    delivered = {item.entry.episode_id for item in inbox.list_items(user_id)}
    assert delivered == {ep1, ep2}


def test_backfill_inbox_is_idempotent(cli_db):
    user_id, podcast_id = _seed_user_and_podcast(cli_db)
    _publish_episode(cli_db, podcast_id, "ep1")

    conn = sqlite3.connect(cli_db)
    conn.execute(
        "INSERT INTO podcast_followers (id, user_id, podcast_id) VALUES (?, ?, ?)",
        (str(uuid.uuid4()), user_id, podcast_id),
    )
    conn.commit()
    conn.close()

    runner = CliRunner()
    runner.invoke(main, ["backfill-inbox"])
    second = runner.invoke(main, ["backfill-inbox"])

    assert second.exit_code == 0
    # ON CONFLICT DO NOTHING means the second run inserts 0 rows.
    assert "0 inbox rows delivered" in second.output

    inbox = SqliteInboxRepository(cli_db)
    assert len(inbox.list_items(user_id)) == 1
