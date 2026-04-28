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

"""GLiNER-driven entity extraction over ``AnnotatedTranscript`` segments.

Spec #28 §1.2. Reads the structured JSON sidecar (NOT the rendered
Markdown — segment IDs and timestamps come from the typed source) and
emits ``EntityMention`` rows with ``entity_id=None`` and
``resolution_status=PENDING``. The resolution stage fills the FK in
later.

Default entity labels: ``person``, ``company``, ``product``, ``topic``
(maps to the four ``EntityType`` values in
``thestill.models.entities``). The label set is configurable so a
future spec can add e.g. "law" or "publication" without touching
extraction logic.

Why GLiNER rather than spaCy or an LLM:

- Per spec strategy §"What we explicitly do not do": custom NER
  training is six months for ~5% accuracy, not worth it for v1.
- Per-chunk LLM calls cost real money at corpus scale (~$5k to
  re-extract 10k episodes).
- GLiNER runs on CPU, costs cents per corpus, and handles the four
  typed entities we care about with acceptable recall.

Loading the model is expensive (~5-10s, ~400MB download on first
use), so callers should construct one ``EntityExtractor`` and reuse
it across episodes — typically held by the pipeline handler at
process scope.
"""

from __future__ import annotations

import re
from dataclasses import dataclass
from typing import TYPE_CHECKING, Iterable, List, Optional

from structlog import get_logger

from ..models.annotated_transcript import AnnotatedSegment, AnnotatedTranscript
from ..models.entities import EntityMention, EntityType, ResolutionStatus

if TYPE_CHECKING:
    # ``gliner`` is an optional dep — imported lazily inside ``_load_model``
    # so this module can be imported (and unit-tested) without it.
    from gliner import GLiNER  # noqa: F401

logger = get_logger(__name__)


# Default GLiNER model — small enough to run on CPU comfortably,
# large enough that recall on PERSON/COMPANY/PRODUCT is usable.
# Pinned in tests; overridable in production via ``EntityExtractor(model=...)``.
DEFAULT_GLINER_MODEL = "urchade/gliner_small-v2.1"

# Map GLiNER's prompt labels (lowercase, descriptive) to our typed
# ``EntityType`` enum. Spec #28 §"Strategy" pins the four types.
DEFAULT_LABELS_TO_TYPES = {
    "person": EntityType.PERSON,
    "company": EntityType.COMPANY,
    "product": EntityType.PRODUCT,
    "topic": EntityType.TOPIC,
}

# Confidence floor — GLiNER returns scored predictions; very low scores
# tend to be wikipedia-tier surface forms ("you", "all the time") that
# the resolution stage would reject anyway. Filtering at extraction
# time saves the resolution call.
DEFAULT_CONFIDENCE_THRESHOLD = 0.5

# Quote excerpt window: ±N characters around the surface form. Matches
# the spec contract in §"Citation-shaped results" — every mention
# returns a 1-sentence excerpt the LLM harness can quote without
# re-fetching the full segment.
QUOTE_EXCERPT_WINDOW = 200

# GLiNER zero-shot at threshold 0.5 happily classifies first/second/third-
# person pronouns as ``person`` entities (44x "you", 35x "I", 14x "we" on
# a 68-content-segment fixture). These never resolve to a Wikidata QID
# and would just bloat the resolution batch. Filtered case-insensitively
# at extraction time. Real entities containing these tokens (e.g. "I, Robot")
# come through because we match on the whole surface form, not substrings.
_PRONOUN_STOPLIST = frozenset(
    {
        # subject / object pronouns
        "i",
        "me",
        "you",
        "he",
        "him",
        "she",
        "her",
        "it",
        "we",
        "us",
        "they",
        "them",
        # possessive / reflexive
        "my",
        "mine",
        "your",
        "yours",
        "his",
        "hers",
        "its",
        "our",
        "ours",
        "their",
        "theirs",
        "myself",
        "yourself",
        "himself",
        "herself",
        "itself",
        "ourselves",
        "yourselves",
        "themselves",
        # demonstratives + articles
        "this",
        "that",
        "these",
        "those",
        "the",
        "a",
        "an",
        # misc filler the model loves to tag
        "everybody",
        "everyone",
        "anybody",
        "anyone",
        "nobody",
        "someone",
    }
)


@dataclass(frozen=True)
class _SegmentPrediction:
    """Internal record paired with the segment that produced it."""

    segment: AnnotatedSegment
    surface_form: str
    label: str
    confidence: float
    char_start: int
    char_end: int


class EntityExtractor:
    """GLiNER zero-shot entity extractor over annotated transcripts.

    The model is loaded lazily on first ``extract`` call. Subsequent
    calls reuse the in-memory model — callers should hold the
    extractor at process scope rather than per-episode.
    """

    def __init__(
        self,
        *,
        model: str = DEFAULT_GLINER_MODEL,
        labels_to_types: Optional[dict] = None,
        confidence_threshold: float = DEFAULT_CONFIDENCE_THRESHOLD,
        preloaded_model: Optional["GLiNER"] = None,
    ):
        """``preloaded_model`` is a test seam — pass a stub or a
        pre-warmed real model and ``_load_model`` becomes a no-op.
        Production callers don't pass it.
        """
        self.model_name = model
        self.labels_to_types = dict(labels_to_types or DEFAULT_LABELS_TO_TYPES)
        self.confidence_threshold = confidence_threshold
        self._model: Optional["GLiNER"] = preloaded_model

    # ------------------------------------------------------------------
    # Public API
    # ------------------------------------------------------------------

    def extract(
        self,
        transcript: AnnotatedTranscript,
        *,
        episode_id: str,
    ) -> List[EntityMention]:
        """Run GLiNER over each ``content`` segment, return pending mentions.

        ``episode_id`` is taken as a parameter (not from the JSON
        sidecar's own ``episode_id`` field) because the sidecar is
        often written with an empty ``episode_id`` — the database row
        is the authority.
        """
        self._load_model()
        predictions = self._collect_predictions(transcript.segments)
        mentions = [self._to_mention(p, episode_id=episode_id) for p in predictions]
        logger.info(
            "entity_extraction_complete",
            episode_id=episode_id,
            mentions=len(mentions),
            content_segments=sum(1 for s in transcript.segments if s.kind == "content"),
        )
        return mentions

    # ------------------------------------------------------------------
    # Internals
    # ------------------------------------------------------------------

    def _load_model(self) -> None:
        if self._model is not None:
            return
        try:
            from gliner import GLiNER
        except ImportError as exc:  # pragma: no cover — env-specific
            raise RuntimeError(
                "gliner is not installed — install the entities extra: " 'pip install -e ".[entities]"'
            ) from exc
        logger.info("gliner_model_loading", model=self.model_name)
        self._model = GLiNER.from_pretrained(self.model_name)
        logger.info("gliner_model_loaded", model=self.model_name)

    def _collect_predictions(self, segments: Iterable[AnnotatedSegment]) -> List[_SegmentPrediction]:
        labels = list(self.labels_to_types.keys())
        # Spec §"Strategy" — only ``content`` carries narrative
        # entities. Skipping ad_break/intro/outro/filler/music avoids
        # polluting the index with sponsor and music-cue noise the
        # spec explicitly excludes.
        targets = [s for s in segments if s.kind == "content" and s.text.strip()]
        if not targets:
            return []

        # GLiNER's ``inference`` runs a single padded forward pass
        # over multiple texts (replacing the deprecated
        # ``batch_predict_entities``) — ~2x faster on CPU than one
        # call per segment for typical episode sizes (50-150 content
        # segments). Falls back to per-segment on environments where
        # the batch API misbehaves (e.g. flashdeberta + GLiNER
        # incompatibility, see GLiNER#263).
        try:
            batch_results = self._model.inference(
                [s.text for s in targets],
                labels,
                threshold=self.confidence_threshold,
            )
        except Exception:
            logger.exception("gliner_batch_inference_failed_falling_back")
            return self._collect_predictions_per_segment(targets, labels)

        results: List[_SegmentPrediction] = []
        for segment, hits in zip(targets, batch_results):
            for hit in hits:
                surface = hit["text"]
                if surface.lower().strip() in _PRONOUN_STOPLIST:
                    continue
                results.append(
                    _SegmentPrediction(
                        segment=segment,
                        surface_form=surface,
                        label=hit["label"],
                        confidence=float(hit["score"]),
                        char_start=int(hit["start"]),
                        char_end=int(hit["end"]),
                    )
                )
        return results

    def _collect_predictions_per_segment(
        self, targets: List[AnnotatedSegment], labels: List[str]
    ) -> List[_SegmentPrediction]:
        """Fallback path when the batch API errors. Same output shape."""
        results: List[_SegmentPrediction] = []
        for segment in targets:
            try:
                raw = self._model.predict_entities(segment.text, labels, threshold=self.confidence_threshold)
            except Exception:  # pragma: no cover — model-runtime
                logger.exception(
                    "gliner_predict_failed",
                    segment_id=segment.id,
                    text_len=len(segment.text),
                )
                continue
            for hit in raw:
                surface = hit["text"]
                if surface.lower().strip() in _PRONOUN_STOPLIST:
                    continue
                results.append(
                    _SegmentPrediction(
                        segment=segment,
                        surface_form=surface,
                        label=hit["label"],
                        confidence=float(hit["score"]),
                        char_start=int(hit["start"]),
                        char_end=int(hit["end"]),
                    )
                )
        return results

    def _to_mention(self, pred: _SegmentPrediction, *, episode_id: str) -> EntityMention:
        # Per AnnotatedSegment: start/end are seconds (float). Convert
        # to milliseconds for the SQLite columns.
        start_ms = int(round(pred.segment.start * 1000))
        end_ms = int(round(pred.segment.end * 1000))
        return EntityMention(
            entity_id=None,
            resolution_status=ResolutionStatus.PENDING,
            episode_id=episode_id,
            segment_id=pred.segment.id,
            start_ms=start_ms,
            end_ms=end_ms,
            speaker=pred.segment.speaker,
            role=None,
            surface_form=pred.surface_form,
            quote_excerpt=_excerpt_around(pred.segment.text, pred.char_start, pred.char_end),
            sentiment=None,
            confidence=pred.confidence,
            extractor=f"gliner:{self.model_name}",
        )


def _excerpt_around(text: str, char_start: int, char_end: int) -> str:
    """Return ±``QUOTE_EXCERPT_WINDOW`` chars around the mention.

    Snaps to the nearest sentence boundary on either side when
    available, otherwise just slices and trims whitespace. Guarantees
    a non-empty result for any in-bounds match.
    """
    text_len = len(text)
    left = max(0, char_start - QUOTE_EXCERPT_WINDOW)
    right = min(text_len, char_end + QUOTE_EXCERPT_WINDOW)

    # Snap left to the nearest sentence-ending punctuation if there
    # is one in the window — keeps quotes readable.
    snap_match = re.search(r"[\.!?]\s+", text[left:char_start])
    if snap_match:
        left += snap_match.end()

    # Snap right to the nearest sentence-ending punctuation.
    snap_match = re.search(r"[\.!?](\s|$)", text[char_end:right])
    if snap_match:
        right = char_end + snap_match.end()

    return text[left:right].strip()
