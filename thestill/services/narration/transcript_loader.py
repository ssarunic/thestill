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

from dataclasses import dataclass
from pathlib import Path
from typing import Dict, List, Optional, Tuple

from structlog import get_logger

from ...core.facts_manager import FactsManager
from ...models.annotated_transcript import AnnotatedTranscript
from ...models.facts import EpisodeFacts, strip_role_annotation
from ...models.podcast import Episode, Podcast
from ...utils.path_manager import PathManager
from .models import SpeakerRole

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
    speaker_role: SpeakerRole
    text: str
    start_seconds: float
    end_seconds: float
    is_ad_adjacent: bool


def _classify_role(annotated: str) -> SpeakerRole:
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
        # Cache facts per (podcast_slug, episode_slug) so a single
        # generator run that asks for both speaker mapping and episode
        # facts (keywords, sponsors) reads the Markdown file once.
        self._facts_cache: Dict[Tuple[str, str], Optional[EpisodeFacts]] = {}

    def load(self, podcast: Podcast, episode: Episode) -> List[ResolvedTurn]:
        """Return resolved content turns, or ``[]`` when the sidecar is missing.

        Phase 1 reads only the structured JSON sidecar; the blended
        Markdown is not parsed. Episodes cleaned via the legacy path that
        do not produce a sidecar yield no quote candidates and route to
        the rapid-fire tail.
        """
        sidecar_path = self._resolve_sidecar_path(podcast, episode)
        if sidecar_path is None:
            return []
        transcript = self._load_sidecar(sidecar_path, episode)
        if transcript is None:
            return []

        speaker_map = self._speaker_mapping(podcast, episode)
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
            role: SpeakerRole = "unknown"
            if speaker_label and speaker_label in speaker_map:
                annotated = speaker_map[speaker_label]
                resolved_name = strip_role_annotation(annotated).strip() or None
                role = _classify_role(annotated)

            turns.append(
                ResolvedTurn(
                    episode_id=episode.id,
                    podcast_title=podcast.title,
                    segment_id=seg.id,
                    speaker_label=speaker_label,
                    speaker_name=resolved_name,
                    speaker_role=role,
                    text=seg.text,
                    start_seconds=float(seg.start),
                    end_seconds=float(seg.end),
                    is_ad_adjacent=_is_ad_adjacent(
                        float(seg.start),
                        float(seg.end),
                        ad_break_spans,
                        self.AD_ADJACENT_WINDOW_S,
                    ),
                )
            )
        return turns

    def load_episode_facts(self, podcast: Podcast, episode: Episode) -> Optional[EpisodeFacts]:
        """Best-effort facts load. Returns ``None`` on any failure or missing slug."""
        if not podcast.slug or not episode.slug:
            return None
        key = (podcast.slug, episode.slug)
        if key in self._facts_cache:
            return self._facts_cache[key]
        try:
            facts = self.facts_manager.load_episode_facts(podcast.slug, episode.slug)
        except Exception as exc:  # noqa: BLE001 — facts files are user-editable; tolerate
            logger.warning(
                "narration: failed to load episode facts",
                episode_id=episode.id,
                error=str(exc),
            )
            facts = None
        self._facts_cache[key] = facts
        return facts

    def _resolve_sidecar_path(
        self, podcast: Podcast, episode: Episode
    ) -> Optional[Path]:
        if not episode.clean_transcript_json_path:
            logger.debug(
                "narration: no clean transcript json sidecar; skipping for quotes",
                episode_id=episode.id,
            )
            return None
        if not podcast.slug:
            logger.debug(
                "narration: missing podcast slug; cannot resolve sidecar path",
                episode_id=episode.id,
            )
            return None
        try:
            path = self.path_manager.clean_transcript_json_file(
                podcast.slug, episode.clean_transcript_json_path
            )
        except ValueError as exc:
            logger.warning(
                "narration: invalid sidecar path; skipping",
                episode_id=episode.id,
                error=str(exc),
            )
            return None
        if not path.exists():
            logger.debug(
                "narration: clean transcript json sidecar missing on disk",
                episode_id=episode.id,
                path=str(path),
            )
            return None
        return path

    @staticmethod
    def _load_sidecar(path: Path, episode: Episode) -> Optional[AnnotatedTranscript]:
        try:
            return AnnotatedTranscript.model_validate_json(
                path.read_text(encoding="utf-8")
            )
        except Exception as exc:  # noqa: BLE001 — write-once disk artefact, log + continue
            logger.warning(
                "narration: failed to load clean transcript json",
                episode_id=episode.id,
                path=str(path),
                error=str(exc),
            )
            return None

    def _speaker_mapping(self, podcast: Podcast, episode: Episode) -> Dict[str, str]:
        facts = self.load_episode_facts(podcast, episode)
        return dict(facts.speaker_mapping) if facts else {}


def _is_ad_adjacent(
    start_s: float,
    end_s: float,
    ad_break_spans: List[Tuple[float, float]],
    window: float,
) -> bool:
    if not ad_break_spans:
        return False
    for ad_start, ad_end in ad_break_spans:
        if start_s <= ad_end and ad_start <= end_s:
            return True
        distance = min(abs(start_s - ad_end), abs(ad_start - end_s))
        if distance <= window:
            return True
    return False
