"""
Abstract repository interfaces for podcast and episode persistence.

These interfaces define contracts that all concrete implementations must follow,
enabling easy swapping between different storage backends (JSON, SQLite, PostgreSQL, etc.).
"""

from abc import ABC, abstractmethod
from datetime import datetime
from typing import TYPE_CHECKING, Dict, List, Optional, Sequence, Set, Tuple

from ..models.podcast import AlternateEnclosure, Episode, Podcast, TranscriptLink

if TYPE_CHECKING:
    # Pure dataclasses from the core layer, imported type-only to keep the
    # repository layer free of runtime core dependencies.
    from ..core.refresh_failure import RefreshDecision, RefreshFailure, RefreshPolicySettings


class PodcastRepository(ABC):
    """
    Abstract repository for podcast persistence operations.

    Implementations must provide thread-safe access to podcast data.
    """

    @abstractmethod
    def get_all(self) -> List[Podcast]:
        """
        Get all podcasts.

        Returns:
            List of all podcasts, ordered by creation date (newest first)
        """
        pass

    @abstractmethod
    def get_podcasts_for_refresh(self) -> Tuple[List[Podcast], Dict[str, Set[str]]]:
        """
        Lightweight loader for the refresh hot path (spec #19).

        Returns podcasts without hydrating episodes, plus a separately
        queried ``{podcast_id: {external_id, ...}}`` map for in-memory
        dedup. Replaces the N+1 ``get_all()`` on refresh.
        """
        pass

    @abstractmethod
    def get(self, podcast_id: str) -> Optional[Podcast]:
        """
        Get podcast by internal UUID (primary key).

        Args:
            podcast_id: Internal UUID of the podcast

        Returns:
            Podcast if found, None otherwise
        """
        pass

    @abstractmethod
    def get_by_id(self, podcast_id: str) -> Optional[Podcast]:
        """
        Get podcast by internal UUID.

        Args:
            podcast_id: Internal UUID identifier

        Returns:
            Podcast if found, None otherwise
        """
        pass

    @abstractmethod
    def get_by_index(self, index: int) -> Optional[Podcast]:
        """
        Get podcast by 1-based index (for CLI/user convenience).

        Args:
            index: 1-based index (human-friendly ID)

        Returns:
            Podcast if found, None otherwise
        """
        pass

    @abstractmethod
    def get_by_url(self, url: str) -> Optional[Podcast]:
        """
        Get podcast by RSS URL (unique external identifier).

        Args:
            url: RSS feed URL (unique identifier)

        Returns:
            Podcast if found, None otherwise
        """
        pass

    @abstractmethod
    def touch_last_processed_at(self, podcast_id: str, when: datetime) -> None:
        """Record the wall-clock time an episode was last processed.

        Distinct from ``last_processed`` (the incremental-refresh discovery
        watermark). Implementations must write ONLY the processing-time column
        so the watermark is never moved to a wall clock.
        """
        pass

    @abstractmethod
    def get_by_slug(self, slug: str) -> Optional[Podcast]:
        """
        Get podcast by URL-safe slug.

        Args:
            slug: URL-safe slug identifier

        Returns:
            Podcast if found, None otherwise
        """
        pass

    @abstractmethod
    def exists(self, url: str) -> bool:
        """
        Check if podcast with given URL exists.

        Args:
            url: RSS feed URL

        Returns:
            True if podcast exists, False otherwise
        """
        pass

    @abstractmethod
    def save(self, podcast: Podcast) -> Podcast:
        """
        Save or update a podcast.

        If a podcast with the same URL already exists, it will be updated.
        Otherwise, a new podcast will be created.

        Args:
            podcast: Podcast to save or update

        Returns:
            The saved podcast (may include generated fields)
        """
        pass

    @abstractmethod
    def delete(self, url: str) -> bool:
        """
        Delete podcast by URL.

        Args:
            url: RSS feed URL of podcast to delete

        Returns:
            True if podcast was deleted, False if not found
        """
        pass

    @abstractmethod
    def update_episode(self, podcast_url: str, episode_external_id: str, updates: dict) -> bool:
        """
        Update specific episode fields.

        This method allows atomic updates to episode fields without
        requiring a full podcast save operation.

        Args:
            podcast_url: URL of the podcast containing the episode
            episode_external_id: External ID (from RSS feed) of the episode to update
            updates: Dictionary of field names and new values

        Returns:
            True if episode was found and updated, False otherwise

        Example:
            repository.update_episode(
                "https://example.com/feed.xml",
                "episode-123",
                {"audio_path": "/path/to/audio.mp3", "audio_size": 1024000}
            )
        """
        pass

    @abstractmethod
    def save_podcast(self, podcast: Podcast) -> Podcast:
        """
        Save or update podcast metadata only. Does NOT touch episodes.

        Idempotent: Only updates updated_at if data actually changed.

        Args:
            podcast: Podcast model with metadata to save

        Returns:
            The saved podcast (with updated timestamps if changed)
        """
        pass

    @abstractmethod
    def save_episode(self, episode: Episode) -> Episode:
        """
        Save or update a single episode.

        Idempotent: Only updates updated_at if data actually changed.
        Requires: episode.podcast_id must be set.

        Args:
            episode: Episode model to save

        Returns:
            The saved episode

        Raises:
            ValueError: If episode.podcast_id is not set
        """
        pass

    @abstractmethod
    def save_episodes(self, episodes: List[Episode]) -> List[Episode]:
        """
        Save or update multiple episodes in a single transaction.

        Idempotent: Only updates updated_at for episodes with actual changes.
        Requires: Each episode.podcast_id must be set.

        Args:
            episodes: List of Episode models to save

        Returns:
            List of saved episodes

        Raises:
            ValueError: If any episode.podcast_id is not set
        """
        pass

    @abstractmethod
    def save_refresh_batch(
        self,
        changed_podcasts: List[Podcast],
        new_episodes: List[Episode],
        episode_image_updates: Optional[List[Tuple[str, str, Optional[str]]]] = None,
        episode_audio_updates: Optional[List[Tuple[str, str, str, Optional[str]]]] = None,
        episode_alternate_enclosures: Optional[List[Tuple[str, str, "AlternateEnclosure"]]] = None,
    ) -> None:
        """
        Persist a refresh batch in a single transaction (spec #19).

        Updates metadata + conditional-GET cache headers for every
        podcast in ``changed_podcasts`` and bulk-inserts
        ``new_episodes``. Intended to replace the per-podcast
        ``save_podcast`` / ``save_episodes`` calls inside the refresh
        loop so N podcasts pay one transaction instead of 2N.

        Args:
            changed_podcasts: Podcasts whose state changed this refresh.
            new_episodes: Episodes to insert. Must each have
                ``podcast_id`` set.
            episode_image_updates: Optional ``(podcast_id, external_id,
                image_url)`` triples re-syncing existing episodes' artwork from
                the feed (rotating signed URLs go stale because new-episode
                discovery never revisits an existing row). Applied as a guarded
                update so only drifted rows write.
            episode_audio_updates: Optional ``(podcast_id, external_id,
                audio_url, mime_type)`` rows re-syncing existing episodes'
                enclosure URLs from the feed (some hosts re-publish audio under
                a new URL for the same GUID, so the stored URL 404s before the
                episode is fetched). ``audio_mime_type`` is written alongside
                ``audio_url`` so the pair stays consistent — the playback
                manifest classifies the rendition from it (spec #61). Applied
                as a guarded update scoped to episodes whose audio hasn't been
                downloaded or transcribed yet, so a rotating-URL feed can't
                churn already-processed rows.
            episode_alternate_enclosures: Optional ``(podcast_id, external_id,
                AlternateEnclosure)`` rows observed from the feed's
                ``<podcast:alternateEnclosure>`` tags (spec #62), covering both
                new and already-tracked episodes. Inserted in the same
                transaction (``ON CONFLICT DO NOTHING`` keyed by
                ``(episode_id, source_uri)``) so the observation lands together
                with the conditional-GET checkpoint — a post-commit failure
                here would be unrecoverable, since the next refresh 304s and
                never re-parses this feed revision.
        """
        pass

    @abstractmethod
    def add_transcript_links(self, episode_id: str, links: List[TranscriptLink]) -> int:
        """
        Add transcript links for an episode.

        Skips duplicates (same episode_id + url).

        Args:
            episode_id: Episode UUID
            links: List of TranscriptLink objects to add

        Returns:
            Number of links actually inserted (excludes duplicates)
        """
        pass

    @abstractmethod
    def get_alternate_enclosures(self, episode_id: str) -> List[AlternateEnclosure]:
        """
        Get all alternate enclosures for an episode (spec #62), observation order.

        Args:
            episode_id: Episode UUID
        """
        pass

    @abstractmethod
    def get_alternate_enclosures_for_episodes(self, episode_ids: List[str]) -> Dict[str, List[AlternateEnclosure]]:
        """
        Batched alternate-enclosure lookup (spec #62) for list endpoints.

        Every requested id must be a key in the result (empty list when the
        episode has no rows).

        Args:
            episode_ids: Episode UUIDs.
        """
        pass

    # ------------------------------------------------------------------
    # Spec #48/#60 — background refresh scheduling + failure policy.
    # Declared abstract so a backend missing one of these fails LOUDLY at
    # construction instead of shipping silently (the missing Postgres
    # ``clear_podcast_refresh_failures`` reached production exactly because
    # these methods were absent from this interface — spec #60 Phase 0).
    # ------------------------------------------------------------------

    @abstractmethod
    def get_due_podcasts(self, now: Optional[datetime] = None, limit: int = 500) -> List[str]:
        """Ids of feeds due for refresh (scheduled, ``next_refresh_at <= now``,
        past any ``refresh_retry_after_at``), oldest-due first."""
        pass

    @abstractmethod
    def get_quarantine_probe_due(
        self, probe_interval_seconds: int, now: Optional[datetime] = None, limit: int = 200
    ) -> List[str]:
        """Quarantined ``feed_gone``/``invalid_content`` feeds due one
        low-frequency re-probe (never ``auth_required``/``blocked_unsafe``)."""
        pass

    @abstractmethod
    def seed_unscheduled_feeds(self, default_interval_seconds: int, now: Optional[datetime] = None) -> int:
        """Arm never-scheduled active feeds with a jittered first due time.
        Must NOT revive quarantined feeds. Returns the number seeded."""
        pass

    @abstractmethod
    def reschedule_unscheduled_feed(self, podcast_id: str, now: Optional[datetime] = None) -> bool:
        """Re-arm ONE unscheduled feed in response to an explicit user signal
        (a new follow). Unlike ``seed_unscheduled_feeds`` this also revives
        feeds with a stale failure record — a feed that fell out of the
        schedule before it had followers must resume when someone follows it.
        Quarantined feeds (``refresh_disabled_reason`` set) stay parked; they
        have their own probe/operator path. Returns True if rescheduled."""
        pass

    @abstractmethod
    def record_refresh_success(
        self,
        podcast_id: str,
        found_new: bool,
        min_interval: int,
        max_interval: int,
        default_interval: int,
        now: Optional[datetime] = None,
    ) -> str:
        """Record a successful refresh: recompute the AIMD interval and clear
        ALL failure state (error, kind, streak, quarantine reason,
        retry-after). Returns the new ``next_refresh_at`` ISO string."""
        pass

    @abstractmethod
    def record_refresh_failure(
        self,
        podcast_id: str,
        failure: "RefreshFailure",
        settings: "RefreshPolicySettings",
        now: Optional[datetime] = None,
    ) -> "RefreshDecision":
        """Apply the spec #60 failure policy in one atomic state transition
        (IGNORE / BACKOFF / QUARANTINE — see ``core.refresh_failure``)."""
        pass

    @abstractmethod
    def clear_podcast_refresh_failure(
        self, podcast_id: str, default_interval: int, now: Optional[datetime] = None
    ) -> str:
        """Operator re-arm of one parked/quarantined feed: clear all failure
        state, set ``next_refresh_at = now``. Returns the new value."""
        pass

    @abstractmethod
    def clear_podcast_refresh_failures(
        self, podcast_ids: Sequence[str], default_interval: int, now: Optional[datetime] = None
    ) -> int:
        """Bulk variant of :meth:`clear_podcast_refresh_failure`. Returns the
        number of rows updated."""
        pass

    @abstractmethod
    def get_refresh_health_counts(self, now: Optional[datetime] = None) -> Dict[str, object]:
        """Aggregate counts for status surfacing: active / due_now /
        backing_off / parked_total / parked_by_reason."""
        pass

    # ------------------------------------------------------------------
    # Spec #69 Phase 4 — aggregates and light lookups.
    #
    # ``get_all()`` hydrates every episode of every podcast (1+P queries,
    # tens of MB at target scale), which the hot web paths were using for
    # counting, filtering, and slug resolution. The methods below are the
    # purpose-built replacements. The defaults here fall back to
    # ``get_all()`` so any implementation stays correct; the SQLite and
    # Postgres backends override each with a single SQL query. New callers
    # should prefer these over ``get_all()`` — the fallback is the slow
    # path, not the contract.
    # ------------------------------------------------------------------

    def count_podcasts(self) -> int:
        """Number of tracked podcasts."""
        return len(self.get_all())

    def resolve_podcast_slug(self, slug: str) -> Optional[str]:
        """Resolve a podcast slug to its id, without hydrating episodes."""
        podcast = self.get_by_slug(slug)
        return str(podcast.id) if podcast else None

    def count_episode_states(self) -> Dict[str, int]:
        """Aggregate episode pipeline-state counts in one pass.

        Returns a dict with ``podcasts_tracked``, ``episodes_total``, one
        bucket per :class:`EpisodeState` value (keyed by the enum value;
        failed episodes count only under ``failed``, mirroring
        ``Episode.state``), and ``with_summary_path`` — rows with a
        ``summary_path`` regardless of failure state (the historical
        "transcripts_available" stat).
        """
        from ..models.podcast import EpisodeState

        podcasts = self.get_all()
        counts: Dict[str, int] = {s.value: 0 for s in EpisodeState}
        total = 0
        with_summary = 0
        for podcast in podcasts:
            for episode in podcast.episodes:
                total += 1
                counts[episode.state.value] += 1
                if episode.summary_path:
                    with_summary += 1
        return {
            "podcasts_tracked": len(podcasts),
            "episodes_total": total,
            "with_summary_path": with_summary,
            **counts,
        }

    def get_recent_activity_rows(self, limit: int = 20, offset: int = 0) -> Tuple[List[Dict], int]:
        """Episodes ordered by ``updated_at`` DESC, with podcast display
        fields, plus the total episode count.

        Row keys: episode_id, episode_title, episode_slug, podcast_id,
        podcast_title, podcast_slug, state (``Episode.state`` value),
        updated_at, pub_date, duration, episode_image_url,
        podcast_image_url.
        """
        rows: List[Dict] = []
        for podcast in self.get_all():
            for episode in podcast.episodes:
                rows.append(
                    {
                        "episode_id": str(episode.id),
                        "episode_title": episode.title,
                        "episode_slug": episode.slug,
                        "podcast_id": str(podcast.id),
                        "podcast_title": podcast.title,
                        "podcast_slug": podcast.slug,
                        "state": episode.state.value,
                        "updated_at": episode.updated_at,
                        "pub_date": episode.pub_date,
                        "duration": episode.duration,
                        "episode_image_url": episode.image_url,
                        "podcast_image_url": podcast.image_url,
                    }
                )
        rows.sort(key=lambda r: r["updated_at"] or datetime.min, reverse=True)
        return rows[offset : offset + limit], len(rows)

    def list_podcast_rows(
        self,
        *,
        podcast_ids: Optional[Sequence[str]] = None,
        q: Optional[str] = None,
        limit: Optional[int] = None,
        offset: int = 0,
    ) -> Tuple[List[Dict], int]:
        """Light podcast listing — no episode hydration.

        Ordered ``created_at`` DESC (the same order as :meth:`get_all`).
        ``podcast_ids`` filters to that set (``[]`` returns nothing;
        ``None`` means all); ``q`` is a case-insensitive substring match on
        title or author; ``limit``/``offset`` paginate after filtering.
        Returns ``(rows, total)`` where ``total`` counts the filtered set.
        Row keys match :class:`PodcastWithIndex` fields except ``index``,
        with per-podcast ``episodes_count`` / ``episodes_processed``
        (processed = CLEANED or SUMMARIZED).
        """
        wanted = None if podcast_ids is None else {str(pid) for pid in podcast_ids}
        needle = (q or "").strip().lower()
        rows = []
        for podcast in self.get_all():
            if wanted is not None and str(podcast.id) not in wanted:
                continue
            if needle and needle not in podcast.title.lower() and needle not in (podcast.author or "").lower():
                continue
            rows.append(self._podcast_row_from_model(podcast))
        total = len(rows)
        if limit is not None:
            rows = rows[offset : offset + limit]
        elif offset:
            rows = rows[offset:]
        return rows, total

    def get_podcast_row_by_slug(self, slug: str) -> Optional[Dict]:
        """Single-podcast variant of :meth:`list_podcast_rows` — the
        podcast-page metadata (with episode counts) without hydrating the
        back catalog."""
        podcast = self.get_by_slug(slug)
        return self._podcast_row_from_model(podcast) if podcast else None

    def sync_podcast_chart_urls(self, podcast_id: str) -> Dict[str, Optional[str]]:
        """Copy ``apple_url`` / ``youtube_url`` from the ``top_podcasts`` chart
        row with the same ``rss_url`` onto the local podcast (spec #73
        follow-up).

        Called by the lazy-import path (``POST /api/podcasts/resolve``) so a
        podcast imported from the chart carries its store links; safe to call
        for a podcast that is not on any chart (no-op). Chart values only ever
        *fill or replace* — a NULL chart value never clears a stored link.

        Returns the podcast's ``{"apple_url": ..., "youtube_url": ...}`` after
        the sync (both ``None`` when the podcast is unknown or off-chart).
        Backends without a chart table return ``{}``.
        """
        return {}

    @staticmethod
    def _podcast_row_from_model(podcast: Podcast) -> Dict:
        """Shared row shape for the fallback implementations above."""
        from ..models.podcast import EpisodeState

        processed = sum(1 for e in podcast.episodes if e.state in (EpisodeState.CLEANED, EpisodeState.SUMMARIZED))
        return {
            "id": str(podcast.id),
            "title": podcast.title,
            "description": podcast.description,
            "rss_url": str(podcast.rss_url),
            "slug": podcast.slug,
            "image_url": podcast.image_url,
            "language": podcast.language,
            "primary_category": podcast.primary_category,
            "primary_subcategory": podcast.primary_subcategory,
            "secondary_category": podcast.secondary_category,
            "secondary_subcategory": podcast.secondary_subcategory,
            "last_processed": podcast.last_processed,
            "last_processed_at": podcast.last_processed_at,
            "episodes_count": len(podcast.episodes),
            "episodes_processed": processed,
            "author": podcast.author,
            "explicit": podcast.explicit,
            "show_type": podcast.show_type,
            "website_url": podcast.website_url,
            "is_complete": podcast.is_complete,
            "copyright": podcast.copyright,
            "apple_url": podcast.apple_url,
            "youtube_url": podcast.youtube_url,
        }


class EpisodeRepository(ABC):
    """
    Abstract repository for episode-specific queries.

    This interface provides episode-focused operations that may be more
    efficient than loading full podcast objects.
    """

    def get_episodes_by_ids(self, episode_ids: Sequence[str]) -> Dict[str, Tuple[Podcast, Episode]]:
        """Batch variant of :meth:`get_episode` (spec #69 Phase 8).

        Returns ``{episode_id: (podcast, episode)}``; missing ids are
        silently absent so callers can preserve their own ordering and
        detect deletions. Default falls back to one lookup per id; the SQL
        backends override with a single JOIN query.
        """
        out: Dict[str, Tuple[Podcast, Episode]] = {}
        for episode_id in episode_ids:
            pair = self.get_episode(episode_id)
            if pair is not None:
                out[str(episode_id)] = pair
        return out

    def set_episode_summary_preview(self, episode_id: str, preview: "Optional[str]") -> None:
        """Persist the pre-computed summary preview (spec #69 Phase 6.5).

        Best-effort persistence hint written at summarize time (and by the
        episode list's lazy backfill). Deliberately does NOT touch
        ``updated_at`` — backfilling an old episode must not resurface it
        in the activity feed. Default is a no-op so non-SQL test doubles
        stay valid; both SQL backends override.
        """
        return None

    @abstractmethod
    def get_episodes_by_podcast(self, podcast_url: str) -> List[Episode]:
        """
        Get all episodes for a podcast.

        Args:
            podcast_url: RSS feed URL of the podcast

        Returns:
            List of episodes for the podcast, ordered by pub_date (newest first)
        """
        pass

    @abstractmethod
    def get_episode(self, episode_id: str) -> Optional[tuple[Podcast, Episode]]:
        """
        Get episode by internal UUID (primary key).

        Args:
            episode_id: Internal UUID of the episode

        Returns:
            Tuple of (Podcast, Episode) if found, None otherwise
        """
        pass

    @abstractmethod
    def get_episode_by_external_id(self, podcast_url: str, episode_external_id: str) -> Optional[Episode]:
        """
        Get specific episode by external ID (from RSS feed).

        Args:
            podcast_url: RSS feed URL of the podcast
            episode_external_id: External ID of the episode (publisher's GUID)

        Returns:
            Episode if found, None otherwise
        """
        pass

    @abstractmethod
    def get_episode_by_slug(self, podcast_slug: str, episode_slug: str) -> Optional[tuple[Podcast, Episode]]:
        """
        Get episode by podcast slug and episode slug.

        Args:
            podcast_slug: URL-safe slug of the podcast
            episode_slug: URL-safe slug of the episode

        Returns:
            Tuple of (Podcast, Episode) if found, None otherwise
        """
        pass

    @abstractmethod
    def mark_episode_published(self, episode_id: str) -> bool:
        """
        Set ``published_at`` on an episode if it isn't already published.

        The conditional ``WHERE published_at IS NULL`` makes the call
        idempotent: re-running the publish transition is a no-op.

        Returns:
            True if the row transitioned from unpublished to published
            (caller should fan out to follower inboxes); False if the row
            was already published, or if the episode does not exist.
        """
        pass

    @abstractmethod
    def get_unprocessed_episodes(self, state: str) -> List[tuple[Podcast, Episode]]:
        """
        Get episodes in specific processing state.

        This method is used to get episodes that need processing at each
        stage of the pipeline (download, downsample, transcribe, clean).

        Args:
            state: Processing state to filter by. Valid values:
                - 'discovered': Has audio_url but no audio_path
                - 'downloaded': Has audio_path but no downsampled_audio_path
                - 'downsampled': Has downsampled_audio_path but no raw_transcript_path
                - 'transcribed': Has raw_transcript_path but no clean_transcript_path

        Returns:
            List of (Podcast, Episode) tuples matching the state

        Example:
            # Get all episodes ready for download
            episodes_to_download = repository.get_unprocessed_episodes('discovered')
            for podcast, episode in episodes_to_download:
                download_audio(podcast, episode)
        """
        pass

    @abstractmethod
    def get_all_episodes(
        self,
        limit: int = 20,
        offset: int = 0,
        search: Optional[str] = None,
        podcast_id: Optional[str] = None,
        state: Optional[str] = None,
        date_from: Optional[datetime] = None,
        date_to: Optional[datetime] = None,
        updated_from: Optional[datetime] = None,
        sort_by: str = "pub_date",
        sort_order: str = "desc",
    ) -> Tuple[List[Tuple[Podcast, Episode]], int]:
        """
        Get episodes across all podcasts with filtering and pagination.

        Args:
            limit: Maximum number of episodes to return (default 20)
            offset: Number of episodes to skip for pagination (default 0)
            search: Case-insensitive title search (optional)
            podcast_id: Filter by podcast UUID (optional)
            state: Filter by processing state (optional)
            date_from: Only include episodes published on/after this date (optional)
            date_to: Only include episodes published on/before this date (optional)
            updated_from: Only include episodes updated on/after this date (optional)
            sort_by: Sort field - 'pub_date', 'title', or 'updated_at' (default 'pub_date')
            sort_order: Sort direction - 'asc' or 'desc' (default 'desc')

        Returns:
            Tuple of:
                - List of (Podcast, Episode) tuples matching criteria
                - Total count of matching episodes (for pagination)

        Example:
            # Get first page of transcribed episodes
            episodes, total = repository.get_all_episodes(
                limit=20, offset=0, state='transcribed'
            )
        """
        pass
