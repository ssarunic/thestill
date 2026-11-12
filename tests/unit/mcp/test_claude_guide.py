"""Spec #108 — the /claude guide never promises a tool the connector lacks.

The guide's tool tables are the public documentation of the connector, so
they must name exactly the tools a non-admin reader can be granted: every
``read`` and ``follows`` tool, except the admin-only analytics tool.
"""

from __future__ import annotations

import re
from pathlib import Path

from thestill.mcp.annotations import hints_for_tool
from thestill.mcp.scopes import registered_tools, scope_for_tool

GUIDE = Path(__file__).parents[3] / "thestill" / "web" / "frontend" / "public" / "help" / "claude.md"
_ADMIN_ONLY = {"get_analytics"}
_ROW = re.compile(r"^\| `([a-z_]+)` \|", re.M)


def _section(text: str, start: str, end: str) -> str:
    return text.split(start, 1)[1].split(end, 1)[0]


def test_guide_lists_exactly_the_reader_tools():
    text = GUIDE.read_text(encoding="utf-8")
    reader_tools = {
        name for name in registered_tools() if scope_for_tool(name) in ("read", "follows") and name not in _ADMIN_ONLY
    }
    assert set(_ROW.findall(text)) == reader_tools


def test_guide_files_each_tool_under_the_right_heading():
    text = GUIDE.read_text(encoding="utf-8")
    read_rows = _ROW.findall(_section(text, "Read tools.", "Write tools."))
    write_rows = _ROW.findall(_section(text, "Write tools.", "## Disconnect"))
    assert read_rows and write_rows
    assert all(hints_for_tool(name).read_only for name in read_rows)
    assert not any(hints_for_tool(name).read_only for name in write_rows)
