"""Spec #90 — /api/me/listening routes over a real SQLite store."""

import json
from unittest.mock import MagicMock

import pytest
from fastapi import Depends, FastAPI
from fastapi.testclient import TestClient

from thestill.models.user import User
from thestill.repositories.sqlite_listening_progress_repository import SqliteListeningProgressRepository
from thestill.repositories.sqlite_podcast_repository import SqlitePodcastRepository
from thestill.repositories.sqlite_user_repository import SqliteUserRepository
from thestill.services.listening_progress_service import ListeningProgressService
from thestill.utils.sqlite_ext import connect
from thestill.web.dependencies import get_app_state, require_auth
from thestill.web.routes import api_me_listening

from .auth_harness import ADMIN_USER, PLAIN_USER

ROUTE = "/api/me/listening"
EP = "eeeeeeee-eeee-4eee-8eee-eeeeeeeeeeee"
MISSING = "00000000-0000-4000-8000-000000000000"


@pytest.fixture(autouse=True)
def _uuid_users(monkeypatch):
    # The SQLite users table enforces 36-char ids; the harness users are short.
    monkeypatch.setattr(ADMIN_USER, "id", "aaaaaaaa-aaaa-4aaa-8aaa-aaaaaaaaaaaa")
    monkeypatch.setattr(PLAIN_USER, "id", "bbbbbbbb-bbbb-4bbb-8bbb-bbbbbbbbbbbb")


def _client(tmp_path, *, current_user: User | None):
    db = str(tmp_path / "t.db")
    podcasts = SqlitePodcastRepository(db_path=db)
    users = SqliteUserRepository(db_path=db)
    for u in (ADMIN_USER, PLAIN_USER):
        users.save(u)
    with connect(db) as conn:
        conn.execute(
            "INSERT INTO podcasts (id, rss_url, title, slug, description, language)"
            " VALUES ('pppppppp-pppp-4ppp-8ppp-pppppppppppp', 'https://x.test/f.xml', 'Show', 'show', '', 'en')"
        )
        conn.execute(
            "INSERT INTO episodes (id, podcast_id, external_id, title, slug, audio_url, duration)"
            " VALUES (?, 'pppppppp-pppp-4ppp-8ppp-pppppppppppp', 'x1', 'Ep', 'ep', 'https://x.test/e.mp3', 3600)",
            (EP,),
        )
    mark_read = MagicMock(return_value=True)
    state = MagicMock()
    state.auth_service.get_current_user.return_value = current_user
    state.listening_service = ListeningProgressService(
        SqliteListeningProgressRepository(db_path=db), podcasts, mark_read=mark_read
    )

    app = FastAPI()
    app.include_router(api_me_listening.router, prefix=ROUTE, dependencies=[Depends(require_auth)])
    app.dependency_overrides[get_app_state] = lambda: state
    return TestClient(app, raise_server_exceptions=False), mark_read


def _body(res):
    return res.json()


def _put(client, episode_id=EP, **body):
    payload = {"event": "tick", "position_seconds": 600.0, "duration_seconds": 3600.0, "session_id": "s1", "seq": 1}
    payload.update(body)
    return client.put(f"{ROUTE}/{episode_id}", json=payload)


def test_anonymous_is_401(tmp_path):
    client, _ = _client(tmp_path, current_user=None)
    assert client.get(f"{ROUTE}/latest").status_code == 401
    assert _put(client).status_code == 401


def test_put_then_get_round_trip(tmp_path):
    client, _ = _client(tmp_path, current_user=PLAIN_USER)
    res = _put(client, position_seconds=1934.2, seq=41)
    assert res.status_code == 200
    data = res.json()
    assert data["accepted"] is True and data["completed"] is False
    assert data["listening"]["position_seconds"] == 1934.2

    item = client.get(f"{ROUTE}/{EP}").json()["item"]
    assert item["position_seconds"] == 1934.2
    assert item["duration_seconds"] == 3600.0
    assert set(item) == {"position_seconds", "duration_seconds", "completed_at", "updated_at"}


def test_get_never_played_is_null_item(tmp_path):
    client, _ = _client(tmp_path, current_user=PLAIN_USER)
    assert _body(client.get(f"{ROUTE}/{EP}"))["item"] is None


def test_out_of_order_write_is_accepted_false(tmp_path):
    client, _ = _client(tmp_path, current_user=PLAIN_USER)
    _put(client, position_seconds=500.0, seq=10)
    body = _body(_put(client, position_seconds=400.0, seq=9))
    assert body["accepted"] is False
    assert body["listening"]["position_seconds"] == 500.0


def test_unknown_or_malformed_episode_is_404(tmp_path):
    client, _ = _client(tmp_path, current_user=PLAIN_USER)
    assert _put(client, episode_id=MISSING).status_code == 404
    assert _put(client, episode_id="not-a-uuid").status_code == 404


@pytest.mark.parametrize("position", [-1.0, "NaN", "Infinity"])
def test_negative_or_non_finite_position_is_400(tmp_path, position):
    client, _ = _client(tmp_path, current_user=PLAIN_USER)
    raw = '{"event": "tick", "position_seconds": %s, "session_id": "s1", "seq": 1}' % (
        json.dumps(position) if isinstance(position, float) else position
    )
    res = client.put(f"{ROUTE}/{EP}", content=raw, headers={"Content-Type": "application/json"})
    assert res.status_code == 400


def test_unknown_event_is_rejected(tmp_path):
    client, _ = _client(tmp_path, current_user=PLAIN_USER)
    assert _put(client, event="rewind").status_code == 422


def test_ended_completes_and_marks_read(tmp_path):
    client, mark_read = _client(tmp_path, current_user=PLAIN_USER)
    body = _body(_put(client, event="ended", position_seconds=3600.0))
    assert body["completed"] is True
    assert body["listening"]["position_seconds"] == 0.0
    mark_read.assert_called_once_with(PLAIN_USER.id, EP)


def test_latest_route_is_not_captured_as_an_episode_id(tmp_path):
    client, _ = _client(tmp_path, current_user=PLAIN_USER)
    assert _body(client.get(f"{ROUTE}/latest"))["item"] is None

    _put(client, position_seconds=700.0)
    item = _body(client.get(f"{ROUTE}/latest"))["item"]
    assert item["listening"]["position_seconds"] == 700.0
    assert item["episode"]["id"] == EP
    assert item["episode"]["podcast_slug"] == "show"
    assert item["episode"]["playback"]["audio"]["url"] == "https://x.test/e.mp3"


def test_in_progress_list_and_stop(tmp_path):
    client, _ = _client(tmp_path, current_user=PLAIN_USER)
    _put(client, position_seconds=700.0, seq=1)
    _put(client, event="stop", position_seconds=710.0, seq=2)

    assert _body(client.get(f"{ROUTE}/latest"))["item"] is None
    items = _body(client.get(ROUTE, params={"state": "in_progress", "limit": 3}))["items"]
    assert [i["episode"]["id"] for i in items] == [EP]
    assert client.get(ROUTE, params={"state": "completed"}).status_code == 422
    assert client.get(ROUTE, params={"limit": 50}).status_code == 422


def test_progress_is_per_user(tmp_path):
    client, _ = _client(tmp_path, current_user=PLAIN_USER)
    _put(client, position_seconds=700.0)
    state = client.app.dependency_overrides[get_app_state]()
    state.auth_service.get_current_user.return_value = ADMIN_USER
    assert _body(client.get(f"{ROUTE}/{EP}"))["item"] is None
