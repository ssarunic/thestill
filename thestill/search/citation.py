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
the desktop app scheme. The page and audio links come from
``episode_links``, the same builder the search citations use.
"""

from __future__ import annotations

from typing import TYPE_CHECKING, Iterable, List, Sequence

from ..models.entities import CitationRow, MatchType
from ..repositories.sqlite_entity_repository import MentionContext
from ..utils.url_generator import episode_links
from .base import ResolvedHit

if TYPE_CHECKING:
    from ..repositories.podcast_repository import PodcastRepository


def build_citation_row(ctx: MentionContext, *, score: float = 1.0, base_url: str = "") -> CitationRow:
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
        **episode_links(
            base_url=base_url,
            podcast_slug=ctx.podcast_slug,
            episode_slug=ctx.episode_slug,
            audio_url=ctx.episode_audio_url,
            start_ms=ctx.mention.start_ms,
        ),
    )


def build_citation_rows(contexts: Iterable[MentionContext], *, base_url: str = "") -> List[CitationRow]:
    """Convenience: bulk-convert a list."""
    return [build_citation_row(c, base_url=base_url) for c in contexts]


def search_citations(hits: Sequence[ResolvedHit], *, repository: "PodcastRepository", base_url: str = "") -> List[dict]:
    """``as_citation`` for each search hit, with the page and audio links.

    Hits carry ids, not slugs; one ``get_episodes_by_ids`` round-trip
    fills them in. An episode the lookup misses keeps ``None`` links.
    """
    episodes = repository.get_episodes_by_ids(list(dict.fromkeys(h.episode_id for h in hits))) if hits else {}
    out: List[dict] = []
    for hit in hits:
        podcast, episode = episodes.get(hit.episode_id, (None, None))
        out.append(
            hit.as_citation(
                base_url=base_url,
                podcast_slug=podcast.slug if podcast else None,
                episode_slug=episode.slug if episode else None,
                audio_url=str(episode.audio_url) if episode else None,
            )
        )
    return out
