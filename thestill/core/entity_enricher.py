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

"""Spec #45 Tier 0 — assemble entity-page enrichment from Wikidata + Wikipedia.

``EntityEnricher.enrich`` is intentionally **side-effect-free**: it
fetches and builds an :class:`EntityEnrichment`, but does not persist it.
The caller (``thestill enrich-entities``) owns the ``upsert_enrichment``
write. That keeps the enricher trivially testable with stub clients and
keeps all DB writes on one side of the boundary.

Per-source failures are recorded, not raised: a transient Wikidata/
Wikipedia outage marks that source ``FAILED`` with a ``retry_after`` and
leaves the rest of the record intact — never cached as "no data"
(spec #42 FM-1).
"""

from __future__ import annotations

import re
from datetime import datetime, timedelta, timezone
from typing import Callable, List, Optional
from urllib.parse import quote

from structlog import get_logger

from ..models.enrichment import EnrichmentStatus, EnrichmentUnavailable, EntityAffiliation, EntityEnrichment, EntityFact
from ..models.entities import EntityRecord, EntityType
from .wikidata_client import WikidataClient, WikidataEntity
from .wikipedia_client import WikipediaClient, WikipediaSummary

logger = get_logger(__name__)

# Bump when the fetch/parse logic changes so ``entity_ids_needing_enrichment``
# treats older rows as stale and refreshes them.
ENRICHMENT_SCHEMA_VERSION = 1

_MONTHS = (
    "January",
    "February",
    "March",
    "April",
    "May",
    "June",
    "July",
    "August",
    "September",
    "October",
    "November",
    "December",
)

# Wikidata properties we read. Grouped by entity type so the page only
# shows fields that make sense for a person vs a company.
_P_IMAGE_PERSON = "P18"
_P_IMAGE_COMPANY = "P154"  # logo
_P_WEBSITE = "P856"
_P_TWITTER = "P2002"
_P_INSTAGRAM = "P2003"


class EntityEnricher:
    """Builds Tier-0 enrichment for a single QID-bearing entity.

    Clients are injected so tests can pass ``Null*Client`` / stubs;
    ``find_entity_by_qid`` cross-links founders/CEO/employer to local
    entity pages when we already hold the referenced entity.
    """

    def __init__(
        self,
        *,
        wikidata_client: WikidataClient,
        wikipedia_client: WikipediaClient,
        find_entity_by_qid: Callable[[str], Optional[EntityRecord]],
        language: str = "en",
        retry_backoff: timedelta = timedelta(hours=6),
    ):
        self._wikidata = wikidata_client
        self._wikipedia = wikipedia_client
        self._find_entity_by_qid = find_entity_by_qid
        self._language = language
        self._retry_backoff = retry_backoff

    def enrich(self, entity: EntityRecord) -> EntityEnrichment:
        """Fetch + assemble enrichment for ``entity`` (does not persist)."""
        now = datetime.now(timezone.utc)
        enrichment = EntityEnrichment(
            entity_id=entity.id,
            schema_version=ENRICHMENT_SCHEMA_VERSION,
            created_at=now,
            updated_at=now,
        )

        if not entity.wikidata_qid:
            # Defensive — callers gate on a QID. Nothing external to fetch.
            enrichment.wikidata_status = EnrichmentStatus.EMPTY
            enrichment.wikipedia_status = EnrichmentStatus.EMPTY
            return enrichment

        wd = self._enrich_from_wikidata(entity, enrichment, now)
        self._enrich_from_wikipedia(wd, enrichment, now)
        return enrichment

    # ------------------------------------------------------------------
    # Wikidata
    # ------------------------------------------------------------------

    def _enrich_from_wikidata(
        self, entity: EntityRecord, enrichment: EntityEnrichment, now: datetime
    ) -> Optional[WikidataEntity]:
        qid = entity.wikidata_qid
        try:
            wd = self._wikidata.fetch_facts(qid, language=self._language)
        except EnrichmentUnavailable as exc:
            logger.warning("entity_enrich_wikidata_failed", entity_id=entity.id, qid=qid, error=str(exc))
            enrichment.wikidata_status = EnrichmentStatus.FAILED
            enrichment.retry_after = now + self._retry_backoff
            return None

        enrichment.wikidata_fetched_at = now
        if wd is None:
            enrichment.wikidata_status = EnrichmentStatus.EMPTY
            return None

        enrichment.headline = wd.description
        self._apply_image(entity, enrichment, wd)

        # Resolve referenced QIDs to readable labels (best-effort: a label
        # outage degrades to "skip referenced facts", not a failed entity).
        try:
            labels = self._wikidata.fetch_labels(wd.referenced_qids(), language=self._language)
        except EnrichmentUnavailable as exc:
            logger.warning("entity_enrich_labels_failed", entity_id=entity.id, qid=qid, error=str(exc))
            labels = {}

        if entity.type == EntityType.COMPANY:
            self._company_facts(wd, labels, enrichment)
        else:
            self._person_facts(wd, labels, enrichment)

        enrichment.wikidata_status = EnrichmentStatus.OK
        return wd

    def _apply_image(self, entity: EntityRecord, enrichment: EntityEnrichment, wd: WikidataEntity) -> None:
        # Companies prefer the logo (P154) then a photo; people the reverse.
        if entity.type == EntityType.COMPANY:
            filename = wd.first_string(_P_IMAGE_COMPANY) or wd.first_string(_P_IMAGE_PERSON)
        else:
            filename = wd.first_string(_P_IMAGE_PERSON) or wd.first_string(_P_IMAGE_COMPANY)
        if filename:
            enrichment.image_url = _commons_image_url(filename)
            enrichment.image_attribution = "Wikimedia Commons"

    def _person_facts(self, wd: WikidataEntity, labels: dict, enrichment: EntityEnrichment) -> None:
        facts: List[EntityFact] = []
        _append_time_fact(facts, "Born", wd.first_time("P569"))
        _append_time_fact(facts, "Died", wd.first_time("P570"))
        _append_label_fact(facts, "Born in", wd.entity_refs("P19"), labels, limit=1)
        _append_label_fact(facts, "Citizenship", wd.entity_refs("P27"), labels, limit=2)
        _append_label_fact(facts, "Occupation", wd.entity_refs("P106"), labels, limit=4)
        _append_url_facts(facts, wd)
        enrichment.facts = facts
        enrichment.affiliations = self._affiliations(wd.entity_refs("P108"), "Works at", labels)

    def _company_facts(self, wd: WikidataEntity, labels: dict, enrichment: EntityEnrichment) -> None:
        facts: List[EntityFact] = []
        _append_time_fact(facts, "Founded", wd.first_time("P571"))
        _append_label_fact(facts, "Headquarters", wd.entity_refs("P159"), labels, limit=1)
        _append_label_fact(facts, "Industry", wd.entity_refs("P452"), labels, limit=3)
        employees = wd.first_quantity("P1128")
        if employees:
            facts.append(EntityFact(label="Employees", value=_format_quantity(employees)))
        _append_label_fact(facts, "Products", wd.entity_refs("P1056"), labels, limit=6)
        _append_url_facts(facts, wd)
        enrichment.facts = facts
        affiliations = self._affiliations(wd.entity_refs("P112"), "Founder", labels)
        affiliations += self._affiliations(wd.entity_refs("P169"), "CEO", labels)
        enrichment.affiliations = affiliations

    def _affiliations(self, qids: List[str], relation: str, labels: dict, *, limit: int = 6) -> List[EntityAffiliation]:
        # Cap per relation: Wikidata lists every historical holder (e.g. all
        # past CEOs, a long employer history), which would flood the page.
        # We can't cheaply tell current from historical without qualifier
        # parsing (P582 end-time) — a noted Tier-1 follow-up — so we take the
        # first ``limit`` in claim order.
        out: List[EntityAffiliation] = []
        for qid in qids:
            local = self._find_entity_by_qid(qid)
            # Prefer our own canonical name; fall back to the Wikidata label.
            label = local.canonical_name if local else labels.get(qid)
            if not label:
                # No readable name and not a local entity — a bare QID chip
                # helps no one. Skip it.
                continue
            out.append(
                EntityAffiliation(
                    qid=qid,
                    label=label,
                    relation=relation,
                    entity_id=local.id if local else None,
                    entity_type=local.type.value if local else None,
                )
            )
            if len(out) >= limit:
                break
        return out

    # ------------------------------------------------------------------
    # Wikipedia
    # ------------------------------------------------------------------

    def _enrich_from_wikipedia(self, wd: Optional[WikidataEntity], enrichment: EntityEnrichment, now: datetime) -> None:
        title = wd.sitelink_title(self._language) if wd else None
        if not title:
            # When ``wd`` is None because Wikidata FAILED, we never got a
            # sitelink to follow — leave Wikipedia PENDING (not EMPTY) so the
            # retry actually attempts it. EMPTY is only honest when Wikidata
            # succeeded but carried no sitelink (spec #42 FM-1).
            if enrichment.wikidata_status != EnrichmentStatus.FAILED:
                enrichment.wikipedia_status = EnrichmentStatus.EMPTY
            return
        try:
            summary = self._wikipedia.fetch_summary(title, language=self._language)
        except EnrichmentUnavailable as exc:
            logger.warning("entity_enrich_wikipedia_failed", title=title, error=str(exc))
            enrichment.wikipedia_status = EnrichmentStatus.FAILED
            enrichment.retry_after = now + self._retry_backoff
            return
        enrichment.wikipedia_fetched_at = now
        if summary is None:
            enrichment.wikipedia_status = EnrichmentStatus.EMPTY
            return
        self._apply_wikipedia(enrichment, summary)
        enrichment.wikipedia_status = EnrichmentStatus.OK

    def _apply_wikipedia(self, enrichment: EntityEnrichment, summary: WikipediaSummary) -> None:
        enrichment.wikipedia_extract = summary.extract
        enrichment.wikipedia_url = summary.url
        # Use the Wikipedia lead image only when Wikidata had no photo/logo.
        if not enrichment.image_url:
            image = summary.original_image_url or summary.thumbnail_url
            if image:
                enrichment.image_url = image
                enrichment.image_attribution = "Wikipedia"


# ----------------------------------------------------------------------
# Fact-building helpers
# ----------------------------------------------------------------------


def _append_time_fact(facts: List[EntityFact], label: str, raw_time: Optional[str]) -> None:
    formatted = _format_wikidata_time(raw_time)
    if formatted:
        facts.append(EntityFact(label=label, value=formatted))


def _append_label_fact(facts: List[EntityFact], label: str, qids: List[str], labels: dict, *, limit: int) -> None:
    values = [labels[q] for q in qids if labels.get(q)][:limit]
    if values:
        facts.append(EntityFact(label=label, value=", ".join(values)))


def _append_url_facts(facts: List[EntityFact], wd: WikidataEntity) -> None:
    website = wd.first_string(_P_WEBSITE)
    if website:
        facts.append(EntityFact(label="Website", value=_display_url(website), url=website))
    twitter = wd.first_string(_P_TWITTER)
    if twitter:
        handle = twitter.lstrip("@")
        facts.append(EntityFact(label="X (Twitter)", value=f"@{handle}", url=f"https://x.com/{handle}"))
    instagram = wd.first_string(_P_INSTAGRAM)
    if instagram:
        handle = instagram.lstrip("@")
        facts.append(EntityFact(label="Instagram", value=f"@{handle}", url=f"https://www.instagram.com/{handle}/"))


def _commons_image_url(filename: str) -> str:
    """Build a thumbnail URL for a Wikimedia Commons file.

    ``Special:FilePath`` redirects to the actual upload host (``https``,
    permitted by the app CSP), so we never hard-code the hashed path.
    """
    encoded = quote(filename.replace(" ", "_"), safe="")
    return f"https://commons.wikimedia.org/wiki/Special:FilePath/{encoded}?width=400"


def _format_wikidata_time(raw: Optional[str]) -> Optional[str]:
    """Format a Wikidata time literal (``+1971-06-28T00:00:00Z``).

    Wikidata uses ``00`` for unknown month/day, so we degrade to
    ``Month Year`` or ``Year`` accordingly. Falls back to the raw date on
    anything unexpected (e.g. BCE years).
    """
    if not raw:
        return None
    date_part = raw.lstrip("+").split("T", 1)[0]
    parts = date_part.split("-")
    if len(parts) != 3:
        return date_part
    try:
        year, month, day = (int(p) for p in parts)
    except ValueError:
        return date_part
    if month == 0 or not 1 <= month <= 12:
        return str(year)
    if day == 0:
        return f"{_MONTHS[month - 1]} {year}"
    return f"{_MONTHS[month - 1]} {day}, {year}"


def _format_quantity(amount: str) -> str:
    try:
        number = int(float(amount.lstrip("+")))
    except (ValueError, AttributeError):
        return amount
    return f"{number:,}"


def _display_url(url: str) -> str:
    """Strip scheme / ``www.`` / trailing slash for a tidy display label."""
    display = re.sub(r"^https?://", "", url)
    display = re.sub(r"^www\.", "", display)
    return display.rstrip("/")
