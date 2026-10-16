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

"""Spec #90 — listening progress rules (``plan_row``) and service orchestration."""

import math
import uuid
from datetime import datetime, timedelta, timezone
from unittest.mock import MagicMock

import pytest

from thestill.models.listening import ListeningProgress, ProgressWrite
from thestill.repositories.sqlite_listening_progress_repository import SqliteListeningProgressRepository
from thestill.repositories.sqlite_podcast_repository import SqlitePodcastRepository
from thestill.services.listening_progress_service import (
    InvalidProgressError,
    ListeningEpisodeNotFoundError,
    ListeningProgressService,
    plan_row,
)
from thestill.utils.sqlite_ext import connect

NOW = datetime(2026, 10, 16, 9, 0, tzinfo=timezone.utc)
USER = "aaaaaaaa-aaaa-4aaa-8aaa-aaaaaaaaaaaa"
EP = "eeeeeeee-eeee-4eee-8eee-eeeeeeeeeeee"


def _write(event="tick", position=600.0, duration=3600.0, session="tab-a", seq=1) -> ProgressWrite:
    return ProgressWrite(event=event, position_seconds=position, duration_seconds=duration, session_id=session, seq=seq)


def _plan(write, stored=None):
    return plan_row(write, user_id=USER, episode_id=EP, stored=stored, now=NOW)


def _stored(**overrides) -> ListeningProgress:
    base = dict(
        user_id=USER,
        episode_id=EP,
        position_seconds=600.0,
        duration_seconds=3600.0,
        session_id="tab-a",
        seq=5,
        updated_at=NOW - timedelta(minutes=5),
    )
    base.update(overrides)
    return ListeningProgress(**base)


# ---------------------------------------------------------------------------
# plan_row — pure rules
# ---------------------------------------------------------------------------
class TestPlanRow:
    def test_tick_stores_position_and_server_time(self):
        row = _plan(_write("tick", position=1934.2, seq=41))
        assert row.position_seconds == 1934.2
        assert row.duration_seconds == 3600.0
        assert row.completed_at is None and row.stopped_at is None
        assert row.session_id == "tab-a" and row.seq == 41
        assert row.updated_at == NOW

    @pytest.mark.parametrize("bad", [-0.1, math.nan, math.inf, -math.inf])
    def test_rejects_non_finite_or_negative_position(self, bad):
        with pytest.raises(InvalidProgressError):
            _plan(_write(position=bad))

    @pytest.mark.parametrize("session", ["", "x" * 65])
    def test_rejects_bad_session_id(self, session):
        with pytest.raises(InvalidProgressError):
            _plan(_write(session=session))

    def test_clamps_beyond_duration_plus_slack(self):
        assert _plan(_write("seek", position=3604.0)).position_seconds == 3604.0
        assert _plan(_write("seek", position=3700.0)).position_seconds == 3600.0

    @pytest.mark.parametrize("event", ["pause", "stop"])
    def test_pause_or_stop_within_45s_of_the_end_completes(self, event):
        row = _plan(_write(event, position=3555.0))
        assert row.completed_at == NOW
        assert row.position_seconds == 0.0

    def test_46s_left_does_not_complete(self):
        row = _plan(_write("pause", position=3554.0))
        assert row.completed_at is None
        assert row.position_seconds == 3554.0

    @pytest.mark.parametrize("event", ["tick", "seek"])
    def test_tick_and_seek_never_complete(self, event):
        """A backgrounded tab still playing near the end is not finished."""
        assert _plan(_write(event, position=3599.0)).completed_at is None

    def test_ended_completes_even_without_a_duration(self):
        row = _plan(_write("ended", position=0.0, duration=None))
        assert row.completed_at == NOW and row.position_seconds == 0.0

    def test_unknown_duration_completes_only_on_ended(self):
        assert _plan(_write("pause", position=10.0, duration=None)).completed_at is None

    @pytest.mark.parametrize("unusable", [0.0, math.nan, math.inf])
    def test_unusable_engine_duration_is_stored_as_unknown(self, unusable):
        assert _plan(_write(duration=unusable)).duration_seconds is None

    def test_stored_duration_backs_the_45s_rule_when_the_write_has_none(self):
        row = _plan(_write("pause", position=3570.0, duration=None), stored=_stored())
        assert row.completed_at == NOW
        assert row.duration_seconds == 3600.0

    def test_stop_sets_stopped_at_and_any_other_write_clears_it(self):
        assert _plan(_write("stop", position=100.0)).stopped_at == NOW
        assert _plan(_write("tick", position=100.0), stored=_stored(stopped_at=NOW)).stopped_at is None

    def test_mark_played_completes_at_zero_ignoring_the_position(self):
        row = _plan(_write("mark_played", position=math.nan))
        assert row.completed_at == NOW and row.position_seconds == 0.0

    def test_mark_unplayed_clears_completion_at_zero(self):
        row = _plan(_write("mark_unplayed", position=999.0), stored=_stored(completed_at=NOW, position_seconds=0.0))
        assert row.completed_at is None and row.position_seconds == 0.0

    def test_non_completing_write_clears_a_previous_completion(self):
        row = _plan(_write("tick", position=30.0), stored=_stored(completed_at=NOW - timedelta(days=1)))
        assert row.completed_at is None


# ---------------------------------------------------------------------------
# ListeningProgressService — orchestration over real SQLite
# ---------------------------------------------------------------------------
def _seed(db: str) -> None:
    with connect(db) as conn:
        conn.execute(
            "INSERT INTO users (id, email, created_at) VALUES (?, 'a@x.test', ?)",
            (USER, NOW.isoformat()),
        )
        conn.execute(
            "INSERT INTO podcasts (id, created_at, updated_at, rss_url, title, slug, description, language)"
            " VALUES ('pppppppp-pppp-4ppp-8ppp-pppppppppppp', ?, ?, 'https://x.test/f.xml', 'Show', 'show', '', 'en')",
            (NOW.isoformat(), NOW.isoformat()),
        )
        conn.execute(
            "INSERT INTO episodes (id, podcast_id, created_at, updated_at, external_id, title, slug, audio_url, duration)"
            " VALUES (?, 'pppppppp-pppp-4ppp-8ppp-pppppppppppp', ?, ?, 'x1', 'Ep', 'ep', 'https://x.test/e.mp3', 3600)",
            (EP, NOW.isoformat(), NOW.isoformat()),
        )


@pytest.fixture
def svc(tmp_path):
    db = str(tmp_path / "svc.db")
    podcasts = SqlitePodcastRepository(db_path=db)
    _seed(db)
    clock = MagicMock(return_value=NOW)
    mark_read = MagicMock(return_value=True)
    service = ListeningProgressService(
        SqliteListeningProgressRepository(db_path=db), podcasts, mark_read=mark_read, clock=clock
    )
    return service, mark_read, clock


class TestRecord:
    def test_unknown_or_malformed_episode_raises_not_found(self, svc):
        service, _, _ = svc
        with pytest.raises(ListeningEpisodeNotFoundError):
            service.record(USER, str(uuid.uuid4()), _write())
        with pytest.raises(ListeningEpisodeNotFoundError):
            service.record(USER, "not-a-uuid", _write())

    def test_accepted_write_returns_the_stored_summary(self, svc):
        service, mark_read, _ = svc
        outcome = service.record(USER, EP, _write(position=120.0, seq=1))
        assert outcome.accepted and not outcome.completed
        assert outcome.listening.position_seconds == 120.0
        assert outcome.listening.updated_at == NOW
        mark_read.assert_not_called()

    def test_out_of_order_write_is_rejected_and_returns_the_winner(self, svc):
        service, _, _ = svc
        service.record(USER, EP, _write(position=500.0, seq=10))
        outcome = service.record(USER, EP, _write(position=400.0, seq=9))
        assert not outcome.accepted
        assert outcome.listening.position_seconds == 500.0

    def test_other_session_is_accepted(self, svc):
        service, _, _ = svc
        service.record(USER, EP, _write(position=500.0, seq=10))
        assert service.record(USER, EP, _write(position=60.0, session="phone", seq=0)).accepted

    def test_completion_marks_the_inbox_row_read(self, svc):
        service, mark_read, _ = svc
        outcome = service.record(USER, EP, _write("ended", position=3600.0, seq=2))
        assert outcome.completed
        assert outcome.listening.position_seconds == 0.0
        mark_read.assert_called_once_with(USER, EP)

    def test_mark_played_over_a_higher_stored_seq(self, svc):
        service, mark_read, _ = svc
        service.record(USER, EP, _write(position=500.0, session="tab-a", seq=40))
        outcome = service.record(USER, EP, _write("mark_played", session="tab-a", seq=0))
        assert outcome.accepted and outcome.completed
        mark_read.assert_called_once_with(USER, EP)

    def test_mark_unplayed_does_not_touch_the_inbox(self, svc):
        service, mark_read, _ = svc
        service.record(USER, EP, _write("mark_played", session="m1", seq=0))
        mark_read.reset_mock()
        outcome = service.record(USER, EP, _write("mark_unplayed", session="m2", seq=0))
        assert outcome.accepted and not outcome.completed
        assert outcome.listening.completed_at is None
        mark_read.assert_not_called()

    def test_failed_mark_read_does_not_fail_the_write(self, svc):
        service, mark_read, _ = svc
        mark_read.side_effect = RuntimeError("inbox down")
        outcome = service.record(USER, EP, _write("ended", seq=1))
        assert outcome.accepted and outcome.completed
        assert service.get_summary(USER, EP).completed_at == NOW


class TestReads:
    def test_get_summary_none_when_never_played(self, svc):
        service, _, _ = svc
        assert service.get_summary(USER, EP) is None

    def test_latest_hydrates_a_player_track(self, svc):
        service, _, _ = svc
        service.record(USER, EP, _write(position=700.0, seq=1))
        item = service.latest(USER)
        assert item.listening.position_seconds == 700.0
        episode = item.episode
        assert episode["id"] == EP
        assert episode["slug"] == "ep" and episode["podcast_slug"] == "show"
        assert episode["audio_url"] == "https://x.test/e.mp3"
        assert episode["playback"]["kind"] == "audio"

    def test_stop_hides_from_latest_until_the_next_write(self, svc):
        service, _, _ = svc
        service.record(USER, EP, _write(position=700.0, seq=1))
        service.record(USER, EP, _write("stop", position=710.0, seq=2))
        assert service.latest(USER) is None
        # Stop dismisses the bar, not the episode: still in progress.
        assert [i.episode["id"] for i in service.list_in_progress(USER, 3)] == [EP]

        service.record(USER, EP, _write("tick", position=725.0, session="next-play", seq=1))
        assert service.latest(USER).listening.position_seconds == 725.0

    def test_old_row_is_still_the_restore_candidate(self, svc):
        service, _, clock = svc
        clock.return_value = NOW - timedelta(days=365)
        service.record(USER, EP, _write(position=700.0, seq=1))
        assert service.latest(USER) is not None

    def test_continue_listening_shows_only_the_last_3_days(self, svc):
        """Abandoned episodes drop out of the list; they keep their position
        and are still the restore candidate."""
        service, _, clock = svc
        clock.return_value = NOW - timedelta(days=3, minutes=1)
        service.record(USER, EP, _write(position=700.0, seq=1))
        clock.return_value = NOW
        assert service.list_in_progress(USER, 3) == []
        assert service.latest(USER).listening.position_seconds == 700.0
        assert service.get_summary(USER, EP).position_seconds == 700.0

        clock.return_value = NOW - timedelta(days=2)
        service.record(USER, EP, _write(position=710.0, seq=2))
        clock.return_value = NOW
        assert [i.episode["id"] for i in service.list_in_progress(USER, 3)] == [EP]

    def test_completed_or_unplayed_rows_are_not_in_progress(self, svc):
        service, _, _ = svc
        service.record(USER, EP, _write("mark_played", seq=0))
        assert service.latest(USER) is None
        service.record(USER, EP, _write("mark_unplayed", session="m2", seq=0))
        assert service.latest(USER) is None
        assert service.list_in_progress(USER, 3) == []
