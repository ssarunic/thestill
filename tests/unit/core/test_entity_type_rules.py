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

from thestill.core.entity_type_rules import classify_entity_type, is_ai_model, is_kind_of_person
from thestill.models.entities import EntityType


class TestPersonRule:
    def test_human_p31_forces_person(self):
        # Even if the resolver thought it was a company.
        assert classify_entity_type(["Q5"], EntityType.COMPANY) is EntityType.PERSON

    def test_fictional_human_treated_as_person(self):
        assert classify_entity_type(["Q15632617"], EntityType.TOPIC) is EntityType.PERSON


class TestKindOfPersonDemotion:
    """Linked common nouns the a16z rail showed under "People": the item
    is a profession, position, kinship or population group, not a person."""

    def test_profession_p31_demotes_to_topic_from_person(self):
        # "entrepreneur" (Q131524): profession + legal status.
        assert classify_entity_type(["Q28640", "Q2628882"], EntityType.PERSON) is EntityType.TOPIC

    def test_occupation_p31_demotes_to_topic(self):
        # "Photographer" (Q33231): occupation + ISCO-08 group.
        assert classify_entity_type(["Q12737077", "Q108300140"], EntityType.PERSON) is EntityType.TOPIC

    def test_academic_rank_and_position_demote_to_topic(self):
        # "Professor" (Q121594).
        p31 = ["Q480319", "Q5767753", "Q486983", "Q12737077", "Q4164871", "Q3529618"]
        assert classify_entity_type(p31, EntityType.PERSON) is EntityType.TOPIC

    def test_population_group_demotes_to_topic(self):
        # "child" (Q7569): population group + class of human.
        assert classify_entity_type(["Q851990", "Q134264207"], EntityType.PERSON) is EntityType.TOPIC

    def test_kinship_demotes_to_topic(self):
        # "parent" (Q7566): kinship + social role.
        assert classify_entity_type(["Q171318", "Q214339"], EntityType.PERSON) is EntityType.TOPIC

    def test_a_people_is_a_kind_of_person(self):
        # Prod dry run 2026-11-03: "Russians", "Iraqis", "Samaritans" tagged
        # person sat held; a demonym is never a person's name.
        assert classify_entity_type(["Q41710", "Q2472587"], EntityType.PERSON) is EntityType.TOPIC
        assert is_kind_of_person(["Q33829", "Q6266"])
        assert is_kind_of_person(["Q11197007"])

    def test_a_disputed_or_pseudonymous_human_stays_a_person(self):
        # Satoshi Nakamoto (Q15924): pseudonym + disputed human + "group of
        # humans", no Q5. The person rule must win over the group class.
        p31 = ["Q61002", "Q21070568", "Q16334295"]
        assert classify_entity_type(p31, EntityType.PERSON, qid="Q15924") is EntityType.PERSON
        assert classify_entity_type(p31, EntityType.TOPIC, qid="Q15924") is EntityType.PERSON

    def test_a_corporate_title_is_a_kind_of_person(self):
        # Prod dry run 2026-11-03: "chief product officer" carries only the
        # corporate-title class, which the topic list names but the hold
        # exemption did not, so it sat held like a name collision.
        assert classify_entity_type(["Q11488158"], EntityType.PERSON) is EntityType.TOPIC
        assert is_kind_of_person(["Q11488158"])

    def test_social_status_and_life_phase_demote_to_topic(self):
        # "billionaire" (Q189970 social status) and "Adult" (Q785260 phase
        # of human life) from the local person bucket.
        assert classify_entity_type(["Q189970", "Q5932254"], EntityType.PERSON) is EntityType.TOPIC
        assert classify_entity_type(["Q785260", "Q185836"], EntityType.PERSON) is EntityType.TOPIC

    def test_the_human_class_itself_is_a_topic(self):
        # "Human" (Q5) is the class every person is an instance of; its own
        # P31 is a taxonomy class no list names, so the QID decides.
        assert classify_entity_type(["Q55983715"], EntityType.PERSON, qid="Q5") is EntityType.TOPIC

    def test_a_real_human_with_a_position_stays_a_person(self):
        # A person row can carry a role class beside Q5; the person rule wins.
        assert classify_entity_type(["Q5", "Q4164871"], EntityType.PERSON, qid="Q937") is EntityType.PERSON

    def test_a_kind_of_person_labelled_topic_stays_a_topic(self):
        assert classify_entity_type(["Q28640"], EntityType.TOPIC) is EntityType.TOPIC


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

    def test_business_p31_with_topic_fallback_is_a_company(self):
        # Wikidata's business class wins over GLiNER's topic guess: the
        # live linker chose the QID with context, so a wrong company here
        # is a linking error, not a typing one (spec #92 follow-up; before
        # that this stayed a topic).
        assert classify_entity_type(["Q4830453"], EntityType.TOPIC) is EntityType.COMPANY


class TestProductAcceptance:
    def test_software_p31_with_product_fallback_stays_product(self):
        assert classify_entity_type(["Q7397"], EntityType.PRODUCT) is EntityType.PRODUCT

    def test_software_p31_with_company_fallback_is_a_product(self):
        assert classify_entity_type(["Q7397"], EntityType.COMPANY) is EntityType.PRODUCT


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


class TestWorks:
    """Spec #92 Phase 0: Q11424 is *film*, not a genre. It sat in the topic
    list, which runs before the product check, so every film became a topic."""

    def test_a_film_is_a_product_whatever_gliner_said(self):
        for fallback in EntityType:
            assert classify_entity_type(["Q11424"], fallback) is EntityType.PRODUCT

    def test_series_book_and_podcast_are_products_from_a_topic_tag(self):
        for p31 in ("Q5398426", "Q571", "Q7725634", "Q24634210"):
            assert classify_entity_type([p31], EntityType.TOPIC) is EntityType.PRODUCT

    def test_a_work_with_a_topic_class_too_is_still_a_product(self):
        assert classify_entity_type(["Q11424", "Q1656682"], EntityType.TOPIC) is EntityType.PRODUCT

    def test_film_genre_is_a_topic(self):
        assert classify_entity_type(["Q201658"], EntityType.PRODUCT) is EntityType.TOPIC

    def test_a_person_stays_a_person(self):
        assert classify_entity_type(["Q5", "Q11424"], EntityType.TOPIC) is EntityType.PERSON


class TestMintedWorks:
    def test_a_minted_film_is_a_product(self):
        from thestill.core.entity_review import _mint_type_from_p31

        assert _mint_type_from_p31(["Q11424"]) is EntityType.PRODUCT
        assert _mint_type_from_p31(["Q5398426", "Q1656682"]) is EntityType.PRODUCT
        assert _mint_type_from_p31(["Q5"]) is EntityType.PERSON


class TestBusinessAndSoftware:
    """Follow-up to spec #92: the prod dry run showed real companies and
    software becoming topics through the generic "organization" class and the
    product-only-if-GLiNER-said-product gate."""

    ALLIANZ = ["Q2143354", "Q891723", "Q4830453", "Q43229"]
    JETBLUE = ["Q46970", "Q891723", "Q43229"]

    def test_a_company_with_the_generic_organization_class_is_a_company(self):
        for fallback in EntityType:
            if fallback is EntityType.PERSON:
                continue
            assert classify_entity_type(self.ALLIANZ, fallback) is EntityType.COMPANY
        assert classify_entity_type(self.JETBLUE, EntityType.TOPIC) is EntityType.COMPANY

    def test_software_is_a_product_whatever_gliner_said(self):
        assert classify_entity_type(["Q7397"], EntityType.COMPANY) is EntityType.PRODUCT
        assert classify_entity_type(["Q1668024", "Q166142"], EntityType.TOPIC) is EntityType.PRODUCT

    def test_countries_intergovernmental_bodies_and_ngos_stay_topics(self):
        assert classify_entity_type(["Q3624078", "Q6256", "Q43229"], EntityType.COMPANY) is EntityType.TOPIC
        assert classify_entity_type(["Q245065", "Q484652"], EntityType.COMPANY) is EntityType.TOPIC
        assert (
            classify_entity_type(["Q15911314", "Q43229", "Q79913", "Q163740"], EntityType.COMPANY) is EntityType.TOPIC
        )

    def test_only_the_broad_organization_class_keeps_a_company(self):
        # Prod dry run 2026-10-27: these were all demoted company → topic.
        sanofi = ["Q19644607", "Q43229"]  # pharmaceutical company
        framatome = ["Q43229"]
        xtx_markets = ["Q11691", "Q43229"]  # stock exchange
        for p31 in (sanofi, framatome, xtx_markets):
            assert classify_entity_type(p31, EntityType.COMPANY) is EntityType.COMPANY
            assert classify_entity_type(p31, EntityType.TOPIC) is EntityType.TOPIC

    def test_organization_with_another_topic_class_is_still_a_topic(self):
        assert classify_entity_type(["Q1127126", "Q43229"], EntityType.COMPANY) is EntityType.TOPIC  # alliance
        assert classify_entity_type(["Q7278", "Q43229"], EntityType.COMPANY) is EntityType.TOPIC  # party

    def test_a_place_that_lists_a_business_class_stays_a_topic(self):
        assert classify_entity_type(["Q515", "Q4830453"], EntityType.COMPANY) is EntityType.TOPIC

    def test_people_and_works_still_win(self):
        assert classify_entity_type(["Q5", "Q4830453"], EntityType.COMPANY) is EntityType.PERSON
        assert classify_entity_type(["Q7889", "Q7397"], EntityType.COMPANY) is EntityType.PRODUCT

    def test_minting_agrees(self):
        from thestill.core.entity_review import _mint_type_from_p31

        assert _mint_type_from_p31(self.ALLIANZ) is EntityType.COMPANY
        assert _mint_type_from_p31(["Q7397"]) is EntityType.PRODUCT


class TestAIModels:
    """Prod 2026-11-03: "Sora (text-to-video model)" and "Claude (AI)" under
    "People in this episode", ChatGPT under companies. P31 sets as Wikidata
    lists them."""

    SORA = ["Q115517227", "Q114617315"]  # text-to-video model, diffusion model
    CLAUDE = ["Q870780", "Q116961403", "Q124629760", "Q133284163"]
    CHATGPT = ["Q133284163", "Q116777014", "Q115305900", "Q19967801", "Q35127", "Q870780", "Q117246174"]
    DALL_E = ["Q96369982", "Q113940039", "Q117349473", "Q116777014", "Q133857185"]

    def test_an_ai_model_is_a_product_whatever_gliner_said(self):
        for p31 in (self.SORA, self.CLAUDE, self.CHATGPT, self.DALL_E):
            for fallback in EntityType:
                assert classify_entity_type(p31, fallback) is EntityType.PRODUCT

    def test_an_ai_model_is_exempt_from_the_person_hold(self):
        assert is_ai_model(self.SORA) and is_ai_model(self.CLAUDE)
        assert not is_ai_model(["Q5"]) and not is_ai_model(["Q11424"])

    def test_the_broad_generative_ai_class_alone_is_not_a_model(self):
        # "generative artificial intelligence" (Q117246174) is also the field.
        assert not is_ai_model(["Q117246174"])
        assert classify_entity_type(["Q117246174"], EntityType.TOPIC) is EntityType.TOPIC

    def test_minting_agrees(self):
        from thestill.core.entity_review import _mint_type_from_p31

        assert _mint_type_from_p31(self.SORA) is EntityType.PRODUCT


class TestPlacesAndEventsBySuperclass:
    """Croatian towns, battles and demographics from "Sjeverna Dalmacija u
    Domovinskom ratu": Wikidata types them by classes no list names, so they
    kept GLiNER's company/person guess."""

    SUPERCLASSES = {
        "Q15105893": ["Q3435941", "Q12357832", "Q3957", "Q57058", "Q515"],  # town in Croatia
        "Q4845841": ["Q486972", "Q12357920", "Q3435941"],  # settlement in Croatia
        "Q23983664": ["Q2725376", "Q74817647"],  # demographics of country or region
        "Q207320": ["Q1673189"],  # paramilitary organization
        "Q46970": ["Q43229", "Q4830453"],  # airline: "organization" + business
    }

    def lookup(self, qid):
        return self.SUPERCLASSES.get(qid, [])

    def test_a_town_in_croatia_is_a_topic(self):
        for fallback in (EntityType.COMPANY, EntityType.PERSON, EntityType.PRODUCT):
            assert classify_entity_type(["Q15105893"], fallback, self.lookup) is EntityType.TOPIC
        assert classify_entity_type(["Q4845841"], EntityType.PERSON, self.lookup) is EntityType.TOPIC

    def test_demographics_is_a_topic(self):
        assert classify_entity_type(["Q62944360", "Q23983664"], EntityType.COMPANY, self.lookup) is EntityType.TOPIC

    def test_battles_and_military_operations_are_topics_without_a_lookup(self):
        assert classify_entity_type(["Q178561"], EntityType.COMPANY) is EntityType.TOPIC
        assert classify_entity_type(["Q645883"], EntityType.COMPANY) is EntityType.TOPIC

    def test_an_organization_superclass_does_not_demote(self):
        assert classify_entity_type(["Q207320"], EntityType.COMPANY, self.lookup) is EntityType.COMPANY
        assert classify_entity_type(["Q46970"], EntityType.COMPANY, self.lookup) is EntityType.COMPANY

    def test_superclasses_are_only_fetched_when_no_class_matched(self):
        fetched = []

        def lookup(qid):
            fetched.append(qid)
            return []

        assert classify_entity_type(["Q5"], EntityType.COMPANY, lookup) is EntityType.PERSON
        assert classify_entity_type(["Q999"], EntityType.TOPIC, lookup) is EntityType.TOPIC
        assert fetched == []
