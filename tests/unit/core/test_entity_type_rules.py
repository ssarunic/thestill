"""Spec #28 §5.2 — Wikidata-P31 driven bucket gating tests.

The rules collapse common NER misclassifications:

- Countries/states (Q6256, Q3624078, ...) labelled as Person/Company → Topic
- Ethnic groups (Q41710) labelled as Person → Topic
- Political parties (Q7278) labelled as Company → Topic (we surface
  parties as topics by convention; they are organisations on Wikidata
  but the rail "Companies mentioned" reads as commerce)
- Abstract fields/concepts (Q151885, Q11862829) → Topic
- Genuine humans (Q5) → Person regardless of incoming fallback

The fallback path: empty P31 set, or P31 not in any allowlist, returns
the resolver's tentative type unchanged.
"""

from __future__ import annotations

from thestill.core.entity_type_rules import classify_entity_type
from thestill.models.entities import EntityType


class TestPersonRule:
    def test_human_p31_forces_person(self):
        # Even if the resolver thought it was a company.
        assert classify_entity_type(["Q5"], EntityType.COMPANY) is EntityType.PERSON

    def test_fictional_human_treated_as_person(self):
        assert classify_entity_type(["Q15632617"], EntityType.TOPIC) is EntityType.PERSON


class TestCountryDemotion:
    def test_country_p31_demotes_to_topic_from_company(self):
        # The "Israel as Company" / "United States as Company" cases
        # from the user's example sidebar.
        assert classify_entity_type(["Q6256"], EntityType.COMPANY) is EntityType.TOPIC

    def test_sovereign_state_p31_demotes_to_topic(self):
        assert classify_entity_type(["Q3624078"], EntityType.PERSON) is EntityType.TOPIC


class TestEthnicGroupDemotion:
    def test_ethnic_group_p31_demotes_to_topic_from_person(self):
        # The "Jews as Person" case.
        assert classify_entity_type(["Q41710"], EntityType.PERSON) is EntityType.TOPIC


class TestAbstractConceptDemotion:
    def test_abstract_concept_p31_demotes_to_topic_from_company(self):
        # The "Artificial intelligence as Company" case (Q11660 ≈ AI,
        # Q151885 = concept).
        assert classify_entity_type(["Q151885"], EntityType.COMPANY) is EntityType.TOPIC

    def test_academic_discipline_p31_demotes_to_topic(self):
        assert classify_entity_type(["Q11862829"], EntityType.COMPANY) is EntityType.TOPIC


class TestPoliticalPartyDemotion:
    def test_political_party_p31_demotes_to_topic_from_company(self):
        # The "Tory as Company" case.
        assert classify_entity_type(["Q7278"], EntityType.COMPANY) is EntityType.TOPIC


class TestCompanyAcceptance:
    def test_business_p31_with_company_fallback_stays_company(self):
        assert classify_entity_type(["Q4830453"], EntityType.COMPANY) is EntityType.COMPANY

    def test_company_p31_with_topic_fallback_demotes_to_topic(self):
        # If GLiNER thought it was a topic but Wikidata says it's a
        # business, we trust GLiNER's bucket guess less than its
        # "this is a noun-phrase entity at all" signal — the safe
        # move is topic so the user doesn't see a false company.
        assert classify_entity_type(["Q4830453"], EntityType.TOPIC) is EntityType.TOPIC


class TestProductAcceptance:
    def test_software_p31_with_product_fallback_stays_product(self):
        assert classify_entity_type(["Q7397"], EntityType.PRODUCT) is EntityType.PRODUCT

    def test_software_p31_with_company_fallback_demotes_to_topic(self):
        assert classify_entity_type(["Q7397"], EntityType.COMPANY) is EntityType.TOPIC


class TestNoSignalFallback:
    def test_empty_p31_returns_fallback(self):
        assert classify_entity_type([], EntityType.COMPANY) is EntityType.COMPANY

    def test_unknown_p31_returns_fallback(self):
        # P31 we don't have rules for — no override.
        assert classify_entity_type(["Q99999999"], EntityType.PERSON) is EntityType.PERSON

    def test_blank_qid_string_skipped(self):
        # Defensive: a stray empty string in the list shouldn't trip
        # the rules. Should fall through to fallback.
        assert classify_entity_type(["", "Q99999999"], EntityType.TOPIC) is EntityType.TOPIC


class TestPriorityOrdering:
    def test_topic_takes_precedence_over_company_when_both_match(self):
        # An entity that's BOTH a country (topic) and an organization
        # (company) — e.g. some sovereign-state QIDs carry both — must
        # land in topic. The rule order in classify_entity_type
        # encodes this; this test pins it.
        assert classify_entity_type(["Q6256", "Q43229"], EntityType.COMPANY) is EntityType.TOPIC

    def test_person_takes_precedence_over_topic_when_both_match(self):
        # Hypothetical case — shouldn't really happen but defensive.
        assert classify_entity_type(["Q5", "Q151885"], EntityType.TOPIC) is EntityType.PERSON
