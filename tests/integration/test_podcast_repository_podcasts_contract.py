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

"""Dual-backend contract suite for the PODCAST side of the podcast repository
(spec #44 — ``PodcastsMixin`` port).

The same tests run against BOTH the SQLite ``SqlitePodcastRepository`` and a
minimal concrete composition of the Postgres ``PodcastsMixin``, asserting
identical behaviour on both engines (spec #42 FM-5: real Postgres, no mocks).

Postgres cases are skipped (not failed) when no server is reachable:

    TEST_DATABASE_URL=postgresql://postgres@127.0.0.1:55432/thestill_podcast \\
        ./venv/bin/python -m pytest tests/integration/test_podcast_repository_podcasts_contract.py

Fixture data is namespaced (``https://p1.test/`` URL prefix + per-test unique
keys) because the episode-side agent shares the same test database.
"""

from __future__ import annotations

import json
import os
import uuid
from datetime import datetime, timedelta, timezone

import pytest

from thestill.core.refresh_failure import RefreshFailure, RefreshFailureKind, RefreshPolicySettings
from thestill.models.podcast import Episode, FailureType, Podcast
from thestill.repositories.postgres_podcast_repository_podcasts import PodcastsMixin
from thestill.repositories.sqlite_podcast_repository import SYNTHETIC_AUDIO_IMPORTS_ID, SqlitePodcastRepository

# Spec #60 — shared policy settings + failure builders for the refresh tests.
_SETTINGS = RefreshPolicySettings(min_interval_seconds=600, max_interval_seconds=86400, default_interval_seconds=3600)


def _gone_410() -> RefreshFailure:
    return RefreshFailure(kind=RefreshFailureKind.REMOTE_GONE, http_status=410, exception="410 Gone")


def _connectivity() -> RefreshFailure:
    return RefreshFailure(kind=RefreshFailureKind.CONNECTIVITY, exception="[Errno 8] nodename nor servname")


PG_DSN = os.getenv("TEST_DATABASE_URL", "")

# Tables owned by this suite (truncated per test on the PG side).
_PG_TABLES = "podcasts, episodes, categories, top_podcasts, top_podcast_rankings, top_podcasts_meta, users"


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


class _PgRepo(PodcastsMixin):
    """Minimal concrete composition of the podcast-side mixin."""

    def __init__(self, dsn):
        self.dsn = dsn


def _is_pg(repo) -> bool:
    return isinstance(repo, _PgRepo)


@pytest.fixture(params=["sqlite", "postgres"])
def repo(request, tmp_path):
    """Yield a clean podcast repository for each backend."""
    if request.param == "sqlite":
        yield SqlitePodcastRepository(db_path=str(tmp_path / "contract.db"))
        return

    if not PG_OK:
        pytest.skip("Postgres not reachable — set TEST_DATABASE_URL to include this backend")
    import psycopg

    from thestill.repositories.postgres_schema import ensure_schema

    ensure_schema(PG_DSN)  # idempotent typed-schema bootstrap
    with psycopg.connect(PG_DSN) as conn:
        conn.execute(f"TRUNCATE {_PG_TABLES} CASCADE")
    yield _PgRepo(PG_DSN)


# ---------------------------------------------------------------------------
# Raw-SQL helpers (backend-branching for seeding / white-box asserts)
# ---------------------------------------------------------------------------
def _exec(repo, sql: str, params=(), fetch: bool = False):
    """Run raw SQL against the fixture's backend.

    ``sql`` is written with ``?`` placeholders; rewritten to ``%s`` for PG.
    Returns a list of dict rows when ``fetch`` is True.
    """
    if _is_pg(repo):
        import psycopg
        from psycopg.rows import dict_row

        with psycopg.connect(PG_DSN, row_factory=dict_row) as conn:
            cur = conn.execute(sql.replace("?", "%s"), params)
            return [dict(r) for r in cur.fetchall()] if fetch else None

    import sqlite3

    conn = sqlite3.connect(str(repo.db_path))
    conn.row_factory = sqlite3.Row
    try:
        cur = conn.execute(sql, params)
        rows = [dict(r) for r in cur.fetchall()] if fetch else None
        conn.commit()
        return rows
    finally:
        conn.close()


def _ts(repo, dt: datetime):
    """Bind a timestamp for raw SQL: datetime on PG, ISO-8601 (+00:00) on SQLite."""
    return dt if _is_pg(repo) else dt.isoformat()


def _uniq() -> str:
    return uuid.uuid4().hex[:10]


def _mk_podcast(**overrides) -> Podcast:
    u = _uniq()
    base = dict(
        rss_url=f"https://p1.test/{u}/feed.xml",
        title=f"Podcast {u}",
        description="A test podcast",
    )
    base.update(overrides)
    return Podcast(**base)


def _mk_episode(**overrides) -> Episode:
    u = _uniq()
    base = dict(
        external_id=f"ep-{u}",
        title=f"Episode {u}",
        description="An episode",
        audio_url=f"https://p1.test/audio/{u}.mp3",
        pub_date=datetime(2026, 1, 5, 10, 0, tzinfo=timezone.utc),
        duration=1234,
        explicit=False,
    )
    base.update(overrides)
    return Episode(**base)


def _seed_user_and_follow(repo, podcast_id: str) -> str:
    """Insert a user + podcast_followers row via raw SQL; returns user id."""
    user_id = str(uuid.uuid4())
    now = _ts(repo, datetime.now(timezone.utc))
    _exec(
        repo,
        "INSERT INTO users (id, email, created_at) VALUES (?, ?, ?)",
        (user_id, f"u-{user_id[:8]}@p1.test", now),
    )
    _exec(
        repo,
        "INSERT INTO podcast_followers (id, user_id, podcast_id, created_at) VALUES (?, ?, ?, ?)",
        (str(uuid.uuid4()), user_id, podcast_id, now),
    )
    return user_id


# ---------------------------------------------------------------------------
# save / get round-trips (typed fields)
# ---------------------------------------------------------------------------
def test_save_get_roundtrip_typed_fields(repo):
    lp = datetime(2026, 1, 1, 12, 0, 0, 123456, tzinfo=timezone.utc)
    p = _mk_podcast(
        author="Ann Author",
        explicit=False,  # tricky case: False must NOT collapse to None/True
        show_type="episodic",
        website_url="https://p1.test/site",
        is_complete=True,
        copyright="(c) p1",
        last_processed=lp,
        etag='W/"abc"',
        last_modified="Mon, 01 Jan 2026 00:00:00 GMT",
        language="hr",
    )
    repo.save(p)

    got = repo.get_by_url(str(p.rss_url))
    assert got is not None
    assert got.id == p.id
    assert got.title == p.title
    assert got.slug == p.slug
    assert got.description == p.description
    assert got.language == "hr"
    assert got.author == "Ann Author"
    assert got.explicit is False
    assert got.show_type == "episodic"
    assert got.website_url == "https://p1.test/site"
    assert got.is_complete is True
    assert got.copyright == "(c) p1"
    assert got.created_at == p.created_at
    assert got.last_processed == lp
    assert got.last_processed.tzinfo is not None
    # save() intentionally does NOT persist cache headers or the wall-clock
    # processing time — only save_podcast/save_refresh_batch write those.
    assert got.etag is None
    assert got.last_modified is None
    assert got.last_processed_at is None
    # No categories provided → stored/read back as None on both engines.
    assert got.primary_category is None
    assert got.primary_subcategory is None
    assert got.secondary_category is None

    # Entity-id list columns: jsonb list on PG, JSON text on SQLite — both
    # must default to an empty list, never a double-encoded string.
    rows = _exec(repo, "SELECT host_entity_ids, recurring_entity_ids FROM podcasts WHERE id = ?", (p.id,), fetch=True)
    for col in ("host_entity_ids", "recurring_entity_ids"):
        value = rows[0][col]
        if _is_pg(repo):
            assert value == []
        else:
            assert json.loads(value) == []


def test_save_get_roundtrip_explicit_none(repo):
    p = _mk_podcast(explicit=None, is_complete=False)
    repo.save(p)
    got = repo.get(p.id)
    assert got is not None
    assert got.explicit is None
    assert got.is_complete is False
    assert got.last_processed is None
    assert got.etag is None


def test_save_with_episodes_roundtrip_and_destructive_replace(repo):
    p = _mk_podcast()
    e_new = _mk_episode(pub_date=datetime(2026, 3, 2, tzinfo=timezone.utc), explicit=True)
    e_old = _mk_episode(
        pub_date=datetime(2026, 3, 1, tzinfo=timezone.utc),
        failed_at_stage="download",
        failure_reason="boom",
        failure_type=FailureType.FATAL,
        failed_at=datetime(2026, 3, 1, 6, 0, tzinfo=timezone.utc),
        playback_time_offset_seconds=1.5,
    )
    p.episodes = [e_new, e_old]
    repo.save(p)

    got = repo.get(p.id)
    assert got is not None
    assert [e.external_id for e in got.episodes] == [e_new.external_id, e_old.external_id]  # pub_date DESC

    g_new, g_old = got.episodes
    assert g_new.id == e_new.id
    assert g_new.podcast_id == p.id
    assert g_new.explicit is True
    assert g_new.duration == 1234
    assert g_new.pub_date == e_new.pub_date
    assert g_new.created_at == e_new.created_at
    assert str(g_new.audio_url) == str(e_new.audio_url)
    assert g_new.playback_time_offset_seconds == 0.0
    assert g_new.failure_type is None

    assert g_old.failed_at_stage == "download"
    assert g_old.failure_reason == "boom"
    assert g_old.failure_type is FailureType.FATAL
    assert g_old.failed_at == e_old.failed_at
    assert g_old.playback_time_offset_seconds == 1.5

    # save() is destructive: re-saving with one episode replaces the set.
    p.episodes = [e_new]
    repo.save(p)
    got = repo.get(p.id)
    assert [e.external_id for e in got.episodes] == [e_new.external_id]


def test_save_upsert_preserves_identity(repo):
    p1 = _mk_podcast(title="Original")
    repo.save(p1)
    # Same rss_url under a NEW model id → updates in place, identity stays.
    dup = _mk_podcast(rss_url=str(p1.rss_url), title="Renamed")
    repo.save(dup)

    got = repo.get_by_url(str(p1.rss_url))
    assert got.id == p1.id
    assert got.title == "Renamed"
    assert len([q for q in repo.get_all() if str(q.rss_url) == str(p1.rss_url)]) == 1


# ---------------------------------------------------------------------------
# Lookups: url / slug / index / exists / delete
# ---------------------------------------------------------------------------
def test_get_by_url_slug_index(repo):
    t0 = datetime(2026, 5, 1, tzinfo=timezone.utc)
    p1 = _mk_podcast(created_at=t0)
    p2 = _mk_podcast(created_at=t0 + timedelta(hours=1))
    repo.save(p1)
    repo.save(p2)

    assert repo.get_by_url(str(p1.rss_url)).id == p1.id
    assert repo.get_by_url("https://p1.test/nope.xml") is None

    assert repo.get_by_slug(p1.slug).id == p1.id
    assert repo.get_by_slug("") is None
    assert repo.get_by_slug("no-such-slug-p1") is None

    # 1-based index ordered by created_at DESC — newest first.
    assert repo.get_by_index(1).id == p2.id
    assert repo.get_by_index(2).id == p1.id
    assert repo.get_by_index(0) is None
    assert repo.get_by_index(3) is None

    assert repo.get(p1.id).id == p1.id
    assert repo.get(str(uuid.uuid4())) is None
    assert repo.get_by_id(p2.id).id == p2.id


def test_exists_and_delete(repo):
    p = _mk_podcast()
    p.episodes = [_mk_episode()]
    repo.save(p)

    url = str(p.rss_url)
    assert repo.exists(url) is True
    assert repo.delete(url) is True
    assert repo.exists(url) is False
    assert repo.get(p.id) is None
    # Episodes were explicitly deleted too.
    rows = _exec(repo, "SELECT COUNT(*) AS n FROM episodes WHERE podcast_id = ?", (p.id,), fetch=True)
    assert rows[0]["n"] == 0
    assert repo.delete(url) is False  # already gone


# ---------------------------------------------------------------------------
# save_podcast (metadata-only, idempotent updated_at)
# ---------------------------------------------------------------------------
def test_save_podcast_metadata_only_is_idempotent(repo):
    p = _mk_podcast(last_processed=datetime(2026, 1, 3, tzinfo=timezone.utc))
    e = _mk_episode()
    p.episodes = [e]
    repo.save(p)

    def updated_at():
        return _exec(repo, "SELECT updated_at FROM podcasts WHERE id = ?", (p.id,), fetch=True)[0]["updated_at"]

    u1 = updated_at()
    repo.save_podcast(p)  # no change → updated_at untouched
    assert updated_at() == u1

    p.title = "Changed Title"
    repo.save_podcast(p)
    assert updated_at() != u1

    # Episodes are NOT touched by save_podcast.
    got = repo.get(p.id)
    assert got.title == "Changed Title"
    assert [x.external_id for x in got.episodes] == [e.external_id]


def test_save_podcast_inserts_when_missing(repo):
    p = _mk_podcast(etag='W/"new"', last_modified="lm")
    repo.save_podcast(p)
    got = repo.get_by_url(str(p.rss_url))
    assert got is not None
    assert got.id == p.id
    assert got.etag == 'W/"new"'
    assert got.last_modified == "lm"
    assert got.episodes == []


def test_touch_last_processed_at(repo):
    lp = datetime(2026, 1, 1, tzinfo=timezone.utc)
    p = _mk_podcast(last_processed=lp)
    repo.save(p)

    when = datetime(2026, 6, 6, 6, 6, 6, tzinfo=timezone.utc)
    repo.touch_last_processed_at(p.id, when)
    got = repo.get(p.id)
    assert got.last_processed_at == when
    assert got.last_processed == lp  # watermark never clobbered


# ---------------------------------------------------------------------------
# Refresh loaders (spec #19 / #48)
# ---------------------------------------------------------------------------
def test_get_podcasts_for_refresh_and_single(repo):
    p = _mk_podcast(etag='W/"r1"', last_modified="lm-r1")
    e1, e2 = _mk_episode(), _mk_episode()
    p.episodes = [e1, e2]
    repo.save(p)
    repo.save_podcast(p)  # save() drops cache headers; save_podcast persists them
    _seed_user_and_follow(repo, p.id)  # spec #63: refresh requires a follower

    synthetic_id = repo.ensure_synthetic_audio_imports_parent()
    auto_url = f"https://p1.test/{_uniq()}/auto.xml"
    auto_id, _, _ = repo.upsert_auto_added_podcast(rss_url=auto_url, title=f"Auto {_uniq()}")

    podcasts, dedup = repo.get_podcasts_for_refresh()
    ids = {x.id for x in podcasts}
    assert p.id in ids
    assert synthetic_id not in ids  # synthetic parents never refresh
    assert auto_id not in ids  # auto-added without a follower is skipped
    loaded = next(x for x in podcasts if x.id == p.id)
    assert loaded.episodes == []  # no hydration on the hot path
    assert loaded.etag == 'W/"r1"'
    assert dedup[p.id] == {e1.external_id, e2.external_id}

    # A follower makes the auto-added podcast poll-worthy.
    _seed_user_and_follow(repo, auto_id)
    podcasts, _ = repo.get_podcasts_for_refresh()
    assert auto_id in {x.id for x in podcasts}

    # Single-feed analogue.
    result = repo.get_podcast_for_refresh(p.id)
    assert result is not None
    single, known = result
    assert single.id == p.id
    assert single.episodes == []
    assert known == {e1.external_id, e2.external_id}
    assert repo.get_podcast_for_refresh(str(uuid.uuid4())) is None


def test_refresh_excludes_unfollowed_everywhere(repo):
    """Spec #63 — a plain (non-auto_added) podcast with zero followers is
    invisible to every bulk refresh surface: the spec #19 loader, the due
    query, seeding, and the health counts."""
    base = datetime(2026, 7, 1, 12, 0, 0, tzinfo=timezone.utc)
    p = _mk_podcast()
    repo.save(p)

    assert p.id not in {x.id for x in repo.get_podcasts_for_refresh()[0]}
    assert repo.seed_unscheduled_feeds(3600, now=base) == 0
    assert p.id not in repo.get_due_podcasts(now=base + timedelta(days=1))
    before = repo.get_refresh_health_counts(now=base)

    # A follower flips every surface on.
    _seed_user_and_follow(repo, p.id)
    assert p.id in {x.id for x in repo.get_podcasts_for_refresh()[0]}
    assert repo.seed_unscheduled_feeds(3600, now=base) == 1
    assert p.id in repo.get_due_podcasts(now=base + timedelta(seconds=3600))
    after = repo.get_refresh_health_counts(now=base + timedelta(days=1))
    assert after["active"] >= before["active"] + 1


def test_refresh_scheduling_bookkeeping(repo):
    p = _mk_podcast()
    repo.save(p)
    _seed_user_and_follow(repo, p.id)  # spec #63: scheduling requires a follower
    base = datetime(2026, 7, 1, 12, 0, 0, tzinfo=timezone.utc)

    # Seed: only never-scheduled feeds; second run is a no-op.
    assert repo.seed_unscheduled_feeds(3600, now=base) == 1
    assert repo.seed_unscheduled_feeds(3600, now=base) == 0

    # Seeded next_refresh_at = base + (hash % 3600) → due within one interval.
    assert p.id in repo.get_due_podcasts(now=base + timedelta(seconds=3600))
    assert p.id not in repo.get_due_podcasts(now=base - timedelta(seconds=1))

    # AIMD: no new episodes → interval * 1.5 (3600 → 5400).
    next_iso = repo.record_refresh_success(
        p.id, found_new=False, min_interval=600, max_interval=86400, default_interval=3600, now=base
    )
    assert datetime.fromisoformat(next_iso) == base + timedelta(seconds=5400)

    # New episodes → interval // 2 (5400 → 2700).
    next_iso = repo.record_refresh_success(
        p.id, found_new=True, min_interval=600, max_interval=86400, default_interval=3600, now=base
    )
    assert datetime.fromisoformat(next_iso) == base + timedelta(seconds=2700)
    assert p.id in repo.get_due_podcasts(now=base + timedelta(seconds=2700))

    # Spec #60 — a decisive 410 QUARANTINES the feed (next_refresh_at NULL,
    # reason recorded).
    decision = repo.record_refresh_failure(p.id, _gone_410(), _SETTINGS, now=base)
    assert decision.disabled_reason == "feed_gone"
    assert p.id not in repo.get_due_podcasts(now=base + timedelta(days=365))
    row = _exec(
        repo,
        "SELECT last_refresh_error, refresh_disabled_reason, last_refresh_failure_kind, "
        "last_refresh_status_code, consecutive_refresh_failures FROM podcasts WHERE id = ?",
        (p.id,),
        fetch=True,
    )[0]
    assert row["last_refresh_error"] == "410 Gone"
    assert row["refresh_disabled_reason"] == "feed_gone"
    assert row["last_refresh_failure_kind"] == "remote_gone"
    assert row["last_refresh_status_code"] == 410
    assert row["consecutive_refresh_failures"] == 1
    # A quarantined feed is never silently re-seeded.
    assert repo.seed_unscheduled_feeds(3600, now=base) == 0

    # Operator retry re-arms to now and clears ALL failure state.
    next_iso = repo.clear_podcast_refresh_failure(p.id, 3600, now=base)
    assert datetime.fromisoformat(next_iso) == base
    assert p.id in repo.get_due_podcasts(now=base)
    row = _exec(
        repo,
        "SELECT last_refresh_error, refresh_disabled_reason, last_refresh_failure_kind, "
        "consecutive_refresh_failures FROM podcasts WHERE id = ?",
        (p.id,),
        fetch=True,
    )[0]
    assert row["last_refresh_error"] is None
    assert row["refresh_disabled_reason"] is None
    assert row["last_refresh_failure_kind"] is None
    assert row["consecutive_refresh_failures"] == 0

    # Spec #60 core incident fix — connectivity backs off but NEVER parks.
    decision = repo.record_refresh_failure(p.id, _connectivity(), _SETTINGS, now=base)
    assert decision.disabled_reason is None
    row = _exec(
        repo,
        "SELECT next_refresh_at, refresh_disabled_reason FROM podcasts WHERE id = ?",
        (p.id,),
        fetch=True,
    )[0]
    assert row["next_refresh_at"] is not None
    assert row["refresh_disabled_reason"] is None
    # Backed off: due again after the lengthened interval, not immediately.
    assert p.id not in repo.get_due_podcasts(now=base)
    assert p.id in repo.get_due_podcasts(now=base + timedelta(days=2))


def test_record_refresh_failure_policy_matrix(repo):
    """Decisive kinds quarantine on first sight; internal never touches the
    schedule; success resets the streak (both backends)."""
    base = datetime(2026, 7, 1, 12, 0, 0, tzinfo=timezone.utc)

    for failure, expected_reason in [
        (RefreshFailure(kind=RefreshFailureKind.SECURITY_POLICY, exception="ssrf refusal"), "blocked_unsafe"),
        (RefreshFailure(kind=RefreshFailureKind.AUTHENTICATION, http_status=401, exception="401"), "auth_required"),
    ]:
        p = _mk_podcast()
        repo.save(p)
        repo.seed_unscheduled_feeds(3600, now=base)
        decision = repo.record_refresh_failure(p.id, failure, _SETTINGS, now=base)
        assert decision.disabled_reason == expected_reason
        row = _exec(
            repo, "SELECT next_refresh_at, refresh_disabled_reason FROM podcasts WHERE id = ?", (p.id,), fetch=True
        )[0]
        assert row["next_refresh_at"] is None
        assert row["refresh_disabled_reason"] == expected_reason

    # INTERNAL (our bug): stamps visibility only, schedule untouched.
    p = _mk_podcast()
    repo.save(p)
    repo.seed_unscheduled_feeds(3600, now=base)
    before = _exec(repo, "SELECT next_refresh_at FROM podcasts WHERE id = ?", (p.id,), fetch=True)[0]
    repo.record_refresh_failure(
        p.id,
        RefreshFailure(kind=RefreshFailureKind.INTERNAL, exception="KeyError('bug')", is_internal=True),
        _SETTINGS,
        now=base,
    )
    row = _exec(
        repo,
        "SELECT next_refresh_at, refresh_disabled_reason, consecutive_refresh_failures, "
        "last_refresh_failure_kind FROM podcasts WHERE id = ?",
        (p.id,),
        fetch=True,
    )[0]
    assert row["next_refresh_at"] == before["next_refresh_at"]  # untouched
    assert row["refresh_disabled_reason"] is None
    assert row["consecutive_refresh_failures"] == 0  # IGNORE does not extend the streak
    assert row["last_refresh_failure_kind"] == "internal"

    # Success clears the whole failure streak.
    repo.record_refresh_failure(p.id, _connectivity(), _SETTINGS, now=base)
    repo.record_refresh_success(
        p.id, found_new=False, min_interval=600, max_interval=86400, default_interval=3600, now=base
    )
    row = _exec(
        repo,
        "SELECT consecutive_refresh_failures, last_refresh_failure_kind, "
        "refresh_failure_streak_started_at FROM podcasts WHERE id = ?",
        (p.id,),
        fetch=True,
    )[0]
    assert row["consecutive_refresh_failures"] == 0
    assert row["last_refresh_failure_kind"] is None
    assert row["refresh_failure_streak_started_at"] is None


def test_404_horizon_gate(repo):
    """404 quarantines only once the AIMD interval sits at max AND the streak
    has persisted a full max interval of wall clock — never on a quick burst."""
    base = datetime(2026, 7, 1, 12, 0, 0, tzinfo=timezone.utc)
    p = _mk_podcast()
    repo.save(p)
    repo.seed_unscheduled_feeds(3600, now=base)
    gone_404 = RefreshFailure(kind=RefreshFailureKind.REMOTE_GONE, http_status=404, exception="404")

    # Burst of 404s minutes apart: backs off, never quarantines.
    for minute in range(3):
        decision = repo.record_refresh_failure(p.id, gone_404, _SETTINGS, now=base + timedelta(minutes=minute))
        assert decision.disabled_reason is None

    # Force the feed to the AIMD max interval, streak already old — the next
    # 404 after a full max-interval horizon quarantines.
    _exec(
        repo,
        "UPDATE podcasts SET refresh_interval_seconds = ?, refresh_failure_streak_started_at = ? WHERE id = ?",
        (86400, _ts(repo, base - timedelta(days=2)), p.id),
    )
    decision = repo.record_refresh_failure(p.id, gone_404, _SETTINGS, now=base + timedelta(days=2))
    assert decision.disabled_reason == "feed_gone"
    # 410 needs no horizon: immediate (fresh podcast, min interval).
    p2 = _mk_podcast()
    repo.save(p2)
    repo.seed_unscheduled_feeds(3600, now=base)
    assert repo.record_refresh_failure(p2.id, _gone_410(), _SETTINGS, now=base).disabled_reason == "feed_gone"


def test_clear_podcast_refresh_failures_bulk(repo):
    """Spec #60 Phase 0 — the bulk re-arm previously MISSING on Postgres."""
    base = datetime(2026, 7, 1, 12, 0, 0, tzinfo=timezone.utc)
    parked = []
    for _ in range(3):
        p = _mk_podcast()
        repo.save(p)
        _seed_user_and_follow(repo, p.id)  # spec #63
        repo.seed_unscheduled_feeds(3600, now=base)
        repo.record_refresh_failure(p.id, _gone_410(), _SETTINGS, now=base)
        parked.append(p.id)
    for pid in parked:
        assert pid not in repo.get_due_podcasts(now=base + timedelta(days=365))

    updated = repo.clear_podcast_refresh_failures(parked, 3600, now=base)
    assert updated == 3
    due = repo.get_due_podcasts(now=base)
    for pid in parked:
        assert pid in due
    assert repo.clear_podcast_refresh_failures([], 3600, now=base) == 0


def test_quarantine_probe_due_and_reasons(repo):
    """Only feed_gone/invalid_content quarantines are re-probed; auth/security
    quarantines are never auto-probed regardless of age."""
    base = datetime(2026, 7, 1, 12, 0, 0, tzinfo=timezone.utc)
    old = base - timedelta(days=30)

    gone = _mk_podcast()
    repo.save(gone)
    _seed_user_and_follow(repo, gone.id)  # spec #63
    repo.seed_unscheduled_feeds(3600, now=old)
    repo.record_refresh_failure(gone.id, _gone_410(), _SETTINGS, now=old)

    blocked = _mk_podcast()
    repo.save(blocked)
    _seed_user_and_follow(repo, blocked.id)  # spec #63
    repo.seed_unscheduled_feeds(3600, now=old)
    repo.record_refresh_failure(
        blocked.id, RefreshFailure(kind=RefreshFailureKind.SECURITY_POLICY, exception="ssrf"), _SETTINGS, now=old
    )

    fresh_gone = _mk_podcast()
    repo.save(fresh_gone)
    _seed_user_and_follow(repo, fresh_gone.id)  # spec #63
    repo.seed_unscheduled_feeds(3600, now=base)
    repo.record_refresh_failure(fresh_gone.id, _gone_410(), _SETTINGS, now=base)

    probes = repo.get_quarantine_probe_due(7 * 86400, now=base)
    assert gone.id in probes  # 30 days old → due a probe
    assert blocked.id not in probes  # security quarantine: NEVER probed
    assert fresh_gone.id not in probes  # quarantined just now → not due yet


def test_get_refresh_health_counts(repo):
    base = datetime(2026, 7, 1, 12, 0, 0, tzinfo=timezone.utc)

    healthy = _mk_podcast()
    repo.save(healthy)
    _seed_user_and_follow(repo, healthy.id)  # spec #63
    repo.seed_unscheduled_feeds(3600, now=base)

    backing = _mk_podcast()
    repo.save(backing)
    _seed_user_and_follow(repo, backing.id)  # spec #63
    repo.seed_unscheduled_feeds(3600, now=base)
    repo.record_refresh_failure(backing.id, _connectivity(), _SETTINGS, now=base)

    quarantined = _mk_podcast()
    repo.save(quarantined)
    _seed_user_and_follow(repo, quarantined.id)  # spec #63
    repo.seed_unscheduled_feeds(3600, now=base)
    repo.record_refresh_failure(quarantined.id, _gone_410(), _SETTINGS, now=base)

    counts = repo.get_refresh_health_counts(now=base + timedelta(days=2))
    assert counts["active"] >= 2  # healthy + backing (still scheduled)
    assert counts["backing_off"] >= 1
    assert counts["parked_by_reason"].get("feed_gone", 0) >= 1
    assert counts["parked_total"] >= 1


def test_get_due_podcasts_excludes_inactive(repo):
    base = datetime(2026, 7, 1, tzinfo=timezone.utc)
    done = _mk_podcast(is_complete=True)
    repo.save(done)
    _exec(
        repo,
        "UPDATE podcasts SET next_refresh_at = ? WHERE id = ?",
        (_ts(repo, base - timedelta(hours=1)), done.id),
    )
    assert done.id not in repo.get_due_podcasts(now=base)


# ---------------------------------------------------------------------------
# Import helpers: synthetic parent / auto-added upsert / real parent lookup
# ---------------------------------------------------------------------------
def test_synthetic_parent_is_deterministic_and_idempotent(repo):
    id1 = repo.ensure_synthetic_audio_imports_parent()
    id2 = repo.ensure_synthetic_audio_imports_parent()
    assert id1 == id2 == SYNTHETIC_AUDIO_IMPORTS_ID
    # The synthetic:// rss_url can't hydrate into a Podcast model (HttpUrl
    # validation) on either backend, so verify the row directly.
    rows = _exec(repo, "SELECT title, rss_url, synthetic, auto_added FROM podcasts WHERE id = ?", (id1,), fetch=True)
    assert len(rows) == 1
    assert rows[0]["title"] == "Audio imports"
    assert rows[0]["rss_url"] == "synthetic://audio-imports"
    assert bool(rows[0]["synthetic"]) is True
    assert bool(rows[0]["auto_added"]) is False


def test_upsert_auto_added_podcast(repo):
    url = f"https://p1.test/{_uniq()}/channel.xml"
    pid, title, slug = repo.upsert_auto_added_podcast(
        rss_url=url, title="Deduced Channel", description="d", image_url="https://p1.test/a.png"
    )
    assert title == "Deduced Channel"
    assert slug == "deduced-channel"

    # Second call returns the existing row unchanged (title does not move).
    pid2, title2, slug2 = repo.upsert_auto_added_podcast(rss_url=url, title="Different Title")
    assert (pid2, title2, slug2) == (pid, "Deduced Channel", slug)

    # A manually-subscribed podcast is returned as-is, never overwritten.
    manual = _mk_podcast(title="Manual Show")
    repo.save(manual)
    mid, mtitle, mslug = repo.upsert_auto_added_podcast(rss_url=str(manual.rss_url), title="Ignored")
    assert (mid, mtitle, mslug) == (manual.id, "Manual Show", manual.slug)


def test_get_real_parent_podcast_for_episode(repo):
    p = _mk_podcast(title="Real Parent")
    e = _mk_episode()
    p.episodes = [e]
    repo.save(p)
    assert repo.get_real_parent_podcast_for_episode(e.id) == (p.id, "Real Parent", p.slug)

    # Episode under the synthetic parent → None.
    syn_id = repo.ensure_synthetic_audio_imports_parent()
    syn_ep_id = str(uuid.uuid4())
    now = _ts(repo, datetime.now(timezone.utc))
    _exec(
        repo,
        """
        INSERT INTO episodes (id, podcast_id, created_at, updated_at, external_id,
                              title, slug, description, description_html, audio_url)
        VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
        """,
        (syn_ep_id, syn_id, now, now, f"imp-{_uniq()}", "Imported", "imported", "", "", "https://p1.test/i.mp3"),
    )
    assert repo.get_real_parent_podcast_for_episode(syn_ep_id) is None
    assert repo.get_real_parent_podcast_for_episode(str(uuid.uuid4())) is None


# ---------------------------------------------------------------------------
# Categories (lazy id<->pair resolution on PG; seeded taxonomy on SQLite)
# ---------------------------------------------------------------------------
def _seed_pg_categories(repo):
    """Seed a minimal Comedy/Improv/News taxonomy on PG (SQLite seeds Apple's
    full taxonomy at init, so it already has these)."""
    if not _is_pg(repo):
        return
    comedy = _exec(
        repo,
        "INSERT INTO categories (name, slug, parent_id) VALUES (?, ?, NULL) RETURNING id",
        ("Comedy", "comedy"),
        fetch=True,
    )[0]["id"]
    _exec(
        repo,
        "INSERT INTO categories (name, slug, parent_id) VALUES (?, ?, ?)",
        ("Improv", "improv", comedy),
    )
    _exec(
        repo,
        "INSERT INTO categories (name, slug, parent_id) VALUES (?, ?, NULL)",
        ("News", "news"),
    )


def test_category_resolution_roundtrip(repo):
    _seed_pg_categories(repo)
    p = _mk_podcast(
        primary_category="Comedy",
        primary_subcategory="Improv",
        secondary_category="News",
    )
    repo.save(p)
    got = repo.get(p.id)
    assert got.primary_category == "Comedy"
    assert got.primary_subcategory == "Improv"
    assert got.secondary_category == "News"
    assert got.secondary_subcategory is None

    # Unknown top-level category resolves to None (best-effort, no error);
    # a known top with an unknown sub falls back to the top-level row.
    p2 = _mk_podcast(
        primary_category="Not A Real Category",
        primary_subcategory="Nope",
        secondary_category="Comedy",
        secondary_subcategory="Nonexistent Sub",
    )
    repo.save(p2)
    got2 = repo.get(p2.id)
    assert got2.primary_category is None
    assert got2.primary_subcategory is None
    assert got2.secondary_category == "Comedy"
    assert got2.secondary_subcategory is None


@pytest.mark.skipif(not PG_OK, reason="Postgres not reachable — set TEST_DATABASE_URL")
def test_pg_empty_categories_resolve_gracefully():
    """With an EMPTY categories table (no seeding on PG), category strings
    resolve to None and reads succeed — no crash, no cache poisoning."""
    import psycopg

    from thestill.repositories.postgres_schema import ensure_schema

    ensure_schema(PG_DSN)
    with psycopg.connect(PG_DSN) as conn:
        conn.execute(f"TRUNCATE {_PG_TABLES} CASCADE")
    repo = _PgRepo(PG_DSN)
    p = _mk_podcast(primary_category="Comedy", primary_subcategory="Improv")
    repo.save(p)
    got = repo.get(p.id)
    assert got.primary_category is None
    assert got.primary_subcategory is None


@pytest.mark.skipif(not PG_OK, reason="Postgres not reachable — set TEST_DATABASE_URL")
def test_pg_get_podcast_for_episode():
    """PG-only: the SQLite original under-projects columns and crashes
    (latent bug); the port selects the full column list and works."""
    import psycopg

    from thestill.repositories.postgres_schema import ensure_schema

    ensure_schema(PG_DSN)
    with psycopg.connect(PG_DSN) as conn:
        conn.execute(f"TRUNCATE {_PG_TABLES} CASCADE")
    repo = _PgRepo(PG_DSN)
    p = _mk_podcast()
    e = _mk_episode()
    p.episodes = [e]
    repo.save(p)
    got = repo.get_podcast_for_episode(e.id)
    assert got is not None
    assert got.id == p.id
    assert [x.external_id for x in got.episodes] == [e.external_id]
    assert repo.get_podcast_for_episode(str(uuid.uuid4())) is None


# ---------------------------------------------------------------------------
# Top podcasts (chart) — seeded via direct SQL on both backends
# ---------------------------------------------------------------------------
def _seed_chart(repo, region: str):
    """Seed 3 chart rows in ``region``: Comedy-sub, News, and uncategorised."""
    _seed_pg_categories(repo)
    comedy_sub_id = _exec(
        repo,
        "SELECT c.id FROM categories c JOIN categories p ON p.id = c.parent_id " "WHERE c.name = ? AND p.name = ?",
        ("Improv", "Comedy"),
        fetch=True,
    )[0]["id"]
    news_id = _exec(
        repo,
        "SELECT id FROM categories WHERE name = ? AND parent_id IS NULL",
        ("News",),
        fetch=True,
    )[
        0
    ]["id"]

    now = _ts(repo, datetime.now(timezone.utc))
    u = _uniq()
    entries = [
        (f"Alpha Laughs {u}", "Ann Artist", f"https://p1.test/{u}/tp1.xml", comedy_sub_id, 1, "Improv"),
        (f"Beta Bulletin {u}", "Bob Broadcaster", f"https://p1.test/{u}/tp2.xml", news_id, 2, "News"),
        (f"Gamma Show {u}", None, f"https://p1.test/{u}/tp3.xml", None, 3, None),
    ]
    rss_urls = []
    for name, artist, rss, cat_id, rank, genre in entries:
        top_id = _exec(
            repo,
            "INSERT INTO top_podcasts (name, artist, rss_url, apple_url, youtube_url, apple_track_id,"
            " category_id, first_seen_at, last_seen_at) VALUES (?, ?, ?, NULL, NULL, NULL, ?, ?, ?) RETURNING id",
            (name, artist, rss, cat_id, now, now),
            fetch=True,
        )[0]["id"]
        _exec(
            repo,
            "INSERT INTO top_podcast_rankings (top_podcast_id, region, rank, source_genre, scraped_at)"
            " VALUES (?, ?, ?, ?, ?)",
            (top_id, region, rank, genre, now),
        )
        rss_urls.append(rss)
    _exec(
        repo,
        "INSERT INTO top_podcasts_meta (region, source_path, source_mtime, row_count, seeded_at)"
        " VALUES (?, ?, ?, ?, ?)",
        (region, f"/tmp/top_podcasts_{region}.json", 1.0, len(entries), now),
    )
    return rss_urls


def test_top_podcasts_chart_queries(repo):
    region = "zz"
    rss_urls = _seed_chart(repo, region)

    # Regions list (SQLite pre-seeds real regions from data/*.json → membership).
    assert region in repo.get_top_podcast_regions()

    # Category picker rolls sub-categories up to the parent, drops NULLs.
    assert repo.get_top_podcast_categories(region) == ["Comedy", "News"]
    assert repo.get_top_podcast_categories("") == []

    rows = repo.get_top_podcasts(region)
    assert [r["rank"] for r in rows] == [1, 2, 3]
    assert rows[0]["rss_url"] == rss_urls[0]
    assert rows[0]["category"] == "Improv"
    assert rows[2]["category"] is None
    assert all(r["is_following"] is False for r in rows)
    assert all(r["podcast_slug"] is None for r in rows)  # not imported yet

    # Case-insensitive substring search on name AND artist, rank order kept.
    assert [r["rss_url"] for r in repo.get_top_podcasts(region, q="alpha laughs")] == [rss_urls[0]]
    assert [r["rss_url"] for r in repo.get_top_podcasts(region, q="bob broad")] == [rss_urls[1]]
    assert repo.get_top_podcasts(region, q="zzz-no-match") == []

    # Top-level category filter includes rows tagged with its sub-categories.
    assert [r["rss_url"] for r in repo.get_top_podcasts(region, category="Comedy")] == [rss_urls[0]]
    assert [r["rss_url"] for r in repo.get_top_podcasts(region, category="News")] == [rss_urls[1]]

    assert repo.get_top_podcasts("", limit=5) == []
    assert len(repo.get_top_podcasts(region, limit=2)) == 2

    # Region gate for the free tier (case-insensitive region).
    assert repo.is_top_podcast_in_region(rss_urls[0], region) is True
    assert repo.is_top_podcast_in_region(rss_urls[0], region.upper()) is True
    assert repo.is_top_podcast_in_region("https://p1.test/none.xml", region) is False
    assert repo.is_top_podcast_in_region("", region) is False
    assert repo.is_top_podcast_in_region(rss_urls[0], "") is False


def test_top_podcasts_following_join(repo):
    region = "zy"
    rss_urls = _seed_chart(repo, region)

    # Import the #1 chart entry as a real podcast and follow it.
    p = _mk_podcast(rss_url=rss_urls[0], image_url="https://p1.test/art.png")
    repo.save(p)
    user_id = _seed_user_and_follow(repo, p.id)

    rows = repo.get_top_podcasts(region, user_id=user_id)
    assert rows[0]["is_following"] is True
    assert rows[0]["podcast_slug"] == p.slug
    assert rows[0]["image_url"] == "https://p1.test/art.png"
    assert rows[1]["is_following"] is False

    # Anonymous user: LEFT JOIN misses → everything False, slug still surfaced.
    rows = repo.get_top_podcasts(region, user_id=None)
    assert rows[0]["is_following"] is False
    assert rows[0]["podcast_slug"] == p.slug


# ---------------------------------------------------------------------------
# Health
# ---------------------------------------------------------------------------
def test_get_chunks_health_empty(repo):
    assert repo.get_chunks_health() == (0, "")
