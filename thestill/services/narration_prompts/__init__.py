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

"""Anchor system prompts for the narrated digest (spec #33).

Each anchor voice lives as its own Markdown file in this directory so
prompts can be diffed, shipped, and overridden per environment without
code changes (spec #33 §"Anchor Voice & Prompts" / Open Question O5).
"""

from pathlib import Path

PROMPTS_DIR = Path(__file__).parent
DEFAULT_ANCHOR_PROMPT_FILE = PROMPTS_DIR / "default_anchor.md"


def load_default_anchor_prompt() -> str:
    """Load the default anchor system prompt from disk.

    Reads on every call so an operator can edit the file and re-run
    ``thestill narrate`` without restarting a long-running process. The
    file is small (well under 4 KB), so the unconditional read is fine.
    """
    return DEFAULT_ANCHOR_PROMPT_FILE.read_text(encoding="utf-8")
