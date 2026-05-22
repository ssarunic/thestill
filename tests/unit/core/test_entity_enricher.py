"""Spec #45 Tier 0 — EntityEnricher tests.

Exercises the enricher with stub Wikidata/Wikipedia clients (no network):

- person + company facts/affiliations are assembled correctly
- founder/CEO/employer QIDs cross-link to local entity pages when known
- a transient source failure is recorded as FAILED + retry_after, never
  cached as "no data" (spec #42 FM-1); an empty source is EMPTY
- a label-lookup outage degrades to "skip referenced facts", not failure
"""

from __future__ import annotations

from datetime import timedelta
from typing import Optional

from thestill.core.entity_enricher import EntityEnricher
from thestill.core.wikidata_client import WikidataEntity
from thestill.core.wikipedia_client import WikipediaSummary
from thestill.models.enrichment import EnrichmentStatus, EnrichmentUnavailable
from thestill.models.entities import EntityRecord, EntityType


class StubWikidata:
    def __init__(self, *, facts=None, facts_exc=None, labels=None, labels_exc=None):
        self._facts = facts
        self._facts_exc = facts_exc
        self._labels = labels or {}
        self._labels_exc = labels_exc

    def fetch_facts(self, qid, *, language="en"):
        if self._facts_exc:
            raise self._facts_exc
        return self._facts

    def fetch_labels(self, qids, *, language="en"):
        if self._labels_exc:
            raise self._labels_exc
        return {q: self._labels[q] for q in qids if q in self._labels}


class StubWikipedia:
    def __init__(self, *, summary=None, exc=None):
        self._summary = summary
        self._exc = exc

    def fetch_summary(self, title, *, language="en"):
        if self._exc:
            raise self._exc
        return self._summary


def _person_record() -> EntityRecord:
    return EntityRecord(
        id="person:elon-musk", type=EntityType.PERSON, canonical_name="Elon Musk", wikidata_qid="Q317521"
    )


def _company_record() -> EntityRecord:
    return EntityRecord(id="company:tesla", type=EntityType.COMPANY, canonical_name="Tesla", wikidata_qid="Q478214")


def _person_wd() -> WikidataEntity:
    return WikidataEntity(
        qid="Q317521",
        label="Elon Musk",
        description="business magnate",
        sitelinks={"enwiki": "Elon Musk"},
        string_claims={"P18": ["Musk.jpg"], "P856": ["https://x.com"], "P2002": ["elonmusk"]},
        time_claims={"P569": ["+1971-06-28T00:00:00Z"]},
        entity_claims={"P106": ["Q131524"], "P108": ["Q478214"]},
    )


def _company_wd() -> WikidataEntity:
    return WikidataEntity(
        qid="Q478214",
        label="Tesla",
        description="American EV company",
        sitelinks={"enwiki": "Tesla, Inc."},
        string_claims={"P154": ["Tesla logo.svg"], "P856": ["https://www.tesla.com/"]},
        time_claims={"P571": ["+2003-07-01T00:00:00Z"]},
        quantity_claims={"P1128": ["+127855"]},
        entity_claims={"P112": ["Q317521"], "P169": ["Q444"], "P452": ["Q200"]},
    )


def _enricher(wikidata, wikipedia, find=lambda qid: None) -> EntityEnricher:
    return EntityEnricher(
        wikidata_client=wikidata,
        wikipedia_client=wikipedia,
        find_entity_by_qid=find,
        retry_backoff=timedelta(hours=6),
    )


def _facts_by_label(enrichment) -> dict:
    return {f.label: f for f in enrichment.facts}


class TestPersonEnrichment:
    def test_builds_facts_and_image(self):
        wd = StubWikidata(facts=_person_wd(), labels={"Q131524": "entrepreneur", "Q478214": "Tesla, Inc."})
        wp = StubWikipedia(summary=WikipediaSummary(extract="Lead.", url="https://en.wikipedia.org/wiki/Elon_Musk"))
        e = _enricher(wd, wp).enrich(_person_record())

        assert e.wikidata_status == EnrichmentStatus.OK
        assert e.wikipedia_status == EnrichmentStatus.OK
        assert e.headline == "business magnate"
        assert e.image_url and "Special:FilePath" in e.image_url and "Musk.jpg" in e.image_url
        assert e.image_attribution == "Wikimedia Commons"
        assert e.wikipedia_extract == "Lead."

        facts = _facts_by_label(e)
        assert facts["Born"].value == "June 28, 1971"
        assert facts["Occupation"].value == "entrepreneur"
        assert facts["Website"].value == "x.com" and facts["Website"].url == "https://x.com"
        assert facts["X (Twitter)"].url == "https://x.com/elonmusk"

    def test_employer_cross_links_to_local_entity(self):
        company = _company_record()
        wd = StubWikidata(facts=_person_wd(), labels={"Q478214": "Tesla, Inc."})
        wp = StubWikipedia(summary=None)
        # Q478214 is a company we already hold → affiliation links to its page.
        e = _enricher(wd, wp, find=lambda qid: company if qid == "Q478214" else None).enrich(_person_record())

        works_at = [a for a in e.affiliations if a.relation == "Works at"]
        assert len(works_at) == 1
        assert works_at[0].entity_id == "company:tesla"
        assert works_at[0].entity_type == "company"
        # Prefers our canonical name over the Wikidata label.
        assert works_at[0].label == "Tesla"


class TestCompanyEnrichment:
    def test_builds_company_facts_and_affiliations(self):
        founder = _person_record()
        wd = StubWikidata(
            facts=_company_wd(),
            labels={"Q317521": "Elon Musk", "Q444": "Some CEO", "Q200": "automotive"},
        )
        wp = StubWikipedia(summary=WikipediaSummary(extract="Tesla is a company."))
        e = _enricher(wd, wp, find=lambda qid: founder if qid == "Q317521" else None).enrich(_company_record())

        assert e.image_url and "Tesla_logo.svg" in e.image_url
        facts = _facts_by_label(e)
        assert facts["Founded"].value == "July 1, 2003"
        assert facts["Employees"].value == "127,855"
        assert facts["Industry"].value == "automotive"
        assert facts["Website"].value == "tesla.com"

        by_relation = {a.relation: a for a in e.affiliations}
        assert by_relation["Founder"].entity_id == "person:elon-musk"  # cross-linked
        assert by_relation["CEO"].label == "Some CEO" and by_relation["CEO"].entity_id is None

    def test_affiliations_are_capped_per_relation(self):
        # Wikidata lists every historical CEO; we cap to keep the page tidy.
        ceo_qids = [f"Q{n}" for n in range(20)]
        wd_entity = WikidataEntity(
            qid="Q478214", description="co", sitelinks={"enwiki": "Co"}, entity_claims={"P169": ceo_qids}
        )
        wd = StubWikidata(facts=wd_entity, labels={q: f"CEO {q}" for q in ceo_qids})
        e = _enricher(wd, StubWikipedia(summary=None)).enrich(_company_record())
        ceos = [a for a in e.affiliations if a.relation == "CEO"]
        assert len(ceos) == 6


class TestFailureModes:
    def test_wikidata_transient_failure_is_failed_not_empty(self):
        wd = StubWikidata(facts_exc=EnrichmentUnavailable("503"))
        wp = StubWikipedia(summary=None)
        e = _enricher(wd, wp).enrich(_person_record())
        assert e.wikidata_status == EnrichmentStatus.FAILED
        assert e.retry_after is not None
        # Wikidata failed → no sitelink to follow. Wikipedia stays PENDING
        # (never attempted), NOT EMPTY — EMPTY would falsely assert it had
        # nothing (spec #42 FM-1).
        assert e.wikipedia_status == EnrichmentStatus.PENDING
        assert not e.has_content()

    def test_wikipedia_failure_leaves_wikidata_ok(self):
        wd = StubWikidata(facts=_person_wd(), labels={"Q131524": "entrepreneur"})
        wp = StubWikipedia(exc=EnrichmentUnavailable("timeout"))
        e = _enricher(wd, wp).enrich(_person_record())
        assert e.wikidata_status == EnrichmentStatus.OK
        assert e.wikipedia_status == EnrichmentStatus.FAILED
        assert e.retry_after is not None
        assert e.has_content()  # Wikidata facts still present

    def test_wikidata_empty_entity_is_empty(self):
        wd = StubWikidata(facts=None)
        wp = StubWikipedia(summary=None)
        e = _enricher(wd, wp).enrich(_person_record())
        assert e.wikidata_status == EnrichmentStatus.EMPTY
        assert e.wikipedia_status == EnrichmentStatus.EMPTY
        assert not e.has_content()

    def test_label_outage_degrades_without_failing(self):
        # fetch_facts OK but fetch_labels raises → referenced facts skipped,
        # status stays OK, literal facts (Born) still present.
        wd = StubWikidata(facts=_person_wd(), labels_exc=EnrichmentUnavailable("labels down"))
        wp = StubWikipedia(summary=None)
        e = _enricher(wd, wp).enrich(_person_record())
        assert e.wikidata_status == EnrichmentStatus.OK
        facts = _facts_by_label(e)
        assert "Born" in facts
        assert "Occupation" not in facts  # needed a label we couldn't fetch

    def test_entity_without_qid_makes_no_fetch(self):
        wd = StubWikidata(facts=_person_wd())
        wp = StubWikipedia(summary=WikipediaSummary(extract="x"))
        no_qid = EntityRecord(id="person:nobody", type=EntityType.PERSON, canonical_name="Nobody")
        e = _enricher(wd, wp).enrich(no_qid)
        assert e.wikidata_status == EnrichmentStatus.EMPTY
        assert e.wikipedia_status == EnrichmentStatus.EMPTY
        assert not e.has_content()


class TestImageFallback:
    def test_uses_wikipedia_image_when_wikidata_has_none(self):
        wd_entity = WikidataEntity(qid="Q1", description="d", sitelinks={"enwiki": "X"})
        wd = StubWikidata(facts=wd_entity)
        wp = StubWikipedia(
            summary=WikipediaSummary(extract="x", original_image_url="https://upload.wikimedia.org/X.jpg")
        )
        e = _enricher(wd, wp).enrich(_person_record())
        assert e.image_url == "https://upload.wikimedia.org/X.jpg"
        assert e.image_attribution == "Wikipedia"
