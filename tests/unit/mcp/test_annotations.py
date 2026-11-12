"""Spec #109 — every MCP tool carries a title and explicit behaviour hints."""

from __future__ import annotations

from mcp.types import Tool

from tests.docs.helpers import extract_mcp_tool_names
from thestill.mcp.annotations import annotate, annotated_tool_names, hints_for_tool
from thestill.mcp.scopes import scope_for_tool


def test_every_registered_tool_has_hints():
    """The registry names every ``Tool(name=...)`` the MCP modules declare,
    and nothing else, so a new tool cannot be listed without hints."""
    assert extract_mcp_tool_names() == set(annotated_tool_names())


def test_titles_are_present_and_distinct():
    titles = [hints_for_tool(name).title for name in annotated_tool_names()]
    assert all(title.strip() for title in titles)
    assert len(set(titles)) == len(titles)


def test_hints_agree_with_scopes():
    """A ``read`` tool is read-only and every other tool is not: the scope a
    token needs and the hint a client shows must tell the same story."""
    for name in annotated_tool_names():
        hints = hints_for_tool(name)
        assert hints.read_only == (scope_for_tool(name) == "read"), name
        if hints.read_only:
            assert not hints.destructive, name


def test_only_unfollowing_is_destructive():
    destructive = {name for name in annotated_tool_names() if hints_for_tool(name).destructive}
    assert destructive == {"remove_podcast"}


def test_annotate_sets_title_and_both_hints_on_the_wire():
    tools = annotate(
        [
            Tool(name="get_summary", description="d", input_schema={"type": "object"}),
            Tool(name="remove_podcast", description="d", input_schema={"type": "object"}),
        ]
    )
    wire = [t.model_dump(by_alias=True, exclude_none=True) for t in tools]
    assert wire[0]["title"] == "Get an episode summary"
    assert wire[0]["annotations"] == {"readOnlyHint": True, "destructiveHint": False}
    assert wire[1]["annotations"] == {"readOnlyHint": False, "destructiveHint": True}
    # The definition itself is untouched.
    assert wire[0]["description"] == "d"
    assert wire[0]["inputSchema"] == {"type": "object"}


def test_annotate_leaves_an_unknown_tool_alone():
    tool = Tool(name="definitely_not_a_tool", description="d", input_schema={"type": "object"})
    assert annotate([tool]) == [tool]
