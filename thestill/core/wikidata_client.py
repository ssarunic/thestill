# Copyright 2025-2026 Thestill
#
# Licensed under the Apache License, Version 2.0 (the "License");
# you may not use this file except in compliance with the License.
# You may obtain a copy of the License at
#
#     http://www.apache.org/licenses/LICENSE-2.0

"""Spec #28 §5.2 — Wikidata REST client for ``instance of`` (P31) lookups.

We resolve QIDs in :class:`thestill.core.entity_resolver.EntityResolver`
via ReFinED, but ReFinED returns only the QID — not the entity's
classification properties. To gate buckets by P31 (see
``entity_type_rules``) we need a separate lookup.

This client is intentionally tiny: a single ``GET`` against
``Special:EntityData/{QID}.json`` extracts the P31 claims without
touching the Wikidata SPARQL endpoint, and is far cheaper than running
a full Wikidata mirror locally. Failures are silent — a missing P31
means "fall back to whatever the resolver guessed".

Caching is the caller's responsibility (the entity row's
``wikidata_instance_of`` column persists results across runs). This
class only adds an in-process LRU on top so a single resolve batch
doesn't hammer Wikidata for the same QID twice.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from functools import lru_cache
from typing import Dict, Iterable, List, Optional

import requests
from structlog import get_logger

from ..models.enrichment import EnrichmentUnavailable

logger = get_logger(__name__)


WIKIDATA_ENTITY_URL = "https://www.wikidata.org/wiki/Special:EntityData/{qid}.json"
WIKIDATA_API_URL = "https://www.wikidata.org/w/api.php"
DEFAULT_TIMEOUT_SEC = 5.0
DEFAULT_USER_AGENT = "thestill-podcast-pipeline/0.1 (https://github.com/sasasarunic/thestill)"
# wbgetentities accepts up to 50 ids per request.
_LABEL_BATCH_SIZE = 50


class WikidataClient:
    """Tiny client around ``Special:EntityData/<QID>.json``.

    The shape of the JSON response is documented at
    https://www.wikidata.org/wiki/Wikidata:Data_access — we only need
    ``entities[QID].claims.P31[*].mainsnak.datavalue.value.id``.

    The class is constructed once and reused across resolutions; the
    ``functools.lru_cache`` on :meth:`fetch_p31` keeps recently-fetched
    QIDs in memory for the rest of the process lifetime so backfill
    runs over hundreds of entities don't make redundant requests.
    """

    def __init__(
        self,
        *,
        timeout_sec: float = DEFAULT_TIMEOUT_SEC,
        user_agent: str = DEFAULT_USER_AGENT,
        cache_size: int = 4096,
    ):
        self.timeout_sec = timeout_sec
        self.user_agent = user_agent
        # Wrap _fetch_p31_uncached with an instance-level LRU so the
        # cache is per-client (tests can construct a fresh client to
        # bypass it).
        self._cached = lru_cache(maxsize=cache_size)(self._fetch_p31_uncached)
        # Spec #45 — separate caches for the enrichment surface. ``fetch_facts``
        # raises on transient failure (unlike ``fetch_p31``); ``lru_cache``
        # does not memoise exceptions, so a failed fetch stays retryable.
        self._facts_cached = lru_cache(maxsize=cache_size)(self._fetch_facts_uncached)
        # Referenced-QID labels are reused across entities (e.g. "Q5" human,
        # country/industry QIDs), so a process-lifetime dict cache pays off.
        self._label_cache: Dict[str, str] = {}

    def fetch_p31(self, qid: str) -> List[str]:
        """Return the list of P31 QIDs for ``qid``, or ``[]`` on error.

        Never raises: a HTTP error, parse error, missing-claim error
        all collapse to ``[]``. The caller should treat empty as "no
        signal" rather than "definitely no P31" — at the resolver level
        that means "keep the original fallback type".
        """
        if not qid:
            return []
        return self._cached(qid)

    def _fetch_p31_uncached(self, qid: str) -> List[str]:
        url = WIKIDATA_ENTITY_URL.format(qid=qid)
        try:
            resp = requests.get(
                url,
                timeout=self.timeout_sec,
                headers={"User-Agent": self.user_agent, "Accept": "application/json"},
            )
        except requests.RequestException as exc:
            logger.warning("wikidata_fetch_failed", qid=qid, error=str(exc))
            return []
        if resp.status_code != 200:
            logger.warning("wikidata_fetch_non_200", qid=qid, status=resp.status_code)
            return []
        try:
            payload = resp.json()
        except ValueError as exc:
            logger.warning("wikidata_parse_failed", qid=qid, error=str(exc))
            return []
        return _extract_p31_qids(payload, qid)

    # ------------------------------------------------------------------
    # Spec #45 — enrichment surface (facts + label resolution)
    # ------------------------------------------------------------------

    def fetch_facts(self, qid: str, *, language: str = "en") -> Optional["WikidataEntity"]:
        """Return the parsed claims/labels/sitelinks for ``qid``.

        Returns ``None`` when the QID resolves to no entity (genuinely
        empty). Raises :class:`EnrichmentUnavailable` on a *transient*
        failure (network/non-200/parse) so the caller can record
        ``FAILED`` and retry later rather than caching "no data".
        """
        if not qid:
            return None
        return self._facts_cached(qid, language)

    def _fetch_facts_uncached(self, qid: str, language: str) -> Optional["WikidataEntity"]:
        url = WIKIDATA_ENTITY_URL.format(qid=qid)
        try:
            resp = requests.get(
                url,
                timeout=self.timeout_sec,
                headers={"User-Agent": self.user_agent, "Accept": "application/json"},
            )
        except requests.RequestException as exc:
            raise EnrichmentUnavailable(f"wikidata facts fetch failed for {qid}: {exc}") from exc
        if resp.status_code != 200:
            raise EnrichmentUnavailable(f"wikidata facts returned {resp.status_code} for {qid}")
        try:
            payload = resp.json()
        except ValueError as exc:
            raise EnrichmentUnavailable(f"wikidata facts unparseable for {qid}: {exc}") from exc
        return _parse_entity_payload(payload, qid, language)

    def fetch_labels(self, qids: Iterable[str], *, language: str = "en") -> Dict[str, str]:
        """Resolve referenced QIDs (occupation, founders, …) to readable
        labels, batching uncached ids into ``wbgetentities`` requests.

        Best-effort: a QID with no label in ``language`` (or ``en``
        fallback) is simply omitted from the result. Raises
        :class:`EnrichmentUnavailable` on a transient request failure —
        the enricher catches it and degrades to "skip the referenced
        facts" without failing the whole entity.
        """
        out: Dict[str, str] = {}
        missing: List[str] = []
        for qid in dict.fromkeys(qids):  # dedupe, preserve order
            if not qid:
                continue
            cached = self._label_cache.get(qid)
            if cached is not None:
                out[qid] = cached
            else:
                missing.append(qid)
        for start in range(0, len(missing), _LABEL_BATCH_SIZE):
            batch = missing[start : start + _LABEL_BATCH_SIZE]
            fetched = self._fetch_labels_batch(batch, language)
            for qid in batch:
                label = fetched.get(qid)
                if label:
                    self._label_cache[qid] = label
                    out[qid] = label
        return out

    def _fetch_labels_batch(self, qids: List[str], language: str) -> Dict[str, str]:
        params = {
            "action": "wbgetentities",
            "ids": "|".join(qids),
            "props": "labels",
            "languages": f"{language}|en",
            "format": "json",
        }
        try:
            resp = requests.get(
                WIKIDATA_API_URL,
                params=params,
                timeout=self.timeout_sec,
                headers={"User-Agent": self.user_agent, "Accept": "application/json"},
            )
        except requests.RequestException as exc:
            raise EnrichmentUnavailable(f"wikidata labels fetch failed: {exc}") from exc
        if resp.status_code != 200:
            raise EnrichmentUnavailable(f"wikidata labels returned {resp.status_code}")
        try:
            payload = resp.json()
        except ValueError as exc:
            raise EnrichmentUnavailable(f"wikidata labels unparseable: {exc}") from exc
        entities = payload.get("entities") or {}
        result: Dict[str, str] = {}
        for qid, entity in entities.items():
            label = _localized(entity.get("labels") if isinstance(entity, dict) else None, language)
            if label:
                result[qid] = label
        return result


@dataclass(frozen=True)
class WikidataEntity:
    """Parsed projection of a ``Special:EntityData`` payload (spec #45).

    Claims are bucketed by datavalue type so the enricher can read the
    properties it cares about without re-walking the raw JSON. Entity
    references (``entity_claims``) carry QIDs that
    :meth:`WikidataClient.fetch_labels` turns into readable text.
    """

    qid: str
    label: Optional[str] = None
    description: Optional[str] = None
    sitelinks: Dict[str, str] = field(default_factory=dict)
    string_claims: Dict[str, List[str]] = field(default_factory=dict)
    time_claims: Dict[str, List[str]] = field(default_factory=dict)
    quantity_claims: Dict[str, List[str]] = field(default_factory=dict)
    entity_claims: Dict[str, List[str]] = field(default_factory=dict)

    def first_string(self, prop: str) -> Optional[str]:
        vals = self.string_claims.get(prop)
        return vals[0] if vals else None

    def first_time(self, prop: str) -> Optional[str]:
        vals = self.time_claims.get(prop)
        return vals[0] if vals else None

    def first_quantity(self, prop: str) -> Optional[str]:
        vals = self.quantity_claims.get(prop)
        return vals[0] if vals else None

    def entity_refs(self, prop: str) -> List[str]:
        return self.entity_claims.get(prop, [])

    def sitelink_title(self, language: str) -> Optional[str]:
        return self.sitelinks.get(f"{language}wiki") or self.sitelinks.get("enwiki")

    def referenced_qids(self) -> List[str]:
        """Every QID referenced by an entity-valued claim (for label batching)."""
        out: List[str] = []
        for qids in self.entity_claims.values():
            out.extend(qids)
        return out


def _localized(node: Optional[dict], language: str) -> Optional[str]:
    """Pull a value from a Wikidata labels/descriptions block, preferring
    ``language`` and falling back to English."""
    block = node or {}
    entry = block.get(language) or block.get("en")
    if isinstance(entry, dict):
        value = entry.get("value")
        return value if isinstance(value, str) and value else None
    return None


def _parse_entity_payload(payload: dict, qid: str, language: str) -> Optional["WikidataEntity"]:
    """Bucket a ``Special:EntityData`` payload into a :class:`WikidataEntity`.

    Defensive at every level — Wikidata returns redirects and stripped
    bodies for retired QIDs. Unknown datavalue types are ignored.
    """
    entities = payload.get("entities") or {}
    entity = entities.get(qid)
    if not entity and entities:
        entity = next(iter(entities.values()))
    if not isinstance(entity, dict):
        return None

    sitelinks_raw = entity.get("sitelinks") or {}
    sitelinks = {
        key: val["title"]
        for key, val in sitelinks_raw.items()
        if isinstance(val, dict) and isinstance(val.get("title"), str)
    }

    string_claims: Dict[str, List[str]] = {}
    time_claims: Dict[str, List[str]] = {}
    quantity_claims: Dict[str, List[str]] = {}
    entity_claims: Dict[str, List[str]] = {}
    claims = entity.get("claims") or {}
    for prop, snaks in claims.items():
        if not isinstance(snaks, list):
            continue
        for snak in snaks:
            try:
                datavalue = snak["mainsnak"]["datavalue"]
            except (KeyError, TypeError):
                continue
            vtype = datavalue.get("type")
            value = datavalue.get("value")
            if vtype == "string" and isinstance(value, str):
                string_claims.setdefault(prop, []).append(value)
            elif vtype == "time" and isinstance(value, dict) and value.get("time"):
                time_claims.setdefault(prop, []).append(value["time"])
            elif vtype == "quantity" and isinstance(value, dict) and value.get("amount"):
                quantity_claims.setdefault(prop, []).append(value["amount"])
            elif vtype == "wikibase-entityid" and isinstance(value, dict) and value.get("id"):
                entity_claims.setdefault(prop, []).append(value["id"])

    return WikidataEntity(
        qid=qid,
        label=_localized(entity.get("labels"), language),
        description=_localized(entity.get("descriptions"), language),
        sitelinks=sitelinks,
        string_claims=string_claims,
        time_claims=time_claims,
        quantity_claims=quantity_claims,
        entity_claims=entity_claims,
    )


def _extract_p31_qids(payload: dict, qid: str) -> List[str]:
    """Pull the P31 entity ids out of a ``Special:EntityData`` JSON
    response. Defensive against missing keys at every level — Wikidata
    occasionally returns redirects or stripped responses for retired
    QIDs.
    """
    entities = payload.get("entities") or {}
    entity = entities.get(qid)
    if not entity:
        # Wikidata sometimes returns the redirected QID under a different
        # key. Take the first entity in the dict as a fallback.
        if entities:
            entity = next(iter(entities.values()))
    if not entity:
        return []
    claims = (entity or {}).get("claims") or {}
    p31_claims = claims.get("P31") or []
    out: List[str] = []
    for claim in p31_claims:
        try:
            target = claim["mainsnak"]["datavalue"]["value"]["id"]
        except (KeyError, TypeError):
            continue
        if isinstance(target, str) and target.startswith("Q"):
            out.append(target)
    return out


class NullWikidataClient:
    """No-op client used when P31 gating should be skipped.

    Returns ``[]`` for every QID — keeps the resolver's call shape
    identical without touching the network. Tests inject this when
    they want to assert pre-gating behavior.
    """

    def fetch_p31(self, qid: str) -> List[str]:  # noqa: D401 — protocol shim
        return []

    def fetch_facts(self, qid: str, *, language: str = "en") -> Optional["WikidataEntity"]:
        return None

    def fetch_labels(self, qids: Iterable[str], *, language: str = "en") -> Dict[str, str]:
        return {}
