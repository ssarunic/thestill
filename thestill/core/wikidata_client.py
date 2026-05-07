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

from functools import lru_cache
from typing import List

import requests
from structlog import get_logger

logger = get_logger(__name__)


WIKIDATA_ENTITY_URL = "https://www.wikidata.org/wiki/Special:EntityData/{qid}.json"
DEFAULT_TIMEOUT_SEC = 5.0
DEFAULT_USER_AGENT = "thestill-podcast-pipeline/0.1 (https://github.com/sasasarunic/thestill)"


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
