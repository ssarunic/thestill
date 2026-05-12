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
Import-arbitrary-episodes service (spec #31).

Lets a user paste an external URL and have the resulting episode land in
their inbox immediately. The pipeline (download → downsample → transcribe →
clean → summarize → entity branch) runs unchanged in the background; this
module only handles URL resolution, parent-podcast bootstrap, and inbox-row
creation.

Resolver lineup is pluggable. The current resolvers cover direct audio URLs
(.mp3, .m4a, .opus, .ogg, .wav); YouTube and Apple resolvers will plug into
the same protocol.
"""

import hashlib
from dataclasses import dataclass
from datetime import datetime, timedelta, timezone
from typing import Any, Callable, List, Literal, Optional, Protocol, Sequence, Tuple
from urllib.parse import urlparse, urlunparse

from structlog import get_logger

from ..core.queue_manager import QueueManager, TaskStage
from ..models.inbox import InboxEntry
from ..repositories.inbox_repository import InboxRepository
from ..repositories.sqlite_podcast_repository import SqlitePodcastRepository
from ..utils.url_patterns import (
    extract_apple_episode_id,
    extract_apple_podcast_id,
    is_apple_podcast_url,
    is_youtube_url,
)

logger = get_logger(__name__)


# Audio file extensions recognised by ``BareAudioResolver``. Lower-case;
# matches are case-insensitive.
_BARE_AUDIO_EXTENSIONS = (".mp3", ".m4a", ".opus", ".ogg", ".wav")

# Tracking / analytics query params we drop during URL normalisation so the
# same logical episode shared with different campaign tags collapses to the
# same canonical id. Conservative list: only well-known tracking keys.
_TRACKING_PARAMS = frozenset(
    {
        "utm_source",
        "utm_medium",
        "utm_campaign",
        "utm_term",
        "utm_content",
        "fbclid",
        "gclid",
        "mc_cid",
        "mc_eid",
        "_hsenc",
        "_hsmi",
    }
)


class ImportError(Exception):
    """Base exception for import failures."""


class UnsupportedUrlError(ImportError):
    """No registered resolver can handle this URL."""


class ResolverError(ImportError):
    """A resolver matched the URL but failed to build a CanonicalSource."""


@dataclass(frozen=True)
class CanonicalParent:
    """
    A real parent podcast deduced from the URL.

    ``rss_url`` is the feed the existing refresh loop will use IF a user
    follows the auto-added podcast — it is captured at import time so
    follow-the-channel works without re-resolving the URL.
    """

    external_id: str
    rss_url: str
    title: str
    description: str = ""
    image_url: Optional[str] = None


@dataclass(frozen=True)
class CanonicalSource:
    """
    Normalised, resolver-issued description of an importable URL.

    ``parent`` is set by resolvers that can deduce the source podcast
    (YouTube channel, RSS feed). Bare-audio URLs leave it ``None`` and
    fall back to the synthetic audio-imports row.
    """

    kind: Literal["bare_audio", "youtube", "apple_episode", "rss_episode"]
    canonical_id: str  # e.g. "audio:<sha256>"
    audio_url: str  # what the download stage fetches
    title: str
    description: str = ""
    duration_seconds: Optional[int] = None
    pub_date: Optional[datetime] = None
    image_url: Optional[str] = None
    source_handle: str = ""  # display label (host for bare audio, channel for YouTube)
    external_id: str = ""  # episode-level external id (video_id for YouTube)
    parent: Optional[CanonicalParent] = None


class Resolver(Protocol):
    """Pluggable URL → CanonicalSource adapter."""

    def matches(self, url: str) -> bool: ...

    def resolve(self, url: str) -> CanonicalSource: ...


def _normalise_url(url: str) -> str:
    """
    Stable form of a URL for canonical-id derivation.

    Lower-cases the host, strips tracking query params, drops the fragment.
    Path case is preserved — many CDNs treat the path as case-sensitive.
    """
    parsed = urlparse(url.strip())
    host = (parsed.hostname or "").lower()
    if parsed.port:
        host = f"{host}:{parsed.port}"
    if parsed.username or parsed.password:
        userinfo = parsed.username or ""
        if parsed.password:
            userinfo = f"{userinfo}:{parsed.password}"
        netloc = f"{userinfo}@{host}" if userinfo else host
    else:
        netloc = host

    if parsed.query:
        kept = [
            (k, v)
            for k, v in (pair.partition("=")[::2] for pair in parsed.query.split("&") if pair)
            if k.lower() not in _TRACKING_PARAMS
        ]
        query = "&".join(f"{k}={v}" if v else k for k, v in kept)
    else:
        query = ""

    return urlunparse((parsed.scheme.lower(), netloc, parsed.path, parsed.params, query, ""))


class BareAudioResolver:
    """
    Resolver for direct audio URLs (no RSS feed, no platform metadata).

    ``resolve`` does NOT make a network call — everything we need is in the
    URL itself (extension, filename, host). The actual audio is fetched
    later by the existing download stage, which is also where transport
    errors surface.
    """

    def matches(self, url: str) -> bool:
        try:
            parsed = urlparse(url.strip())
        except ValueError:
            return False
        if parsed.scheme not in ("http", "https"):
            return False
        path = (parsed.path or "").lower()
        return path.endswith(_BARE_AUDIO_EXTENSIONS)

    def resolve(self, url: str) -> CanonicalSource:
        normalised = _normalise_url(url)
        digest = hashlib.sha256(normalised.encode("utf-8")).hexdigest()
        canonical_id = f"audio:{digest}"
        parsed = urlparse(normalised)
        filename = (parsed.path.rsplit("/", 1)[-1] or "audio").rsplit(".", 1)[0]
        title = filename.replace("_", " ").replace("-", " ").strip() or "Imported audio"
        return CanonicalSource(
            kind="bare_audio",
            canonical_id=canonical_id,
            audio_url=normalised,
            title=title,
            source_handle=parsed.hostname or "",
            external_id=digest,
        )


YouTubeMetadataFetcher = Callable[[str], dict]
"""Function that returns the yt-dlp metadata dict for a YouTube URL.

Indirection lets tests inject canned metadata without invoking yt-dlp or
hitting the network.
"""


def _default_youtube_metadata_fetch(url: str) -> dict:
    import yt_dlp

    opts = {"quiet": True, "no_warnings": True, "skip_download": True, "extract_flat": False}
    with yt_dlp.YoutubeDL(opts) as ydl:
        info = ydl.extract_info(url, download=False)
    if not isinstance(info, dict):
        raise ResolverError(f"yt-dlp returned no metadata for {url!r}")
    return info


def _yt_pub_date(raw: Any) -> Optional[datetime]:
    """yt-dlp emits ``upload_date`` as a YYYYMMDD string. Normalise to UTC."""
    if not raw:
        return None
    if isinstance(raw, datetime):
        return raw
    text = str(raw)
    if len(text) == 8 and text.isdigit():
        try:
            return datetime.strptime(text, "%Y%m%d")
        except ValueError:
            return None
    return None


def _yt_thumbnail(info: dict) -> Optional[str]:
    """Pick the largest thumbnail; fall back to the top-level ``thumbnail`` field."""
    thumbs = info.get("thumbnails") or []
    if isinstance(thumbs, list) and thumbs:
        last = thumbs[-1]
        if isinstance(last, dict) and last.get("url"):
            return last["url"]
    fallback = info.get("thumbnail")
    return fallback if isinstance(fallback, str) else None


class YouTubeResolver:
    """
    Resolver for YouTube watch / shorts / youtu.be URLs.

    Uses yt-dlp's ``extract_info`` for metadata. The download stage already
    routes YouTube URLs to yt-dlp automatically (via ``MediaSourceFactory``),
    so the resolved ``audio_url`` here is just the public watch URL.

    Each video carries its channel as a ``CanonicalParent`` so the import
    flow can upsert the channel into ``podcasts`` (auto_added=1) and the
    UI can offer "Follow this channel" without re-resolving the URL.
    """

    def __init__(self, *, metadata_fetcher: Optional[YouTubeMetadataFetcher] = None) -> None:
        self._fetch = metadata_fetcher or _default_youtube_metadata_fetch

    def matches(self, url: str) -> bool:
        return is_youtube_url(url)

    def resolve(self, url: str) -> CanonicalSource:
        info = self._fetch(url)
        video_id = info.get("id")
        if not isinstance(video_id, str) or not video_id:
            raise ResolverError(f"yt-dlp metadata missing 'id' for {url!r}")
        title = info.get("title") or "Untitled YouTube video"
        webpage = info.get("webpage_url") or url
        channel_id = info.get("channel_id") or info.get("uploader_id")
        channel_name = info.get("channel") or info.get("uploader") or "YouTube"
        duration_raw = info.get("duration")
        duration = int(duration_raw) if isinstance(duration_raw, (int, float)) else None
        parent: Optional[CanonicalParent] = None
        if isinstance(channel_id, str) and channel_id.startswith("UC"):
            parent = CanonicalParent(
                external_id=channel_id,
                rss_url=f"https://www.youtube.com/feeds/videos.xml?channel_id={channel_id}",
                title=channel_name,
                description=info.get("channel_description") or "",
                image_url=info.get("channel_thumbnail"),
            )
        return CanonicalSource(
            kind="youtube",
            canonical_id=f"youtube:{video_id}",
            audio_url=webpage,
            title=title,
            description=info.get("description") or "",
            duration_seconds=duration,
            pub_date=_yt_pub_date(info.get("upload_date") or info.get("timestamp")),
            image_url=_yt_thumbnail(info),
            source_handle=channel_name,
            external_id=video_id,
            parent=parent,
        )


AppleEpisodeLookup = Callable[[str, Optional[str]], dict]
"""Function that returns the iTunes Search lookup payload for an Apple
episode track id, optionally given the show's ``collectionId`` for a
fallback lookup. Indirection lets tests inject canned responses without
hitting Apple's servers.

The expected response shape is the iTunes Search ``results[0]`` object
for a ``podcastEpisode`` entity: ``trackId``, ``trackName``, ``feedUrl``,
``collectionId``, ``collectionName``, ``episodeUrl``, ``releaseDate``,
``description``, ``trackTimeMillis``, ``artworkUrl600`` (and many more
fields we ignore).
"""


def _itunes_lookup(params: str, *, label: str) -> list:
    import requests

    url = f"https://itunes.apple.com/lookup?{params}"
    try:
        resp = requests.get(
            url,
            timeout=10,
            headers={"User-Agent": "thestill/1.0 (+https://github.com/ssarunic/thestill)"},
        )
    except requests.RequestException as exc:
        raise ResolverError(f"iTunes lookup failed for {label}: {exc}") from exc
    if resp.status_code != 200:
        raise ResolverError(f"iTunes lookup returned HTTP {resp.status_code} for {label}")
    try:
        payload = resp.json()
    except ValueError as exc:
        raise ResolverError(f"iTunes lookup returned non-JSON for {label}: {exc}") from exc
    return payload.get("results") or []


def _default_apple_episode_lookup(track_id: str, collection_id: Optional[str] = None) -> dict:
    # Apple's lookup endpoint is unreliable when keyed on episode ``trackId``:
    # many episodes (especially older ones) are not indexed and the call
    # returns ``resultCount: 0``. Looking up the show by ``collectionId`` with
    # ``entity=podcastEpisode&limit=200`` and filtering locally is the reliable
    # path. We try the direct trackId lookup first as a fast path.
    direct = _itunes_lookup(
        f"id={track_id}&entity=podcastEpisode",
        label=f"trackId {track_id}",
    )
    for entry in direct:
        if entry.get("wrapperType") == "podcastEpisode":
            return entry

    if not collection_id:
        raise ResolverError(f"iTunes lookup found no episode for trackId {track_id}")

    show_results = _itunes_lookup(
        f"id={collection_id}&entity=podcastEpisode&limit=200",
        label=f"collectionId {collection_id}",
    )
    for entry in show_results:
        if entry.get("wrapperType") == "podcastEpisode" and str(entry.get("trackId")) == str(track_id):
            return entry
    raise ResolverError(
        f"iTunes lookup found no episode for trackId {track_id} "
        f"(searched {len(show_results)} entries under collectionId {collection_id})"
    )


def _apple_pub_date(raw: Any) -> Optional[datetime]:
    """Parse Apple's ISO-8601 ``releaseDate`` (e.g. ``2024-01-15T10:00:00Z``)."""
    if not raw:
        return None
    if isinstance(raw, datetime):
        return raw
    if isinstance(raw, str):
        try:
            return datetime.fromisoformat(raw.replace("Z", "+00:00"))
        except ValueError:
            return None
    return None


class ApplePodcastsResolver:
    """
    Resolver for ``podcasts.apple.com`` share links.

    Uses Apple's public iTunes Search API ``lookup`` endpoint to translate
    the share link's ``?i=<track_id>`` into an episode payload that includes
    the audio URL and the show's RSS feed. The download stage then fetches
    the audio directly via the regular HTTP path — the iTunes API is only
    consulted at resolve time.

    Show-only Apple links (``/idNNN`` with no ``?i=``) are not single
    episodes and therefore not supported by this resolver — paste the RSS
    feed or a specific episode link instead.
    """

    def __init__(self, *, episode_lookup: Optional[AppleEpisodeLookup] = None) -> None:
        self._lookup = episode_lookup or _default_apple_episode_lookup

    def matches(self, url: str) -> bool:
        return is_apple_podcast_url(url)

    def resolve(self, url: str) -> CanonicalSource:
        episode_id = extract_apple_episode_id(url)
        if not episode_id:
            raise ResolverError(
                "Apple Podcasts link does not point to a single episode "
                "(missing ?i=<track_id>). Paste the episode link from the "
                "Share menu, not the show URL."
            )
        # Show id from the URL path (always present on a valid share link)
        # is preferred over the lookup payload's collectionId so a buggy
        # iTunes response can't reattach the import to the wrong show. It is
        # also passed to the lookup so it can fall back to a show-level query
        # when the direct trackId lookup misses (Apple's per-episode index is
        # not comprehensive — many older episodes return resultCount=0).
        collection_id = extract_apple_podcast_id(url)
        info = self._lookup(episode_id, collection_id)
        track_name = info.get("trackName") or "Untitled Apple episode"
        episode_audio = info.get("episodeUrl") or info.get("previewUrl")
        if not isinstance(episode_audio, str) or not episode_audio:
            raise ResolverError(f"iTunes lookup did not return an audio URL for trackId {episode_id}")
        feed_url = info.get("feedUrl")
        collection_name = info.get("collectionName") or "Apple Podcasts"
        duration_ms = info.get("trackTimeMillis")
        duration = int(duration_ms / 1000) if isinstance(duration_ms, (int, float)) else None
        parent: Optional[CanonicalParent] = None
        if isinstance(feed_url, str) and feed_url and isinstance(collection_id, (str, int)):
            parent = CanonicalParent(
                external_id=str(collection_id),
                rss_url=feed_url,
                title=collection_name,
                description=info.get("collectionDescription") or "",
                image_url=info.get("artworkUrl600") or info.get("artworkUrl100"),
            )
        return CanonicalSource(
            kind="apple_episode",
            canonical_id=f"apple:{episode_id}",
            audio_url=episode_audio,
            title=track_name,
            description=info.get("description") or "",
            duration_seconds=duration,
            pub_date=_apple_pub_date(info.get("releaseDate")),
            image_url=info.get("artworkUrl600") or info.get("artworkUrl100"),
            source_handle=collection_name,
            external_id=episode_id,
            parent=parent,
        )


@dataclass(frozen=True)
class ImportResult:
    """Outcome of ``ImportService.import_url``."""

    episode_id: str
    canonical_id: str
    title: str
    kind: str
    source_handle: str
    inbox_entry: InboxEntry
    episode_created: bool
    inbox_created: bool
    # The materialised parent podcast (auto_added or pre-existing). All
    # three are NULL when the import fell back to the synthetic
    # ``audio-imports`` parent. Carries enough for a "Follow this channel"
    # CTA without a second DB round-trip on the API layer.
    parent_podcast_id: Optional[str] = None
    parent_title: Optional[str] = None
    parent_slug: Optional[str] = None


class ImportService:
    """
    Orchestrates URL → episode → inbox-row materialisation.

    Idempotency:

    - Re-importing the same URL by the same user returns the existing
      episode + the existing inbox row (no second pipeline task).
    - Re-importing the same URL by a *different* user adds a new inbox
      row pointing at the shared episode (no second Whisper run).
    """

    # Rolling window for the per-user import counter logged on each
    # ``import_completed`` event. Future quota enforcement should read
    # the same window so dashboard and limits stay in sync.
    QUOTA_WINDOW = timedelta(hours=24)

    def __init__(
        self,
        repository: SqlitePodcastRepository,
        inbox_repository: InboxRepository,
        queue_manager: QueueManager,
        resolvers: Optional[Sequence[Resolver]] = None,
        feed_manager: Optional[Any] = None,
    ) -> None:
        self._repository = repository
        self._inbox_repo = inbox_repository
        self._queue = queue_manager
        # Optional. When set, imports that auto-create a parent podcast also
        # trigger a one-shot RSS refresh on that parent so its description,
        # cover, and full episode list show up immediately — instead of the
        # show appearing as "No description, 1 episode" until someone follows
        # it. Best-effort: any failure falls through to the single-episode
        # path so the import still succeeds.
        self._feed_manager = feed_manager
        # Default order: Apple → YouTube → BareAudio. None of the matchers
        # overlap (Apple needs podcasts.apple.com, YouTube needs youtube.com /
        # youtu.be, BareAudio needs an audio extension), so the order is
        # incidental — kept for documentation.
        self._resolvers: List[Resolver] = (
            list(resolvers) if resolvers else [ApplePodcastsResolver(), YouTubeResolver(), BareAudioResolver()]
        )
        logger.info(
            "ImportService initialized",
            resolvers=[type(r).__name__ for r in self._resolvers],
            feed_ingest_on_import=self._feed_manager is not None,
        )

    def import_url(self, *, user_id: str, url: str) -> ImportResult:
        """Import ``url`` into ``user_id``'s inbox. See class docstring for idempotency."""
        canonical = self._resolve(url)
        episode_id, episode_created, parent_summary = self._find_or_create_episode(canonical)
        inbox_entry, inbox_created = self._inbox_repo.find_or_create(
            user_id=user_id,
            episode_id=episode_id,
            source="import",
        )
        if episode_created:
            # Imports start at TRANSCRIBE: the Dalston transcribe handler detects
            # `audio_url` + no downsampled path and fetches the audio itself, so
            # we skip the local download/downsample stages entirely. `run_full_pipeline`
            # keeps clean → summarize → entities chaining after transcribe.
            self._queue.add_task(
                episode_id=episode_id,
                stage=TaskStage.TRANSCRIBE,
                metadata={"run_full_pipeline": True, "initiated_by": "import"},
            )
            logger.info(
                "import_pipeline_enqueued",
                episode_id=episode_id,
                canonical_id=canonical.canonical_id,
                kind=canonical.kind,
            )
        # Quota plumbing — counted but not enforced. The count includes
        # the row we just inserted so logs reflect post-import state.
        imports_in_window = self._inbox_repo.count_imports_for_user_since(
            user_id, datetime.now(timezone.utc) - self.QUOTA_WINDOW
        )
        logger.info(
            "import_completed",
            episode_id=episode_id,
            canonical_id=canonical.canonical_id,
            kind=canonical.kind,
            episode_created=episode_created,
            inbox_created=inbox_created,
            user_id=user_id,
            imports_in_24h=imports_in_window,
        )
        parent_id, parent_title, parent_slug = parent_summary if parent_summary else (None, None, None)
        return ImportResult(
            episode_id=episode_id,
            canonical_id=canonical.canonical_id,
            title=canonical.title,
            kind=canonical.kind,
            source_handle=canonical.source_handle,
            inbox_entry=inbox_entry,
            episode_created=episode_created,
            inbox_created=inbox_created,
            parent_podcast_id=parent_id,
            parent_title=parent_title,
            parent_slug=parent_slug,
        )

    # ------------------------------------------------------------------
    # Internals
    # ------------------------------------------------------------------

    def _resolve(self, url: str) -> CanonicalSource:
        for resolver in self._resolvers:
            if resolver.matches(url):
                try:
                    return resolver.resolve(url)
                except ImportError:
                    raise
                except Exception as exc:
                    raise ResolverError(f"{type(resolver).__name__} failed to resolve {url!r}: {exc}") from exc
        raise UnsupportedUrlError(
            f"No resolver matched URL {url!r}. v1 supports direct audio links " "(.mp3, .m4a, .opus, .ogg, .wav)."
        )

    def _find_or_create_episode(self, canonical: CanonicalSource) -> Tuple[str, bool, Optional[Tuple[str, str, str]]]:
        """Return ``(episode_id, created, parent_summary)``.

        ``parent_summary`` is ``(id, title, slug)`` of the real parent for
        both fresh and dedup imports, or ``None`` when the import lives
        under the synthetic ``audio-imports`` row (no follow target).
        """
        existing_id = self._repository.find_episode_id_by_canonical_id(canonical.canonical_id)
        if existing_id is not None:
            parent_summary = self._repository.get_real_parent_podcast_for_episode(existing_id)
            return existing_id, False, parent_summary

        if canonical.parent is not None:
            parent_summary = self._repository.upsert_auto_added_podcast(
                rss_url=canonical.parent.rss_url,
                title=canonical.parent.title,
                description=canonical.parent.description,
                image_url=canonical.parent.image_url,
            )
            parent_id = parent_summary[0]
            user_visible_parent: Optional[Tuple[str, str, str]] = parent_summary
            # Pull the parent's full RSS so the show isn't stuck on iTunes'
            # thin metadata ("No description, 1 episode"). Best-effort: a
            # failed fetch must not block the import.
            matched_id = self._ingest_parent_feed(parent_id, canonical)
            if matched_id is not None:
                return matched_id, True, user_visible_parent
        else:
            parent_id = self._repository.ensure_synthetic_audio_imports_parent()
            user_visible_parent = None
        episode_id = self._repository.insert_imported_episode(
            podcast_id=parent_id,
            canonical_id=canonical.canonical_id,
            external_id=canonical.external_id or canonical.canonical_id,
            title=canonical.title,
            audio_url=canonical.audio_url,
            description=canonical.description,
            pub_date=canonical.pub_date,
            duration=canonical.duration_seconds,
            image_url=canonical.image_url,
        )
        return episode_id, True, user_visible_parent

    def _ingest_parent_feed(self, parent_id: str, canonical: CanonicalSource) -> Optional[str]:
        """Refresh the parent's RSS and return the matched episode id, if any.

        On success, the parent podcast row gains its full RSS metadata
        (description, cover, categories, etc.) and every episode in the
        feed is inserted as a discovered row. The imported episode is
        identified inside the freshly-discovered set by ``audio_url``
        (Apple's iTunes ``episodeUrl`` matches the RSS enclosure URL
        byte-for-byte; YouTube's webpage URL likewise matches the channel
        feed link), and its ``canonical_id`` is stamped onto that row so
        future imports of the same URL dedup correctly.

        Returns the matched episode id, or ``None`` when:

        - ``feed_manager`` was not injected (CLI / unit-test paths);
        - the RSS fetch failed (logged warning, falls back to the
          single-episode insert below so the import still succeeds);
        - the RSS feed does not contain an episode whose ``audio_url``
          matches ``canonical.audio_url`` (rare — Apple lookup or yt-dlp
          surfaced an episode the canonical RSS does not carry).
        """
        if self._feed_manager is None:
            return None
        try:
            self._feed_manager.get_new_episodes(podcast_id=parent_id)
        except Exception as exc:  # noqa: BLE001 — refresh is best-effort
            logger.warning(
                "import_parent_feed_refresh_failed",
                parent_id=parent_id,
                rss_url=canonical.parent.rss_url if canonical.parent else None,
                error=str(exc),
                exc_info=True,
            )
            return None
        matched = self._repository.find_episode_id_by_audio_url(parent_id, canonical.audio_url)
        if matched is None:
            logger.info(
                "import_episode_not_in_feed",
                parent_id=parent_id,
                audio_url=canonical.audio_url,
                canonical_id=canonical.canonical_id,
            )
            return None
        try:
            self._repository.set_episode_canonical_id(matched, canonical.canonical_id)
        except Exception as exc:  # noqa: BLE001 — race with concurrent import
            logger.warning(
                "import_canonical_id_attach_failed",
                episode_id=matched,
                canonical_id=canonical.canonical_id,
                error=str(exc),
            )
            return None
        logger.info(
            "import_attached_to_feed_episode",
            episode_id=matched,
            parent_id=parent_id,
            canonical_id=canonical.canonical_id,
        )
        return matched
