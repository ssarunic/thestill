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

import re
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

# Raw diarisation label as emitted by the transcriber. The segmented
# cleaner substitutes these with real names before writing the sidecar
# (``_apply_speaker_mapping``); one surviving to output means facts
# extraction missed that speaker.
_SPEAKER_LABEL_RE = re.compile(r"^SPEAKER_\d+$")


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


def _resolve_speaker(
    label: Optional[str],
    by_label: Dict[str, str],
    by_name: Dict[str, str],
) -> Tuple[Optional[str], SpeakerRole]:
    """Resolve a sidecar ``speaker`` value to ``(name, role)``.

    Spec #77 §1b. The sidecar written by the segmented cleaner already
    carries resolved names (``"Azeem Azhar"``), while the facts Speaker
    Mapping is keyed by raw label (``SPEAKER_02 -> "Azeem Azhar (Guest)"``).
    Look the value up both ways so legacy label-keyed sidecars and
    current name-keyed ones resolve. A real name with no facts row stays
    eligible with role ``unknown``; a raw ``SPEAKER_NN`` label that no
    mapping resolves stays ineligible (spec #33 §"Quote Selection").
    """
    if not label:
        return None, "unknown"
    annotated = by_label.get(label) or by_name.get(label)
    if annotated:
        return strip_role_annotation(annotated).strip() or None, _classify_role(annotated)
    if _SPEAKER_LABEL_RE.match(label):
        return None, "unknown"
    return label, "unknown"


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

        by_label, by_name = self._speaker_lookup(podcast, episode)
        ad_break_spans: List[Tuple[float, float]] = [
            (float(seg.start), float(seg.end)) for seg in transcript.segments if seg.kind == "ad_break"
        ]

        turns: List[ResolvedTurn] = []
        for seg in transcript.segments:
            if seg.kind != "content":
                continue
            speaker_label = seg.speaker
            resolved_name, role = _resolve_speaker(speaker_label, by_label, by_name)

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

    def sidecar_exists(self, podcast: Podcast, episode: Episode) -> bool:
        """True when the episode's cleaned-transcript JSON sidecar is on disk.

        Lets the generator tell "no transcripts to quote from" apart from
        "transcripts present but nothing selected" (spec #77 §6).
        """
        return self._resolve_sidecar_path(podcast, episode) is not None

    def _resolve_sidecar_path(self, podcast: Podcast, episode: Episode) -> Optional[Path]:
        if not episode.clean_transcript_json_path:
            logger.debug(
                "narration: no clean transcript json sidecar; skipping for quotes",
                episode_id=episode.id,
            )
            return None
        # ``clean_transcript_json_path`` is stored storage-relative
        # (``<podcast-slug>/<stem>.json``, see task_handlers), so resolve
        # it the way every other reader does (podcast_service). Feeding
        # it to the slug-prefixing helper doubled the slug and the suffix
        # and silently emptied the quote pool (spec #77 §1a).
        try:
            path = self.path_manager._assert_inside_root(
                self.path_manager.clean_transcript_file(episode.clean_transcript_json_path)
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
            return AnnotatedTranscript.model_validate_json(path.read_text(encoding="utf-8"))
        except Exception as exc:  # noqa: BLE001 — write-once disk artefact, log + continue
            logger.warning(
                "narration: failed to load clean transcript json",
                episode_id=episode.id,
                path=str(path),
                error=str(exc),
            )
            return None

    def _speaker_lookup(self, podcast: Podcast, episode: Episode) -> Tuple[Dict[str, str], Dict[str, str]]:
        """Facts Speaker Mapping keyed both by raw label and by resolved name."""
        facts = self.load_episode_facts(podcast, episode)
        by_label: Dict[str, str] = dict(facts.speaker_mapping) if facts else {}
        by_name: Dict[str, str] = {}
        for annotated in by_label.values():
            name = strip_role_annotation(annotated).strip()
            if name:
                by_name.setdefault(name, annotated)
        return by_label, by_name


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
