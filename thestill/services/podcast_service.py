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
Podcast service - Business logic for podcast and episode management
"""

import re
import threading
from datetime import datetime, timedelta, timezone
from pathlib import Path, PurePosixPath
from typing import TYPE_CHECKING, List, Literal, NamedTuple, Optional, Union

from pydantic import BaseModel, computed_field
from structlog import get_logger

from ..core.feed_manager import PodcastFeedManager
from ..core.summary_artifacts import (
    load_valid_summary_manifest,
    load_valid_translation_metadata,
    write_summary_manifest,
    write_translation_metadata,
)
from ..core.summary_citations import load_valid_citations_for_api
from ..models.annotated_transcript import AnnotatedTranscript, WordSpan
from ..models.podcast import Episode, Podcast
from ..models.transcript import Segment as RawSegment
from ..models.transcript import Word
from ..repositories.podcast_repository import PodcastRepository
from ..utils.duration import format_duration
from ..utils.file_storage import FileStorage
from ..utils.language_config import normalize_language_code
from ..utils.path_manager import PathManager

logger = get_logger(__name__)

if TYPE_CHECKING:
    from ..core.llm_provider import LLMProvider

# Type alias for transcript type
TranscriptType = Literal["cleaned", "raw"]


def extract_summary_preview(content: str, max_length: int = 200) -> Optional[str]:
    """Extract a preview from the numbered first section of a summary.

    Takes the markdown content as a string — callers own the read. Spec #35
    pushed file I/O up to ``FileStorage``; centralising the extraction here
    keeps the regex + truncation logic in one place without recoupling it
    to a specific storage backend.

    Args:
        content: The full summary-markdown text.
        max_length: Maximum length of the preview.

    Returns:
        Preview text or None if no gist section is present / extraction fails.
    """
    try:
        import re

        # Spec #58 localises section headings. Key off the stable section
        # number/structure rather than the English words "The Gist".
        gist_match = re.search(
            r"^##\s*1\.?\s*(?:🎙️\s*)?[^\n]*\n+([\s\S]*?)(?=^##|^---|\Z)",
            content,
            re.IGNORECASE | re.MULTILINE,
        )
        if gist_match:
            gist_content = gist_match.group(1).strip()
            lines = [line.strip() for line in gist_content.split("\n") if line.strip()]
            if len(lines) > 1:
                summary_text = " ".join(lines[1:])
            elif lines:
                summary_text = lines[0]
            else:
                return None

            # Strip markdown formatting
            summary_text = re.sub(r"\*\*([^*]+)\*\*", r"\1", summary_text)  # **bold**
            summary_text = re.sub(r"\*([^*]+)\*", r"\1", summary_text)  # *italic*
            summary_text = re.sub(r"\[([^\]]+)\]\([^)]+\)", r"\1", summary_text)  # [link](url)

            if len(summary_text) > max_length:
                summary_text = summary_text[: max_length - 3].rsplit(" ", 1)[0] + "..."

            return summary_text

        return None
    except Exception:
        return None


class TranscriptResult(NamedTuple):
    """Result from get_transcript with type information"""

    content: str
    transcript_type: Optional[TranscriptType]  # None if N/A message


class SegmentedTranscriptResult(NamedTuple):
    """Loaded ``AnnotatedTranscript`` JSON sidecar (spec #18 Phase D).

    The service layer reads the sidecar from disk and overwrites its
    cached ``playback_time_offset_seconds`` with the DB-authoritative
    value before returning, so API responses are always consistent
    regardless of when the JSON was last written.
    """

    annotated: AnnotatedTranscript


class SegmentWordsResult(NamedTuple):
    """One ``AnnotatedSegment`` worth of raw word-level timestamps.

    ``segment_id`` matches ``AnnotatedSegment.id`` so the client can join
    against the segmented transcript it already holds. ``words`` carries
    the raw ``Word`` objects (with ``start``/``end`` in raw audio seconds);
    the client adds the response's ``playback_time_offset_seconds`` before
    comparing to ``audio.currentTime``.
    """

    segment_id: int
    words: List[Word]


class TranscriptWordsResult(NamedTuple):
    """Per-segment word-level timestamps for the karaoke wipe (spec #38).

    Segments with no resolvable word data (no ``source_word_span``, or a
    span pointing at raw words that have no timestamps) are omitted, not
    empty-array'd, so the client can cheaply fall back to segment-level
    highlighting on a per-segment basis.
    """

    playback_time_offset_seconds: float
    segments: List[SegmentWordsResult]


def _collect_words_in_span(span: WordSpan, raw_by_id: dict[int, RawSegment]) -> List[Word]:
    """Walk a ``WordSpan`` and collect the raw ``Word`` objects it points to.

    A span is inclusive on both endpoints and may cross multiple raw
    segments. Words without ``start``/``end`` timestamps are skipped — they
    can't drive a karaoke wipe even though they're part of the span.

    Mismatched indices (segment id not present, word index out of range)
    raise ``KeyError`` / ``IndexError`` — corrupted data should fail loudly
    rather than silently produce a malformed response.
    """
    words: List[Word] = []
    for seg_id in range(span.start_segment_id, span.end_segment_id + 1):
        raw_seg = raw_by_id[seg_id]
        start_idx = span.start_word_index if seg_id == span.start_segment_id else 0
        end_idx = span.end_word_index if seg_id == span.end_segment_id else len(raw_seg.words) - 1
        for i in range(start_idx, end_idx + 1):
            w = raw_seg.words[i]
            if w.start is not None and w.end is not None:
                words.append(w)
    return words


class PodcastWithIndex(BaseModel):
    """Podcast with human-friendly index number"""

    id: str  # Internal UUID for direct access
    index: int
    title: str
    description: str
    rss_url: str
    slug: str
    image_url: Optional[str] = None
    language: str = "en"  # ISO 639-1 language code
    # Category fields (Apple Podcasts taxonomy)
    primary_category: Optional[str] = None
    primary_subcategory: Optional[str] = None
    secondary_category: Optional[str] = None
    secondary_subcategory: Optional[str] = None
    last_processed: Optional[datetime] = None  # discovery watermark (newest episode pub_date)
    last_processed_at: Optional[datetime] = None  # wall-clock time an episode was last processed
    episodes_count: int = 0
    episodes_processed: int = 0
    # THES-146: New metadata fields
    author: Optional[str] = None
    explicit: Optional[bool] = None
    show_type: Optional[str] = None
    website_url: Optional[str] = None
    is_complete: bool = False
    copyright: Optional[str] = None

    @computed_field  # type: ignore[misc]
    @property
    def description_text(self) -> str:
        """Plain text version of description (HTML stripped)"""
        from thestill.utils.html_utils import html_to_plain_text

        return html_to_plain_text(self.description)


class EpisodeWithIndex(BaseModel):
    """Episode with human-friendly index numbers"""

    id: str  # Internal UUID for direct access
    podcast_index: int
    podcast_slug: str
    episode_index: int
    title: str
    slug: str
    description: str
    pub_date: Optional[datetime] = None
    audio_url: str
    duration: Optional[int] = None  # Duration in seconds
    external_id: str  # External ID from RSS feed (publisher's GUID)
    state: str  # Processing state (discovered, downloaded, downsampled, transcribed, cleaned)
    transcript_available: bool = False
    summary_available: bool = False
    image_url: Optional[str] = None  # Episode-specific artwork
    summary_preview: Optional[str] = None  # Preview text from summary (The Gist section)
    # THES-146: New metadata fields
    explicit: Optional[bool] = None
    episode_type: Optional[str] = None
    episode_number: Optional[int] = None
    season_number: Optional[int] = None

    @computed_field  # type: ignore[misc]
    @property
    def duration_formatted(self) -> Optional[str]:
        """Human-readable duration (e.g., '1:08:01' or '45:30')"""
        if self.duration is None:
            return None
        return format_duration(self.duration)


class PodcastService:
    """
    Service for podcast and episode management with flexible ID resolution.

    Podcast ID formats supported:
    - Integer index (1, 2, 3...) - 1-based indexing
    - RSS URL string

    Episode ID formats supported:
    - Integer index (1, 2, 3...) - 1=latest, 2=second latest, etc.
    - "latest" keyword - most recent episode
    - Date string (YYYY-MM-DD) - match by publish date
    - GUID string - exact match

    Attributes:
        storage_path: Path to data storage directory
        path_manager: Path manager for file operations
        repository: Repository for podcast persistence
        feed_manager: Feed manager for RSS operations
    """

    def __init__(
        self,
        storage_path: Union[str, Path],
        podcast_repository: PodcastRepository,
        path_manager: PathManager,
        file_storage: FileStorage,
    ) -> None:
        """
        Initialize podcast service.

        Args:
            storage_path: Path to data storage directory (str or Path).
            podcast_repository: Repository for podcast persistence.
            path_manager: Path manager for file path operations.
            file_storage: Spec #35 backend for transcript / summary reads.
        """
        self.storage_path: Path = Path(storage_path) if isinstance(storage_path, str) else storage_path
        self.path_manager: PathManager = path_manager
        self.repository: PodcastRepository = podcast_repository
        self.file_storage: FileStorage = file_storage
        self._summary_translation_locks: dict[str, threading.Lock] = {}
        self._summary_translation_locks_guard = threading.Lock()

        self.feed_manager: PodcastFeedManager = PodcastFeedManager(
            podcast_repository=podcast_repository, path_manager=path_manager
        )

        logger.info("PodcastService initialized", storage=str(self.storage_path))

    def _read_relative(self, absolute_path: Path) -> str:
        """Spec #35 read helper — collapses the two-call ``read_text(to_relative(p))``
        pattern into one. Used by every read-side method below."""
        return self.file_storage.read_text(self.path_manager.to_relative(absolute_path))

    def add_podcast(self, url: str) -> Optional[Podcast]:
        """
        Add a new podcast to tracking, or return existing if already tracked.

        This method is idempotent - calling it multiple times with the same URL
        will return the same podcast without error.

        Args:
            url: RSS URL, Apple Podcast URL, or YouTube channel/playlist URL

        Returns:
            Podcast object if successful or already exists, None if failed
        """
        logger.info(f"Adding podcast: {url}")
        added_podcast = self.feed_manager.add_podcast(url)

        if added_podcast:
            logger.info(f"Successfully added podcast: {added_podcast.title}")
            return added_podcast

        # Podcast may already exist - return existing one (idempotent behavior)
        # Note: URL might have been resolved (e.g., Apple Podcasts -> RSS), so check both
        existing = self.repository.get_by_url(url)
        if existing:
            logger.info(f"Podcast already exists: {existing.title}")
            return existing

        logger.warning(f"Failed to add podcast: {url}")
        return None

    def remove_podcast(self, podcast_id: Union[str, int]) -> bool:
        """
        Remove a podcast from tracking.

        Args:
            podcast_id: Podcast index (int) or RSS URL (str)

        Returns:
            True if removed, False if not found
        """
        # Resolve podcast ID to RSS URL
        podcast = self.get_podcast(podcast_id)
        if not podcast:
            logger.warning(f"Podcast not found: {podcast_id}")
            return False

        rss_url = str(podcast.rss_url)
        logger.info(f"Removing podcast: {podcast.title}")
        return self.feed_manager.remove_podcast(rss_url)

    def get_podcasts(self) -> List[PodcastWithIndex]:
        """
        Get all tracked podcasts with index numbers.

        Returns:
            List of podcasts with human-friendly indices
        """
        podcasts = self.feed_manager.list_podcasts()
        logger.debug(f"Listing {len(podcasts)} podcasts")

        from ..models.podcast import EpisodeState

        result = []
        for idx, podcast in enumerate(podcasts, start=1):
            # Count episodes that have completed the cleaning pipeline (CLEANED or SUMMARIZED)
            episodes_processed = sum(
                1 for ep in podcast.episodes if ep.state in (EpisodeState.CLEANED, EpisodeState.SUMMARIZED)
            )
            result.append(
                PodcastWithIndex(
                    id=podcast.id,
                    index=idx,
                    title=podcast.title,
                    description=podcast.description,
                    rss_url=str(podcast.rss_url),
                    slug=podcast.slug,
                    image_url=podcast.image_url,
                    language=podcast.language,
                    primary_category=podcast.primary_category,
                    primary_subcategory=podcast.primary_subcategory,
                    secondary_category=podcast.secondary_category,
                    secondary_subcategory=podcast.secondary_subcategory,
                    last_processed=podcast.last_processed,
                    last_processed_at=podcast.last_processed_at,
                    episodes_count=len(podcast.episodes),
                    episodes_processed=episodes_processed,
                    # THES-146: New metadata fields
                    author=podcast.author,
                    explicit=podcast.explicit,
                    show_type=podcast.show_type,
                    website_url=podcast.website_url,
                    is_complete=podcast.is_complete,
                    copyright=podcast.copyright,
                )
            )

        return result

    def get_podcast(self, podcast_id: Union[str, int]) -> Optional[Podcast]:
        """
        Get a podcast by ID.

        Args:
            podcast_id: Integer index (1-based), slug, RSS URL string, or UUID string

        Returns:
            Podcast object or None if not found
        """
        podcasts = self.feed_manager.list_podcasts()

        # If integer, treat as index (1-based)
        if isinstance(podcast_id, int):
            if 1 <= podcast_id <= len(podcasts):
                logger.debug(f"Retrieved podcast by index: {podcast_id}")
                return podcasts[podcast_id - 1]
            logger.warning(f"Podcast index out of range: {podcast_id}")
            return None

        # If string that looks like a number, convert to int
        if isinstance(podcast_id, str) and podcast_id.isdigit():
            return self.get_podcast(int(podcast_id))

        # Check if it's a UUID (internal ID)
        if isinstance(podcast_id, str) and len(podcast_id) == 36 and podcast_id.count("-") == 4:
            for podcast in podcasts:
                if podcast.id == podcast_id:
                    logger.debug(f"Retrieved podcast by UUID: {podcast.title}")
                    return podcast

        # Check if it's a slug (URL-safe identifier)
        if isinstance(podcast_id, str):
            for podcast in podcasts:
                if podcast.slug == podcast_id:
                    logger.debug(f"Retrieved podcast by slug: {podcast.title}")
                    return podcast

        # Otherwise, treat as RSS URL
        for podcast in podcasts:
            if str(podcast.rss_url) == podcast_id:
                logger.debug(f"Retrieved podcast by URL: {podcast.title}")
                return podcast

        logger.warning(f"Podcast not found: {podcast_id}")
        return None

    def get_episode(self, podcast_id: Union[str, int], episode_id: Union[str, int]) -> Optional[Episode]:
        """
        Get an episode by podcast ID and episode ID.

        Args:
            podcast_id: Podcast index, RSS URL, or UUID
            episode_id: Episode index (1=latest), 'latest', date (YYYY-MM-DD), UUID, or external ID

        Returns:
            Episode object or None if not found
        """
        # First, get the podcast
        podcast = self.get_podcast(podcast_id)
        if not podcast:
            logger.warning(f"Podcast not found for episode lookup: {podcast_id}")
            return None

        if not podcast.episodes:
            logger.warning(f"No episodes found for podcast: {podcast.title}")
            return None

        # Sort episodes by pub_date descending (latest first)
        sorted_episodes = sorted(
            podcast.episodes,
            key=lambda ep: ep.pub_date or datetime.min.replace(tzinfo=timezone.utc),
            reverse=True,
        )

        # Handle "latest" keyword
        if episode_id == "latest":
            logger.debug(f"Retrieved latest episode from: {podcast.title}")
            return sorted_episodes[0]

        # Handle integer index (1=latest, 2=second latest, etc.)
        if isinstance(episode_id, int):
            if 1 <= episode_id <= len(sorted_episodes):
                logger.debug(f"Retrieved episode by index {episode_id} from: {podcast.title}")
                return sorted_episodes[episode_id - 1]
            logger.warning(f"Episode index out of range: {episode_id}")
            return None

        # If string that looks like a number, convert to int
        if isinstance(episode_id, str) and episode_id.isdigit():
            return self.get_episode(podcast_id, int(episode_id))

        # Handle date format (YYYY-MM-DD)
        if isinstance(episode_id, str) and len(episode_id) == 10 and episode_id.count("-") == 2:
            try:
                target_date = datetime.fromisoformat(episode_id).date()
                for episode in sorted_episodes:
                    if episode.pub_date and episode.pub_date.date() == target_date:
                        logger.debug(f"Retrieved episode by date {episode_id}: {episode.title}")
                        return episode
                logger.warning(f"No episode found for date: {episode_id}")
                return None
            except ValueError:
                pass  # Not a valid date, continue to UUID/GUID matching

        # Check if it's a UUID (internal ID)
        if isinstance(episode_id, str) and len(episode_id) == 36 and episode_id.count("-") == 4:
            for episode in podcast.episodes:
                if episode.id == episode_id:
                    logger.debug(f"Retrieved episode by UUID: {episode.title}")
                    return episode

        # Otherwise, treat as external ID (GUID from RSS feed)
        for episode in podcast.episodes:
            if episode.external_id == episode_id:
                logger.debug(f"Retrieved episode by external ID: {episode.title}")
                return episode

        logger.warning(f"Episode not found: {episode_id}")
        return None

    def get_episodes(
        self,
        podcast_id: Union[str, int],
        limit: int = 100,
        offset: int = 0,
        since_hours: Optional[int] = None,
    ) -> Optional[List[EpisodeWithIndex]]:
        """
        Get episodes for a podcast with optional filtering and pagination.

        Args:
            podcast_id: Podcast index or RSS URL
            limit: Maximum number of episodes to return (default 100)
            offset: Number of episodes to skip (default 0)
            since_hours: Only include episodes published in last N hours

        Returns:
            List of episodes with indices, or None if podcast not found
        """
        # Get the podcast
        podcast = self.get_podcast(podcast_id)
        if not podcast:
            logger.warning(f"Podcast not found for episode listing: {podcast_id}")
            return None

        # Get podcast index for response
        podcasts = self.get_podcasts()
        podcast_index = next((p.index for p in podcasts if str(p.rss_url) == str(podcast.rss_url)), 0)

        # Sort episodes by pub_date descending (latest first)
        sorted_episodes = sorted(
            podcast.episodes,
            key=lambda ep: ep.pub_date or datetime.min.replace(tzinfo=timezone.utc),
            reverse=True,
        )

        # Filter by date if since_hours specified
        if since_hours is not None:
            cutoff_time = datetime.now(timezone.utc) - timedelta(hours=since_hours)
            sorted_episodes = [ep for ep in sorted_episodes if ep.pub_date and ep.pub_date >= cutoff_time]
            logger.debug(f"Filtered to {len(sorted_episodes)} episodes from last {since_hours}h")

        # Apply offset and limit
        sorted_episodes = sorted_episodes[offset : offset + limit]

        # Build result with indices (account for offset in indexing)
        result = []
        for idx, episode in enumerate(sorted_episodes, start=offset + 1):
            # Extract summary preview if summary exists. Spec #35 — read via
            # FileStorage and use FileNotFoundError to detect absence in one
            # call instead of exists()+read (two S3 round-trips).
            summary_preview = None
            if episode.summary_path:
                try:
                    summary_preview = extract_summary_preview(
                        self._read_relative(self.path_manager.summary_file(episode.summary_path))
                    )
                except FileNotFoundError:
                    pass

            result.append(
                EpisodeWithIndex(
                    id=episode.id,
                    podcast_index=podcast_index,
                    podcast_slug=podcast.slug,
                    episode_index=idx,
                    title=episode.title,
                    slug=episode.slug,
                    description=episode.description,
                    pub_date=episode.pub_date,
                    audio_url=str(episode.audio_url),
                    duration=episode.duration,
                    external_id=episode.external_id,
                    state=episode.state.value,
                    # Spec #35 — exists() probes also route through
                    # FileStorage. Two HeadObject calls per episode on S3
                    # is the price; the list-episodes path is a UI hot
                    # path so we accept it for now (a future optimisation
                    # could batch via list_files with a prefix).
                    transcript_available=bool(
                        episode.clean_transcript_path
                        and self.file_storage.exists(
                            self.path_manager.to_relative(
                                self.path_manager.clean_transcript_file(episode.clean_transcript_path)
                            )
                        )
                    ),
                    summary_available=bool(
                        episode.summary_path
                        and self.file_storage.exists(
                            self.path_manager.to_relative(self.path_manager.summary_file(episode.summary_path))
                        )
                    ),
                    image_url=episode.image_url,
                    summary_preview=summary_preview,
                    # THES-146: New metadata fields
                    explicit=episode.explicit,
                    episode_type=episode.episode_type,
                    episode_number=episode.episode_number,
                    season_number=episode.season_number,
                )
            )

        logger.debug(f"Listed {len(result)} episodes from: {podcast.title}")
        return result

    def get_episodes_count(self, podcast_id: Union[str, int], since_hours: Optional[int] = None) -> Optional[int]:
        """
        Get total count of episodes for a podcast.

        Args:
            podcast_id: Podcast index or RSS URL
            since_hours: Only count episodes published in last N hours

        Returns:
            Total episode count, or None if podcast not found
        """
        podcast = self.get_podcast(podcast_id)
        if not podcast:
            return None

        if since_hours is not None:
            cutoff_time = datetime.now(timezone.utc) - timedelta(hours=since_hours)
            return sum(1 for ep in podcast.episodes if ep.pub_date and ep.pub_date >= cutoff_time)

        return len(podcast.episodes)

    def get_transcript(self, podcast_id: Union[str, int], episode_id: Union[str, int]) -> Optional[TranscriptResult]:
        """
        Get the transcript for an episode, preferring cleaned over raw.

        Args:
            podcast_id: Podcast index or RSS URL
            episode_id: Episode index, 'latest', date, or GUID

        Returns:
            TranscriptResult with content and type, or None if episode not found
        """
        episode = self.get_episode(podcast_id, episode_id)
        if not episode:
            logger.warning(f"Episode not found for transcript: {podcast_id}/{episode_id}")
            return None
        return self.get_transcript_for_episode(episode)

    def get_transcript_for_episode(self, episode: Episode) -> TranscriptResult:
        """Get the transcript for an already-resolved episode.

        Preferred entry point for callers that already hold an
        ``Episode`` (the web route is one) — skips the podcast/episode
        re-lookup the id-based variant pays.
        """
        # Try cleaned transcript first (preferred). Spec #35 — route reads
        # through FileStorage; the require_file_exists guard becomes a
        # try/FileNotFoundError so we don't pay two S3 round-trips per call.
        if episode.clean_transcript_path:
            md_path = self.path_manager.clean_transcript_file(episode.clean_transcript_path)
            try:
                content = self._read_relative(md_path)
                logger.info(f"Retrieved cleaned transcript for: {episode.title}")
                return TranscriptResult(content=content, transcript_type="cleaned")
            except FileNotFoundError:
                logger.warning(f"Cleaned transcript file not found: {md_path}")
            except Exception as e:
                logger.error(f"Error reading cleaned transcript file: {e}")

        # Fall back to raw transcript if available
        if episode.raw_transcript_path:
            json_path = self.path_manager.raw_transcript_file(episode.raw_transcript_path)
            try:
                import json

                from ..models.transcript import Transcript

                transcript_data = json.loads(self._read_relative(json_path))
                # Render raw JSON via the spec #18 ``AnnotatedTranscript`` path
                # rather than the legacy ``TranscriptFormatter``. The former
                # is byte-identical to the latter for ``from_raw`` input
                # (verified across 20 real transcripts), so the summariser
                # and the frontend's regex parser keep working unchanged.
                transcript = Transcript.model_validate(transcript_data)
                annotated = AnnotatedTranscript.from_raw(transcript, episode_id=str(episode.id))
                annotated.playback_time_offset_seconds = episode.playback_time_offset_seconds
                content = annotated.to_blended_markdown()
                logger.info(f"Retrieved raw transcript for: {episode.title}")
                return TranscriptResult(content=content, transcript_type="raw")
            except FileNotFoundError:
                logger.warning(f"Raw transcript file not found: {json_path}")
            except Exception as e:
                logger.error(f"Error reading raw transcript file: {e}")

        # No transcript available
        logger.info(f"No transcript available for: {episode.title}")
        return TranscriptResult(content="N/A - No transcript available", transcript_type=None)

    def get_segmented_transcript(
        self, podcast_id: Union[str, int], episode_id: Union[str, int]
    ) -> Optional[SegmentedTranscriptResult]:
        """Load the ``AnnotatedTranscript`` JSON sidecar for an episode (spec #18).

        Convenience wrapper around
        :meth:`get_segmented_transcript_for_episode` that resolves the
        episode from its ids first. Prefer calling the ``_for_episode``
        variant when the caller already has an ``Episode`` in scope —
        it avoids a redundant podcast/episode lookup (and on the web
        route, three such lookups per request).
        """
        episode = self.get_episode(podcast_id, episode_id)
        if episode is None:
            logger.warning(f"Episode not found for segmented transcript: {podcast_id}/{episode_id}")
            return None
        return self.get_segmented_transcript_for_episode(episode)

    def get_segmented_transcript_for_episode(self, episode: Episode) -> Optional[SegmentedTranscriptResult]:
        """Load the ``AnnotatedTranscript`` JSON sidecar for an already-resolved episode.

        Returns ``None`` when the episode row carries no
        ``clean_transcript_json_path`` (the segmented cleanup never ran,
        or the file has since been deleted). Before returning, the
        ``playback_time_offset_seconds`` cached in the JSON is
        overwritten with the DB value — the DB is the source of truth
        and the sidecar is a write-through cache.
        """
        if not episode.clean_transcript_json_path:
            return None

        json_path = self.path_manager.clean_transcript_file(episode.clean_transcript_json_path)
        try:
            annotated = AnnotatedTranscript.model_validate_json(self._read_relative(json_path))
        except FileNotFoundError:
            logger.warning(f"Segmented transcript JSON missing on disk: {json_path}")
            return None
        except Exception as error:  # pylint: disable=broad-except
            logger.error(f"Error reading segmented transcript JSON: {error}")
            return None

        annotated.playback_time_offset_seconds = episode.playback_time_offset_seconds
        return SegmentedTranscriptResult(annotated=annotated)

    def get_transcript_words_for_episode(self, episode: Episode) -> Optional[TranscriptWordsResult]:
        """Load per-segment word-level timestamps for the karaoke wipe (spec #38).

        Reads two sidecars: the segmented JSON (for ``AnnotatedSegment.id`` →
        ``source_word_span`` mapping) and the raw transcript JSON (for the
        actual ``Word`` objects with their start/end seconds).

        Returns ``None`` — which the route translates to 404 — when:
          - the segmented JSON sidecar is missing (episode wasn't cleaned
            through the spec #18 path);
          - the raw transcript file is missing;
          - no segment has resolvable word data (Whisper-CPU mode or other
            providers that didn't surface word timestamps).

        A ``source_word_span`` that references a non-existent raw segment or
        an out-of-bounds word index is treated as data corruption: the
        resulting ``KeyError`` / ``IndexError`` propagates to the route's
        generic 500 handler.
        """
        if not episode.clean_transcript_json_path or not episode.raw_transcript_path:
            return None

        annotated_path = self.path_manager.clean_transcript_file(episode.clean_transcript_json_path)
        raw_path = self.path_manager.raw_transcript_file(episode.raw_transcript_path)

        # Spec #35 — collapse the prior exists+read into a single read,
        # treating FileNotFoundError on either file as the same "missing"
        # signal. The structured log line keeps the same shape so any
        # alerting rules on it continue to work.
        from ..models.transcript import Transcript

        try:
            annotated_payload = self._read_relative(annotated_path)
        except FileNotFoundError:
            logger.warning(
                "transcript_words.file_missing",
                episode_id=episode.id,
                annotated_present=False,
                raw_present=None,
            )
            return None
        try:
            raw_payload = self._read_relative(raw_path)
        except FileNotFoundError:
            logger.warning(
                "transcript_words.file_missing",
                episode_id=episode.id,
                annotated_present=True,
                raw_present=False,
            )
            return None

        annotated = AnnotatedTranscript.model_validate_json(annotated_payload)
        raw = Transcript.model_validate_json(raw_payload)

        raw_by_id: dict[int, RawSegment] = {seg.id: seg for seg in raw.segments}

        segments_out: List[SegmentWordsResult] = []
        for ann_seg in annotated.segments:
            span = ann_seg.source_word_span
            if span is None:
                continue
            words = _collect_words_in_span(span, raw_by_id)
            if not words:
                continue
            segments_out.append(SegmentWordsResult(segment_id=ann_seg.id, words=words))

        if not segments_out:
            return None

        return TranscriptWordsResult(
            playback_time_offset_seconds=episode.playback_time_offset_seconds,
            segments=segments_out,
        )

    def _summary_path_for_language(
        self,
        episode: Episode,
        *,
        language: Optional[str] = None,
        canonical_language: str = "en",
    ) -> Optional[Path]:
        """Resolve the canonical or language-suffixed summary artefact path."""

        if not episode.summary_path:
            return None
        canonical = normalize_language_code(canonical_language)
        requested = normalize_language_code(language, default=canonical) if language else canonical
        base_path = self.path_manager.summary_file(episode.summary_path)
        if requested == canonical:
            return base_path
        if base_path.suffix == ".md":
            return base_path.with_suffix(f".{requested}.md")
        return base_path.with_name(f"{base_path.name}.{requested}.md")

    def get_summary(
        self,
        podcast_id: Union[str, int],
        episode_id: Union[str, int],
        language: Optional[str] = None,
    ) -> Optional[str]:
        """
        Get the summary for an episode.

        Args:
            podcast_id: Podcast index or RSS URL
            episode_id: Episode index, 'latest', date, or GUID

        Returns:
            Summary Markdown content, "N/A" message, or None if episode not found
        """
        episode = self.get_episode(podcast_id, episode_id)
        if not episode:
            logger.warning(f"Episode not found for summary: {podcast_id}/{episode_id}")
            return None
        canonical_language = self.get_recorded_summary_language(episode) or "en"
        return self.get_summary_for_episode(episode, language=language, canonical_language=canonical_language)

    def get_recorded_summary_language(self, episode: Episode) -> Optional[str]:
        """Return a valid canonical-language manifest value, if one exists."""

        if not episode.summary_path:
            return None
        base_path = self.path_manager.summary_file(episode.summary_path)
        base_key = self.path_manager.to_relative(base_path)
        try:
            content = self.file_storage.read_text(base_key)
        except FileNotFoundError:
            return None
        manifest = load_valid_summary_manifest(
            self.file_storage,
            summary_key=base_key,
            summary_content=content,
        )
        return manifest.canonical_language if manifest else None

    def detect_and_record_summary_language(
        self,
        episode: Episode,
        *,
        podcast_language: str,
        provider: "LLMProvider",
    ) -> str:
        """Resolve an unmarked summary as legacy English or podcast-language output."""

        if not episode.summary_path:
            return "en"
        base_path = self.path_manager.summary_file(episode.summary_path)
        base_key = self.path_manager.to_relative(base_path)
        with self._summary_translation_locks_guard:
            lock = self._summary_translation_locks.setdefault(f"manifest:{base_key}", threading.Lock())
        with lock:
            recorded = self.get_recorded_summary_language(episode)
            if recorded is not None:
                return recorded
            content = self.file_storage.read_text(base_key)
            podcast_code = normalize_language_code(podcast_language)
            if podcast_code == "en":
                detected = "en"
            else:
                from ..core.summary_translation import SummaryTranslator

                detected = SummaryTranslator(provider).detect_language(
                    content,
                    candidates=("en", podcast_code),
                )
            write_summary_manifest(
                self.file_storage,
                summary_key=base_key,
                summary_content=content,
                canonical_language=detected,
            )
            return detected

    def get_summary_for_episode(
        self,
        episode: Episode,
        *,
        language: Optional[str] = None,
        canonical_language: str = "en",
    ) -> Optional[str]:
        """Read one summary variant for an already-resolved episode.

        A missing translated variant returns ``None`` so the caller can lazily
        generate it. The canonical summary keeps the legacy ``N/A`` messages.
        """

        # Check if episode has a summary
        if not episode.summary_path:
            logger.info(f"Episode not yet summarized: {episode.title}")
            return "N/A - Episode not yet summarized"

        # Spec #35 — route through FileStorage. The single try/except
        # replaces the prior require_file_exists + open() pair (two S3
        # round-trips → one).
        canonical = normalize_language_code(canonical_language)
        requested = normalize_language_code(language, default=canonical) if language else canonical
        summary_path = self._summary_path_for_language(
            episode,
            language=requested,
            canonical_language=canonical,
        )
        if summary_path is None:
            return "N/A - Episode not yet summarized"
        try:
            content = self._read_relative(summary_path)
        except FileNotFoundError:
            logger.warning(f"Summary file not found: {summary_path}")
            return None if requested != canonical else "N/A - Summary file not found"
        except Exception as e:
            logger.error(f"Error reading summary file: {e}")
            return f"N/A - Error reading summary: {e}"

        if requested != canonical:
            base_path = self._summary_path_for_language(
                episode,
                canonical_language=canonical,
            )
            if base_path is None:
                return None
            try:
                source_content = self._read_relative(base_path)
            except FileNotFoundError:
                return None
            summary_key = self.path_manager.to_relative(summary_path)
            if (
                load_valid_translation_metadata(
                    self.file_storage,
                    summary_key=summary_key,
                    source_content=source_content,
                    translated_content=content,
                    source_language=canonical,
                    target_language=requested,
                )
                is None
            ):
                logger.info(
                    "summary_translation.stale_or_untrusted",
                    episode_id=episode.id,
                    language=requested,
                )
                return None
        logger.info(f"Retrieved summary for: {episode.title}")
        return content

    def get_or_create_summary_translation(
        self,
        episode: Episode,
        *,
        source_language: str,
        target_language: str,
        provider: "LLMProvider",
    ) -> Optional[str]:
        """Return a cached translation, creating and citation-resolving it once."""

        source = normalize_language_code(source_language)
        target = normalize_language_code(target_language, default=source)
        if target == source:
            return self.get_summary_for_episode(episode, canonical_language=source)

        translation_path = self._summary_path_for_language(
            episode,
            language=target,
            canonical_language=source,
        )
        if translation_path is None:
            return "N/A - Episode not yet summarized"
        translation_key = self.path_manager.to_relative(translation_path)
        with self._summary_translation_locks_guard:
            lock = self._summary_translation_locks.setdefault(translation_key, threading.Lock())

        with lock:
            cached = self.get_summary_for_episode(
                episode,
                language=target,
                canonical_language=source,
            )
            if cached is not None:
                return cached

            original = self.get_summary_for_episode(episode, canonical_language=source)
            if original is None or original.startswith("N/A"):
                return original

            from ..core.summary_citations import resolve_and_persist_summary_citations
            from ..core.summary_translation import SummaryTranslator

            translated = SummaryTranslator(provider).translate(
                original,
                target_language=target,
                source_language=source,
            )
            persisted = resolve_and_persist_summary_citations(
                summary_markdown=translated,
                episode=episode,
                summary_path=translation_path,
                path_manager=self.path_manager,
                file_storage=self.file_storage,
            )
            write_translation_metadata(
                self.file_storage,
                summary_key=translation_key,
                source_content=original,
                translated_content=persisted.markdown,
                source_language=source,
                target_language=target,
            )
            return persisted.markdown

    def get_available_summary_languages(self, episode: Episode, *, canonical_language: str) -> List[str]:
        """List the canonical language and cached sibling translations."""

        if not episode.summary_path:
            return []
        canonical = normalize_language_code(canonical_language)
        base_path = self.path_manager.summary_file(episode.summary_path)
        base_key = self.path_manager.to_relative(base_path)
        key_path = PurePosixPath(base_key)
        stem = base_path.stem if base_path.suffix == ".md" else base_path.name
        pattern = f"{stem}.*.md"
        languages = {canonical}
        try:
            for metadata in self.file_storage.list_files(
                prefix="" if str(key_path.parent) == "." else str(key_path.parent),
                pattern=pattern,
            ):
                match = re.fullmatch(rf"{re.escape(stem)}\.([a-z]{{2,3}})\.md", PurePosixPath(metadata.path).name)
                if (
                    match
                    and self.get_summary_for_episode(
                        episode,
                        language=match.group(1),
                        canonical_language=canonical,
                    )
                    is not None
                ):
                    languages.add(match.group(1))
        except Exception as exc:  # Listing failure must not hide the summary itself.
            logger.warning("summary_translation.list_failed", episode_id=episode.id, error=str(exc))
        return [canonical, *sorted(languages - {canonical})]

    def get_summary_citations_for_episode(
        self,
        episode: Episode,
        summary_content: str,
        *,
        language: Optional[str] = None,
        canonical_language: str = "en",
    ) -> Optional[List[dict]]:
        """Load frontend-safe summary citations for an already-resolved episode.

        Invalid, stale, or missing sidecars return ``None`` so callers can
        render the summary as normal markdown. The helper validates the sidecar
        against the exact summary content being served and the current
        annotated transcript metadata.
        """
        if not episode.summary_path:
            return None
        segmented = self.get_segmented_transcript_for_episode(episode)
        if segmented is None:
            return None
        summary_path = self._summary_path_for_language(
            episode,
            language=language,
            canonical_language=canonical_language,
        )
        if summary_path is None:
            return None
        return load_valid_citations_for_api(
            summary_markdown=summary_content,
            episode=episode,
            transcript=segmented.annotated,
            summary_path=summary_path,
            path_manager=self.path_manager,
            file_storage=self.file_storage,
        )
