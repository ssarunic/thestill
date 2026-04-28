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
Centralized path management for all file artifacts in the thestill pipeline.

This module provides a single source of truth for constructing file paths
across the entire application, preventing scattered path logic and reducing
errors when directory structures change.

Spec #25 item 3.3: every method that accepts an external string and
builds a path now (a) validates slug-shaped inputs against ``_SLUG_RE``
and (b) resolves the final path and asserts it stays under
``storage_path``. The two checks are belt-and-braces — the regex blocks
the obvious ``../`` cases at input time, the resolve guards against any
sneaky variant (URL-encoded traversal, NFC/NFD Unicode tricks, symlinks
inside the data dir).
"""

import re
from pathlib import Path
from typing import Literal, Optional

# Declared here (rather than imported from the processor) to keep
# ``PathManager`` free of upward dependencies on ``core``. The processor
# validates its own env-flag input against the same names.
CleanupPipelineName = Literal["segmented", "legacy"]

# Spec #28 — only the three entity types that get rendered pages on
# disk (per the corpus layout in Strategy §1). ``product`` exists in the
# entity database but does not produce a Markdown page in v1.
CorpusEntityType = Literal["person", "company", "topic"]


# Slugs originate from ``utils.slug.generate_slug`` which uses
# python-slugify and emits lowercase ``[a-z0-9-]+``. The regex below
# additionally forbids leading hyphens and bounds length — both
# defence-in-depth against future slug-source changes.
_SLUG_RE = re.compile(r"^[a-z0-9][a-z0-9-]{0,99}$")

# UUID4 episode IDs are 36 chars of ``[0-9a-f-]`` arranged as 8-4-4-4-12.
# We validate independently of ``_SLUG_RE`` because UUIDs do not match
# the slug pattern (and we don't want to relax the slug rules).
_UUID_RE = re.compile(r"^[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12}$")


def _validate_slug(slug: str, *, name: str) -> str:
    """Refuse slugs that don't match the canonical shape.

    ``name`` is the parameter name in the caller and is included in the
    raised message so the failing field is obvious (``podcast_slug`` vs
    ``episode_slug`` etc.). ``ValueError`` is raised loud so callers
    can't silently coerce malicious input.
    """
    if not isinstance(slug, str) or not _SLUG_RE.fullmatch(slug):
        raise ValueError(f"invalid {name}={slug!r}: must match [a-z0-9][a-z0-9-]{{0,99}}")
    return slug


def _validate_episode_id(episode_id: str) -> str:
    """Refuse anything that isn't a UUID4-shaped string.

    Used by the spec #28 corpus path helpers (``corpus_episode_file`` /
    ``corpus_episode_segmap_file``) where the filename comes from
    ``Episode.id`` rather than a slug. The DB enforces ``length(id) =
    36`` already; this is the same belt-and-braces stance as
    ``_validate_slug``. Returns the **lowercased** form so a caller
    that round-trips an uppercase UUID through this validator gets
    consistent file paths on case-sensitive filesystems.
    """
    if not isinstance(episode_id, str) or not _UUID_RE.fullmatch(episode_id.lower()):
        raise ValueError(f"invalid episode_id={episode_id!r}: must be a UUID4")
    return episode_id.lower()


class PathManager:
    """
    Manages all file paths for the thestill podcast processing pipeline.

    Provides methods to get paths for:
    - Original audio files
    - Downsampled audio files
    - Raw transcripts (JSON)
    - Cleaned transcripts (Markdown)
    - Summaries
    - Evaluations
    - Feed metadata

    Usage:
        paths = PathManager(storage_path="./data")

        # Get directory paths
        original_audio_dir = paths.original_audio_dir()

        # Get file paths
        audio_file = paths.original_audio_file("episode.mp3")
        downsampled_file = paths.downsampled_audio_file("episode.wav")
        transcript_file = paths.raw_transcript_file("episode_transcript.json")
    """

    def __init__(self, storage_path: str = "./data"):
        """
        Initialize PathManager with base storage directory.

        Args:
            storage_path: Base directory for all data storage (default: ./data)
        """
        self.storage_path = Path(storage_path)
        # Cache the resolved root once. ``Path.resolve()`` consults the
        # filesystem each call (in case ``..`` segments need expanding);
        # caching is safe because ``storage_path`` is set once at init
        # and the data root is not expected to move under us.
        self._storage_root_resolved = self.storage_path.resolve()

        # Define all subdirectories
        self._original_audio = "original_audio"
        self._downsampled_audio = "downsampled_audio"
        self._raw_transcripts = "raw_transcripts"
        self._clean_transcripts = "clean_transcripts"
        self._summaries = "summaries"
        self._evaluations = "evaluations"
        self._podcast_facts = "podcast_facts"
        self._episode_facts = "episode_facts"
        self._debug_feeds = "debug_feeds"
        self._pending_operations = "pending_operations"
        self._external_transcripts = "external_transcripts"
        self._digests = "digests"
        # Spec #28 — corpus is the rendered Markdown projection that qmd
        # indexes (per-episode pages with segment anchors + per-entity
        # pages). It's a regenerable view over ``clean_transcripts/`` +
        # the ``entities`` SQLite tables, not a source of truth.
        self._corpus = "corpus"
        self._corpus_episodes = "episodes"
        self._corpus_persons = "persons"
        self._corpus_companies = "companies"
        self._corpus_topics = "topics"
        self._feeds_file = "feeds.json"

    def _assert_inside_root(self, path: Path) -> Path:
        """Resolve ``path`` and refuse if it escapes ``storage_path``.

        Belt-and-braces — slug regex catches the obvious ``../`` at the
        input boundary; this catches any sneaky variant the regex misses
        (URL-encoded sequences, NFC/NFD Unicode normalisation tricks,
        symlinks pointing outside ``data/``). Returns the *un-resolved*
        path so callers see the same shape as before — only the check
        is new.
        """
        try:
            resolved = path.resolve()
        except (OSError, RuntimeError) as exc:
            # ``resolve(strict=False)`` should not actually raise, but
            # treat any filesystem-side failure as a refusal: better to
            # fail loud than to assume safety.
            raise ValueError(f"could not resolve path {path!r}: {exc}") from exc
        if not resolved.is_relative_to(self._storage_root_resolved):
            raise ValueError(
                f"path {path!r} (resolves to {resolved!r}) escapes storage root " f"{self._storage_root_resolved!r}"
            )
        return path

    # Directory path methods

    def original_audio_dir(self) -> Path:
        """Get path to original audio directory"""
        return self.storage_path / self._original_audio

    def downsampled_audio_dir(self) -> Path:
        """Get path to downsampled audio directory"""
        return self.storage_path / self._downsampled_audio

    def raw_transcripts_dir(self) -> Path:
        """Get path to raw transcripts directory"""
        return self.storage_path / self._raw_transcripts

    def clean_transcripts_dir(self) -> Path:
        """Get path to cleaned transcripts directory"""
        return self.storage_path / self._clean_transcripts

    def summaries_dir(self) -> Path:
        """Get path to summaries directory"""
        return self.storage_path / self._summaries

    def evaluations_dir(self) -> Path:
        """Get path to evaluations directory"""
        return self.storage_path / self._evaluations

    def podcast_facts_dir(self) -> Path:
        """Get path to podcast facts directory"""
        return self.storage_path / self._podcast_facts

    def episode_facts_dir(self) -> Path:
        """Get path to episode facts directory"""
        return self.storage_path / self._episode_facts

    def debug_feeds_dir(self) -> Path:
        """Get path to debug feeds directory (stores last downloaded RSS for each podcast)"""
        return self.storage_path / self._debug_feeds

    def pending_operations_dir(self) -> Path:
        """Get path to pending operations directory (stores in-progress transcription jobs)"""
        return self.storage_path / self._pending_operations

    def external_transcripts_dir(self) -> Path:
        """Get path to external transcripts directory (stores downloaded RSS transcripts)"""
        return self.storage_path / self._external_transcripts

    def digests_dir(self) -> Path:
        """Get path to digests directory (stores generated digest markdown files)"""
        return self.storage_path / self._digests

    # --- Spec #28 corpus paths -------------------------------------------------
    # The four entity-type subdirs are split because a rendered Markdown
    # entity page is itself just ``<type>/<slug>.md`` — keeping them in
    # parallel directories matches the spec layout (Strategy §1) and lets
    # Obsidian/qmd globs (`persons/*.md` etc.) work out of the box.

    def corpus_dir(self) -> Path:
        """Root of the rendered corpus projection (``data/corpus/``)."""
        return self.storage_path / self._corpus

    def corpus_episodes_dir(self) -> Path:
        return self.corpus_dir() / self._corpus_episodes

    def corpus_persons_dir(self) -> Path:
        return self.corpus_dir() / self._corpus_persons

    def corpus_companies_dir(self) -> Path:
        return self.corpus_dir() / self._corpus_companies

    def corpus_topics_dir(self) -> Path:
        return self.corpus_dir() / self._corpus_topics

    def corpus_episode_file(self, podcast_slug: str, episode_id: str) -> Path:
        """Rendered episode Markdown page.

        ``episode_id`` is the UUID4 from ``episodes.id`` (36 chars,
        ``[a-f0-9-]``). It does NOT match ``_SLUG_RE`` (which only
        accepts ``[a-z0-9][a-z0-9-]{0,99}``), so we validate it
        independently rather than passing it through ``_validate_slug``.
        ``_assert_inside_root`` is the load-bearing safety check that
        keeps the path under ``data/``.
        """
        _validate_slug(podcast_slug, name="podcast_slug")
        _validate_episode_id(episode_id)
        return self._assert_inside_root(self.corpus_episodes_dir() / podcast_slug / f"{episode_id}.md")

    def corpus_episode_segmap_file(self, podcast_slug: str, episode_id: str) -> Path:
        """Sidecar JSON that maps qmd hits back to segment timestamps.

        Lives next to ``<id>.md`` so byte-offset → segment-id resolution
        is one disk read. Spec Strategy §4 ("Mapping qmd hits to exact
        timestamps").
        """
        _validate_slug(podcast_slug, name="podcast_slug")
        _validate_episode_id(episode_id)
        return self._assert_inside_root(self.corpus_episodes_dir() / podcast_slug / f"{episode_id}.segmap.json")

    def corpus_entity_file(self, entity_type: CorpusEntityType, entity_slug: str) -> Path:
        """Rendered entity page (``persons/<slug>.md`` etc.)."""
        _validate_slug(entity_slug, name="entity_slug")
        type_to_dir = {
            "person": self.corpus_persons_dir(),
            "company": self.corpus_companies_dir(),
            "topic": self.corpus_topics_dir(),
        }
        if entity_type not in type_to_dir:
            raise ValueError(f"unknown entity_type={entity_type!r}: must be one of {sorted(type_to_dir)}")
        return self._assert_inside_root(type_to_dir[entity_type] / f"{entity_slug}.md")

    # File path methods

    def original_audio_file(self, filename: str) -> Path:
        """
        Get full path to an original audio file.

        Args:
            filename: Name of the audio file

        Returns:
            Full path to the audio file in original_audio directory
        """
        return self.original_audio_dir() / filename

    def downsampled_audio_file(self, filename: str) -> Path:
        """
        Get full path to a downsampled audio file.

        Args:
            filename: Name of the downsampled audio file

        Returns:
            Full path to the audio file in downsampled_audio directory
        """
        return self.downsampled_audio_dir() / filename

    def raw_transcript_file(self, filename: str) -> Path:
        """
        Get full path to a raw transcript file.

        Supports both flat structure (legacy) and podcast subdirectory structure.
        If filename contains a path separator (e.g., "podcast-slug/episode_transcript.json"),
        it will be treated as a relative path.

        Args:
            filename: Name of the transcript file, or relative path with podcast subdirectory

        Returns:
            Full path to the transcript file in raw_transcripts directory
        """
        return self.raw_transcripts_dir() / filename

    def raw_transcript_file_with_podcast(self, podcast_slug: str, episode_filename: str) -> Path:
        """
        Get full path to a raw transcript file in a podcast subdirectory.

        Uses podcast subdirectory structure to organize transcripts by podcast.

        Args:
            podcast_slug: Slugified podcast title
            episode_filename: Filename of the raw transcript (e.g., "episode-slug_hash_transcript.json")

        Returns:
            Full path: raw_transcripts/{podcast_slug}/{episode_filename}
        """
        _validate_slug(podcast_slug, name="podcast_slug")
        return self._assert_inside_root(self.raw_transcripts_dir() / podcast_slug / episode_filename)

    def clean_transcript_file(self, filename: str) -> Path:
        """
        Get full path to a cleaned transcript file.

        Supports both flat structure (legacy) and podcast subdirectory structure.
        If filename contains a path separator (e.g., "podcast-slug/episode_cleaned.md"),
        it will be treated as a relative path.

        Args:
            filename: Name of the cleaned transcript file, or relative path with podcast subdirectory

        Returns:
            Full path to the cleaned transcript file in clean_transcripts directory
        """
        return self.clean_transcripts_dir() / filename

    def clean_transcript_file_with_podcast(self, podcast_slug: str, episode_filename: str) -> Path:
        """
        Get full path to a cleaned transcript file in a podcast subdirectory.

        Uses podcast subdirectory structure to organize transcripts by podcast.

        Args:
            podcast_slug: Slugified podcast title
            episode_filename: Filename of the cleaned transcript (e.g., "episode-slug_hash_cleaned.md")

        Returns:
            Full path: clean_transcripts/{podcast_slug}/{episode_filename}
        """
        _validate_slug(podcast_slug, name="podcast_slug")
        return self._assert_inside_root(self.clean_transcripts_dir() / podcast_slug / episode_filename)

    def clean_transcript_json_file(self, podcast_slug: str, episode_filename: str) -> Path:
        """Full path to the structured ``AnnotatedTranscript`` JSON sidecar.

        Sits alongside the blended Markdown produced by the segmented
        cleanup path (spec #18). ``episode_filename`` is the Markdown
        filename (e.g. ``episode-slug_hash_cleaned.md``) — the ``.md``
        suffix is swapped for ``.json`` so the two files pair up by name.

        Args:
            podcast_slug: Slugified podcast title.
            episode_filename: The cleaned-transcript Markdown filename.

        Returns:
            Full path: clean_transcripts/{podcast_slug}/{base}.json
        """
        _validate_slug(podcast_slug, name="podcast_slug")
        base = episode_filename
        if base.endswith(".md"):
            base = base[:-3]
        return self._assert_inside_root(self.clean_transcripts_dir() / podcast_slug / f"{base}.json")

    def clean_transcript_shadow_file(
        self,
        podcast_slug: str,
        episode_filename: str,
        pipeline: CleanupPipelineName,
    ) -> Path:
        """Full path to a shadow cleanup debug artefact.

        When the cleanup processor runs one pipeline as the primary and
        the other as a shadow (spec #18 §"Dual output during debugging"),
        the shadow's blended Markdown lives here. The filename carries
        the shadow pipeline's name so either variant is self-describing:

        - ``.../debug/{base}.shadow_legacy.md`` when the segmented path
          is primary and the legacy cleaner shadowed.
        - ``.../debug/{base}.shadow_segmented.md`` when the legacy path
          is primary and the segmented cleaner shadowed.

        Args:
            podcast_slug: Slugified podcast title.
            episode_filename: The cleaned-transcript Markdown filename.
            pipeline: Either ``"legacy"`` or ``"segmented"`` — identifies
                which pipeline produced this shadow artefact.

        Returns:
            Full path to the shadow debug file.

        Raises:
            ValueError: When ``pipeline`` is neither ``"legacy"`` nor
                ``"segmented"``.
        """
        if pipeline not in ("legacy", "segmented"):
            raise ValueError(f"pipeline must be 'legacy' or 'segmented', got {pipeline!r}")
        _validate_slug(podcast_slug, name="podcast_slug")
        base = episode_filename
        if base.endswith(".md"):
            base = base[:-3]
        return self._assert_inside_root(
            self.clean_transcripts_dir() / podcast_slug / "debug" / f"{base}.shadow_{pipeline}.md"
        )

    def summary_file(self, filename: str) -> Path:
        """
        Get full path to a summary file.

        Args:
            filename: Name of the summary file

        Returns:
            Full path to the summary file in summaries directory
        """
        return self.summaries_dir() / filename

    def digest_file(self, filename: str) -> Path:
        """
        Get full path to a digest file.

        Args:
            filename: Name of the digest file (e.g., "digest_2025-01-26_120000.md")

        Returns:
            Full path to the digest file in digests directory
        """
        return self.digests_dir() / filename

    def external_transcript_file(self, podcast_slug: str, episode_slug: str, extension: str) -> Path:
        """
        Get full path to an external transcript file downloaded from RSS feed.

        External transcripts are stored in podcast subdirectories with format-specific extensions.

        Args:
            podcast_slug: Slugified podcast title
            episode_slug: Slugified episode title
            extension: File extension (e.g., "srt", "vtt", "json", "txt", "html")

        Returns:
            Full path: external_transcripts/{podcast_slug}/{episode_slug}.{extension}
        """
        _validate_slug(podcast_slug, name="podcast_slug")
        _validate_slug(episode_slug, name="episode_slug")
        return self._assert_inside_root(self.external_transcripts_dir() / podcast_slug / f"{episode_slug}.{extension}")

    def external_transcript_dir_for_podcast(self, podcast_slug: str) -> Path:
        """
        Get path to external transcripts directory for a specific podcast.

        Args:
            podcast_slug: Slugified podcast title

        Returns:
            Full path: external_transcripts/{podcast_slug}/
        """
        _validate_slug(podcast_slug, name="podcast_slug")
        return self._assert_inside_root(self.external_transcripts_dir() / podcast_slug)

    def evaluation_file(self, filename: str) -> Path:
        """
        Get full path to an evaluation file.

        Args:
            filename: Name of the evaluation file

        Returns:
            Full path to the evaluation file in evaluations directory
        """
        return self.evaluations_dir() / filename

    def raw_transcript_evaluation_file(self, podcast_slug: str, episode_filename: str) -> Path:
        """
        Get full path to a raw transcript evaluation file.

        Evaluations are organized by type (raw vs clean) and podcast.

        Args:
            podcast_slug: Slugified podcast title
            episode_filename: Filename for the evaluation (e.g., "episode-slug_hash_evaluation.json")

        Returns:
            Full path: evaluations/raw/{podcast_slug}/{episode_filename}
        """
        _validate_slug(podcast_slug, name="podcast_slug")
        return self._assert_inside_root(self.evaluations_dir() / "raw" / podcast_slug / episode_filename)

    def clean_transcript_evaluation_file(self, podcast_slug: str, episode_filename: str) -> Path:
        """
        Get full path to a clean transcript evaluation file.

        Evaluations are organized by type (raw vs clean) and podcast.

        Args:
            podcast_slug: Slugified podcast title
            episode_filename: Filename for the evaluation (e.g., "episode-slug_hash_evaluation.json")

        Returns:
            Full path: evaluations/clean/{podcast_slug}/{episode_filename}
        """
        _validate_slug(podcast_slug, name="podcast_slug")
        return self._assert_inside_root(self.evaluations_dir() / "clean" / podcast_slug / episode_filename)

    def podcast_facts_file(self, podcast_slug: str) -> Path:
        """
        Get full path to a podcast facts file.

        Args:
            podcast_slug: Slugified podcast title

        Returns:
            Full path to the facts file in podcast_facts directory
        """
        _validate_slug(podcast_slug, name="podcast_slug")
        return self._assert_inside_root(self.podcast_facts_dir() / f"{podcast_slug}.facts.md")

    def episode_facts_file(self, podcast_slug: str, episode_slug: str) -> Path:
        """
        Get full path to an episode facts file.

        Uses podcast subdirectory structure to avoid name collisions.

        Args:
            podcast_slug: Slugified podcast title
            episode_slug: Slugified episode title

        Returns:
            Full path to the facts file: episode_facts/{podcast_slug}/{episode_slug}.facts.md
        """
        _validate_slug(podcast_slug, name="podcast_slug")
        _validate_slug(episode_slug, name="episode_slug")
        return self._assert_inside_root(self.episode_facts_dir() / podcast_slug / f"{episode_slug}.facts.md")

    def debug_feed_file(self, podcast_slug: str) -> Path:
        """
        Get full path to a debug RSS feed file.

        Stores the last downloaded RSS XML for debugging purposes.
        Overwrites previous version on each refresh.

        Args:
            podcast_slug: Slugified podcast title

        Returns:
            Full path to the RSS file in debug_feeds directory
        """
        _validate_slug(podcast_slug, name="podcast_slug")
        return self._assert_inside_root(self.debug_feeds_dir() / f"{podcast_slug}.xml")

    def pending_operation_file(self, operation_id: str) -> Path:
        """
        Get full path to a pending operation state file.

        Stores the state of in-progress Google Cloud transcription operations
        so they can be resumed if the app is restarted.

        Args:
            operation_id: Unique operation identifier

        Returns:
            Full path to the operation JSON file in pending_operations directory
        """
        return self.pending_operations_dir() / f"{operation_id}.json"

    def chunks_dir(self, podcast_slug: str, episode_slug: str) -> Path:
        """
        Get path to chunk debug directory for an episode.

        Chunks are stored inside the podcast subdirectory to avoid clashes
        between episodes from different podcasts.

        Structure: raw_transcripts/{podcast_slug}/chunks/{episode_slug}/

        Args:
            podcast_slug: Slugified podcast title
            episode_slug: Slugified episode title (used as subfolder name)

        Returns:
            Full path to the chunks directory for this episode
        """
        _validate_slug(podcast_slug, name="podcast_slug")
        _validate_slug(episode_slug, name="episode_slug")
        return self._assert_inside_root(self.raw_transcripts_dir() / podcast_slug / "chunks" / episode_slug)

    def feeds_file(self) -> Path:
        """
        Get full path to the feeds.json metadata file.

        Returns:
            Full path to feeds.json
        """
        return self.storage_path / self._feeds_file

    # Utility methods

    def ensure_directories_exist(self):
        """Create all required directories if they don't exist"""
        directories = [
            self.storage_path,
            self.original_audio_dir(),
            self.downsampled_audio_dir(),
            self.raw_transcripts_dir(),
            self.clean_transcripts_dir(),
            self.summaries_dir(),
            self.evaluations_dir(),
            self.podcast_facts_dir(),
            self.episode_facts_dir(),
            self.debug_feeds_dir(),
            self.pending_operations_dir(),
            self.digests_dir(),
            # Spec #28 corpus tree.
            self.corpus_dir(),
            self.corpus_episodes_dir(),
            self.corpus_persons_dir(),
            self.corpus_companies_dir(),
            self.corpus_topics_dir(),
        ]

        for directory in directories:
            directory.mkdir(parents=True, exist_ok=True)

    def file_exists(self, directory_type: str, filename: Optional[str] = None) -> bool:
        """
        Check if a file or directory exists.

        Args:
            directory_type: Type of directory ('original_audio', 'downsampled_audio',
                           'raw_transcripts', 'clean_transcripts', 'summaries', 'evaluations',
                           'podcast_facts', 'episode_facts')
            filename: Optional filename to check within the directory

        Returns:
            True if the file/directory exists, False otherwise
        """
        dir_map = {
            "original_audio": self.original_audio_dir(),
            "downsampled_audio": self.downsampled_audio_dir(),
            "raw_transcripts": self.raw_transcripts_dir(),
            "clean_transcripts": self.clean_transcripts_dir(),
            "summaries": self.summaries_dir(),
            "evaluations": self.evaluations_dir(),
            "podcast_facts": self.podcast_facts_dir(),
            "episode_facts": self.episode_facts_dir(),
        }

        if directory_type not in dir_map:
            raise ValueError(f"Unknown directory type: {directory_type}")

        directory = dir_map[directory_type]

        if filename:
            return (directory / filename).exists()
        else:
            return directory.exists()

    def get_file_path(self, directory_type: str, filename: str) -> Path:
        """
        Generic method to get a file path for any directory type.

        Args:
            directory_type: Type of directory ('original_audio', 'downsampled_audio', etc.)
            filename: Name of the file

        Returns:
            Full path to the file
        """
        file_map = {
            "original_audio": self.original_audio_file,
            "downsampled_audio": self.downsampled_audio_file,
            "raw_transcripts": self.raw_transcript_file,
            "clean_transcripts": self.clean_transcript_file,
            "summaries": self.summary_file,
            "evaluations": self.evaluation_file,
            "podcast_facts": self.podcast_facts_file,
            "episode_facts": self.episode_facts_file,
        }

        if directory_type not in file_map:
            raise ValueError(f"Unknown directory type: {directory_type}")

        return file_map[directory_type](filename)

    def require_file_exists(self, file_path: Path, error_message: str) -> Path:
        """
        Check if file exists and raise FileNotFoundError if not.

        This helper centralizes file existence checking with custom error messages,
        reducing repeated existence checks across CLI and services.

        Args:
            file_path: Path to check
            error_message: Custom error message to include in exception

        Returns:
            The same path if it exists

        Raises:
            FileNotFoundError: If file does not exist

        Example:
            >>> path = path_manager.require_file_exists(
            ...     episode_path,
            ...     "Episode audio file not found"
            ... )
        """
        if not file_path.exists():
            raise FileNotFoundError(f"{error_message}: {file_path}")
        return file_path
