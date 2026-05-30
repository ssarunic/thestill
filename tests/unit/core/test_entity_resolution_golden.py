# Copyright 2025-2026 Thestill
#
# Licensed under the Apache License, Version 2.0 (the "License").

"""Golden-set regression eval for entity resolution.

Guards the *tunable* parts of the resolver against regressions when we
change a parameter (``min_qid_confidence``, the P31 frozensets, the
blacklist) or upgrade ReFinED. Each case is a real surface form in
context with the answer we expect after the resolver's post-processing.

Two tiers (see the review-queue investigation):

* **Fast tier** (this module, runs under ``make test``): feeds *frozen*
  ReFinED output + *frozen* Wikidata P31 into the real
  :class:`EntityResolver`, then asserts the final QID/type/status. It
  never loads the ~6GB model, so it runs in milliseconds and exercises
  the confidence floor, the blacklist wiring, P31 type-gating and the
  span picker — everything we actually tune.

* **Heavy tier** (``test_golden_endto_end_real_model``, skipped unless
  ``refined`` is importable): re-resolves the real-world cases through
  the actual model. This is the only thing that catches a *model
  version* regression, and it is how you refresh the frozen snapshots
  below after a ReFinED upgrade.

When the detection queue finds a new bad match and you blacklist /
override it, add it here as a case so the eval guards it forever.
"""

from __future__ import annotations

import importlib.util
import os
from dataclasses import dataclass, field
from types import SimpleNamespace
from typing import Dict, List, Optional, Tuple

import pytest

from thestill.core.entity_resolver import EntityResolver
from thestill.models.entities import EntityMention, EntityType


@dataclass(frozen=True)
class GoldenCase:
    """One resolution expectation.

    ``refined`` is the frozen model snapshot: ``substring -> (qid, title,
    coarse_type, confidence)``. ``p31`` is the frozen Wikidata
    instance-of snapshot: ``qid -> [P31 qids]``. ``blacklist`` lists the
    ``(surface, wrong_qid)`` corrections already in force.
    """

    id: str
    surface: str
    excerpt: str
    label: Optional[str]
    refined: Dict[str, Tuple[str, str, str, float]]
    p31: Dict[str, List[str]] = field(default_factory=dict)
    blacklist: Tuple[Tuple[str, str], ...] = ()
    expect_status: str = "resolved"
    expect_qid: Optional[str] = None
    expect_type: EntityType = EntityType.TOPIC
    guards: str = ""  # which parameter/behaviour this case protects
    model_case: bool = True  # False for synthetic cases the real model can't reproduce


GOLDEN_CASES: List[GoldenCase] = [
    # --- the live bug + its correction -------------------------------------
    GoldenCase(
        id="anthropic_blacklisted",
        surface="Anthropic",
        excerpt="We switched our agents over to Claude from Anthropic last quarter.",
        label="company",
        refined={"Anthropic": ("Q240581", "Anthropic principle", "ORG", 0.95)},
        p31={"Q240581": ["Q211364"]},
        blacklist=(("Anthropic", "Q240581"),),
        expect_status="unresolvable",
        expect_qid=None,
        expect_type=EntityType.COMPANY,
        guards="blacklist blocks the cosmology QID even at high confidence",
    ),
    # --- positive controls: must keep working after any tuning -------------
    GoldenCase(
        id="elon_musk_person",
        surface="Elon Musk",
        excerpt="Then Elon Musk tweeted about it.",
        label="person",
        refined={"Elon Musk": ("Q317521", "Elon Musk", "PER", 0.99)},
        p31={"Q317521": ["Q5"]},
        expect_status="resolved",
        expect_qid="Q317521",
        expect_type=EntityType.PERSON,
        guards="ordinary high-confidence person resolution",
    ),
    GoldenCase(
        id="openai_company",
        surface="OpenAI",
        excerpt="OpenAI shipped a new model today.",
        label="company",
        refined={"OpenAI": ("Q21708200", "OpenAI", "ORG", 0.97)},
        p31={"Q21708200": ["Q4830453"]},
        expect_status="resolved",
        expect_qid="Q21708200",
        expect_type=EntityType.COMPANY,
        guards="ordinary company resolution + P31 keeps it a company",
    ),
    # --- P31 type-gating: country must demote out of "company" -------------
    GoldenCase(
        id="israel_country_demoted",
        surface="Israel",
        excerpt="Israel announced new export rules.",
        label="company",
        refined={"Israel": ("Q801", "Israel", "ORG", 0.96)},
        p31={"Q801": ["Q6256"]},
        expect_status="resolved",
        expect_qid="Q801",
        expect_type=EntityType.TOPIC,
        guards="TOPIC_P31 (Q6256 country) overrides a wrong GLiNER company label",
    ),
    # --- confidence floor: matched pair straddling DEFAULT_MIN_QID_CONFIDENCE
    GoldenCase(
        id="low_confidence_rejected",
        surface="frontier labs",
        excerpt="The frontier labs are pouring money into compute.",
        label="topic",
        refined={"frontier labs": ("Q9999", "Reinforcement learning", "MISC", 0.30)},
        expect_status="unresolvable",
        expect_qid=None,
        expect_type=EntityType.TOPIC,
        guards="below-threshold prediction is rejected (floor must stay >0.30)",
        model_case=False,
    ),
    GoldenCase(
        id="high_confidence_accepted",
        surface="frontier labs",
        excerpt="The frontier labs are pouring money into compute.",
        label="topic",
        refined={"frontier labs": ("Q9999", "Reinforcement learning", "MISC", 0.80)},
        expect_status="resolved",
        expect_qid="Q9999",
        expect_type=EntityType.TOPIC,
        guards="above-threshold prediction is accepted (floor must stay <0.80)",
        model_case=False,
    ),
]


class _GoldenReFinED:
    """Frozen ReFinED snapshot. Emits one span per configured substring
    found in the text, carrying a confidence score on the span (the real
    model exposes ``entity_linking_model_confidence_score``)."""

    def __init__(self, refined: Dict[str, Tuple[str, str, str, float]]):
        self._refined = refined

    def process_text(self, text: str):
        spans = []
        for surface, (qid, title, coarse, conf) in self._refined.items():
            if surface not in text:
                continue
            spans.append(
                SimpleNamespace(
                    text=surface,
                    coarse_type=coarse,
                    entity_linking_model_confidence_score=conf,
                    predicted_entity=SimpleNamespace(
                        wikidata_entity_id=qid,
                        wikipedia_entity_title=title,
                        human_readable_name=title,
                        description=None,
                    ),
                )
            )
        return spans


class _FrozenP31:
    """Frozen Wikidata P31 lookup matching the resolver's ``_P31Lookup``."""

    def __init__(self, p31: Dict[str, List[str]]):
        self._p31 = p31

    def fetch_p31(self, qid: str) -> List[str]:
        return self._p31.get(qid, [])


def _mention(case: GoldenCase) -> EntityMention:
    return EntityMention(
        id=1,
        episode_id="ep-golden",
        segment_id=0,
        start_ms=0,
        end_ms=1000,
        surface_form=case.surface,
        surface_label=case.label,
        quote_excerpt=case.excerpt,
        confidence=0.9,
        extractor="golden",
    )


def _blacklist_fn(case: GoldenCase):
    block = {(s, q) for s, q in case.blacklist}
    return lambda surface, qid: (surface, qid) in block


@pytest.mark.parametrize("case", GOLDEN_CASES, ids=[c.id for c in GOLDEN_CASES])
def test_golden_resolution_fast(case: GoldenCase):
    """Run frozen ReFinED + frozen P31 through the real resolver and
    assert the post-processed outcome. Guards: ``case.guards``."""
    resolver = EntityResolver(
        preloaded_model=_GoldenReFinED(case.refined),
        wikidata_client=_FrozenP31(case.p31),
        # Intentionally NOT overriding min_qid_confidence — we want the
        # module default under test, so changing it trips these cases.
    )
    results = resolver.resolve([_mention(case)], is_blacklisted=_blacklist_fn(case))
    assert len(results) == 1
    r = results[0]
    assert r.status == case.expect_status, f"{case.id}: status ({case.guards})"
    assert r.entity.wikidata_qid == case.expect_qid, f"{case.id}: qid ({case.guards})"
    assert r.entity.type == case.expect_type, f"{case.id}: type ({case.guards})"


@pytest.mark.skipif(
    not os.environ.get("THESTILL_GOLDEN_HEAVY") or importlib.util.find_spec("refined") is None,
    reason="heavy tier is opt-in: set THESTILL_GOLDEN_HEAVY=1 with refined installed",
)
def test_golden_end_to_end_real_model():
    """Heavy tier: re-resolve the real-world cases through the actual
    model. Run this when bumping ReFinED to catch model-version
    regressions and to refresh the frozen ``refined`` snapshots above::

        THESTILL_GOLDEN_HEAVY=1 ./venv/bin/pytest \\
            tests/unit/core/test_entity_resolution_golden.py -k real_model

    Loads the ~6GB model and hits live Wikidata, so it is opt-in (off in
    normal ``make test``, even on machines where ``refined`` is present).
    Only ``model_case=True`` cases are checked — synthetic threshold
    cases can't be reproduced by the model.
    """
    from thestill.core.wikidata_client import WikidataClient

    resolver = EntityResolver(wikidata_client=WikidataClient())
    for case in (c for c in GOLDEN_CASES if c.model_case):
        results = resolver.resolve([_mention(case)], is_blacklisted=_blacklist_fn(case))
        r = results[0]
        assert r.status == case.expect_status, f"{case.id} ({case.guards})"
        assert r.entity.wikidata_qid == case.expect_qid, f"{case.id} ({case.guards})"
        assert r.entity.type == case.expect_type, f"{case.id} ({case.guards})"
