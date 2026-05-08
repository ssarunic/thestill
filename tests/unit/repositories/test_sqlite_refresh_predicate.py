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

"""
Spec #31 — ``get_podcasts_for_refresh()`` predicate.

Refresh must skip:
- synthetic parents (the bare-audio fallback);
- auto_added podcasts (real channel/feed rows inserted as a side-effect of
  an import) when no user follows them.

Once any user follows an auto_added podcast, it must show up in refresh
again.
"""

import sqlite3
import uuid
from datetime import datetime, timezone

import pytest

from thestill.models.podcast import Podcast
from thestill.repositories.sqlite_podcast_repository import (
    SYNTHETIC_AUDIO_IMPORTS_ID,
    SqlitePodcastRepository,
)


@pytest.fixture
def db_path(tmp_path):
    path = str(tmp_path / "refresh.db")
    SqlitePodcastRepository(path)
    return path


@pytest.fixture
def repo(db_path):
    return SqlitePodcastRepository(db_path)


def _insert_podcast(db_path, *, title, rss_url, synthetic=0, auto_added=0):
    pid = str(uuid.uuid4())
    now = datetime.now(timezone.utc).isoformat()
    with sqlite3.connect(db_path) as conn:
        conn.execute(
            """
            INSERT INTO podcasts (id, created_at, updated_at, rss_url, title, slug,
                                  description, language, synthetic, auto_added)
            VALUES (?, ?, ?, ?, ?, ?, '', 'en', ?, ?)
            """,
            (pid, now, now, rss_url, title, title.lower().replace(" ", "-"), synthetic, auto_added),
        )
        conn.commit()
    return pid


def _add_follower(db_path, podcast_id):
    """Insert a fake follower row directly. The user FK isn't enforced
    on the test DB without a users row, so we make a minimal one."""
    user_id = str(uuid.uuid4())
    now = datetime.now(timezone.utc).isoformat()
    with sqlite3.connect(db_path) as conn:
        conn.execute("PRAGMA foreign_keys = OFF")
        conn.execute(
            "INSERT INTO podcast_followers (id, user_id, podcast_id, created_at) VALUES (?, ?, ?, ?)",
            (str(uuid.uuid4()), user_id, podcast_id, now),
        )
        conn.commit()
    return user_id


def test_refresh_includes_normal_podcasts(repo, db_path):
    pid = _insert_podcast(db_path, title="Real", rss_url="https://example.com/real.xml")
    podcasts, _ = repo.get_podcasts_for_refresh()
    assert [p.id for p in podcasts] == [pid]


def test_refresh_excludes_synthetic_parent(repo, db_path):
    _insert_podcast(db_path, title="Real", rss_url="https://example.com/real.xml")
    # ensure synthetic parent exists
    repo.ensure_synthetic_audio_imports_parent()
    podcasts, _ = repo.get_podcasts_for_refresh()
    assert SYNTHETIC_AUDIO_IMPORTS_ID not in {p.id for p in podcasts}


def test_refresh_excludes_auto_added_without_followers(repo, db_path):
    real_id = _insert_podcast(db_path, title="Real", rss_url="https://example.com/real.xml")
    auto_id = _insert_podcast(
        db_path, title="Auto", rss_url="https://example.com/auto.xml", auto_added=1
    )
    podcasts, _ = repo.get_podcasts_for_refresh()
    ids = {p.id for p in podcasts}
    assert real_id in ids
    assert auto_id not in ids


def test_refresh_includes_auto_added_once_followed(repo, db_path):
    auto_id = _insert_podcast(
        db_path, title="Auto", rss_url="https://example.com/auto.xml", auto_added=1
    )
    # No follower yet → excluded.
    assert auto_id not in {p.id for p in repo.get_podcasts_for_refresh()[0]}

    _add_follower(db_path, auto_id)

    # With a follower → included.
    assert auto_id in {p.id for p in repo.get_podcasts_for_refresh()[0]}
