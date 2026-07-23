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

"""Spec #63 — add a podcast and, in single-user mode, follow it in one step.

Once the refresh predicate requires a follower for EVERY podcast (not just
``auto_added`` ones), an add path that never follows is a silent dead end:
the podcast row exists but no recurring refresh will ever pick it up. This
module is the shared orchestration used by every add path that has no
authenticated caller to follow on behalf of (CLI ``thestill add``, the MCP
``add_podcast`` tool, and the web resolve endpoint).
"""

from typing import TYPE_CHECKING, Optional

from structlog import get_logger

from ..models.podcast import Podcast

if TYPE_CHECKING:
    from ..utils.config import Config
    from .auth_service import AuthService
    from .follower_service import FollowerService
    from .podcast_service import PodcastService

logger = get_logger(__name__)


def add_podcast_and_auto_follow(
    podcast_service: "PodcastService",
    follower_service: "FollowerService",
    auth_service: "AuthService",
    config: "Config",
    url: str,
) -> Optional[Podcast]:
    """Add a podcast and, in single-user mode only, follow it for the
    default user in the same call.

    Multi-user callers are untouched: the resolve endpoint keeps its
    browse-without-committing UX, and the explicit web add flow already
    follows the authenticated caller itself. The follow only fires when
    there is exactly one (implicit) user, where "added it" and "wants it
    in their feed" are the same intent.

    The follow is best-effort — a follow failure never turns a
    successful add into a failure.
    """
    podcast = podcast_service.add_podcast(url)
    if podcast is not None and not config.multi_user:
        default_user = auth_service.get_or_create_default_user()
        follower_service.follow_best_effort(default_user.id, podcast.id)
    return podcast
