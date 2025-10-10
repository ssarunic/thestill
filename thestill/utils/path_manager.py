# Copyright 2025 thestill.ai
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

        Args:
            filename: Name of the transcript file

        Returns:
            Full path to the transcript file in raw_transcripts directory
        """
        return self.raw_transcripts_dir() / filename

    def clean_transcript_file(self, filename: str) -> Path:
        """
        Get full path to a cleaned transcript file.

        Args:
            filename: Name of the cleaned transcript file

        Returns:
            Full path to the cleaned transcript file in clean_transcripts directory
        """
        return self.clean_transcripts_dir() / filename

    def summary_file(self, filename: str) -> Path:
        """
        Get full path to a summary file.

        Args:
            filename: Name of the summary file

        Returns:
            Full path to the summary file in summaries directory
        """
        return self.summaries_dir() / filename

    def evaluation_file(self, filename: str) -> Path:
        """
        Get full path to an evaluation file.

        Args:
            filename: Name of the evaluation file

        Returns:
            Full path to the evaluation file in evaluations directory
        """
        return self.evaluations_dir() / filename

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
            self.evaluations_dir()
        ]

        for directory in directories:
            directory.mkdir(parents=True, exist_ok=True)

    def file_exists(self, directory_type: str, filename: Optional[str] = None) -> bool:
        """
        Check if a file or directory exists.

        Args:
            directory_type: Type of directory ('original_audio', 'downsampled_audio',
                           'raw_transcripts', 'clean_transcripts', 'summaries', 'evaluations')
            filename: Optional filename to check within the directory

        Returns:
            True if the file/directory exists, False otherwise
        """
        dir_map = {
            'original_audio': self.original_audio_dir(),
            'downsampled_audio': self.downsampled_audio_dir(),
            'raw_transcripts': self.raw_transcripts_dir(),
            'clean_transcripts': self.clean_transcripts_dir(),
            'summaries': self.summaries_dir(),
            'evaluations': self.evaluations_dir()
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
            'original_audio': self.original_audio_file,
            'downsampled_audio': self.downsampled_audio_file,
            'raw_transcripts': self.raw_transcript_file,
            'clean_transcripts': self.clean_transcript_file,
            'summaries': self.summary_file,
            'evaluations': self.evaluation_file
        }

        if directory_type not in file_map:
            raise ValueError(f"Unknown directory type: {directory_type}")

        return file_map[directory_type](filename)
