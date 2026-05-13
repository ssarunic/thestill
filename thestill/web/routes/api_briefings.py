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
Briefing API endpoints for Thestill web UI.

Provides endpoints for listing, viewing, creating, and reading briefing documents.
"""

from datetime import datetime, timezone
from pathlib import Path
from typing import List, Optional, Union

from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel, Field, field_validator
from structlog import get_logger

from ...models.briefing import Briefing, BriefingStatus
from ...models.user import User
from ...services.briefing_selector import BriefingEpisodeSelector, BriefingSelectionCriteria
from ...services.narration import NarrationRunnerError, read_narration_header
from ...utils.duration import resolve_target_or_default, slug_for_duration_seconds
from ...utils.path_manager import _validate_slug
from ..dependencies import AppState, get_app_state, require_auth
from ..responses import api_response, bad_request, not_found, paginated_response

logger = get_logger(__name__)

router = APIRouter()


# =============================================================================
# Request/Response Models
# =============================================================================


class BriefingResponse(BaseModel):
    """Response model for a single briefing."""

    id: str
    user_id: str
    created_at: str
    updated_at: str
    period_start: str
    period_end: str
    status: str
    file_path: Optional[str] = None
    episode_ids: List[str] = Field(default_factory=list)
    episodes_total: int = 0
    episodes_completed: int = 0
    episodes_failed: int = 0
    processing_time_seconds: Optional[float] = None
    error_message: Optional[str] = None
    success_rate: float = 0.0
    is_complete: bool = False


class CreateBriefingRequest(BaseModel):
    """Request body for creating a new briefing."""

    since_days: int = Field(default=7, ge=1, le=365)
    max_episodes: int = Field(default=10, ge=1, le=100)
    podcast_id: Optional[str] = None
    ready_only: bool = False
    exclude_briefed: bool = False


class CreateBriefingResponse(BaseModel):
    """Response for a successful briefing creation."""

    status: str
    message: str
    briefing_id: str
    episodes_selected: int


class BriefingPreviewRequest(BaseModel):
    """Request body for previewing briefing selection."""

    since_days: int = Field(default=7, ge=1, le=365)
    max_episodes: int = Field(default=10, ge=1, le=100)
    podcast_id: Optional[str] = None
    ready_only: bool = False
    exclude_briefed: bool = False


class BriefingPreviewEpisode(BaseModel):
    """Episode info in preview response."""

    episode_id: str
    episode_title: str
    episode_slug: str
    podcast_id: str
    podcast_title: str
    podcast_slug: str
    state: str
    pub_date: Optional[str] = None


class BriefingPreviewResponse(BaseModel):
    """Response for briefing preview."""

    status: str
    episodes: List[BriefingPreviewEpisode]
    total_matching: int
    criteria: dict


# =============================================================================
# Helper Functions
# =============================================================================


def _briefing_to_response(briefing: Briefing) -> BriefingResponse:
    """Convert Briefing model to response model."""
    return BriefingResponse(
        id=briefing.id,
        user_id=briefing.user_id,
        created_at=briefing.created_at.isoformat(),
        updated_at=briefing.updated_at.isoformat(),
        period_start=briefing.period_start.isoformat(),
        period_end=briefing.period_end.isoformat(),
        status=briefing.status.value,
        file_path=briefing.file_path,
        episode_ids=briefing.episode_ids,
        episodes_total=briefing.episodes_total,
        episodes_completed=briefing.episodes_completed,
        episodes_failed=briefing.episodes_failed,
        processing_time_seconds=briefing.processing_time_seconds,
        error_message=briefing.error_message,
        success_rate=briefing.success_rate,
        is_complete=briefing.is_complete,
    )


def _list_narrations_for_briefing(narrations_dir: Path, briefing_id: str) -> List[dict]:
    """Filesystem-driven list of narration variants for ``briefing_id``.

    Each variant is keyed by ``<briefing_id>-<slug>.json`` (the runner's
    canonical filename), so a directory glob is sufficient — no schema
    column needed for v1. ``slug`` is recovered by stripping the
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


# =============================================================================
# Endpoints
# =============================================================================


@router.get("")
async def list_briefings(
    limit: int = 50,
    offset: int = 0,
    status: Optional[str] = None,
    state: AppState = Depends(get_app_state),
    user: User = Depends(require_auth),
):
    """
    List briefings for the current user.

    Supports pagination and optional status filtering.
    """
    # Validate status if provided
    status_enum = None
    if status:
        try:
            status_enum = BriefingStatus(status)
        except ValueError:
            valid_statuses = [s.value for s in BriefingStatus]
            bad_request(f"Invalid status: {status}. Valid values: {valid_statuses}")

    briefings = state.briefing_repository.get_all(
        limit=limit,
        offset=offset,
        status=status_enum,
        user_id=user.id,
    )

    # Get total count for pagination
    total = state.briefing_repository.count(
        status=status_enum,
        user_id=user.id,
    )

    briefing_responses = [_briefing_to_response(d) for d in briefings]

    return paginated_response(
        items=[d.model_dump() for d in briefing_responses],
        total=total,
        offset=offset,
        limit=limit,
        items_key="briefings",
    )


@router.get("/latest")
async def get_latest_briefing(
    state: AppState = Depends(get_app_state),
    user: User = Depends(require_auth),
):
    """
    Return the user's most recent briefing, lazy-generating one when eligible.

    Selection comes from the inbox window
    ``[previous_briefing.period_end, now)`` — so each delivered episode is
    briefed exactly once. The throttle inside ``BriefingService`` collapses
    accidental rapid-fire triggers (cron racing the UI). Returns 404 when
    no eligible inbox items fall in the open window — callers should hide
    the "Today's briefing" card.
    """
    briefing = state.briefing_service.generate_for_user(user.id)
    if briefing is None:
        not_found("Briefing", "latest")

    narrations = _list_narrations_for_briefing(state.path_manager.narrations_dir(), briefing.id)
    return api_response(
        {
            "briefing": _briefing_to_response(briefing).model_dump(),
            "narrations": narrations,
        }
    )


@router.get("/morning-briefing")
async def get_morning_briefing(
    state: AppState = Depends(get_app_state),
    user: User = Depends(require_auth),
):
    """
    Get morning briefing preview using server-configured defaults.

    Returns episodes that would be included in a quick catch-up briefing,
    using BRIEFING_DEFAULT_SINCE_DAYS and BRIEFING_DEFAULT_MAX_EPISODES from config.
    """
    criteria = BriefingSelectionCriteria(
        since_days=state.config.briefing_default_since_days,
        max_episodes=state.config.briefing_default_max_episodes,
        ready_only=True,
        exclude_briefed=True,
    )

    selector = BriefingEpisodeSelector(
        episode_repository=state.repository,
        briefing_repository=state.briefing_repository,
    )

    result = selector.preview(criteria)

    episodes = []
    for podcast, episode in result.episodes:
        episodes.append(
            BriefingPreviewEpisode(
                episode_id=episode.id,
                episode_title=episode.title,
                episode_slug=episode.slug,
                podcast_id=podcast.id,
                podcast_title=podcast.title,
                podcast_slug=podcast.slug,
                state=episode.state.value,
                pub_date=episode.pub_date.isoformat() if episode.pub_date else None,
            )
        )

    return api_response(
        {
            "episodes": [e.model_dump() for e in episodes],
            "total_matching": result.total_matching,
            "criteria": {
                "since_days": criteria.since_days,
                "max_episodes": criteria.max_episodes,
                "ready_only": criteria.ready_only,
                "exclude_briefed": criteria.exclude_briefed,
            },
        }
    )


@router.post("/morning-briefing")
async def create_morning_briefing(
    state: AppState = Depends(get_app_state),
    user: User = Depends(require_auth),
):
    """
    Create a morning briefing briefing using server-configured defaults.

    Uses BRIEFING_DEFAULT_SINCE_DAYS and BRIEFING_DEFAULT_MAX_EPISODES from config.
    Only includes already-summarized episodes and excludes previously briefed ones.
    The render-write-save sequence lives in ``BriefingService`` so this and
    ``POST /api/briefings`` (ready_only) stay in lockstep.
    """
    criteria = BriefingSelectionCriteria(
        since_days=state.config.briefing_default_since_days,
        max_episodes=state.config.briefing_default_max_episodes,
        ready_only=True,
        exclude_briefed=True,
    )

    briefing = state.briefing_service.generate_from_criteria(user.id, criteria)
    if briefing is None:
        return api_response(
            {
                "status": "no_episodes",
                "message": "No episodes match the selection criteria",
                "briefing_id": None,
                "episodes_selected": 0,
            }
        )

    return api_response(
        {
            "status": "completed",
            "message": f"Briefing created with {briefing.episodes_total} episodes",
            "briefing_id": briefing.id,
            "episodes_selected": briefing.episodes_total,
        }
    )


@router.get("/{briefing_id}")
async def get_briefing(
    briefing_id: str,
    state: AppState = Depends(get_app_state),
    user: User = Depends(require_auth),
):
    """Get a single briefing by ID, plus any narration variants on disk."""
    briefing = state.briefing_repository.get_by_id(briefing_id)

    if not briefing:
        not_found("Briefing", briefing_id)

    if briefing.user_id != user.id:
        not_found("Briefing", briefing_id)

    narrations = _list_narrations_for_briefing(state.path_manager.narrations_dir(), briefing_id)
    return api_response(
        {
            "briefing": _briefing_to_response(briefing).model_dump(),
            "narrations": narrations,
        }
    )


@router.post("/{briefing_id}/narrate", status_code=201)
async def narrate_briefing(
    briefing_id: str,
    body: NarrateBriefingRequest,
    state: AppState = Depends(get_app_state),
    user: User = Depends(require_auth),
):
    """Generate (or regenerate) a narrated briefing for ``briefing_id``.

    Used by the length-switcher UI: each click writes
    ``<briefing_id>-<slug>.{json,md}`` so previous variants are preserved.
    """
    briefing = state.briefing_repository.get_by_id(briefing_id)
    if not briefing:
        not_found("Briefing", briefing_id)
    if briefing.user_id != user.id:
        not_found("Briefing", briefing_id)

    if state.narration_runner is None:
        raise HTTPException(
            status_code=503,
            detail=(
                "Narration is disabled — set NARRATION_ENABLED=true and"
                " configure an LLM provider to enable briefing narration."
            ),
        )

    try:
        target_seconds = resolve_target_or_default(
            body.target_duration, state.config.narration_default_duration_seconds
        )
    except ValueError as exc:
        bad_request(str(exc))

    slug = body.slug or slug_for_duration_seconds(target_seconds)

    try:
        run = state.narration_runner.run(
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


@router.get("/{briefing_id}/content")
async def get_briefing_content(
    briefing_id: str,
    state: AppState = Depends(get_app_state),
    user: User = Depends(require_auth),
):
    """
    Get the markdown content of a briefing.

    Returns the raw markdown content if the briefing file exists.
    """
    briefing = state.briefing_repository.get_by_id(briefing_id)

    if not briefing:
        not_found("Briefing", briefing_id)

    # Verify ownership
    if briefing.user_id != user.id:
        not_found("Briefing", briefing_id)

    if not briefing.file_path:
        return api_response(
            {
                "briefing_id": briefing_id,
                "content": None,
                "available": False,
            }
        )

    # Read briefing file
    briefing_file = state.path_manager.briefings_dir() / briefing.file_path
    if not briefing_file.exists():
        return api_response(
            {
                "briefing_id": briefing_id,
                "content": None,
                "available": False,
            }
        )

    try:
        content = briefing_file.read_text(encoding="utf-8")
        return api_response(
            {
                "briefing_id": briefing_id,
                "content": content,
                "available": True,
            }
        )
    except Exception as e:
        logger.error("Failed to read briefing file", briefing_id=briefing_id, error=str(e))
        return api_response(
            {
                "briefing_id": briefing_id,
                "content": None,
                "available": False,
                "error": str(e),
            }
        )


@router.get("/{briefing_id}/episodes")
async def get_briefing_episodes(
    briefing_id: str,
    state: AppState = Depends(get_app_state),
    user: User = Depends(require_auth),
):
    """
    Get detailed information about episodes in a briefing.

    Returns episode and podcast details for all episodes included in the briefing.
    """
    briefing = state.briefing_repository.get_by_id(briefing_id)

    if not briefing:
        not_found("Briefing", briefing_id)

    # Verify ownership
    if briefing.user_id != user.id:
        not_found("Briefing", briefing_id)

    episodes_info = []
    for episode_id in briefing.episode_ids:
        result = state.repository.get_episode(episode_id)
        if result:
            podcast, episode = result
            episodes_info.append(
                {
                    "episode_id": episode.id,
                    "episode_title": episode.title,
                    "episode_slug": episode.slug,
                    "podcast_id": podcast.id,
                    "podcast_title": podcast.title,
                    "podcast_slug": podcast.slug,
                    "state": episode.state.value,
                    "pub_date": episode.pub_date.isoformat() if episode.pub_date else None,
                    "duration": episode.duration,
                    "image_url": episode.image_url or podcast.image_url,
                }
            )

    return api_response(
        {
            "briefing_id": briefing_id,
            "episodes": episodes_info,
            "count": len(episodes_info),
        }
    )


@router.post("/preview")
async def preview_briefing(
    request: BriefingPreviewRequest,
    state: AppState = Depends(get_app_state),
    user: User = Depends(require_auth),
):
    """
    Preview which episodes would be included in a briefing.

    This is a dry-run operation that shows what would be selected
    without actually creating a briefing.
    """
    criteria = BriefingSelectionCriteria(
        since_days=request.since_days,
        max_episodes=request.max_episodes,
        podcast_id=request.podcast_id,
        ready_only=request.ready_only,
        exclude_briefed=request.exclude_briefed,
    )

    selector = BriefingEpisodeSelector(
        episode_repository=state.repository,
        briefing_repository=state.briefing_repository,
    )

    result = selector.preview(criteria)

    episodes = []
    for podcast, episode in result.episodes:
        episodes.append(
            BriefingPreviewEpisode(
                episode_id=episode.id,
                episode_title=episode.title,
                episode_slug=episode.slug,
                podcast_id=podcast.id,
                podcast_title=podcast.title,
                podcast_slug=podcast.slug,
                state=episode.state.value,
                pub_date=episode.pub_date.isoformat() if episode.pub_date else None,
            )
        )

    return api_response(
        {
            "episodes": [e.model_dump() for e in episodes],
            "total_matching": result.total_matching,
            "criteria": {
                "since_days": criteria.since_days,
                "max_episodes": criteria.max_episodes,
                "podcast_id": criteria.podcast_id,
                "ready_only": criteria.ready_only,
                "exclude_briefed": criteria.exclude_briefed,
            },
        }
    )


@router.post("")
async def create_briefing(
    request: CreateBriefingRequest,
    state: AppState = Depends(get_app_state),
    user: User = Depends(require_auth),
):
    """
    Create a new briefing.

    This selects episodes based on the criteria and generates a briefing document
    immediately (synchronous for ready_only mode, or just records what needs
    processing for full pipeline mode).

    For ready_only=True: Generates briefing immediately from already-summarized episodes
    (delegates to ``BriefingService.generate_from_criteria`` — same render-write-save
    path as ``POST /api/briefings/morning-briefing``).
    For ready_only=False: Creates a pending briefing record for episodes that need processing.
    """
    criteria = BriefingSelectionCriteria(
        since_days=request.since_days,
        max_episodes=request.max_episodes,
        podcast_id=request.podcast_id,
        ready_only=request.ready_only,
        exclude_briefed=request.exclude_briefed,
    )

    if request.ready_only:
        briefing = state.briefing_service.generate_from_criteria(user.id, criteria)
        if briefing is None:
            return api_response(
                {
                    "status": "no_episodes",
                    "message": "No episodes match the selection criteria",
                    "briefing_id": None,
                    "episodes_selected": 0,
                }
            )
        return api_response(
            {
                "status": "completed",
                "message": f"Briefing created with {briefing.episodes_total} episodes",
                "briefing_id": briefing.id,
                "episodes_selected": briefing.episodes_total,
            }
        )

    # ready_only=False: episodes still need pipeline processing, so we
    # only persist a PENDING row. Selector runs here because there's no
    # render to share with the service path.
    selector = BriefingEpisodeSelector(
        episode_repository=state.repository,
        briefing_repository=state.briefing_repository,
    )
    result = selector.select(criteria)

    if not result.episodes:
        return api_response(
            {
                "status": "no_episodes",
                "message": "No episodes match the selection criteria",
                "briefing_id": None,
                "episodes_selected": 0,
            }
        )

    now = datetime.now(timezone.utc)
    briefing = Briefing(
        user_id=user.id,
        period_start=criteria.date_from,
        period_end=now,
        episode_ids=[ep.id for _, ep in result.episodes],
        episodes_total=len(result.episodes),
        status=BriefingStatus.PENDING,
    )
    state.briefing_repository.save(briefing)

    logger.info(
        "Briefing created (pending processing)",
        briefing_id=briefing.id,
        episodes_count=len(result.episodes),
    )

    return api_response(
        {
            "status": "pending",
            "message": f"Briefing created with {len(result.episodes)} episodes pending processing",
            "briefing_id": briefing.id,
            "episodes_selected": len(result.episodes),
        }
    )


@router.delete("/{briefing_id}")
async def delete_briefing(
    briefing_id: str,
    state: AppState = Depends(get_app_state),
    user: User = Depends(require_auth),
):
    """Delete a briefing."""
    briefing = state.briefing_repository.get_by_id(briefing_id)

    if not briefing:
        not_found("Briefing", briefing_id)

    # Verify ownership
    if briefing.user_id != user.id:
        not_found("Briefing", briefing_id)

    # Delete the file if it exists
    if briefing.file_path:
        briefing_file = state.path_manager.briefings_dir() / briefing.file_path
        if briefing_file.exists():
            try:
                briefing_file.unlink()
                logger.info("Deleted briefing file", path=str(briefing_file))
            except Exception as e:
                logger.warning("Failed to delete briefing file", path=str(briefing_file), error=str(e))

    # Delete the database record
    state.briefing_repository.delete(briefing_id)

    return api_response(
        {
            "status": "deleted",
            "message": f"Briefing {briefing_id} deleted",
            "briefing_id": briefing_id,
        }
    )
