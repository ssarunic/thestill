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

"""Curation writes are reachable only through the audited services.

The CLI (``thestill/cli.py``) and the web routes must not call these
repository writes directly: ``EntityCurationService`` and
``PodcastCurationService`` make them and record the audit row, so a new
command or endpoint cannot forget the audit. Add a write here when a new
curation primitive appears.
"""

from __future__ import annotations

import ast
from pathlib import Path

import pytest

REPO = Path(__file__).resolve().parents[2]

# Repository methods that change curated data.
CURATION_WRITES = frozenset(
    {
        "add_override",
        "add_blacklist_entry",
        "set_entity_public_hidden",
        "set_podcast_hosts",
        "set_podcast_recurring",
        "set_episode_guests",
        "set_podcast_platform_url",
        "set_podcast_summary_profile",
        "set_podcast_public_pages",
        "set_entity_wikidata",
        "replace_aliases",
        "repoint_entity_references",
        # ``move_mentions`` is left out: the service method of the same name
        # is what the entry points call.
    }
)

# Module functions that write curated data without auditing.
CURATION_FUNCTIONS = frozenset({"apply_correction"})

ENTRY_POINTS = [REPO / "thestill" / "cli.py", *sorted((REPO / "thestill" / "web" / "routes").glob("*.py"))]


def _direct_writes(path: Path) -> list[str]:
    tree = ast.parse(path.read_text(encoding="utf-8"))
    hits = []
    for node in ast.walk(tree):
        if not isinstance(node, ast.Call):
            continue
        if isinstance(node.func, ast.Attribute) and node.func.attr in CURATION_WRITES:
            hits.append(f"{path.relative_to(REPO)}:{node.lineno} .{node.func.attr}()")
        elif isinstance(node.func, ast.Name) and node.func.id in CURATION_FUNCTIONS:
            hits.append(f"{path.relative_to(REPO)}:{node.lineno} {node.func.id}()")
    return hits


@pytest.mark.parametrize("path", ENTRY_POINTS, ids=lambda p: p.name)
def test_entry_point_makes_no_direct_curation_write(path):
    assert _direct_writes(path) == [], (
        "Curation writes must go through EntityCurationService / PodcastCurationService, " "which record the audit row."
    )
