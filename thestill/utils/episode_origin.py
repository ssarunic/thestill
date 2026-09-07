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

"""
Listener-facing provenance of an episode (spec #76 §3.6 "Source" row).

An imported episode (spec #31) carries a ``canonical_id`` whose prefix names
the resolver that minted it. Nothing else on the episode row records how it
arrived, so this is the one place that turns the prefix into the
``origin`` / ``import_kind`` pair the episode detail response exposes.
"""

from typing import Literal, Optional, Tuple

EpisodeOrigin = Literal["feed", "import"]

# Prefix before the first ``:`` of a canonical id → the ``ImportKind`` the
# frontend already knows (``CanonicalSource.kind`` in ``import_service``).
_PREFIX_TO_IMPORT_KIND = {
    "audio": "bare_audio",
    "youtube": "youtube",
    "apple": "apple_episode",
    "rss": "rss_episode",
}


def derive_episode_origin(canonical_id: Optional[str]) -> Tuple[EpisodeOrigin, Optional[str]]:
    """
    Map an episode's ``canonical_id`` to ``(origin, import_kind)``.

    - ``None`` / empty → ``("feed", None)``: discovered from a followed feed.
    - Known prefix → ``("import", <kind>)``.
    - Unknown prefix → ``("import", None)``: still an import, but the kind is
      not one the UI labels. Never raises — a future resolver must not 500
      the episode page.
    """
    if not canonical_id:
        return "feed", None
    prefix, _, _ = canonical_id.partition(":")
    return "import", _PREFIX_TO_IMPORT_KIND.get(prefix)
