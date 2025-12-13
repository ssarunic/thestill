# Copyright 2025 thestill.me
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
"""

from pathlib import Path
from typing import Optional


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
        self._feeds_file = "feeds.json"

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
        return self.raw_transcripts_dir() / podcast_slug / episode_filename

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
        return self.clean_transcripts_dir() / podcast_slug / episode_filename

    def summary_file(self, filename: str) -> Path:
        """
        Get full path to a summary file.

        Args:
            filename: Name of the summary file

        Returns:
            Full path to the summary file in summaries directory
        """
        return self.summaries_dir() / filename

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
        return self.external_transcripts_dir() / podcast_slug / f"{episode_slug}.{extension}"

    def external_transcript_dir_for_podcast(self, podcast_slug: str) -> Path:
        """
        Get path to external transcripts directory for a specific podcast.

        Args:
            podcast_slug: Slugified podcast title

        Returns:
            Full path: external_transcripts/{podcast_slug}/
        """
        return self.external_transcripts_dir() / podcast_slug

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
        return self.evaluations_dir() / "raw" / podcast_slug / episode_filename

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
        return self.evaluations_dir() / "clean" / podcast_slug / episode_filename

    def podcast_facts_file(self, podcast_slug: str) -> Path:
        """
        Get full path to a podcast facts file.

        Args:
            podcast_slug: Slugified podcast title

        Returns:
            Full path to the facts file in podcast_facts directory
        """
        return self.podcast_facts_dir() / f"{podcast_slug}.facts.md"

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
        return self.episode_facts_dir() / podcast_slug / f"{episode_slug}.facts.md"

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
        return self.debug_feeds_dir() / f"{podcast_slug}.xml"

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
        return self.raw_transcripts_dir() / podcast_slug / "chunks" / episode_slug

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
