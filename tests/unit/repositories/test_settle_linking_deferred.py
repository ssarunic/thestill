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

"""Spec #81 - ``linking_deferred`` settles to ``complete``, and nothing else does."""

import sqlite3

import pytest

from thestill.repositories.sqlite_podcast_repository import SqlitePodcastRepository

POD = "11111111-0000-4000-8000-000000000000"
EP = "22222222-0000-4000-8000-000000000000"


@pytest.fixture
def repo(tmp_path):
    db = str(tmp_path / "settle.db")
    repository = SqlitePodcastRepository(db_path=db)
    with sqlite3.connect(db) as conn:
        conn.execute(
            "INSERT INTO podcasts (id, rss_url, title, description) VALUES (?, 'https://x/y', 'Show', '')", (POD,)
        )
        conn.execute(
            "INSERT INTO episodes (id, podcast_id, external_id, title, description, audio_url) "
            "VALUES (?, ?, 'e1', 'Ep', '', 'https://x/e.mp3')",
            (EP, POD),
        )
    repository.db = db
    return repository


def _status(repo):
    with sqlite3.connect(repo.db) as conn:
        return conn.execute("SELECT entity_extraction_status FROM episodes WHERE id = ?", (EP,)).fetchone()[0]


def test_a_deferred_episode_becomes_complete(repo):
    repo.update_entity_extraction_status(episode_id=EP, status="linking_deferred")
    assert repo.settle_linking_deferred(EP) is True
    assert _status(repo) == "complete"


@pytest.mark.parametrize("status", ["failed", "complete", "skipped_unavailable", "pending"])
def test_any_other_status_is_left_alone(repo, status):
    repo.update_entity_extraction_status(episode_id=EP, status=status)
    assert repo.settle_linking_deferred(EP) is False
    assert _status(repo) == status


def test_an_unknown_episode_is_not_an_error(repo):
    assert repo.settle_linking_deferred("no-such-episode") is False
