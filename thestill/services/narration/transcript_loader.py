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

"""Load cleaned-transcript turns with resolved speaker names.

Reads the structured ``AnnotatedTranscript`` JSON sidecar (spec #18)
and pairs each ``content`` segment with the real speaker name from the
episode-facts ``Speaker Mapping`` section. Segments tagged ``ad_break``,
``music``, ``intro``, ``outro``, or ``filler`` are dropped because they
are never quote-eligible. Content segments adjacent to an ad-break (per
spec #33 §"Quote Selection" sponsor-read filtering) are flagged so the
selector can drop them when the boundary-trim heuristic also fires.
"""

import json
from dataclasses import dataclass
from typing import Dict, List, Optional, Tuple

from structlog import get_logger

from ...core.facts_manager import FactsManager
from ...models.annotated_transcript import AnnotatedTranscript
from ...models.facts import EpisodeFacts, strip_role_annotation
from ...models.podcast import Episode, Podcast
from ...utils.path_manager import PathManager

logger = get_logger(__name__)


@dataclass(frozen=True)
class ResolvedTurn:
    """One content turn with the speaker resolved from facts.

    ``speaker_name`` is ``None`` when no mapping resolved the raw label.
    The selector treats unresolved turns as quote-ineligible per spec
    #33 §"Quote Selection".
    """

    episode_id: str
    podcast_title: str
    segment_id: int
    speaker_label: Optional[str]
    speaker_name: Optional[str]
    speaker_role: str  # "host" | "guest" | "unknown"
    text: str
    start_seconds: float
    end_seconds: float
    is_ad_adjacent: bool


def _classify_role(annotated: str) -> str:
    """Pull a role tag out of a Speaker Mapping value like ``Name (Host)``."""
    lower = annotated.lower()
    if "(host" in lower:
        return "host"
    if "(guest" in lower:
        return "guest"
    return "unknown"


class TranscriptTurnLoader:
    """Resolve cleaned-transcript JSON sidecars into ``ResolvedTurn`` lists."""

    AD_ADJACENT_WINDOW_S = 30.0

    def __init__(self, path_manager: PathManager, facts_manager: FactsManager):
        self.path_manager = path_manager
        self.facts_manager = facts_manager

    def load(self, podcast: Podcast, episode: Episode) -> List[ResolvedTurn]:
        """Return resolved content turns, or ``[]`` when the sidecar is missing.

        Phase 1 reads only the structured JSON sidecar; the blended
        Markdown is not parsed. Episodes cleaned via the legacy path that
        do not produce a sidecar therefore yield no quote candidates and
        will be routed to the rapid-fire tail.
        """
        if not episode.clean_transcript_json_path:
            logger.debug(
                "narration: no clean transcript json sidecar; skipping for quotes",
                episode_id=episode.id,
            )
            return []
        if not podcast.slug:
            logger.debug(
                "narration: missing podcast slug; cannot resolve sidecar path",
                episode_id=episode.id,
            )
            return []
        try:
            json_path = self.path_manager.clean_transcript_json_file(
                podcast.slug, episode.clean_transcript_json_path
            )
        except ValueError as exc:
            logger.warning(
                "narration: invalid sidecar path; skipping",
                episode_id=episode.id,
                error=str(exc),
            )
            return []
        if not json_path.exists():
            logger.debug(
                "narration: clean transcript json sidecar missing on disk",
                episode_id=episode.id,
                path=str(json_path),
            )
            return []
        try:
            raw = json.loads(json_path.read_text(encoding="utf-8"))
            transcript = AnnotatedTranscript.model_validate(raw)
        except Exception as exc:  # noqa: BLE001 — write-once disk artefact, log + continue
            logger.warning(
                "narration: failed to load clean transcript json",
                episode_id=episode.id,
                path=str(json_path),
                error=str(exc),
            )
            return []

        speaker_map = self._load_speaker_mapping(podcast, episode)

        # Track each ad-break's full ``[start, end]`` span. Adjacency is
        # measured to the nearest edge so a content segment 5s before an
        # ad starts and one 5s after the same ad ends are both flagged.
        ad_break_spans: List[Tuple[float, float]] = [
            (float(seg.start), float(seg.end))
            for seg in transcript.segments
            if seg.kind == "ad_break"
        ]

        turns: List[ResolvedTurn] = []
        for seg in transcript.segments:
            if seg.kind != "content":
                continue
            speaker_label = seg.speaker
            resolved_name: Optional[str] = None
            role = "unknown"
            if speaker_label and speaker_label in speaker_map:
                annotated = speaker_map[speaker_label]
                stripped = strip_role_annotation(annotated).strip()
                resolved_name = stripped or None
                role = _classify_role(annotated)

            turn = ResolvedTurn(
                episode_id=episode.id,
                podcast_title=podcast.title,
                segment_id=seg.id,
                speaker_label=speaker_label,
                speaker_name=resolved_name,
                speaker_role=role,
                text=seg.text,
                start_seconds=float(seg.start),
                end_seconds=float(seg.end),
                is_ad_adjacent=self._is_ad_adjacent(
                    float(seg.start), float(seg.end), ad_break_spans
                ),
            )
            turns.append(turn)
        return turns

    def load_episode_facts(self, podcast: Podcast, episode: Episode) -> Optional[EpisodeFacts]:
        """Best-effort facts load. Returns ``None`` on any failure or missing slug."""
        if not podcast.slug or not episode.slug:
            return None
        try:
            return self.facts_manager.load_episode_facts(podcast.slug, episode.slug)
        except Exception as exc:  # noqa: BLE001 — facts files are user-editable; tolerate
            logger.warning(
                "narration: failed to load episode facts",
                episode_id=episode.id,
                error=str(exc),
            )
            return None

    def _load_speaker_mapping(self, podcast: Podcast, episode: Episode) -> Dict[str, str]:
        facts = self.load_episode_facts(podcast, episode)
        return dict(facts.speaker_mapping) if facts else {}

    def _is_ad_adjacent(
        self,
        start_s: float,
        end_s: float,
        ad_break_spans: List[Tuple[float, float]],
    ) -> bool:
        if not ad_break_spans:
            return False
        window = self.AD_ADJACENT_WINDOW_S
        for ad_start, ad_end in ad_break_spans:
            # Closest distance between ``[start_s, end_s]`` and the
            # ad span ``[ad_start, ad_end]``. Zero (overlap) → adjacent.
            if start_s <= ad_end and ad_start <= end_s:
                return True
            distance = min(
                abs(start_s - ad_end),
                abs(ad_start - end_s),
            )
            if distance <= window:
                return True
        return False
