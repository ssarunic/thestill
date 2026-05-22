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

"""Spec #45 — Wikipedia REST summary client for entity enrichment.

A thin sibling to :class:`thestill.core.wikidata_client.WikidataClient`:
one ``GET`` against the Wikipedia REST ``page/summary`` endpoint returns
the lead paragraph (``extract``), the canonical page URL, and a
thumbnail — a cheap "what is this" plus an image fallback when Wikidata
``P18``/``P154`` is absent.

The page title comes from the QID's sitelinks (already in the Wikidata
``EntityData`` payload), so no separate title search is needed.

Failure semantics mirror the enrichment contract: a genuine miss
(404 / disambiguation page) returns ``None``; a transient failure
(network / non-200 / parse) raises :class:`EnrichmentUnavailable`.
"""

from __future__ import annotations

from dataclasses import dataclass
from functools import lru_cache
from typing import Optional
from urllib.parse import quote

import requests
from structlog import get_logger

from ..models.enrichment import EnrichmentUnavailable

logger = get_logger(__name__)

WIKIPEDIA_SUMMARY_URL = "https://{lang}.wikipedia.org/api/rest_v1/page/summary/{title}"
DEFAULT_TIMEOUT_SEC = 5.0
DEFAULT_USER_AGENT = "thestill-podcast-pipeline/0.1 (https://github.com/sasasarunic/thestill)"


@dataclass(frozen=True)
class WikipediaSummary:
    """Parsed projection of a REST ``page/summary`` response."""

    extract: Optional[str] = None
    url: Optional[str] = None
    thumbnail_url: Optional[str] = None
    original_image_url: Optional[str] = None


class WikipediaClient:
    """Tiny client around the Wikipedia REST ``page/summary`` endpoint.

    Constructed once and reused; an instance-level LRU keeps recently
    fetched titles in memory so a backfill over many entities doesn't
    re-request the same page.
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
        self._cached = lru_cache(maxsize=cache_size)(self._fetch_summary_uncached)

    def fetch_summary(self, title: str, *, language: str = "en") -> Optional[WikipediaSummary]:
        """Return the page summary for ``title`` in ``language``.

        ``None`` when the page is missing or is a disambiguation page.
        Raises :class:`EnrichmentUnavailable` on a transient failure.
        """
        if not title:
            return None
        return self._cached(title, language)

    def _fetch_summary_uncached(self, title: str, language: str) -> Optional[WikipediaSummary]:
        # ``quote`` with an empty safe set so slashes in titles are encoded.
        url = WIKIPEDIA_SUMMARY_URL.format(lang=language, title=quote(title.replace(" ", "_"), safe=""))
        try:
            resp = requests.get(
                url,
                timeout=self.timeout_sec,
                headers={"User-Agent": self.user_agent, "Accept": "application/json"},
            )
        except requests.RequestException as exc:
            raise EnrichmentUnavailable(f"wikipedia summary fetch failed for {title!r}: {exc}") from exc
        if resp.status_code == 404:
            # Genuine miss — the page does not exist.
            return None
        if resp.status_code != 200:
            raise EnrichmentUnavailable(f"wikipedia summary returned {resp.status_code} for {title!r}")
        try:
            payload = resp.json()
        except ValueError as exc:
            raise EnrichmentUnavailable(f"wikipedia summary unparseable for {title!r}: {exc}") from exc
        # Disambiguation pages carry no useful prose — treat as a miss.
        if payload.get("type") == "disambiguation":
            return None
        return _parse_summary_payload(payload)


def _parse_summary_payload(payload: dict) -> WikipediaSummary:
    extract = payload.get("extract") or None
    content_urls = payload.get("content_urls") or {}
    desktop = content_urls.get("desktop") if isinstance(content_urls, dict) else None
    url = desktop.get("page") if isinstance(desktop, dict) else None
    thumbnail = payload.get("thumbnail") or {}
    original = payload.get("originalimage") or {}
    return WikipediaSummary(
        extract=extract,
        url=url,
        thumbnail_url=thumbnail.get("source") if isinstance(thumbnail, dict) else None,
        original_image_url=original.get("source") if isinstance(original, dict) else None,
    )


class NullWikipediaClient:
    """No-op client for tests / offline runs — every lookup is a miss."""

    def fetch_summary(self, title: str, *, language: str = "en") -> Optional[WikipediaSummary]:
        return None
