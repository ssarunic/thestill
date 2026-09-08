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
"""Who is calling an MCP handler, and what may they do (spec #78 Phase 2).

The one seam ``tools.py`` and ``resources.py`` both import. Over HTTP the
ASGI guard stashes the resolved user, effective admin flag and scopes on
the ASGI scope state; the MCP SDK forwards the Starlette request into
``server.request_context.request``, so handlers read identity from there.
On stdio there is no request at all, and every handler takes the explicit
identity-None branch (legacy semantics: whole corpus, delete, numeric
ids, no scopes) rather than inheriting per-user behaviour by accident.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import TYPE_CHECKING, FrozenSet, List, Optional

from .scopes import effective_scopes, scope_for_tool

if TYPE_CHECKING:
    from mcp.server import Server
    from mcp.types import Tool

    from ..models.user import User

STATE_USER = "mcp_user"
STATE_IS_ADMIN = "mcp_is_admin"
STATE_SCOPES = "mcp_scopes"


@dataclass(frozen=True)
class McpIdentity:
    """Resolved caller. ``user is None`` means stdio (unscoped local access)."""

    user: Optional["User"]
    is_admin: bool
    scopes: FrozenSet[str]

    @property
    def is_remote(self) -> bool:
        return self.user is not None

    @property
    def effective_scopes(self) -> FrozenSet[str]:
        return effective_scopes(self.scopes, is_admin=self.is_admin)


STDIO = McpIdentity(user=None, is_admin=True, scopes=frozenset())


class ScopeError(Exception):
    """A remote caller invoked a tool its token does not grant."""

    def __init__(self, tool_name: str, required_scope: Optional[str]):
        self.tool_name = tool_name
        self.required_scope = required_scope
        if required_scope is None:
            detail = f"Tool {tool_name!r} is not available over the remote connector."
        else:
            detail = f"This connector token is missing the {required_scope!r} scope required by {tool_name!r}."
        super().__init__(detail)


def current_mcp_identity(server: "Server") -> McpIdentity:
    """Read identity from the current request, or STDIO when there is none."""
    try:
        request = server.request_context.request
    except LookupError:  # called outside any request (defensive; tests)
        return STDIO
    if request is None:
        return STDIO
    state = request.scope.get("state") or {}
    user = state.get(STATE_USER)
    if user is None:
        return STDIO
    return McpIdentity(
        user=user,
        is_admin=bool(state.get(STATE_IS_ADMIN, False)),
        scopes=frozenset(state.get(STATE_SCOPES) or ()),
    )


def require_scope(identity: McpIdentity, tool_name: str) -> None:
    """Raise ScopeError unless the caller may invoke ``tool_name``.

    stdio passes unconditionally. Over HTTP an unregistered tool fails
    closed, and the check is against *effective* scopes so a demoted
    admin loses ``pipeline`` immediately.
    """
    if not identity.is_remote:
        return
    required = scope_for_tool(tool_name)
    if required is None or required not in identity.effective_scopes:
        raise ScopeError(tool_name, required)


def visible_tools(identity: McpIdentity, tools: List["Tool"]) -> List["Tool"]:
    """Filter ``tools/list`` to what the caller may invoke (advisory; the
    call-time check is the control)."""
    if not identity.is_remote:
        return tools
    granted = identity.effective_scopes
    return [t for t in tools if scope_for_tool(t.name) in granted]
