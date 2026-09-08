"""Spec #78 Phase 2 — scope registry contract + identity rules."""

from __future__ import annotations

from types import SimpleNamespace

import pytest
from mcp.types import Tool

from tests.docs.helpers import extract_mcp_tool_names
from thestill.mcp.identity import (
    STATE_IS_ADMIN,
    STATE_SCOPES,
    STATE_USER,
    STDIO,
    McpIdentity,
    ScopeError,
    current_mcp_identity,
    require_scope,
    visible_tools,
)
from thestill.mcp.scopes import effective_scopes, registered_tools, scope_for_tool
from thestill.models.user import User


def test_every_registered_tool_has_a_scope():
    """Fail closed: a tool missing from the registry would vanish from the
    remote surface, so the registry must name every ``Tool(name=...)`` the
    MCP modules declare — and nothing else."""
    assert extract_mcp_tool_names() == set(registered_tools())


def test_unknown_tool_has_no_scope():
    assert scope_for_tool("definitely_not_a_tool") is None


def test_effective_scopes_strips_pipeline_for_non_admin():
    granted = frozenset({"read", "follows", "pipeline"})
    assert effective_scopes(granted, is_admin=True) == granted
    assert effective_scopes(granted, is_admin=False) == frozenset({"read", "follows"})


def _remote(scopes, *, is_admin=False) -> McpIdentity:
    return McpIdentity(user=User(id="u1", email="u@example.com"), is_admin=is_admin, scopes=frozenset(scopes))


class TestRequireScope:
    def test_stdio_is_unscoped(self):
        require_scope(STDIO, "refresh_feeds")  # no raise

    def test_read_token_refused_on_follows_tool_names_scope(self):
        with pytest.raises(ScopeError) as exc:
            require_scope(_remote({"read"}), "add_podcast")
        assert exc.value.required_scope == "follows"
        assert "'follows'" in str(exc.value)

    def test_follows_token_can_add(self):
        require_scope(_remote({"read", "follows"}), "add_podcast")

    def test_pipeline_requires_admin_now(self):
        require_scope(_remote({"read", "pipeline"}, is_admin=True), "refresh_feeds")
        with pytest.raises(ScopeError):
            require_scope(_remote({"read", "pipeline"}, is_admin=False), "refresh_feeds")

    def test_unregistered_tool_fails_closed(self):
        with pytest.raises(ScopeError) as exc:
            require_scope(_remote({"read", "follows", "pipeline"}, is_admin=True), "brand_new_tool")
        assert exc.value.required_scope is None


class TestVisibleTools:
    TOOLS = [
        Tool(name=n, description="", inputSchema={"type": "object"})
        for n in ("list_podcasts", "add_podcast", "refresh_feeds", "unregistered")
    ]

    def test_stdio_sees_everything(self):
        assert visible_tools(STDIO, self.TOOLS) == self.TOOLS

    def test_read_only(self):
        assert [t.name for t in visible_tools(_remote({"read"}), self.TOOLS)] == ["list_podcasts"]

    def test_admin_with_pipeline(self):
        names = [t.name for t in visible_tools(_remote({"read", "follows", "pipeline"}, is_admin=True), self.TOOLS)]
        assert names == ["list_podcasts", "add_podcast", "refresh_feeds"]

    def test_demoted_admin_loses_pipeline_without_rotate(self):
        names = [t.name for t in visible_tools(_remote({"read", "pipeline"}, is_admin=False), self.TOOLS)]
        assert names == ["list_podcasts"]


class TestCurrentIdentity:
    def _server(self, request):
        return SimpleNamespace(request_context=SimpleNamespace(request=request))

    def test_no_request_is_stdio(self):
        assert current_mcp_identity(self._server(None)) is STDIO

    def test_outside_request_context_is_stdio(self):
        class Boom:
            @property
            def request_context(self):
                raise LookupError

        assert current_mcp_identity(Boom()) is STDIO

    def test_reads_state(self):
        user = User(id="u1", email="u@example.com")
        request = SimpleNamespace(
            scope={"state": {STATE_USER: user, STATE_IS_ADMIN: True, STATE_SCOPES: ["read", "pipeline"]}}
        )
        identity = current_mcp_identity(self._server(request))
        assert identity.user is user and identity.is_admin and identity.scopes == {"read", "pipeline"}
        assert identity.is_remote

    def test_state_without_user_is_stdio(self):
        request = SimpleNamespace(scope={"state": {}})
        assert current_mcp_identity(self._server(request)) is STDIO
