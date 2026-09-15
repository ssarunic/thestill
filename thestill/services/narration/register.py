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

"""Deterministic register metrics over a narration script (spec #77 Phase 2b).

Everything here is computed from the block list alone so the same numbers
land in the run stats, the JSON header and the A/B script. The two
metrics that need judgement (ideas per episode, stakes lines) live in
``scripts/narration_ab.py`` with the LLM judge.
"""

import re
from dataclasses import dataclass, field
from typing import Dict, List, Optional, Sequence

from .models import ScriptBlock, ThemePlan

_SENTENCE_SPLIT = re.compile(r"(?<=[.!?])\s+")
_FIRST_PERSON = re.compile(r"\b(I|I'm|I’m|I'd|I’d|I've|I’ve|I'll|I’ll|me|my|myself)\b")
# The listener being addressed: the "why you'd care" line is written to "you".
_LISTENER = re.compile(r"\b(you|your|you'd|you’d|you're|you’re|you'll|you’ll|yours)\b", re.IGNORECASE)
_REPORTAGE = re.compile(
    r"\b(he|she|they|the (?:host|guest)s?)\s+(?:told|explained|discussed|described|argued|said that|"
    r"talked about|walked through|mentioned|noted)\b|\bexplained how\b|\baccording to\b",
    re.IGNORECASE,
)
# A single-quoted span of up to four words that is not an apostrophe
# inside a word ("don't") or a possessive ("guests' rights").
_SCARE_QUOTE = re.compile(r"(?<!\w)[‘']([^'’\n]{1,40}?)[’'](?!\w)")
_BRIDGE = re.compile(
    r"exactly what|that same|the same (?:thing|question|energy|idea)|which brings us|speaking of|"
    r"ties into|that shift|this connects|on the same note|similarly|in the same way|"
    r"is exactly what|that'?s what .* was",
    re.IGNORECASE,
)
_PERMITTED = re.compile(r"^(?:completely different thing|okay,|ok,|meanwhile|right,|next up)", re.IGNORECASE)
_NAMED_RELATIONSHIP = re.compile(
    r"disagree|agree|contradict|builds? on|pushes? back|takes? the opposite|same question|both .* say",
    re.IGNORECASE,
)


@dataclass(frozen=True)
class RegisterMetrics:
    sentences: int = 0
    first_person_sentences: int = 0
    reportage_sentences: int = 0
    scare_quotes: int = 0
    sentence_len_p50: float = 0.0
    sentence_len_p90: float = 0.0
    clips: int = 0
    clips_with_reaction: int = 0
    segments: int = 0
    segments_with_first_person: int = 0
    transitions: Dict[str, int] = field(default_factory=dict)
    # Bridges into a segment the clusterer did not type as related.
    bridges_unearned: int = 0

    @property
    def first_person_ratio(self) -> float:
        return self.first_person_sentences / self.sentences if self.sentences else 0.0

    @property
    def reportage_ratio(self) -> float:
        return self.reportage_sentences / self.sentences if self.sentences else 0.0


def split_sentences(text: str) -> List[str]:
    return [s.strip() for s in _SENTENCE_SPLIT.split(text.strip()) if s.strip()]


def has_first_person(text: str) -> bool:
    return bool(_FIRST_PERSON.search(text or ""))


def addresses_listener(text: str) -> bool:
    return bool(_LISTENER.search(text or ""))


def unquote_scare_quotes(text: str) -> str:
    """Drop single quotes around short spans: own the phrase or lose the quotes."""
    return _SCARE_QUOTE.sub(lambda m: m.group(1) if len(m.group(1).split()) <= 4 else m.group(0), text or "")


def _percentile(values: Sequence[int], pct: float) -> float:
    if not values:
        return 0.0
    ordered = sorted(values)
    idx = min(len(ordered) - 1, max(0, int(round((len(ordered) - 1) * pct))))
    return float(ordered[idx])


def classify_transition(first_sentence: str) -> str:
    """``bridge`` / ``permitted`` / ``named_relationship`` / ``hard_cut`` for a segment's first line."""
    text = first_sentence.strip()
    if _PERMITTED.match(text):
        return "permitted"
    if _NAMED_RELATIONSHIP.search(text):
        return "named_relationship"
    if _BRIDGE.search(text):
        return "bridge"
    return "hard_cut"


def measure_register(blocks: Sequence[ScriptBlock], plan: Optional[ThemePlan] = None) -> RegisterMetrics:
    spoken = [b for b in blocks if b.kind in ("narration", "reaction") and b.text]
    sentences = [s for b in spoken for s in split_sentences(b.text or "")]
    lengths = [len(s.split()) for s in sentences]

    clips = [i for i, b in enumerate(blocks) if b.kind == "quote"]
    with_reaction = sum(
        1 for i in clips if i + 1 < len(blocks) and blocks[i + 1].kind == "reaction" and blocks[i + 1].text
    )

    # Per-segment first-person presence and the opening line of each
    # segment after the first (the seam between shows).
    sections: Dict[str, List[ScriptBlock]] = {}
    for b in spoken:
        if b.section.startswith("segment-"):
            sections.setdefault(b.section, []).append(b)
    relationships = {f"segment-{s.rank}": s.relationship for s in plan.segments} if plan else {}
    seg_fp = 0
    transitions: Dict[str, int] = {}
    unearned = 0
    for name in sorted(sections, key=lambda n: int(n.split("-")[1])):
        text = " ".join(b.text or "" for b in sections[name])
        if _FIRST_PERSON.search(text):
            seg_fp += 1
        if name == "segment-1":
            continue
        first = split_sentences(sections[name][0].text or "")
        kind = classify_transition(first[0]) if first else "hard_cut"
        transitions[kind] = transitions.get(kind, 0) + 1
        if kind == "bridge" and relationships.get(name, "none") == "none":
            unearned += 1

    return RegisterMetrics(
        sentences=len(sentences),
        first_person_sentences=sum(1 for s in sentences if _FIRST_PERSON.search(s)),
        reportage_sentences=sum(1 for s in sentences if _REPORTAGE.search(s)),
        scare_quotes=sum(1 for b in spoken for span in _SCARE_QUOTE.findall(b.text or "") if len(span.split()) <= 4),
        sentence_len_p50=_percentile(lengths, 0.5),
        sentence_len_p90=_percentile(lengths, 0.9),
        clips=len(clips),
        clips_with_reaction=with_reaction,
        segments=len(sections),
        segments_with_first_person=seg_fp,
        transitions=transitions,
        bridges_unearned=unearned,
    )
