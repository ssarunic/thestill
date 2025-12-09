#!/usr/bin/env python3
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
Unit tests for PathManager class.

Tests cover:
- Initialization with default and custom paths
- Directory path generation
- File path generation
- Path existence checking
- Directory creation
- Generic path retrieval
- Edge cases and error handling
"""

import tempfile
from pathlib import Path

import pytest

from thestill.utils.path_manager import PathManager


class TestPathManagerInitialization:
    """Test PathManager initialization"""

    def test_default_initialization(self):
        """Test initialization with default storage path"""
        pm = PathManager()
        assert pm.storage_path == Path("./data")

    def test_custom_initialization(self):
        """Test initialization with custom storage path"""
        custom_path = "/tmp/custom_data"
        pm = PathManager(storage_path=custom_path)
        assert pm.storage_path == Path(custom_path)

    def test_path_object_initialization(self):
        """Test initialization with Path object"""
        custom_path = Path("/tmp/custom_data")
        pm = PathManager(storage_path=str(custom_path))
        assert pm.storage_path == custom_path


class TestDirectoryPaths:
    """Test directory path generation methods"""

    def test_original_audio_dir(self):
        """Test original_audio_dir returns correct path"""
        pm = PathManager(storage_path="/tmp/test")
        expected = Path("/tmp/test/original_audio")
        assert pm.original_audio_dir() == expected

    def test_downsampled_audio_dir(self):
        """Test downsampled_audio_dir returns correct path"""
        pm = PathManager(storage_path="/tmp/test")
        expected = Path("/tmp/test/downsampled_audio")
        assert pm.downsampled_audio_dir() == expected

    def test_raw_transcripts_dir(self):
        """Test raw_transcripts_dir returns correct path"""
        pm = PathManager(storage_path="/tmp/test")
        expected = Path("/tmp/test/raw_transcripts")
        assert pm.raw_transcripts_dir() == expected

    def test_clean_transcripts_dir(self):
        """Test clean_transcripts_dir returns correct path"""
        pm = PathManager(storage_path="/tmp/test")
        expected = Path("/tmp/test/clean_transcripts")
        assert pm.clean_transcripts_dir() == expected

    def test_summaries_dir(self):
        """Test summaries_dir returns correct path"""
        pm = PathManager(storage_path="/tmp/test")
        expected = Path("/tmp/test/summaries")
        assert pm.summaries_dir() == expected

    def test_evaluations_dir(self):
        """Test evaluations_dir returns correct path"""
        pm = PathManager(storage_path="/tmp/test")
        expected = Path("/tmp/test/evaluations")
        assert pm.evaluations_dir() == expected


class TestFilePaths:
    """Test file path generation methods"""

    def test_original_audio_file(self):
        """Test original_audio_file returns correct path"""
        pm = PathManager(storage_path="/tmp/test")
        filename = "episode.mp3"
        expected = Path("/tmp/test/original_audio/episode.mp3")
        assert pm.original_audio_file(filename) == expected

    def test_downsampled_audio_file(self):
        """Test downsampled_audio_file returns correct path"""
        pm = PathManager(storage_path="/tmp/test")
        filename = "episode.wav"
        expected = Path("/tmp/test/downsampled_audio/episode.wav")
        assert pm.downsampled_audio_file(filename) == expected

    def test_raw_transcript_file(self):
        """Test raw_transcript_file returns correct path"""
        pm = PathManager(storage_path="/tmp/test")
        filename = "episode_transcript.json"
        expected = Path("/tmp/test/raw_transcripts/episode_transcript.json")
        assert pm.raw_transcript_file(filename) == expected

    def test_raw_transcript_file_with_subdirectory(self):
        """Test raw_transcript_file with podcast subdirectory path"""
        pm = PathManager(storage_path="/tmp/test")
        filename = "my-podcast/episode_transcript.json"
        expected = Path("/tmp/test/raw_transcripts/my-podcast/episode_transcript.json")
        assert pm.raw_transcript_file(filename) == expected

    def test_raw_transcript_file_with_podcast(self):
        """Test raw_transcript_file_with_podcast returns correct path"""
        pm = PathManager(storage_path="/tmp/test")
        podcast_slug = "my-podcast"
        episode_filename = "episode-slug_abc123_transcript.json"
        expected = Path("/tmp/test/raw_transcripts/my-podcast/episode-slug_abc123_transcript.json")
        assert pm.raw_transcript_file_with_podcast(podcast_slug, episode_filename) == expected

    def test_clean_transcript_file(self):
        """Test clean_transcript_file returns correct path"""
        pm = PathManager(storage_path="/tmp/test")
        filename = "episode_clean.md"
        expected = Path("/tmp/test/clean_transcripts/episode_clean.md")
        assert pm.clean_transcript_file(filename) == expected

    def test_summary_file(self):
        """Test summary_file returns correct path"""
        pm = PathManager(storage_path="/tmp/test")
        filename = "episode_summary.json"
        expected = Path("/tmp/test/summaries/episode_summary.json")
        assert pm.summary_file(filename) == expected

    def test_evaluation_file(self):
        """Test evaluation_file returns correct path"""
        pm = PathManager(storage_path="/tmp/test")
        filename = "episode_eval.json"
        expected = Path("/tmp/test/evaluations/episode_eval.json")
        assert pm.evaluation_file(filename) == expected

    def test_feeds_file(self):
        """Test feeds_file returns correct path"""
        pm = PathManager(storage_path="/tmp/test")
        expected = Path("/tmp/test/feeds.json")
        assert pm.feeds_file() == expected


class TestFilePathsWithSpecialCharacters:
    """Test file paths with special characters and edge cases"""

    def test_filename_with_spaces(self):
        """Test file path with spaces in filename"""
        pm = PathManager(storage_path="/tmp/test")
        filename = "my episode file.mp3"
        result = pm.original_audio_file(filename)
        assert result == Path("/tmp/test/original_audio/my episode file.mp3")

    def test_filename_with_unicode(self):
        """Test file path with unicode characters"""
        pm = PathManager(storage_path="/tmp/test")
        filename = "episode_résumé_日本語.json"
        result = pm.raw_transcript_file(filename)
        assert result == Path("/tmp/test/raw_transcripts/episode_résumé_日本語.json")

    def test_filename_with_dots(self):
        """Test file path with multiple dots"""
        pm = PathManager(storage_path="/tmp/test")
        filename = "episode.backup.old.mp3"
        result = pm.original_audio_file(filename)
        assert result == Path("/tmp/test/original_audio/episode.backup.old.mp3")


class TestEnsureDirectoriesExist:
    """Test directory creation functionality"""

    def test_ensure_directories_exist_creates_all(self):
        """Test that ensure_directories_exist creates all required directories"""
        with tempfile.TemporaryDirectory() as tmpdir:
            pm = PathManager(storage_path=tmpdir)
            pm.ensure_directories_exist()

            # Check all directories were created
            assert pm.storage_path.exists()
            assert pm.original_audio_dir().exists()
            assert pm.downsampled_audio_dir().exists()
            assert pm.raw_transcripts_dir().exists()
            assert pm.clean_transcripts_dir().exists()
            assert pm.summaries_dir().exists()
            assert pm.evaluations_dir().exists()

    def test_ensure_directories_exist_idempotent(self):
        """Test that ensure_directories_exist can be called multiple times safely"""
        with tempfile.TemporaryDirectory() as tmpdir:
            pm = PathManager(storage_path=tmpdir)
            pm.ensure_directories_exist()
            pm.ensure_directories_exist()  # Should not raise error

            # Directories should still exist
            assert pm.original_audio_dir().exists()

    def test_ensure_directories_exist_with_nested_path(self):
        """Test directory creation with deeply nested storage path"""
        with tempfile.TemporaryDirectory() as tmpdir:
            nested_path = Path(tmpdir) / "level1" / "level2" / "level3"
            pm = PathManager(storage_path=str(nested_path))
            pm.ensure_directories_exist()

            # Check nested path and subdirectories were created
            assert nested_path.exists()
            assert pm.original_audio_dir().exists()


class TestFileExists:
    """Test file_exists functionality"""

    def test_file_exists_directory_exists(self):
        """Test file_exists returns True for existing directory"""
        with tempfile.TemporaryDirectory() as tmpdir:
            pm = PathManager(storage_path=tmpdir)
            pm.ensure_directories_exist()

            assert pm.file_exists("original_audio") is True

    def test_file_exists_directory_not_exists(self):
        """Test file_exists returns False for non-existing directory"""
        with tempfile.TemporaryDirectory() as tmpdir:
            pm = PathManager(storage_path=tmpdir)
            # Don't create directories

            assert pm.file_exists("original_audio") is False

    def test_file_exists_file_exists(self):
        """Test file_exists returns True for existing file"""
        with tempfile.TemporaryDirectory() as tmpdir:
            pm = PathManager(storage_path=tmpdir)
            pm.ensure_directories_exist()

            # Create a test file
            test_file = pm.original_audio_file("test.mp3")
            test_file.touch()

            assert pm.file_exists("original_audio", "test.mp3") is True

    def test_file_exists_file_not_exists(self):
        """Test file_exists returns False for non-existing file"""
        with tempfile.TemporaryDirectory() as tmpdir:
            pm = PathManager(storage_path=tmpdir)
            pm.ensure_directories_exist()

            assert pm.file_exists("original_audio", "nonexistent.mp3") is False

    def test_file_exists_invalid_directory_type(self):
        """Test file_exists raises ValueError for invalid directory type"""
        pm = PathManager(storage_path="/tmp/test")

        with pytest.raises(ValueError, match="Unknown directory type"):
            pm.file_exists("invalid_directory_type")

    def test_file_exists_all_directory_types(self):
        """Test file_exists works with all valid directory types"""
        with tempfile.TemporaryDirectory() as tmpdir:
            pm = PathManager(storage_path=tmpdir)
            pm.ensure_directories_exist()

            valid_types = [
                "original_audio",
                "downsampled_audio",
                "raw_transcripts",
                "clean_transcripts",
                "summaries",
                "evaluations",
            ]

            for dir_type in valid_types:
                assert pm.file_exists(dir_type) is True


class TestGetFilePath:
    """Test get_file_path generic method"""

    def test_get_file_path_original_audio(self):
        """Test get_file_path for original_audio"""
        pm = PathManager(storage_path="/tmp/test")
        result = pm.get_file_path("original_audio", "episode.mp3")
        expected = Path("/tmp/test/original_audio/episode.mp3")
        assert result == expected

    def test_get_file_path_downsampled_audio(self):
        """Test get_file_path for downsampled_audio"""
        pm = PathManager(storage_path="/tmp/test")
        result = pm.get_file_path("downsampled_audio", "episode.wav")
        expected = Path("/tmp/test/downsampled_audio/episode.wav")
        assert result == expected

    def test_get_file_path_raw_transcripts(self):
        """Test get_file_path for raw_transcripts"""
        pm = PathManager(storage_path="/tmp/test")
        result = pm.get_file_path("raw_transcripts", "transcript.json")
        expected = Path("/tmp/test/raw_transcripts/transcript.json")
        assert result == expected

    def test_get_file_path_clean_transcripts(self):
        """Test get_file_path for clean_transcripts"""
        pm = PathManager(storage_path="/tmp/test")
        result = pm.get_file_path("clean_transcripts", "clean.md")
        expected = Path("/tmp/test/clean_transcripts/clean.md")
        assert result == expected

    def test_get_file_path_summaries(self):
        """Test get_file_path for summaries"""
        pm = PathManager(storage_path="/tmp/test")
        result = pm.get_file_path("summaries", "summary.json")
        expected = Path("/tmp/test/summaries/summary.json")
        assert result == expected

    def test_get_file_path_evaluations(self):
        """Test get_file_path for evaluations"""
        pm = PathManager(storage_path="/tmp/test")
        result = pm.get_file_path("evaluations", "eval.json")
        expected = Path("/tmp/test/evaluations/eval.json")
        assert result == expected

    def test_get_file_path_invalid_directory_type(self):
        """Test get_file_path raises ValueError for invalid directory type"""
        pm = PathManager(storage_path="/tmp/test")

        with pytest.raises(ValueError, match="Unknown directory type"):
            pm.get_file_path("invalid_type", "file.txt")


class TestPathManagerIntegration:
    """Integration tests for PathManager"""

    def test_full_workflow(self):
        """Test a full workflow: create directories, add files, check existence"""
        with tempfile.TemporaryDirectory() as tmpdir:
            pm = PathManager(storage_path=tmpdir)

            # Create directories
            pm.ensure_directories_exist()

            # Create test files in each directory
            test_files = {
                "original_audio": "episode.mp3",
                "downsampled_audio": "episode.wav",
                "raw_transcripts": "transcript.json",
                "clean_transcripts": "clean.md",
                "summaries": "summary.json",
                "evaluations": "eval.json",
            }

            for dir_type, filename in test_files.items():
                file_path = pm.get_file_path(dir_type, filename)
                file_path.touch()

            # Verify all files exist
            for dir_type, filename in test_files.items():
                assert pm.file_exists(dir_type, filename) is True

    def test_relative_and_absolute_paths_consistency(self):
        """Test that relative and absolute paths work consistently"""
        with tempfile.TemporaryDirectory() as tmpdir:
            pm_abs = PathManager(storage_path=tmpdir)
            pm_abs.ensure_directories_exist()

            # Both should create the same directory structure
            assert pm_abs.original_audio_dir().exists()
            assert pm_abs.original_audio_dir().is_dir()


class TestPathManagerEdgeCases:
    """Test edge cases and error scenarios"""

    def test_empty_filename(self):
        """Test behavior with empty filename"""
        pm = PathManager(storage_path="/tmp/test")
        result = pm.original_audio_file("")
        assert result == Path("/tmp/test/original_audio")

    def test_path_with_trailing_slash(self):
        """Test storage path with trailing slash"""
        pm = PathManager(storage_path="/tmp/test/")
        assert pm.storage_path == Path("/tmp/test/")
        assert pm.original_audio_dir() == Path("/tmp/test/original_audio")

    def test_multiple_path_managers_same_storage(self):
        """Test multiple PathManager instances with same storage path"""
        with tempfile.TemporaryDirectory() as tmpdir:
            pm1 = PathManager(storage_path=tmpdir)
            pm2 = PathManager(storage_path=tmpdir)

            pm1.ensure_directories_exist()

            # Second instance should see directories created by first
            assert pm2.file_exists("original_audio") is True

    def test_feeds_file_location(self):
        """Test that feeds.json is at storage root, not in subdirectory"""
        pm = PathManager(storage_path="/tmp/test")
        feeds_path = pm.feeds_file()

        assert feeds_path == Path("/tmp/test/feeds.json")
        assert feeds_path.parent == pm.storage_path


class TestRequireFileExists:
    """Test require_file_exists helper method"""

    def test_require_file_exists_when_file_exists(self):
        """Test require_file_exists returns path when file exists"""
        with tempfile.TemporaryDirectory() as tmpdir:
            pm = PathManager(storage_path=tmpdir)
            pm.ensure_directories_exist()

            # Create a test file
            test_file = pm.original_audio_file("test.mp3")
            test_file.touch()

            # Should return the path without raising
            result = pm.require_file_exists(test_file, "Test file not found")
            assert result == test_file

    def test_require_file_exists_when_file_not_exists(self):
        """Test require_file_exists raises FileNotFoundError when file doesn't exist"""
        with tempfile.TemporaryDirectory() as tmpdir:
            pm = PathManager(storage_path=tmpdir)
            pm.ensure_directories_exist()

            # File doesn't exist
            test_file = pm.original_audio_file("nonexistent.mp3")

            # Should raise FileNotFoundError
            with pytest.raises(FileNotFoundError, match="Test file not found"):
                pm.require_file_exists(test_file, "Test file not found")

    def test_require_file_exists_error_message_includes_path(self):
        """Test require_file_exists error message includes the file path"""
        with tempfile.TemporaryDirectory() as tmpdir:
            pm = PathManager(storage_path=tmpdir)
            pm.ensure_directories_exist()

            test_file = pm.original_audio_file("missing.mp3")

            # Should include both custom message and path in error
            with pytest.raises(FileNotFoundError) as exc_info:
                pm.require_file_exists(test_file, "Custom error message")

            error_message = str(exc_info.value)
            assert "Custom error message" in error_message
            assert str(test_file) in error_message

    def test_require_file_exists_with_directory(self):
        """Test require_file_exists works with directories too"""
        with tempfile.TemporaryDirectory() as tmpdir:
            pm = PathManager(storage_path=tmpdir)
            pm.ensure_directories_exist()

            # Should work with existing directory
            audio_dir = pm.original_audio_dir()
            result = pm.require_file_exists(audio_dir, "Directory not found")
            assert result == audio_dir

    def test_require_file_exists_with_nonexistent_directory(self):
        """Test require_file_exists raises for non-existent directory"""
        with tempfile.TemporaryDirectory() as tmpdir:
            pm = PathManager(storage_path=tmpdir)
            # Don't create directories

            audio_dir = pm.original_audio_dir()

            # Should raise for non-existent directory
            with pytest.raises(FileNotFoundError, match="Directory not found"):
                pm.require_file_exists(audio_dir, "Directory not found")
