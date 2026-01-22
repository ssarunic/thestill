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
Abstract file storage interface for thestill.

This module provides a storage abstraction layer that allows the application
to work with different storage backends (local filesystem, S3, GCS) without
changing the core business logic.

Usage:
    from thestill.utils.file_storage import LocalFileStorage

    storage = LocalFileStorage(base_path="./data")

    # Write and read text files
    storage.write_text("transcripts/episode.md", "# Transcript...")
    content = storage.read_text("transcripts/episode.md")

    # Write and read binary files
    storage.write_bytes("audio/episode.mp3", audio_data)
    audio = storage.read_bytes("audio/episode.mp3")

    # Check existence and delete
    if storage.exists("transcripts/episode.md"):
        storage.delete("transcripts/episode.md")
"""

from abc import ABC, abstractmethod
from pathlib import Path
from typing import Iterator, Optional


class FileStorage(ABC):
    """
    Abstract interface for file storage operations.

    This abstraction allows the application to work with different storage
    backends (local filesystem, S3, GCS) through a unified interface.

    All paths are relative to the storage root and use forward slashes
    as separators (e.g., "transcripts/podcast-slug/episode.md").
    """

    @abstractmethod
    def read_text(self, path: str, encoding: str = "utf-8") -> str:
        """
        Read text content from a file.

        Args:
            path: Relative path to the file (forward slashes)
            encoding: Text encoding (default: utf-8)

        Returns:
            File contents as string

        Raises:
            FileNotFoundError: If file does not exist
            IOError: If file cannot be read
        """
        pass

    @abstractmethod
    def write_text(
        self, path: str, content: str, encoding: str = "utf-8"
    ) -> None:
        """
        Write text content to a file.

        Creates parent directories if they don't exist.

        Args:
            path: Relative path to the file (forward slashes)
            content: Text content to write
            encoding: Text encoding (default: utf-8)

        Raises:
            IOError: If file cannot be written
        """
        pass

    @abstractmethod
    def read_bytes(self, path: str) -> bytes:
        """
        Read binary content from a file.

        Args:
            path: Relative path to the file (forward slashes)

        Returns:
            File contents as bytes

        Raises:
            FileNotFoundError: If file does not exist
            IOError: If file cannot be read
        """
        pass

    @abstractmethod
    def write_bytes(self, path: str, content: bytes) -> None:
        """
        Write binary content to a file.

        Creates parent directories if they don't exist.

        Args:
            path: Relative path to the file (forward slashes)
            content: Binary content to write

        Raises:
            IOError: If file cannot be written
        """
        pass

    @abstractmethod
    def exists(self, path: str) -> bool:
        """
        Check if a file exists.

        Args:
            path: Relative path to the file (forward slashes)

        Returns:
            True if file exists, False otherwise
        """
        pass

    @abstractmethod
    def delete(self, path: str) -> bool:
        """
        Delete a file.

        Args:
            path: Relative path to the file (forward slashes)

        Returns:
            True if file was deleted, False if it didn't exist
        """
        pass

    @abstractmethod
    def list_files(
        self, prefix: str = "", pattern: Optional[str] = None
    ) -> Iterator[str]:
        """
        List files with optional prefix and glob pattern.

        Args:
            prefix: Directory prefix to search in (e.g., "transcripts/")
            pattern: Optional glob pattern (e.g., "*.md", "**/*.json")

        Yields:
            Relative paths of matching files
        """
        pass

    @abstractmethod
    def get_size(self, path: str) -> int:
        """
        Get the size of a file in bytes.

        Args:
            path: Relative path to the file (forward slashes)

        Returns:
            File size in bytes

        Raises:
            FileNotFoundError: If file does not exist
        """
        pass

    @abstractmethod
    def get_modified_time(self, path: str) -> float:
        """
        Get the last modified timestamp of a file.

        Args:
            path: Relative path to the file (forward slashes)

        Returns:
            Unix timestamp of last modification

        Raises:
            FileNotFoundError: If file does not exist
        """
        pass

    def get_public_url(
        self, path: str, expires_in: int = 3600
    ) -> Optional[str]:
        """
        Get a public/presigned URL for the file.

        For cloud storage backends, returns a presigned URL that allows
        temporary public access. For local storage, returns None.

        Args:
            path: Relative path to the file (forward slashes)
            expires_in: URL expiration time in seconds (default: 1 hour)

        Returns:
            Public URL string, or None if not supported
        """
        return None

    def get_local_path(self, path: str) -> Optional[Path]:
        """
        Get local filesystem path for a file.

        For local storage, returns the actual filesystem path.
        For cloud storage, may download to a temporary file and return that path.

        This is needed for tools that require filesystem access (e.g., pydub, whisper).

        Args:
            path: Relative path to the file (forward slashes)

        Returns:
            Local Path object, or None if not available
        """
        return None

    def ensure_directory(self, path: str) -> None:
        """
        Ensure a directory exists (for backends that support directories).

        For cloud storage backends, this may be a no-op since directories
        are implicit in object keys.

        Args:
            path: Relative directory path (forward slashes)
        """
        pass


class LocalFileStorage(FileStorage):
    """
    Local filesystem implementation of FileStorage.

    Stores files on the local filesystem under a base directory.
    All paths are relative to this base directory.
    """

    def __init__(self, base_path: str):
        """
        Initialize local file storage.

        Args:
            base_path: Base directory for all file operations
        """
        self.base_path = Path(base_path).resolve()
        self.base_path.mkdir(parents=True, exist_ok=True)

    def _resolve_path(self, path: str) -> Path:
        """Resolve relative path to absolute path under base directory."""
        # Normalize path separators and resolve
        normalized = path.replace("\\", "/")
        resolved = (self.base_path / normalized).resolve()

        # Security: ensure resolved path is under base_path
        try:
            resolved.relative_to(self.base_path)
        except ValueError:
            raise ValueError(f"Path '{path}' escapes base directory")

        return resolved

    def read_text(self, path: str, encoding: str = "utf-8") -> str:
        """Read text content from a file."""
        file_path = self._resolve_path(path)
        return file_path.read_text(encoding=encoding)

    def write_text(
        self, path: str, content: str, encoding: str = "utf-8"
    ) -> None:
        """Write text content to a file."""
        file_path = self._resolve_path(path)
        file_path.parent.mkdir(parents=True, exist_ok=True)
        file_path.write_text(content, encoding=encoding)

    def read_bytes(self, path: str) -> bytes:
        """Read binary content from a file."""
        file_path = self._resolve_path(path)
        return file_path.read_bytes()

    def write_bytes(self, path: str, content: bytes) -> None:
        """Write binary content to a file."""
        file_path = self._resolve_path(path)
        file_path.parent.mkdir(parents=True, exist_ok=True)
        file_path.write_bytes(content)

    def exists(self, path: str) -> bool:
        """Check if a file exists."""
        file_path = self._resolve_path(path)
        return file_path.exists()

    def delete(self, path: str) -> bool:
        """Delete a file."""
        file_path = self._resolve_path(path)
        if file_path.exists():
            file_path.unlink()
            return True
        return False

    def list_files(
        self, prefix: str = "", pattern: Optional[str] = None
    ) -> Iterator[str]:
        """List files with optional prefix and glob pattern."""
        search_path = self._resolve_path(prefix) if prefix else self.base_path

        if not search_path.exists():
            return

        if pattern:
            # Use glob pattern
            for file_path in search_path.glob(pattern):
                if file_path.is_file():
                    # Return relative path with forward slashes
                    relative = file_path.relative_to(self.base_path)
                    yield str(relative).replace("\\", "/")
        else:
            # List all files recursively
            for file_path in search_path.rglob("*"):
                if file_path.is_file():
                    relative = file_path.relative_to(self.base_path)
                    yield str(relative).replace("\\", "/")

    def get_size(self, path: str) -> int:
        """Get the size of a file in bytes."""
        file_path = self._resolve_path(path)
        return file_path.stat().st_size

    def get_modified_time(self, path: str) -> float:
        """Get the last modified timestamp of a file."""
        file_path = self._resolve_path(path)
        return file_path.stat().st_mtime

    def get_local_path(self, path: str) -> Optional[Path]:
        """Get local filesystem path for a file."""
        return self._resolve_path(path)

    def ensure_directory(self, path: str) -> None:
        """Ensure a directory exists."""
        dir_path = self._resolve_path(path)
        dir_path.mkdir(parents=True, exist_ok=True)
