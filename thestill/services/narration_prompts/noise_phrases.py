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

"""Noise phrases the anchor must never use (spec #77 §3).

Single source of truth: the prompt loader substitutes ``{{noise_phrases}}``
in a voice file with this list, and the script writer counts hits in the
generated narration as a register metric. Keeping the list here, next to
the prompts, means the instruction and the lint cannot drift.

Hand-tuned against gemini-3-flash-preview; other providers may need
different entries.
"""

import re
from typing import Tuple

NOISE_PHRASES: Tuple[str, ...] = (
    "the conversation is moving",
    "the landscape",
    "underscores",
    "highlights",
    "delves",
    "it's worth noting",
    "in a world where",
    "this sentiment was echoed",
    "serves as a cautionary tale",
    "the intersection of",
    "a reality check",
    "the next frontier",
    "a paradigm",
    "navigate",
    "unpack",
    "at the end of the day",
    "a deep dive",
    "notably",
    "arguably",
    "a stark reminder",
)

_WORD_RE = re.compile(r"[^\w\s]+", re.UNICODE)


def render_noise_phrases() -> str:
    """The list as it is spliced into a prompt: quoted, comma-separated."""
    return ", ".join(f'"{p}"' for p in NOISE_PHRASES)


def count_noise_phrase_hits(text: str) -> int:
    """Occurrences of any noise phrase in ``text`` (case- and punctuation-insensitive)."""
    if not text:
        return 0
    haystack = " ".join(_WORD_RE.sub(" ", text.lower()).split())
    return sum(haystack.count(" ".join(_WORD_RE.sub(" ", p.lower()).split())) for p in NOISE_PHRASES)
