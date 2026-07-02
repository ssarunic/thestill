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

"""Sanitize LLM-produced text before it is persisted.

LLMs occasionally emit raw control characters — the incident that motivated
this module was Gemini's clean stage returning ``saut\\u0000 onions`` for
"sauté onions" (U+0000 where the ``é`` belongs). SQLite stores an embedded NUL
silently, so the corruption propagated invisibly into ``chunks.text`` and
``entity_mentions.quote_excerpt`` until the Postgres migration (which forbids
NUL in ``text``) surfaced it.

``sanitize_text`` strips the C0/C1 control ranges EXCEPT the whitespace we
legitimately persist: tab (U+0009), newline (U+000A), and carriage return
(U+000D). Everything printable — accents, CJK, emoji, quotes, markdown
syntax — passes through untouched. Callers that need observability use the
returned count to log how many characters were removed (never strip silently:
spec #42 FM-4).
"""

from __future__ import annotations

import re

# C0 controls minus \t \n \r, plus DEL and the C1 range. As *codepoints* —
# this is unicode-aware (U+0085 NEL etc.), not byte munging.
_CONTROL_CHARS_RE = re.compile(r"[\x00-\x08\x0b\x0c\x0e-\x1f\x7f-\x9f]")


def sanitize_text(text: str) -> tuple[str, int]:
    """Strip disallowed control characters from ``text``.

    Returns ``(clean_text, removed_count)``. ``removed_count`` is 0 for the
    overwhelmingly common clean case, so callers can gate logging on it.
    """
    clean, count = _CONTROL_CHARS_RE.subn("", text)
    return clean, count
