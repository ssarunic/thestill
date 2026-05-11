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

"""Word-level transcript timestamps for the karaoke wipe feature (spec #38).

Mounted at ``/api/podcasts`` so the URL pattern slots in next to the existing
``/api/podcasts/{podcast_slug}/episodes/{episode_slug}/transcript`` route.
A separate file (rather than appending to ``api_podcasts.py``) keeps the
DTOs and the route close together — the route is the only consumer.

The wire format uses short field names (``w``, ``s``, ``e``) to keep a 10k-
word episode's payload around 100–150 KB gzipped; long names would push it
over 200 KB and the response is fetched only when a user opts into the
karaoke chip, so payload weight directly affects time-to-paint when the
feature is engaged.
"""

from typing import List

from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel
from structlog import get_logger

from ..dependencies import AppState, get_app_state
from ..responses import api_response, not_found

logger = get_logger(__name__)

router = APIRouter()


class WordTimestamp(BaseModel):
    """One word with its raw-audio start/end seconds.

    Short field names: ``w`` (word text), ``s`` (start seconds), ``e``
    (end seconds). The client adds ``playback_time_offset_seconds`` from
    the response envelope before comparing to the audio element's
    ``currentTime`` — the offset is response metadata, not applied
    server-side, so a single shared offset value doesn't have to be
    repeated on every word.
    """

    w: str
    s: float
    e: float


class SegmentWords(BaseModel):
    """One ``AnnotatedSegment`` worth of words, keyed by its ``id``.

    ``segment_id`` matches ``AnnotatedSegment.id`` from the segmented
    transcript the client already holds, so the join on the frontend is
    a single ``Map`` lookup per active-segment transition.
    """

    segment_id: int
    words: List[WordTimestamp]


@router.get("/{podcast_slug}/episodes/{episode_slug}/transcript/words")
async def get_episode_transcript_words(
    podcast_slug: str,
    episode_slug: str,
    state: AppState = Depends(get_app_state),
) -> dict:
    """Return per-segment word-level timestamps for an episode.

    Returns 404 when the episode has no segmented transcript, no raw
    transcript, or no resolvable word timestamps anywhere in its raw
    transcript (e.g. Whisper-CPU runs that didn't surface word
    boundaries). The frontend treats 404 as "disable the karaoke chip
    for this episode" — the segment-level highlight still works.
    """
    result = state.repository.get_episode_by_slug(podcast_slug, episode_slug)
    if not result:
        not_found("Episode", f"{podcast_slug}/{episode_slug}")

    _, episode = result
    try:
        words_result = state.podcast_service.get_transcript_words_for_episode(episode)
    except (KeyError, IndexError) as error:
        # A ``source_word_span`` pointing at a raw segment id or word index
        # that doesn't exist. The raw JSON is write-once so this should never
        # happen in practice — when it does, fail loudly with a structured
        # log entry rather than silently producing a malformed response.
        logger.error(
            "transcript_words.corrupted_span",
            episode_id=episode.id,
            podcast_slug=podcast_slug,
            episode_slug=episode_slug,
            error_type=type(error).__name__,
            error=str(error),
        )
        raise HTTPException(
            status_code=500,
            detail="Transcript word data is inconsistent",
        ) from error

    if words_result is None:
        not_found("Word timestamps", f"{podcast_slug}/{episode_slug}")

    segments_payload = []
    for sw in words_result.segments:
        words_dto: List[WordTimestamp] = []
        for raw_w in sw.words:
            # Belt-and-braces: ``_collect_words_in_span`` is the single
            # source of "skip words without timestamps", but the API
            # surface contract is stricter ("never emit a word with an
            # undefined position"), so we keep a boundary check that
            # holds even if a future service change quietly weakens the
            # service-side guarantee. The local copy also narrows
            # Optional[float] → float for the DTO call below.
            start_s, end_s = raw_w.start, raw_w.end
            if start_s is None or end_s is None:
                continue
            words_dto.append(WordTimestamp(w=raw_w.word, s=start_s, e=end_s))
        segments_payload.append(SegmentWords(segment_id=sw.segment_id, words=words_dto).model_dump())

    return api_response(
        {
            "episode_id": episode.id,
            "playback_time_offset_seconds": words_result.playback_time_offset_seconds,
            "segments": segments_payload,
        }
    )
