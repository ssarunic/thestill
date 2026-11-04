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

"""Transcript editor routes (spec #110 §6).

Mounted at ``/api/episodes``; every route depends on
``require_permission("transcript.edit")``, never on ``require_admin``, so a
moderator role later changes the permission helper and nothing here.
The transcript itself is read from the existing transcript endpoint,
which serves the effective transcript; these routes carry the op log,
conflicts and the speaker panel, and take writes.
"""

import mimetypes
from typing import Any, Dict, List, Optional

from fastapi import APIRouter, Depends, HTTPException, Query
from fastapi.responses import FileResponse
from pydantic import BaseModel, Field
from structlog import get_logger

from ...models.transcript_edits import TranscriptEdit
from ...models.user import User
from ...services.abuse.actor import Actor
from ...services.transcript_edit_service import EditorState, TranscriptEditError, TranscriptEditService
from ..dependencies import AppState, get_app_state
from ..permissions import TRANSCRIPT_EDIT, require_permission
from ..responses import api_response

logger = get_logger(__name__)

router = APIRouter()

_EDITOR = Depends(require_permission(TRANSCRIPT_EDIT))

_STATUS = {"not_found": 404, "no_transcript": 400, "busy": 409, "stale": 409, "rejected": 409, "invalid": 422}


class ApplyEditsRequest(BaseModel):
    base_seq: int = Field(ge=0, description="The last seq the editor saw; 409 when it moved.")
    session_id: str = Field(min_length=1, max_length=64)
    report_id: Optional[str] = Field(default=None, max_length=64, description="Spec #86 report this applies.")
    ops: List[Dict[str, Any]] = Field(min_length=1, max_length=50)


class RevertEditRequest(BaseModel):
    base_seq: int = Field(ge=0)
    session_id: str = Field(min_length=1, max_length=64)


class FinishSessionRequest(BaseModel):
    session_id: str = Field(min_length=1, max_length=64)


class SaveHostRequest(BaseModel):
    name: str = Field(min_length=1, max_length=200)
    entity_id: Optional[str] = Field(default=None, max_length=200)


def _service(state: AppState) -> TranscriptEditService:
    service = state.transcript_edit_service
    if service is None:
        raise HTTPException(status_code=503, detail="Transcript editing is not available")
    return service


def _refused(error: TranscriptEditError) -> HTTPException:
    detail: Dict[str, Any] = {"error": error.kind, "message": str(error)}
    for key, value in error.detail.items():
        if key == "edits":
            detail["edits"] = [_edit_json(e, "applied") for e in value]
        elif key == "conflict":
            detail["conflict"] = value
        else:
            detail[key] = value
    if error.kind == "stale":
        detail["error"] = "stale_base_seq"
    elif error.kind == "rejected":
        detail["error"] = "edit_rejected"
    return HTTPException(status_code=_STATUS[error.kind], detail=detail)


def _edit_json(edit: TranscriptEdit, status: str, conflict: Optional[Dict[str, Any]] = None) -> Dict[str, Any]:
    return {
        "id": edit.id,
        "seq": edit.seq,
        "op": edit.op,
        "anchor": edit.anchor,
        "args": edit.args,
        "before": edit.before,
        "reverts": edit.reverts,
        "session_id": edit.session_id,
        "report_id": edit.report_id,
        "actor_user_id": edit.actor_user_id,
        "actor_kind": edit.actor_kind,
        "created_at": edit.created_at.isoformat(),
        "status": status,
        "conflict": conflict,
    }


def _state_json(state: EditorState) -> Dict[str, Any]:
    effective = state.effective
    applied = {e.id for e in effective.applied}
    conflicts = {c.edit_id: c.to_dict() for c in effective.conflicts}
    edits = []
    for edit in effective.edits:
        # A revert that was itself reverted (a redo) is "reverted" too.
        if edit.id in effective.reverted_ids:
            status = "reverted"
        elif edit.op == "revert":
            status = "revert"
        elif edit.id in applied:
            status = "applied"
        else:
            status = "conflict"
        edits.append(_edit_json(edit, status, conflicts.get(edit.id)))
    return {
        "episode_id": state.episode.id,
        "last_seq": effective.last_seq,
        "raw_sha256": effective.raw_sha256,
        "overlay_failed": effective.overlay_failed,
        "busy": state.busy,
        "edits": edits,
        "conflicts": list(conflicts.values()),
        "speakers": [
            {"label": s.label, "name": s.name, "entity_id": s.entity_id, "segment_ids": s.segment_ids}
            for s in effective.speakers
        ],
        "summary_stale_edits": state.summary_stale_edits,
        "algorithm_version": effective.transcript.algorithm_version,
        "local_audio": state.local_audio,
    }


@router.get("/{episode_id}/transcript/edits")
def get_transcript_edits(
    episode_id: str,
    state: AppState = Depends(get_app_state),
    _: User = _EDITOR,
) -> Dict[str, Any]:
    """The op log with each op's status, the conflicts, the speaker panel
    and whether the summary predates live edits."""
    try:
        return api_response(_state_json(_service(state).get_state(episode_id)))
    except TranscriptEditError as error:
        raise _refused(error) from error


@router.post("/{episode_id}/transcript/edits")
def apply_transcript_edits(
    episode_id: str,
    request: ApplyEditsRequest,
    state: AppState = Depends(get_app_state),
    user: User = _EDITOR,
) -> Dict[str, Any]:
    """Append ops against ``base_seq``. 409 ``stale_base_seq`` carries the
    newer ops; 409 ``busy`` while a transcribe or clean is in flight; 409
    ``edit_rejected`` when an op no longer applies; 422 for a malformed op."""
    try:
        result = _service(state).apply(
            episode_id,
            actor=Actor.web(user.id),
            session_id=request.session_id,
            base_seq=request.base_seq,
            ops=request.ops,
            report_id=request.report_id,
        )
    except TranscriptEditError as error:
        raise _refused(error) from error
    return api_response({"applied": [_edit_json(e, "applied") for e in result.applied], "last_seq": result.last_seq})


@router.post("/{episode_id}/transcript/edits/finish")
def finish_transcript_edits(
    episode_id: str,
    request: FinishSessionRequest,
    state: AppState = Depends(get_app_state),
    user: User = _EDITOR,
) -> Dict[str, Any]:
    """Close the session: one audit row and the spec #106 entity refresh."""
    try:
        result = _service(state).finish(episode_id, actor=Actor.web(user.id), session_id=request.session_id)
    except TranscriptEditError as error:
        raise _refused(error) from error
    return api_response({"session_ops": result.session_ops, "refresh": result.refresh, "task_id": result.task_id})


@router.post("/{episode_id}/transcript/edits/save-host")
def save_speaker_as_host(
    episode_id: str,
    request: SaveHostRequest,
    state: AppState = Depends(get_app_state),
    user: User = _EDITOR,
) -> Dict[str, Any]:
    """Add a named speaker to the podcast's facts hosts (audited)."""
    try:
        hosts = _service(state).save_as_host(
            episode_id, name=request.name, entity_id=request.entity_id, actor=Actor.web(user.id)
        )
    except TranscriptEditError as error:
        raise _refused(error) from error
    return api_response({"hosts": hosts})


@router.get("/{episode_id}/transcript/edits/people")
def search_people(
    episode_id: str,  # pylint: disable=unused-argument  # scopes the permission check
    q: str = Query("", max_length=100),
    state: AppState = Depends(get_app_state),
    _: User = _EDITOR,
) -> Dict[str, Any]:
    """Person entities for the rename-or-link picker."""
    return api_response({"people": _service(state).search_people(q)})


@router.get("/{episode_id}/transcript/edits/audio")
def get_editor_audio(
    episode_id: str,
    state: AppState = Depends(get_app_state),
    _: User = _EDITOR,
) -> FileResponse:
    """Spec #110 §5.6 — the episode's downloaded audio, the file the
    timestamps were made from (the publisher's URL may carry other ads).
    404 when the copy is gone or the deployment keeps audio off local disk."""
    service = _service(state)
    try:
        _podcast, episode = service._episode(episode_id)  # pylint: disable=protected-access
    except TranscriptEditError as error:
        raise _refused(error) from error
    path = service.local_audio_path(episode)
    if path is None:
        raise HTTPException(status_code=404, detail={"error": "no_local_audio", "message": "No local audio copy"})
    media_type = mimetypes.guess_type(path.name)[0] or "application/octet-stream"
    return FileResponse(path, media_type=media_type, filename=path.name, content_disposition_type="inline")


@router.post("/{episode_id}/transcript/edits/{edit_id}/revert")
def revert_transcript_edit(
    episode_id: str,
    edit_id: str,
    request: RevertEditRequest,
    state: AppState = Depends(get_app_state),
    user: User = _EDITOR,
) -> Dict[str, Any]:
    """Undo (or redo, by reverting a revert) as a new row."""
    try:
        result = _service(state).revert(
            episode_id, edit_id, actor=Actor.web(user.id), session_id=request.session_id, base_seq=request.base_seq
        )
    except TranscriptEditError as error:
        raise _refused(error) from error
    return api_response({"applied": [_edit_json(e, "applied") for e in result.applied], "last_seq": result.last_seq})
