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

"""LIKE / ILIKE pattern escaping (spec #85).

``%`` and ``_`` are wildcards inside a SQL ``LIKE`` pattern, so a user
searching for ``50%`` would otherwise match every row containing ``50``.
This is the first place in the codebase that escapes them; the older
podcast / episode / entity searches still interpolate raw text and inherit
the wildcard quirk (a known, out-of-scope gap).

The helper only builds the bind value. The SQL text must pair it with
``ESCAPE '\\'`` (a single backslash once Python string escaping is
applied), which both SQLite and Postgres accept.
"""

LIKE_ESCAPE_CHAR = "\\"

# The SQL fragment to append after the pattern placeholder. Written once so
# both dialect-specific repositories emit exactly the same text.
LIKE_ESCAPE_CLAUSE = "ESCAPE '\\'"


def escape_like_wildcards(value: str, *, escape_char: str = LIKE_ESCAPE_CHAR) -> str:
    """Escape ``escape_char``, ``%`` and ``_`` so ``value`` matches literally.

    The caller still wraps the result in the wildcards it wants
    (``f"%{escaped}%"`` for a substring match). The escape character is
    doubled first: doing ``%`` / ``_`` first would then double-escape the
    backslashes those steps introduce.
    """
    return value.replace(escape_char, escape_char * 2).replace("%", f"{escape_char}%").replace("_", f"{escape_char}_")


def substring_pattern(value: str) -> str:
    """``%<escaped value>%`` — a case-preserving substring LIKE pattern."""
    return f"%{escape_like_wildcards(value)}%"
