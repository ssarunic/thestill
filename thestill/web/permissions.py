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

"""Named permissions (spec #110 §7).

Every permission is admin-only today. The point of naming them is that a
future moderator role (§"Future: moderator role") is a change to
``has_permission`` and a grants table, not to any route, CLI command or UI
entry point: callers ask for ``transcript.edit``, never for "admin".
"""

from typing import TYPE_CHECKING, Callable, List, Optional

from fastapi import Depends, HTTPException, Request

from .dependencies import AppState, get_app_state, is_effective_admin, require_auth

if TYPE_CHECKING:
    from ..models.user import User
    from ..utils.config import Config

TRANSCRIPT_EDIT = "transcript.edit"

#: Every named permission, in the order ``/api/auth/status`` lists them.
PERMISSIONS = (TRANSCRIPT_EDIT,)


def has_permission(user: "User", permission: str, config: "Config", *, episode_id: Optional[str] = None) -> bool:
    """Whether ``user`` holds ``permission`` (on ``episode_id``, when scoped).

    ``episode_id`` is in the signature now so a per-podcast grant later
    changes no call sites. Unknown permissions are never granted.
    """
    if permission not in PERMISSIONS:
        return False
    # Spec #110: every permission is admin-only for now.
    return is_effective_admin(user, config)


def permissions_for(user: "User", config: "Config") -> List[str]:
    """The permissions ``user`` holds, for ``/api/auth/status`` and ``/me``."""
    return [p for p in PERMISSIONS if has_permission(user, p, config)]


def require_permission(permission: str) -> Callable[..., "User"]:
    """FastAPI dependency factory: 401 unauthenticated, 403 without ``permission``.

    New routes use this, never ``require_admin`` directly, so the gate
    stays one helper away from a moderator role.
    """
    if permission not in PERMISSIONS:
        raise ValueError(f"unknown permission {permission!r}")

    def dependency(
        request: Request,
        state: AppState = Depends(get_app_state),
        user: "User" = Depends(require_auth),
    ) -> "User":
        episode_id = request.path_params.get("episode_id")
        if not has_permission(user, permission, state.config, episode_id=episode_id):
            raise HTTPException(status_code=403, detail=f"Permission required: {permission}")
        return user

    return dependency
