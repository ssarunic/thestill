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

"""Spec #92 — the summary's Resource List as entity-extraction input.

Pure functions only: no file, database or network access, and no logging.
``resource_bundle_io`` loads the inputs and reports what happened.

Three steps, each with its own evidence rule:

- **Parse** section 8 into items. Lenient, because every existing summary
  is free-form. A name is never split or shortened here: ``/`` and
  `` by `` fallbacks are only *offered*, and tried when the full name does
  not ground. ``&`` and ``,`` are never split points ("Pride & Prejudice").
- **Admit** an item only when the transcript says its full name. A single
  word must also appear near the segment the item's citation points to.
  First names, surnames and initials never admit anything, so a summary's
  "Paul Kedrosky" is not admitted because some other Paul spoke.
- **Expand** an admitted person to their surname, unless that surname is
  ambiguous in the episode. Expansion only proposes mentions; the linker
  decides identity.
"""

from __future__ import annotations

import re
from dataclasses import dataclass, field
from typing import Dict, FrozenSet, List, Mapping, Optional, Sequence, Tuple

from ..models.annotated_transcript import AnnotatedSegment, AnnotatedTranscript
from .entity_linking.types import surface_key
from .summary_citations import parse_timestamp_label

KINDS = (
    "book",
    "film",
    "tv",
    "podcast",
    "article",
    "paper",
    "person",
    "company",
    "product",
    "tool",
    "place",
    "event",
    "other",
)

# Free-form kinds seen in summaries → the closed list. Matched on the whole
# kind text first, then word by word ("Book by Nir Eyal" → book).
_KIND_SYNONYMS: Dict[str, str] = {
    "book": "book",
    "books": "book",
    "novel": "book",
    "memoir": "book",
    "film": "film",
    "movie": "film",
    "documentary": "film",
    "tv": "tv",
    "tv show": "tv",
    "tv series": "tv",
    "show": "tv",
    "series": "tv",
    "sitcom": "tv",
    "podcast": "podcast",
    "article": "article",
    "essay": "article",
    "newsletter": "article",
    "blog": "article",
    "paper": "paper",
    "study": "paper",
    "person": "person",
    "people": "person",
    "author": "person",
    "guest": "person",
    "investor": "person",
    "host": "person",
    "company": "company",
    "startup": "company",
    "firm": "company",
    "fund": "company",
    "bank": "company",
    "brand": "company",
    "product": "product",
    "app": "product",
    "software": "product",
    "model": "product",
    "device": "product",
    "tool": "tool",
    "tools": "tool",
    "platform": "tool",
    "service": "tool",
    "data": "tool",
    "place": "place",
    "city": "place",
    "country": "place",
    "event": "event",
    "other": "other",
}

# Gloss words that settle a kind-less item's kind. The earliest match in
# the gloss wins: "Stripe's internal data visualization tool" is a tool.
_GLOSS_KIND_WORDS: Dict[str, str] = {
    **{
        w: "person"
        for w in (
            "author",
            "writer",
            "journalist",
            "founder",
            "co-founder",
            "ceo",
            "cfo",
            "cto",
            "investor",
            "professor",
            "economist",
            "scientist",
            "researcher",
            "psychiatrist",
            "psychologist",
            "officer",
            "analyst",
            "actor",
            "actress",
            "director",
            "comedian",
            "politician",
            "senator",
            "president",
            "winner",
            "host",
            "historian",
            "philosopher",
            "entrepreneur",
            "billionaire",
            "artist",
            "musician",
        )
    },
    **{w: "company" for w in ("company", "startup", "firm", "bank", "fund", "brand", "retailer", "lab")},
    **{w: "book" for w in ("book", "novel", "memoir")},
    **{w: "film" for w in ("film", "movie", "documentary")},
    **{w: "tv" for w in ("sitcom", "series", "tv")},
    "podcast": "podcast",
    **{w: "article" for w in ("article", "essay", "newsletter")},
    **{w: "paper" for w in ("paper", "study")},
    **{w: "tool" for w in ("tool", "platform", "service")},
    **{w: "product" for w in ("app", "software", "model", "device", "product")},
}
_GLOSS_KIND_RE = re.compile(
    r"(?<![\w-])(" + "|".join(sorted(map(re.escape, _GLOSS_KIND_WORDS), key=len, reverse=True)) + r")(?![\w-])",
    re.IGNORECASE,
)

# Spec #92 Stage 4: a kind sets only the linker's fallback type; the P31
# rules decide the final one when a QID is found.
KIND_TO_SURFACE_LABEL: Dict[str, str] = {
    "person": "person",
    "company": "company",
    **{k: "product" for k in ("product", "tool", "book", "film", "tv", "podcast", "article", "paper")},
    **{k: "topic" for k in ("place", "event", "other")},
}

DEFAULT_GROUNDING_WINDOW_S = 90.0
MIN_SURNAME_CHARS = 4

_SECTION_RE = re.compile(r"^#{2,3}\s*8\.[^\n]*\n(.*?)(?=^#{2,3}\s*\d+\.|\Z)", re.MULTILINE | re.DOTALL)
_BULLET_RE = re.compile(r"^\s*[*+-]\s+(.+?)\s*$")
_CITE_LINK_RE = re.compile(r"\[([^\]]+)\]\(\?[^)]*?cite=(c\d+)[^)]*\)")
_BARE_LABEL_RE = re.compile(r"\[(\d{1,2}:\d{2}(?::\d{2})?)(?:\s*[-–—]\s*[^\]]*)?\]")
_LABEL_START_RE = re.compile(r"\d{1,2}:\d{2}(?::\d{2})?")
_PAREN_RE = re.compile(r"\s*\(([^()]*)\)")
_GLOSS_SPLIT_RE = re.compile(r":\s+|\s+[—–]\s+|\s+-\s+")
_CONTRACT_RE = re.compile(r"^\*\*[^*]+\*\*\s+\((?P<kind>[a-z]+)\):\s+\S")
# The author must be a full name: "Stand by Me" offers no fallback.
_BY_RE = re.compile(r"^(?P<title>.+?)\s+by\s+(?P<author>[A-Z][\w.'’-]*(?:\s+[A-Z][\w.'’-]*)+)$")
_QUOTE_CHARS = "\"'“”‘’*_` "


class ResourceParseError(ValueError):
    """Section 8 exists but cannot be read; never reported as "no resources"."""


@dataclass(frozen=True)
class ResourceItem:
    """One Resource List bullet.

    ``fallbacks`` are the names grounding may try, in order, when ``name``
    itself is not in the transcript: the parts of an "A / B" item, or the
    title and author of "Title by Author". Each is ``(name, kind)``.
    """

    name: str
    kind: Optional[str]
    gloss: str
    cite_id: Optional[str]
    raw_label: Optional[str]
    contract: bool = False
    fallbacks: Tuple[Tuple[str, Optional[str]], ...] = ()


@dataclass(frozen=True)
class PlanContext:
    """What the episode already knows, identical at extract and at resolve.

    ``anchor_surfaces``: casefolded host/guest/recurring variants.
    ``extracted_names``: ``(surface_form, surface_label)`` of the episode's
    GLiNER mentions — in memory at extract, read back at resolve.
    """

    anchor_surfaces: FrozenSet[str] = frozenset()
    extracted_names: Sequence[Tuple[str, Optional[str]]] = ()


@dataclass(frozen=True)
class GroundedItem:
    item: ResourceItem
    name: str  # the admitted form, as the summary wrote it
    outcome: str  # "near" | "elsewhere"
    kind: Optional[str]
    surface_label: Optional[str]
    scan_surfaces: Tuple[str, ...]
    dropped_surfaces: Tuple[str, ...] = ()


@dataclass(frozen=True)
class ResourceSeed:
    """A surface the extractor scans for, and what it writes when found."""

    surface: str
    surface_label: Optional[str]
    case_sensitive: bool


@dataclass(frozen=True)
class ResourcePlan:
    items: Tuple[ResourceItem, ...]
    grounded: Tuple[GroundedItem, ...]
    dropped: Tuple[Tuple[ResourceItem, str], ...]  # (item, "ungrounded" | "anchor")
    stats: Dict[str, int] = field(default_factory=dict)

    def seeds(self) -> List[ResourceSeed]:
        out: List[ResourceSeed] = []
        for grounded in self.grounded:
            for surface in grounded.scan_surfaces:
                out.append(ResourceSeed(surface, grounded.surface_label, is_single_token(surface)))
        return out

    def hints(self) -> Dict[str, Tuple[str, str]]:
        """surface_key → (kind, gloss) for every scan surface.

        Keyed by the same unambiguous surfaces the extractor scans, so a hint
        never reaches a mention through a dropped surface or a first name.
        """
        out: Dict[str, Tuple[str, str]] = {}
        for grounded in self.grounded:
            if not grounded.kind and not grounded.item.gloss:
                continue
            for surface in grounded.scan_surfaces:
                out.setdefault(surface_key(surface), (grounded.kind or "", grounded.item.gloss))
        return out


# ---------------------------------------------------------------------------
# Parse
# ---------------------------------------------------------------------------


def parse_resource_list(markdown: str) -> List[ResourceItem]:
    """Every item in every ``## 8.`` section, deduplicated by name.

    Chunked summaries repeat all nine sections once per chunk; the first
    occurrence of a name wins, with its cite. Returns ``[]`` when there is
    no section 8 or it has no bullets. A bullet with no name is skipped,
    but a section whose bullets *all* fail raises
    :class:`ResourceParseError` — never an empty result that reads as "no
    resources".
    """
    items: List[ResourceItem] = []
    seen: set = set()
    bullets = resource_bullets(markdown)
    for text in bullets:
        try:
            item = parse_resource_line(text)
        except ResourceParseError:
            continue
        key = surface_key(item.name)
        if key in seen:
            continue
        seen.add(key)
        items.append(item)
    if bullets and not items:
        raise ResourceParseError(f"none of {len(bullets)} resource bullets could be read")
    return items


def resource_bullets(markdown: str) -> List[str]:
    """The text of every bullet in every ``## 8.`` section, in order."""
    out: List[str] = []
    for section in _SECTION_RE.findall(markdown or ""):
        for line in section.splitlines():
            bullet = _BULLET_RE.match(line)
            if bullet:
                out.append(bullet.group(1))
    return out


def parse_resource_line(text: str) -> ResourceItem:
    """One bullet's text (without the bullet marker) → a :class:`ResourceItem`."""
    contract_match = _CONTRACT_RE.match(text)
    contract = bool(contract_match and contract_match.group("kind") in KINDS)

    cite_id: Optional[str] = None
    raw_label: Optional[str] = None
    cite = _CITE_LINK_RE.search(text)
    if cite:
        cite_id = cite.group(2)
        label = _LABEL_START_RE.match(cite.group(1).strip())
        raw_label = label.group(0) if label else None
    text = _CITE_LINK_RE.sub("", text)
    bare = _BARE_LABEL_RE.search(text)
    if bare and raw_label is None:
        raw_label = bare.group(1)
    text = _BARE_LABEL_RE.sub("", text).strip()

    name_part, gloss = _split_name_and_gloss(text)
    kind: Optional[str] = None
    extra_gloss: List[str] = []

    def _take_paren(match: "re.Match[str]") -> str:
        nonlocal kind
        mapped = normalize_kind(match.group(1))
        if mapped is not None and kind is None:
            kind = mapped
        elif match.group(1).strip():
            extra_gloss.append(match.group(1).strip())
        return ""

    name = _PAREN_RE.sub(_take_paren, name_part)
    name = _clean_name(name)
    if not name:
        raise ResourceParseError(f"resource bullet has no name: {text[:80]!r}")
    gloss = _clean_gloss(gloss)
    if extra_gloss:
        gloss = f"{gloss} ({'; '.join(extra_gloss)})".strip()
    return ResourceItem(
        name=name,
        kind=kind,
        gloss=gloss,
        cite_id=cite_id,
        raw_label=raw_label,
        contract=contract,
        fallbacks=_fallbacks(name, kind),
    )


def _split_name_and_gloss(text: str) -> Tuple[str, str]:
    """``**Name:** gloss`` / ``**Name** (kind): gloss`` / ``Name - gloss`` / ``Name``."""
    if text.startswith("**"):
        end = text.find("**", 2)
        if end > 2:
            name = text[2:end]
            rest = text[end + 2 :].strip()
            if name.rstrip().endswith(":"):
                return name.rstrip()[:-1], rest
            paren = re.match(r"^\(([^()]*)\)", rest)
            if paren:
                name = f"{name} ({paren.group(1)})"
                rest = rest[paren.end() :].strip()
            return name, rest.lstrip(":—–- ").strip()
    parts = _GLOSS_SPLIT_RE.split(text, maxsplit=1)
    return (parts[0], parts[1]) if len(parts) == 2 else (text, "")


def _clean_name(name: str) -> str:
    name = " ".join(re.sub(r"[*\"“”]", "", name).split())
    return name.strip(_QUOTE_CHARS).rstrip(":").strip(_QUOTE_CHARS)


def _clean_gloss(gloss: str) -> str:
    return " ".join(gloss.replace("**", "").split()).strip(" .;")


def normalize_kind(text: str) -> Optional[str]:
    """A free-form kind → the closed list, or ``None`` if nothing maps."""
    lowered = " ".join(text.lower().split())
    if lowered in _KIND_SYNONYMS:
        return _KIND_SYNONYMS[lowered]
    for chunk in re.split(r"[/,;]", lowered):
        chunk = chunk.strip()
        if chunk in _KIND_SYNONYMS:
            return _KIND_SYNONYMS[chunk]
        for word in chunk.split():
            if word in _KIND_SYNONYMS:
                return _KIND_SYNONYMS[word]
    return None


def _fallbacks(name: str, kind: Optional[str]) -> Tuple[Tuple[str, Optional[str]], ...]:
    """Names to try only when ``name`` itself does not ground."""
    if " / " in name:
        parts = [p.strip() for p in name.split(" / ")]
        return tuple((p, kind) for p in parts if p)
    by = _BY_RE.match(name)
    if by:
        return ((by.group("title").strip(), kind), (by.group("author").strip(), "person"))
    return ()


def is_contract_line(text: str) -> bool:
    """True when a bullet's text is in the spec #92 contract shape."""
    match = _CONTRACT_RE.match(text)
    return bool(match and match.group("kind") in KINDS)


def infer_kind(item_kind: Optional[str], gloss: str) -> Optional[str]:
    """The item's own kind, else the earliest kind word in its gloss."""
    if item_kind:
        return item_kind
    match = _GLOSS_KIND_RE.search(gloss or "")
    return _GLOSS_KIND_WORDS[match.group(1).lower()] if match else None


# ---------------------------------------------------------------------------
# Admission and expansion
# ---------------------------------------------------------------------------


def is_single_token(surface: str) -> bool:
    return len(surface.split()) == 1


def surface_pattern(surface: str) -> "re.Pattern[str]":
    """Word-bounded; case-insensitive for phrases, exact case for one word
    ("Ramp" the company, not "ramp up")."""
    flags = 0 if is_single_token(surface) else re.IGNORECASE
    return re.compile(r"(?<![\w])" + re.escape(surface) + r"(?![\w])", flags)


def plan_resources(
    items: Sequence[ResourceItem],
    transcript: AnnotatedTranscript,
    ctx: PlanContext,
    *,
    window_s: float = DEFAULT_GROUNDING_WINDOW_S,
) -> ResourcePlan:
    """Admit, expand and disambiguate.

    The cited time is the citation's own label ("12:09" in
    ``[12:09](?t=…&cite=c3)``), which is what the citations sidecar stores
    as ``cited_playback_s``; no sidecar read is needed. It is playback
    time, so ``transcript.playback_time_offset_seconds`` must be the
    episode's.
    """
    segments = [s for s in transcript.segments if s.kind == "content" and s.text.strip()]
    offset = transcript.playback_time_offset_seconds
    extracted_labels = {surface_key(n): label for n, label in ctx.extracted_names if label}

    admitted: List[Tuple[ResourceItem, str, str, Optional[str]]] = []  # item, form, outcome, kind
    dropped: List[Tuple[ResourceItem, str]] = []
    stats = {"items": len(items), "near": 0, "elsewhere": 0, "ungrounded": 0, "anchor": 0, "fallback": 0}

    for item in items:
        if surface_key(item.name) in ctx.anchor_surfaces:
            dropped.append((item, "anchor"))
            stats["anchor"] += 1
            continue
        cited_s = parse_timestamp_label(item.raw_label) if item.raw_label else None
        outcome = _ground(item.name, segments, cited_s, offset, window_s)
        if outcome is not None:
            admitted.append((item, item.name, outcome, item.kind))
            continue
        parts = []
        for name, kind in item.fallbacks:
            if surface_key(name) in ctx.anchor_surfaces:
                continue
            part_outcome = _ground(name, segments, cited_s, offset, window_s)
            if part_outcome is not None:
                parts.append((item, name, part_outcome, kind))
        if parts:
            admitted.extend(parts)
            stats["fallback"] += 1
        else:
            dropped.append((item, "ungrounded"))
            stats["ungrounded"] += 1

    grounded = _expand(admitted, ctx, extracted_labels)
    for g in grounded:
        stats[g.outcome] += 1
    return ResourcePlan(items=tuple(items), grounded=tuple(grounded), dropped=tuple(dropped), stats=stats)


def _ground(
    name: str,
    segments: Sequence[AnnotatedSegment],
    cited_s: Optional[float],
    offset: float,
    window_s: float,
) -> Optional[str]:
    """``near`` / ``elsewhere`` / ``None`` for one admission form.

    Admission forms are the name and the name without a leading "The" —
    never a first name, surname or initial. A single word is admitted on
    ``near`` only: one capitalised word somewhere in an hour of speech is
    weak evidence.
    """
    forms = [name]
    if name.lower().startswith("the ") and len(name) > 4:
        forms.append(name[4:])
    found_elsewhere = False
    for form in forms:
        pattern = surface_pattern(form)
        for segment in segments:
            if not pattern.search(segment.text):
                continue
            if cited_s is not None and _distance_s(segment, cited_s - offset) <= window_s:
                return "near"
            found_elsewhere = True
    if found_elsewhere and not is_single_token(name):
        return "elsewhere"
    return None


def _distance_s(segment: AnnotatedSegment, raw_t: float) -> float:
    if segment.start <= raw_t <= segment.end:
        return 0.0
    return min(abs(raw_t - segment.start), abs(raw_t - segment.end))


def _expand(
    admitted: Sequence[Tuple[ResourceItem, str, str, Optional[str]]],
    ctx: PlanContext,
    extracted_labels: Mapping[str, str],
) -> List[GroundedItem]:
    """Scan surfaces per admitted item, with ambiguous surnames dropped.

    A surname is ambiguous when it is also another admitted item's surface,
    an anchor surface, or the last word of a different multi-word name
    GLiNER found in the episode ("Kate Perkins" listed, "Bill Perkins"
    spoken). The full form that admitted the item always stays.
    """
    proposals: List[Tuple[ResourceItem, str, str, Optional[str], Optional[str], Optional[str]]] = []
    for item, name, outcome, kind in admitted:
        kind = infer_kind(kind, item.gloss if name == item.name else "")
        label = KIND_TO_SURFACE_LABEL.get(kind) if kind else extracted_labels.get(surface_key(name))
        surname = _surname(name) if label == "person" else None
        proposals.append((item, name, outcome, kind, label, surname))

    claims: Dict[str, int] = {}
    for _, name, _, _, _, surname in proposals:
        for surface in (name, surname):
            if surface:
                claims[surface_key(surface)] = claims.get(surface_key(surface), 0) + 1
    multiword = [surface_key(n) for n, _ in ctx.extracted_names if len(n.split()) > 1]

    out: List[GroundedItem] = []
    for item, name, outcome, kind, label, surname in proposals:
        scan = [name]
        dropped: List[str] = []
        if surname:
            key = surface_key(surname)
            full = surface_key(name)
            clashes = (
                claims.get(key, 0) > 1
                or key in ctx.anchor_surfaces
                or any(n != full and n.endswith(" " + key) for n in multiword)
            )
            (dropped if clashes else scan).append(surname)
        out.append(GroundedItem(item, name, outcome, kind, label, tuple(scan), tuple(dropped)))
    return out


def _surname(name: str) -> Optional[str]:
    tokens = name.split()
    if len(tokens) < 2:
        return None
    last = tokens[-1].strip(".,'’")
    if len(last) < MIN_SURNAME_CHARS or not last[:1].isupper():
        return None
    return last
