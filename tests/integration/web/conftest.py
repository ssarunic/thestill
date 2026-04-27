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

"""Shared HTTP-level fixtures for FastAPI route tests.

These fixtures spin up the real FastAPI app with a tmp_path-backed SQLite
database and bypass the production lifespan. The lifespan runs
``validate_transcription_provider`` and starts the background task worker,
neither of which is wanted (or even reachable) in these tests. Instead we
build the ``AppState`` object directly and stamp it onto ``app.state``,
which is all the route handlers care about.

If a test needs the task worker or migration recovery, it should not use
this fixture — write a different one or use the full ``with TestClient(...)``
form.
"""

from __future__ import annotations

import sqlite3
from datetime import datetime, timezone
from pathlib import Path
from typing import Iterator

import pytest
from fastapi.testclient import TestClient

from thestill.core.feed_manager import PodcastFeedManager
from thestill.core.progress_store import ProgressStore
from thestill.core.queue_manager import QueueManager
from thestill.repositories.sqlite_digest_repository import SqliteDigestRepository
from thestill.repositories.sqlite_podcast_follower_repository import SqlitePodcastFollowerRepository
from thestill.repositories.sqlite_podcast_repository import SqlitePodcastRepository
from thestill.repositories.sqlite_user_repository import SqliteUserRepository
from thestill.services import FollowerService, PodcastService, RefreshService, StatsService
from thestill.services.auth_service import AuthService
from thestill.utils.config import Config
from thestill.utils.path_manager import PathManager
from thestill.web.app import create_app
from thestill.web.dependencies import AppState
from thestill.web.task_manager import get_task_manager


@pytest.fixture
def app_config(tmp_path: Path) -> Config:
    """Minimal Config rooted at a tmp directory.

    ``jwt_secret_key`` is set so AuthService doesn't warn-and-randomise; that
    randomisation is fine in production but breaks tests that want a stable
    token across multiple requests.
    """
    return Config(
        storage_path=tmp_path,
        multi_user=False,
        jwt_secret_key="test" * 16,
        cookie_secure=False,
    )


@pytest.fixture
def app_state(app_config: Config) -> AppState:
    """Construct the same wiring as ``create_app`` without the lifespan.

    The task worker is never started — tests that need it should add a
    different fixture rather than re-using this one.
    """
    path_manager = PathManager(str(app_config.storage_path))
    repository = SqlitePodcastRepository(db_path=app_config.database_path)
    feed_manager = PodcastFeedManager(repository, path_manager)
    podcast_service = PodcastService(app_config.storage_path, repository, path_manager)
    refresh_service = RefreshService(feed_manager, podcast_service)
    stats_service = StatsService(app_config.storage_path, repository, path_manager)
    queue_manager = QueueManager(app_config.database_path)
    progress_store = ProgressStore()
    user_repository = SqliteUserRepository(db_path=app_config.database_path)
    auth_service = AuthService(app_config, user_repository)
    follower_repository = SqlitePodcastFollowerRepository(db_path=app_config.database_path)
    follower_service = FollowerService(follower_repository, repository)
    digest_repository = SqliteDigestRepository(db_path=app_config.database_path)

    return AppState(
        config=app_config,
        path_manager=path_manager,
        repository=repository,
        feed_manager=feed_manager,
        podcast_service=podcast_service,
        refresh_service=refresh_service,
        stats_service=stats_service,
        task_manager=get_task_manager(),
        queue_manager=queue_manager,
        task_worker=None,  # type: ignore[arg-type]  # not started in tests
        progress_store=progress_store,
        user_repository=user_repository,
        auth_service=auth_service,
        follower_repository=follower_repository,
        follower_service=follower_service,
        digest_repository=digest_repository,
    )


@pytest.fixture
def client(app_config: Config, app_state: AppState) -> Iterator[TestClient]:
    """FastAPI TestClient with ``app.state.app_state`` pre-stamped.

    Constructed without ``with`` so the lifespan never runs — startup
    validates the configured transcription provider, which would require
    optional ``torch``/``whisper`` deps that the slim CI image deliberately
    omits. Routes that need ``app_state`` (which is most of them) get it
    via ``request.app.state`` regardless of lifespan.
    """
    app = create_app(app_config)
    app.state.app_state = app_state
    yield TestClient(app)


def seed_top_chart(app_state: AppState, region: str, entries: list[dict]) -> None:
    """Insert top-podcast rows directly. Mirrors the helper in the repo unit
    tests but lives here so HTTP tests can call it.

    Each entry: ``{rank, name, artist, rss_url, category_name?}``.
    """
    now = datetime.now(timezone.utc).isoformat()
    db_path = app_state.repository.db_path
    with sqlite3.connect(str(db_path)) as conn:
        conn.execute("PRAGMA foreign_keys = ON")
        # Wipe everything the seeder may have populated. The repo's init seeds
        # ``gb`` + ``us`` from data/top_podcasts_*.json; without clearing meta,
        # ``_resolve_region`` would still pick the first alphabetical region
        # the file knew about and ignore our test seed.
        conn.execute("DELETE FROM podcast_followers")
        conn.execute("DELETE FROM top_podcast_rankings")
        conn.execute("DELETE FROM top_podcasts")
        conn.execute("DELETE FROM top_podcasts_meta")
        conn.execute("DELETE FROM podcasts")

        for entry in entries:
            top_id = conn.execute(
                """
                INSERT INTO top_podcasts
                    (name, artist, rss_url, first_seen_at, last_seen_at)
                VALUES (?, ?, ?, ?, ?)
                """,
                (entry["name"], entry.get("artist"), entry["rss_url"], now, now),
            ).lastrowid
            conn.execute(
                """
                INSERT INTO top_podcast_rankings
                    (top_podcast_id, region, rank, source_genre, scraped_at)
                VALUES (?, ?, ?, ?, ?)
                """,
                (top_id, region, entry["rank"], entry.get("source_genre"), now),
            )
        # ``top_podcasts_meta`` drives `_resolve_region` — record this region.
        conn.execute(
            """
            INSERT INTO top_podcasts_meta (region, source_path, source_mtime, row_count, seeded_at)
            VALUES (?, ?, ?, ?, ?)
            ON CONFLICT(region) DO UPDATE SET row_count = excluded.row_count
            """,
            (region, "test", 0.0, len(entries), now),
        )
        conn.commit()
