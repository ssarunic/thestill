"""Spec #92 Phase 0 — choosing which entities backfill-entity-types changes."""

from thestill.core.entity_retype import plan_backfill, plan_retype
from thestill.models.entities import EntityRecord, EntityType


def _entity(id_, type_, p31=None, qid="Q1"):
    return EntityRecord(
        id=id_, type=type_, canonical_name=id_.split(":")[1], wikidata_qid=qid, wikidata_instance_of=p31 or []
    )


FILM = ["Q11424"]
COMPANY_AND_ORG = ["Q4830453", "Q891723", "Q43229"]  # Allianz: business + public company + "organization"


class TestOnlyWorks:
    def test_a_work_becomes_a_product(self):
        assert plan_retype(_entity("topic:margin-call", EntityType.TOPIC), FILM, only_works=True) is EntityType.PRODUCT
        assert (
            plan_retype(_entity("company:factorio", EntityType.COMPANY), ["Q7889"], only_works=True)
            is EntityType.PRODUCT
        )

    def test_a_product_work_is_left_alone(self):
        assert plan_retype(_entity("product:mad-men", EntityType.PRODUCT), ["Q5398426"], only_works=True) is None

    def test_a_company_with_the_generic_organization_class_is_not_demoted(self):
        allianz = _entity("company:allianz", EntityType.COMPANY)
        assert plan_retype(allianz, COMPANY_AND_ORG) is None  # business beats the generic "organization"
        assert plan_retype(allianz, COMPANY_AND_ORG, only_works=True) is None

    def test_software_and_people_do_not_move(self):
        assert plan_retype(_entity("company:alexnet", EntityType.COMPANY), ["Q7397"], only_works=True) is None
        assert plan_retype(_entity("company:michael-bloomberg", EntityType.COMPANY), ["Q5"], only_works=True) is None

    def test_a_person_who_is_also_listed_as_a_work_stays_put(self):
        assert plan_retype(_entity("topic:x", EntityType.TOPIC), ["Q5", "Q11424"], only_works=True) is None


class TestKindsOfPerson:
    def test_a_profession_typed_person_becomes_a_topic(self):
        # "entrepreneur" from the a16z rail: a profession is a kind of person.
        row = _entity("topic:entrepreneur", EntityType.PERSON, ["Q28640", "Q2628882"], qid="Q131524")
        assert plan_retype(row, row.wikidata_instance_of) is EntityType.TOPIC

    def test_the_human_class_typed_person_becomes_a_topic(self):
        human = _entity("person:human", EntityType.PERSON, ["Q55983715"], qid="Q5")
        assert plan_retype(human, human.wikidata_instance_of) is EntityType.TOPIC

    def test_only_works_leaves_kinds_of_person_alone(self):
        row = _entity("topic:entrepreneur", EntityType.PERSON, ["Q28640"], qid="Q131524")
        assert plan_retype(row, row.wikidata_instance_of, only_works=True) is None


class TestSuperclasses:
    def test_a_town_typed_company_becomes_a_topic(self):
        knin = _entity("company:knin", EntityType.PERSON, ["Q15105893"], qid="Q208514")
        plans = list(plan_backfill([knin], None, superclasses=lambda q: ["Q3957", "Q515"]))
        assert [(p.entity.id, p.new_type) for p in plans] == [("company:knin", EntityType.TOPIC)]

    def test_without_a_lookup_the_town_keeps_its_type(self):
        knin = _entity("company:knin", EntityType.PERSON, ["Q15105893"], qid="Q208514")
        assert list(plan_backfill([knin], None)) == []


class TestPlanBackfill:
    def test_cached_only_never_fetches_and_skips_entities_without_p31(self):
        entities = [
            _entity("topic:margin-call", EntityType.TOPIC, FILM),
            _entity("company:uncached", EntityType.COMPANY),
        ]
        plans = list(plan_backfill(entities, None, only_works=True))
        assert [(p.entity.id, p.new_type) for p in plans] == [("topic:margin-call", EntityType.PRODUCT)]

    def test_fetches_only_what_is_not_stored(self):
        fetched = []

        def fetch(qid):
            fetched.append(qid)
            return ["Q11424"]

        entities = [
            _entity("topic:cached-film", EntityType.TOPIC, FILM, qid="Q10"),
            _entity("topic:uncached-film", EntityType.TOPIC, qid="Q20"),
        ]
        plans = list(plan_backfill(entities, fetch, only_works=True))
        assert fetched == ["Q20"]
        assert {p.entity.id for p in plans if p.new_type is EntityType.PRODUCT} == {
            "topic:cached-film",
            "topic:uncached-film",
        }

    def test_a_fetched_p31_with_no_type_change_is_a_cache_update(self):
        (plan,) = plan_backfill([_entity("company:acme", EntityType.COMPANY)], lambda q: ["Q4830453"], only_works=True)
        assert plan.new_type is None and plan.caches_p31

    def test_nothing_to_do_is_not_yielded(self):
        assert (
            list(plan_backfill([_entity("company:acme", EntityType.COMPANY, ["Q4830453"])], None, only_works=True))
            == []
        )


class TestPersonDemotionsAreHeld:
    """Prod dry run 2026-10-27: a person linked to a same-name place or work
    (Bellingham the footballer → the US city) is a wrong link, not a wrong
    type, so the backfill must not turn people into places on its own."""

    CITY = ["Q515"]  # city

    def test_a_person_linked_to_a_place_is_held(self):
        (plan,) = plan_backfill([_entity("person:bellingham", EntityType.PERSON, self.CITY)], None)
        assert plan.new_type is EntityType.TOPIC and plan.held

    def test_a_released_person_moves(self):
        entity = _entity("person:london", EntityType.PERSON, self.CITY)
        (plan,) = plan_backfill([entity], None, release={"person:london"})
        assert plan.new_type is EntityType.TOPIC and not plan.held

    def test_other_types_are_never_held(self):
        (plan,) = plan_backfill([_entity("company:knin", EntityType.COMPANY, self.CITY)], None)
        assert plan.new_type is EntityType.TOPIC and not plan.held

    def test_a_human_stays_a_person_and_is_not_held(self):
        assert list(plan_backfill([_entity("person:ada", EntityType.PERSON, ["Q5"])], None)) == []

    def test_a_kind_of_person_is_not_held(self):
        # "child" (population group) and the class item "human" (Q5) are
        # common nouns, not name collisions: they move without review.
        child = _entity("person:child", EntityType.PERSON, ["Q851990", "Q134264207"], qid="Q7569")
        human = _entity("person:human", EntityType.PERSON, ["Q55983715"], qid="Q5")
        plans = list(plan_backfill([child, human], None))
        assert [(p.entity.id, p.new_type, p.held) for p in plans] == [
            ("person:child", EntityType.TOPIC, False),
            ("person:human", EntityType.TOPIC, False),
        ]

    def test_an_ai_model_typed_person_is_not_held(self):
        # "Sora" and "Claude" tagged person by GLiNER: right link, wrong tag.
        sora = _entity("company:sora-text-to-video-model", EntityType.PERSON, ["Q115517227"], qid="Q124544998")
        claude = _entity("product:claude-ai", EntityType.PERSON, ["Q870780", "Q124629760"], qid="Q118876059")
        plans = list(plan_backfill([sora, claude], None))
        assert [(p.entity.id, p.new_type, p.held) for p in plans] == [
            ("company:sora-text-to-video-model", EntityType.PRODUCT, False),
            ("product:claude-ai", EntityType.PRODUCT, False),
        ]
