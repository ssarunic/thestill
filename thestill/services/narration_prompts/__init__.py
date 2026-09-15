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

"""Anchor system prompts for the narrated briefing (spec #33, #77).

Each anchor voice lives as its own Markdown file in this directory so
prompts can be diffed, shipped, and overridden per environment without
code changes (spec #33 O5). ``NARRATION_ANCHOR_PROMPT`` picks the file
by basename (spec #77 §7):

- ``conversational_anchor`` — one narrator talking to a friend (default)
- ``conversational_v2`` — the same narrator with a point of view: one claim
  per show, a ``reaction`` block after every clip, honest transitions
  (spec #77 Phase 2b; becomes the default once the A/B gate passes)
- ``newsroom_anchor`` — the original measured news-anchor voice

A voice file may contain ``{{noise_phrases}}``; the loader replaces it
with the list in :mod:`.noise_phrases` so the instruction the model sees
and the lint the writer runs never drift apart.
"""

import re
from pathlib import Path
from typing import List

from .noise_phrases import NOISE_PHRASES, count_noise_phrase_hits, find_noise_phrases, render_noise_phrases

PROMPTS_DIR = Path(__file__).parent
DEFAULT_ANCHOR_PROMPT_NAME = "conversational_anchor"
NOISE_PHRASES_PLACEHOLDER = "{{noise_phrases}}"

# Basename only: no separators, no dots, so a config value can never
# resolve outside this directory (defence-in-depth alongside the
# resolved-path check below).
_PROMPT_NAME_RE = re.compile(r"^[a-z0-9_]+$")

__all__ = [
    "DEFAULT_ANCHOR_PROMPT_NAME",
    "NOISE_PHRASES",
    "PROMPTS_DIR",
    "available_anchor_prompts",
    "count_noise_phrase_hits",
    "find_noise_phrases",
    "load_anchor_prompt",
    "load_default_anchor_prompt",
]


def available_anchor_prompts() -> List[str]:
    """Basenames of the voice files shipped in this directory."""
    return sorted(p.stem for p in PROMPTS_DIR.glob("*.md"))


def load_anchor_prompt(name: str) -> str:
    """Load the anchor prompt ``<name>.md`` with placeholders filled in.

    Reads on every call so an operator can edit the file and re-run
    ``thestill narrate`` without restarting a long-running process. The
    files are small (well under 8 KB), so the unconditional read is fine.

    Raises ``ValueError`` for a name that is not a plain basename, that
    resolves outside the prompts directory, or that does not exist —
    callers at the config boundary turn that into a startup error rather
    than narrating with the wrong voice silently.
    """
    if not _PROMPT_NAME_RE.match(name or ""):
        raise ValueError(
            f"anchor prompt name {name!r} must be a basename like 'conversational_anchor'"
            f" (available: {', '.join(available_anchor_prompts())})"
        )
    path = (PROMPTS_DIR / f"{name}.md").resolve()
    if PROMPTS_DIR.resolve() not in path.parents:
        raise ValueError(f"anchor prompt {name!r} resolves outside the prompts directory")
    if not path.is_file():
        raise ValueError(f"anchor prompt {name!r} not found (available: {', '.join(available_anchor_prompts())})")
    text = path.read_text(encoding="utf-8")
    return text.replace(NOISE_PHRASES_PLACEHOLDER, render_noise_phrases())


def load_default_anchor_prompt() -> str:
    """The default voice (spec #77: conversational)."""
    return load_anchor_prompt(DEFAULT_ANCHOR_PROMPT_NAME)
