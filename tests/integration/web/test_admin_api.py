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

"""Spec #104 Phase 4 — the admin console API over HTTP."""

from __future__ import annotations

from tests.integration.web.test_abuse_controls_api import (  # noqa: F401 — fixtures
    _fresh_buckets,
    _podcast,
    _sign_in,
    _user,
    app_config,
    app_state,
    client,
)
from thestill.models.user import UserStatus


def _admin(client, app_state):
    admin = _user(app_state, "op@example.com", is_admin=True)
    _sign_in(client, app_state, admin)
    return admin


def test_admin_routes_need_an_admin(client, app_state):
    _sign_in(client, app_state, _user(app_state, "reader@example.com"))
    for method, path in (
        ("get", "/api/admin/users"),
        ("get", "/api/admin/settings"),
        ("get", "/api/admin/audit"),
        ("post", "/api/admin/alerts/test"),
    ):
        assert getattr(client, method)(path).status_code == 403, path
    client.cookies.clear()
    assert client.get("/api/admin/users").status_code == 401


def test_users_list_puts_the_ones_worth_a_look_first(client, app_state):
    _admin(client, app_state)
    quiet = _user(app_state, "quiet@example.com")
    noisy = _user(app_state, "noisy@example.com")
    from thestill.repositories.usage_event_repository import UsageRecord
    from thestill.utils.datetime_utils import now_utc

    app_state.abuse.quota._usage.record(noisy.id, [UsageRecord("quota_exceeded", "q")] * 2, now=now_utc())
    body = client.get("/api/admin/users").json()
    assert body["total"] == 3 and body["users"][0]["email"] == "noisy@example.com"
    assert body["users"][0]["quota_hits_24h"] == 2
    assert {u["email"] for u in body["users"]} >= {quiet.email}

    found = client.get("/api/admin/users?q=QUIET").json()
    assert [u["email"] for u in found["users"]] == ["quiet@example.com"]
    assert client.get("/api/admin/users?sort=nonsense").status_code == 422


def test_user_detail_and_status_actions_are_audited(client, app_state):
    admin = _admin(client, app_state)
    target = _user(app_state, "spam@example.com")
    podcast = _podcast(app_state, "show")
    app_state.follower_service.follow(target.id, podcast.id)

    detail = client.get(f"/api/admin/users/{target.id}").json()
    assert detail["user"]["status"] == "active"
    assert detail["follows"][0]["slug"] == "show" and detail["follows"][0]["introduced"] is True
    assert detail["usage_24h"] == {"follow": 1, "feed_introduced": 1}
    assert len(detail["daily_7d"]["follow"]) == 7

    assert client.post(f"/api/admin/users/{target.id}/status", json={"status": "banned"}).status_code == 409
    banned = client.post(
        f"/api/admin/users/{target.id}/status",
        json={"status": "banned", "reason": "script", "remove_introduced_feeds": True},
    )
    assert banned.status_code == 200 and banned.json()["user"]["status"] == "banned"
    assert app_state.user_repository.get_by_id(target.id).status == UserStatus.BANNED

    history = client.get(f"/api/admin/users/{target.id}").json()["history"]
    assert history[0]["action"] == "user.ban" and history[0]["actor"] == "web"
    assert history[0]["actor_user_id"] == admin.id

    assert client.post(f"/api/admin/users/{target.id}/status", json={"status": "active"}).status_code == 200
    assert client.post(f"/api/admin/users/{target.id}/logout-everywhere").status_code == 200
    removed = client.post(f"/api/admin/users/{target.id}/remove-introduced-feeds").json()
    assert removed["feeds_removed"] == []  # the ban already dropped it


def test_an_admin_cannot_disable_or_demote_themselves(client, app_state):
    admin = _admin(client, app_state)
    _user(app_state, "other-admin@example.com", is_admin=True)
    refused = client.post(f"/api/admin/users/{admin.id}/status", json={"status": "suspended", "reason": "x"})
    assert refused.status_code == 409 and refused.json()["detail"]["error"] == "cannot_modify_self"
    assert client.post(f"/api/admin/users/{admin.id}/admin", json={"is_admin": False}).status_code == 409


def test_quotas_and_admin_role(client, app_state):
    _admin(client, app_state)
    target = _user(app_state, "u@example.com")
    saved = client.put(f"/api/admin/users/{target.id}/quotas", json={"imports_per_day": 9})
    assert saved.json()["quota_overrides"] == {"imports_per_day": 9}
    assert client.put(f"/api/admin/users/{target.id}/quotas", json={"nope": 1}).status_code == 409
    assert client.put(f"/api/admin/users/{target.id}/quotas", json={}).json()["quota_overrides"] == {}

    assert client.post(f"/api/admin/users/{target.id}/admin", json={"is_admin": True}).json()["user"]["is_admin"]
    actions = [e["action"] for e in client.get("/api/admin/audit").json()["entries"]]
    assert actions[:3] == ["admin.grant", "user.quota_override", "user.quota_override"]


def test_settings_read_write_reset_and_test_alert(client, app_state):
    _admin(client, app_state)
    body = client.get("/api/admin/settings").json()
    keys = {s["key"]: s for s in body["settings"]}
    assert keys["signup_mode"]["value"] == "open" and keys["signup_mode"]["source"] == "env"
    assert body["non_default"] == {} and body["alert_channels"] == ["log"]

    changed = client.put("/api/admin/settings/signup_mode", json={"value": "closed"})
    assert changed.json()["setting"]["source"] == "runtime"
    assert client.get("/api/admin/settings").json()["non_default"] == {"signup_mode": "closed"}
    assert client.put("/api/admin/settings/signup_mode", json={"value": "maybe"}).status_code == 422
    assert client.put("/api/admin/settings/nope", json={"value": 1}).status_code == 404
    assert client.delete("/api/admin/settings/signup_mode").json()["setting"]["value"] == "open"

    assert client.post("/api/admin/alerts/test").json()["results"] == {"log": True}

    audit = client.get("/api/admin/audit?action=setting.set").json()
    assert audit["total"] == 2 and audit["entries"][0]["actor_email"] == "op@example.com"
    assert client.get("/api/admin/audit?action=bogus").status_code == 422


def test_admin_writes_pass_read_only_mode(client, app_state):
    _admin(client, app_state)
    app_state.abuse.settings.set(
        "read_only", True, actor=__import__("thestill.services.abuse.actor", fromlist=["Actor"]).Actor.cli()
    )
    assert client.delete("/api/admin/settings/read_only").status_code == 200


def test_audit_lists_its_actions(client, app_state):
    _admin(client, app_state)
    body = client.get("/api/admin/audit").json()
    assert "user.ban" in body["actions"] and "setting.set" in body["actions"]
