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

"""Shared category id <-> (top, sub) cache for the Postgres mixins.

Both ``PodcastsMixin`` and ``EpisodesMixin`` need the same two maps over the
``categories`` table. They grew parallel implementations while being ported
in tandem (the cleanup note in each module docstring), and spec #69 Phase 8.3
then added memoization to only one of them — leaving two independent caches
of one read-only table on the same composed instance.

This is that consolidation: one implementation, one cache, inherited by both.
Each mixin must be independently usable (the repository contract suites
compose ``EpisodesMixin`` on its own), so the cache lives here rather than in
either sibling.

The taxonomy is small (~100-200 rows) and effectively read-only at runtime.
Unlike the SQLite repo — which loads it in ``__init__`` right after seeding —
the mixins have no init hook, so the cache fills lazily on first use.
"""

from __future__ import annotations

from typing import Dict, Optional, Tuple

import psycopg

from ..utils.podcast_categories import normalize_category_name

# ``(top_norm, sub_norm | None) -> category id`` and its inverse, which holds
# the *display* names rather than the normalized keys.
PairToId = Dict[Tuple[str, Optional[str]], int]
IdToPair = Dict[int, Tuple[Optional[str], Optional[str]]]


class CategoryCacheMixin:
    """Lazy per-instance cache of the ``categories`` taxonomy."""

    def _ensure_category_cache(self, conn: psycopg.Connection) -> None:
        """Load the categories table into per-instance dicts on first use.

        An empty table yields empty caches — every lookup then resolves to
        ``None`` gracefully rather than raising.
        """
        if getattr(self, "_cat_cache_loaded", False):
            return
        # Top-level rows first, so ``top_id_to_name`` is populated before any
        # subcategory that references it is processed in the same pass.
        rows = conn.execute("SELECT id, name, parent_id FROM categories ORDER BY parent_id IS NOT NULL, id").fetchall()
        cat_id_to_pair: IdToPair = {}
        cat_pair_to_id: PairToId = {}
        top_id_to_name: Dict[int, str] = {}
        for row in rows:
            if row["parent_id"] is None:
                top_id_to_name[row["id"]] = row["name"]
                cat_id_to_pair[row["id"]] = (row["name"], None)
                cat_pair_to_id[(normalize_category_name(row["name"]), None)] = row["id"]
            else:
                top_name = top_id_to_name.get(row["parent_id"])
                if top_name is None:
                    continue  # orphan subcategory — defensive, FK should prevent
                cat_id_to_pair[row["id"]] = (top_name, row["name"])
                cat_pair_to_id[(normalize_category_name(top_name), normalize_category_name(row["name"]))] = row["id"]
        self._cat_id_to_pair = cat_id_to_pair
        self._cat_pair_to_id = cat_pair_to_id
        self._cat_cache_loaded = True

    def _category_maps(self, conn: psycopg.Connection) -> Tuple[PairToId, IdToPair]:
        """Return ``(pair→id, id→pair)``, filling the cache on first use.

        Keys of ``pair→id`` are normalized via ``normalize_category_name``;
        top-level rows are stored under ``(top_norm, None)``.
        """
        self._ensure_category_cache(conn)
        return self._cat_pair_to_id, self._cat_id_to_pair
