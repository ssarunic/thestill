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
