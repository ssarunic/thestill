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

"""Spec #104 §1.1, §1.4–§1.6 — the operator's actions on an account."""

from __future__ import annotations

from datetime import timedelta

import pytest

from thestill.core.queue_manager import TaskStage, TaskStatus
from thestill.models.user import UserStatus
from thestill.services.abuse.account_admin_service import revocation_instant
from thestill.services.abuse.actor import Actor
from thestill.services.abuse.errors import AdminActionError

from .conftest import NOW, make_world


@pytest.fixture
def admin(world):
    return world.add_user("op@example.com", is_admin=True)


def _actions(world, user_id):
    return [e.action for e in world.repos.audit_log.list(target_user_id=user_id)]


def test_revocation_instant_rounds_up_to_the_next_second():
    assert revocation_instant(NOW.replace(microsecond=1)) == NOW + timedelta(seconds=1)
    assert revocation_instant(NOW) == NOW + timedelta(seconds=1)


def test_suspend_keeps_follows_and_revokes_sessions(world, admin):
    user = world.add_user("spam@example.com")
    pid, _ = world.add_podcast("show")
    world.follow(user.id, pid)

    world.abuse.account_admin.suspend(user.id, reason="looking into it", actor=Actor.web(admin.id))

    stored = world.repos.user.get_by_id(user.id)
    assert stored.status == UserStatus.SUSPENDED
    assert stored.status_reason == "looking into it"
    assert stored.status_changed_by == admin.id
    assert stored.tokens_valid_after is not None
    assert world.repos.follower.get_followed_podcast_ids(user.id) == [pid]
    [entry] = world.repos.audit_log.list(target_user_id=user.id)
    assert entry.action == "user.suspend" and entry.actor == "web" and entry.actor_user_id == admin.id
    assert entry.details["reason"] == "looking into it"


def test_ban_moves_follows_aside_and_cancels_orphaned_work(world, admin):
    banned, other = world.add_user("banned@example.com"), world.add_user("fine@example.com")
    solo, solo_eps = world.add_podcast("solo", episodes=2)
    shared, shared_eps = world.add_podcast("shared", episodes=1)
    world.follow(banned.id, solo)
    world.follow(banned.id, shared)
    world.follow(other.id, shared)
    queue = world.repos.queue_manager
    solo_tasks = [queue.add_task(eid, TaskStage.DOWNLOAD) for eid in solo_eps]
    shared_task = queue.add_task(shared_eps[0], TaskStage.DOWNLOAD)

    result = world.abuse.account_admin.ban(banned.id, reason="script", actor=Actor.cli())

    assert world.repos.user.get_by_id(banned.id).status == UserStatus.BANNED
    assert result.follows_moved == 2
    assert result.feeds_orphaned == [solo]
    assert result.tasks_cancelled == 2
    assert world.repos.follower.count_by_podcast(solo) == 0
    assert world.repos.follower.count_by_podcast(shared) == 1  # the other user is unaffected
    assert {queue.get_task(t.id).status for t in solo_tasks} == {TaskStatus.SUPERSEDED}
    assert queue.get_task(shared_task.id).status == TaskStatus.PENDING
    assert _actions(world, banned.id) == ["user.ban"]


def test_reinstate_after_ban_restores_follows_and_bypasses_the_gate(world, admin):
    user = world.add_user("banned@example.com")
    pid, _ = world.add_podcast("solo")
    world.follow(user.id, pid, at=NOW - timedelta(days=9))
    world.abuse.account_admin.ban(user.id, reason="x", actor=Actor.cli())
    # New feeds paused: the unban still reactivates the dormant feed.
    world.abuse.settings.set("new_feeds_enabled", False, actor=Actor.cli())

    result = world.abuse.account_admin.reinstate(user.id, actor=Actor.cli())

    assert world.repos.user.get_by_id(user.id).is_active
    assert result.follows_restored == 1 and result.feeds_reactivated == [pid]
    [follow] = world.repos.follower.get_by_user(user.id)
    assert follow.created_at == NOW - timedelta(days=9)
    entry = world.repos.audit_log.list(action="user.reinstate")[0]
    assert entry.details["reactivated_feeds"] == 1 and entry.details["old"] == "banned"


def test_ban_then_suspend_then_reinstate_keeps_the_follows(world, admin):
    user = world.add_user("u@example.com")
    pid, _ = world.add_podcast("show")
    world.follow(user.id, pid)
    svc = world.abuse.account_admin
    svc.ban(user.id, reason="x", actor=Actor.cli())
    svc.suspend(user.id, reason="downgraded", actor=Actor.cli())

    result = svc.reinstate(user.id, actor=Actor.cli())

    assert result.follows_restored == 1
    assert world.repos.follower.get_followed_podcast_ids(user.id) == [pid]
    assert world.repos.suspended_follow.list_podcast_ids(user.id) == []


def test_rerunning_a_ban_that_crashed_after_the_move_still_cancels(world, admin):
    user = world.add_user("u@example.com")
    pid, eps = world.add_podcast("solo", episodes=1)
    world.follow(user.id, pid)
    task = world.repos.queue_manager.add_task(eps[0], TaskStage.DOWNLOAD)
    # The first run got as far as moving the follows aside, then died.
    world.repos.suspended_follow.move_follows_aside(user.id, now=NOW)

    result = world.abuse.account_admin.ban(user.id, reason="x", actor=Actor.cli())

    assert result.follows_moved == 0
    assert result.feeds_orphaned == [pid] and result.tasks_cancelled == 1
    assert world.repos.queue_manager.get_task(task.id).status == TaskStatus.SUPERSEDED


def test_suspend_and_ban_require_a_reason(world, admin):
    user = world.add_user("u@example.com")
    with pytest.raises(AdminActionError) as exc:
        world.abuse.account_admin.ban(user.id, reason="  ", actor=Actor.cli())
    assert exc.value.code == "reason_required"
    assert world.repos.user.get_by_id(user.id).is_active


def test_admin_cannot_disable_or_demote_themselves(world, admin):
    world.add_user("other-admin@example.com", is_admin=True)
    svc = world.abuse.account_admin
    for action in (
        lambda: svc.suspend(admin.id, reason="x", actor=Actor.web(admin.id)),
        lambda: svc.ban(admin.id, reason="x", actor=Actor.web(admin.id)),
        lambda: svc.revoke_admin(admin.id, actor=Actor.web(admin.id)),
    ):
        with pytest.raises(AdminActionError) as exc:
            action()
        assert exc.value.code == "cannot_modify_self"


def test_last_active_admin_is_protected(world, admin):
    svc = world.abuse.account_admin
    with pytest.raises(AdminActionError) as exc:
        svc.revoke_admin(admin.id, actor=Actor.cli())
    assert exc.value.code == "last_admin"
    with pytest.raises(AdminActionError):
        svc.suspend(admin.id, reason="x", actor=Actor.cli())

    second = world.add_user("second@example.com")
    svc.grant_admin(second.id, actor=Actor.cli())
    svc.revoke_admin(admin.id, actor=Actor.cli())
    assert not world.repos.user.get_by_id(admin.id).is_admin
    assert _actions(world, second.id) == ["admin.grant"]
    assert _actions(world, admin.id) == ["admin.revoke"]


def test_logout_everywhere_moves_tokens_valid_after(world, admin):
    user = world.add_user("u@example.com")
    world.abuse.account_admin.logout_everywhere(user.id, actor=Actor.cli())
    assert world.repos.user.get_by_id(user.id).tokens_valid_after is not None
    assert world.repos.user.get_by_id(user.id).is_active
    assert _actions(world, user.id) == ["user.logout_everywhere"]


def test_remove_introduced_feeds_spares_feeds_others_follow(world, admin):
    user, other = world.add_user("u@example.com"), world.add_user("o@example.com")
    mine, mine_eps = world.add_podcast("mine", episodes=1)
    shared, _ = world.add_podcast("shared")
    theirs, _ = world.add_podcast("theirs")
    world.follow(user.id, mine, at=NOW - timedelta(days=3))
    world.follow(user.id, shared, at=NOW - timedelta(days=3))
    world.follow(other.id, shared, at=NOW)
    world.follow(other.id, theirs, at=NOW - timedelta(days=5))
    world.follow(user.id, theirs, at=NOW)
    task = world.repos.queue_manager.add_task(mine_eps[0], TaskStage.DOWNLOAD)

    result = world.abuse.account_admin.remove_introduced_feeds(user.id, actor=Actor.cli())

    assert result.feeds_removed == [mine]
    assert result.tasks_cancelled == 1
    assert sorted(world.repos.follower.get_followed_podcast_ids(user.id)) == sorted([shared, theirs])
    assert world.repos.queue_manager.get_task(task.id).status == TaskStatus.SUPERSEDED
    # The podcast row itself stays.
    assert world.repos.podcast.get_by_id(mine) is not None


def test_ban_with_remove_introduced_feeds_ignores_suspended_co_followers(world, admin):
    user, suspended = world.add_user("u@example.com"), world.add_user("s@example.com")
    pid, _ = world.add_podcast("mine")
    world.follow(user.id, pid, at=NOW - timedelta(days=1))
    world.follow(suspended.id, pid, at=NOW)
    world.abuse.account_admin.suspend(suspended.id, reason="x", actor=Actor.cli())

    result = world.abuse.account_admin.ban(user.id, reason="x", actor=Actor.cli(), remove_introduced_feeds=True)

    assert result.feeds_removed == [pid]
    assert world.repos.suspended_follow.list_podcast_ids(user.id) == []


def test_user_admin_needs_multi_user_mode(tmp_path):
    world = make_world(tmp_path, multi_user=False)
    user = world.add_user("local@example.com")
    svc = world.abuse.account_admin
    for action in (
        lambda: svc.suspend(user.id, reason="x", actor=Actor.cli()),
        lambda: svc.list_users(),
        lambda: svc.count_users(),
        lambda: svc.audit_page(),
        lambda: svc.detail(user.id),
    ):
        with pytest.raises(AdminActionError) as exc:
            action()
        assert exc.value.code == "single_user_mode"


def test_resolve_user_by_email_case_insensitively_or_id(world):
    user = world.add_user("mixed@example.com")
    svc = world.abuse.account_admin
    assert svc.resolve_user("MIXED@example.com").id == user.id
    assert svc.resolve_user(user.id).id == user.id
    with pytest.raises(AdminActionError):
        svc.resolve_user("nobody@example.com")


@pytest.mark.parametrize("edit", ["one", "all"])
def test_override_edits_keep_another_workers_change(world, admin, edit):
    """The service's settings cache is warm with an old map; another worker
    then sets a different user's limit. An edit here must build on the
    stored map, not the cached one, so that change survives."""
    actor = Actor.web(admin.id)
    a, b = world.add_user("a@example.com"), world.add_user("b@example.com")
    service = world.abuse.account_admin
    assert world.abuse.settings.get("quota_overrides") == {}  # warm the cache
    world.repos.settings.upsert(
        "quota_overrides", {a.id: {"imports_per_day": 7}}, updated_by=None, updated_at=NOW
    )  # another worker's write

    if edit == "one":
        service.set_quota_override(b.id, "imports_per_day", 9, actor=actor)
    else:
        service.replace_quota_overrides(b.id, {"imports_per_day": 9}, actor=actor)

    stored = world.repos.settings.get_all()["quota_overrides"].value
    assert stored == {a.id: {"imports_per_day": 7}, b.id: {"imports_per_day": 9}}


def _connect_app(world, user_id, request="p1"):
    """Spec #109 — give the user an active OAuth grant (a connected app)."""
    import uuid

    from thestill.models.oauth import OAuthPending

    world.repos.oauth.save_pending(
        OAuthPending(
            id_hash=request,
            client_id="https://claude.ai/oauth/mcp-oauth-client-metadata",
            redirect_uri="https://claude.ai/api/mcp/auth_callback",
            redirect_uri_explicit=True,
            scopes=("read",),
            code_challenge="c" * 43,
            created_at=NOW,
            expires_at=NOW + timedelta(minutes=10),
        )
    )
    grant, _ = world.repos.oauth.approve(
        request,
        now=NOW,
        user_id=user_id,
        new_grant_id=str(uuid.uuid4()),
        client_name="Claude",
        redirect_host="claude.ai",
        scopes=("read",),
        code_hash=f"code-{request}",
        code_expires_at=NOW + timedelta(seconds=60),
    )
    return grant


@pytest.mark.parametrize("action", ["suspend", "ban", "logout_everywhere"])
def test_account_actions_revoke_connected_apps(world, admin, action):
    user = world.add_user("reader@example.com")
    _connect_app(world, user.id)
    assert world.abuse.account_admin.footprint(user.id).connected_apps == ["Claude (claude.ai)"]

    account_admin = world.abuse.account_admin
    if action == "logout_everywhere":
        account_admin.logout_everywhere(user.id, actor=Actor.web(admin.id))
    else:
        getattr(account_admin, action)(user.id, reason="spam", actor=Actor.web(admin.id))

    assert world.repos.oauth.list_active_grants(user.id) == []
    entry = world.repos.audit_log.list(target_user_id=user.id)[0]
    assert entry.details["oauth_grants_revoked"] == 1
