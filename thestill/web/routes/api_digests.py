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
Digest API endpoints for Thestill web UI.

Provides endpoints for listing, viewing, creating, and reading digest documents.
"""

from datetime import datetime, timezone
from pathlib import Path
from typing import List, Optional, Union

from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel, Field, field_validator
from structlog import get_logger

from ...models.digest import Digest, DigestStatus
from ...models.user import User
from ...services.digest_generator import DigestGenerator
from ...services.digest_selector import DigestEpisodeSelector, DigestSelectionCriteria
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


class DigestResponse(BaseModel):
    """Response model for a single digest."""

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


class CreateDigestRequest(BaseModel):
    """Request body for creating a new digest."""

    since_days: int = Field(default=7, ge=1, le=365)
    max_episodes: int = Field(default=10, ge=1, le=100)
    podcast_id: Optional[str] = None
    ready_only: bool = False
    exclude_digested: bool = False


class CreateDigestResponse(BaseModel):
    """Response for a successful digest creation."""

    status: str
    message: str
    digest_id: str
    episodes_selected: int


class DigestPreviewRequest(BaseModel):
    """Request body for previewing digest selection."""

    since_days: int = Field(default=7, ge=1, le=365)
    max_episodes: int = Field(default=10, ge=1, le=100)
    podcast_id: Optional[str] = None
    ready_only: bool = False
    exclude_digested: bool = False


class DigestPreviewEpisode(BaseModel):
    """Episode info in preview response."""

    episode_id: str
    episode_title: str
    episode_slug: str
    podcast_id: str
    podcast_title: str
    podcast_slug: str
    state: str
    pub_date: Optional[str] = None


class DigestPreviewResponse(BaseModel):
    """Response for digest preview."""

    status: str
    episodes: List[DigestPreviewEpisode]
    total_matching: int
    criteria: dict


# =============================================================================
# Helper Functions
# =============================================================================


def _digest_to_response(digest: Digest) -> DigestResponse:
    """Convert Digest model to response model."""
    return DigestResponse(
        id=digest.id,
        user_id=digest.user_id,
        created_at=digest.created_at.isoformat(),
        updated_at=digest.updated_at.isoformat(),
        period_start=digest.period_start.isoformat(),
        period_end=digest.period_end.isoformat(),
        status=digest.status.value,
        file_path=digest.file_path,
        episode_ids=digest.episode_ids,
        episodes_total=digest.episodes_total,
        episodes_completed=digest.episodes_completed,
        episodes_failed=digest.episodes_failed,
        processing_time_seconds=digest.processing_time_seconds,
        error_message=digest.error_message,
        success_rate=digest.success_rate,
        is_complete=digest.is_complete,
    )


def _list_narrations_for_digest(narrations_dir: Path, digest_id: str) -> List[dict]:
    """Filesystem-driven list of narration variants for ``digest_id``.

    Each variant is keyed by ``<digest_id>-<slug>.json`` (the runner's
    canonical filename), so a directory glob is sufficient — no schema
    column needed for v1. ``slug`` is recovered by stripping the
    ``<digest_id>-`` prefix off the filename stem.
    """
    if not narrations_dir.exists():
        return []
    out: List[dict] = []
    prefix = f"{digest_id}-"
    for json_path in sorted(narrations_dir.glob(f"{digest_id}-*.json")):
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


class NarrateDigestRequest(BaseModel):
    """Body for ``POST /api/digests/{digest_id}/narrate``."""

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
            "Output slug (filenames are keyed on ``<digest_id>-<slug>``)."
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
async def list_digests(
    limit: int = 50,
    offset: int = 0,
    status: Optional[str] = None,
    state: AppState = Depends(get_app_state),
    user: User = Depends(require_auth),
):
    """
    List digests for the current user.

    Supports pagination and optional status filtering.
    """
    # Validate status if provided
    status_enum = None
    if status:
        try:
            status_enum = DigestStatus(status)
        except ValueError:
            valid_statuses = [s.value for s in DigestStatus]
            bad_request(f"Invalid status: {status}. Valid values: {valid_statuses}")

    digests = state.digest_repository.get_all(
        limit=limit,
        offset=offset,
        status=status_enum,
        user_id=user.id,
    )

    # Get total count for pagination
    total = state.digest_repository.count(
        status=status_enum,
        user_id=user.id,
    )

    digest_responses = [_digest_to_response(d) for d in digests]

    return paginated_response(
        items=[d.model_dump() for d in digest_responses],
        total=total,
        offset=offset,
        limit=limit,
        items_key="digests",
    )


@router.get("/latest")
async def get_latest_digest(
    state: AppState = Depends(get_app_state),
    user: User = Depends(require_auth),
):
    """
    Return the user's most recent digest, lazy-generating one when eligible.

    Selection comes from the inbox window
    ``[previous_digest.period_end, now)`` — so each delivered episode is
    briefed exactly once. The throttle inside ``DigestService`` collapses
    accidental rapid-fire triggers (cron racing the UI). Returns 404 when
    no eligible inbox items fall in the open window — callers should hide
    the "Today's briefing" card.
    """
    digest = state.digest_service.generate_for_user(user.id)
    if digest is None:
        not_found("Digest", "latest")

    narrations = _list_narrations_for_digest(state.path_manager.narrations_dir(), digest.id)
    return api_response(
        {
            "digest": _digest_to_response(digest).model_dump(),
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

    Returns episodes that would be included in a quick catch-up digest,
    using DIGEST_DEFAULT_SINCE_DAYS and DIGEST_DEFAULT_MAX_EPISODES from config.
    """
    criteria = DigestSelectionCriteria(
        since_days=state.config.digest_default_since_days,
        max_episodes=state.config.digest_default_max_episodes,
        ready_only=True,
        exclude_digested=True,
    )

    selector = DigestEpisodeSelector(
        episode_repository=state.repository,
        digest_repository=state.digest_repository,
    )

    result = selector.preview(criteria)

    episodes = []
    for podcast, episode in result.episodes:
        episodes.append(
            DigestPreviewEpisode(
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
                "exclude_digested": criteria.exclude_digested,
            },
        }
    )


@router.post("/morning-briefing")
async def create_morning_briefing(
    state: AppState = Depends(get_app_state),
    user: User = Depends(require_auth),
):
    """
    Create a morning briefing digest using server-configured defaults.

    Uses DIGEST_DEFAULT_SINCE_DAYS and DIGEST_DEFAULT_MAX_EPISODES from config.
    Only includes already-summarized episodes and excludes previously digested ones.
    """
    import time

    criteria = DigestSelectionCriteria(
        since_days=state.config.digest_default_since_days,
        max_episodes=state.config.digest_default_max_episodes,
        ready_only=True,
        exclude_digested=True,
    )

    selector = DigestEpisodeSelector(
        episode_repository=state.repository,
        digest_repository=state.digest_repository,
    )

    result = selector.select(criteria)

    if not result.episodes:
        return api_response(
            {
                "status": "no_episodes",
                "message": "No episodes match the selection criteria",
                "digest_id": None,
                "episodes_selected": 0,
            }
        )

    # Create digest record
    now = datetime.now(timezone.utc)
    digest = Digest(
        user_id=user.id,
        period_start=criteria.date_from,
        period_end=now,
        episode_ids=[ep.id for _, ep in result.episodes],
        episodes_total=len(result.episodes),
    )

    # Generate digest immediately from summarized episodes
    start_time = time.time()

    generator = DigestGenerator(state.path_manager, state.config.file_storage)
    content = generator.generate(
        episodes=result.episodes,
        processing_time_seconds=0,
        failures=[],
    )

    # Write digest to file
    output_filename = f"digest_{now.strftime('%Y%m%d_%H%M%S')}.md"
    output_path = state.path_manager.digests_dir() / output_filename
    generator.write(content, output_path)

    processing_time = time.time() - start_time

    # Update digest with completion info
    digest.mark_completed(
        file_path=output_filename,
        episodes_completed=len(result.episodes),
        episodes_failed=0,
        processing_time_seconds=processing_time,
    )

    state.digest_repository.save(digest)

    logger.info(
        "Morning briefing digest created",
        digest_id=digest.id,
        episodes_count=len(result.episodes),
        file_path=output_filename,
    )

    return api_response(
        {
            "status": "completed",
            "message": f"Digest created with {len(result.episodes)} episodes",
            "digest_id": digest.id,
            "episodes_selected": len(result.episodes),
        }
    )


@router.get("/{digest_id}")
async def get_digest(
    digest_id: str,
    state: AppState = Depends(get_app_state),
    user: User = Depends(require_auth),
):
    """Get a single digest by ID, plus any narration variants on disk."""
    digest = state.digest_repository.get_by_id(digest_id)

    if not digest:
        not_found("Digest", digest_id)

    if digest.user_id != user.id:
        not_found("Digest", digest_id)

    narrations = _list_narrations_for_digest(state.path_manager.narrations_dir(), digest_id)
    return api_response(
        {
            "digest": _digest_to_response(digest).model_dump(),
            "narrations": narrations,
        }
    )


@router.post("/{digest_id}/narrate", status_code=201)
async def narrate_digest(
    digest_id: str,
    body: NarrateDigestRequest,
    state: AppState = Depends(get_app_state),
    user: User = Depends(require_auth),
):
    """Generate (or regenerate) a narrated digest for ``digest_id``.

    Used by the length-switcher UI: each click writes
    ``<digest_id>-<slug>.{json,md}`` so previous variants are preserved.
    """
    digest = state.digest_repository.get_by_id(digest_id)
    if not digest:
        not_found("Digest", digest_id)
    if digest.user_id != user.id:
        not_found("Digest", digest_id)

    if state.narration_runner is None:
        raise HTTPException(
            status_code=503,
            detail=(
                "Narration is disabled — set NARRATION_ENABLED=true and"
                " configure an LLM provider to enable digest narration."
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
            digest_id=digest_id,
            target_duration_seconds=target_seconds,
            slug=slug,
        )
    except NarrationRunnerError as exc:
        logger.warning(
            "digest.narrate_failed",
            digest_id=digest_id,
            error=str(exc),
        )
        not_found("Digest", digest_id)

    stats = run.content.stats
    return api_response(
        {
            "narration_id": run.narration_id,
            "digest_id": run.digest_id,
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


@router.get("/{digest_id}/content")
async def get_digest_content(
    digest_id: str,
    state: AppState = Depends(get_app_state),
    user: User = Depends(require_auth),
):
    """
    Get the markdown content of a digest.

    Returns the raw markdown content if the digest file exists.
    """
    digest = state.digest_repository.get_by_id(digest_id)

    if not digest:
        not_found("Digest", digest_id)

    # Verify ownership
    if digest.user_id != user.id:
        not_found("Digest", digest_id)

    if not digest.file_path:
        return api_response(
            {
                "digest_id": digest_id,
                "content": None,
                "available": False,
            }
        )

    # Read digest file
    digest_file = state.path_manager.digests_dir() / digest.file_path
    if not digest_file.exists():
        return api_response(
            {
                "digest_id": digest_id,
                "content": None,
                "available": False,
            }
        )

    try:
        content = digest_file.read_text(encoding="utf-8")
        return api_response(
            {
                "digest_id": digest_id,
                "content": content,
                "available": True,
            }
        )
    except Exception as e:
        logger.error("Failed to read digest file", digest_id=digest_id, error=str(e))
        return api_response(
            {
                "digest_id": digest_id,
                "content": None,
                "available": False,
                "error": str(e),
            }
        )


@router.get("/{digest_id}/episodes")
async def get_digest_episodes(
    digest_id: str,
    state: AppState = Depends(get_app_state),
    user: User = Depends(require_auth),
):
    """
    Get detailed information about episodes in a digest.

    Returns episode and podcast details for all episodes included in the digest.
    """
    digest = state.digest_repository.get_by_id(digest_id)

    if not digest:
        not_found("Digest", digest_id)

    # Verify ownership
    if digest.user_id != user.id:
        not_found("Digest", digest_id)

    episodes_info = []
    for episode_id in digest.episode_ids:
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
            "digest_id": digest_id,
            "episodes": episodes_info,
            "count": len(episodes_info),
        }
    )


@router.post("/preview")
async def preview_digest(
    request: DigestPreviewRequest,
    state: AppState = Depends(get_app_state),
    user: User = Depends(require_auth),
):
    """
    Preview which episodes would be included in a digest.

    This is a dry-run operation that shows what would be selected
    without actually creating a digest.
    """
    criteria = DigestSelectionCriteria(
        since_days=request.since_days,
        max_episodes=request.max_episodes,
        podcast_id=request.podcast_id,
        ready_only=request.ready_only,
        exclude_digested=request.exclude_digested,
    )

    selector = DigestEpisodeSelector(
        episode_repository=state.repository,
        digest_repository=state.digest_repository,
    )

    result = selector.preview(criteria)

    episodes = []
    for podcast, episode in result.episodes:
        episodes.append(
            DigestPreviewEpisode(
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
                "exclude_digested": criteria.exclude_digested,
            },
        }
    )


@router.post("")
async def create_digest(
    request: CreateDigestRequest,
    state: AppState = Depends(get_app_state),
    user: User = Depends(require_auth),
):
    """
    Create a new digest.

    This selects episodes based on the criteria and generates a digest document
    immediately (synchronous for ready_only mode, or just records what needs
    processing for full pipeline mode).

    For ready_only=True: Generates digest immediately from already-summarized episodes.
    For ready_only=False: Creates a pending digest record for episodes that need processing.
    """
    import time

    criteria = DigestSelectionCriteria(
        since_days=request.since_days,
        max_episodes=request.max_episodes,
        podcast_id=request.podcast_id,
        ready_only=request.ready_only,
        exclude_digested=request.exclude_digested,
    )

    selector = DigestEpisodeSelector(
        episode_repository=state.repository,
        digest_repository=state.digest_repository,
    )

    result = selector.select(criteria)

    if not result.episodes:
        return api_response(
            {
                "status": "no_episodes",
                "message": "No episodes match the selection criteria",
                "digest_id": None,
                "episodes_selected": 0,
            }
        )

    # Create digest record
    now = datetime.now(timezone.utc)
    digest = Digest(
        user_id=user.id,
        period_start=criteria.date_from,
        period_end=now,
        episode_ids=[ep.id for _, ep in result.episodes],
        episodes_total=len(result.episodes),
    )

    if request.ready_only:
        # Generate digest immediately from summarized episodes
        start_time = time.time()

        generator = DigestGenerator(state.path_manager, state.config.file_storage)
        content = generator.generate(
            episodes=result.episodes,
            processing_time_seconds=0,  # Will be updated
            failures=[],
        )

        # Write digest to file
        output_filename = f"digest_{now.strftime('%Y%m%d_%H%M%S')}.md"
        output_path = state.path_manager.digests_dir() / output_filename
        generator.write(content, output_path)

        processing_time = time.time() - start_time

        # Update digest with completion info
        digest.mark_completed(
            file_path=output_filename,
            episodes_completed=len(result.episodes),
            episodes_failed=0,
            processing_time_seconds=processing_time,
        )

        state.digest_repository.save(digest)

        logger.info(
            "Digest created",
            digest_id=digest.id,
            episodes_count=len(result.episodes),
            file_path=output_filename,
        )

        return api_response(
            {
                "status": "completed",
                "message": f"Digest created with {len(result.episodes)} episodes",
                "digest_id": digest.id,
                "episodes_selected": len(result.episodes),
            }
        )
    else:
        # For non-ready-only mode, mark as pending (episodes need processing)
        digest.status = DigestStatus.PENDING
        state.digest_repository.save(digest)

        logger.info(
            "Digest created (pending processing)",
            digest_id=digest.id,
            episodes_count=len(result.episodes),
        )

        return api_response(
            {
                "status": "pending",
                "message": f"Digest created with {len(result.episodes)} episodes pending processing",
                "digest_id": digest.id,
                "episodes_selected": len(result.episodes),
            }
        )


@router.delete("/{digest_id}")
async def delete_digest(
    digest_id: str,
    state: AppState = Depends(get_app_state),
    user: User = Depends(require_auth),
):
    """Delete a digest."""
    digest = state.digest_repository.get_by_id(digest_id)

    if not digest:
        not_found("Digest", digest_id)

    # Verify ownership
    if digest.user_id != user.id:
        not_found("Digest", digest_id)

    # Delete the file if it exists
    if digest.file_path:
        digest_file = state.path_manager.digests_dir() / digest.file_path
        if digest_file.exists():
            try:
                digest_file.unlink()
                logger.info("Deleted digest file", path=str(digest_file))
            except Exception as e:
                logger.warning("Failed to delete digest file", path=str(digest_file), error=str(e))

    # Delete the database record
    state.digest_repository.delete(digest_id)

    return api_response(
        {
            "status": "deleted",
            "message": f"Digest {digest_id} deleted",
            "digest_id": digest_id,
        }
    )
