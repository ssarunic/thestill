"""Anchor-set expansion for spec #28 §1.13.4.

Given a host/guest/recurring ``EntityRecord``, generate the surface
variants that should match in body text and resolve directly to the
anchor entity:

- the canonical name itself
- its existing aliases
- the last token alone (surname-only mentions)
- the first token alone (first-name only)
- ``First-Initial. Last`` for two-token names

Designed to be conservative — we only expand when the result is
unambiguous. Single-token canonical names produce no extra variants.
Tokens that are too short (≤ 2 chars after stripping punctuation) are
dropped to avoid spurious "I." / "A." matches.
"""

from __future__ import annotations

import re
from dataclasses import dataclass
from typing import Dict, FrozenSet, Iterable, List

from ..models.entities import EntityRecord, EntityType

_TOKEN_SPLIT_RE = re.compile(r"\s+")
_PUNCT_STRIP_RE = re.compile(r"[^\w\-']")
_MIN_TOKEN_CHARS = 3


@dataclass(frozen=True)
class AnchorVariant:
    """One ``(surface, entity_id)`` pair the extractor matches against."""

    surface: str
    entity_id: str
    entity_type: EntityType


def expand_anchor_variants(entities: Iterable[EntityRecord]) -> List[AnchorVariant]:
    """Generate surface variants for the given anchor entities.

    Variants are emitted longest-first so the extractor's substring
    matcher prefers the most specific span. Identical (surface,
    entity_id) pairs are deduplicated; the same surface mapped to
    multiple entity_ids is preserved (caller marks ambiguous).
    """
    variants: List[AnchorVariant] = []
    seen: set = set()
    for entity in entities:
        for surface in _surfaces_for(entity):
            key = (surface.lower(), entity.id)
            if key in seen:
                continue
            seen.add(key)
            variants.append(AnchorVariant(surface=surface, entity_id=entity.id, entity_type=entity.type))
    variants.sort(key=lambda v: (-len(v.surface), v.surface.lower()))
    return variants


def index_variants_by_surface(variants: Iterable[AnchorVariant]) -> Dict[str, List[AnchorVariant]]:
    """Group variants by their lowercased surface for fast extractor lookup."""
    bucket: Dict[str, List[AnchorVariant]] = {}
    for variant in variants:
        bucket.setdefault(variant.surface.lower(), []).append(variant)
    return bucket


def _surfaces_for(entity: EntityRecord) -> List[str]:
    """Surface variants for one anchor entity."""
    out: List[str] = []
    seen: FrozenSet[str] = frozenset()

    def _add(text: str) -> None:
        nonlocal seen
        cleaned = text.strip()
        if not cleaned or cleaned.lower() in seen:
            return
        seen = frozenset({*seen, cleaned.lower()})
        out.append(cleaned)

    _add(entity.canonical_name)
    for alias in entity.aliases:
        _add(alias)

    # Person-specific token expansion. Companies/products/topics rarely
    # benefit from token splitting — "Apple Inc" is not the same as
    # "Apple", and treating it as such would mis-anchor every mention
    # of the fruit. Keep it limited to people.
    if entity.type == EntityType.PERSON:
        tokens = [t for t in _TOKEN_SPLIT_RE.split(entity.canonical_name) if t]
        cleaned = [_PUNCT_STRIP_RE.sub("", t) for t in tokens]
        cleaned = [t for t in cleaned if len(t) >= _MIN_TOKEN_CHARS]
        if len(cleaned) >= 2:
            # Last name (most common short form: "Karpathy said...")
            _add(cleaned[-1])
            # First name ("Andrej said...")
            _add(cleaned[0])
            # First-initial. Last ("A. Karpathy")
            _add(f"{cleaned[0][0]}. {cleaned[-1]}")
    return out
