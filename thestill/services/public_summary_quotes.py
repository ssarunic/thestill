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

"""An episode's public quotes (spec #120): the summary's Best Quotes that
are in the transcript and said by the person the summary names, each with
its short link (spec #114).

One function for every surface that shows or shares them: the public
episode page (and its server-written copy) and the signed-in transcript's
share icons, which may offer only quotes that are already public (spec
#114 Rule A).
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import TYPE_CHECKING, Any, Dict, List, Optional

from structlog import get_logger

from ..core.summary_quotes import SpeakerRef, VerifiedQuote, parse_best_quotes, verify_quotes
from ..utils.language_config import normalize_language_code

if TYPE_CHECKING:
    from ..models.podcast import Episode, Podcast
    from ..repositories.entity_repository import EntityRepository
    from .podcast_service import PodcastService
    from .quote_share_service import QuoteShareService

logger = get_logger(__name__)


@dataclass(frozen=True)
class PublicSummaryQuote:
    quote: VerifiedQuote
    #: The short link id, or None when the quote may not become a card
    #: (spec #114 §1), in read-only mode, or without the share service.
    share_id: Optional[str]


def verified_summary_quotes(
    podcast_service: "PodcastService", entity_repository: "EntityRepository", podcast: "Podcast", episode: "Episode"
) -> List[VerifiedQuote]:
    """The summary's Best Quotes that pass both checks, in its order.

    Read from the canonical summary only: a translation's quotes are not
    the words that were said. Any missing piece (summary, transcript,
    citations) yields no quotes rather than unchecked ones.
    """
    canonical = normalize_language_code(
        podcast_service.get_recorded_summary_language(episode), default=normalize_language_code(podcast.language)
    )
    summary = podcast_service.get_summary_for_episode(episode, language=canonical, canonical_language=canonical)
    if not summary or summary.startswith("N/A"):
        return []
    quotes = parse_best_quotes(summary)
    if not quotes:
        return []
    segmented = podcast_service.get_segmented_transcript_for_episode(episode)
    if segmented is None:
        return []
    citations = podcast_service.get_summary_citations_for_episode(
        episode, summary, language=canonical, canonical_language=canonical, segmented=segmented
    )
    speakers = {
        int(row["segment_id"]): SpeakerRef(str(row["speaker_id"]), row["speaker_name"])
        for row in entity_repository.public_quote_queries().segment_speakers(str(episode.id))
    }
    kept, dropped = verify_quotes(
        quotes,
        citations={citation["id"]: citation for citation in citations or []},
        segments=segmented.annotated.segments,
        speakers=speakers,
        playback_offset_s=segmented.annotated.playback_time_offset_seconds or 0.0,
    )
    if dropped:
        reasons: Dict[str, int] = {}
        for rejection in dropped:
            reasons[rejection.reason] = reasons.get(rejection.reason, 0) + 1
        logger.info("public_summary_quotes_dropped", episode_id=str(episode.id), kept=len(kept), **reasons)
    return kept


def episode_summary_quotes(
    podcast_service: "PodcastService",
    entity_repository: "EntityRepository",
    quote_share_service: Optional["QuoteShareService"],
    podcast: "Podcast",
    episode: "Episode",
) -> List[PublicSummaryQuote]:
    """The episode's public quotes with their short link ids. The caller
    has checked that the episode is public.

    A failure while checking (an unreadable sidecar or transcript, a query
    error) shows no quotes rather than failing the page's other blocks
    built with them, such as who takes part; it is logged with the episode.
    """
    try:
        quotes = verified_summary_quotes(podcast_service, entity_repository, podcast, episode)
    except Exception:  # pylint: disable=broad-except
        logger.exception("public_summary_quotes_failed", episode_id=str(episode.id))
        return []
    rows: List[Dict[str, Any]] = [
        {
            "mention_id": index,
            "quote": quote.text,
            "episode_id": str(episode.id),
            "podcast_id": str(podcast.id),
            "segment_id": quote.segment_id,
            "start_ms": quote.start_ms,
            "turn_end_ms": quote.end_ms,
            "speaker_id": quote.speaker.entity_id,
            "source": "summary",
        }
        for index, quote in enumerate(quotes)
    ]
    ids = quote_share_service.mint(rows) if quote_share_service is not None and rows else {}
    return [PublicSummaryQuote(quote, ids.get(index)) for index, quote in enumerate(quotes)]
