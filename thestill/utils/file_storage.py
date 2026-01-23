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

This module provides a cloud-first storage abstraction layer that allows the
application to work with different storage backends (local filesystem, S3, GCS)
without changing the core business logic.

Design Philosophy:
    - Cloud-native: Interface designed around cloud storage semantics
    - Efficient: Batch metadata retrieval, idempotent operations
    - Local adapts to cloud: LocalFileStorage emulates cloud behavior

Usage:
    from thestill.utils.file_storage import LocalFileStorage, FileMetadata

    storage = LocalFileStorage(base_path="./data")

    # Write and read files
    storage.write_text("transcripts/episode.md", "# Transcript...")
    content = storage.read_text("transcripts/episode.md")

    # Get metadata efficiently (single call for all attributes)
    metadata = storage.get_metadata("transcripts/episode.md")
    print(f"Size: {metadata.size}, Modified: {metadata.modified_time}")

    # List files with metadata (no extra API calls needed)
    for meta in storage.list_files("transcripts/"):
        print(f"{meta.path}: {meta.size} bytes")

    # Idempotent delete (no existence check needed)
    storage.delete("transcripts/episode.md")  # Safe even if doesn't exist

    # Batch delete (efficient for cloud - S3 can delete 1000 at once)
    storage.delete_batch(["file1.txt", "file2.txt", "file3.txt"])
"""

import fnmatch
import tempfile
from abc import ABC, abstractmethod
from dataclasses import dataclass
from datetime import datetime
from pathlib import Path
from typing import TYPE_CHECKING, Iterator, List, Optional

if TYPE_CHECKING:
    from google.cloud.storage import Bucket, Client as GCSClient
    from mypy_boto3_s3 import S3Client


@dataclass
class FileMetadata:
    """
    Metadata for a file in storage.

    This class represents all metadata that can be retrieved in a single
    API call from cloud storage backends. Using this instead of separate
    get_size/get_modified_time calls reduces API requests.
    """

    path: str
    size: int
    modified_time: datetime
    content_type: Optional[str] = None
    etag: Optional[str] = None

    @property
    def modified_timestamp(self) -> float:
        """Get modified time as Unix timestamp for backwards compatibility."""
        return self.modified_time.timestamp()


class FileStorage(ABC):
    """
    Abstract interface for file storage operations.

    This abstraction allows the application to work with different storage
    backends (local filesystem, S3, GCS) through a unified interface.

    All paths are relative to the storage root and use forward slashes
    as separators (e.g., "transcripts/podcast-slug/episode.md").

    Design Principles:
        - Operations are idempotent where possible (delete won't fail if missing)
        - Metadata is batched (get_metadata returns all attributes at once)
        - list_files returns metadata to avoid N+1 API calls
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

        Note: Prefer handling FileNotFoundError from read operations
        rather than checking existence first (saves an API call).

        Args:
            path: Relative path to the file (forward slashes)

        Returns:
            True if file exists, False otherwise
        """
        pass

    @abstractmethod
    def delete(self, path: str) -> None:
        """
        Delete a file (idempotent).

        This operation is idempotent - it succeeds even if the file
        doesn't exist. This matches cloud storage semantics and avoids
        the need for existence checks.

        Args:
            path: Relative path to the file (forward slashes)
        """
        pass

    @abstractmethod
    def delete_batch(self, paths: List[str]) -> int:
        """
        Delete multiple files efficiently.

        For cloud backends, this can delete many files in a single API call
        (S3 supports up to 1000 per request). For local storage, this
        iterates but still provides a consistent interface.

        Args:
            paths: List of relative paths to delete

        Returns:
            Number of files that were actually deleted
        """
        pass

    @abstractmethod
    def get_metadata(self, path: str) -> FileMetadata:
        """
        Get file metadata in a single call.

        This is the preferred way to get file attributes. Use this instead
        of separate get_size/get_modified_time calls to reduce API requests.

        Args:
            path: Relative path to the file (forward slashes)

        Returns:
            FileMetadata with size, modified_time, etc.

        Raises:
            FileNotFoundError: If file does not exist
        """
        pass

    @abstractmethod
    def list_files(
        self, prefix: str = "", pattern: Optional[str] = None
    ) -> Iterator[FileMetadata]:
        """
        List files with metadata.

        Returns FileMetadata for each file, avoiding the need for
        separate get_metadata calls. Cloud storage APIs return this
        information with the listing at no extra cost.

        Args:
            prefix: Directory prefix to search in (e.g., "transcripts/")
            pattern: Optional glob pattern (e.g., "*.md", "**/*.json")

        Yields:
            FileMetadata for each matching file
        """
        pass

    def get_size(self, path: str) -> int:
        """
        Get the size of a file in bytes.

        Note: Prefer get_metadata() if you need multiple attributes.

        Args:
            path: Relative path to the file (forward slashes)

        Returns:
            File size in bytes

        Raises:
            FileNotFoundError: If file does not exist
        """
        return self.get_metadata(path).size

    def get_modified_time(self, path: str) -> float:
        """
        Get the last modified timestamp of a file.

        Note: Prefer get_metadata() if you need multiple attributes.

        Args:
            path: Relative path to the file (forward slashes)

        Returns:
            Unix timestamp of last modification

        Raises:
            FileNotFoundError: If file does not exist
        """
        return self.get_metadata(path).modified_timestamp

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

    def get_local_path(self, path: str) -> Path:
        """
        Get local filesystem path for a file.

        For local storage, returns the actual filesystem path.
        For cloud storage, downloads to a temporary file and returns that path.

        This is needed for tools that require filesystem access (e.g., pydub, whisper).

        Args:
            path: Relative path to the file (forward slashes)

        Returns:
            Local Path object

        Raises:
            FileNotFoundError: If file does not exist
        """
        raise NotImplementedError("Subclass must implement get_local_path")

    def ensure_directory(self, path: str) -> None:
        """
        Ensure a directory exists (for backends that support directories).

        For cloud storage backends, this is a no-op since directories
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

    def delete(self, path: str) -> None:
        """Delete a file (idempotent)."""
        file_path = self._resolve_path(path)
        file_path.unlink(missing_ok=True)

    def delete_batch(self, paths: List[str]) -> int:
        """Delete multiple files."""
        deleted = 0
        for path in paths:
            file_path = self._resolve_path(path)
            if file_path.exists():
                file_path.unlink()
                deleted += 1
        return deleted

    def get_metadata(self, path: str) -> FileMetadata:
        """Get file metadata."""
        file_path = self._resolve_path(path)
        stat = file_path.stat()
        return FileMetadata(
            path=path,
            size=stat.st_size,
            modified_time=datetime.fromtimestamp(stat.st_mtime),
            content_type=None,  # Local filesystem doesn't track content type
            etag=None,
        )

    def list_files(
        self, prefix: str = "", pattern: Optional[str] = None
    ) -> Iterator[FileMetadata]:
        """List files with metadata."""
        search_path = self._resolve_path(prefix) if prefix else self.base_path

        if not search_path.exists():
            return

        if pattern:
            # Use glob pattern
            for file_path in search_path.glob(pattern):
                if file_path.is_file():
                    relative = str(file_path.relative_to(self.base_path)).replace("\\", "/")
                    stat = file_path.stat()
                    yield FileMetadata(
                        path=relative,
                        size=stat.st_size,
                        modified_time=datetime.fromtimestamp(stat.st_mtime),
                    )
        else:
            # List all files recursively
            for file_path in search_path.rglob("*"):
                if file_path.is_file():
                    relative = str(file_path.relative_to(self.base_path)).replace("\\", "/")
                    stat = file_path.stat()
                    yield FileMetadata(
                        path=relative,
                        size=stat.st_size,
                        modified_time=datetime.fromtimestamp(stat.st_mtime),
                    )

    def get_local_path(self, path: str) -> Path:
        """Get local filesystem path for a file."""
        file_path = self._resolve_path(path)
        if not file_path.exists():
            raise FileNotFoundError(f"File not found: {path}")
        return file_path

    def ensure_directory(self, path: str) -> None:
        """Ensure a directory exists."""
        dir_path = self._resolve_path(path)
        dir_path.mkdir(parents=True, exist_ok=True)


class S3FileStorage(FileStorage):
    """
    Amazon S3 implementation of FileStorage.

    Stores files in an S3 bucket. Supports AWS S3 and S3-compatible
    services like MinIO and LocalStack.

    Requires: pip install boto3

    Usage:
        storage = S3FileStorage(
            bucket="my-bucket",
            region="us-east-1",
            prefix="data/",  # Optional prefix for all keys
            endpoint_url="http://localhost:4566",  # For LocalStack
        )
    """

    def __init__(
        self,
        bucket: str,
        region: str = "us-east-1",
        prefix: str = "",
        endpoint_url: Optional[str] = None,
        access_key_id: Optional[str] = None,
        secret_access_key: Optional[str] = None,
    ):
        """
        Initialize S3 file storage.

        Args:
            bucket: S3 bucket name
            region: AWS region (default: us-east-1)
            prefix: Optional prefix for all keys (e.g., "data/")
            endpoint_url: Custom endpoint for S3-compatible services
            access_key_id: AWS access key (uses environment/IAM if not provided)
            secret_access_key: AWS secret key (uses environment/IAM if not provided)
        """
        try:
            import boto3
            from botocore.config import Config as BotoConfig
            from botocore.exceptions import ClientError

            self._ClientError = ClientError
        except ImportError:
            raise ImportError(
                "boto3 is required for S3 storage. Install with: pip install boto3"
            )

        self.bucket_name = bucket
        self.prefix = prefix.rstrip("/") + "/" if prefix else ""

        # Configure boto3 client
        client_kwargs = {
            "service_name": "s3",
            "region_name": region,
            "config": BotoConfig(
                signature_version="s3v4",
                retries={"max_attempts": 3, "mode": "adaptive"},
            ),
        }

        if endpoint_url:
            client_kwargs["endpoint_url"] = endpoint_url

        if access_key_id and secret_access_key:
            client_kwargs["aws_access_key_id"] = access_key_id
            client_kwargs["aws_secret_access_key"] = secret_access_key

        self._client: "S3Client" = boto3.client(**client_kwargs)

        # Store for presigned URLs
        self._endpoint_url = endpoint_url
        self._region = region

    def _get_key(self, path: str) -> str:
        """Convert relative path to S3 key with prefix."""
        normalized = path.replace("\\", "/").lstrip("/")
        return f"{self.prefix}{normalized}"

    def _strip_prefix(self, key: str) -> str:
        """Remove prefix from S3 key to get relative path."""
        if self.prefix and key.startswith(self.prefix):
            return key[len(self.prefix) :]
        return key

    def read_text(self, path: str, encoding: str = "utf-8") -> str:
        """Read text content from S3."""
        return self.read_bytes(path).decode(encoding)

    def write_text(
        self, path: str, content: str, encoding: str = "utf-8"
    ) -> None:
        """Write text content to S3."""
        self.write_bytes(path, content.encode(encoding))

    def read_bytes(self, path: str) -> bytes:
        """Read binary content from S3."""
        key = self._get_key(path)
        try:
            response = self._client.get_object(Bucket=self.bucket_name, Key=key)
            return response["Body"].read()
        except self._ClientError as e:
            if e.response["Error"]["Code"] == "NoSuchKey":
                raise FileNotFoundError(f"File not found: {path}")
            raise IOError(f"Failed to read {path}: {e}")

    def write_bytes(self, path: str, content: bytes) -> None:
        """Write binary content to S3."""
        key = self._get_key(path)
        try:
            self._client.put_object(
                Bucket=self.bucket_name,
                Key=key,
                Body=content,
            )
        except self._ClientError as e:
            raise IOError(f"Failed to write {path}: {e}")

    def exists(self, path: str) -> bool:
        """Check if an object exists in S3."""
        key = self._get_key(path)
        try:
            self._client.head_object(Bucket=self.bucket_name, Key=key)
            return True
        except self._ClientError as e:
            if e.response["Error"]["Code"] == "404":
                return False
            raise

    def delete(self, path: str) -> None:
        """Delete an object from S3 (idempotent)."""
        key = self._get_key(path)
        # S3 delete is already idempotent - no error if key doesn't exist
        self._client.delete_object(Bucket=self.bucket_name, Key=key)

    def delete_batch(self, paths: List[str]) -> int:
        """Delete multiple objects from S3 efficiently."""
        if not paths:
            return 0

        # S3 supports deleting up to 1000 objects per request
        deleted = 0
        batch_size = 1000

        for i in range(0, len(paths), batch_size):
            batch = paths[i : i + batch_size]
            objects = [{"Key": self._get_key(p)} for p in batch]

            response = self._client.delete_objects(
                Bucket=self.bucket_name,
                Delete={"Objects": objects, "Quiet": False},
            )

            # Count successfully deleted (not errors)
            deleted += len(response.get("Deleted", []))

        return deleted

    def get_metadata(self, path: str) -> FileMetadata:
        """Get file metadata in a single API call."""
        key = self._get_key(path)
        try:
            response = self._client.head_object(Bucket=self.bucket_name, Key=key)
            return FileMetadata(
                path=path,
                size=response["ContentLength"],
                modified_time=response["LastModified"],
                content_type=response.get("ContentType"),
                etag=response.get("ETag", "").strip('"'),
            )
        except self._ClientError as e:
            if e.response["Error"]["Code"] == "404":
                raise FileNotFoundError(f"File not found: {path}")
            raise

    def list_files(
        self, prefix: str = "", pattern: Optional[str] = None
    ) -> Iterator[FileMetadata]:
        """List objects in S3 with metadata."""
        search_prefix = self._get_key(prefix) if prefix else self.prefix

        paginator = self._client.get_paginator("list_objects_v2")
        pages = paginator.paginate(Bucket=self.bucket_name, Prefix=search_prefix)

        for page in pages:
            for obj in page.get("Contents", []):
                key = obj["Key"]
                # Skip "directory" markers
                if key.endswith("/"):
                    continue

                relative_path = self._strip_prefix(key)

                # Apply pattern filter if specified
                if pattern:
                    if not (
                        fnmatch.fnmatch(relative_path, pattern)
                        or fnmatch.fnmatch(relative_path.split("/")[-1], pattern)
                    ):
                        continue

                yield FileMetadata(
                    path=relative_path,
                    size=obj["Size"],
                    modified_time=obj["LastModified"],
                    etag=obj.get("ETag", "").strip('"'),
                )

    def get_public_url(
        self, path: str, expires_in: int = 3600
    ) -> Optional[str]:
        """Generate a presigned URL for temporary access."""
        key = self._get_key(path)
        try:
            url = self._client.generate_presigned_url(
                "get_object",
                Params={"Bucket": self.bucket_name, "Key": key},
                ExpiresIn=expires_in,
            )
            return url
        except self._ClientError:
            return None

    def get_local_path(self, path: str) -> Path:
        """Download file to a temporary location and return the path."""
        # Determine file extension for temp file
        suffix = Path(path).suffix or ""

        # Create temp file and download
        temp_file = tempfile.NamedTemporaryFile(
            delete=False, suffix=suffix, prefix="thestill_s3_"
        )
        try:
            content = self.read_bytes(path)  # Raises FileNotFoundError if missing
            temp_file.write(content)
            temp_file.close()
            return Path(temp_file.name)
        except Exception:
            # Clean up on failure
            temp_file.close()
            Path(temp_file.name).unlink(missing_ok=True)
            raise

    def ensure_directory(self, path: str) -> None:
        """No-op for S3 (directories are implicit in key names)."""
        pass

    def upload_file(self, local_path: Path, remote_path: str) -> None:
        """
        Upload a local file to S3 (optimized for large files).

        Uses multipart upload for files larger than 8MB.
        """
        key = self._get_key(remote_path)
        try:
            self._client.upload_file(
                str(local_path),
                self.bucket_name,
                key,
            )
        except self._ClientError as e:
            raise IOError(f"Failed to upload {local_path} to {remote_path}: {e}")

    def download_file(self, remote_path: str, local_path: Path) -> None:
        """
        Download a file from S3 to local filesystem (optimized for large files).

        Uses multipart download for large files.
        """
        key = self._get_key(remote_path)
        try:
            local_path.parent.mkdir(parents=True, exist_ok=True)
            self._client.download_file(
                self.bucket_name,
                key,
                str(local_path),
            )
        except self._ClientError as e:
            if e.response["Error"]["Code"] == "404":
                raise FileNotFoundError(f"File not found: {remote_path}")
            raise IOError(f"Failed to download {remote_path} to {local_path}: {e}")


class GCSFileStorage(FileStorage):
    """
    Google Cloud Storage implementation of FileStorage.

    Stores files in a GCS bucket.

    Requires: pip install google-cloud-storage

    Usage:
        storage = GCSFileStorage(
            bucket="my-bucket",
            project="my-project",
            prefix="data/",  # Optional prefix for all keys
        )
    """

    def __init__(
        self,
        bucket: str,
        project: Optional[str] = None,
        prefix: str = "",
        credentials_path: Optional[str] = None,
    ):
        """
        Initialize GCS file storage.

        Args:
            bucket: GCS bucket name
            project: Google Cloud project ID (uses default if not provided)
            prefix: Optional prefix for all keys (e.g., "data/")
            credentials_path: Path to service account JSON key file
        """
        try:
            from google.api_core import exceptions as gcs_exceptions
            from google.cloud import storage as gcs

            self._gcs = gcs
            self._gcs_exceptions = gcs_exceptions
        except ImportError:
            raise ImportError(
                "google-cloud-storage is required for GCS storage. "
                "Install with: pip install google-cloud-storage"
            )

        self.bucket_name = bucket
        self.prefix = prefix.rstrip("/") + "/" if prefix else ""

        # Initialize client
        client_kwargs = {}
        if project:
            client_kwargs["project"] = project

        if credentials_path:
            self._client: "GCSClient" = gcs.Client.from_service_account_json(
                credentials_path, **client_kwargs
            )
        else:
            self._client = gcs.Client(**client_kwargs)

        self._bucket: "Bucket" = self._client.bucket(bucket)

    def _get_key(self, path: str) -> str:
        """Convert relative path to GCS blob name with prefix."""
        normalized = path.replace("\\", "/").lstrip("/")
        return f"{self.prefix}{normalized}"

    def _strip_prefix(self, blob_name: str) -> str:
        """Remove prefix from blob name to get relative path."""
        if self.prefix and blob_name.startswith(self.prefix):
            return blob_name[len(self.prefix) :]
        return blob_name

    def read_text(self, path: str, encoding: str = "utf-8") -> str:
        """Read text content from GCS."""
        return self.read_bytes(path).decode(encoding)

    def write_text(
        self, path: str, content: str, encoding: str = "utf-8"
    ) -> None:
        """Write text content to GCS."""
        self.write_bytes(path, content.encode(encoding))

    def read_bytes(self, path: str) -> bytes:
        """Read binary content from GCS."""
        blob_name = self._get_key(path)
        blob = self._bucket.blob(blob_name)
        try:
            return blob.download_as_bytes()
        except self._gcs_exceptions.NotFound:
            raise FileNotFoundError(f"File not found: {path}")
        except Exception as e:
            raise IOError(f"Failed to read {path}: {e}")

    def write_bytes(self, path: str, content: bytes) -> None:
        """Write binary content to GCS."""
        blob_name = self._get_key(path)
        blob = self._bucket.blob(blob_name)
        try:
            blob.upload_from_string(content)
        except Exception as e:
            raise IOError(f"Failed to write {path}: {e}")

    def exists(self, path: str) -> bool:
        """Check if a blob exists in GCS."""
        blob_name = self._get_key(path)
        blob = self._bucket.blob(blob_name)
        return blob.exists()

    def delete(self, path: str) -> None:
        """Delete a blob from GCS (idempotent)."""
        blob_name = self._get_key(path)
        blob = self._bucket.blob(blob_name)
        try:
            blob.delete()
        except self._gcs_exceptions.NotFound:
            pass  # Already deleted - idempotent

    def delete_batch(self, paths: List[str]) -> int:
        """Delete multiple blobs from GCS."""
        if not paths:
            return 0

        deleted = 0
        for path in paths:
            blob_name = self._get_key(path)
            blob = self._bucket.blob(blob_name)
            try:
                blob.delete()
                deleted += 1
            except self._gcs_exceptions.NotFound:
                pass  # Already deleted

        return deleted

    def get_metadata(self, path: str) -> FileMetadata:
        """Get file metadata in a single API call."""
        blob_name = self._get_key(path)
        blob = self._bucket.blob(blob_name)
        blob.reload()  # Fetch metadata

        if blob.size is None:
            raise FileNotFoundError(f"File not found: {path}")

        return FileMetadata(
            path=path,
            size=blob.size,
            modified_time=blob.updated,
            content_type=blob.content_type,
            etag=blob.etag,
        )

    def list_files(
        self, prefix: str = "", pattern: Optional[str] = None
    ) -> Iterator[FileMetadata]:
        """List blobs in GCS with metadata."""
        search_prefix = self._get_key(prefix) if prefix else self.prefix

        blobs = self._client.list_blobs(self._bucket, prefix=search_prefix)

        for blob in blobs:
            # Skip "directory" markers
            if blob.name.endswith("/"):
                continue

            relative_path = self._strip_prefix(blob.name)

            # Apply pattern filter if specified
            if pattern:
                if not (
                    fnmatch.fnmatch(relative_path, pattern)
                    or fnmatch.fnmatch(relative_path.split("/")[-1], pattern)
                ):
                    continue

            yield FileMetadata(
                path=relative_path,
                size=blob.size,
                modified_time=blob.updated,
                content_type=blob.content_type,
                etag=blob.etag,
            )

    def get_public_url(
        self, path: str, expires_in: int = 3600
    ) -> Optional[str]:
        """Generate a signed URL for temporary access."""
        import datetime

        blob_name = self._get_key(path)
        blob = self._bucket.blob(blob_name)
        try:
            url = blob.generate_signed_url(
                expiration=datetime.timedelta(seconds=expires_in),
                method="GET",
            )
            return url
        except Exception:
            return None

    def get_local_path(self, path: str) -> Path:
        """Download file to a temporary location and return the path."""
        # Determine file extension for temp file
        suffix = Path(path).suffix or ""

        # Create temp file and download
        temp_file = tempfile.NamedTemporaryFile(
            delete=False, suffix=suffix, prefix="thestill_gcs_"
        )
        try:
            content = self.read_bytes(path)  # Raises FileNotFoundError if missing
            temp_file.write(content)
            temp_file.close()
            return Path(temp_file.name)
        except Exception:
            # Clean up on failure
            temp_file.close()
            Path(temp_file.name).unlink(missing_ok=True)
            raise

    def ensure_directory(self, path: str) -> None:
        """No-op for GCS (directories are implicit in blob names)."""
        pass

    def upload_file(self, local_path: Path, remote_path: str) -> None:
        """
        Upload a local file to GCS (uses resumable upload for large files).
        """
        blob_name = self._get_key(remote_path)
        blob = self._bucket.blob(blob_name)
        try:
            blob.upload_from_filename(str(local_path))
        except Exception as e:
            raise IOError(f"Failed to upload {local_path} to {remote_path}: {e}")

    def download_file(self, remote_path: str, local_path: Path) -> None:
        """
        Download a file from GCS to local filesystem.
        """
        blob_name = self._get_key(remote_path)
        blob = self._bucket.blob(blob_name)
        try:
            local_path.parent.mkdir(parents=True, exist_ok=True)
            blob.download_to_filename(str(local_path))
        except self._gcs_exceptions.NotFound:
            raise FileNotFoundError(f"File not found: {remote_path}")
        except Exception as e:
            raise IOError(f"Failed to download {remote_path} to {local_path}: {e}")
