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

"""The summary's Best Quotes as public quotes (spec #120).

The summarizer picks 5-7 quotes per episode that "actually land" and writes
them as ``* "Quote text..." - Speaker [mm:ss](?t=…&cite=cN)``. This module
turns that section into quotes a public page may show, and refuses any it
cannot stand behind:

1. **The words are in the transcript.** Each quote is looked up near the
   segment its citation resolved to. The summarizer trims with ``...`` and
   sometimes tidies a word or two, so the check is per fragment and allows
   small differences, but a quote that is not substantially there is
   dropped: it may be paraphrased or invented.
2. **The speaker is who the summary says.** The segment where the words
   were found must have a resolved speaker (its entity ``speaking`` row),
   and the summary's name must be that person's. A disagreement, an
   unnamed speaker, or a name the summary made up ("Anonymous HR Manager
   (via George Kurtz)") drops the quote: a wrong name on a public quote is
   the worst failure these pages can have (spec #100).

Everything here is pure: the caller loads the summary, its citations, the
transcript and the episode's speaking rows.
"""

from __future__ import annotations

import difflib
import re
import unicodedata
from bisect import bisect_left
from dataclasses import dataclass
from typing import Dict, List, Mapping, Optional, Sequence, Tuple
from urllib.parse import parse_qs

from thestill.models.annotated_transcript import AnnotatedSegment

#: A summary section whose heading names quotes ("💬 Best Quotes", "Notable Quotes").
_QUOTES_HEADING_RE = re.compile(r"^#{1,3}\s+.*\bquotes?\b", re.IGNORECASE)
_ANY_HEADING_RE = re.compile(r"^#{1,3}\s+")
#: ``* "text" - Speaker [label](dest)``; the label link is optional, and the
#: quote marks may be straight or curly, the dash a hyphen, en or em dash.
_BULLET_RE = re.compile(
    r"""^\s*[*\-+]\s+(?:\*\*)?\s*["“](?P<text>.+?)["”]\s*(?:\*\*)?\s*"""
    r"""[-–—]\s*(?P<speaker>[^\[\n]+?)\s*(?P<cite>\[[^\]]*\](?:\([^)]*\))?)?\s*$"""
)
_LINK_DEST_RE = re.compile(r"\]\(\?(?P<query>[^)]*)\)")
_ELLIPSIS_RE = re.compile(r"\.\.\.|…")
_EDITORIAL_RE = re.compile(r"\[[^\]]*\]")
_PAREN_RE = re.compile(r"\s*\([^)]*\)")
#: The summarizer's attributions that name no one, or several people.
_NOT_A_NAME_RE = re.compile(r"\b(anonymous|unknown|unnamed|host|guest|speaker|narrator|both|all)\b|&|\band\b|,|/", re.I)

#: How far around the cited segment the words are looked for: the label
#: can fall mid-quote, and a quote can run into the next segment.
SEGMENTS_BEFORE = 1
SEGMENTS_AFTER = 2
#: The share of a fragment's words that must be found, in order, in the
#: transcript. Measured on local summaries (spec #120 §2): verbatim quotes
#: score 1.0, a tidied word or two about 0.9, paraphrases well under 0.7.
MIN_FRAGMENT_COVERAGE = 0.85
#: A fragment shorter than this many words is not checked on its own (an
#: ellipsis can leave "and" or "so" behind), but counts toward the total.
MIN_FRAGMENT_WORDS = 3
#: The longest quote shown: the same limit as a quote card (spec #114 §1).
MAX_QUOTE_CHARS = 320
#: Inside a long turn the start is estimated from word position; playback
#: starts this much earlier, so it never cuts into the first word.
START_LEAD_SECONDS = 2.0


@dataclass(frozen=True)
class BestQuote:
    """One bullet of the summary's quotes section, as written."""

    text: str
    speaker: str
    cite_id: Optional[str]
    #: The label's seek time (playback seconds) from the link, when present.
    label_seconds: Optional[float]


@dataclass(frozen=True)
class SpeakerRef:
    """A segment's resolved speaker: the entity its ``speaking`` row names."""

    entity_id: str
    name: str


@dataclass(frozen=True)
class VerifiedQuote:
    """A summary quote that passed both checks."""

    text: str
    speaker: SpeakerRef
    segment_id: int
    #: Where the words start, in playback milliseconds (what ``#t=`` seeks).
    start_ms: int
    #: Where the segment the words end in ends, in playback milliseconds.
    end_ms: int
    #: How much of the quote was found, 0-1 (1.0 is verbatim).
    coverage: float


@dataclass(frozen=True)
class Rejection:
    """A summary quote that was not shown, and why (for logs and evals)."""

    quote: BestQuote
    reason: str


def parse_best_quotes(markdown: str) -> List[BestQuote]:
    """The bullets of the first quotes section, in the summary's order."""
    out: List[BestQuote] = []
    inside = False
    for line in markdown.split("\n"):
        if _ANY_HEADING_RE.match(line):
            if inside:
                break
            inside = bool(_QUOTES_HEADING_RE.match(line))
            continue
        if not inside:
            continue
        match = _BULLET_RE.match(line)
        if not match:
            continue
        text = match.group("text").strip()
        speaker = match.group("speaker").strip().rstrip("*:,;").strip()
        cite_id, label_seconds = _citation(match.group("cite") or "")
        if text and speaker:
            out.append(BestQuote(text=text, speaker=speaker, cite_id=cite_id, label_seconds=label_seconds))
    return out


def _citation(link: str) -> Tuple[Optional[str], Optional[float]]:
    dest = _LINK_DEST_RE.search(link)
    if not dest:
        return None, None
    query = parse_qs(dest.group("query"))
    cite = (query.get("cite") or [None])[0]
    try:
        seconds: Optional[float] = float((query.get("t") or [""])[0])
    except ValueError:
        seconds = None
    return cite, seconds


def normalize_words(text: str) -> List[str]:
    """Lower-case words with punctuation, accents and apostrophes folded."""
    text = unicodedata.normalize("NFKD", text)
    text = "".join(ch for ch in text if not unicodedata.combining(ch))
    text = text.lower().replace("’", "'").replace("'", "")
    return re.findall(r"[a-z0-9]+", text)


def _coverage(fragment: Sequence[str], window: Sequence[str]) -> Tuple[float, int]:
    """The share of ``fragment``'s words found in order in ``window``, and
    the window index where the first matched word is."""
    if not fragment:
        return 1.0, 0
    matcher = difflib.SequenceMatcher(None, list(fragment), list(window), autojunk=False)
    blocks = [block for block in matcher.get_matching_blocks() if block.size]
    matched = sum(block.size for block in blocks)
    return matched / len(fragment), (blocks[0].b if blocks else 0)


def _fragments(text: str) -> List[List[str]]:
    """The quote's runs of words as spoken: split at each ellipsis, with
    editorial insertions ("[to play soccer]") left out, since the speaker
    never said them."""
    spoken = _EDITORIAL_RE.sub(" ", text)
    return [words for words in (normalize_words(part) for part in _ELLIPSIS_RE.split(spoken)) if words]


@dataclass(frozen=True)
class Located:
    """Where a quote's words are in the transcript."""

    coverage: float
    first: int
    last: int
    #: How far into ``segments[first]`` the words start, 0-1, by word count.
    start_fraction: float


def locate(
    text: str, segments: Sequence[AnnotatedSegment], anchor_index: int, *, before: int = SEGMENTS_BEFORE
) -> Optional[Located]:
    """Find ``text`` around ``segments[anchor_index]`` (from ``before``
    segments earlier), or None when any checkable fragment falls short of
    :data:`MIN_FRAGMENT_COVERAGE`."""
    lo = max(0, anchor_index - before)
    hi = min(len(segments), anchor_index + SEGMENTS_AFTER + 1)
    window: List[str] = []
    owner: List[int] = []
    first_word: Dict[int, int] = {}
    for index in range(lo, hi):
        words = normalize_words(segments[index].text)
        first_word[index] = len(window)
        window.extend(words)
        owner.extend([index] * len(words))
    if not window:
        return None
    fragments = _fragments(text)
    if not fragments:
        return None
    total = sum(len(fragment) for fragment in fragments)
    found = 0.0
    first_at: Optional[int] = None
    last: Optional[int] = None
    for fragment in fragments:
        share, at = _coverage(fragment, window)
        if len(fragment) >= MIN_FRAGMENT_WORDS and share < MIN_FRAGMENT_COVERAGE:
            return None
        found += share * len(fragment)
        if share > 0:
            if first_at is None:
                first_at = at
            last = owner[min(len(owner) - 1, at + len(fragment) - 1)]
    coverage = found / total
    if coverage < MIN_FRAGMENT_COVERAGE or first_at is None or last is None:
        return None
    first = owner[first_at]
    in_segment = len(normalize_words(segments[first].text)) or 1
    return Located(coverage, first, last, (first_at - first_word[first]) / in_segment)


class TranscriptIndex:
    """Every content word of a transcript, for finding a quote whose
    citation points at the wrong place (the summarizer's timestamp is off,
    or it gave none)."""

    #: The shortest exact run of words that may place a quote on its own.
    MIN_ANCHOR_WORDS = 5

    def __init__(self, segments: Sequence[AnnotatedSegment]) -> None:
        self._starts: List[int] = []
        self._owner: List[int] = []
        parts: List[str] = []
        offset = 0
        for index, segment in enumerate(segments):
            for word in normalize_words(segment.text):
                self._starts.append(offset)
                self._owner.append(index)
                parts.append(word)
                offset += len(word) + 1
        self._joined = " ".join(parts)

    def anchors(self, text: str) -> List[int]:
        """Segment indices where the quote's longest fragment occurs word
        for word, in transcript order (at most a few)."""
        longest = max(_fragments(text), key=len, default=[])
        if len(longest) < self.MIN_ANCHOR_WORDS:
            return []
        needle = " ".join(longest)
        found: List[int] = []
        at = self._joined.find(needle)
        while at != -1 and len(found) < 3:
            # Only a match that starts on a word boundary.
            word = bisect_left(self._starts, at)
            if word < len(self._starts) and self._starts[word] == at:
                found.append(self._owner[word])
            at = self._joined.find(needle, at + 1)
        return found


def _name_tokens(name: str) -> List[str]:
    return normalize_words(_PAREN_RE.sub("", name))


def names_match(summary_name: str, entity_name: str) -> bool:
    """Whether the summary's attribution names this person.

    The full name, or one of its words when the summary uses only a first
    name or a surname ("George", "Kurtz"); a parenthetical ("Jason
    (host)") is ignored. Never true for attributions that name no one.
    """
    if _NOT_A_NAME_RE.search(_PAREN_RE.sub("", summary_name)):
        return False
    said = _name_tokens(summary_name)
    full = _name_tokens(entity_name)
    if not said or not full:
        return False
    if said == full:
        return True
    if len(said) == 1:
        return said[0] in (full[0], full[-1]) and len(said[0]) >= 3
    # "Dr. Jane Smith", "Jane Smith Jr." and the like: every word of the
    # entity's name is in the summary's, in order, and nothing more than a
    # title or suffix around it.
    joined = " ".join(said)
    return " ".join(full) in joined and len(said) - len(full) <= 1


def _estimated_start(segment: AnnotatedSegment, fraction: float) -> float:
    """Where in ``segment`` the quote starts, in transcript seconds: the
    share of its words before the quote, of its duration, less a small lead
    so playback starts just before the words rather than after them. A
    quote at the top of a turn starts with the turn."""
    if fraction <= 0:
        return segment.start
    estimate = segment.start + (segment.end - segment.start) * fraction - START_LEAD_SECONDS
    return max(segment.start, estimate)


def verify_quotes(
    quotes: Sequence[BestQuote],
    *,
    citations: Mapping[str, Mapping[str, object]],
    segments: Sequence[AnnotatedSegment],
    speakers: Mapping[int, SpeakerRef],
    playback_offset_s: float = 0.0,
) -> Tuple[List[VerifiedQuote], List[Rejection]]:
    """The quotes that pass both checks, in the summary's order, and why
    each of the others did not.

    ``citations`` are the summary's valid citations by id (as served to the
    signed-in reader); ``segments`` the effective transcript's segments in
    order; ``speakers`` each segment id's resolved, public speaker.
    """
    index_of: Dict[int, int] = {segment.id: i for i, segment in enumerate(segments)}
    transcript_index: Optional[TranscriptIndex] = None
    kept: List[VerifiedQuote] = []
    dropped: List[Rejection] = []
    seen: set = set()
    for quote in quotes:
        if len(quote.text) > MAX_QUOTE_CHARS:
            dropped.append(Rejection(quote, "too_long"))
            continue
        citation = citations.get(quote.cite_id or "")
        hint = citation.get("segment_id_hint") if citation else None
        anchor = index_of.get(hint) if isinstance(hint, int) else None
        found = locate(quote.text, segments, anchor) if anchor is not None else None
        if found is None or segments[found.first].kind != "content":
            # The words may be elsewhere: the label was off or missing, or
            # it points at a cold open that previews the line said later.
            if transcript_index is None:
                transcript_index = TranscriptIndex(segments)
            for elsewhere in transcript_index.anchors(quote.text):
                # An exact match starts here: look no earlier, or a
                # preview of the same line just before would win again.
                again = locate(quote.text, segments, elsewhere, before=0)
                if again is not None and segments[again.first].kind == "content":
                    found = again
                    break
        if found is None:
            dropped.append(Rejection(quote, "not_in_transcript" if citation else "no_citation"))
            continue
        segment = segments[found.first]
        if segment.kind != "content":
            dropped.append(Rejection(quote, "not_content"))
            continue
        speaker = speakers.get(segment.id)
        if speaker is None:
            dropped.append(Rejection(quote, "speaker_unresolved"))
            continue
        if not names_match(quote.speaker, speaker.name):
            dropped.append(Rejection(quote, "speaker_mismatch"))
            continue
        start_ms = int(round((_estimated_start(segment, found.start_fraction) + playback_offset_s) * 1000))
        if (start_ms, quote.text) in seen:
            continue
        seen.add((start_ms, quote.text))
        kept.append(
            VerifiedQuote(
                text=quote.text,
                speaker=speaker,
                segment_id=segment.id,
                start_ms=start_ms,
                end_ms=int(round((segments[found.last].end + playback_offset_s) * 1000)),
                coverage=round(found.coverage, 3),
            )
        )
    return kept, dropped
