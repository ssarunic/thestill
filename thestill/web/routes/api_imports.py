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
Spec #31 — Import arbitrary episodes API.

POST /api/imports {url} → 201 with the materialised episode + inbox row.

Idempotent: re-posting the same URL by the same user returns 200 with
``deduplicated=true`` and the existing row (no second pipeline task).
"""

from fastapi import APIRouter, Depends
from pydantic import BaseModel
from structlog import get_logger

from ...models.user import User
from ...services.import_service import (
    ImportError as ImportServiceError,
    ResolverError,
    UnsupportedUrlError,
)
from ..dependencies import AppState, get_app_state, require_auth
from ..responses import api_response, bad_request

logger = get_logger(__name__)

router = APIRouter()


class ImportRequest(BaseModel):
    """Body for ``POST /api/imports``."""

    url: str


@router.post("")
async def create_import(
    body: ImportRequest,
    app_state: AppState = Depends(get_app_state),
    user: User = Depends(require_auth),
):
    """Import a URL into the calling user's inbox."""
    url = body.url.strip()
    if not url:
        bad_request("url must be a non-empty string")

    try:
        result = app_state.import_service.import_url(user_id=user.id, url=url)
    except UnsupportedUrlError as exc:
        bad_request(str(exc))
    except ResolverError as exc:
        bad_request(str(exc))
    except ImportServiceError as exc:
        bad_request(str(exc))

    parent: dict | None = None
    if result.parent_podcast_id is not None:
        parent_row = app_state.repository.get(result.parent_podcast_id)
        if parent_row is not None:
            parent = {
                "id": parent_row.id,
                "title": parent_row.title,
                "slug": parent_row.slug or "",
            }

    return api_response(
        {
            "import": {
                "episode_id": result.episode_id,
                "canonical_id": result.canonical_id,
                "title": result.title,
                "kind": result.kind,
                "source_handle": result.source_handle,
                "deduplicated": not result.episode_created,
                "inbox_created": result.inbox_created,
                "inbox_entry": result.inbox_entry.model_dump(mode="json"),
                "parent": parent,
            }
        }
    )
