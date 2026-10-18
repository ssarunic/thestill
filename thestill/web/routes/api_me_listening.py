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
"""Per-user listening progress routes (spec #90).

``/api/me/listening`` is user-authenticated (router-level ``require_auth``
in app.py); in single-user mode the caller is the default user, as for
``/api/inbox``. Static paths are declared before ``/{episode_id}`` so
``latest`` is never captured as an id.
"""

from typing import Literal, Optional

from fastapi import APIRouter, Depends, Query, Request
from pydantic import BaseModel, Field

from ...models.listening import ProgressWrite
from ...models.user import User
from ...services.listening_progress_service import InvalidProgressError, ListeningEpisodeNotFoundError
from ..analytics_request import may_measure
from ..dependencies import AppState, get_app_state, require_auth
from ..responses import api_response, bad_request, not_found

router = APIRouter()

# bigint headroom; a tab never gets near it.
_MAX_SEQ = 2**62


class ProgressWriteRequest(ProgressWrite):
    """One position write. ``mark_*`` events ignore ``position_seconds``.

    Position and session validation happens in the service so a bad value
    is a 400, not a 422; only the bigint bound on ``seq`` is checked here.
    """

    seq: int = Field(ge=0, le=_MAX_SEQ)


def _json(model: Optional[BaseModel]) -> Optional[dict]:
    return model.model_dump(mode="json") if model else None


@router.get("/latest")
def get_latest(state: AppState = Depends(get_app_state), user: User = Depends(require_auth)) -> dict:
    """The restore candidate: most recently updated unfinished episode not stopped since."""
    return api_response({"item": _json(state.listening_service.latest(user.id))})


@router.get("")
def list_listening(
    state_filter: Literal["in_progress"] = Query("in_progress", alias="state"),
    limit: int = Query(3, ge=1, le=20),
    state: AppState = Depends(get_app_state),
    user: User = Depends(require_auth),
) -> dict:
    """Unfinished episodes, newest first, for the Continue listening strip."""
    items = state.listening_service.list_in_progress(user.id, limit)
    return api_response({"items": [_json(i) for i in items]})


@router.get("/{episode_id}")
def get_listening(
    episode_id: str,
    state: AppState = Depends(get_app_state),
    user: User = Depends(require_auth),
) -> dict:
    """The caller's progress on one episode, or ``{"item": null}``."""
    return api_response({"item": _json(state.listening_service.get_summary(user.id, episode_id))})


@router.put("/{episode_id}")
def put_listening(
    episode_id: str,
    body: ProgressWriteRequest,
    request: Request,
    state: AppState = Depends(get_app_state),
    user: User = Depends(require_auth),
) -> dict:
    """Upsert the caller's position. ``accepted: false`` means an older write was ignored."""
    try:
        outcome = state.listening_service.record(user.id, episode_id, body, measure=may_measure(user, request))
    except ListeningEpisodeNotFoundError:
        not_found("Episode", episode_id)
    except InvalidProgressError as e:
        bad_request(str(e))
    return api_response(
        {
            "accepted": outcome.accepted,
            "completed": outcome.completed,
            "listening": _json(outcome.listening),
        }
    )


__all__ = ["router"]
