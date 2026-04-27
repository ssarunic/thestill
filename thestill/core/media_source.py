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
Media Source Strategy Pattern for handling different podcast sources.

This module implements the Strategy Pattern to handle different types of media sources
(RSS feeds, YouTube channels/playlists, etc.) in a consistent way. Each source type
implements the MediaSource interface, providing URL validation, episode fetching,
and downloading capabilities.

Benefits:
- Separation of concerns: YouTube logic isolated from RSS logic
- Extensibility: Easy to add new sources (Spotify, SoundCloud, etc.)
- Testability: Each source can be tested independently
- Type safety: Explicit contracts via ABC
"""

import json
import urllib.request
from abc import ABC, abstractmethod
from datetime import datetime
from pathlib import Path
from typing import TYPE_CHECKING, Any, Dict, List, NamedTuple, Optional, Tuple

import defusedxml.ElementTree as ET  # Hardened against XXE / billion-laughs attacks in untrusted RSS feeds.
import feedparser
import requests
from requests.adapters import HTTPAdapter
from structlog import get_logger
from urllib3.util.retry import Retry

from ..models.podcast import Episode, TranscriptLink
from ..utils.duration import parse_duration
from ..utils.podcast_categories import validate_category
from ..utils.timing import log_phase_timing
from ..utils.url_guard import UnsafeURLError, _GuardedHTTPAdapter, validate_public_url
from ..utils.url_patterns import APPLE_PODCAST_ID_RE, extract_apple_podcast_id, looks_like_rss
from .youtube_downloader import YouTubeDownloader

if TYPE_CHECKING:
    from ..utils.path_manager import PathManager

logger = get_logger(__name__)


class FetchRSSResult(NamedTuple):
    """Outcome of a single RSS HTTP fetch (spec #19).

    ``not_modified`` is the 304 signal; ``content`` is ``None`` in that
    case. ``etag`` / ``last_modified`` are echoed from the response
    headers (or kept from the input on 304) so the caller can persist
    them for the next refresh.
    """

    content: Optional[str]
    status_code: int
    etag: Optional[str]
    last_modified: Optional[str]
    not_modified: bool
    error: Optional[str]


class FetchAndParseResult(NamedTuple):
    """Outcome of :meth:`RSSMediaSource.fetch_and_parse`.

    Adds the parsed feedparser result to :class:`FetchRSSResult`. On a
    304 hit, both ``content`` and ``parsed_feed`` are ``None``.
    """

    content: Optional[str]
    parsed_feed: Optional[Any]
    status_code: int
    etag: Optional[str]
    last_modified: Optional[str]
    not_modified: bool
    error: Optional[str]


class MediaSource(ABC):
    """
    Abstract base class for media sources.

    A media source represents a platform that provides podcast/audio content
    (e.g., RSS feeds, YouTube, Spotify). Each source must implement methods
    for URL validation, episode fetching, and downloading.
    """

    @abstractmethod
    def is_valid_url(self, url: str) -> bool:
        """
        Check if URL is valid for this media source.

        Args:
            url: URL to validate

        Returns:
            True if URL matches this source's patterns, False otherwise
        """

    @abstractmethod
    def extract_metadata(self, url: str) -> Optional[Dict[str, Any]]:
        """
        Extract metadata (title, description) from the source URL.

        Args:
            url: Source URL to extract metadata from

        Returns:
            Dictionary with 'title', 'description', and source-specific fields,
            or None if extraction fails
        """

    @abstractmethod
    def fetch_episodes(
        self,
        url: str,
        existing_episodes: List[Episode],
        last_processed: Optional[datetime] = None,
        max_episodes: Optional[int] = None,
    ) -> List[Episode]:
        """
        Fetch new episodes from the source.

        Args:
            url: Source URL to fetch from
            existing_episodes: List of episodes already tracked
            last_processed: Timestamp of last processed episode (for RSS incremental fetch)
            max_episodes: Optional limit on number of episodes to return

        Returns:
            List of new Episode objects discovered from the source
        """

    @abstractmethod
    def download_episode(self, episode: Episode, podcast_title: str, storage_path: str) -> Optional[str]:
        """
        Download episode audio file.

        Args:
            episode: Episode to download
            podcast_title: Podcast title (for filename generation)
            storage_path: Directory to save audio file

        Returns:
            Path to downloaded audio file, or None if download fails
        """


class RSSMediaSource(MediaSource):
    """
    Media source implementation for RSS/Atom podcast feeds.

    Handles:
    - Standard RSS podcast feeds
    - Apple Podcasts URLs (resolved to RSS via iTunes API)
    - Feedparser-based episode extraction
    - Saving raw RSS content for debugging (optional)
    """

    # Default timeout: (connect, read) in seconds.
    # Short connect avoids hanging on unreachable hosts; read is generous
    # enough for large feed bodies but not for a hung server.
    DEFAULT_TIMEOUT: tuple = (5, 15)

    def __init__(self, path_manager: Optional["PathManager"] = None, pool_maxsize: int = 16) -> None:
        """
        Initialize RSS media source.

        Args:
            path_manager: Optional path manager for saving debug RSS files.
                         If provided, raw RSS content will be saved during fetch_episodes.
            pool_maxsize: Max concurrent HTTP connections per host for the shared session.
        """
        self.path_manager = path_manager
        self.session = self._build_session(pool_maxsize)

    @staticmethod
    def _build_session(pool_maxsize: int) -> requests.Session:
        """Build a requests.Session with retry, keep-alive, and connection pooling."""
        session = requests.Session()
        # Custom User-Agent avoids being blocked by some podcast hosts (e.g., Buzzsprout)
        session.headers.update({"User-Agent": "Thestill/1.0 (+https://thestill.me)"})
        retry = Retry(
            total=2,
            connect=2,
            read=1,
            backoff_factor=0.5,
            status_forcelist=(500, 502, 503, 504),
            allowed_methods=frozenset(["GET"]),
            respect_retry_after_header=True,
        )
        # Guarded adapter re-validates the URL on every send, so HTTP redirects
        # cannot smuggle a public host into a private/loopback target.
        adapter = _GuardedHTTPAdapter(max_retries=retry, pool_connections=pool_maxsize, pool_maxsize=pool_maxsize)
        session.mount("http://", adapter)
        session.mount("https://", adapter)
        return session

    def is_valid_url(self, url: str) -> bool:
        """
        Check if URL is an RSS feed or Apple Podcasts URL.

        Args:
            url: URL to validate

        Returns:
            True if URL is RSS or Apple Podcasts, False otherwise
        """
        # Apple Podcasts URLs are handled by extracting RSS
        if "podcasts.apple.com" in url or "itunes.apple.com" in url:
            return True

        # Check for common RSS feed URL shapes — this is a hint, not a
        # safety check (the SSRF guard runs before any fetch).
        if looks_like_rss(url):
            return True

        # Default: Assume it's RSS if not YouTube or other known sources
        # This is a fallback since RSS feeds can have any URL structure
        return not YouTubeDownloader.is_youtube_url(url)

    def extract_metadata(
        self,
        url: str,
        rss_content: Optional[str] = None,
        parsed_feed: Optional[Any] = None,
    ) -> Optional[Dict[str, Any]]:
        """
        Extract podcast metadata from RSS feed or Apple Podcasts URL.

        If `rss_content` and `parsed_feed` are supplied, the HTTP fetch and
        feedparser parse are skipped — the caller is expected to have already
        fetched and parsed the feed (see `fetch_and_parse`). This is how
        `PodcastFeedManager.get_new_episodes` avoids the historical
        double-fetch of each RSS body per refresh (spec #19).

        Args:
            url: RSS feed URL or Apple Podcasts URL
            rss_content: Optional pre-fetched RSS body. Skips HTTP fetch if set.
            parsed_feed: Optional pre-parsed feedparser result. Skips parse if set.

        Returns:
            Dictionary with 'title', 'description', 'rss_url', 'image_url', 'language',
            and category fields, or None if extraction fails.
        """
        try:
            if rss_content is not None and parsed_feed is not None:
                rss_url = url
            else:
                # Resolve Apple Podcasts URLs to RSS first
                rss_url = self._extract_rss_from_apple_url(url)
                if not rss_url:
                    rss_url = url  # Assume it's already an RSS URL

                if rss_content is None:
                    fetch_result = self.fetch_rss_content(rss_url)
                    rss_content = fetch_result.content
                    if not rss_content:
                        logger.warning(f"Failed to fetch RSS content: {rss_url}")
                        return None

                if parsed_feed is None:
                    parsed_feed = self.parse_rss(rss_content, rss_url)
                    if parsed_feed is None:
                        return None

            feed = parsed_feed.feed

            # Extract podcast artwork URL
            # Priority: itunes:image (higher quality) > standard RSS image
            image_url = None
            if hasattr(feed, "itunes_image") and feed.itunes_image:
                # Feedparser returns itunes:image as a dict with 'href' key
                if isinstance(feed.itunes_image, dict):
                    image_url = feed.itunes_image.get("href")
                else:
                    image_url = str(feed.itunes_image)
            elif hasattr(feed, "image") and feed.image:
                # Standard RSS image tag
                if hasattr(feed.image, "href"):
                    image_url = feed.image.href
                elif isinstance(feed.image, dict):
                    image_url = feed.image.get("href") or feed.image.get("url")

            if image_url:
                logger.debug(f"Extracted podcast artwork: {image_url}")

            # Extract language from RSS <language> tag
            # RSS format: "en", "en-us", "en-US", "hr", "hr-HR", etc.
            # Normalize to ISO 639-1 two-letter code
            language = "en"  # Default to English
            feed_language = feed.get("language", "")
            if feed_language:
                # Extract first part before hyphen and lowercase: "en-US" -> "en", "hr-HR" -> "hr"
                language = feed_language.split("-")[0].lower()[:2]
                logger.debug(f"Extracted language from RSS: {feed_language} -> {language}")

            # Extract categories from raw XML (feedparser doesn't handle nested categories well)
            categories = self._extract_categories(rss_content)

            # THES-143: Extract author (itunes:author)
            author = None
            if hasattr(feed, "author") and feed.author:
                author = str(feed.author)[:255]  # Max 255 chars per Apple spec
            elif hasattr(feed, "itunes_author") and feed.itunes_author:
                author = str(feed.itunes_author)[:255]

            # THES-143: Extract explicit flag (itunes:explicit)
            explicit = self._parse_explicit_flag(getattr(feed, "itunes_explicit", None))

            # THES-144: Extract show type (itunes:type) - "episodic" or "serial"
            show_type = None
            itunes_type = getattr(feed, "itunes_type", None)
            if itunes_type and itunes_type.lower() in ("episodic", "serial"):
                show_type = itunes_type.lower()

            # THES-144: Extract website URL (channel <link>)
            website_url = feed.get("link")

            # THES-145: Extract is_complete flag (itunes:complete)
            is_complete = False
            itunes_complete = getattr(feed, "itunes_complete", None)
            if itunes_complete and str(itunes_complete).lower() == "yes":
                is_complete = True

            # THES-145: Extract copyright
            copyright_text = feed.get("rights") or feed.get("copyright")

            # THES-145: Detect feed migration (itunes:new-feed-url)
            new_feed_url = self._extract_new_feed_url(rss_content)
            if new_feed_url:
                logger.warning(f"Feed migration detected! New URL: {new_feed_url}")

            return {
                "title": feed.get("title", "Unknown Podcast"),
                "description": feed.get("description", ""),
                "rss_url": rss_url,
                "image_url": image_url,
                "language": language,
                "primary_category": categories.get("primary_category"),
                "primary_subcategory": categories.get("primary_subcategory"),
                "secondary_category": categories.get("secondary_category"),
                "secondary_subcategory": categories.get("secondary_subcategory"),
                # THES-143: Essential metadata
                "author": author,
                "explicit": explicit,
                # THES-144: Show organization
                "show_type": show_type,
                "website_url": website_url,
                # THES-145: Feed management
                "is_complete": is_complete,
                "copyright": copyright_text,
                "new_feed_url": new_feed_url,  # For caller to handle migration
            }

        except Exception as e:
            logger.error(f"Error extracting RSS metadata from {url}: {e}")
            return None

    def fetch_episodes(
        self,
        url: str,
        existing_episodes: List[Episode],
        last_processed: Optional[datetime] = None,
        max_episodes: Optional[int] = None,
        podcast_slug: Optional[str] = None,
        parsed_feed: Optional[Any] = None,
        known_external_ids: Optional[set] = None,
    ) -> List[Episode]:
        """
        Fetch new episodes from RSS feed.

        If `parsed_feed` is supplied, the HTTP fetch and feedparser parse are
        skipped. The feed_manager uses this to avoid double-fetching each RSS
        body per refresh (spec #19).

        Args:
            url: RSS feed URL
            existing_episodes: List of episodes already tracked. Used as the
                dedup source unless ``known_external_ids`` is supplied.
            last_processed: Timestamp of last processed episode (for incremental fetch)
            max_episodes: Optional limit on number of episodes to return
            podcast_slug: Optional podcast slug for saving debug RSS file
            parsed_feed: Optional pre-parsed feedparser result. Skips fetch+parse if set.
            known_external_ids: Optional set of already-tracked external IDs.
                When supplied, replaces the linear scan over
                ``existing_episodes`` on the refresh hot path (spec #19 PR 3).

        Returns:
            List of new Episode objects from the feed
        """
        try:
            if parsed_feed is None:
                # Fetch raw RSS content and optionally save for debugging
                fetch_result = self.fetch_rss_content(url, podcast_slug)
                rss_content = fetch_result.content
                if rss_content is None:
                    logger.warning(f"Failed to fetch RSS feed: {url}")
                    return []

                parsed_feed = self.parse_rss(rss_content, url)
                if parsed_feed is None:
                    logger.warning(f"Invalid RSS feed during episode fetch: {url}")
                    return []

            if known_external_ids is not None:
                seen_ids = known_external_ids
                known_count = len(known_external_ids)
            else:
                seen_ids = {ep.external_id for ep in existing_episodes}
                known_count = len(existing_episodes)

            episodes = []
            for entry in parsed_feed.entries:
                episode_date = self._parse_date(entry.get("published_parsed"))
                episode_external_id = entry.get("guid", entry.get("id", str(episode_date)))

                if episode_external_id in seen_ids:
                    continue

                # Include episode if:
                # 1. last_processed is not set (first refresh), OR
                # 2. It's newer than last_processed, OR
                # 3. We have very few episodes tracked (indicates tracking was broken/reset)
                should_include = (
                    last_processed is None or episode_date > last_processed or known_count < 3
                )  # Assume most feeds have >3 episodes

                if should_include:
                    audio_url, audio_file_size, audio_mime_type = self._extract_enclosure_info(entry)
                    if audio_url:
                        description, description_html = self._extract_descriptions(entry)

                        # THES-143: Extract explicit flag
                        explicit = self._parse_explicit_flag(getattr(entry, "itunes_explicit", None))

                        # THES-143: Extract episode type (full, trailer, bonus)
                        episode_type = None
                        itunes_episode_type = getattr(entry, "itunes_episodetype", None)
                        if itunes_episode_type and itunes_episode_type.lower() in ("full", "trailer", "bonus"):
                            episode_type = itunes_episode_type.lower()

                        # THES-144: Extract episode and season numbers
                        episode_number = self._parse_int_field(getattr(entry, "itunes_episode", None))
                        season_number = self._parse_int_field(getattr(entry, "itunes_season", None))

                        # THES-144: Extract episode website URL
                        website_url = entry.get("link")

                        episode = Episode(
                            title=entry.get("title", "Unknown Episode"),
                            description=description,
                            description_html=description_html,
                            pub_date=episode_date,
                            audio_url=audio_url,  # type: ignore[arg-type]  # feedparser returns str, Pydantic validates to HttpUrl
                            duration=parse_duration(entry.get("itunes_duration")),
                            external_id=episode_external_id,
                            image_url=self._extract_episode_image(entry),
                            # THES-143: Essential metadata
                            explicit=explicit,
                            episode_type=episode_type,
                            # THES-144: Episode organization
                            episode_number=episode_number,
                            season_number=season_number,
                            website_url=website_url,
                            # THES-145: Enclosure metadata
                            audio_file_size=audio_file_size,
                            audio_mime_type=audio_mime_type,
                        )
                        episodes.append(episode)

            # Apply max_episodes limit if set
            if episodes and max_episodes:
                episodes.sort(key=lambda e: e.pub_date or datetime.min, reverse=True)
                episodes = episodes[:max_episodes]

            return episodes

        except Exception as e:
            logger.error(f"Error fetching episodes from RSS feed {url}: {e}")
            return []

    def download_episode(self, episode: Episode, podcast_title: str, storage_path: str) -> Optional[str]:
        """
        Download episode audio from RSS feed URL.

        Note: This method delegates to the calling code (AudioDownloader) which handles
        HTTP downloads with retry logic. RSS episodes are downloaded via standard HTTP.

        Args:
            episode: Episode to download
            podcast_title: Podcast title (for filename generation)
            storage_path: Directory to save audio file

        Returns:
            None (signals caller to use standard HTTP download)
        """
        # RSS episodes are downloaded via standard HTTP by AudioDownloader
        # This method returns None to signal that behavior
        return None

    def fetch_rss_content(
        self,
        url: str,
        podcast_slug: Optional[str] = None,
        etag: Optional[str] = None,
        last_modified: Optional[str] = None,
    ) -> "FetchRSSResult":
        """
        Fetch RSS content from URL, optionally with conditional GET.

        Uses the shared `requests.Session` with retry + connection pooling.
        When ``etag`` or ``last_modified`` are supplied, sends
        ``If-None-Match`` / ``If-Modified-Since`` so unchanged feeds can
        return 304 and skip the body download (spec #19).

        Emits exactly one ``feed_phase_timing`` event per call. The phase
        label is ``conditional_get_hit`` on 304 and ``http_fetch`` on all
        other outcomes (200, 4xx/5xx, network errors) so hit rate is
        greppable without arithmetic.

        Args:
            url: RSS feed URL.
            podcast_slug: Optional slug for debug file write.
            etag: Previously stored ``ETag`` header, echoed as
                ``If-None-Match``.
            last_modified: Previously stored ``Last-Modified`` header,
                echoed as ``If-Modified-Since``.

        Returns:
            ``FetchRSSResult`` capturing body, cache headers, and status.
        """
        headers: Dict[str, str] = {}
        if etag:
            headers["If-None-Match"] = etag
        if last_modified:
            headers["If-Modified-Since"] = last_modified

        conditional = bool(headers)
        with log_phase_timing("http_fetch", url=url, podcast_slug=podcast_slug, conditional=conditional) as timing_ctx:
            try:
                validate_public_url(url)
                response = self.session.get(url, timeout=self.DEFAULT_TIMEOUT, headers=headers or None)
                timing_ctx["status_code"] = response.status_code
                if response.status_code == 304:
                    timing_ctx["phase"] = "conditional_get_hit"
                    return FetchRSSResult(
                        content=None,
                        status_code=304,
                        etag=response.headers.get("ETag") or etag,
                        last_modified=response.headers.get("Last-Modified") or last_modified,
                        not_modified=True,
                        error=None,
                    )
                response.raise_for_status()
                rss_content = response.text
                timing_ctx["bytes"] = len(rss_content)
            except requests.RequestException as e:
                timing_ctx["error"] = str(e)
                logger.error(f"Error fetching RSS feed {url}: {e}")
                return FetchRSSResult(
                    content=None,
                    status_code=0,
                    etag=None,
                    last_modified=None,
                    not_modified=False,
                    error=str(e),
                )
            except UnsafeURLError as e:
                timing_ctx["error"] = str(e)
                logger.warning("rss_fetch_blocked_unsafe_url", url=url, error=str(e))
                return FetchRSSResult(
                    content=None,
                    status_code=0,
                    etag=None,
                    last_modified=None,
                    not_modified=False,
                    error=f"Unsafe URL refused: {e}",
                )

        if self.path_manager and podcast_slug:
            self._save_debug_rss(podcast_slug, rss_content)

        return FetchRSSResult(
            content=rss_content,
            status_code=response.status_code,
            etag=response.headers.get("ETag"),
            last_modified=response.headers.get("Last-Modified"),
            not_modified=False,
            error=None,
        )

    def parse_rss(self, rss_content: str, url: str = "") -> Optional[Any]:
        """
        Parse RSS content with feedparser. Emits a timed `parse` phase event.

        Args:
            rss_content: Raw RSS XML as string.
            url: Optional feed URL, attached to the timing event for correlation.

        Returns:
            Parsed feedparser result, or None if the content is malformed.
        """
        with log_phase_timing("parse", url=url, bytes=len(rss_content)) as parse_ctx:
            parsed_feed = feedparser.parse(rss_content)
            parse_ctx["entries"] = len(parsed_feed.entries)
        if parsed_feed.bozo:
            logger.warning(f"Invalid RSS feed: {url}")
            return None
        return parsed_feed

    def fetch_and_parse(
        self,
        url: str,
        podcast_slug: Optional[str] = None,
        etag: Optional[str] = None,
        last_modified: Optional[str] = None,
    ) -> "FetchAndParseResult":
        """
        One-shot fetch + parse with conditional-GET support.

        Replaces the historical pattern of fetching and parsing once for
        metadata extraction and again for episode extraction — see spec #19.
        When ``etag``/``last_modified`` are supplied and the server returns
        304, no parse is performed and the caller short-circuits the whole
        refresh for this podcast.

        Args:
            url: RSS feed URL.
            podcast_slug: Optional slug for debug RSS saving.
            etag: Previously stored ``ETag`` header (conditional GET input).
            last_modified: Previously stored ``Last-Modified`` header
                (conditional GET input).

        Returns:
            ``FetchAndParseResult`` — body, parsed feed, cache headers, and
            ``not_modified`` flag. On 304, ``content`` and ``parsed_feed``
            are both ``None`` and ``not_modified`` is ``True``. On error,
            all fields except ``error`` are ``None``/``False``.
        """
        fetch = self.fetch_rss_content(url, podcast_slug, etag=etag, last_modified=last_modified)
        if fetch.not_modified or fetch.content is None:
            return FetchAndParseResult(
                content=fetch.content,
                parsed_feed=None,
                status_code=fetch.status_code,
                etag=fetch.etag,
                last_modified=fetch.last_modified,
                not_modified=fetch.not_modified,
                error=fetch.error,
            )
        parsed_feed = self.parse_rss(fetch.content, url)
        return FetchAndParseResult(
            content=fetch.content,
            parsed_feed=parsed_feed,
            status_code=fetch.status_code,
            etag=fetch.etag,
            last_modified=fetch.last_modified,
            not_modified=False,
            error=None,
        )

    def _save_debug_rss(self, podcast_slug: str, content: str) -> None:
        """
        Save RSS content to debug file for troubleshooting.

        Overwrites previous version on each refresh.

        Args:
            podcast_slug: Slugified podcast title
            content: Raw RSS XML content
        """
        try:
            if not self.path_manager:
                return

            debug_file = self.path_manager.debug_feed_file(podcast_slug)
            debug_file.parent.mkdir(parents=True, exist_ok=True)
            debug_file.write_text(content, encoding="utf-8")
            logger.debug(f"Saved debug RSS feed: {debug_file}")

        except Exception as e:
            # Don't fail the refresh if debug save fails
            logger.warning(f"Failed to save debug RSS feed for {podcast_slug}: {e}")

    def _extract_categories(self, rss_content: str) -> Dict[str, Optional[str]]:
        """
        Extract and validate podcast categories from raw RSS content.

        Parses itunes:category tags from the channel element. Podcasts can have
        up to two categories (primary and secondary), each with an optional subcategory.

        RSS structure:
        ```xml
        <itunes:category text="Society &amp; Culture">
            <itunes:category text="Documentary"/>
        </itunes:category>
        <itunes:category text="News">
            <itunes:category text="Politics"/>
        </itunes:category>
        ```

        Args:
            rss_content: Raw RSS XML content

        Returns:
            Dict with keys: primary_category, primary_subcategory,
                           secondary_category, secondary_subcategory
            All values may be None if not found or invalid.
        """
        result: Dict[str, Optional[str]] = {
            "primary_category": None,
            "primary_subcategory": None,
            "secondary_category": None,
            "secondary_subcategory": None,
        }

        try:
            root = ET.fromstring(rss_content)
        except ET.ParseError as e:
            logger.warning(f"Failed to parse RSS XML for category extraction: {e}")
            return result

        # Define iTunes namespace
        ns = {"itunes": "http://www.itunes.com/dtds/podcast-1.0.dtd"}

        # Find channel element
        channel = root.find("channel")
        if channel is None:
            return result

        # Find all top-level itunes:category tags in channel
        # (not nested ones - those are subcategories)
        categories = channel.findall("itunes:category", ns)
        if not categories:
            return result

        # Process primary category (first itunes:category)
        if len(categories) >= 1:
            primary = categories[0]
            primary_cat = primary.get("text", "")

            # Decode HTML entities (e.g., "&amp;" -> "&")
            if primary_cat:
                primary_cat = self._decode_html_entities(primary_cat)

            # Look for nested subcategory
            primary_sub = None
            nested = primary.find("itunes:category", ns)
            if nested is not None:
                primary_sub = nested.get("text", "")
                if primary_sub:
                    primary_sub = self._decode_html_entities(primary_sub)

            # Validate against Apple taxonomy
            validated = validate_category(primary_cat, primary_sub)
            result["primary_category"] = validated.category
            result["primary_subcategory"] = validated.subcategory

            if validated.category:
                logger.debug(f"Extracted primary category: {validated.category} / {validated.subcategory}")

        # Process secondary category (second itunes:category)
        if len(categories) >= 2:
            secondary = categories[1]
            secondary_cat = secondary.get("text", "")

            if secondary_cat:
                secondary_cat = self._decode_html_entities(secondary_cat)

            # Look for nested subcategory
            secondary_sub = None
            nested = secondary.find("itunes:category", ns)
            if nested is not None:
                secondary_sub = nested.get("text", "")
                if secondary_sub:
                    secondary_sub = self._decode_html_entities(secondary_sub)

            # Validate against Apple taxonomy
            validated = validate_category(secondary_cat, secondary_sub)
            result["secondary_category"] = validated.category
            result["secondary_subcategory"] = validated.subcategory

            if validated.category:
                logger.debug(f"Extracted secondary category: {validated.category} / {validated.subcategory}")

        return result

    def _decode_html_entities(self, text: str) -> str:
        """
        Decode HTML entities in text.

        Common entities in RSS: &amp; -> &, &quot; -> ", etc.

        Args:
            text: Text with potential HTML entities

        Returns:
            Decoded text
        """
        import html

        return html.unescape(text)

    def _parse_explicit_flag(self, value: Any) -> Optional[bool]:
        """
        Parse itunes:explicit value to boolean.

        Apple Podcasts accepts: "true", "false", "yes", "no", "clean", "explicit"
        Legacy values "clean" and "explicit" are still supported.

        Args:
            value: Raw value from RSS feed

        Returns:
            True if explicit, False if clean, None if not specified
        """
        if value is None:
            return None

        value_str = str(value).lower().strip()
        if value_str in ("true", "yes", "explicit"):
            return True
        if value_str in ("false", "no", "clean"):
            return False
        return None

    def _extract_new_feed_url(self, rss_content: str) -> Optional[str]:
        """
        Extract itunes:new-feed-url from RSS content.

        This tag indicates the podcast has moved to a new RSS URL.
        Used for feed migration detection.

        Args:
            rss_content: Raw RSS XML content

        Returns:
            New feed URL if found, None otherwise
        """
        try:
            root = ET.fromstring(rss_content)
        except ET.ParseError:
            return None

        ns = {"itunes": "http://www.itunes.com/dtds/podcast-1.0.dtd"}
        channel = root.find("channel")
        if channel is None:
            return None

        new_feed_url = channel.find("itunes:new-feed-url", ns)
        if new_feed_url is not None and new_feed_url.text:
            return new_feed_url.text.strip()

        return None

    def _extract_rss_from_apple_url(self, url: str) -> Optional[str]:
        """
        Extract RSS feed URL from Apple Podcast URL using iTunes Lookup API.

        Args:
            url: Apple Podcast URL

        Returns:
            RSS feed URL or None if not an Apple URL or extraction fails
        """
        try:
            # Check if this is an Apple Podcast URL
            if "podcasts.apple.com" not in url and "itunes.apple.com" not in url:
                return None

            # Extract podcast ID from URL
            podcast_id = extract_apple_podcast_id(url)
            if not podcast_id:
                logger.warning(f"Could not extract podcast ID from Apple URL: {url}")
                return None

            # Use iTunes Lookup API to get RSS feed
            lookup_url = f"https://itunes.apple.com/lookup?id={podcast_id}"

            with urllib.request.urlopen(lookup_url) as response:
                data = json.load(response)

            if data.get("resultCount", 0) > 0:
                result = data["results"][0]
                feed_url = result.get("feedUrl")
                if feed_url:
                    logger.info(f"Extracted RSS feed from Apple Podcast: {feed_url}")
                    return str(feed_url)
                logger.warning(f"No RSS feed URL found for podcast ID {podcast_id}")
                return None

            # If the ID doesn't work, try to resolve redirect
            logger.info(f"No podcast found for ID {podcast_id}, attempting to resolve redirect...")
            return self._resolve_apple_podcast_redirect(url)

        except Exception as e:
            logger.error(f"Error extracting RSS from Apple URL {url}: {e}")
            return None

    def _resolve_apple_podcast_redirect(self, url: str) -> Optional[str]:
        """
        Resolve Apple Podcast redirects to get the actual podcast ID.

        Args:
            url: Apple Podcast URL that may redirect

        Returns:
            RSS feed URL if found, None otherwise
        """
        try:
            # Refuse to follow user-supplied Apple URLs into
            # private / loopback / cloud-metadata space.
            validate_public_url(url)
            request = urllib.request.Request(url)
            request.add_header("User-Agent", "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36")

            with urllib.request.urlopen(request) as response:  # noqa: S310 — URL is SSRF-validated above
                page_content = response.read().decode("utf-8", errors="ignore")

                # Extract all potential IDs from the page content. Bound is
                # 12 digits (Apple IDs are 10) — see utils.url_patterns.
                id_matches = APPLE_PODCAST_ID_RE.findall(page_content)

                # Try each ID found on the page
                for potential_id in set(id_matches):  # Use set to avoid duplicates
                    logger.debug(f"Trying podcast ID: {potential_id}")

                    lookup_url = f"https://itunes.apple.com/lookup?id={potential_id}"
                    try:
                        with urllib.request.urlopen(lookup_url) as api_response:
                            data = json.load(api_response)

                        if data.get("resultCount", 0) > 0:
                            result = data["results"][0]
                            feed_url = result.get("feedUrl")
                            if feed_url:
                                logger.info(f"Successfully found RSS feed with ID {potential_id}: {feed_url}")
                                return str(feed_url)
                    except Exception as id_error:
                        logger.debug(f"Failed to lookup ID {potential_id}: {id_error}")
                        continue

                return None

        except Exception as e:
            logger.error(f"Error resolving Apple Podcast redirect {url}: {e}")
            return None

    def _parse_date(self, date_tuple: Any) -> datetime:
        """
        Parse feedparser date tuple to datetime.

        Args:
            date_tuple: Feedparser date tuple (time.struct_time or None)

        Returns:
            Parsed datetime or current datetime if parsing fails
        """
        if date_tuple:
            try:
                return datetime(*date_tuple[:6])
            except (TypeError, ValueError):
                pass
        return datetime.now()

    def _extract_audio_url(self, entry: Any) -> Optional[str]:
        """
        Extract audio URL from feed entry.

        Args:
            entry: Feedparser entry object

        Returns:
            Audio URL if found, None otherwise
        """
        url, _, _ = self._extract_enclosure_info(entry)
        return url

    def _extract_enclosure_info(self, entry: Any) -> tuple[Optional[str], Optional[int], Optional[str]]:
        """
        Extract audio enclosure info from feed entry.

        THES-145: Returns URL plus length and type attributes from enclosure tag.

        Args:
            entry: Feedparser entry object

        Returns:
            Tuple of (audio_url, file_size_bytes, mime_type)
        """
        # Check links first
        for link in entry.get("links", []):
            mime_type = link.get("type", "")
            if mime_type.startswith("audio/"):
                href = link.get("href")
                if href:
                    length = self._parse_int_field(link.get("length"))
                    return str(href), length, mime_type

        # Check enclosures
        for enclosure in entry.get("enclosures", []):
            mime_type = enclosure.get("type", "")
            if mime_type.startswith("audio/"):
                href = enclosure.get("href")
                if href:
                    length = self._parse_int_field(enclosure.get("length"))
                    return str(href), length, mime_type

        return None, None, None

    def _parse_int_field(self, value: Any) -> Optional[int]:
        """
        Parse a value to integer, returning None if invalid.

        Used for episode/season numbers and file sizes.

        Args:
            value: Value to parse (string or int)

        Returns:
            Integer value or None if parsing fails
        """
        if value is None:
            return None
        try:
            result = int(value)
            return result if result > 0 else None  # Only positive integers
        except (ValueError, TypeError):
            return None

    def _extract_descriptions(self, entry: Any) -> tuple[str, str]:
        """
        Extract plain text and HTML descriptions from RSS entry.

        RSS feeds may provide descriptions in multiple formats:
        - entry.description: Usually plain text (links stripped)
        - entry.content: List of content objects, may include HTML with <a> tags

        Args:
            entry: Feedparser entry object

        Returns:
            Tuple of (plain_text_description, html_description)
        """
        plain_text = entry.get("description", "")
        html_content = ""

        # Check for HTML content in entry.content
        content_list = entry.get("content", [])
        for c in content_list:
            content_type = c.get("type", "")
            content_value = c.get("value", "")
            if content_type == "text/html" and content_value:
                html_content = content_value
                break

        return plain_text, html_content

    def _extract_episode_image(self, entry: Any) -> Optional[str]:
        """
        Extract episode-specific artwork from RSS entry.

        Priority:
        1. itunes:image on the item (higher quality)
        2. Standard RSS image within item

        Args:
            entry: Feedparser entry object

        Returns:
            Image URL if found, None otherwise (will fall back to podcast artwork)
        """
        # Priority 1: itunes:image (higher quality, podcast-specific)
        if hasattr(entry, "itunes_image") and entry.itunes_image:
            if isinstance(entry.itunes_image, dict):
                image_url = entry.itunes_image.get("href")
                if image_url:
                    logger.debug(f"Found episode iTunes artwork: {image_url[:80]}...")
                    return image_url
            else:
                image_url = str(entry.itunes_image)
                if image_url:
                    logger.debug(f"Found episode iTunes artwork: {image_url[:80]}...")
                    return image_url

        # Priority 2: Standard RSS image within item
        if hasattr(entry, "image") and entry.image:
            if hasattr(entry.image, "href"):
                image_url = entry.image.href
                if image_url:
                    logger.debug(f"Found episode RSS artwork: {image_url[:80]}...")
                    return image_url
            elif isinstance(entry.image, dict):
                image_url = entry.image.get("href") or entry.image.get("url")
                if image_url:
                    logger.debug(f"Found episode RSS artwork: {image_url[:80]}...")
                    return image_url

        return None

    def extract_transcript_links(self, rss_content: str) -> Dict[str, List[TranscriptLink]]:
        """
        Extract podcast:transcript links from raw RSS content.

        Feedparser only returns the last transcript tag per entry, so we need to
        parse the raw XML to get all transcript formats (SRT, VTT, JSON, etc.).

        Args:
            rss_content: Raw RSS XML content

        Returns:
            Dict mapping episode GUID -> list of TranscriptLink objects
        """
        result: Dict[str, List[TranscriptLink]] = {}

        try:
            root = ET.fromstring(rss_content)
        except ET.ParseError as e:
            logger.warning(f"Failed to parse RSS XML for transcript extraction: {e}")
            return result

        # Define podcast namespace
        ns = {"podcast": "https://podcastindex.org/namespace/1.0"}

        # Find all items
        for item in root.findall(".//item"):
            # Get episode GUID (same logic as feedparser)
            guid_elem = item.find("guid")
            id_elem = item.find("id")  # Fallback

            if guid_elem is not None and guid_elem.text:
                episode_guid = guid_elem.text
            elif id_elem is not None and id_elem.text:
                episode_guid = id_elem.text
            else:
                # Skip items without identifiable GUID
                continue

            # Find all podcast:transcript tags for this item
            transcripts = item.findall("podcast:transcript", ns)
            if not transcripts:
                continue

            links: List[TranscriptLink] = []
            for t in transcripts:
                url = t.get("url")
                mime_type = t.get("type")

                if not url or not mime_type:
                    continue

                try:
                    link = TranscriptLink(
                        url=url,  # type: ignore[arg-type]  # Pydantic validates to HttpUrl
                        mime_type=mime_type,
                        language=t.get("language"),
                        rel=t.get("rel"),
                    )
                    links.append(link)
                except Exception as e:
                    logger.debug(f"Failed to create TranscriptLink for {url}: {e}")
                    continue

            if links:
                result[episode_guid] = links
                logger.debug(f"Found {len(links)} transcript links for episode {episode_guid[:50]}...")

        if result:
            logger.info(f"Extracted transcript links for {len(result)} episodes")

        return result


class YouTubeMediaSource(MediaSource):
    """
    Media source implementation for YouTube channels and playlists.

    Handles:
    - YouTube videos, playlists, channels
    - yt-dlp-based episode extraction
    - Audio-only downloads
    """

    def __init__(self, storage_path: str):
        """
        Initialize YouTube media source.

        Args:
            storage_path: Directory for storing downloaded audio files
        """
        self.youtube_downloader = YouTubeDownloader(storage_path)

    def is_valid_url(self, url: str) -> bool:
        """
        Check if URL is a YouTube URL.

        Args:
            url: URL to validate

        Returns:
            True if URL is YouTube, False otherwise
        """
        return YouTubeDownloader.is_youtube_url(url)

    def extract_metadata(self, url: str) -> Optional[Dict[str, Any]]:
        """
        Extract playlist/channel metadata from YouTube URL.

        Args:
            url: YouTube playlist or channel URL

        Returns:
            Dictionary with 'title', 'description', 'uploader', 'language' or None if extraction fails
        """
        try:
            playlist_info = self.youtube_downloader.extract_playlist_info(url)
            if not playlist_info:
                logger.warning(f"Could not extract YouTube playlist info from: {url}")
                return None

            return {
                "title": playlist_info.get("title", "Unknown YouTube Podcast"),
                "description": playlist_info.get("description", ""),
                "uploader": playlist_info.get("uploader", ""),
                "rss_url": url,  # YouTube URL is treated as RSS URL
                "language": "en",  # Default to English for YouTube (could be enhanced with yt-dlp metadata)
            }

        except Exception as e:
            logger.error(f"Error extracting YouTube metadata from {url}: {e}")
            return None

    def fetch_episodes(
        self,
        url: str,
        existing_episodes: List[Episode],
        last_processed: Optional[datetime] = None,
        max_episodes: Optional[int] = None,
    ) -> List[Episode]:
        """
        Fetch new episodes from YouTube playlist/channel.

        Args:
            url: YouTube playlist or channel URL
            existing_episodes: List of episodes already tracked
            last_processed: Timestamp of last processed episode (unused for YouTube)
            max_episodes: Optional limit on number of episodes to return

        Returns:
            List of new Episode objects from YouTube
        """
        try:
            # Get all episodes from YouTube
            all_episodes = self.youtube_downloader.get_episodes_from_playlist(url)

            # Apply limit before filtering (most recent episodes first)
            if max_episodes:
                all_episodes.sort(key=lambda e: e.pub_date or datetime.min, reverse=True)
                all_episodes = all_episodes[:max_episodes]

            # Filter out episodes that already exist in the database
            new_episodes = []
            for episode in all_episodes:
                # Check if episode already exists (regardless of state)
                existing_episode = next((ep for ep in existing_episodes if ep.external_id == episode.external_id), None)
                if not existing_episode:
                    new_episodes.append(episode)

            return new_episodes

        except Exception as e:
            logger.error(f"Error fetching episodes from YouTube {url}: {e}")
            return []

    def download_episode(self, episode: Episode, podcast_title: str, storage_path: str) -> Optional[str]:
        """
        Download YouTube video as audio file.

        Args:
            episode: Episode to download (with YouTube URL)
            podcast_title: Podcast title (for filename generation)
            storage_path: Directory to save audio file (unused, YouTubeDownloader has its own)

        Returns:
            Path to downloaded audio file, or None if download fails
        """
        return self.youtube_downloader.download_episode(episode, podcast_title)


class MediaSourceFactory:
    """
    Factory for detecting and creating appropriate media source instances.

    Automatically detects the source type from URL patterns and returns
    the appropriate MediaSource implementation.
    """

    def __init__(self, storage_path: str, path_manager: Optional["PathManager"] = None):
        """
        Initialize media source factory.

        Args:
            storage_path: Directory for storing downloaded audio files
            path_manager: Optional path manager for debug RSS saving
        """
        self.storage_path = storage_path
        self.path_manager = path_manager
        # Initialize sources
        self._youtube_source = YouTubeMediaSource(storage_path)
        self._rss_source = RSSMediaSource(path_manager)

    def detect_source(self, url: str) -> MediaSource:
        """
        Detect media source type from URL and return appropriate implementation.

        Detection order:
        1. YouTube (explicit patterns)
        2. RSS (default fallback)

        Args:
            url: URL to detect source type for

        Returns:
            MediaSource implementation for the detected source type
        """
        # Check YouTube first (explicit patterns)
        if self._youtube_source.is_valid_url(url):
            return self._youtube_source

        # Default to RSS (handles RSS feeds and Apple Podcasts)
        return self._rss_source

    def get_youtube_source(self) -> YouTubeMediaSource:
        """
        Get YouTube source instance.

        Returns:
            YouTubeMediaSource instance
        """
        return self._youtube_source

    def get_rss_source(self) -> RSSMediaSource:
        """
        Get RSS source instance.

        Returns:
            RSSMediaSource instance
        """
        return self._rss_source
