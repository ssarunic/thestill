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

"""The effective transcript: cleaned sidecar plus replayed edits (spec #110 §3).

``load_effective_transcript`` is the one place a cleaned-transcript JSON
sidecar is parsed. Every reader (the transcript and words APIs, MCP, the
summarizer, entity extraction, search chunks, citations, narration and the
repair tools) goes through it, so an admin's corrections reach all of them
and an episode nobody edited reads exactly as the sidecar was written.

Replay rules (§3): an op applies only when it was written against the
current raw transcript (``raw_sha256``), its anchor resolves to exactly one
segment, and that segment's text still hashes to what the editor saw.
Anything else is a listed conflict, never a silent drop. Reverts cancel
ops; a revert of a revert restores one.
"""

from __future__ import annotations

import hashlib
import threading
import uuid
from collections import Counter, OrderedDict, defaultdict
from dataclasses import dataclass, field, replace
from pathlib import Path
from typing import TYPE_CHECKING, Any, Dict, List, Literal, Optional, Tuple

from structlog import get_logger

from ..models.annotated_transcript import AnnotatedSegment, AnnotatedTranscript, SegmentKind, WordSpan
from ..models.podcast import Episode
from ..models.transcript import Segment as RawSegment
from ..models.transcript import Transcript
from ..models.transcript_edits import (
    STRUCTURAL_OPS,
    EditOp,
    RawWordRef,
    SegmentAnchor,
    SegmentMerge,
    SegmentSetKind,
    SegmentSetSpeaker,
    SegmentSetText,
    SegmentSplit,
    SpeakerMergeLabels,
    SpeakerRenameLabel,
    TranscriptEdit,
    text_sha256,
)

if TYPE_CHECKING:
    from ..repositories.transcript_edit_repository import TranscriptEditRepository
    from ..utils.file_storage import FileStorage
    from ..utils.path_manager import PathManager

logger = get_logger(__name__)

#: Segment kinds the summarizer never sees. The cleaner strips them from
#: ``_cleaned.md``; the effective transcript's Markdown projection strips
#: the same set so an edit never changes what reaches the LLM (§3).
SUMMARIZER_EXCLUDED_KINDS: frozenset = frozenset({"ad_break"})

#: ``AnnotatedSegment.metadata`` keys the replay writes.
EDITED_KEY = "edited"
SPEAKER_ENTITY_KEY = "speaker_entity_id"
#: Set on a segment whose text was edited: its raw word timings no longer
#: line up with the text (word re-alignment is a non-goal in v1).
WORDS_STALE_KEY = "words_stale"
# Kinds that carry a voice: speaker ops rename and merge these, and only
# these list in the speaker panel. An ad read is spoken too, but its
# attribution is the sponsor, not the host (``rename_label`` leaves it).
SPEECH_KINDS: Tuple[str, ...] = ("content", "filler", "intro", "outro")

_CACHE_SIZE = 64


class EditLogUnavailable(RuntimeError):
    """The op log could not be read. Pipeline stages retry rather than
    build artifacts from the unedited sidecar (spec #42 FM-1)."""


@dataclass(frozen=True)
class EditConflict:
    edit_id: str
    seq: int
    op: str
    reason: str
    detail: Dict[str, Any] = field(default_factory=dict)

    def to_dict(self) -> Dict[str, Any]:
        return {"edit_id": self.edit_id, "seq": self.seq, "op": self.op, "reason": self.reason, "detail": self.detail}


@dataclass
class SpeakerLabel:
    """One diarization label as the speaker panel shows it."""

    label: str
    name: Optional[str]
    entity_id: Optional[str]
    segment_ids: List[int]


@dataclass
class EffectiveTranscript:
    transcript: AnnotatedTranscript
    edits: List[TranscriptEdit]
    applied: List[TranscriptEdit]
    conflicts: List[EditConflict]
    reverted_ids: frozenset
    last_seq: int
    raw_sha256: Optional[str]
    speakers: List[SpeakerLabel]
    labels_by_segment: Dict[int, str]
    overlay_failed: bool = False

    @property
    def has_applied_edits(self) -> bool:
        return bool(self.applied)

    @property
    def live_edits(self) -> List[TranscriptEdit]:
        """Ops that are neither reverts nor reverted (applied or in conflict)."""
        return [e for e in self.edits if e.op != "revert" and e.id not in self.reverted_ids]

    def to_summarizer_markdown(self) -> str:
        """The blended Markdown the summarizer reads, ads stripped like ``_cleaned.md``."""
        return self.transcript.to_blended_markdown(exclude_kinds=SUMMARIZER_EXCLUDED_KINDS)


def raw_sha256(text: str) -> str:
    return hashlib.sha256(text.encode("utf-8")).hexdigest()


def is_placeholder_speaker(speaker: Optional[str]) -> bool:
    """A segment whose speaker a reader would see as unnamed (success metric §)."""
    if not speaker:
        return True
    value = speaker.strip()
    return not value or _is_diarization_label(value)


def _is_diarization_label(value: str) -> bool:
    upper = value.upper()
    return upper.startswith("SPEAKER_") and upper[8:].isdigit()


# ---------------------------------------------------------------------------
# Loading
# ---------------------------------------------------------------------------


def _read(path: Path, path_manager: "PathManager", file_storage: Optional["FileStorage"]) -> str:
    # Handlers read local disk; the web and citation paths go through the
    # configured backend (spec #35). Each caller keeps the read it had.
    if file_storage is not None:
        return file_storage.read_text(path_manager.to_relative(path))
    return path.read_text(encoding="utf-8")


def _parse_sidecar(text: str) -> AnnotatedTranscript:
    # The only parse of a cleaned sidecar in the package (enforced by a test).
    return AnnotatedTranscript.model_validate_json(text)


def load_effective_transcript(
    episode: Episode,
    *,
    edits: Optional["TranscriptEditRepository"],
    path_manager: "PathManager",
    file_storage: Optional["FileStorage"] = None,
    include_labels: bool = False,
    on_log_error: Literal["raise", "fallback"] = "raise",
) -> Optional[EffectiveTranscript]:
    """Return the episode's effective transcript, or ``None`` without a sidecar.

    ``edits`` may be ``None`` (hand-built fixtures): the sidecar is returned
    as written. ``include_labels`` also reads the raw transcript when there
    are no ops, so the speaker panel can list diarization labels for an
    episode nobody has edited yet. ``on_log_error`` decides what an
    unreadable op log means: pipeline stages ``raise`` (and retry), reader
    views ``fallback`` to the base transcript with ``overlay_failed`` set.
    """
    if not episode.clean_transcript_json_path:
        return None
    sidecar_path = path_manager.clean_transcript_file(episode.clean_transcript_json_path)
    try:
        sidecar_text = _read(sidecar_path, path_manager, file_storage)
    except FileNotFoundError:
        return None

    overlay_failed = False
    rows: List[TranscriptEdit] = []
    if edits is not None:
        try:
            rows = edits.list_for_episode(episode.id)
        except Exception as exc:  # pylint: disable=broad-except
            if on_log_error == "raise":
                raise EditLogUnavailable(f"transcript edit log unreadable for episode {episode.id}: {exc}") from exc
            logger.error("transcript_edits.overlay_failed", episode_id=episode.id, error=str(exc), exc_info=True)
            overlay_failed = True

    if not rows and not include_labels:
        # Unedited episode: one indexed query, then the sidecar as written.
        return EffectiveTranscript(
            transcript=_parse_sidecar(sidecar_text),
            edits=[],
            applied=[],
            conflicts=[],
            reverted_ids=frozenset(),
            last_seq=0,
            raw_sha256=None,
            speakers=[],
            labels_by_segment={},
            overlay_failed=overlay_failed,
        )

    raw_text: Optional[str] = None
    if episode.raw_transcript_path:
        raw_path = path_manager.raw_transcript_file(episode.raw_transcript_path)
        try:
            raw_text = _read(raw_path, path_manager, file_storage)
        except FileNotFoundError:
            raw_text = None

    last_seq = rows[-1].seq if rows else 0
    raw_hash = raw_sha256(raw_text) if raw_text is not None else None
    key = (episode.id, raw_hash, raw_sha256(sidecar_text), last_seq)
    cached = _cache_get(key)
    if cached is not None:
        return replace(cached, transcript=cached.transcript.model_copy(deep=True), overlay_failed=overlay_failed)

    transcript = _parse_sidecar(sidecar_text)
    raw = Transcript.model_validate_json(raw_text) if raw_text is not None else None
    result = _Replayer(transcript, raw, raw_hash).replay(rows)
    result.overlay_failed = overlay_failed
    _cache_put(key, result)
    return replace(result, transcript=result.transcript.model_copy(deep=True))


_cache: "OrderedDict[Tuple[Any, ...], EffectiveTranscript]" = OrderedDict()
_cache_lock = threading.Lock()


def _cache_get(key: Tuple[Any, ...]) -> Optional[EffectiveTranscript]:
    with _cache_lock:
        value = _cache.get(key)
        if value is not None:
            _cache.move_to_end(key)
        return value


def _cache_put(key: Tuple[Any, ...], value: EffectiveTranscript) -> None:
    with _cache_lock:
        _cache[key] = value
        _cache.move_to_end(key)
        while len(_cache) > _CACHE_SIZE:
            _cache.popitem(last=False)


def clear_cache() -> None:
    with _cache_lock:
        _cache.clear()


# ---------------------------------------------------------------------------
# Replay
# ---------------------------------------------------------------------------


def live_edits(rows: List[TranscriptEdit]) -> Tuple[List[TranscriptEdit], frozenset]:
    """Split the log into the ops to apply and the ids cancelled by reverts.

    An op is live unless a *live* revert targets it, so reverting a revert
    restores the op (undo/redo, §2).
    """
    reverts_of: Dict[str, List[str]] = defaultdict(list)
    for row in rows:
        if row.op == "revert":
            target = row.reverts or row.anchor.get("edit_id")
            if target:
                reverts_of[target].append(row.id)
    memo: Dict[str, bool] = {}

    def is_live(edit_id: str) -> bool:
        if edit_id not in memo:
            memo[edit_id] = False  # guards a malformed cycle
            memo[edit_id] = not any(is_live(r) for r in reverts_of.get(edit_id, ()))
        return memo[edit_id]

    reverted = frozenset(row.id for row in rows if not is_live(row.id))
    return [row for row in rows if row.op != "revert" and row.id not in reverted], reverted


def _majority(values: List[Optional[str]]) -> Optional[str]:
    counts = Counter(v for v in values if v)
    if not counts:
        return None
    best = max(counts.values())
    for value in values:  # first encountered among the tied
        if value and counts[value] == best:
            return value
    return None


def _span_equal(a: Optional[WordSpan], b: Optional[WordSpan]) -> bool:
    if a is None or b is None:
        return a is b
    return a.model_dump() == b.model_dump()


def _segment_snapshot(segment: AnnotatedSegment) -> Dict[str, Any]:
    return segment.model_dump()


class _Replayer:
    """Applies ops in ``seq`` order to one sidecar + raw transcript pair."""

    def __init__(self, transcript: AnnotatedTranscript, raw: Optional[Transcript], raw_hash: Optional[str]) -> None:
        self.transcript = transcript
        self.segments: List[AnnotatedSegment] = [s.model_copy(deep=True) for s in transcript.segments]
        self.raw_hash = raw_hash
        self.raw_by_id: Dict[int, RawSegment] = {s.id: s for s in raw.segments} if raw else {}
        self.raw_order: Dict[int, int] = {s.id: i for i, s in enumerate(raw.segments)} if raw else {}
        self.labels: List[Optional[str]] = [self.label_for(s) for s in self.segments]
        self.label_names: Dict[str, Optional[str]] = {}
        self.label_entities: Dict[str, Optional[str]] = {}
        by_label: Dict[str, List[Optional[str]]] = defaultdict(list)
        for segment, label in zip(self.segments, self.labels):
            if not label:
                continue
            # Every label is known, even one heard only in an ad read, so a
            # row re-tagged to speech later can be renamed; its name comes
            # from the speech rows alone.
            by_label[label].append(segment.speaker if segment.kind in SPEECH_KINDS else None)
        for label, speakers in by_label.items():
            self.label_names[label] = _majority(speakers)
        # Labels a speaker op touched: a row re-tagged into a speech kind
        # afterwards takes the label's current name (``_set_kind``).
        self.touched_labels: set = set()
        self.structural_seq: Optional[int] = None

    # -- labels ------------------------------------------------------------

    def label_for(self, segment: AnnotatedSegment) -> Optional[str]:
        """The diarization label behind a cleaned segment: the majority raw
        label across its ``source_segment_ids`` (§2 "Speaker labels")."""
        raw_labels = [self.raw_by_id[sid].speaker for sid in segment.source_segment_ids if sid in self.raw_by_id]
        return _majority(raw_labels)

    def _display_name(self, label: str) -> str:
        return self.label_names.get(label) or label

    # -- anchors -----------------------------------------------------------

    def _find(self, anchor: SegmentAnchor) -> Tuple[Optional[int], Optional[EditConflict]]:
        """Index of the one segment the anchor names, or the conflict reason."""
        want = list(anchor.source_segment_ids)
        candidates = [
            i
            for i, s in enumerate(self.segments)
            if s.source_segment_ids == want and _span_equal(s.source_word_span, anchor.source_word_span)
        ]
        if not candidates:
            return None, EditConflict("", 0, "", "anchor_missing", {"source_segment_ids": want})
        if len(candidates) > 1:
            # The children of a split without word-level anchors share ids
            # and span; the text hash is what tells them apart.
            by_text = [i for i in candidates if text_sha256(self.segments[i].text) == anchor.text_sha256]
            if len(by_text) > 1:
                return None, EditConflict("", 0, "", "anchor_ambiguous", {"source_segment_ids": want})
            if not by_text:
                return None, EditConflict(
                    "", 0, "", "text_changed", {"current_text": self.segments[candidates[0]].text}
                )
            return by_text[0], None
        index = candidates[0]
        current = self.segments[index].text
        if text_sha256(current) != anchor.text_sha256:
            return None, EditConflict("", 0, "", "text_changed", {"current_text": current})
        return index, None

    # -- apply ---------------------------------------------------------------

    def apply(self, row: TranscriptEdit) -> Tuple[Optional[EditConflict], Dict[str, Any]]:
        """Apply one live op. Returns ``(conflict, before)``; ``before`` is the
        snapshot of what the op replaced (empty on conflict)."""
        if self.raw_hash is None:
            return self._conflict(row, "raw_missing"), {}
        if row.raw_sha256 != self.raw_hash:
            return self._conflict(row, "retranscribed", {"written_against": row.raw_sha256}), {}
        try:
            op = row.parsed()
        except ValueError as exc:
            return self._conflict(row, "invalid", {"error": str(exc)}), {}
        conflict, before = self._apply_op(op, row)
        if conflict is not None:
            return replace(conflict, edit_id=row.id, seq=row.seq, op=row.op), {}
        if row.op in STRUCTURAL_OPS:
            self.structural_seq = row.seq
        return None, before

    def _conflict(self, row: TranscriptEdit, reason: str, detail: Optional[Dict[str, Any]] = None) -> EditConflict:
        return EditConflict(row.id, row.seq, row.op, reason, detail or {})

    def _apply_op(self, op: EditOp, row: TranscriptEdit) -> Tuple[Optional[EditConflict], Dict[str, Any]]:
        if isinstance(op, SpeakerRenameLabel):
            return self._rename_label(op)
        if isinstance(op, SpeakerMergeLabels):
            return self._merge_labels(op)
        if isinstance(op, SegmentSetSpeaker):
            return self._set_speaker(op, row)
        if isinstance(op, SegmentSetText):
            return self._set_text(op, row)
        if isinstance(op, SegmentSetKind):
            return self._set_kind(op, row)
        if isinstance(op, SegmentSplit):
            return self._split(op, row)
        if isinstance(op, SegmentMerge):
            return self._merge(op, row)
        return EditConflict("", 0, "", "invalid", {"error": f"unsupported op {op.op}"}), {}

    def _mark(self, segment: AnnotatedSegment, row: TranscriptEdit, child: int = 0) -> None:
        segment.metadata[EDITED_KEY] = True
        if not segment.user_segment_id:
            segment.user_segment_id = str(uuid.uuid5(uuid.NAMESPACE_URL, f"thestill:transcript-edit:{row.id}:{child}"))

    def _rename_label(self, op: SpeakerRenameLabel) -> Tuple[Optional[EditConflict], Dict[str, Any]]:
        label = op.anchor.label
        if label not in self.label_names:
            return EditConflict("", 0, "", "label_missing", {"label": label}), {}
        before = {"name": self.label_names.get(label), "entity_id": self.label_entities.get(label)}
        self.label_names[label] = op.args.name
        self.label_entities[label] = op.args.entity_id
        self.touched_labels.add(label)
        for i, segment in enumerate(self.segments):
            # Only speech rows: an ad break built from the same raw segment
            # keeps its (empty) speaker.
            if self.labels[i] == label and segment.kind in SPEECH_KINDS:
                segment.speaker = op.args.name
                segment.metadata[EDITED_KEY] = True
                if op.args.entity_id:
                    segment.metadata[SPEAKER_ENTITY_KEY] = op.args.entity_id
                else:
                    segment.metadata.pop(SPEAKER_ENTITY_KEY, None)
        return None, before

    def _merge_labels(self, op: SpeakerMergeLabels) -> Tuple[Optional[EditConflict], Dict[str, Any]]:
        label, into = op.anchor.label, op.args.into_label
        if label not in self.label_names:
            return EditConflict("", 0, "", "label_missing", {"label": label}), {}
        if into not in self.label_names:
            return EditConflict("", 0, "", "label_missing", {"label": into}), {}
        moved = [i for i, l in enumerate(self.labels) if l == label]
        spoken = [i for i in moved if self.segments[i].kind in SPEECH_KINDS]
        before = {
            "name": self.label_names.get(label),
            "entity_id": self.label_entities.get(label),
            "segments": len(spoken),
        }
        name = self._display_name(into)
        entity_id = self.label_entities.get(into)
        self.touched_labels.add(into)
        for i in moved:
            # Every row of the label moves, so an ad read re-tagged to
            # speech later belongs to ``into``; only speech rows are renamed.
            self.labels[i] = into
        for i in spoken:
            self.segments[i].speaker = name
            self.segments[i].metadata[EDITED_KEY] = True
            if entity_id:
                self.segments[i].metadata[SPEAKER_ENTITY_KEY] = entity_id
            else:
                self.segments[i].metadata.pop(SPEAKER_ENTITY_KEY, None)
        self.label_names.pop(label, None)
        self.label_entities.pop(label, None)
        return None, before

    def _set_speaker(self, op: SegmentSetSpeaker, row: TranscriptEdit) -> Tuple[Optional[EditConflict], Dict[str, Any]]:
        index, conflict = self._find(op.anchor)
        if conflict is not None:
            return conflict, {}
        segment = self.segments[index]
        before = {"speaker": segment.speaker, "label": self.labels[index]}
        if op.args.label:
            if op.args.label not in self.label_names:
                return EditConflict("", 0, "", "label_missing", {"label": op.args.label}), {}
            self.labels[index] = op.args.label
            segment.speaker = self._display_name(op.args.label)
            entity_id = self.label_entities.get(op.args.label)
        else:
            # A name that is no voice of this episode: the segment leaves
            # its diarization label behind.
            self.labels[index] = None
            segment.speaker = op.args.name
            entity_id = op.args.entity_id
        if entity_id:
            segment.metadata[SPEAKER_ENTITY_KEY] = entity_id
        else:
            segment.metadata.pop(SPEAKER_ENTITY_KEY, None)
        self._mark(segment, row)
        return None, before

    def _set_text(self, op: SegmentSetText, row: TranscriptEdit) -> Tuple[Optional[EditConflict], Dict[str, Any]]:
        index, conflict = self._find(op.anchor)
        if conflict is not None:
            return conflict, {}
        segment = self.segments[index]
        before = {"text": segment.text}
        segment.text = op.args.new_text
        segment.metadata[WORDS_STALE_KEY] = True
        self._mark(segment, row)
        return None, before

    def _set_kind(self, op: SegmentSetKind, row: TranscriptEdit) -> Tuple[Optional[EditConflict], Dict[str, Any]]:
        index, conflict = self._find(op.anchor)
        if conflict is not None:
            return conflict, {}
        segment = self.segments[index]
        before = {"kind": segment.kind}
        was_speech = segment.kind in SPEECH_KINDS
        segment.kind = op.args.kind
        label = self.labels[index]
        if not was_speech and op.args.kind in SPEECH_KINDS and label and label in self.touched_labels:
            # The row sat out the rename or merge of its voice; it takes the
            # label's current name now that it speaks.
            segment.speaker = self._display_name(label)
            entity_id = self.label_entities.get(label)
            if entity_id:
                segment.metadata[SPEAKER_ENTITY_KEY] = entity_id
            else:
                segment.metadata.pop(SPEAKER_ENTITY_KEY, None)
        self._mark(segment, row)
        return None, before

    def _split(self, op: SegmentSplit, row: TranscriptEdit) -> Tuple[Optional[EditConflict], Dict[str, Any]]:
        index, conflict = self._find(op.anchor)
        if conflict is not None:
            return conflict, {}
        segment = self.segments[index]
        words = segment.text.split()
        k = op.args.text_word_index
        if k >= len(words):
            return EditConflict("", 0, "", "invalid", {"error": "split index past the last word"}), {}
        before = {"segment": _segment_snapshot(segment)}
        at = op.args.at or raw_word_ref_for_split(segment, k, self.raw_by_id, self.raw_order)
        boundary = self._boundary_time(segment, at, k, len(words))
        left_ids, right_ids, left_span, right_span = self._split_anchors(segment, at)

        left = segment.model_copy(deep=True)
        left.text = " ".join(words[:k])
        left.end = boundary
        left.source_segment_ids = left_ids
        left.source_word_span = left_span
        right = segment.model_copy(deep=True)
        right.text = " ".join(words[k:])
        right.start = boundary
        right.source_segment_ids = right_ids
        right.source_word_span = right_span
        right.user_segment_id = None
        for child, part in enumerate((left, right)):
            part.metadata = dict(segment.metadata)
            if left_span is right_span and left_span is not None:
                # No word addressing: both halves still name the parent's
                # whole span, so neither can drive a karaoke wipe.
                part.metadata[WORDS_STALE_KEY] = True
            self._mark(part, row, child)
        self.segments[index : index + 1] = [left, right]
        self.labels[index : index + 1] = [self.labels[index], self.labels[index]]
        return None, before

    def _boundary_time(self, segment: AnnotatedSegment, at: Optional[RawWordRef], k: int, n: int) -> float:
        if at is not None:
            raw = self.raw_by_id.get(at.raw_segment_id)
            if raw is not None and 0 <= at.word_index < len(raw.words):
                start = raw.words[at.word_index].start
                if start is not None:
                    return min(max(float(start), segment.start), segment.end)
        return segment.start + (segment.end - segment.start) * (k / n)

    def _split_anchors(
        self, segment: AnnotatedSegment, at: Optional[RawWordRef]
    ) -> Tuple[List[int], List[int], Optional[WordSpan], Optional[WordSpan]]:
        span = segment.source_word_span
        ids = list(segment.source_segment_ids)
        if at is None or span is None or at.raw_segment_id not in self.raw_order:
            # No word addressing: both children keep the whole anchor and
            # are told apart by their text hashes on later ops.
            return ids, ids, span, span
        cut = self.raw_order[at.raw_segment_id]
        # A cut on a raw segment's first word leaves that segment wholly on
        # the right.
        left_last = cut if at.word_index > 0 else cut - 1
        left_ids = [sid for sid in ids if self.raw_order.get(sid, cut) <= left_last] or ids[:1]
        right_ids = [sid for sid in ids if self.raw_order.get(sid, cut) >= cut] or ids[-1:]
        if at.word_index > 0:
            left_span = WordSpan(
                start_segment_id=span.start_segment_id,
                start_word_index=span.start_word_index,
                end_segment_id=at.raw_segment_id,
                end_word_index=at.word_index - 1,
            )
        else:
            prev = self._previous_word(at)
            left_span = (
                WordSpan(
                    start_segment_id=span.start_segment_id,
                    start_word_index=span.start_word_index,
                    end_segment_id=prev.raw_segment_id,
                    end_word_index=prev.word_index,
                )
                if prev
                else None
            )
        right_span = WordSpan(
            start_segment_id=at.raw_segment_id,
            start_word_index=at.word_index,
            end_segment_id=span.end_segment_id,
            end_word_index=span.end_word_index,
        )
        return left_ids, right_ids, left_span, right_span

    def _previous_word(self, at: RawWordRef) -> Optional[RawWordRef]:
        order = self.raw_order.get(at.raw_segment_id)
        if order is None:
            return None
        ordered = sorted(self.raw_order, key=self.raw_order.__getitem__)
        for sid in reversed(ordered[:order]):
            words = self.raw_by_id[sid].words
            if words:
                return RawWordRef(raw_segment_id=sid, word_index=len(words) - 1)
        return None

    def _merge(self, op: SegmentMerge, row: TranscriptEdit) -> Tuple[Optional[EditConflict], Dict[str, Any]]:
        first, conflict = self._find(op.anchor.first)
        if conflict is not None:
            return conflict, {}
        second, conflict = self._find(op.anchor.second)
        if conflict is not None:
            return conflict, {}
        if second != first + 1:
            return EditConflict("", 0, "", "not_adjacent", {"first": first, "second": second}), {}
        a, b = self.segments[first], self.segments[second]
        before = {"segments": [_segment_snapshot(a), _segment_snapshot(b)]}
        merged = a.model_copy(deep=True)
        merged.text = f"{a.text} {b.text}".strip()
        merged.end = b.end
        merged.source_segment_ids = list(a.source_segment_ids) + [
            sid for sid in b.source_segment_ids if sid not in a.source_segment_ids
        ]
        if a.source_word_span and b.source_word_span:
            merged.source_word_span = WordSpan(
                start_segment_id=a.source_word_span.start_segment_id,
                start_word_index=a.source_word_span.start_word_index,
                end_segment_id=b.source_word_span.end_segment_id,
                end_word_index=b.source_word_span.end_word_index,
            )
        else:
            merged.source_word_span = None
        merged.metadata = dict(a.metadata)
        if a.metadata.get(WORDS_STALE_KEY) or b.metadata.get(WORDS_STALE_KEY):
            merged.metadata[WORDS_STALE_KEY] = True
        self._mark(merged, row)
        self.segments[first : second + 1] = [merged]
        self.labels[first : second + 1] = [self.labels[first]]
        return None, before

    # -- result --------------------------------------------------------------

    def replay(self, rows: List[TranscriptEdit]) -> EffectiveTranscript:
        live, reverted = live_edits(rows)
        applied: List[TranscriptEdit] = []
        conflicts: List[EditConflict] = []
        for row in live:
            conflict, _before = self.apply(row)
            if conflict is None:
                applied.append(row)
            else:
                conflicts.append(conflict)
        return self.finish(rows, applied, conflicts, reverted)

    def finish(
        self,
        rows: List[TranscriptEdit],
        applied: List[TranscriptEdit],
        conflicts: List[EditConflict],
        reverted: frozenset,
    ) -> EffectiveTranscript:
        transcript = self.transcript.model_copy(update={"segments": self.segments})
        if self.structural_seq is not None:
            # Ids are positional; a split or merge moved them, so citations
            # stop trusting their ``segment_id_hint`` for this version (§3).
            for i, segment in enumerate(self.segments):
                segment.id = i
            transcript.algorithm_version = f"{self.transcript.algorithm_version}+edits.{self.structural_seq}"
        labels_by_segment: Dict[int, str] = {}
        speakers: "OrderedDict[str, SpeakerLabel]" = OrderedDict()
        for segment, label in zip(self.segments, self.labels):
            if not label or segment.kind not in SPEECH_KINDS:
                continue
            labels_by_segment[segment.id] = label
            entry = speakers.get(label)
            if entry is None:
                entry = SpeakerLabel(
                    label=label,
                    name=self.label_names.get(label),
                    entity_id=self.label_entities.get(label),
                    segment_ids=[],
                )
                speakers[label] = entry
            entry.segment_ids.append(segment.id)
        return EffectiveTranscript(
            transcript=transcript,
            edits=list(rows),
            applied=applied,
            conflicts=conflicts,
            reverted_ids=reverted,
            last_seq=rows[-1].seq if rows else 0,
            raw_sha256=self.raw_hash,
            speakers=list(speakers.values()),
            labels_by_segment=labels_by_segment,
        )


def raw_word_ref_for_split(
    segment: AnnotatedSegment,
    text_word_index: int,
    raw_by_id: Dict[int, RawSegment],
    raw_order: Dict[int, int],
) -> Optional[RawWordRef]:
    """The raw word a split before cleaned word ``text_word_index`` lands on.

    Cleaned text and raw words differ in count (the cleaner rewrites), so
    the position is mapped proportionally across the segment's word span.
    ``None`` when the segment has no usable span.
    """
    span = segment.source_word_span
    if span is None or span.start_segment_id not in raw_order or span.end_segment_id not in raw_order:
        return None
    refs: List[RawWordRef] = []
    for sid in sorted(raw_order, key=raw_order.__getitem__):
        if raw_order[sid] < raw_order[span.start_segment_id] or raw_order[sid] > raw_order[span.end_segment_id]:
            continue
        words = raw_by_id[sid].words
        first = span.start_word_index if sid == span.start_segment_id else 0
        last = span.end_word_index if sid == span.end_segment_id else len(words) - 1
        for index in range(first, min(last, len(words) - 1) + 1):
            refs.append(RawWordRef(raw_segment_id=sid, word_index=index))
    if len(refs) < 2:
        return None
    n = max(len(segment.text.split()), 1)
    position = round(text_word_index * len(refs) / n)
    return refs[min(max(position, 1), len(refs) - 1)]


def unnamed_content_segments(transcript: AnnotatedTranscript) -> int:
    """Content segments a reader sees with no real speaker name (success metric)."""
    return sum(1 for s in transcript.segments if s.kind == "content" and is_placeholder_speaker(s.speaker))


def segment_kinds() -> Tuple[SegmentKind, ...]:
    return ("content", "filler", "ad_break", "music", "intro", "outro")
