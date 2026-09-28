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

The unit of work is a podcast. Per platform the flow is: read the
candidates (unlinked episodes inside the show's newest window whose
not-found marker has expired), make at most ONE remote listing, then write
found rows and not-found markers for every candidate. A show with no
candidates costs no request, which is the steady state.

- **Apple** (Phase 1): the iTunes lookup window, exact GUID first.
- **Spotify**: publisher-provided links only. Spotify's developer program
  closed to small apps in 2026 and its pages no longer expose episode
  lists, so the feed itself is the only source (spec #87 "Spotify").
- **YouTube** (Phase 2): publisher-provided links first (import, alternate
  enclosure, item link, unique description link), then a flat yt-dlp
  listing of the show's channel matched on title, date and duration.

Wired into both refresh paths (queued ``handle_refresh_feed`` and inline
``RefreshService``) as best-effort work, and into ``thestill link-platforms``
for backfills. Every network helper is injectable so tests never touch
iTunes or YouTube.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime, timedelta
from typing import Any, Callable, Dict, List, Optional, Tuple
from urllib.parse import quote_plus, urlsplit, urlunsplit

from structlog import get_logger

from ..core import youtube_episode_linker as youtube
from ..core.apple_episode_linker import apple_collection_id, match_candidates, normalize_title, parse_lookup_window
from ..core.publisher_links import (
    PublisherLink,
    describe_sources,
    publisher_links_for_candidate,
    show_links_from_sources,
    spotify_episode_url,
)
from ..core.spotify_resolver import SpotifyEpisodeMetadata, default_episode_metadata, itunes_lookup, itunes_search
from ..models.podcast import PlatformLink, PlatformLinkCandidate, Podcast
from ..utils.datetime_utils import now_utc

logger = get_logger(__name__)

# Apple's per-show episode index is capped at the newest 200 entries and
# ignores ``offset``; the candidate window mirrors it so older episodes are
# never candidates (spec #87 "Candidates and throttle"). The same window is
# used for the other platforms so one rule explains every "why no link".
DEFAULT_WINDOW = 200

PLATFORM_APPLE = "apple"
PLATFORM_SPOTIFY = "spotify"
PLATFORM_YOUTUBE = "youtube"


@dataclass(frozen=True)
class PodcastLinkOutcome:
    """What one pass over one podcast did for one platform — logged and echoed by the CLI."""

    podcast_id: str
    platform: str
    candidates: int
    linked: int = 0
    not_found: int = 0
    # 'no_candidates' | 'no_apple_id' | 'no_channel' | 'lookup_failed'
    skipped: Optional[str] = None
    matched_by: Dict[str, int] = field(default_factory=dict)


@dataclass(frozen=True)
class PodcastLinkReport:
    """All platforms for one podcast."""

    podcast_id: str
    outcomes: List[PodcastLinkOutcome]

    @property
    def candidates(self) -> int:
        return sum(o.candidates for o in self.outcomes)

    @property
    def linked(self) -> int:
        return sum(o.linked for o in self.outcomes)

    @property
    def not_found(self) -> int:
        return sum(o.not_found for o in self.outcomes)


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
        window: int = DEFAULT_WINDOW,
        recheck_hours: int = 24,
        youtube_listing_limit: int = youtube.DEFAULT_LISTING_LIMIT,
        lookup: Callable[[str], list] = itunes_lookup,
        search: Callable[[str], list] = itunes_search,
        list_videos: Callable[..., List[youtube.YouTubeVideoEntry]] = youtube.list_channel_videos,
        fetch_video: Callable[[str], Optional[youtube.YouTubeVideoEntry]] = youtube.fetch_video,
        fetch_spotify_episode: Callable[[str], SpotifyEpisodeMetadata] = lambda episode_id: default_episode_metadata(
            episode_id, spotify_episode_url(episode_id)
        ),
        clock: Callable[[], datetime] = now_utc,
    ) -> None:
        self.repository = repository
        self.window = window
        self.recheck = timedelta(hours=recheck_hours)
        self.youtube_listing_limit = youtube_listing_limit
        self._lookup = lookup
        self._search = search
        self._list_videos = list_videos
        self._fetch_video = fetch_video
        self._fetch_spotify_episode = fetch_spotify_episode
        self._clock = clock

    # ------------------------------------------------------------------
    # Public entry points
    # ------------------------------------------------------------------

    def link_podcast(self, podcast: Podcast, *, dry_run: bool = False, force: bool = False) -> PodcastLinkReport:
        """
        Resolve every platform for one podcast. Never raises for network or
        data problems — a refresh must not fail because Apple or YouTube is
        down; a failed listing is simply retried on the next refresh.
        """
        now = self._clock()
        recheck_before = None if force else now - self.recheck
        # The chart sync writes (it backfills apple_url / youtube_url from the
        # chart row); a dry run promises to write nothing, so it reads only.
        urls = (
            self.repository.get_podcast_platform_urls(podcast.id)
            if dry_run
            else self.repository.sync_podcast_chart_urls(podcast.id)
        )
        if not isinstance(urls, dict):
            urls = {}
        outcomes = [self._link_apple(podcast, urls, now, recheck_before, dry_run=dry_run)]
        outcomes.extend(self._link_spotify_and_youtube(podcast, urls, now, recheck_before, dry_run=dry_run))
        return PodcastLinkReport(podcast.id, outcomes)

    def link_all(
        self,
        *,
        podcasts: Optional[List[Podcast]] = None,
        dry_run: bool = False,
        force: bool = False,
    ) -> List[PodcastLinkReport]:
        """Backfill: one :meth:`link_podcast` per podcast (all when ``podcasts`` is None)."""
        targets = podcasts if podcasts is not None else self.repository.get_all()
        return [self.link_podcast(p, dry_run=dry_run, force=force) for p in targets]

    # ------------------------------------------------------------------
    # Shared helpers
    # ------------------------------------------------------------------

    def _candidates(
        self, podcast_id: str, platform: str, recheck_before: Optional[datetime]
    ) -> List[PlatformLinkCandidate]:
        found = self.repository.get_platform_link_candidates(
            podcast_id, platform, window=self.window, recheck_before=recheck_before
        )
        return list(found) if isinstance(found, list) else []

    @staticmethod
    def _not_found_rows(platform: str, candidates: List[PlatformLinkCandidate], now: datetime) -> List[PlatformLink]:
        return [PlatformLink(episode_id=c.episode_id, platform=platform, checked_at=now) for c in candidates]

    def _write(self, rows: List[PlatformLink], *, dry_run: bool) -> None:
        if rows and not dry_run:
            self.repository.upsert_platform_links(rows)

    def _store_show_url(self, podcast: Podcast, platform: str, url: str, *, verified_by: str, dry_run: bool) -> None:
        if not dry_run:
            self.repository.set_podcast_platform_url(podcast.id, platform, url)
        logger.info(
            "platform_links_show_resolved",
            podcast_id=podcast.id,
            podcast_title=podcast.title,
            platform=platform,
            url=url,
            verified_by=verified_by,
        )

    # ------------------------------------------------------------------
    # Apple (Phase 1)
    # ------------------------------------------------------------------

    def _link_apple(
        self,
        podcast: Podcast,
        urls: Dict[str, Optional[str]],
        now: datetime,
        recheck_before: Optional[datetime],
        *,
        dry_run: bool,
    ) -> PodcastLinkOutcome:
        candidates = self._candidates(podcast.id, PLATFORM_APPLE, recheck_before)
        if not candidates:
            return PodcastLinkOutcome(podcast.id, PLATFORM_APPLE, candidates=0, skipped="no_candidates")

        show = self._resolve_apple_show(podcast, urls.get("apple_url"), dry_run=dry_run)
        if show is None:
            return self._no_apple_id(podcast, candidates, now, dry_run=dry_run)
        collection_id = show.collection_id

        try:
            results = self._lookup(f"id={collection_id}&entity=podcastEpisode&limit={self.window}")
        except Exception as exc:  # noqa: BLE001 — any resolver/network failure: retry next refresh
            logger.warning(
                "platform_links_lookup_failed",
                podcast_id=podcast.id,
                platform=PLATFORM_APPLE,
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
            if show.view_url:
                self._store_show_url(
                    podcast, PLATFORM_APPLE, show.view_url, verified_by="episode_window", dry_run=dry_run
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
        ] + self._not_found_rows(PLATFORM_APPLE, [c for c in candidates if c.episode_id not in matched_ids], now)
        self._write(rows, dry_run=dry_run)

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
        self._log_outcome(podcast, outcome, dry_run=dry_run, collection_id=collection_id, window_entries=len(entries))
        return outcome

    def _resolve_apple_show(self, podcast: Podcast, stored: Optional[str], *, dry_run: bool) -> Optional[_ShowMatch]:
        """
        The show's Apple collection: from the stored (chart-sourced)
        ``apple_url`` first; else one iTunes search by title, accepted
        outright when a result's ``feedUrl`` is the podcast's RSS URL, or
        provisionally (``verified=False``) when exactly one result carries
        the same title — the same show on another feed host is common
        (megaphone vs anchor, substack vs flightcast).
        """
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
                if view_url:
                    self._store_show_url(podcast, PLATFORM_APPLE, view_url, verified_by="feed_url", dry_run=dry_run)
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
        self._write(self._not_found_rows(PLATFORM_APPLE, candidates, now), dry_run=dry_run)
        logger.info(
            "platform_links_no_apple_id",
            podcast_id=podcast.id,
            podcast_title=podcast.title,
            candidates=len(candidates),
        )
        return PodcastLinkOutcome(
            podcast.id, PLATFORM_APPLE, candidates=len(candidates), not_found=len(candidates), skipped="no_apple_id"
        )

    # ------------------------------------------------------------------
    # Spotify (publisher-provided only) + YouTube (publisher, then channel)
    # ------------------------------------------------------------------

    def _link_spotify_and_youtube(
        self,
        podcast: Podcast,
        urls: Dict[str, Optional[str]],
        now: datetime,
        recheck_before: Optional[datetime],
        *,
        dry_run: bool,
    ) -> List[PodcastLinkOutcome]:
        spotify_cands = self._candidates(podcast.id, PLATFORM_SPOTIFY, recheck_before)
        youtube_cands = self._candidates(podcast.id, PLATFORM_YOUTUBE, recheck_before)
        if not spotify_cands and not youtube_cands:
            return [
                PodcastLinkOutcome(podcast.id, PLATFORM_SPOTIFY, candidates=0, skipped="no_candidates"),
                PodcastLinkOutcome(podcast.id, PLATFORM_YOUTUBE, candidates=0, skipped="no_candidates"),
            ]

        wanted = {
            PLATFORM_SPOTIFY: {c.episode_id for c in spotify_cands},
            PLATFORM_YOUTUBE: {c.episode_id for c in youtube_cands},
        }
        by_id: Dict[str, PlatformLinkCandidate] = {c.episode_id: c for c in [*spotify_cands, *youtube_cands]}
        alternates = (
            self.repository.get_alternate_enclosures_for_episodes([c.episode_id for c in youtube_cands])
            if youtube_cands
            else {}
        )
        if not isinstance(alternates, dict):
            alternates = {}

        # 1. What the publisher already told us, per episode. Trusted sources
        #    become rows; a description link is only a claim to verify (4.).
        rows: List[PlatformLink] = []
        publisher_found: Dict[str, List[PublisherLink]] = {PLATFORM_SPOTIFY: [], PLATFORM_YOUTUBE: []}
        claims: Dict[str, List[Tuple[PlatformLinkCandidate, PublisherLink]]] = {
            PLATFORM_SPOTIFY: [],
            PLATFORM_YOUTUBE: [],
        }
        for candidate in by_id.values():
            found = publisher_links_for_candidate(candidate, alternates.get(candidate.episode_id, []))
            for platform, link in found.items():
                if candidate.episode_id not in wanted.get(platform, ()):
                    continue
                if not link.verified:
                    claims[platform].append((candidate, link))
                    continue
                publisher_found[platform].append(link)
                rows.append(self._publisher_row(candidate, link, now))
        linked_ids = {p: {r.episode_id for r in rows if r.platform == p} for p in (PLATFORM_SPOTIFY, PLATFORM_YOUTUBE)}

        # 2. Show-level links the publisher states, stored once when missing.
        show = show_links_from_sources(
            [podcast.description, getattr(podcast, "description_html", None), podcast.website_url],
            [c.description_html for c in by_id.values()],
        )
        if show.spotify_url and not urls.get("spotify_url"):
            self._store_show_url(podcast, PLATFORM_SPOTIFY, show.spotify_url, verified_by="publisher", dry_run=dry_run)
        channel_url = urls.get("youtube_url")
        if not channel_url and show.youtube_url:
            self._store_show_url(podcast, PLATFORM_YOUTUBE, show.youtube_url, verified_by="publisher", dry_run=dry_run)
            channel_url = show.youtube_url

        # 3. Spotify: the feed is the only source (spec #87 "Spotify"), so a
        #    description claim is checked against the episode page's own
        #    title / date / duration and that is the end of the road.
        for candidate, link in claims[PLATFORM_SPOTIFY]:
            if candidate.episode_id in linked_ids[PLATFORM_SPOTIFY]:
                continue
            if self._verify_spotify_claim(candidate, link, now):
                publisher_found[PLATFORM_SPOTIFY].append(link)
                rows.append(self._publisher_row(candidate, link, now))
                linked_ids[PLATFORM_SPOTIFY].add(candidate.episode_id)
        spotify_rest = [c for c in spotify_cands if c.episode_id not in linked_ids[PLATFORM_SPOTIFY]]
        rows.extend(self._not_found_rows(PLATFORM_SPOTIFY, spotify_rest, now))
        spotify_outcome = PodcastLinkOutcome(
            podcast.id,
            PLATFORM_SPOTIFY,
            candidates=len(spotify_cands),
            linked=len(linked_ids[PLATFORM_SPOTIFY]),
            not_found=len(spotify_rest),
            skipped=None if spotify_cands else "no_candidates",
            matched_by={"publisher": len(linked_ids[PLATFORM_SPOTIFY])} if linked_ids[PLATFORM_SPOTIFY] else {},
        )

        # 4. YouTube: the channel listing for what the feed did not carry,
        #    then the description claims (checked against the listing entry
        #    when the video is in it, else against the video itself).
        youtube_rest = [c for c in youtube_cands if c.episode_id not in linked_ids[PLATFORM_YOUTUBE]]
        matched_by: Dict[str, int] = {}
        if linked_ids[PLATFORM_YOUTUBE]:
            matched_by["publisher"] = len(linked_ids[PLATFORM_YOUTUBE])
        skipped: Optional[str] = None if youtube_cands else "no_candidates"
        channel_linked = 0
        listing_shape: Dict[str, int] = {}
        entries: List[youtube.YouTubeVideoEntry] = []
        if youtube_rest and channel_url:
            try:
                entries = self._list_videos(channel_url, limit=self.youtube_listing_limit)
            except Exception as exc:  # noqa: BLE001 — bot checks, network, private channel
                logger.warning(
                    "platform_links_lookup_failed",
                    podcast_id=podcast.id,
                    platform=PLATFORM_YOUTUBE,
                    channel_url=channel_url,
                    error=str(exc),
                )
                skipped = "lookup_failed"
                youtube_rest = []  # nothing written for them: retried next refresh
            else:
                listing_shape = youtube.describe(entries)
                matches = youtube.match_candidates(youtube_rest, entries, now=now)
                matched = {m.episode_id for m in matches}
                channel_linked = len(matches)
                for m in matches:
                    matched_by[m.match_method] = matched_by.get(m.match_method, 0) + 1
                    rows.append(
                        PlatformLink(
                            episode_id=m.episode_id,
                            platform=PLATFORM_YOUTUBE,
                            url=m.url,
                            external_ref=m.external_ref,
                            match_method=m.match_method,
                            checked_at=now,
                        )
                    )
                youtube_rest = [c for c in youtube_rest if c.episode_id not in matched]
        elif youtube_rest:
            skipped = "no_channel"
        if skipped != "lookup_failed":
            listed = {e.video_id: e for e in entries}
            pending = {c.episode_id for c in youtube_rest}
            for candidate, link in claims[PLATFORM_YOUTUBE]:
                if candidate.episode_id not in pending:
                    continue
                if self._verify_youtube_claim(candidate, link, listed.get(link.external_ref), now):
                    publisher_found[PLATFORM_YOUTUBE].append(link)
                    rows.append(self._publisher_row(candidate, link, now))
                    matched_by["publisher"] = matched_by.get("publisher", 0) + 1
                    pending.discard(candidate.episode_id)
            youtube_rest = [c for c in youtube_rest if c.episode_id in pending]
            rows.extend(self._not_found_rows(PLATFORM_YOUTUBE, youtube_rest, now))
        youtube_outcome = PodcastLinkOutcome(
            podcast.id,
            PLATFORM_YOUTUBE,
            candidates=len(youtube_cands),
            linked=(
                len(youtube_cands) - len(youtube_rest)
                if skipped != "lookup_failed"
                else len(linked_ids[PLATFORM_YOUTUBE])
            ),
            not_found=len(youtube_rest) if skipped != "lookup_failed" else 0,
            skipped=skipped,
            matched_by=matched_by,
        )

        self._write(rows, dry_run=dry_run)
        if spotify_cands:
            self._log_outcome(
                podcast, spotify_outcome, dry_run=dry_run, sources=describe_sources(publisher_found[PLATFORM_SPOTIFY])
            )
        if youtube_cands:
            self._log_outcome(
                podcast,
                youtube_outcome,
                dry_run=dry_run,
                channel_url=channel_url,
                sources=describe_sources(publisher_found[PLATFORM_YOUTUBE]),
                **listing_shape,
            )
        return [spotify_outcome, youtube_outcome]

    @staticmethod
    def _publisher_row(candidate: PlatformLinkCandidate, link: PublisherLink, now: datetime) -> PlatformLink:
        return PlatformLink(
            episode_id=candidate.episode_id,
            platform=link.platform,
            url=link.url,
            external_ref=link.external_ref,
            match_method="publisher",
            checked_at=now,
        )

    def _verify_spotify_claim(self, candidate: PlatformLinkCandidate, link: PublisherLink, now: datetime) -> bool:
        """A description's Spotify link is this episode only if the linked
        page's title / release date / duration say so (the channel rules)."""
        try:
            meta = self._fetch_spotify_episode(link.external_ref)
        except Exception as exc:  # noqa: BLE001
            logger.warning(
                "platform_links_claim_fetch_failed",
                episode_id=candidate.episode_id,
                platform=PLATFORM_SPOTIFY,
                url=link.url,
                error=str(exc),
            )
            return False
        method = self._claim_method(candidate, meta.title, meta.release_date, meta.duration_seconds, now)
        self._log_claim(candidate, link, method)
        return method is not None

    def _verify_youtube_claim(
        self,
        candidate: PlatformLinkCandidate,
        link: PublisherLink,
        listed: Optional[youtube.YouTubeVideoEntry],
        now: datetime,
    ) -> bool:
        """Same for a description's YouTube link: the listing entry when the
        video is on the show's channel, else the video's own metadata."""
        entry = listed
        if entry is None:
            try:
                entry = self._fetch_video(link.external_ref)
            except Exception as exc:  # noqa: BLE001
                logger.warning(
                    "platform_links_claim_fetch_failed",
                    episode_id=candidate.episode_id,
                    platform=PLATFORM_YOUTUBE,
                    url=link.url,
                    error=str(exc),
                )
                return False
        if entry is None:
            self._log_claim(candidate, link, None)
            return False
        method = self._claim_method(candidate, entry.title, entry.uploaded, entry.duration, now)
        self._log_claim(candidate, link, method)
        return method is not None

    @staticmethod
    def _claim_method(
        candidate: PlatformLinkCandidate,
        title: str,
        released: Optional[datetime],
        duration: Optional[int],
        now: datetime,
    ) -> Optional[str]:
        """The channel rules first; else the publisher's own link is enough
        when the item's date and length agree (a retitled upload)."""
        method = youtube.match_rule(candidate, title=title, released=released, duration=duration, now=now)
        if method is None and youtube.claim_corroborated(candidate, released=released, duration=duration, now=now):
            method = "date_duration"
        return method

    @staticmethod
    def _log_claim(candidate: PlatformLinkCandidate, link: PublisherLink, method: Optional[str]) -> None:
        logger.info(
            "platform_links_description_claim",
            episode_id=candidate.episode_id,
            platform=link.platform,
            url=link.url,
            verified_by=method,
        )

    @staticmethod
    def _log_outcome(podcast: Podcast, outcome: PodcastLinkOutcome, *, dry_run: bool, **extra: Any) -> None:
        logger.info(
            "platform_links_resolved",
            podcast_id=podcast.id,
            podcast_title=podcast.title,
            platform=outcome.platform,
            candidates=outcome.candidates,
            linked=outcome.linked,
            not_found=outcome.not_found,
            skipped=outcome.skipped,
            matched_by=outcome.matched_by,
            dry_run=dry_run,
            **extra,
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
