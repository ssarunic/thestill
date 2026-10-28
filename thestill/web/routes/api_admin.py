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

"""Spec #104 §4.5 — the admin console API (``/api/admin``).

Mounted with ``require_admin``. Every write goes through the same services
as the CLI (``AccountAdminService``, ``SettingsService``), which enforce the
rules (no acting on yourself, never demote the last admin) and write the
audit log; refusals map to 409 through the shared handler. The user and
audit endpoints need multi-user mode; settings and alerts work in both.
"""

from dataclasses import asdict
from datetime import datetime
from typing import Any, Dict, List, Literal, Optional

from fastapi import APIRouter, Depends, HTTPException, Query
from pydantic import BaseModel, Field

from ...models.user import User, UserStatus
from ...repositories.audit_log_repository import AuditEntry
from ...repositories.user_directory_repository import UserSummary
from ...services.abuse.account_admin_service import UserDetail
from ...services.abuse.actor import Actor
from ...services.abuse.audit_service import AUDIT_ACTIONS
from ...services.abuse.settings_service import EffectiveSetting
from ..dependencies import AppState, get_app_state, require_admin
from ..responses import api_response, paginated_response

router = APIRouter()


def _abuse(state: AppState):
    if state.abuse is None:
        raise HTTPException(status_code=503, detail={"error": "unavailable", "message": "Admin services are off."})
    return state.abuse


def _iso(value: Optional[datetime]) -> Optional[str]:
    return value.isoformat() if value else None


def _user_json(user: User) -> Dict[str, Any]:
    """The operator's view of an account: includes the status reason."""
    return {
        "id": user.id,
        "email": user.email,
        "name": user.name,
        "picture": user.picture,
        "created_at": _iso(user.created_at),
        "last_login_at": _iso(user.last_login_at),
        "signup_source": user.signup_source,
        "is_admin": user.is_admin,
        "status": user.status.value,
        "status_reason": user.status_reason,
        "status_changed_at": _iso(user.status_changed_at),
        "status_changed_by": user.status_changed_by,
    }


def _summary_json(summary: UserSummary) -> Dict[str, Any]:
    usage = summary.usage_recent
    return {
        **_user_json(summary.user),
        "follows": summary.follows,
        "suspended_follows": summary.suspended_follows,
        "feeds_brought_in_24h": usage.get("feed_introduced", 0),
        "feeds_brought_in_total": summary.feeds_brought_in_total,
        "follows_24h": usage.get("follow", 0),
        "imports_24h": usage.get("import", 0),
        "translations_24h": usage.get("translation", 0),
        "quota_hits_24h": usage.get("quota_exceeded", 0),
    }


def _detail_json(detail: UserDetail) -> Dict[str, Any]:
    return {
        "user": _user_json(detail.user),
        "usage_24h": detail.usage_24h,
        "usage_7d": detail.usage_7d,
        "daily_7d": detail.daily_7d,
        "follows": [asdict(follow) for follow in detail.follows],
        "quota_limits": detail.quota_limits,
        "quota_overrides": detail.quota_overrides,
        "history": [_audit_json(entry) for entry in detail.history],
    }


def _audit_json(entry: AuditEntry) -> Dict[str, Any]:
    return {
        "id": entry.id,
        "created_at": _iso(entry.created_at),
        "actor": entry.actor,
        "actor_user_id": entry.actor_user_id,
        "action": entry.action,
        "target_user_id": entry.target_user_id,
        "details": entry.details,
    }


def _setting_json(setting: EffectiveSetting) -> Dict[str, Any]:
    return {
        "key": setting.key,
        "value": setting.value,
        "source": setting.source,
        "default": setting.default,
        "is_default": setting.is_default,
        "description": setting.description,
        "updated_at": _iso(setting.updated_at),
        "updated_by": setting.updated_by,
    }


# ----- users -----


@router.get("/users")
def list_users(
    q: Optional[str] = Query(None, max_length=200),
    status: Optional[UserStatus] = None,
    sort: str = Query("attention", pattern="^(attention|created|last-login|follows|email)$"),
    limit: int = Query(50, ge=1, le=200),
    offset: int = Query(0, ge=0),
    state: AppState = Depends(get_app_state),
) -> dict:
    """Every account with its activity; ``attention`` (quota hits, then
    feeds brought in, in the last 24 hours) puts the ones worth a look first."""
    admin = _abuse(state).account_admin
    rows = admin.list_users(status=status, q=q or None, sort=sort, limit=limit, offset=offset)
    total = admin.count_users(status=status, q=q or None)
    return api_response(
        paginated_response([_summary_json(r) for r in rows], total=total, offset=offset, limit=limit, items_key="users")
    )


@router.get("/users/{user_id}")
def get_user(user_id: str, state: AppState = Depends(get_app_state)) -> dict:
    return api_response(_detail_json(_abuse(state).account_admin.detail(user_id)))


class StatusChangeRequest(BaseModel):
    status: UserStatus
    reason: Optional[str] = Field(None, max_length=2000)
    remove_introduced_feeds: bool = False


@router.post("/users/{user_id}/status")
def change_status(
    user_id: str,
    body: StatusChangeRequest,
    state: AppState = Depends(get_app_state),
    admin: User = Depends(require_admin),
) -> dict:
    user = _abuse(state).account_admin.set_status(
        user_id,
        body.status,
        reason=body.reason,
        actor=Actor.web(admin.id),
        remove_introduced_feeds=body.remove_introduced_feeds,
    )
    return api_response({"user": _user_json(user)})


@router.post("/users/{user_id}/logout-everywhere")
def logout_everywhere(
    user_id: str, state: AppState = Depends(get_app_state), admin: User = Depends(require_admin)
) -> dict:
    user = _abuse(state).account_admin.logout_everywhere(user_id, actor=Actor.web(admin.id))
    return api_response({"user": _user_json(user)})


@router.post("/users/{user_id}/remove-introduced-feeds")
def remove_introduced_feeds(
    user_id: str, state: AppState = Depends(get_app_state), admin: User = Depends(require_admin)
) -> dict:
    result = _abuse(state).account_admin.remove_introduced_feeds(user_id, actor=Actor.web(admin.id))
    return api_response({"feeds_removed": result.feeds_removed, "tasks_cancelled": result.tasks_cancelled})


@router.put("/users/{user_id}/quotas")
def set_quotas(
    user_id: str,
    body: Dict[str, int],
    state: AppState = Depends(get_app_state),
    admin: User = Depends(require_admin),
) -> dict:
    """Replace this user's quota overrides (``{quota: limit}``, 0 =
    unlimited); an empty body clears them."""
    overrides = _abuse(state).account_admin.replace_quota_overrides(user_id, body, actor=Actor.web(admin.id))
    return api_response({"quota_overrides": overrides})


class AdminRoleRequest(BaseModel):
    is_admin: bool


@router.post("/users/{user_id}/admin")
def set_admin_role(
    user_id: str,
    body: AdminRoleRequest,
    state: AppState = Depends(get_app_state),
    admin: User = Depends(require_admin),
) -> dict:
    user = _abuse(state).account_admin.set_admin(user_id, body.is_admin, actor=Actor.web(admin.id))
    return api_response({"user": _user_json(user)})


# ----- settings and alerts -----


@router.get("/settings")
def get_settings(state: AppState = Depends(get_app_state)) -> dict:
    """Each runtime setting's effective value and where it comes from."""
    abuse = _abuse(state)
    return api_response(
        {
            "settings": [_setting_json(s) for s in abuse.settings.all_effective()],
            "non_default": abuse.settings.non_default(),
            "alert_channels": abuse.alerts.channel_names,
        }
    )


class SettingValue(BaseModel):
    value: Any


@router.put("/settings/{key}")
def put_setting(
    key: str,
    body: SettingValue,
    state: AppState = Depends(get_app_state),
    admin: User = Depends(require_admin),
) -> dict:
    settings = _abuse(state).settings
    try:
        result = settings.set(key, body.value, actor=Actor.web(admin.id))
    except KeyError as exc:
        raise HTTPException(status_code=404, detail={"error": "unknown_setting", "message": exc.args[0]}) from exc
    except ValueError as exc:
        raise HTTPException(status_code=422, detail={"error": "invalid_setting", "message": str(exc)}) from exc
    return api_response({"setting": _setting_json(result)})


@router.delete("/settings/{key}")
def reset_setting(key: str, state: AppState = Depends(get_app_state), admin: User = Depends(require_admin)) -> dict:
    """Revert ``key`` to its env default."""
    try:
        result = _abuse(state).settings.reset(key, actor=Actor.web(admin.id))
    except KeyError as exc:
        raise HTTPException(status_code=404, detail={"error": "unknown_setting", "message": exc.args[0]}) from exc
    return api_response({"setting": _setting_json(result)})


@router.post("/alerts/test")
def send_test_alert(state: AppState = Depends(get_app_state)) -> dict:
    """Send a test alert to every configured channel."""
    return api_response({"results": _abuse(state).alerts.send_test()})


# ----- audit log -----


@router.get("/audit")
def list_audit(
    action: Optional[str] = Query(None),
    target_user_id: Optional[str] = Query(None),
    limit: int = Query(50, ge=1, le=200),
    offset: int = Query(0, ge=0),
    state: AppState = Depends(get_app_state),
) -> dict:
    """The admin audit log, newest first, and the actions it can filter by."""
    if action is not None and action not in AUDIT_ACTIONS:
        raise HTTPException(status_code=422, detail={"error": "unknown_action", "message": f"Unknown action {action}"})
    entries, total, emails = _abuse(state).account_admin.audit_page(
        action=action, target_user_id=target_user_id, limit=limit, offset=offset
    )
    items: List[Dict[str, Any]] = [
        {**_audit_json(e), "actor_email": emails.get(e.actor_user_id), "target_email": emails.get(e.target_user_id)}
        for e in entries
    ]
    return api_response(
        {
            **paginated_response(items, total=total, offset=offset, limit=limit, items_key="entries"),
            "actions": list(AUDIT_ACTIONS),
        }
    )


# ----- entity curation -----
# The same service as ``thestill entity merge / move-mentions / split /
# relink / prune-orphans`` and ``resolution-blacklist add``; refusals are
# AdminActionError and map to 409 through the shared handler.

EntityTypeName = Literal["person", "company", "product", "topic"]


def _curation(state: AppState):
    from ...services.entity_curation import EntityCurationService

    return EntityCurationService(
        state.entity_repository, _abuse(state).audit, link_decisions=state.link_decision_repository
    )


def _entity_json(entity) -> Dict[str, Any]:
    return {
        "id": entity.id,
        "type": entity.type.value,
        "name": entity.canonical_name,
        "wikidata_qid": entity.wikidata_qid,
        "description": entity.description,
    }


class EntityMergeRequest(BaseModel):
    keeper_id: str = Field(..., min_length=3, max_length=300)
    loser_id: str = Field(..., min_length=3, max_length=300)
    reason: Optional[str] = Field(None, max_length=500)
    dry_run: bool = False


class EntityMoveMentionsRequest(BaseModel):
    from_entity_id: str = Field(..., min_length=3, max_length=300)
    to_entity_id: str = Field(..., min_length=3, max_length=300)
    mention_ids: List[int] = Field(..., min_length=1, max_length=5000)
    delete_emptied: bool = False
    reason: Optional[str] = Field(None, max_length=500)


class EntitySplitRequest(BaseModel):
    from_entity_id: str = Field(..., min_length=3, max_length=300)
    name: str = Field(..., min_length=1, max_length=300)
    type: EntityTypeName
    qid: Optional[str] = Field(None, max_length=20)
    description: Optional[str] = Field(None, max_length=500)
    entity_id: Optional[str] = Field(None, min_length=3, max_length=300)
    #: ``None`` moves everything that refers to the source.
    mention_ids: Optional[List[int]] = Field(None, min_length=1, max_length=5000)
    delete_emptied: bool = True
    reason: Optional[str] = Field(None, max_length=500)


class EntityRelinkRequest(BaseModel):
    #: ``None`` unlinks the entity from Wikidata.
    qid: Optional[str] = Field(None, max_length=20)
    description: Optional[str] = Field(None, max_length=500)
    blacklist_old: bool = True
    reason: Optional[str] = Field(None, max_length=500)


class EntitySetTypeRequest(BaseModel):
    type: EntityTypeName
    reason: Optional[str] = Field(None, max_length=500)


class BlacklistRequest(BaseModel):
    surface_form: str = Field(..., min_length=1, max_length=300)
    wrong_qid: str = Field(..., min_length=2, max_length=20)
    reason: Optional[str] = Field(None, max_length=500)


class PruneOrphansRequest(BaseModel):
    apply: bool = False


@router.post("/entities/merge")
def merge_entities(
    body: EntityMergeRequest, state: AppState = Depends(get_app_state), admin: User = Depends(require_admin)
) -> dict:
    result = _curation(state).merge(
        body.keeper_id, body.loser_id, actor=Actor.web(admin.id), reason=body.reason, dry_run=body.dry_run
    )
    return api_response({**asdict(result), "dry_run": body.dry_run})


@router.post("/entities/move-mentions")
def move_entity_mentions(
    body: EntityMoveMentionsRequest, state: AppState = Depends(get_app_state), admin: User = Depends(require_admin)
) -> dict:
    result = _curation(state).move_mentions(
        body.mention_ids,
        from_entity_id=body.from_entity_id,
        to_entity_id=body.to_entity_id,
        actor=Actor.web(admin.id),
        delete_emptied=body.delete_emptied,
        reason=body.reason,
    )
    return api_response(asdict(result))


@router.post("/entities/split")
def split_entity(
    body: EntitySplitRequest, state: AppState = Depends(get_app_state), admin: User = Depends(require_admin)
) -> dict:
    from ...models.entities import EntityType

    result = _curation(state).split(
        name=body.name,
        entity_type=EntityType(body.type),
        from_entity_id=body.from_entity_id,
        actor=Actor.web(admin.id),
        qid=body.qid,
        description=body.description,
        entity_id=body.entity_id,
        mention_ids=body.mention_ids,
        delete_emptied=body.delete_emptied,
        reason=body.reason,
    )
    return api_response({"entity": _entity_json(result.entity), "moved": result.moved, "deleted": result.deleted})


@router.post("/entities/{entity_id}/relink")
def relink_entity(
    entity_id: str,
    body: EntityRelinkRequest,
    state: AppState = Depends(get_app_state),
    admin: User = Depends(require_admin),
) -> dict:
    result = _curation(state).relink(
        entity_id,
        body.qid,
        actor=Actor.web(admin.id),
        description=body.description,
        blacklist_old=body.blacklist_old,
        reason=body.reason,
    )
    return api_response(asdict(result))


@router.post("/entities/{entity_id}/set-type")
def set_entity_type(
    entity_id: str,
    body: EntitySetTypeRequest,
    state: AppState = Depends(get_app_state),
    admin: User = Depends(require_admin),
) -> dict:
    from ...models.entities import EntityType

    result = _curation(state).set_type(entity_id, EntityType(body.type), actor=Actor.web(admin.id), reason=body.reason)
    return api_response(asdict(result))


@router.post("/entities/blacklist")
def blacklist_entity_link(
    body: BlacklistRequest, state: AppState = Depends(get_app_state), admin: User = Depends(require_admin)
) -> dict:
    _curation(state).blacklist(body.surface_form, body.wrong_qid, actor=Actor.web(admin.id), reason=body.reason)
    return api_response({"surface_form": body.surface_form, "wrong_qid": body.wrong_qid})


@router.post("/entities/prune-orphans")
def prune_orphan_entities(
    body: PruneOrphansRequest, state: AppState = Depends(get_app_state), admin: User = Depends(require_admin)
) -> dict:
    result = _curation(state).prune_orphans(actor=Actor.web(admin.id), apply=body.apply)
    return api_response(
        {
            "orphans": len(result.orphans),
            "sample": result.orphans[:50],
            "deleted": result.deleted,
            "applied": result.applied,
        }
    )
