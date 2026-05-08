# Copyright 2025-2026 Thestill
#
# Licensed under the Apache License, Version 2.0 (the "License");
# you may not use this file except in compliance with the License.
# You may obtain a copy of the License at
#
#     http://www.apache.org/licenses/LICENSE-2.0

"""Spec #28 §5.2 — Wikidata-P31 driven bucket gating for entity types.

The base resolver picks ``EntityType`` from the GLiNER ``surface_label``
or ReFinED's ``coarse_type`` (see ``entity_resolver._infer_entity_type``).
Both signals are noisy:

- GLiNER labels ``"Jews"`` as ``person`` and ``"Tory"`` as ``company``;
- ReFinED's coarse_type lumps countries (Israel, the United States) into
  ``GPE`` which we map to ``topic`` — fine — but the GLiNER label often
  pre-empts that map and routes ``"Israel"`` into ``company``.

Both classes of error reach the right rail and read as misclassifications
to the user. Once we know the Wikidata QID, ``instance of`` (P31) is the
authoritative signal: countries (Q6256), ethnic groups (Q41710), abstract
concepts (Q151885) etc. cannot be people or companies regardless of what
the labels said.

This module provides:

- ``classify_entity_type(p31_qids, fallback)`` — returns the bucket the
  entity *should* land in given its P31 set, or ``None`` if the entity
  is incompatible with all four buckets and should be dropped.
- The hardcoded P31 → ``EntityType`` mapping. We intentionally keep this
  small and conservative: a P31 not in the mapping defers to ``fallback``
  rather than guessing.

Why hardcoded rather than fetched-and-learned: spec #28's four buckets
are stable and the Wikidata classes that map cleanly onto them are a
small known set (humans, organisations, products, etc.). A learned
mapping would be over-engineered for the volume of error we're seeing.
"""

from __future__ import annotations

from typing import Iterable, Optional

from ..models.entities import EntityType

# QIDs whose ``instance of`` (P31) values force the entity into a
# specific bucket. The first match wins — ordering matters when an
# entity has multiple P31 values (rare but happens for e.g. "Apple Inc."
# which is both ``business`` and ``public company``).
#
# The lists are not exhaustive on purpose: if a P31 isn't here we fall
# back to whatever the resolver originally picked (current behavior).
# Add to these lists by inspecting Wikidata for the surface forms that
# slip through.
PERSON_P31 = frozenset(
    {
        "Q5",  # human
        "Q15632617",  # fictional human
        "Q95074",  # fictional character
        "Q3658341",  # literary character
    }
)

COMPANY_P31 = frozenset(
    {
        "Q4830453",  # business
        "Q783794",  # company
        "Q6881511",  # enterprise
        "Q891723",  # public company
        "Q161726",  # multinational corporation
        "Q31629",  # type of business entity
        "Q1058914",  # software company
        "Q15265344",  # broadcaster
        "Q1331793",  # media company
        "Q4438121",  # sports organization
        "Q163740",  # nonprofit organization (keeps NGOs in companies)
        "Q1194093",  # international NGO
        "Q11691",  # stock exchange
        "Q2659904",  # government agency
        "Q327333",  # government agency (alt)
        # Note: Q43229 (broad "organization") and Q484652 (intergov org)
        # live in TOPIC_P31 — they pull in things like NATO and the UN
        # that read as topics, not companies.
    }
)

PRODUCT_P31 = frozenset(
    {
        "Q7397",  # software
        "Q341",  # free software
        "Q166142",  # application
        "Q1395226",  # mobile app
        "Q21198",  # computer science (only when also a product)
        "Q205663",  # process
        "Q2424752",  # product
        "Q21146257",  # type
        "Q187320",  # device
        "Q15401930",  # product (commerce)
        "Q49850",  # journal (publication-as-product)
        "Q11424",  # film
        "Q571",  # book
        "Q5398426",  # television series
    }
)

# Topics is the broad catch-all bucket: places, concepts, events,
# fields of study, religions, ethnic groups, ideologies, etc. We
# enumerate the *common* P31s here so we can demote things that
# slipped into PERSON/COMPANY by mistake.
TOPIC_P31 = frozenset(
    {
        # Geography
        "Q6256",  # country
        "Q3624078",  # sovereign state
        "Q7275",  # state
        "Q515",  # city
        "Q486972",  # human settlement
        "Q5107",  # continent
        "Q23397",  # lake
        "Q39594",  # geographic region
        "Q82794",  # geographic region (alt)
        "Q15642541",  # historical country
        "Q188509",  # suburb
        "Q40080",  # beach
        "Q1489259",  # historical sovereign state
        "Q99541706",  # constitutional republic
        "Q10711424",  # state-recognised by some
        "Q51576574",  # member state of the United Nations
        "Q2221906",  # geographic location
        # Groups / demographics
        "Q41710",  # ethnic group
        "Q9174",  # religion
        "Q3024240",  # historical ethnic group
        "Q1532191",  # demographic group
        "Q874405",  # social group
        "Q4392985",  # religious identity
        "Q2659047",  # generation cohort
        "Q179805",  # political philosophy
        "Q12909644",  # political ideology
        "Q11197007",  # people (collective)
        "Q6266",  # nation
        "Q2472587",  # people (demographic)
        "Q6957341",  # religious denomination type
        # Events / conflicts
        "Q1190554",  # occurrence
        "Q198",  # war
        "Q124734",  # battle
        "Q1656682",  # event
        "Q175331",  # demonstration
        # Concepts / fields
        "Q151885",  # concept
        "Q11862829",  # academic discipline
        "Q1936384",  # field of study
        "Q336",  # science
        "Q4671286",  # academic major
        "Q29028",  # phenomenon
        "Q628523",  # message
        "Q11424",  # genre
        "Q483394",  # literary genre
        # Languages
        "Q34770",  # language
        # Professions / occupations / positions — gliner often tags role
        # nouns ("product manager", "first officer") with surface_label=
        # "person", but Wikidata is unambiguous: a job title is not a
        # human. Demote to topic so the People rail stays clean.
        "Q28640",  # profession
        "Q12737077",  # occupation
        "Q4164871",  # position
        "Q11488158",  # officeholder
        # Political parties + factions — orgs by definition but the
        # rail's "Companies mentioned" reads as commerce, so we surface
        # them as topics.
        "Q7278",  # political party
        "Q24649",  # political faction
        # Movements
        "Q49773",  # social movement
        "Q1052712",  # political movement
        # Inter- and supra-national bodies — NATO, UN, EU bodies. Not
        # companies; the user's rail had NATO listed as a company which
        # read as an obvious miscategorization.
        "Q43229",  # organization (broad)
        "Q484652",  # international organization
        "Q245065",  # intergovernmental organization
        "Q1127126",  # military alliance
        "Q100906234",  # military alliance type (alt)
    }
)


# Type → allowed-P31 set. Used to validate the resolver's tentative
# bucket against the Wikidata signal.
ALLOWED_P31_BY_TYPE = {
    EntityType.PERSON: PERSON_P31,
    EntityType.COMPANY: COMPANY_P31,
    EntityType.PRODUCT: PRODUCT_P31,
    EntityType.TOPIC: TOPIC_P31,
}


def classify_entity_type(
    p31_qids: Iterable[str],
    fallback: EntityType,
) -> Optional[EntityType]:
    """Return the bucket this entity should land in based on its P31 set.

    Resolution rules:

    1. If any P31 is in ``PERSON_P31`` → ``PERSON``. Persons are the
       least ambiguous bucket and the most expensive to misclassify
       (the rail's ``People in this episode`` section is the most
       conspicuous).
    2. Else if any P31 is in ``COMPANY_P31`` and ``fallback`` is also
       company-or-product → ``COMPANY``. We require fallback agreement
       because organisation P31s pull in things like sports leagues and
       government agencies that the user probably doesn't want in
       "Companies mentioned" unless GLiNER also flagged them as
       company-shaped.
    3. Else if any P31 is in ``PRODUCT_P31`` and fallback is product →
       ``PRODUCT``.
    4. Else if any P31 is in ``TOPIC_P31`` → ``TOPIC``. Demotes
       countries / ethnic groups / ideologies that landed elsewhere.
    5. Else → ``fallback`` (we have no signal to override the resolver).

    Returns ``None`` only when ``p31_qids`` is empty *and* ``fallback``
    is ``None`` — the resolver always passes a fallback so this is a
    type-system courtesy, not a runtime case.
    """
    p31_set = {q for q in p31_qids if q}

    if p31_set & PERSON_P31:
        return EntityType.PERSON

    if p31_set & TOPIC_P31:
        # Topic check runs before company because countries/parties
        # have organization-like P31s ("Israel" is also Q3624078
        # sovereign state, sometimes an "Q43229 organization" sibling).
        # Forcing topic first prevents "Israel as Company".
        return EntityType.TOPIC

    if p31_set & COMPANY_P31:
        if fallback in (EntityType.COMPANY, EntityType.PRODUCT):
            return EntityType.COMPANY
        # GLiNER thought it was a person but Wikidata says it's an org —
        # trust Wikidata, demote to topic so it doesn't pollute either
        # the People or Companies sections.
        return EntityType.TOPIC

    if p31_set & PRODUCT_P31:
        if fallback == EntityType.PRODUCT:
            return EntityType.PRODUCT
        return EntityType.TOPIC

    return fallback
