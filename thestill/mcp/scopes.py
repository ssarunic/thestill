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
"""Tool → scope registry for remote MCP tokens (spec #78 Phase 2).

Fails closed: a tool with no entry here is omitted from ``tools/list``
and refused on ``tools/call`` over HTTP. ``tests/unit/mcp/test_scopes.py``
asserts every registered tool has an entry so a new tool cannot become
remotely reachable by omission. stdio is unscoped and never consults
this module.
"""

from __future__ import annotations

from typing import Dict, FrozenSet, Optional

from ..models.mcp_token import SCOPE_FOLLOWS, SCOPE_PIPELINE, SCOPE_READ

_SCOPE_BY_TOOL: Dict[str, str] = {
    # read — everything a signed-in web user can see
    "list_podcasts": SCOPE_READ,
    "list_episodes": SCOPE_READ,
    "get_status": SCOPE_READ,
    "get_transcript": SCOPE_READ,
    "get_summary": SCOPE_READ,
    "get_episode_clip": SCOPE_READ,
    "search_corpus": SCOPE_READ,
    "find_mentions": SCOPE_READ,
    "list_quotes_by": SCOPE_READ,
    "get_entity": SCOPE_READ,
    "list_episodes_by_entity": SCOPE_READ,
    # follows — add_podcast lives here, not read: adding a feed enqueues
    # download + transcription + summarisation, so it spends money.
    "add_podcast": SCOPE_FOLLOWS,
    "remove_podcast": SCOPE_FOLLOWS,
    # pipeline — operator actions, admins only
    "refresh_feeds": SCOPE_PIPELINE,
    "download_episodes": SCOPE_PIPELINE,
    "downsample_audio": SCOPE_PIPELINE,
    "transcribe_episodes": SCOPE_PIPELINE,
    "clean_transcripts": SCOPE_PIPELINE,
    "process_episode": SCOPE_PIPELINE,
    "summarize_episodes": SCOPE_PIPELINE,
}


def scope_for_tool(name: str) -> Optional[str]:
    """The scope a tool requires, or None (⇒ fail closed over HTTP)."""
    return _SCOPE_BY_TOOL.get(name)


def registered_tools() -> FrozenSet[str]:
    return frozenset(_SCOPE_BY_TOOL)


def effective_scopes(granted: FrozenSet[str], *, is_admin: bool) -> FrozenSet[str]:
    """The per-request re-check: ``pipeline`` counts only while the user is
    an admin *now*, whatever was minted. Demotion bites on the next call."""
    if is_admin:
        return granted
    return granted - {SCOPE_PIPELINE}
