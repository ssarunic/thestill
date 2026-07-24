"""docs/mcp-usage.md stays in sync with the registered MCP tools.

Both directions: every Tool(name=...) in thestill/mcp/ is documented,
and every tool name the doc presents as current exists in code.
"""

import re

from tests.docs.helpers import DOCS_DIR, extract_mcp_tool_names, read_doc

MCP_USAGE_MD = DOCS_DIR / "mcp-usage.md"


def test_all_mcp_tools_documented():
    tool_names = extract_mcp_tool_names()
    assert len(tool_names) > 15, "MCP tool extraction looks broken (too few tools)"
    text = read_doc(MCP_USAGE_MD)
    missing = [name for name in sorted(tool_names) if name not in text]
    assert not missing, f"MCP tools registered in code but absent from mcp-usage.md: {missing}"


def test_documented_tool_headings_exist_in_code():
    """Tool names introduced by the doc's per-tool headings (e.g.
    ``### 3. add_podcast`` or ``#### search_corpus``) must exist."""
    tool_names = extract_mcp_tool_names()
    ghosts = []
    for line in read_doc(MCP_USAGE_MD).splitlines():
        match = re.match(r"^#{3,4}\s+(?:\d+\.\s+)?`?([a-z][a-z0-9_]+)`?\s*$", line)
        if match and "_" in match.group(1) and match.group(1) not in tool_names:
            ghosts.append(match.group(1))
    assert not ghosts, f"mcp-usage.md documents tools that are not registered in code: {ghosts}"
