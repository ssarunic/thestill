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
"""Tool → title and behaviour hints (spec #109, "Tool annotations").

Every tool carries a human title and explicit ``readOnlyHint`` /
``destructiveHint`` values. Clients use them to decide which calls may
run without asking and which always prompt, and Claude's connector
directory refuses a server whose tools lack them.

One registry, next to the scope registry, so the tool definitions stay
untouched and a new tool cannot ship without hints:
``tests/unit/mcp/test_annotations.py`` asserts every declared tool has an
entry and that the hints agree with its scope.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Dict, FrozenSet, List

from mcp.types import Tool, ToolAnnotations


@dataclass(frozen=True)
class ToolHints:
    title: str
    read_only: bool
    # Meaningful only when ``read_only`` is false: the call removes or
    # overwrites something rather than only adding.
    destructive: bool = False


def _read(title: str) -> ToolHints:
    return ToolHints(title=title, read_only=True)


def _write(title: str, *, destructive: bool = False) -> ToolHints:
    return ToolHints(title=title, read_only=False, destructive=destructive)


_HINTS_BY_TOOL: Dict[str, ToolHints] = {
    # read
    "list_podcasts": _read("List followed podcasts"),
    "list_episodes": _read("List a podcast's episodes"),
    "get_status": _read("Get library status"),
    "get_transcript": _read("Get an episode transcript"),
    "get_summary": _read("Get an episode summary"),
    "get_episode_clip": _read("Get an episode clip"),
    "search_corpus": _read("Search transcripts"),
    "find_mentions": _read("Find mentions of a person, company or topic"),
    "list_quotes_by": _read("List quotes by a speaker"),
    "get_entity": _read("Get a person, company or topic"),
    "list_episodes_by_entity": _read("List episodes mentioning a person, company or topic"),
    "get_analytics": _read("Get product analytics"),
    "list_inbox": _read("List inbox episodes"),
    # follows — each adds something, except remove_podcast, which unfollows.
    "add_podcast": _write("Follow a podcast"),
    "remove_podcast": _write("Unfollow a podcast", destructive=True),
    "import_episode": _write("Import an episode"),
    "send_to_inbox": _write("Send an episode to the inbox"),
    # pipeline — operator actions; they queue or run work, removing nothing.
    "refresh_feeds": _write("Refresh podcast feeds"),
    "download_episodes": _write("Download episode audio"),
    "downsample_audio": _write("Downsample episode audio"),
    "transcribe_episodes": _write("Transcribe episodes"),
    "clean_transcripts": _write("Clean transcripts"),
    "process_episode": _write("Process an episode"),
    "summarize_episodes": _write("Summarise episodes"),
}


def hints_for_tool(name: str) -> ToolHints | None:
    return _HINTS_BY_TOOL.get(name)


def annotated_tool_names() -> FrozenSet[str]:
    return frozenset(_HINTS_BY_TOOL)


def annotate(tools: List[Tool]) -> List[Tool]:
    """Copies of ``tools`` with their title and hints set.

    A tool with no registry entry is returned unchanged; the contract test
    is what keeps that from happening.
    """
    annotated: List[Tool] = []
    for tool in tools:
        hints = _HINTS_BY_TOOL.get(tool.name)
        if hints is None:
            annotated.append(tool)
            continue
        annotated.append(
            tool.model_copy(
                update={
                    "title": hints.title,
                    "annotations": ToolAnnotations(
                        read_only_hint=hints.read_only,
                        destructive_hint=hints.destructive,
                    ),
                }
            )
        )
    return annotated
