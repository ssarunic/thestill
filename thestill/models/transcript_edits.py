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

"""Transcript edit operations and the op-log row (spec #110 §2).

Every admin correction is one row in the append-only ``transcript_edits``
table: an ``op`` from the closed list below, the durable anchor it applies
to, its arguments and a snapshot of what it replaced. The models here
validate ``(op, anchor, args)`` at write time; replaying them onto the
cleaned sidecar lives in :mod:`thestill.core.transcript_edits`.

A segment anchor is located by the raw ``source_segment_ids`` spec #18
put on every cleaned segment plus the ``source_word_span`` (which tells
the children of a split apart) and the SHA-256 of the segment text as
the editor showed it. The API client sends the text itself; the anchor
hashes it on validation so a row never stores transcript text twice.
"""

from __future__ import annotations

import hashlib
from datetime import datetime
from typing import Annotated, Any, Dict, List, Literal, Optional, Union

from pydantic import BaseModel, Field, TypeAdapter, model_validator

from .annotated_transcript import SegmentKind, WordSpan

#: The ops a row may carry. ``segment.adjust_boundary`` is reserved for
#: Phase 4 and is refused at write time until then.
EDIT_OPS = (
    "speaker.rename_label",
    "speaker.merge_labels",
    "segment.set_speaker",
    "segment.set_text",
    "segment.set_kind",
    "segment.split",
    "segment.merge",
    "revert",
)

#: Ops that move segment ids, so the effective transcript's
#: ``algorithm_version`` gains an ``+edits.<seq>`` suffix (§3).
STRUCTURAL_OPS = frozenset({"segment.split", "segment.merge", "segment.adjust_boundary"})

#: Ops whose anchor is a diarization label rather than a segment.
LABEL_OPS = frozenset({"speaker.rename_label", "speaker.merge_labels"})

MAX_SEGMENT_TEXT_CHARS = 20_000
MAX_NAME_CHARS = 200


def text_sha256(text: str) -> str:
    """The hash an anchor records for a segment's text, exactly as shown."""
    return hashlib.sha256(text.encode("utf-8")).hexdigest()


class SegmentAnchor(BaseModel):
    """Where a segment op applies (§2 "Segment anchor")."""

    source_segment_ids: List[int] = Field(min_length=1)
    source_word_span: Optional[WordSpan] = None
    start_s: float
    end_s: float
    text_sha256: str = Field(pattern=r"^[0-9a-f]{64}$")

    @model_validator(mode="before")
    @classmethod
    def _hash_text(cls, data: Any) -> Any:
        # The editor sends the text it showed; the row stores its hash.
        if isinstance(data, dict) and "text" in data and "text_sha256" not in data:
            data = dict(data)
            data["text_sha256"] = text_sha256(str(data.pop("text")))
        return data


class LabelAnchor(BaseModel):
    label: str = Field(min_length=1, max_length=MAX_NAME_CHARS)


class MergeAnchor(BaseModel):
    """Two adjacent segments; ``first`` comes before ``second``."""

    first: SegmentAnchor
    second: SegmentAnchor


class EditAnchor(BaseModel):
    edit_id: str = Field(min_length=1)


class RawWordRef(BaseModel):
    """A word in the raw transcript, addressed positionally (spec #18)."""

    raw_segment_id: int
    word_index: int = Field(ge=0)


class RenameLabelArgs(BaseModel):
    name: str = Field(min_length=1, max_length=MAX_NAME_CHARS)
    entity_id: Optional[str] = Field(default=None, max_length=MAX_NAME_CHARS)

    @model_validator(mode="after")
    def _strip(self) -> "RenameLabelArgs":
        self.name = self.name.strip()
        if not self.name:
            raise ValueError("name must not be blank")
        return self


class MergeLabelsArgs(BaseModel):
    into_label: str = Field(min_length=1, max_length=MAX_NAME_CHARS)


class SetSpeakerArgs(BaseModel):
    """Either another label of this episode, or a name (with an optional
    person entity) that is not a diarization label at all."""

    label: Optional[str] = Field(default=None, max_length=MAX_NAME_CHARS)
    name: Optional[str] = Field(default=None, max_length=MAX_NAME_CHARS)
    entity_id: Optional[str] = Field(default=None, max_length=MAX_NAME_CHARS)

    @model_validator(mode="after")
    def _one_of(self) -> "SetSpeakerArgs":
        if self.name is not None:
            self.name = self.name.strip() or None
        if bool(self.label) == bool(self.name):
            raise ValueError("pass exactly one of label or name")
        return self


class SetTextArgs(BaseModel):
    old_text: str = Field(max_length=MAX_SEGMENT_TEXT_CHARS)
    new_text: str = Field(min_length=1, max_length=MAX_SEGMENT_TEXT_CHARS)

    @model_validator(mode="after")
    def _not_blank(self) -> "SetTextArgs":
        if not self.new_text.strip():
            raise ValueError("new_text must not be blank")
        return self


class SetKindArgs(BaseModel):
    kind: SegmentKind


class SplitArgs(BaseModel):
    """Split before the ``text_word_index``-th whitespace word of the
    cleaned text. ``at`` is the matching raw word, filled in by the
    service from the segment's word span so a replay lands on the same
    audio boundary; the client never has to compute it."""

    text_word_index: int = Field(ge=1)
    at: Optional[RawWordRef] = None


class NoArgs(BaseModel):
    pass


class SpeakerRenameLabel(BaseModel):
    op: Literal["speaker.rename_label"]
    anchor: LabelAnchor
    args: RenameLabelArgs


class SpeakerMergeLabels(BaseModel):
    op: Literal["speaker.merge_labels"]
    anchor: LabelAnchor
    args: MergeLabelsArgs

    @model_validator(mode="after")
    def _distinct(self) -> "SpeakerMergeLabels":
        if self.anchor.label == self.args.into_label:
            raise ValueError("cannot merge a label into itself")
        return self


class SegmentSetSpeaker(BaseModel):
    op: Literal["segment.set_speaker"]
    anchor: SegmentAnchor
    args: SetSpeakerArgs


class SegmentSetText(BaseModel):
    op: Literal["segment.set_text"]
    anchor: SegmentAnchor
    args: SetTextArgs


class SegmentSetKind(BaseModel):
    op: Literal["segment.set_kind"]
    anchor: SegmentAnchor
    args: SetKindArgs


class SegmentSplit(BaseModel):
    op: Literal["segment.split"]
    anchor: SegmentAnchor
    args: SplitArgs


class SegmentMerge(BaseModel):
    op: Literal["segment.merge"]
    anchor: MergeAnchor
    args: NoArgs = Field(default_factory=NoArgs)


class Revert(BaseModel):
    op: Literal["revert"]
    anchor: EditAnchor
    args: NoArgs = Field(default_factory=NoArgs)


EditOp = Annotated[
    Union[
        SpeakerRenameLabel,
        SpeakerMergeLabels,
        SegmentSetSpeaker,
        SegmentSetText,
        SegmentSetKind,
        SegmentSplit,
        SegmentMerge,
        Revert,
    ],
    Field(discriminator="op"),
]

_OP_ADAPTER: TypeAdapter = TypeAdapter(EditOp)


def parse_op(payload: Dict[str, Any]) -> EditOp:
    """Validate one ``{op, anchor, args}`` dict; raises ``pydantic.ValidationError``."""
    return _OP_ADAPTER.validate_python(payload)


class TranscriptEdit(BaseModel):
    """One row of ``transcript_edits``. Rows are never updated or deleted
    except by the episode cascade; undo is a new row with ``reverts``."""

    id: str
    episode_id: str
    seq: int
    op: str
    raw_sha256: str
    anchor: Dict[str, Any]
    args: Dict[str, Any]
    before: Dict[str, Any] = Field(default_factory=dict)
    reverts: Optional[str] = None
    session_id: str
    report_id: Optional[str] = None
    actor_user_id: Optional[str] = None
    actor_kind: str
    created_at: datetime

    def parsed(self) -> EditOp:
        return parse_op({"op": self.op, "anchor": self.anchor, "args": self.args})

    @property
    def is_structural(self) -> bool:
        return self.op in STRUCTURAL_OPS
