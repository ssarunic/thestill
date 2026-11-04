"""Dual-backend contract suite for the transcript edit op log (spec #110 §2).

Same shape as ``test_mcp_token_repository_contract.py``: every behaviour is
asserted identically on SQLite and PostgreSQL. Postgres cases skip (not
fail) when ``TEST_DATABASE_URL`` is unset or unreachable.
"""

from __future__ import annotations

import os
import uuid
from datetime import datetime, timedelta, timezone

import pytest

from thestill.models.transcript_edits import TranscriptEdit
from thestill.repositories.sqlite_podcast_repository import SqlitePodcastRepository
from thestill.repositories.sqlite_transcript_edit_repository import SqliteTranscriptEditRepository
from thestill.utils.sqlite_ext import connect

PG_DSN = os.getenv("TEST_DATABASE_URL", "")


def _pg_reachable(dsn: str) -> bool:
    if not dsn:
        return False
    try:
        import psycopg

        with psycopg.connect(dsn, connect_timeout=3) as conn:
            conn.execute("SELECT 1")
        return True
    except Exception:
        return False


PG_OK = _pg_reachable(PG_DSN)

NOW = datetime(2026, 11, 2, 12, 0, tzinfo=timezone.utc)
PODCAST_ID = str(uuid.uuid4())
EPISODE_ID = str(uuid.uuid4())
OTHER_EPISODE_ID = str(uuid.uuid4())
USER_ID = str(uuid.uuid4())


@pytest.fixture(params=["sqlite", "postgres"])
def repo(request, tmp_path):
    """A clean op log with one podcast and two episodes seeded for the FK."""
    if request.param == "sqlite":
        db = str(tmp_path / "contract.db")
        SqlitePodcastRepository(db_path=db)  # owns the DDL
        with connect(db) as conn:
            conn.execute(
                "INSERT INTO podcasts (id, rss_url, title, slug, description, language)"
                " VALUES (?, 'https://x.test/f.xml', 'Show', 'show', '', 'en')",
                (PODCAST_ID,),
            )
            for episode_id, slug in ((EPISODE_ID, "one"), (OTHER_EPISODE_ID, "two")):
                conn.execute(
                    "INSERT INTO episodes (id, podcast_id, external_id, title, slug, audio_url, duration)"
                    " VALUES (?, ?, ?, 'Ep', ?, 'https://x.test/e.mp3', 60)",
                    (episode_id, PODCAST_ID, slug, slug),
                )
        yield SqliteTranscriptEditRepository(db_path=db)
        return

    if not PG_OK:
        pytest.skip("Postgres not reachable — set TEST_DATABASE_URL to include this backend")
    import psycopg

    from thestill.repositories.postgres_schema import ensure_schema
    from thestill.repositories.postgres_transcript_edit_repository import PostgresTranscriptEditRepository

    ensure_schema(PG_DSN)
    with psycopg.connect(PG_DSN) as conn:
        conn.execute("TRUNCATE podcasts, episodes, transcript_edits, transcript_edit_refreshes CASCADE")
        conn.execute(
            "INSERT INTO podcasts (id, rss_url, title, slug, description, language)"
            " VALUES (%s, 'https://x.test/f.xml', 'Show', 'show', '', 'en')",
            (PODCAST_ID,),
        )
        for episode_id, slug in ((EPISODE_ID, "one"), (OTHER_EPISODE_ID, "two")):
            conn.execute(
                "INSERT INTO episodes (id, podcast_id, external_id, title, slug, audio_url, duration)"
                " VALUES (%s, %s, %s, 'Ep', %s, 'https://x.test/e.mp3', 60)",
                (episode_id, PODCAST_ID, slug, slug),
            )
    yield PostgresTranscriptEditRepository(PG_DSN)


def _edit(episode_id: str = EPISODE_ID, **overrides) -> TranscriptEdit:
    base = dict(
        id=str(uuid.uuid4()),
        episode_id=episode_id,
        seq=0,
        op="speaker.rename_label",
        raw_sha256="a" * 64,
        anchor={"label": "SPEAKER_01"},
        args={"name": "Grace Hopper", "entity_id": None},
        before={"name": "SPEAKER_01", "entity_id": None},
        reverts=None,
        session_id="s1",
        report_id=None,
        actor_user_id=USER_ID,
        actor_kind="web",
        created_at=NOW,
    )
    base.update(overrides)
    return TranscriptEdit(**base)


def test_append_assigns_seq_and_round_trips(repo):
    stored = repo.append(_edit(), expected_last_seq=0)
    assert stored is not None and stored.seq == 1
    rows = repo.list_for_episode(EPISODE_ID)
    assert len(rows) == 1
    got = rows[0]
    assert got.id == stored.id and got.anchor == {"label": "SPEAKER_01"} and got.before["name"] == "SPEAKER_01"
    assert got.created_at == NOW and got.actor_user_id == USER_ID
    assert repo.get(stored.id) == got
    assert repo.last_seq(EPISODE_ID) == 1


def test_append_refuses_a_stale_base(repo):
    first = repo.append(_edit(), expected_last_seq=0)
    assert repo.append(_edit(), expected_last_seq=0) is None  # someone else wrote seq 1
    second = repo.append(_edit(reverts=first.id, op="revert", anchor={"edit_id": first.id}), expected_last_seq=1)
    assert second.seq == 2 and repo.get(second.id).reverts == first.id
    assert [r.seq for r in repo.list_for_episode(EPISODE_ID)] == [1, 2]


def test_episodes_are_independent(repo):
    repo.append(_edit(), expected_last_seq=0)
    repo.append(_edit(OTHER_EPISODE_ID), expected_last_seq=0)
    assert repo.last_seq(OTHER_EPISODE_ID) == 1
    assert repo.list_for_episode(str(uuid.uuid4())) == [] and repo.last_seq("not-a-uuid") == 0
    assert repo.get("not-a-uuid") is None


def test_list_by_actor_since(repo):
    repo.append(_edit(created_at=NOW), expected_last_seq=0)
    repo.append(_edit(created_at=NOW + timedelta(hours=1)), expected_last_seq=1)
    repo.append(_edit(actor_user_id=None, actor_kind="cli", created_at=NOW + timedelta(hours=2)), expected_last_seq=2)
    rows = repo.list_by_actor(USER_ID, since=NOW + timedelta(minutes=30))
    assert [r.seq for r in rows] == [2]


def test_refresh_bookkeeping(repo):
    repo.append(_edit(created_at=NOW), expected_last_seq=0)
    repo.append(_edit(OTHER_EPISODE_ID, created_at=NOW + timedelta(hours=1)), expected_last_seq=0)
    with_edits = {e for e, _seq, _at in repo.episodes_with_edits()}
    assert with_edits == {EPISODE_ID, OTHER_EPISODE_ID}
    # Only the idle episode is due.
    assert repo.episodes_pending_refresh(idle_before=NOW + timedelta(minutes=30)) == [(EPISODE_ID, 1)]
    repo.mark_refreshed(EPISODE_ID, seq=1, at=NOW + timedelta(minutes=31))
    assert repo.episodes_pending_refresh(idle_before=NOW + timedelta(days=1)) == [(OTHER_EPISODE_ID, 1)]
    repo.append(_edit(created_at=NOW + timedelta(hours=3)), expected_last_seq=1)
    due = dict(repo.episodes_pending_refresh(idle_before=NOW + timedelta(days=1)))
    assert due == {EPISODE_ID: 2, OTHER_EPISODE_ID: 1}
    repo.mark_refreshed(EPISODE_ID, seq=2, at=NOW + timedelta(hours=4))
    repo.mark_refreshed(OTHER_EPISODE_ID, seq=1, at=NOW + timedelta(hours=4))
    assert repo.episodes_pending_refresh(idle_before=NOW + timedelta(days=1)) == []
