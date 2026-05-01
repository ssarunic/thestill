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
from typing import TYPE_CHECKING, Dict, Iterable, List, Optional

from structlog import get_logger

from ..models.annotated_transcript import AnnotatedSegment, AnnotatedTranscript
from ..models.entities import EntityMention, EntityType, MentionRole, ResolutionMethod, ResolutionStatus
from .entity_anchor import AnchorVariant, index_variants_by_surface

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
# tend to be borderline topic surface forms ("discovery", "data") that
# the resolution stage rejects or mis-resolves anyway. Bumped from 0.5
# to 0.65 after a head-to-head probe vs an LLM-prompt extractor: at
# 0.5 we picked up unstable topic predictions in the 0.55-0.65 band;
# at 0.65 we kept every named-entity hit (people, companies, products)
# while cutting ~40% of the topic noise. Filtering at extraction time
# saves the resolution-stage round-trip.
DEFAULT_CONFIDENCE_THRESHOLD = 0.65

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
    # Spec §1.13.4: anchor-derived predictions are tagged here so
    # ``_to_mention`` can write the right ``extractor`` string.
    extractor: str = "gliner"


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
        anchor_variants: Optional[Iterable[AnchorVariant]] = None,
    ) -> List[EntityMention]:
        """Run GLiNER over each ``content`` segment, return mentions.

        ``episode_id`` is taken as a parameter (not from the JSON
        sidecar's own ``episode_id`` field) because the sidecar is
        often written with an empty ``episode_id`` — the database row
        is the authority.

        ``anchor_variants`` (spec §1.13.4) is the list of host/guest/
        recurring surface variants for this episode. When supplied,
        any GLiNER hit whose surface matches an anchor variant is
        marked ``resolution_status=resolved`` directly (skipping
        ReFinED), and the extractor additionally scans body text for
        anchor surfaces GLiNER may have missed (last-name-only,
        first-name-only) and synthesizes mentions for them.
        """
        self._load_model()
        anchor_index = index_variants_by_surface(anchor_variants or [])
        predictions = self._collect_predictions(transcript.segments)
        mentions: List[EntityMention] = []
        seen_spans: set = set()
        for pred in predictions:
            mention = self._to_mention(pred, episode_id=episode_id, anchor_index=anchor_index)
            mentions.append(mention)
            seen_spans.add((pred.segment.id, pred.char_start, pred.char_end))

        # Spec §1.13.4 — separate scan for anchor surfaces that GLiNER
        # missed entirely (e.g. last-name-only mentions in episodes with
        # a clean guest signal). Skip spans GLiNER already covered.
        if anchor_index:
            for anchor_pred in self._scan_anchors(transcript.segments, anchor_index, exclude_spans=seen_spans):
                mentions.append(
                    self._to_mention(
                        anchor_pred,
                        episode_id=episode_id,
                        anchor_index=anchor_index,
                    )
                )

        # Spec §1.13.2 — synthesize one SPEAKING mention per content
        # segment with a non-empty speaker. The speaker name is matched
        # against the anchor index (host/guest/recurring) to attach an
        # entity_id when known; otherwise the row stays pending.
        mentions.extend(self._synthesize_speaker_mentions(transcript, episode_id, anchor_index))

        logger.info(
            "entity_extraction_complete",
            episode_id=episode_id,
            mentions=len(mentions),
            content_segments=sum(1 for s in transcript.segments if s.kind == "content"),
            anchor_variants=len(anchor_index),
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
        gliner_extractor = f"gliner:{self.model_name}"
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
                        extractor=gliner_extractor,
                    )
                )
        return results

    def _collect_predictions_per_segment(
        self, targets: List[AnnotatedSegment], labels: List[str]
    ) -> List[_SegmentPrediction]:
        """Fallback path when the batch API errors. Same output shape."""
        results: List[_SegmentPrediction] = []
        gliner_extractor = f"gliner:{self.model_name}"
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
                        extractor=gliner_extractor,
                    )
                )
        return results

    def _to_mention(
        self,
        pred: _SegmentPrediction,
        *,
        episode_id: str,
        anchor_index: Optional[Dict[str, List[AnchorVariant]]] = None,
    ) -> EntityMention:
        # Per AnnotatedSegment: start/end are seconds (float). Convert
        # to milliseconds for the SQLite columns.
        start_ms = int(round(pred.segment.start * 1000))
        end_ms = int(round(pred.segment.end * 1000))
        anchor_match = _match_anchor(anchor_index, pred.surface_form) if anchor_index else None
        if anchor_match is not None:
            entity_id = anchor_match.entity_id
            label = anchor_match.entity_type.value
            return EntityMention(
                entity_id=entity_id,
                resolution_status=ResolutionStatus.RESOLVED,
                episode_id=episode_id,
                segment_id=pred.segment.id,
                start_ms=start_ms,
                end_ms=end_ms,
                speaker=pred.segment.speaker,
                role=MentionRole.MENTIONED,
                surface_form=pred.surface_form,
                surface_label=label,
                quote_excerpt=_excerpt_around(pred.segment.text, pred.char_start, pred.char_end),
                sentiment=None,
                confidence=pred.confidence,
                extractor=pred.extractor,
                resolution_method=ResolutionMethod.ANCHOR,
                resolved_at=None,
            )
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
            surface_label=pred.label,
            quote_excerpt=_excerpt_around(pred.segment.text, pred.char_start, pred.char_end),
            sentiment=None,
            confidence=pred.confidence,
            extractor=pred.extractor,
        )

    def _scan_anchors(
        self,
        segments: Iterable[AnnotatedSegment],
        anchor_index: Dict[str, List[AnchorVariant]],
        *,
        exclude_spans: set,
    ) -> List[_SegmentPrediction]:
        """Scan body text for anchor surfaces GLiNER missed.

        Word-boundary regex per surface keeps "Karpathy" from matching
        inside "Karpathys" while still matching at sentence boundaries.
        Skips spans GLiNER already produced (passed in
        ``exclude_spans``).
        """
        results: List[_SegmentPrediction] = []
        # Compile each surface once. We sort longest-first so longer
        # variants ("Andrej Karpathy") consume a span before shorter
        # ones ("Andrej") have a chance to.
        compiled = sorted(anchor_index.keys(), key=len, reverse=True)
        for segment in segments:
            if segment.kind != "content" or not segment.text.strip():
                continue
            text = segment.text
            consumed_spans: List = []
            for surface in compiled:
                pattern = re.compile(r"\b" + re.escape(surface) + r"\b", re.IGNORECASE)
                for match in pattern.finditer(text):
                    span = (match.start(), match.end())
                    if (segment.id, span[0], span[1]) in exclude_spans:
                        continue
                    if any(_overlaps(span, c) for c in consumed_spans):
                        continue
                    consumed_spans.append(span)
                    # Pull the canonical-cased variant for the recorded
                    # surface (cosmetic; resolution keys off the
                    # entity_id either way).
                    variants = anchor_index[surface]
                    canonical_surface = variants[0].surface
                    results.append(
                        _SegmentPrediction(
                            segment=segment,
                            surface_form=canonical_surface,
                            label=variants[0].entity_type.value,
                            confidence=1.0,  # anchor-matched, not GLiNER-scored
                            char_start=span[0],
                            char_end=span[1],
                            extractor="anchor:scan",
                        )
                    )
        return results

    def _synthesize_speaker_mentions(
        self,
        transcript: AnnotatedTranscript,
        episode_id: str,
        anchor_index: Dict[str, List[AnchorVariant]],
    ) -> List[EntityMention]:
        """Spec §1.13.2 — emit one SPEAKING mention per content segment.

        The mention's ``surface_form`` is the speaker label. When the
        speaker label exactly matches an anchor variant the mention is
        pre-resolved to that anchor entity (method=ANCHOR); otherwise
        the row is left ``unresolvable`` so it doesn't pollute the
        ReFinED batch (a bare speaker label like "Speaker 1" has no
        Wikidata QID).
        """
        out: List[EntityMention] = []
        for segment in transcript.segments:
            if segment.kind != "content":
                continue
            speaker = (segment.speaker or "").strip()
            if not speaker or speaker.lower() in {"unknown", "speaker", "n/a"}:
                continue
            anchor_match = _match_anchor(anchor_index, speaker)
            start_ms = int(round(segment.start * 1000))
            end_ms = int(round(segment.end * 1000))
            if anchor_match is not None:
                out.append(
                    EntityMention(
                        entity_id=anchor_match.entity_id,
                        resolution_status=ResolutionStatus.RESOLVED,
                        episode_id=episode_id,
                        segment_id=segment.id,
                        start_ms=start_ms,
                        end_ms=end_ms,
                        speaker=speaker,
                        role=MentionRole.SPEAKING,
                        surface_form=speaker,
                        surface_label=anchor_match.entity_type.value,
                        quote_excerpt=_speaker_excerpt(segment.text),
                        sentiment=None,
                        confidence=1.0,
                        extractor="speaker:synth",
                        resolution_method=ResolutionMethod.ANCHOR,
                    )
                )
            else:
                # Spec §1.13.2: still emit a SPEAKING row with surface=
                # speaker label even when unresolved, so quotes-by can
                # filter on speaker even before the operator wires up
                # host/guest metadata.
                out.append(
                    EntityMention(
                        entity_id=None,
                        resolution_status=ResolutionStatus.UNRESOLVABLE,
                        episode_id=episode_id,
                        segment_id=segment.id,
                        start_ms=start_ms,
                        end_ms=end_ms,
                        speaker=speaker,
                        role=MentionRole.SPEAKING,
                        surface_form=speaker,
                        surface_label="person",
                        quote_excerpt=_speaker_excerpt(segment.text),
                        sentiment=None,
                        confidence=1.0,
                        extractor="speaker:synth",
                        resolution_method=ResolutionMethod.UNRESOLVABLE,
                    )
                )
        return out


def _match_anchor(
    anchor_index: Optional[Dict[str, List[AnchorVariant]]],
    surface: str,
) -> Optional[AnchorVariant]:
    """Return the matching anchor variant for ``surface``, or ``None``."""
    if not anchor_index:
        return None
    bucket = anchor_index.get(surface.lower().strip())
    if not bucket:
        return None
    # Multiple anchors collide on the same short form (e.g. two
    # "Andrejs" in one episode) — leave it unresolved here; the coref
    # pass will mark it ambiguous instead of guessing.
    if len(bucket) > 1:
        return None
    return bucket[0]


def _overlaps(a: tuple, b: tuple) -> bool:
    """Half-open span overlap test for the anchor scanner."""
    return not (a[1] <= b[0] or b[1] <= a[0])


def _speaker_excerpt(text: str, *, max_chars: int = 240) -> str:
    """Quote excerpt for a synthesized speaker mention — first sentence
    of the segment so the citation surface still has narrative content,
    truncated to keep the row small.
    """
    cleaned = text.strip()
    if not cleaned:
        return ""
    sentence = re.split(r"(?<=[.!?])\s+", cleaned, maxsplit=1)[0]
    if len(sentence) > max_chars:
        return sentence[:max_chars].rstrip() + "…"
    return sentence


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
