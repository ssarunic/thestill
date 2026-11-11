"""Spec #116 — SqliteSyncTrackRepository: pending / current bookkeeping."""

from __future__ import annotations

import pytest

from thestill.repositories.sqlite_podcast_repository import SqlitePodcastRepository
from thestill.repositories.sqlite_sync_track_repository import SqliteSyncTrackRepository
from thestill.repositories.sync_track_repository import STATE_CURRENT, STATE_PENDING, SyncTrackRecord
from thestill.utils.sqlite_ext import connect

EPISODE = "11111111-1111-4111-8111-111111111111"
PODCAST = "22222222-2222-4222-8222-222222222222"


@pytest.fixture
def repo(tmp_path) -> SqliteSyncTrackRepository:
    db = str(tmp_path / "podcasts.db")
    SqlitePodcastRepository(db_path=db)  # creates the schema
    with connect(db) as conn:
        conn.execute(
            "INSERT INTO podcasts (id, rss_url, title, slug, created_at, updated_at)"
            " VALUES (?, 'https://example.com/rss', 'Show', 'show', '2026-11-10T00:00:00+00:00',"
            " '2026-11-10T00:00:00+00:00')",
            (PODCAST,),
        )
        conn.execute(
            "INSERT INTO episodes (id, podcast_id, external_id, title, slug, audio_url, created_at, updated_at)"
            " VALUES (?, ?, 'ext-1', 'Episode', 'episode', 'https://example.com/a.mp3',"
            " '2026-11-10T00:00:00+00:00', '2026-11-10T00:00:00+00:00')",
            (EPISODE, PODCAST),
        )
    return SqliteSyncTrackRepository(db_path=db)


def _record(generation: str, **over) -> SyncTrackRecord:
    values = dict(
        episode_id=EPISODE,
        generation=generation,
        state=STATE_PENDING,
        path=f"sync_tracks/show/episode.{generation}.sync",
        source="local",
        frames=1000,
        anchors=26,
        audio_bytes=417_000,
        audio_duration_s=26.1,
    )
    values.update(over)
    return SyncTrackRecord(**values)


def test_pending_is_not_visible_as_current(repo):
    assert repo.put_pending(_record("g1")) is None
    assert repo.get(EPISODE) is None
    pending = repo.get(EPISODE, STATE_PENDING)
    assert pending.generation == "g1"
    assert pending.audio_duration_s == 26.1
    assert pending.created_at is not None and pending.created_at.tzinfo is not None


def test_new_pending_replaces_the_old_one_and_reports_its_path(repo):
    repo.put_pending(_record("g1"))
    assert repo.put_pending(_record("g2")) == "sync_tracks/show/episode.g1.sync"
    assert repo.get(EPISODE, STATE_PENDING).generation == "g2"


def test_promote_switches_and_returns_the_retired_path(repo):
    repo.put_pending(_record("g1"))
    assert repo.promote(EPISODE, "g1") == []
    assert repo.get(EPISODE).generation == "g1"
    assert repo.get(EPISODE, STATE_PENDING) is None

    repo.put_pending(_record("g2"))
    assert repo.get(EPISODE).generation == "g1"  # readers still see g1
    assert repo.promote(EPISODE, "g2") == ["sync_tracks/show/episode.g1.sync"]
    assert repo.get(EPISODE).generation == "g2"


def test_promote_of_unknown_generation_changes_nothing(repo):
    repo.put_pending(_record("g1"))
    repo.promote(EPISODE, "g1")
    assert repo.promote(EPISODE, "missing") is None
    assert repo.get(EPISODE).generation == "g1"


def test_retire_by_state_and_all(repo):
    repo.put_pending(_record("g1"))
    repo.promote(EPISODE, "g1")
    repo.put_pending(_record("g2"))
    assert repo.retire(EPISODE, STATE_PENDING) == ["sync_tracks/show/episode.g2.sync"]
    assert repo.get(EPISODE).generation == "g1"
    assert repo.retire(EPISODE) == ["sync_tracks/show/episode.g1.sync"]
    assert repo.get(EPISODE, STATE_CURRENT) is None
    assert repo.retire(EPISODE) == []


def test_promote_requiring_no_current_refuses_to_replace(repo):
    repo.put_pending(_record("g1"))
    assert repo.promote(EPISODE, "g1", require_no_current=True) == []  # nothing to replace: allowed
    repo.put_pending(_record("g2"))

    assert repo.promote(EPISODE, "g2", require_no_current=True) is None

    assert repo.get(EPISODE).generation == "g1"
    assert repo.get(EPISODE, STATE_PENDING).generation == "g2"
