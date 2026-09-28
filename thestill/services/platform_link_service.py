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

"""
Resolve per-episode platform links, one show at a time (spec #87).

Phase 1 covers Apple Podcasts. The unit of work is a podcast: read the
candidates (unlinked episodes inside the show's newest window whose
not-found marker has expired), make at most ONE iTunes lookup, then write
found rows and not-found markers for every candidate. A show with no
candidates costs no request, which is the steady state.

Wired into both refresh paths (queued ``handle_refresh_feed`` and inline
``RefreshService``) as best-effort work, and into ``thestill link-platforms``
for backfills. Network helpers are injectable so tests never touch iTunes.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime, timedelta
from typing import Any, Callable, Dict, List, Optional
from urllib.parse import quote_plus, urlsplit, urlunsplit

from structlog import get_logger

from ..core.apple_episode_linker import apple_collection_id, match_candidates, normalize_title, parse_lookup_window
from ..core.spotify_resolver import itunes_lookup, itunes_search
from ..models.podcast import PlatformLink, PlatformLinkCandidate, Podcast
from ..utils.datetime_utils import now_utc

logger = get_logger(__name__)

# Apple's per-show episode index is capped at the newest 200 entries and
# ignores ``offset``; the candidate window mirrors it so older episodes are
# never candidates (spec #87 "Candidates and throttle").
APPLE_WINDOW = 200

PLATFORM_APPLE = "apple"


@dataclass(frozen=True)
class PodcastLinkOutcome:
    """What one pass over one podcast did — logged and echoed by the CLI."""

    podcast_id: str
    platform: str
    candidates: int
    linked: int = 0
    not_found: int = 0
    skipped: Optional[str] = None  # 'no_candidates' | 'no_apple_id' | 'lookup_failed'
    matched_by: Dict[str, int] = field(default_factory=dict)


@dataclass(frozen=True)
class _ShowMatch:
    """An Apple collection for the show. ``verified`` = accepted on feed-URL
    equality (or already stored); unverified = a unique exact-title search
    hit that must prove itself with a GUID / enclosure match before its URL
    is stored (spec #87 "Apple resolver" step 1)."""

    collection_id: str
    view_url: Optional[str]
    verified: bool


class PlatformLinkService:
    """Per-show platform link resolution (spec #87)."""

    def __init__(
        self,
        repository: Any,
        *,
        window: int = APPLE_WINDOW,
        recheck_hours: int = 24,
        lookup: Callable[[str], list] = itunes_lookup,
        search: Callable[[str], list] = itunes_search,
        clock: Callable[[], datetime] = now_utc,
    ) -> None:
        self.repository = repository
        self.window = window
        self.recheck = timedelta(hours=recheck_hours)
        self._lookup = lookup
        self._search = search
        self._clock = clock

    # ------------------------------------------------------------------
    # Public entry points
    # ------------------------------------------------------------------

    def link_podcast(self, podcast: Podcast, *, dry_run: bool = False, force: bool = False) -> PodcastLinkOutcome:
        """
        Resolve Apple links for one podcast. Never raises for network or
        data problems — a refresh must not fail because Apple is down.
        """
        now = self._clock()
        recheck_before = None if force else now - self.recheck
        candidates = self.repository.get_platform_link_candidates(
            podcast.id, PLATFORM_APPLE, window=self.window, recheck_before=recheck_before
        )
        if not candidates:
            return PodcastLinkOutcome(podcast.id, PLATFORM_APPLE, candidates=0, skipped="no_candidates")

        show = self._resolve_show(podcast, dry_run=dry_run)
        if show is None:
            return self._no_apple_id(podcast, candidates, now, dry_run=dry_run)
        collection_id = show.collection_id

        try:
            results = self._lookup(f"id={collection_id}&entity=podcastEpisode&limit={self.window}")
        except Exception as exc:  # noqa: BLE001 — any resolver/network failure: retry next refresh
            logger.warning(
                "platform_links_lookup_failed",
                podcast_id=podcast.id,
                collection_id=collection_id,
                error=str(exc),
            )
            return PodcastLinkOutcome(podcast.id, PLATFORM_APPLE, candidates=len(candidates), skipped="lookup_failed")

        entries = parse_lookup_window(results)
        matches = match_candidates(candidates, entries)
        if not show.verified:
            # A title-only show hit is accepted only when its window proves it
            # is our feed: at least one exact GUID / enclosure match. Title-date
            # matches alone are not proof — a namesake show could share them.
            if not any(m.match_method in ("guid", "audio_url") for m in matches):
                logger.info(
                    "platform_links_show_unverified",
                    podcast_id=podcast.id,
                    podcast_title=podcast.title,
                    collection_id=collection_id,
                    window_entries=len(entries),
                )
                return self._no_apple_id(podcast, candidates, now, dry_run=dry_run)
            if not dry_run and show.view_url:
                self.repository.set_podcast_apple_url(podcast.id, show.view_url)
            logger.info(
                "platform_links_show_resolved",
                podcast_id=podcast.id,
                podcast_title=podcast.title,
                apple_url=show.view_url,
                verified_by="episode_window",
            )
        matched_ids = {m.episode_id for m in matches}
        rows = [
            PlatformLink(
                episode_id=m.episode_id,
                platform=PLATFORM_APPLE,
                url=m.url,
                external_ref=m.external_ref,
                match_method=m.match_method,
                checked_at=now,
            )
            for m in matches
        ] + [
            PlatformLink(episode_id=c.episode_id, platform=PLATFORM_APPLE, checked_at=now)
            for c in candidates
            if c.episode_id not in matched_ids
        ]
        if not dry_run:
            self.repository.upsert_platform_links(rows)

        matched_by: Dict[str, int] = {}
        for m in matches:
            matched_by[m.match_method] = matched_by.get(m.match_method, 0) + 1
        outcome = PodcastLinkOutcome(
            podcast.id,
            PLATFORM_APPLE,
            candidates=len(candidates),
            linked=len(matches),
            not_found=len(candidates) - len(matches),
            matched_by=matched_by,
        )
        logger.info(
            "platform_links_resolved",
            podcast_id=podcast.id,
            podcast_title=podcast.title,
            collection_id=collection_id,
            window_entries=len(entries),
            candidates=outcome.candidates,
            linked=outcome.linked,
            not_found=outcome.not_found,
            matched_by=matched_by,
            dry_run=dry_run,
        )
        return outcome

    def link_all(
        self,
        *,
        podcasts: Optional[List[Podcast]] = None,
        dry_run: bool = False,
        force: bool = False,
    ) -> List[PodcastLinkOutcome]:
        """Backfill: one :meth:`link_podcast` per podcast (all when ``podcasts`` is None)."""
        targets = podcasts if podcasts is not None else self.repository.get_all()
        return [self.link_podcast(p, dry_run=dry_run, force=force) for p in targets]

    # ------------------------------------------------------------------
    # Show id
    # ------------------------------------------------------------------

    def _resolve_show(self, podcast: Podcast, *, dry_run: bool) -> Optional[_ShowMatch]:
        """
        The show's Apple collection: from the stored (chart-sourced)
        ``apple_url`` first; else one iTunes search by title, accepted
        outright when a result's ``feedUrl`` is the podcast's RSS URL, or
        provisionally (``verified=False``) when exactly one result carries
        the same title — the same show on another feed host is common
        (megaphone vs anchor, substack vs flightcast).
        """
        urls = self.repository.sync_podcast_chart_urls(podcast.id)
        stored = urls.get("apple_url") if isinstance(urls, dict) else None
        collection_id = apple_collection_id(stored)
        if collection_id:
            return _ShowMatch(collection_id, stored, verified=True)

        try:
            results = self._search(f"term={quote_plus(podcast.title)}&media=podcast&entity=podcast&limit=25")
        except Exception as exc:  # noqa: BLE001
            logger.warning("platform_links_show_search_failed", podcast_id=podcast.id, error=str(exc))
            return None

        wanted = _feed_key(str(podcast.rss_url))
        title_key = normalize_title(podcast.title)
        same_title: List[_ShowMatch] = []
        for entry in results:
            if not isinstance(entry, dict):
                continue
            view_url = _show_url(entry.get("collectionViewUrl"))
            found = apple_collection_id(view_url) or (
                str(entry["collectionId"]) if entry.get("collectionId") is not None else None
            )
            if not found:
                continue
            feed_url = entry.get("feedUrl")
            if isinstance(feed_url, str) and _feed_key(feed_url) == wanted:
                if view_url and not dry_run:
                    self.repository.set_podcast_apple_url(podcast.id, view_url)
                logger.info(
                    "platform_links_show_resolved",
                    podcast_id=podcast.id,
                    podcast_title=podcast.title,
                    apple_url=view_url,
                    verified_by="feed_url",
                )
                return _ShowMatch(found, view_url, verified=True)
            name = entry.get("collectionName")
            if isinstance(name, str) and title_key and normalize_title(name) == title_key:
                same_title.append(_ShowMatch(found, view_url, verified=False))
        if len(same_title) == 1:
            return same_title[0]
        return None

    def _no_apple_id(
        self, podcast: Podcast, candidates: List[PlatformLinkCandidate], now: datetime, *, dry_run: bool
    ) -> PodcastLinkOutcome:
        if not dry_run:
            self._write_not_found(candidates, now)
        logger.info(
            "platform_links_no_apple_id",
            podcast_id=podcast.id,
            podcast_title=podcast.title,
            candidates=len(candidates),
        )
        return PodcastLinkOutcome(
            podcast.id, PLATFORM_APPLE, candidates=len(candidates), not_found=len(candidates), skipped="no_apple_id"
        )

    def _write_not_found(self, candidates: List[PlatformLinkCandidate], now: datetime) -> None:
        self.repository.upsert_platform_links(
            [PlatformLink(episode_id=c.episode_id, platform=PLATFORM_APPLE, checked_at=now) for c in candidates]
        )


def _show_url(value: Any) -> Optional[str]:
    """Apple's ``collectionViewUrl`` without its ``?uo=4`` tracking query."""
    if not isinstance(value, str) or not value:
        return None
    parts = urlsplit(value)
    return urlunsplit((parts.scheme, parts.netloc, parts.path, "", ""))


def _feed_key(url: str) -> str:
    """Scheme-insensitive, host case-insensitive, trailing-slash-insensitive feed URL key."""
    try:
        parts = urlsplit(url.strip())
    except ValueError:
        return url.strip().lower()
    return f"{parts.netloc.lower()}{parts.path.rstrip('/')}" + (f"?{parts.query}" if parts.query else "")
