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

"""
Per-user briefing API endpoints (spec #36).

``GET /latest`` lazy-generates: if no briefing exists or the throttle has
elapsed and new inbox items are eligible, a fresh briefing is created and
returned. The throttle in ``BriefingService`` keeps generation cost
bounded; a future spec moves this to an explicit operator trigger.
"""

from datetime import datetime, timezone
from pathlib import Path
from typing import List, Optional, Union

from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel, Field, ValidationError, field_validator
from structlog import get_logger

from ...models.briefing import Briefing
from ...models.briefing_schedule import BriefingFrequency, BriefingSchedule
from ...models.user import User
from ...services.briefing_service import BriefingNotFoundError
from ...services.narration import NarrationRunnerError, read_narration_header
from ...utils.briefing_cadence import next_run_for
from ...utils.duration import resolve_target_or_default, slug_for_duration_seconds
from ...utils.path_manager import _validate_slug
from ..dependencies import AppState, get_app_state, require_auth
from ..responses import api_response, bad_request, not_found, paginated_response

logger = get_logger(__name__)

router = APIRouter()


def _serialize(briefing: Briefing) -> dict:
    return briefing.model_dump(mode="json")


class NarrateBriefingRequest(BaseModel):
    """Body for ``POST /api/briefings/{briefing_id}/narrate``."""

    target_duration: Optional[Union[int, str]] = Field(
        default=None,
        description=(
            "Target spoken duration. Int = seconds; string = preset"
            " (short/medium/long) or unit-suffixed (5m, 120s, 0:05:00)."
        ),
    )
    slug: Optional[str] = Field(
        default=None,
        min_length=1,
        max_length=64,
        description=(
            "Output slug (filenames are keyed on ``<briefing_id>-<slug>``)."
            " Defaults to ``short``/``medium``/``long`` for the matching"
            " preset duration, otherwise ``custom-<seconds>s``."
        ),
    )

    @field_validator("slug")
    @classmethod
    def _check_slug(cls, value: Optional[str]) -> Optional[str]:
        if value is None:
            return value
        try:
            return _validate_slug(value, name="slug")
        except ValueError as exc:
            raise ValueError(str(exc)) from exc


def _list_narrations_for_briefing(narrations_dir: Path, briefing_id: str) -> List[dict]:
    """Filesystem-driven list of narration variants for ``briefing_id``.

    Each variant is keyed by ``<briefing_id>-<slug>.json`` (the runner's
    canonical filename), so a directory glob is sufficient — no schema
    column needed. ``slug`` is recovered by stripping the
    ``<briefing_id>-`` prefix off the filename stem.
    """
    if not narrations_dir.exists():
        return []
    out: List[dict] = []
    prefix = f"{briefing_id}-"
    for json_path in sorted(narrations_dir.glob(f"{briefing_id}-*.json")):
        stem = json_path.stem
        if not stem.startswith(prefix):
            continue
        slug = stem[len(prefix) :]
        if not slug:
            continue
        payload = read_narration_header(json_path)
        if payload is None:
            continue
        md_path = json_path.with_suffix(".md")
        out.append(
            {
                "narration_id": stem,
                "slug": slug,
                "target_duration_seconds": payload.get("target_duration_seconds"),
                "actual_duration_seconds": payload.get("actual_duration_seconds"),
                "mode": payload.get("mode"),
                "fallback_reason": payload.get("fallback_reason"),
                "generated_at": payload.get("generated_at"),
                "schema_version": payload.get("schema_version"),
                "script_path": str(json_path),
                "markdown_path": str(md_path) if md_path.exists() else None,
            }
        )
    out.sort(key=lambda r: r.get("generated_at") or "")
    return out


class ScheduleUpdateRequest(BaseModel):
    """PUT /schedule body (spec #50). Field-level constraints here; the
    cross-field rules (weekday iff weekly, valid IANA zone) are enforced by
    the ``BriefingSchedule`` model on construction."""

    frequency: BriefingFrequency = BriefingFrequency.DAILY
    hour_local: int = Field(default=8, ge=0, le=23)
    weekday: Optional[int] = Field(default=None, ge=0, le=6)
    timezone: str
    enabled: bool = True
    # Spec #51 — email the briefing when the scheduled slot fires. Rejected
    # with 422 when no EMAIL_PROVIDER is configured.
    email_enabled: bool = False


def _require_owned(briefing: Briefing, user: User) -> None:
    """A user may only read or mutate their own briefings."""
    if briefing.user_id != user.id:
        # 404 (not 403) so an attacker can't enumerate other users' IDs
        # by probing for which briefing IDs exist.
        raise HTTPException(status_code=404, detail=f"Briefing not found: {briefing.id}")


@router.get("")
async def list_briefings(
    limit: int = 20,
    offset: int = 0,
    app_state: AppState = Depends(get_app_state),
    user: User = Depends(require_auth),
):
    """Paginated briefing history for the current user, newest first."""
    limit = max(1, min(limit, 100))
    offset = max(0, offset)
    briefings = app_state.briefing_repository.list_for_user(user.id, limit=limit, offset=offset)
    total = app_state.briefing_repository.count_for_user(user.id)
    return paginated_response(
        [_serialize(b) for b in briefings],
        total=total,
        offset=offset,
        limit=limit,
        items_key="briefings",
    )


@router.get("/latest")
async def get_latest_briefing(
    app_state: AppState = Depends(get_app_state),
    user: User = Depends(require_auth),
):
    """Return the user's most recent briefing, generating one if eligible.

    Returns 404 when the inbox has no eligible items in the open window —
    callers should hide the briefing card.
    """
    briefing = app_state.briefing_service.generate_for_user(user.id)
    if briefing is None:
        not_found("Briefing", "latest")
    return api_response(_serialize(briefing))


def _serialize_schedule(schedule: BriefingSchedule) -> dict:
    return {
        "frequency": schedule.frequency.value,
        "hour_local": schedule.hour_local,
        "weekday": schedule.weekday,
        "timezone": schedule.timezone_name,
        "enabled": schedule.enabled,
        "email_enabled": schedule.email_enabled,
        "next_run_at": schedule.next_run_at.isoformat() if schedule.next_run_at else None,
        "updated_at": schedule.updated_at.isoformat(),
    }


# NOTE: /schedule routes are declared before /{briefing_id} so the literal
# path wins over the parameterized one.
@router.get("/schedule")
async def get_briefing_schedule(
    app_state: AppState = Depends(get_app_state),
    user: User = Depends(require_auth),
):
    """Return the current user's briefing schedule. 404 if never configured."""
    schedule = app_state.briefing_schedule_repository.get(user.id)
    if schedule is None:
        not_found("Briefing schedule", user.id)
    return api_response(_serialize_schedule(schedule))


@router.put("/schedule")
async def put_briefing_schedule(
    body: ScheduleUpdateRequest,
    app_state: AppState = Depends(get_app_state),
    user: User = Depends(require_auth),
):
    """Upsert the current user's briefing schedule (spec #50).

    Recomputes ``next_run_at`` (next occurrence strictly after now, in the
    user's timezone) and echoes it back so the UI can show "Next briefing:
    …". Disabling parks the schedule (``next_run_at = NULL``).
    """
    now = datetime.now(timezone.utc)
    if body.email_enabled and app_state.briefing_delivery_service is None:
        # Spec #51: no EMAIL_PROVIDER configured — the checkbox is hidden
        # in the UI, so this only fires for hand-built requests.
        raise HTTPException(
            status_code=422,
            detail="Email delivery is not configured on this server (EMAIL_PROVIDER=none)",
        )
    existing = app_state.briefing_schedule_repository.get(user.id)
    try:
        schedule = BriefingSchedule(
            user_id=user.id,
            frequency=body.frequency,
            hour_local=body.hour_local,
            weekday=body.weekday,
            timezone_name=body.timezone,
            enabled=body.enabled,
            email_enabled=body.email_enabled,
            created_at=existing.created_at if existing else now,
            updated_at=now,
        )
    except ValidationError as exc:
        # Cross-field rules (weekday iff weekly, IANA zone lookup) surface
        # as 422 like FastAPI's own body validation, not a 500.
        raise HTTPException(status_code=422, detail=str(exc)) from exc
    schedule.next_run_at = next_run_for(schedule, after=now) if schedule.enabled else None
    app_state.briefing_schedule_repository.upsert(schedule)
    logger.info(
        "briefing_schedule_updated",
        user_id=user.id,
        frequency=schedule.frequency.value,
        hour_local=schedule.hour_local,
        weekday=schedule.weekday,
        tz=schedule.timezone_name,
        enabled=schedule.enabled,
        email_enabled=schedule.email_enabled,
        next_run_at=schedule.next_run_at.isoformat() if schedule.next_run_at else None,
    )
    return api_response(_serialize_schedule(schedule))


@router.get("/{briefing_id}")
async def get_briefing(
    briefing_id: str,
    app_state: AppState = Depends(get_app_state),
    user: User = Depends(require_auth),
):
    """Fetch a specific briefing's metadata, plus narration variants on disk."""
    briefing = app_state.briefing_repository.get(briefing_id)
    if briefing is None:
        not_found("Briefing", briefing_id)
    _require_owned(briefing, user)
    narrations = _list_narrations_for_briefing(app_state.path_manager.narrations_dir(), briefing_id)
    return api_response({**_serialize(briefing), "narrations": narrations})


@router.post("/{briefing_id}/narrate", status_code=201)
async def narrate_briefing(
    briefing_id: str,
    body: NarrateBriefingRequest,
    app_state: AppState = Depends(get_app_state),
    user: User = Depends(require_auth),
):
    """Generate (or regenerate) a narration for ``briefing_id``.

    Used by the length-switcher UI: each click writes
    ``<briefing_id>-<slug>.{json,md}`` so previous variants are preserved.
    """
    briefing = app_state.briefing_repository.get(briefing_id)
    if briefing is None:
        not_found("Briefing", briefing_id)
    _require_owned(briefing, user)

    if app_state.narration_runner is None:
        raise HTTPException(
            status_code=503,
            detail=(
                "Narration is disabled — set NARRATION_ENABLED=true and"
                " configure an LLM provider to enable briefing narration."
            ),
        )

    try:
        target_seconds = resolve_target_or_default(
            body.target_duration, app_state.config.narration_default_duration_seconds
        )
    except ValueError as exc:
        bad_request(str(exc))

    slug = body.slug or slug_for_duration_seconds(target_seconds)

    try:
        run = app_state.narration_runner.run(
            briefing_id=briefing_id,
            target_duration_seconds=target_seconds,
            slug=slug,
        )
    except NarrationRunnerError as exc:
        logger.warning(
            "briefing.narrate_failed",
            briefing_id=briefing_id,
            error=str(exc),
        )
        not_found("Briefing", briefing_id)

    stats = run.content.stats
    return api_response(
        {
            "narration_id": run.narration_id,
            "briefing_id": run.briefing_id,
            "slug": slug,
            "mode": run.content.mode,
            "target_duration_seconds": stats.target_duration_seconds,
            "actual_duration_seconds": round(stats.actual_duration_seconds, 2),
            "quote_count": stats.quote_count,
            "fallback_reason": stats.fallback_reason,
            "script_path": str(run.json_path) if run.json_path else None,
            "markdown_path": str(run.markdown_path) if run.markdown_path else None,
        }
    )


@router.get("/{briefing_id}/script")
async def get_briefing_script(
    briefing_id: str,
    app_state: AppState = Depends(get_app_state),
    user: User = Depends(require_auth),
):
    """Return the rendered ``script.md`` body as text."""
    briefing = app_state.briefing_repository.get(briefing_id)
    if briefing is None:
        not_found("Briefing", briefing_id)
    _require_owned(briefing, user)
    if not briefing.script_path:
        not_found("Briefing script", briefing_id)
    path = Path(briefing.script_path)
    if not path.exists():
        # Row exists but the file is gone (manual cleanup, deploy mishap).
        # Surface as 404 so the UI can show a friendly empty state.
        logger.warning("briefing_script_missing", briefing_id=briefing_id, path=str(path))
        not_found("Briefing script", briefing_id)
    return api_response({"markdown": path.read_text(encoding="utf-8")})


@router.post("/{briefing_id}/listened")
async def mark_briefing_listened(
    briefing_id: str,
    app_state: AppState = Depends(get_app_state),
    user: User = Depends(require_auth),
):
    """Set ``listened_at`` on the briefing. Idempotent."""
    existing = app_state.briefing_repository.get(briefing_id)
    if existing is None:
        not_found("Briefing", briefing_id)
    _require_owned(existing, user)
    try:
        updated = app_state.briefing_service.mark_listened(briefing_id)
    except BriefingNotFoundError:
        # Race: deleted between ownership check and update.
        not_found("Briefing", briefing_id)
    return api_response(_serialize(updated))
