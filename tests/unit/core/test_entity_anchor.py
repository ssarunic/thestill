"""Spec #28 §1.13.4 — anchor surface variant expansion."""

from __future__ import annotations

from thestill.core.entity_anchor import expand_anchor_variants, index_variants_by_surface
from thestill.models.entities import EntityRecord, EntityType


def _person(canonical: str, aliases=()) -> EntityRecord:
    return EntityRecord(
        id=f"person:{canonical.lower().replace(' ', '-')}",
        type=EntityType.PERSON,
        canonical_name=canonical,
        aliases=list(aliases),
    )


def _company(canonical: str) -> EntityRecord:
    return EntityRecord(
        id=f"company:{canonical.lower().replace(' ', '-')}",
        type=EntityType.COMPANY,
        canonical_name=canonical,
    )


class TestPersonExpansion:
    def test_emits_full_first_last_and_initial_for_two_token_name(self):
        variants = expand_anchor_variants([_person("Andrej Karpathy")])
        surfaces = {v.surface for v in variants}
        assert "Andrej Karpathy" in surfaces
        assert "Andrej" in surfaces
        assert "Karpathy" in surfaces
        assert "A. Karpathy" in surfaces

    def test_includes_aliases(self):
        variants = expand_anchor_variants([_person("Elon Musk", aliases=["Musk", "@elonmusk"])])
        surfaces = {v.surface for v in variants}
        assert "Musk" in surfaces
        assert "@elonmusk" in surfaces

    def test_single_token_canonical_does_not_expand(self):
        variants = expand_anchor_variants([_person("Madonna")])
        surfaces = {v.surface for v in variants}
        assert surfaces == {"Madonna"}

    def test_short_tokens_dropped(self):
        # "A" and "B" are < 3 chars; we still keep the canonical "A B"
        # but not the bare-initial expansions.
        variants = expand_anchor_variants([_person("A B")])
        surfaces = {v.surface for v in variants}
        assert surfaces == {"A B"}

    def test_three_token_uses_first_and_last_only(self):
        variants = expand_anchor_variants([_person("Mary Jane Watson")])
        surfaces = {v.surface for v in variants}
        # Last-name and first-name short forms; explicit middle name
        # not expanded (heuristic favours the common case).
        assert "Mary Jane Watson" in surfaces
        assert "Mary" in surfaces
        assert "Watson" in surfaces

    def test_variants_sorted_longest_first(self):
        variants = expand_anchor_variants([_person("Andrej Karpathy")])
        lengths = [len(v.surface) for v in variants]
        assert lengths == sorted(lengths, reverse=True)


class TestCompanyExpansion:
    def test_company_does_not_token_split(self):
        # We don't want "Apple Inc" → "Apple" — that confuses the
        # extractor on the fruit. Only canonical + aliases.
        variants = expand_anchor_variants([_company("Apple Inc")])
        surfaces = {v.surface for v in variants}
        assert surfaces == {"Apple Inc"}


class TestIndex:
    def test_index_buckets_by_lowercased_surface(self):
        variants = expand_anchor_variants([_person("Andrej Karpathy")])
        idx = index_variants_by_surface(variants)
        assert "andrej karpathy" in idx
        assert "andrej" in idx
        assert "karpathy" in idx

    def test_two_anchors_with_same_first_name_collide(self):
        variants = expand_anchor_variants([_person("Andrej Karpathy"), _person("Andrej Sokolov")])
        idx = index_variants_by_surface(variants)
        assert len(idx["andrej"]) == 2
