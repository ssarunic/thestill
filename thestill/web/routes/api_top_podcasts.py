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

"""Region-scoped top podcasts API.

Reads from the ``top_podcasts`` + ``top_podcast_rankings`` tables seeded
from ``data/top_podcasts_<region>.json`` and surfaces them filtered by
the caller's region. The region resolution chain is:

    explicit ?region= query param  →  current user's stored region
                                   →  first available seeded region
                                   →  ``"us"`` as a final fallback

The returned ``region`` field tells the UI which chart it actually got
back, regardless of which step in the chain produced it.
"""

from typing import Optional

from fastapi import APIRouter, Depends, Query, Request
from structlog import get_logger

from ..dependencies import AppState, get_app_state, get_current_user
from ..responses import api_response

logger = get_logger(__name__)

router = APIRouter()

# Hard upper bound matches the data shape — each region ships ~500 rows.
_MAX_LIMIT = 500
_FALLBACK_REGION = "us"


def _resolve_region(
    requested: Optional[str],
    user_region: Optional[str],
    available_regions: list[str],
) -> str:
    """Pick the region we'll actually serve.

    A request for a region that isn't seeded silently degrades to the
    user's region (or fallback). The picked code is returned to the UI
    so it can render an honest "Top in <X>" header.
    """
    available = set(available_regions)

    if requested:
        cleaned = requested.strip().lower()
        if cleaned in available:
            return cleaned

    if user_region and user_region in available:
        return user_region

    if available_regions:
        return available_regions[0]

    return _FALLBACK_REGION


@router.get("")
async def list_top_podcasts(
    request: Request,
    region: Optional[str] = Query(None, description="ISO 3166-1 alpha-2; defaults to user's region"),
    limit: int = Query(50, ge=1, le=_MAX_LIMIT),
    q: Optional[str] = Query(
        None,
        min_length=1,
        max_length=100,
        description="Case-insensitive substring matched against name and artist",
    ),
    state: AppState = Depends(get_app_state),
):
    """Return the top-podcast chart for the resolved region.

    When ``q`` is provided, the chart is filtered by case-insensitive substring
    match against ``name`` and ``artist``; rank order is preserved. Each row
    carries an ``is_following`` flag — true iff the resolved user follows a
    podcast with the same ``rss_url``. Anonymous callers always see ``false``.
    """
    user = get_current_user(request, state)
    user_region = user.region.lower() if user and user.region else None
    user_id = user.id if user else None

    # Treat whitespace-only `q` as if it were absent. FastAPI's `min_length=1`
    # rejects the empty string before we get here, but `q="  "` would otherwise
    # trigger an always-empty SQL filter.
    q_clean: Optional[str] = q.strip() if q else None
    if not q_clean:
        q_clean = None

    available = state.repository.get_top_podcast_regions()
    resolved = _resolve_region(region, user_region, available)

    rows = state.repository.get_top_podcasts(
        resolved,
        limit=limit,
        q=q_clean,
        user_id=user_id,
    )

    logger.debug(
        "top_podcasts_served",
        region=resolved,
        user_id=user_id,
        q=q_clean,
        count=len(rows),
    )

    return api_response(
        {
            "region": resolved,
            "available_regions": available,
            "user_region": user_region,
            "count": len(rows),
            "top_podcasts": rows,
        }
    )
