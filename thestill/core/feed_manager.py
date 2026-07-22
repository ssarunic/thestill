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

import threading
import time
from concurrent.futures import ThreadPoolExecutor, as_completed
from contextlib import contextmanager
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Callable, Dict, List, NamedTuple, Optional, Tuple
from urllib.parse import urlparse

import feedparser
from structlog import get_logger

from ..models.podcast import AlternateEnclosure, Episode, Podcast
from ..repositories.podcast_repository import PodcastRepository
from ..utils.datetime_utils import ensure_utc, now_utc, parse_struct_time_utc
from ..utils.duration import parse_duration
from ..utils.path_manager import PathManager
from ..utils.timing import log_phase_timing
from ..utils.url_guard import UnsafeURLError, guarded_get
from .media_source import MediaSourceFactory, RSSMediaSource
from .refresh_failure import RefreshAttemptResult, RefreshFailure, RefreshFailureKind, classify_fetch_exception

logger = get_logger(__name__)

# On an incremental refresh of an already-tracked podcast we never cap the
# number of new episodes (capping + advancing ``last_processed`` past the
# trimmed ones leaves permanent holes in the feed). This threshold is a pure
# tripwire: a single refresh yielding more than this many new episodes is
# almost certainly a feed re-publishing its back catalogue (rotated GUIDs,
# misconfiguration) rather than genuine new content, so we log loudly — but
# still ingest everything, because dropping would reintroduce the hole.
INCREMENTAL_BATCH_WARN_THRESHOLD = 100


class RefreshOutcome(NamedTuple):
    """Result of a feed-refresh batch (spec #42, FM-4).

    Refresh historically returned only ``episodes_by_podcast`` and the
    error count lived solely in a log line — so a fleet that went quiet
    because its feeds were *erroring* looked identical to a fleet with
    genuinely nothing new. This carries the liveness signal back to the
    caller so the CLI/briefing can surface it (and exit non-zero) instead
    of reporting a silent success.
    """

    episodes_by_podcast: List[Tuple[Podcast, List[Episode]]]
    podcasts_with_errors: int
    total_podcasts: int
    conditional_get_hits: int


class ImageRepairOutcome(NamedTuple):
    """Result of an episode-image repair pass.

    ``refresh`` never re-reads an existing episode's ``image_url``, so artwork
    served behind rotating signed URLs (e.g. Transistor imgproxy) goes stale
    and starts 404ing. This pass re-syncs episode artwork from the live feed.
    """

    podcasts_checked: int
    episodes_updated: int
    podcasts_with_errors: int
    updated_by_podcast: Dict[str, int]


class PodcastFeedManager:
    """
    Manages podcast feeds and episodes.

    Responsibilities:
    - Fetch RSS/YouTube feeds
    - Parse feed data
    - Coordinate episode discovery
    - Manage episode state transitions

    Does NOT handle:
    - Data persistence (delegates to repository)
    - Business logic (delegates to service layer)
    """

    def __init__(
        self,
        podcast_repository: PodcastRepository,
        path_manager: PathManager,
        max_workers: int = 1,
        max_per_host: int = 2,
    ) -> None:
        """
        Initialize feed manager.

        Args:
            podcast_repository: Repository for persistence
            path_manager: Path manager for file operations
            max_workers: Number of parallel workers for refresh. 1 (default) keeps
                the historical serial behavior. Values >1 enable a ThreadPoolExecutor
                over podcasts; see spec #19 for rationale.
            max_per_host: Cap on concurrent HTTP fetches per origin host. Prevents
                hammering shared podcast hosts (Megaphone, Libsyn, Transistor) when
                many feeds live on the same CDN. Only consulted when max_workers>1.
        """
        self.repository: PodcastRepository = podcast_repository
        self.path_manager: PathManager = path_manager
        self.storage_path: Path = Path(path_manager.storage_path)
        self.storage_path.mkdir(exist_ok=True)
        self.media_source_factory: MediaSourceFactory = MediaSourceFactory(
            str(self.path_manager.original_audio_dir()),
            path_manager=path_manager,
        )
        self._in_transaction: bool = False
        self._transaction_podcasts: Dict[str, Podcast] = {}
        self.max_workers: int = max(1, max_workers)
        self.max_per_host: int = max(1, max_per_host)
        self._host_semaphores: Dict[str, threading.Semaphore] = {}
        self._host_semaphore_lock = threading.Lock()

    @contextmanager
    def transaction(self):
        """
        Context manager for batch updates with deferred save.

        Use this when performing multiple episode state updates to avoid
        multiple file I/O operations. The repository save will happen
        once when the context exits.

        Example:
            with feed_manager.transaction():
                feed_manager.mark_episode_downloaded(url1, external_id1, path1)
                feed_manager.mark_episode_downsampled(url1, external_id1, path2)
                feed_manager.mark_episode_processed(url1, external_id1, raw_path, clean_path)
            # Auto-saves once at end

        Note:
            - Nested transactions are not supported (inner transaction is no-op)
            - All updates within transaction apply to in-memory podcast objects
            - Changes are persisted to disk only when context exits normally
            - If an exception occurs, changes may still be persisted (no rollback)
        """
        # If already in transaction, this is a no-op (nested transaction)
        if self._in_transaction:
            yield self
            return

        # Start transaction
        self._in_transaction = True
        self._transaction_podcasts = {}

        try:
            yield self
        finally:
            # Commit: Save all modified podcasts
            for podcast in self._transaction_podcasts.values():
                self.repository.save(podcast)

            # Clear transaction state
            self._in_transaction = False
            self._transaction_podcasts = {}

    def _get_or_cache_podcast(self, podcast_rss_url: str) -> Optional[Podcast]:
        """
        Get podcast from transaction cache or repository.

        Helper for transaction-aware episode updates. Loads podcast from
        repository on first access within transaction and caches for subsequent updates.

        Args:
            podcast_rss_url: RSS URL of the podcast

        Returns:
            Podcast object if found, None otherwise
        """
        # Check cache first
        if podcast_rss_url in self._transaction_podcasts:
            return self._transaction_podcasts[podcast_rss_url]

        # Load from repository and cache
        podcast = self.repository.get_by_url(podcast_rss_url)
        if podcast:
            self._transaction_podcasts[podcast_rss_url] = podcast
        return podcast

    def add_podcast(self, url: str) -> Optional[Podcast]:
        """
        Add a new podcast feed - handles RSS URLs, Apple Podcast URLs, and YouTube URLs.

        Returns:
            The newly added Podcast object, or None if failed or already exists.
        """
        try:
            # spec #25 item 3.4: refuse anything but http(s) before we hand
            # the string to feedparser / yt-dlp. The SSRF guard blocks these
            # schemes downstream too, but failing fast here gives users a
            # clear error and covers the yt-dlp path that bypasses
            # requests.
            parsed = urlparse(url)
            if parsed.scheme.lower() not in ("http", "https"):
                logger.warning("add_podcast_rejected_scheme", url=url, scheme=parsed.scheme)
                return None

            # Detect source type and extract metadata
            source = self.media_source_factory.detect_source(url)
            metadata = source.extract_metadata(url)

            if not metadata:
                logger.error("Could not extract metadata", url=url)
                return None

            # Create podcast entry
            podcast = Podcast(
                title=metadata.get("title", "Unknown Podcast"),
                description=metadata.get("description", ""),
                rss_url=metadata.get("rss_url", url),  # type: ignore[arg-type]  # Pydantic validates to HttpUrl
                image_url=metadata.get("image_url"),
                language=metadata.get("language", "en"),
                primary_category=metadata.get("primary_category"),
                primary_subcategory=metadata.get("primary_subcategory"),
                secondary_category=metadata.get("secondary_category"),
                secondary_subcategory=metadata.get("secondary_subcategory"),
                # THES-142: New RSS metadata fields
                author=metadata.get("author"),
                explicit=metadata.get("explicit"),
                show_type=metadata.get("show_type"),
                website_url=metadata.get("website_url"),
                is_complete=metadata.get("is_complete", False),
                copyright=metadata.get("copyright"),
            )

            # Save if not already exists, otherwise return existing
            podcast_url = str(podcast.rss_url)
            if not self.repository.exists(podcast_url):
                self.repository.save(podcast)
                logger.info("Added podcast", podcast_title=podcast.title, rss_url=podcast_url)
                return podcast
            # Return existing podcast (idempotent behavior)
            existing = self.repository.get_by_url(podcast_url)
            logger.info("Podcast already exists", podcast_title=existing.title if existing else podcast_url)
            return existing

        except Exception as e:
            logger.error("Error adding podcast", url=url, error=str(e), exc_info=True)
            return None

    def remove_podcast(self, rss_url: str) -> bool:
        """Remove a podcast feed"""
        return self.repository.delete(rss_url)

    def _host_semaphore(self, host: str) -> threading.Semaphore:
        """Return a per-host semaphore, created on first access under a lock."""
        with self._host_semaphore_lock:
            sem = self._host_semaphores.get(host)
            if sem is None:
                sem = threading.Semaphore(self.max_per_host)
                self._host_semaphores[host] = sem
        return sem

    def _refresh_single_podcast(
        self,
        podcast: Podcast,
        max_episodes_per_podcast: Optional[int],
        known_external_ids: Optional[set] = None,
    ) -> RefreshAttemptResult:
        """
        Refresh a single podcast. Safe to call from a worker thread.

        Mutates podcast metadata + caching headers in-memory but does
        NOT write to the database. The batch writer at the end of
        :meth:`refresh_feeds` persists every changed podcast and all
        new episodes in a single transaction (spec #19).

        Returns:
            :class:`RefreshAttemptResult` (spec #60 — replaces the historical
            8-tuple). ``failure`` is ``None`` on success and a classified
            :class:`RefreshFailure` otherwise. ``conditional_hit`` is True when
            the server returned 304 and no parse/extract work ran.
            ``headers_rotated`` is True when a 304 response carried a *new* ETag
            / Last-Modified that we must still persist (RFC 7232) — otherwise
            the rotated header is silently dropped and the next refresh sends a
            stale validator, losing the cache hit. ``source`` is the detected
            media source instance, returned so the caller can reuse it for
            transcript-link extraction without re-detecting. ``image_rows`` are
            ``(podcast_id, external_id, image_url)`` triples for every feed entry
            on a 200 — the batch writer re-syncs existing episodes' drifted
            artwork from these (signed CDN URLs rotate and the stored ones go
            stale because new-episode discovery never revisits an existing row).
            ``audio_rows`` are the analogous ``(podcast_id, external_id,
            audio_url, mime_type)`` rows — some hosts (e.g. BBC mediaselector)
            re-publish audio under a new URL while keeping the GUID, so stored
            enclosure URLs 404 for episodes that haven't been fetched yet; the
            batch writer repairs those with a guarded UPDATE scoped to
            not-yet-fetched episodes. The MIME type travels with the URL so
            ``audio_mime_type`` keeps describing ``audio_url`` (spec #61 — the
            playback manifest classifies the rendition from it).
        """
        podcast_start = time.perf_counter()
        failure: Optional[RefreshFailure] = None
        new_eps: List[Episode] = []
        source: Any = None
        conditional_get_hit = False
        headers_rotated = False
        image_rows: List[Tuple[str, str, Optional[str]]] = []
        audio_rows: List[Tuple[str, str, str, Optional[str]]] = []
        alt_enclosure_rows: List[Tuple[str, str, AlternateEnclosure]] = []
        try:
            rss_url_str = str(podcast.rss_url)
            source = self.media_source_factory.detect_source(rss_url_str)

            parsed_feed: Optional[Any] = None
            rss_content: Optional[str] = None

            if isinstance(source, RSSMediaSource):
                # Parse-once + conditional GET: one fetch, echo stored
                # ETag / Last-Modified, 304 short-circuits parse. Spec #19.
                host = urlparse(rss_url_str).hostname or ""
                fetch_kwargs_rss = {
                    "etag": podcast.etag,
                    "last_modified": podcast.last_modified,
                }
                if self.max_workers > 1 and host:
                    with self._host_semaphore(host):
                        result = source.fetch_and_parse(rss_url_str, podcast.slug, **fetch_kwargs_rss)
                else:
                    result = source.fetch_and_parse(rss_url_str, podcast.slug, **fetch_kwargs_rss)

                if result.not_modified:
                    # Preserve any server-sent header rotation — RFC 7232
                    # allows servers to refresh ETag / Last-Modified on a
                    # 304 and a next-refresh hit depends on us keeping up.
                    # Flag the rotation so the batch writer actually persists
                    # it; otherwise the in-memory update is dropped here.
                    conditional_get_hit = True
                    if result.etag and result.etag != podcast.etag:
                        podcast.etag = result.etag
                        headers_rotated = True
                    if result.last_modified and result.last_modified != podcast.last_modified:
                        podcast.last_modified = result.last_modified
                        headers_rotated = True
                    return RefreshAttemptResult(
                        podcast=podcast,
                        conditional_hit=True,
                        headers_rotated=headers_rotated,
                        image_rows=image_rows,
                        audio_rows=audio_rows,
                        source=source,
                    )

                # Spec #42/#49 — a fetch/parse failure (DNS, HTTP error, empty
                # body) comes back as an error SENTINEL: content/parsed_feed are
                # None and ``error`` is set, with NO exception raised. The old
                # code fell through to ``episodes = []`` and reported success,
                # so a feed outage silently cleared ``last_refresh_error`` and
                # never retried/parked (errors-as-empty-results). Treat it as a
                # hard error so the queued REFRESH_FEED task raises and recovers.
                if result.error or result.content is None:
                    # Spec #60: carry the classified kind out of the fetch
                    # layer instead of a bare boolean. A sentinel without a
                    # kind (defensive) is treated as connectivity — the
                    # keep-trying bias, never a park.
                    failure = RefreshFailure(
                        kind=result.kind or RefreshFailureKind.CONNECTIVITY,
                        http_status=result.status_code or None,
                        retry_after=result.retry_after,
                        exception=result.error or "fetch returned no content",
                    )
                    logger.error(
                        "feed_fetch_failed",
                        podcast_rss_url=rss_url_str,
                        status_code=result.status_code,
                        error=result.error,
                        failure_kind=failure.kind.value,
                    )
                    return RefreshAttemptResult(
                        podcast=podcast,
                        headers_rotated=headers_rotated,
                        image_rows=image_rows,
                        audio_rows=audio_rows,
                        source=source,
                        failure=failure,
                    )

                rss_content = result.content
                parsed_feed = result.parsed_feed

                if result.etag:
                    podcast.etag = result.etag
                if result.last_modified:
                    podcast.last_modified = result.last_modified

                if parsed_feed is not None:
                    metadata = source.extract_metadata(
                        rss_url_str,
                        rss_content=rss_content,
                        parsed_feed=parsed_feed,
                    )
                    if metadata:
                        self._apply_rss_metadata(podcast, metadata)

                    # Re-sync existing episodes' artwork from the feed. Reuses
                    # the already-parsed feed (no extra fetch); the batch
                    # writer's guarded UPDATE only writes rows that drifted, so
                    # this is near-free when nothing changed. Bound to
                    # already-tracked episodes — brand-new ones are inserted with
                    # their current URL below, and emitting the whole catalogue
                    # would fire thousands of no-op UPDATEs against untracked
                    # GUIDs on large feeds.
                    known = known_external_ids or set()
                    if known:
                        feed_images = source.extract_episode_images(parsed_feed)
                        image_rows = [
                            (podcast.id, external_id, url)
                            for external_id, url in feed_images.items()
                            if external_id in known
                        ]
                        # Re-sync existing episodes' enclosure URLs the same
                        # way: hosts like BBC re-publish audio under a new URL
                        # while keeping the GUID, so an episode discovered but
                        # not yet fetched holds a URL that has started 404ing.
                        # The batch writer's guarded UPDATE only touches rows
                        # that drifted AND still need their audio, so this is
                        # near-free when nothing changed.
                        feed_audio_urls = source.extract_episode_audio_urls(parsed_feed)
                        audio_rows = [
                            (podcast.id, external_id, url, mime_type)
                            for external_id, (url, mime_type) in feed_audio_urls.items()
                            if external_id in known
                        ]

                    # Spec #62 — observe <podcast:alternateEnclosure> for the
                    # WHOLE feed window, not just known episodes: brand-new
                    # episodes discovered this refresh must be captured on
                    # first sight (they aren't in ``known`` yet, but their
                    # rows insert in the same batch, so the writer's
                    # INSERT..SELECT resolves them). Untracked GUIDs resolve
                    # to no episode row and no-op — the tag is rare enough
                    # that the extra executemany rows don't matter.
                    if rss_content:
                        alt_by_guid = source.extract_alternate_enclosures(rss_content)
                        alt_enclosure_rows = [
                            (podcast.id, external_id, alt)
                            for external_id, entries in alt_by_guid.items()
                            for alt in entries
                        ]

            # Cap discovery only on a podcast's first-ever refresh — that's the
            # legitimate "don't backfill a 600-episode catalogue when adding a
            # feed" bound. On incremental refreshes the limit is dropped
            # entirely: the per-fetch slice trims everything past the cap and
            # then ``last_processed`` advances past the trimmed episodes, so the
            # gap can never be re-discovered (a silent data-loss hole). A
            # podcast is "first refresh" only when we know of no prior episodes
            # AND have no processing checkpoint.
            is_first_refresh = not known_external_ids and podcast.last_processed is None
            effective_max = max_episodes_per_podcast if is_first_refresh else None

            fetch_kwargs: Dict[str, Any] = {
                "url": rss_url_str,
                "existing_episodes": podcast.episodes,
                "last_processed": podcast.last_processed,
                "max_episodes": effective_max,
            }
            if isinstance(source, RSSMediaSource):
                fetch_kwargs["podcast_slug"] = podcast.slug
                fetch_kwargs["parsed_feed"] = parsed_feed
                # ``known_external_ids`` is an RSS-only fast-path kwarg — the
                # YouTube source's signature doesn't accept it, so it must not
                # leak into the non-RSS call below.
                fetch_kwargs["known_external_ids"] = known_external_ids
                # If fetch_and_parse already failed, skip the episode extraction
                if parsed_feed is None:
                    episodes: List[Episode] = []
                else:
                    episodes = source.fetch_episodes(**fetch_kwargs)
            else:
                episodes = source.fetch_episodes(**fetch_kwargs)

            # Seed podcast.episodes with the newly discovered ones. The
            # refresh loader leaves this list empty (dedup runs via
            # ``known_external_ids`` now), so there's nothing to merge
            # against — appending unconditionally is safe.
            for episode in episodes:
                podcast.episodes.append(episode)

            if episodes:
                new_eps = episodes
                if not is_first_refresh and len(episodes) > INCREMENTAL_BATCH_WARN_THRESHOLD:
                    logger.warning(
                        "Unusually large incremental refresh batch — ingesting all "
                        "(no cap on existing podcasts), but check the feed for "
                        "rotated GUIDs or a re-published back catalogue",
                        podcast_slug=podcast.slug,
                        new_episodes=len(episodes),
                        threshold=INCREMENTAL_BATCH_WARN_THRESHOLD,
                    )
                if podcast.episodes:
                    # Belt-and-suspenders: the ``Episode`` validator already
                    # coerces ``pub_date`` to tz-aware UTC, so this set is
                    # homogeneous in normal flow. ``ensure_utc`` still defends
                    # the ``max`` against any raw-constructed / legacy-loaded
                    # episode that bypassed the model, where a mixed-awareness
                    # ``max`` would raise TypeError.
                    candidates = [ensure_utc(ep.pub_date) for ep in podcast.episodes if ep.pub_date]
                    most_recent_date = max(candidates, default=None)
                    if most_recent_date:
                        podcast.last_processed = most_recent_date

                for episode in episodes:
                    episode.podcast_id = podcast.id

        except Exception as e:
            # Spec #60: classify structurally — a requests-level exception
            # escaping here (e.g. the YouTube source re-raising a network
            # error) is connectivity/transient; anything unrecognized is OUR
            # bug (INTERNAL) and must never condemn the feed.
            failure = classify_fetch_exception(e)
            logger.error(
                "Error checking feed",
                podcast_rss_url=str(podcast.rss_url),
                error=str(e),
                failure_kind=failure.kind.value,
                exc_info=True,
            )
        finally:
            logger.info(
                "feed_refresh_summary",
                podcast_slug=podcast.slug,
                source_type=type(source).__name__ if source is not None else None,
                duration_ms=round((time.perf_counter() - podcast_start) * 1000, 2),
                new_episodes=len(new_eps),
                had_error=failure is not None,
                failure_kind=failure.kind.value if failure else None,
                conditional_get_hit=conditional_get_hit,
            )
        # headers_rotated only governs the 304 path (which returns above); a
        # 200 response routes the podcast through changed_podcasts regardless.
        return RefreshAttemptResult(
            podcast=podcast,
            new_episodes=new_eps,
            conditional_hit=conditional_get_hit,
            headers_rotated=headers_rotated,
            image_rows=image_rows,
            audio_rows=audio_rows,
            alt_enclosure_rows=alt_enclosure_rows,
            source=source,
            failure=failure,
        )

    def _apply_rss_metadata(self, podcast: Podcast, metadata: Dict[str, Any]) -> bool:
        """Apply refreshed RSS metadata to podcast. Returns True if any field changed."""
        changed = False
        if metadata.get("language") and podcast.language != metadata["language"]:
            logger.info(
                "Updating podcast language",
                podcast_slug=podcast.slug,
                old_language=podcast.language,
                new_language=metadata["language"],
            )
            podcast.language = metadata["language"]
            changed = True

        # Transistor and similar CDNs rotate signed URLs, so stored image_url
        # values go stale and 404. Overwrite unconditionally.
        if podcast.image_url != metadata.get("image_url"):
            logger.info(
                "Updating podcast image URL",
                podcast_slug=podcast.slug,
                old_image_url=podcast.image_url,
                new_image_url=metadata.get("image_url"),
            )
            podcast.image_url = metadata.get("image_url")
            changed = True

        for field in ("primary_category", "primary_subcategory", "secondary_category", "secondary_subcategory"):
            if getattr(podcast, field) != metadata.get(field):
                setattr(podcast, field, metadata.get(field))
                changed = True

        # Fill-on-empty for show-level text fields. These are populated at
        # add-time by ``add_podcast``, so a regular subscribed podcast will
        # not see writes here. Auto_added shows (created by import via thin
        # iTunes/yt-dlp metadata) often arrive with a blank description and
        # missing author/website/etc — refresh is their first chance to
        # pick up the canonical RSS values. We only write when the current
        # value is falsy so a user-edited field on a real subscription is
        # never silently overwritten.
        for field in ("description", "author", "show_type", "website_url", "copyright"):
            new_val = metadata.get(field)
            if new_val and not getattr(podcast, field, None):
                setattr(podcast, field, new_val)
                changed = True

        return changed

    def refresh_feeds(
        self,
        max_episodes_per_podcast: Optional[int] = None,
        progress_callback: Optional[Callable[[int, int, str], None]] = None,
        podcast_id: Optional[str] = None,
    ) -> RefreshOutcome:
        """
        Check feeds for new episodes and report batch-level health.

        This is the full entry point; :meth:`get_new_episodes` is a thin
        backward-compatible wrapper that returns only the episode pairs.

        Args:
            max_episodes_per_podcast: Optional limit on episodes to discover per podcast.
                                     If set, only the N most recent episodes will be tracked.
            progress_callback: Optional callback for progress reporting.
                              Called with (current_index, total_count, podcast_title).
            podcast_id: Optional podcast ID or RSS URL to filter. If provided, only
                       this podcast's feed will be checked.

        Returns:
            A :class:`RefreshOutcome` with the (Podcast, List[Episode]) pairs
            that gained episodes plus the batch error / liveness counters.
        """
        new_episodes = []
        batch_start = time.perf_counter()

        # Lightweight refresh load: one query for podcasts + one for the
        # dedup pairs (spec #19 PR 3). The filter path keeps using the
        # fully-hydrated loaders since callers expect the returned
        # podcast to match ``get_by_url`` / ``get_by_id`` shape.
        known_external_ids_by_podcast: Dict[str, set] = {}
        if podcast_id:
            podcast = self.repository.get_by_url(podcast_id)
            if not podcast:
                podcast = self.repository.get_by_id(podcast_id)
            if not podcast:
                logger.warning("Podcast not found for refresh", podcast_id=podcast_id)
                return RefreshOutcome(
                    episodes_by_podcast=[],
                    podcasts_with_errors=0,
                    total_podcasts=0,
                    conditional_get_hits=0,
                )
            podcasts = [podcast]
            known_external_ids_by_podcast[podcast.id] = {ep.external_id for ep in podcast.episodes if ep.external_id}
        else:
            podcasts, known_external_ids_by_podcast = self.repository.get_podcasts_for_refresh()
        total_podcasts = len(podcasts)

        podcasts_with_errors = 0
        conditional_get_hits = 0
        # Accumulators for the end-of-batch write (spec #19). Worker
        # threads mutate podcast/episode models in-memory; the main
        # thread flushes them in a single transaction after the loop.
        changed_podcasts: List[Podcast] = []
        new_episode_rows: List[Episode] = []
        episode_image_updates: List[Tuple[str, str, Optional[str]]] = []
        episode_audio_updates: List[Tuple[str, str, str, Optional[str]]] = []
        episode_alternate_enclosures: List[Tuple[str, str, AlternateEnclosure]] = []
        transcript_link_work: List[Tuple[Podcast, List[Episode], "RSSMediaSource"]] = []

        def _record_outcome(result: RefreshAttemptResult) -> None:
            nonlocal podcasts_with_errors, conditional_get_hits
            podcast = result.podcast
            eps = result.new_episodes
            source = result.source
            if result.failure is not None:
                # FM-2: never certify a checkpoint on a failed refresh. The
                # podcast's etag / last_modified / last_processed were already
                # advanced in-memory before and during fetching; persisting
                # them would make the next refresh receive a 304 and skip a
                # feed we never actually read — turning a one-time error into
                # a permanent, self-hiding stall (the 20VC incident). Leaving
                # the errored podcast out of ``changed_podcasts`` keeps the
                # stored cache headers stale, so the next run re-fetches and
                # retries instead.
                podcasts_with_errors += 1
                return
            if result.conditional_hit:
                conditional_get_hits += 1
                # A plain 304 (nothing changed) skips the batch entirely. But
                # if the server *rotated* its ETag / Last-Modified on the 304,
                # persist the podcast so the next refresh sends the fresh
                # validator — otherwise the rotated header is dropped and we
                # lose the conditional-GET hit on the following run.
                if result.headers_rotated:
                    changed_podcasts.append(podcast)
                return
            changed_podcasts.append(podcast)
            # Re-sync drifted artwork and enclosure URLs for already-tracked
            # episodes (200 path only; a 304 returns above with empty rows).
            # The guarded UPDATEs in the batch writer make unchanged rows a
            # no-op.
            if result.image_rows:
                episode_image_updates.extend(result.image_rows)
            if result.audio_rows:
                episode_audio_updates.extend(result.audio_rows)
            if result.alt_enclosure_rows:
                episode_alternate_enclosures.extend(result.alt_enclosure_rows)
            if eps:
                new_episodes.append((podcast, eps))
                new_episode_rows.extend(eps)
                if isinstance(source, RSSMediaSource):
                    transcript_link_work.append((podcast, eps, source))

        use_pool = self.max_workers > 1 and total_podcasts > 1
        if use_pool:
            # Preserve input ordering in the returned list so callers see a
            # deterministic shape regardless of completion order.
            results: Dict[int, RefreshAttemptResult] = {}
            with ThreadPoolExecutor(
                max_workers=min(self.max_workers, total_podcasts),
                thread_name_prefix="thestill-refresh",
            ) as executor:
                future_to_idx = {
                    executor.submit(
                        self._refresh_single_podcast,
                        podcast,
                        max_episodes_per_podcast,
                        known_external_ids_by_podcast.get(podcast.id, set()),
                    ): idx
                    for idx, podcast in enumerate(podcasts)
                }
                completed = 0
                for future in as_completed(future_to_idx):
                    idx = future_to_idx[future]
                    completed += 1
                    try:
                        results[idx] = future.result()
                    except Exception as e:
                        podcast = podcasts[idx]
                        logger.error(
                            "Refresh worker raised unexpectedly",
                            podcast_rss_url=str(podcast.rss_url),
                            error=str(e),
                            exc_info=True,
                        )
                        # Something escaped _refresh_single_podcast's own
                        # catch-all — classify anyway (spec #60); genuinely
                        # unknown exceptions land as INTERNAL, never a park.
                        results[idx] = RefreshAttemptResult(
                            podcast=podcast,
                            failure=classify_fetch_exception(e),
                        )
                    if progress_callback:
                        progress_callback(completed, total_podcasts, results[idx].podcast.title)

            for idx in range(total_podcasts):
                _record_outcome(results[idx])
        else:
            for idx, podcast in enumerate(podcasts):
                if progress_callback:
                    progress_callback(idx, total_podcasts, podcast.title)
                _record_outcome(
                    self._refresh_single_podcast(
                        podcast,
                        max_episodes_per_podcast,
                        known_external_ids_by_podcast.get(podcast.id, set()),
                    )
                )

        # Single-transaction batch persist (spec #19). Runs even when
        # `new_episode_rows` is empty, because podcasts that saw a 200
        # response still need their refreshed cache headers saved.
        if (
            changed_podcasts
            or new_episode_rows
            or episode_image_updates
            or episode_audio_updates
            or episode_alternate_enclosures
        ):
            with log_phase_timing(
                "persist_batch",
                podcasts=len(changed_podcasts),
                new_episodes=len(new_episode_rows),
                image_updates=len(episode_image_updates),
                audio_url_updates=len(episode_audio_updates),
                alternate_enclosures=len(episode_alternate_enclosures),
            ):
                self.repository.save_refresh_batch(
                    changed_podcasts,
                    new_episode_rows,
                    episode_image_updates,
                    episode_audio_updates,
                    episode_alternate_enclosures,
                )

        # Transcript links rely on the debug RSS file that was written
        # during the fetch; do them after the main batch so a failure
        # here never rolls back the refresh state. Non-critical work.
        for podcast_tl, eps_tl, src_tl in transcript_link_work:
            try:
                self._save_transcript_links_for_episodes(podcast_tl, eps_tl, src_tl)
            except Exception as e:
                logger.warning(
                    "Transcript-link extraction failed; refresh otherwise succeeded",
                    podcast_slug=podcast_tl.slug,
                    error=str(e),
                )

        logger.info(
            "feed_refresh_batch_summary",
            duration_ms=round((time.perf_counter() - batch_start) * 1000, 2),
            total_podcasts=total_podcasts,
            podcasts_with_new_episodes=len(new_episodes),
            total_new_episodes=sum(len(eps) for _, eps in new_episodes),
            podcasts_with_errors=podcasts_with_errors,
            conditional_get_hits=conditional_get_hits,
            max_workers=self.max_workers,
        )
        return RefreshOutcome(
            episodes_by_podcast=new_episodes,
            podcasts_with_errors=podcasts_with_errors,
            total_podcasts=total_podcasts,
            conditional_get_hits=conditional_get_hits,
        )

    def get_new_episodes(
        self,
        max_episodes_per_podcast: Optional[int] = None,
        progress_callback: Optional[Callable[[int, int, str], None]] = None,
        podcast_id: Optional[str] = None,
    ) -> List[Tuple[Podcast, List[Episode]]]:
        """Backward-compatible wrapper over :meth:`refresh_feeds`.

        Returns only the ``(Podcast, List[Episode])`` pairs that gained
        episodes. Callers that need the batch error / liveness counters
        (FM-4) should call :meth:`refresh_feeds` and read
        :attr:`RefreshOutcome.podcasts_with_errors`.
        """
        return self.refresh_feeds(
            max_episodes_per_podcast=max_episodes_per_podcast,
            progress_callback=progress_callback,
            podcast_id=podcast_id,
        ).episodes_by_podcast

    def repair_episode_images(
        self,
        podcast_id: Optional[str] = None,
        dry_run: bool = False,
        progress_callback: Optional[Callable[[int, int, str], None]] = None,
    ) -> ImageRepairOutcome:
        """Re-sync stale episode ``image_url`` values from the live feed.

        ``refresh`` discovers episodes with ``INSERT OR IGNORE`` and never
        revisits an existing row, so episode artwork served behind rotating
        signed URLs (Transistor's imgproxy signatures rotate and the old ones
        start returning 404) is never updated. This compares each tracked
        episode's stored URL against the current feed and updates the ones that
        drifted. Only RSS-backed podcasts are checked — other sources don't use
        these expiring URLs.

        Args:
            podcast_id: Optional podcast UUID or RSS URL to limit the pass.
            dry_run: When True, report what would change without writing.
            progress_callback: Optional ``(index, total, title)`` callback.

        Returns:
            An :class:`ImageRepairOutcome` summarising the pass.
        """
        if podcast_id:
            podcast = self.repository.get_by_url(podcast_id) or self.repository.get_by_id(podcast_id)
            podcasts = [podcast] if podcast else []
            if not podcast:
                logger.warning("Podcast not found for image repair", podcast_id=podcast_id)
        else:
            podcasts = self.repository.get_all()

        total = len(podcasts)
        episodes_updated = 0
        podcasts_with_errors = 0
        updated_by_podcast: Dict[str, int] = {}

        for idx, podcast in enumerate(podcasts):
            if progress_callback:
                progress_callback(idx, total, podcast.title)
            try:
                source = self.media_source_factory.detect_source(str(podcast.rss_url))
                if not isinstance(source, RSSMediaSource):
                    continue

                # Empty dedup filters => the whole feed is returned, each
                # Episode carrying its current image_url.
                feed_episodes = source.fetch_episodes(
                    url=str(podcast.rss_url),
                    existing_episodes=[],
                    last_processed=None,
                    max_episodes=None,
                    podcast_slug=podcast.slug,
                    known_external_ids=None,
                )
                feed_images = {ep.external_id: ep.image_url for ep in feed_episodes if ep.external_id}

                updates: List[Tuple[str, Optional[str]]] = []
                for ep in podcast.episodes:
                    feed_url = feed_images.get(ep.external_id)
                    if feed_url and feed_url != ep.image_url:
                        updates.append((ep.id, feed_url))

                if updates:
                    changed = len(updates) if dry_run else self.repository.update_episode_image_urls(updates)
                    if changed:
                        episodes_updated += changed
                        updated_by_podcast[podcast.slug] = changed
                        logger.info(
                            "episode_images_repaired",
                            podcast_slug=podcast.slug,
                            updated=changed,
                            dry_run=dry_run,
                        )
            except Exception as e:
                podcasts_with_errors += 1
                logger.error(
                    "image_repair_failed",
                    podcast_slug=getattr(podcast, "slug", None),
                    error=str(e),
                    exc_info=True,
                )

        return ImageRepairOutcome(
            podcasts_checked=total,
            episodes_updated=episodes_updated,
            podcasts_with_errors=podcasts_with_errors,
            updated_by_podcast=updated_by_podcast,
        )

    def _save_transcript_links_for_episodes(
        self,
        podcast: Podcast,
        episodes: List[Episode],
        source: RSSMediaSource,
    ) -> None:
        """
        Extract and save transcript links for newly discovered episodes.

        Reads the debug RSS file (saved during fetch_episodes) and extracts
        <podcast:transcript> tags for each episode. Saves links to database
        for later download.

        Args:
            podcast: The podcast these episodes belong to
            episodes: List of newly discovered episodes
            source: The RSSMediaSource that fetched the episodes
        """
        try:
            # Read the debug RSS file (saved during fetch_episodes)
            debug_file = self.path_manager.debug_feed_file(podcast.slug)
            if not debug_file.exists():
                logger.debug("No debug RSS file found, skipping transcript link extraction", podcast_slug=podcast.slug)
                return

            rss_content = debug_file.read_text(encoding="utf-8")

            # Extract transcript links from RSS
            transcript_links_by_guid = source.extract_transcript_links(rss_content)
            if not transcript_links_by_guid:
                return

            # Save transcript links for each new episode
            total_saved = 0
            for episode in episodes:
                links = transcript_links_by_guid.get(episode.external_id, [])
                if links:
                    # Use repository method to save links
                    saved = self.repository.add_transcript_links(episode.id, links)
                    if saved > 0:
                        total_saved += saved
                        logger.debug(
                            "Saved transcript links for episode",
                            count=saved,
                            episode_title=episode.title[:50],
                        )

            if total_saved > 0:
                logger.info(
                    "Saved transcript links for new episodes",
                    total_saved=total_saved,
                    episode_count=len(episodes),
                    podcast_title=podcast.title,
                )

        except Exception as e:
            # Don't fail episode discovery if transcript link extraction fails
            logger.warning(
                "Failed to extract transcript links",
                podcast_title=podcast.title,
                error=str(e),
                exc_info=True,
            )

    def mark_episode_downloaded(
        self,
        podcast_rss_url: str,
        episode_external_id: str,
        audio_path: str,
        duration: Optional[int] = None,
    ) -> None:
        """
        Mark an episode as downloaded with audio file path.

        Args:
            podcast_rss_url: RSS URL of the podcast
            episode_external_id: External ID (from RSS feed) of the episode
            audio_path: Path to the downloaded audio file
            duration: Optional duration in seconds from ffprobe
        """
        if self._in_transaction:
            # Update in-memory cache
            podcast = self._get_or_cache_podcast(podcast_rss_url)
            if podcast:
                for episode in podcast.episodes:
                    if episode.external_id == episode_external_id:
                        episode.audio_path = audio_path
                        if duration is not None:
                            episode.duration = duration
                        logger.info(
                            "Marked episode as downloaded (in transaction)", episode_external_id=episode_external_id
                        )
                        return
                logger.warning("Episode not found for download marking", episode_external_id=episode_external_id)
            else:
                logger.warning("Podcast not found", podcast_rss_url=podcast_rss_url)
        else:
            # Direct repository update
            updates = {"audio_path": audio_path}
            if duration is not None:
                updates["duration"] = duration
            success = self.repository.update_episode(podcast_rss_url, episode_external_id, updates)
            if success:
                logger.info("Marked episode as downloaded", episode_external_id=episode_external_id)
            else:
                logger.warning("Episode not found for download marking", episode_external_id=episode_external_id)

    def mark_episode_downsampled(
        self,
        podcast_rss_url: str,
        episode_external_id: str,
        downsampled_audio_path: str,
        duration: Optional[int] = None,
    ) -> None:
        """
        Mark an episode as downsampled with downsampled audio file path.

        Args:
            podcast_rss_url: RSS URL of the podcast
            episode_external_id: External ID (from RSS feed) of the episode
            downsampled_audio_path: Path to the downsampled audio file
            duration: Optional duration in seconds from ffprobe
        """
        if self._in_transaction:
            # Update in-memory cache
            podcast = self._get_or_cache_podcast(podcast_rss_url)
            if podcast:
                for episode in podcast.episodes:
                    if episode.external_id == episode_external_id:
                        episode.downsampled_audio_path = downsampled_audio_path
                        if duration is not None:
                            episode.duration = duration
                        logger.info(
                            "Marked episode as downsampled (in transaction)", episode_external_id=episode_external_id
                        )
                        return
                logger.warning("Episode not found for downsample marking", episode_external_id=episode_external_id)
            else:
                logger.warning("Podcast not found", podcast_rss_url=podcast_rss_url)
        else:
            # Direct repository update
            updates = {"downsampled_audio_path": downsampled_audio_path}
            if duration is not None:
                updates["duration"] = duration
            success = self.repository.update_episode(podcast_rss_url, episode_external_id, updates)
            if success:
                logger.info("Marked episode as downsampled", episode_external_id=episode_external_id)
            else:
                logger.warning("Episode not found for downsample marking", episode_external_id=episode_external_id)

    def clear_episode_audio_path(
        self,
        podcast_rss_url: str,
        episode_external_id: str,
    ) -> None:
        """
        Clear the audio_path field for an episode after the original audio file has been deleted.

        Args:
            podcast_rss_url: RSS URL of the podcast
            episode_external_id: External ID (from RSS feed) of the episode
        """
        if self._in_transaction:
            # Update in-memory cache
            podcast = self._get_or_cache_podcast(podcast_rss_url)
            if podcast:
                for episode in podcast.episodes:
                    if episode.external_id == episode_external_id:
                        episode.audio_path = None
                        logger.info("Cleared audio_path (in transaction)", episode_external_id=episode_external_id)
                        return
                logger.warning("Episode not found for clearing audio_path", episode_external_id=episode_external_id)
            else:
                logger.warning("Podcast not found", podcast_rss_url=podcast_rss_url)
        else:
            # Direct repository update
            success = self.repository.update_episode(podcast_rss_url, episode_external_id, {"audio_path": None})
            if success:
                logger.info("Cleared audio_path", episode_external_id=episode_external_id)
            else:
                logger.warning("Episode not found for clearing audio_path", episode_external_id=episode_external_id)

    def clear_episode_downsampled_audio_path(
        self,
        podcast_rss_url: str,
        episode_external_id: str,
    ) -> None:
        """
        Clear the downsampled_audio_path field for an episode after the downsampled audio file has been deleted.

        Args:
            podcast_rss_url: RSS URL of the podcast
            episode_external_id: External ID (from RSS feed) of the episode
        """
        if self._in_transaction:
            # Update in-memory cache
            podcast = self._get_or_cache_podcast(podcast_rss_url)
            if podcast:
                for episode in podcast.episodes:
                    if episode.external_id == episode_external_id:
                        episode.downsampled_audio_path = None
                        logger.info(
                            "Cleared downsampled_audio_path (in transaction)", episode_external_id=episode_external_id
                        )
                        return
                logger.warning(
                    "Episode not found for clearing downsampled_audio_path", episode_external_id=episode_external_id
                )
            else:
                logger.warning("Podcast not found", podcast_rss_url=podcast_rss_url)
        else:
            # Direct repository update
            success = self.repository.update_episode(
                podcast_rss_url, episode_external_id, {"downsampled_audio_path": None}
            )
            if success:
                logger.info("Cleared downsampled_audio_path", episode_external_id=episode_external_id)
            else:
                logger.warning(
                    "Episode not found for clearing downsampled_audio_path", episode_external_id=episode_external_id
                )

    def mark_episode_processed(
        self,
        podcast_rss_url: str,
        episode_external_id: str,
        raw_transcript_path: Optional[str] = None,
        clean_transcript_path: Optional[str] = None,
        clean_transcript_json_path: Optional[str] = None,
        summary_path: Optional[str] = None,
    ) -> None:
        """
        Mark an episode as processed.

        Args:
            podcast_rss_url: RSS URL of the podcast
            episode_external_id: External ID (from RSS feed) of the episode
            raw_transcript_path: Optional path to raw transcript file
            clean_transcript_path: Optional path to cleaned transcript file
            clean_transcript_json_path: Optional path to the segmented
                ``AnnotatedTranscript`` JSON sidecar (spec #18 Phase D).
                Populated only when the segmented cleanup pipeline was
                the primary producer; ``None`` for legacy-primary runs.
            summary_path: Optional path to summary file
        """
        if self._in_transaction:
            # Update in-memory cache
            podcast = self._get_or_cache_podcast(podcast_rss_url)
            if podcast:
                episode_found = False
                for episode in podcast.episodes:
                    if episode.external_id == episode_external_id:
                        # Set file paths - state will be auto-computed by model validator
                        # None = don't update, "" = clear the field, "path" = set the field
                        if raw_transcript_path is not None:
                            episode.raw_transcript_path = raw_transcript_path if raw_transcript_path else None
                        if clean_transcript_path is not None:
                            episode.clean_transcript_path = clean_transcript_path if clean_transcript_path else None
                        if clean_transcript_json_path is not None:
                            episode.clean_transcript_json_path = (
                                clean_transcript_json_path if clean_transcript_json_path else None
                            )
                        if summary_path is not None:
                            episode.summary_path = summary_path if summary_path else None
                        # Record processing time only — NEVER the discovery
                        # watermark (``last_processed``), which must stay a real
                        # episode pub_date so refresh keeps finding new episodes.
                        self.repository.touch_last_processed_at(podcast.id, now_utc())
                        logger.info(
                            "Marked episode as processed (in transaction)", episode_external_id=episode_external_id
                        )
                        episode_found = True
                        break

                if not episode_found:
                    logger.warning("Episode not found for processing marking", episode_external_id=episode_external_id)
            else:
                logger.warning("Podcast not found", podcast_rss_url=podcast_rss_url)
        else:
            # Direct repository update (original logic)
            # Build updates dictionary - only file paths, state will be auto-computed
            # None = don't update, "" = clear the field, "path" = set the field
            updates: Dict[str, Any] = {}
            if raw_transcript_path is not None:
                updates["raw_transcript_path"] = raw_transcript_path if raw_transcript_path else None
            if clean_transcript_path is not None:
                updates["clean_transcript_path"] = clean_transcript_path if clean_transcript_path else None
            if clean_transcript_json_path is not None:
                updates["clean_transcript_json_path"] = (
                    clean_transcript_json_path if clean_transcript_json_path else None
                )
            if summary_path is not None:
                updates["summary_path"] = summary_path if summary_path else None

            # Try to update existing episode
            success = self.repository.update_episode(podcast_rss_url, episode_external_id, updates)

            # If episode not found in stored episodes, fetch it from RSS and add it
            if not success:
                try:
                    podcast = self.repository.get_by_url(podcast_rss_url)
                    if not podcast:
                        logger.error("Podcast not found", podcast_rss_url=podcast_rss_url)
                        return

                    # Do NOT let feedparser fetch the URL
                    # directly — it uses urllib internally and bypasses our
                    # SSRF guard. Fetch through the guarded session and hand
                    # feedparser the already-validated body.
                    try:
                        rss_response = guarded_get(str(podcast.rss_url))
                        rss_response.raise_for_status()
                        parsed_feed = feedparser.parse(rss_response.content)
                    except UnsafeURLError as exc:
                        logger.warning(
                            "feedparser_fetch_blocked_unsafe_url",
                            podcast_rss_url=str(podcast.rss_url),
                            error=str(exc),
                        )
                        return
                    for entry in parsed_feed.entries:
                        entry_external_id = entry.get("guid", entry.get("id", ""))
                        if entry_external_id == episode_external_id:
                            episode_date = self._parse_date(entry.get("published_parsed"))
                            audio_url = self._extract_audio_url(entry)
                            if audio_url:
                                # Extract both plain text and HTML descriptions
                                rss_source = RSSMediaSource()
                                description, description_html = rss_source._extract_descriptions(entry)

                                episode = Episode(
                                    title=entry.get("title", "Unknown Episode"),
                                    description=description,
                                    description_html=description_html,
                                    pub_date=episode_date,
                                    audio_url=audio_url,  # type: ignore[arg-type]  # feedparser returns str, Pydantic validates to HttpUrl
                                    duration=parse_duration(entry.get("itunes_duration")),
                                    external_id=entry_external_id,
                                    raw_transcript_path=raw_transcript_path,
                                    clean_transcript_path=clean_transcript_path,
                                    summary_path=summary_path,
                                    podcast_id=podcast.id,
                                )
                                # Use targeted save methods instead of full save()
                                self.repository.save_episode(episode)
                                # Processing-time stamp only; watermark untouched.
                                self.repository.touch_last_processed_at(podcast.id, now_utc())
                                logger.info("Added and marked new episode as processed", episode_title=episode.title)
                                return
                except Exception as e:
                    logger.error(
                        "Error fetching episode info",
                        episode_external_id=episode_external_id,
                        error=str(e),
                        exc_info=True,
                    )
                    return

            # Episode update succeeded — stamp the podcast's processing time.
            podcast = self.repository.get_by_url(podcast_rss_url)
            if podcast:
                # Processing-time stamp only; watermark untouched.
                self.repository.touch_last_processed_at(podcast.id, now_utc())
                logger.info("Marked episode as processed", episode_external_id=episode_external_id)

    def get_downloaded_episodes(self, storage_path: str) -> List[Tuple[Podcast, Episode]]:
        """
        Get all episodes that have downsampled audio but need transcription.

        Returns episodes sorted by publication date (newest first) across all podcasts,
        enabling cross-podcast prioritization when using --max-episodes.

        Args:
            storage_path: Base storage path (unused, kept for compatibility)

        Returns:
            List of (Podcast, Episode) tuples sorted by pub_date descending
        """
        episodes_to_transcribe = []
        podcasts = self.repository.get_all()

        for podcast in podcasts:
            for episode in podcast.episodes:
                # Check if downsampled audio exists (required for transcription)
                if not episode.downsampled_audio_path:
                    continue

                # Check if downsampled audio file actually exists
                if not self.path_manager.downsampled_audio_file(episode.downsampled_audio_path).exists():
                    continue

                # Check if transcript doesn't exist or file is missing
                needs_transcription = False
                if not episode.raw_transcript_path:
                    needs_transcription = True
                else:
                    if not self.path_manager.raw_transcript_file(episode.raw_transcript_path).exists():
                        needs_transcription = True

                if needs_transcription:
                    episodes_to_transcribe.append((podcast, episode))

        # Sort by publication date (newest first) for cross-podcast prioritization
        episodes_to_transcribe.sort(
            key=lambda x: x[1].pub_date or datetime.min.replace(tzinfo=timezone.utc), reverse=True
        )

        return episodes_to_transcribe

    def get_episodes_to_download(self, storage_path: str) -> List[Tuple[Podcast, List[Episode]]]:
        """
        Get all episodes that need audio download (have audio_url but no audio_path).

        Args:
            storage_path: Base storage path (unused, kept for compatibility)

        Returns:
            List of tuples containing (Podcast, List[Episode]) for episodes needing download
        """
        episodes_to_download = []
        podcasts = self.repository.get_all()

        for podcast in podcasts:
            episodes = []
            for episode in podcast.episodes:
                # Check if episode has audio URL
                if not episode.audio_url:
                    continue

                # Check if audio is not yet downloaded or file is missing
                needs_download = False
                if not episode.audio_path:
                    needs_download = True
                else:
                    if not self.path_manager.original_audio_file(episode.audio_path).exists():
                        needs_download = True

                if needs_download:
                    episodes.append(episode)

            if episodes:
                episodes_to_download.append((podcast, episodes))

        return episodes_to_download

    def get_episodes_to_downsample(self, storage_path: str) -> List[Tuple[Podcast, List[Episode]]]:
        """
        Get all episodes that have downloaded audio but need downsampling.

        Args:
            storage_path: Base storage path (unused, kept for compatibility)

        Returns:
            List of tuples containing (Podcast, List[Episode]) for episodes needing downsampling
        """
        episodes_to_downsample = []
        podcasts = self.repository.get_all()

        for podcast in podcasts:
            episodes = []
            for episode in podcast.episodes:
                # Check if original audio is downloaded
                if not episode.audio_path:
                    continue

                # Check if original audio file actually exists
                if not self.path_manager.original_audio_file(episode.audio_path).exists():
                    continue

                # Check if downsampled version doesn't exist or file is missing
                needs_downsampling = False
                if not episode.downsampled_audio_path:
                    needs_downsampling = True
                else:
                    if not self.path_manager.downsampled_audio_file(episode.downsampled_audio_path).exists():
                        needs_downsampling = True

                if needs_downsampling:
                    episodes.append(episode)

            if episodes:
                episodes_to_downsample.append((podcast, episodes))

        return episodes_to_downsample

    def get_episodes_with_raw_transcripts(self, storage_path: str) -> List[Tuple[Podcast, Episode]]:
        """
        Get all episodes that have raw transcripts available for evaluation.

        Returns episodes sorted by publication date (newest first) across all podcasts,
        enabling cross-podcast prioritization when using --max-episodes.

        Args:
            storage_path: Base storage path (unused, kept for compatibility)

        Returns:
            List of (Podcast, Episode) tuples sorted by pub_date descending
        """
        episodes_with_transcripts = []
        podcasts = self.repository.get_all()

        for podcast in podcasts:
            for episode in podcast.episodes:
                # Check if raw transcript path is set
                if not episode.raw_transcript_path:
                    continue

                # Check if raw transcript file actually exists
                if not self.path_manager.raw_transcript_file(episode.raw_transcript_path).exists():
                    continue

                episodes_with_transcripts.append((podcast, episode))

        # Sort by publication date (newest first) for cross-podcast prioritization
        episodes_with_transcripts.sort(
            key=lambda x: x[1].pub_date or datetime.min.replace(tzinfo=timezone.utc), reverse=True
        )

        return episodes_with_transcripts

    def get_episodes_with_clean_transcripts(self, storage_path: str) -> List[Tuple[Podcast, Episode]]:
        """
        Get all episodes that have clean transcripts available for evaluation.

        Returns episodes sorted by publication date (newest first) across all podcasts,
        enabling cross-podcast prioritization when using --max-episodes.

        Args:
            storage_path: Base storage path (unused, kept for compatibility)

        Returns:
            List of (Podcast, Episode) tuples sorted by pub_date descending
        """
        episodes_with_clean = []
        podcasts = self.repository.get_all()

        for podcast in podcasts:
            for episode in podcast.episodes:
                # Check if clean transcript path is set
                if not episode.clean_transcript_path:
                    continue

                # Check if clean transcript file actually exists
                if not self.path_manager.clean_transcript_file(episode.clean_transcript_path).exists():
                    continue

                episodes_with_clean.append((podcast, episode))

        # Sort by publication date (newest first) for cross-podcast prioritization
        episodes_with_clean.sort(key=lambda x: x[1].pub_date or datetime.min.replace(tzinfo=timezone.utc), reverse=True)

        return episodes_with_clean

    def list_podcasts(self) -> List[Podcast]:
        """Return list of all podcasts"""
        return self.repository.get_all()

    def _parse_date(self, date_tuple: Any) -> datetime:
        """Parse a feedparser date tuple to tz-aware UTC (see ``parse_struct_time_utc``)."""
        return parse_struct_time_utc(date_tuple)

    def _extract_audio_url(self, entry: Any) -> Optional[str]:
        """
        Extract audio URL from feed entry.

        Args:
            entry: Feedparser entry object

        Returns:
            Audio URL if found, None otherwise
        """
        for link in entry.get("links", []):
            if link.get("type", "").startswith("audio/"):
                href = link.get("href")
                return str(href) if href else None

        for enclosure in entry.get("enclosures", []):
            if enclosure.get("type", "").startswith("audio/"):
                href = enclosure.get("href")
                return str(href) if href else None

        return None
