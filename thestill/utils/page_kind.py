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

"""What kind of public page a path is (spec #95).

One label for a path, used for ``users.signup_source`` now and for the
PostHog ``from_kind`` / ``next_kind`` / ``kind`` props in Phase 1. It
mirrors the spec #94 public routes (``PUBLIC_PATH`` in the frontend's
``utils/signIn.ts`` and ``ShellRenderer.head_for``) but only labels: it
never validates a slug, never looks anything up and never raises, because
the OAuth callback that calls it turns any exception into a failed login.
"""

from typing import Final, Literal, Optional

PublicPageKind = Literal["landing", "top", "podcast", "episode", "entity", "share", "legal"]

# What ``users.signup_source`` may hold. Legal pages and every non-public
# path collapse to "direct".
SIGNUP_SOURCES: Final = ("landing", "top", "podcast", "episode", "entity", "share", "direct")

_ENTITY_TYPES: Final = frozenset({"person", "company", "product", "topic"})


def public_page_kind(path: Optional[str]) -> Optional[PublicPageKind]:
    """The public page kind of ``path``, or None for anything else.

    ``path`` is a same-origin path such as ``/podcasts/x?ref=y``; query and
    fragment are ignored. Total: garbage in gives None, never an exception.
    """
    try:
        if not isinstance(path, str) or not path.startswith("/"):
            return None
        parts = path.split("?", 1)[0].split("#", 1)[0].strip("/").split("/")
        if parts == [""]:
            return "landing"
        if any(not p for p in parts):
            return None
        head, n = parts[0], len(parts)
        if head == "top" and n == 1:
            return "top"
        if head in ("terms", "privacy") and n == 1:
            return "legal"
        if head == "share":
            return "share"
        if head == "podcasts" and n == 2:
            return "podcast"
        if head == "podcasts" and n == 4 and parts[2] == "episodes":
            return "episode"
        if head == "entities" and n == 3 and parts[1] in _ENTITY_TYPES:
            return "entity"
        return None
    except Exception:  # noqa: BLE001 - total by contract; a label is never worth a failed login
        return None


def signup_source_for(next_path: Optional[str]) -> str:
    """``users.signup_source`` for a sign-in that returned to ``next_path``."""
    kind = public_page_kind(next_path)
    return kind if kind in SIGNUP_SOURCES else "direct"
