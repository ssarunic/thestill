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

"""Spec #28 §4 ("Citation-shaped results") — assemble ``CitationRow``s
from the repository's ``MentionContext`` rows.

The deeplink format is ``thestill://episode/<id>?t=<seconds>`` —
the desktop app scheme. The ``web_url`` is the corresponding web
path (``/episodes/<id>?t=<seconds>``) that the React client uses for
in-browser navigation.
"""

from __future__ import annotations

from typing import Iterable, List

from ..models.entities import CitationRow, MatchType
from ..repositories.sqlite_entity_repository import MentionContext


def build_citation_row(ctx: MentionContext, *, score: float = 1.0) -> CitationRow:
    """Convert one ``MentionContext`` to its wire-shape ``CitationRow``.

    ``match_type`` is hard-coded to ``entity`` — these rows always
    come from entity-scoped queries (``find_mentions``, ``list_quotes_by``,
    ``get_episode_clip``). ``score`` defaults to 1.0 because entity
    matches are exact (the resolver already disambiguated against
    Wikidata); callers can override when ranking by recency or
    cooccurrence count.
    """
    start_seconds = ctx.mention.start_ms // 1000
    return CitationRow(
        episode_id=ctx.episode_id,
        podcast_id=ctx.podcast_id,
        podcast_title=ctx.podcast_title,
        episode_title=ctx.episode_title,
        published_at=ctx.episode_pub_date,
        start_ms=ctx.mention.start_ms,
        end_ms=ctx.mention.end_ms,
        speaker=ctx.mention.speaker,
        quote=ctx.mention.quote_excerpt,
        score=score,
        match_type=MatchType.ENTITY,
        deeplink=f"thestill://episode/{ctx.episode_id}?t={start_seconds}",
        web_url=f"/episodes/{ctx.episode_id}?t={start_seconds}",
    )


def build_citation_rows(contexts: Iterable[MentionContext]) -> List[CitationRow]:
    """Convenience: bulk-convert a list."""
    return [build_citation_row(c) for c in contexts]
