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

"""Spec #35 — ``FileStorage`` ABC and ``FileMetadata`` dataclass.

The abstraction is cloud-shaped on purpose: operations the local FS gets
for free (``delete`` raising on missing, multiple stat calls for size,
mtime, etag) are designed to match S3's contracts so the cloud backend
doesn't need to fake semantics it can't cheaply provide. ``LocalFileStorage``
adapts in the other direction — making local I/O behave like S3 — so callers
write to one contract regardless of which backend is wired up.

Paths are forward-slash strings everywhere. S3 keys are forward-slash by
convention; backslashes on Windows would break the join logic in
``S3FileStorage._key``. ``LocalFileStorage`` normalises inbound paths so the
two backends accept identical inputs.
"""

from __future__ import annotations

from abc import ABC, abstractmethod
from contextlib import contextmanager
from dataclasses import dataclass
from datetime import datetime
from pathlib import Path
from typing import Iterable, Iterator, Optional

from ..exceptions import TransientError


def _normalize_key(path: str) -> str:
    """Normalize a caller-supplied path to a forward-slash, no-leading-slash key.

    Used by both backends — keys are cloud-shaped (forward slashes only,
    no leading slash) so a single helper guarantees the two implementations
    can't drift on whitespace, backslash handling, or leading-slash rules.
    """
    if not isinstance(path, str):
        raise TypeError(f"path must be str, got {type(path).__name__}")
    return path.replace("\\", "/").lstrip("/")


class StorageError(TransientError):
    """Raised for backend-side failures that aren't ``FileNotFoundError``.

    Inherits from :class:`TransientError` because the typical S3 failure
    mode is exactly what ``TransientError`` exists for — network blip,
    throttling, transient permission glitch. The task worker's retry/DLQ
    layer keys off ``TransientError`` and will auto-retry storage failures
    with exponential backoff once callers migrate to ``FileStorage``.

    Callers MAY catch this when they want to distinguish "file isn't there"
    (use ``FileNotFoundError`` — same contract on both backends) from
    "the backend failed for some other reason".
    """


@dataclass(frozen=True)
class FileMetadata:
    """Single-round-trip metadata for a stored object.

    ``list_files`` yields these directly from listing API responses so callers
    that need size or mtime don't have to issue a follow-up ``get_metadata``
    per object — that's the N+1 pattern this dataclass exists to prevent.
    """

    path: str
    """Forward-slash path relative to the backend root. Excludes any
    backend-side prefix (e.g. S3 ``prefix=``) — callers see the same
    keys they wrote."""

    size: int
    """Bytes on the backend."""

    modified_time: datetime
    """UTC, timezone-aware. Local backend uses ``st_mtime``; S3 uses
    ``LastModified`` (already tz-aware from boto3)."""

    content_type: Optional[str] = None
    """MIME type when the backend knows it. ``None`` for local (we don't
    sniff); ``ContentType`` from S3 ``head_object`` / listings."""

    etag: Optional[str] = None
    """Content fingerprint when the backend provides one. ``None`` for
    local; S3 returns an MD5-ish ETag wrapped in quotes — passed through
    as-is."""

    @property
    def modified_timestamp(self) -> float:
        """Unix epoch seconds. Mostly here for callers porting code that
        used ``Path.stat().st_mtime`` directly."""
        return self.modified_time.timestamp()


class FileStorage(ABC):
    """Backend-agnostic file storage.

    Every caller — services, transcribers, web routes, MCP tools — reads
    and writes through this interface so the underlying store can be local
    disk (dev/RPi5) or S3 (AWS production) without touching the call sites.

    Design rules concrete backends MUST follow:

    - **Idempotent delete.** ``delete(missing)`` returns silently. Mirrors S3.
    - **No N+1 metadata.** ``list_files`` yields ``FileMetadata`` populated
      from the listing call — never call ``get_metadata`` per entry.
    - **Forward-slash paths.** Backends accept ``"foo/bar.txt"``; backslashes
      get normalised at the boundary.
    - **``FileNotFoundError`` for missing reads.** Not a backend-specific
      exception type. Callers can rely on the stdlib exception.
    """

    # --- Required surface -----------------------------------------------------

    @abstractmethod
    def read_text(self, path: str, *, encoding: str = "utf-8") -> str:
        """Read a UTF-8 (or specified encoding) text file.

        Raises:
            FileNotFoundError: ``path`` does not exist on the backend.
        """

    @abstractmethod
    def write_text(self, path: str, content: str, *, encoding: str = "utf-8") -> None:
        """Write a string to ``path``. Creates parent directories.

        Atomicity is best-effort on local; S3 ``put_object`` is atomic per
        request.
        """

    @abstractmethod
    def read_bytes(self, path: str) -> bytes:
        """Read raw bytes.

        Raises:
            FileNotFoundError: ``path`` does not exist on the backend.
        """

    @abstractmethod
    def write_bytes(self, path: str, content: bytes) -> None:
        """Write raw bytes to ``path``. Creates parent directories."""

    @abstractmethod
    def exists(self, path: str) -> bool:
        """Whether ``path`` exists.

        Discouraged — prefer ``try: storage.read_*() except FileNotFoundError:``.
        On cloud backends each ``exists`` call is a network round-trip
        (S3 ``HeadObject``); chained with a read it doubles the request count
        and the cost. Kept on the surface for code that genuinely needs
        existence-only semantics (e.g. skip-if-already-done short-circuits).
        """

    @abstractmethod
    def delete(self, path: str) -> None:
        """Delete ``path``. **Idempotent** — no error if the path is missing.

        S3's ``DeleteObject`` is already idempotent; ``LocalFileStorage`` uses
        ``unlink(missing_ok=True)`` to match.
        """

    @abstractmethod
    def delete_batch(self, paths: Iterable[str]) -> int:
        """Delete many paths in one (or few) API calls.

        Returns the number of paths successfully deleted. S3 supports up to
        1000 keys per ``DeleteObjects`` request; the S3 backend chunks
        automatically.
        """

    @abstractmethod
    def get_metadata(self, path: str) -> FileMetadata:
        """Single round-trip metadata for one path.

        Raises:
            FileNotFoundError: ``path`` does not exist on the backend.
        """

    @abstractmethod
    def list_files(self, prefix: str = "", pattern: Optional[str] = None) -> Iterator[FileMetadata]:
        """Yield ``FileMetadata`` for every file under ``prefix``.

        ``pattern`` is matched via :mod:`fnmatch` against (a) the full relative
        key and (b) the basename — so both ``"**/*.json"`` and ``"*.json"``
        work the way callers expect.

        Listings are lazy: backends stream pages and yield as they go, so
        a million-key bucket doesn't OOM the caller.
        """

    # --- Default-implemented helpers -----------------------------------------

    def get_size(self, path: str) -> int:
        """Convenience wrapper over ``get_metadata``."""
        return self.get_metadata(path).size

    def get_modified_time(self, path: str) -> float:
        """Convenience wrapper over ``get_metadata``. Returns Unix epoch seconds."""
        return self.get_metadata(path).modified_time.timestamp()

    def get_public_url(self, path: str, expires_in: int = 3600) -> Optional[str]:
        """Return a URL that bypasses the application for retrieval.

        Cloud backends return a presigned URL (e.g. S3 ``generate_presigned_url``)
        valid for ``expires_in`` seconds. Local returns ``None`` — callers must
        fall back to streaming the file through the web layer.

        ``expires_in`` is advisory for backends that don't honour it.
        """
        return None

    def ensure_directory(self, path: str) -> None:
        """No-op on cloud (S3 has no real directories); creates dir on local.

        Most write paths already mkdir parents internally. This is here for
        callers that want to assert a directory exists ahead of time.
        """
        # Default: no-op. ``LocalFileStorage`` overrides.

    @abstractmethod
    def upload_file(self, local_path: "Path | str", remote_path: str) -> None:
        """Persist a local filesystem file to the backend.

        Promoted to the ABC (vs. spec's original "S3-only extra" framing)
        because audio callers need this portably: a 100 MB MP3 cannot go
        through ``write_bytes`` without loading the whole file into memory.
        ``upload_file`` lets backends stream — boto3's transfer manager
        auto-multiparts above 8 MB; the local backend just moves the file.

        Implementations MUST NOT delete ``local_path``. Callers managing
        a tempfile own its lifecycle.
        """

    @abstractmethod
    def download_file(self, remote_path: str, local_path: "Path | str") -> None:
        """Pull a backend object to a specified local filesystem path.

        Mirror of ``upload_file``. Differs from ``get_local_path`` in that
        the caller chooses the destination — useful when the local path is
        already wired into downstream tooling (e.g. a transcriber's audio
        output directory). ``get_local_path`` mints a tempfile.

        Raises:
            FileNotFoundError: ``remote_path`` does not exist on the backend.
        """

    @abstractmethod
    def get_local_path(self, path: str) -> Path:
        """Return a real filesystem path for ``path``.

        On local backends this is the actual path (no copy). On cloud
        backends a temp file is downloaded; **the caller is responsible for
        cleanup**. Prefer ``local_copy`` (context manager) where possible.

        This is the seam for tools that require a filesystem path:
        :mod:`pydub`, :mod:`ffmpeg-python`, subprocess calls to ffmpeg /
        whisper / etc.

        Raises:
            FileNotFoundError: ``path`` does not exist on the backend.
        """

    @contextmanager
    def local_copy(self, path: str) -> Iterator[Path]:
        """Context-managed real-filesystem path.

        Cloud backends override this to clean up the temp file on exit.
        Local backend's default impl is a no-op wrapper around the real path —
        nothing to clean up.

        Usage::

            with storage.local_copy("audio/ep_abc.mp3") as p:
                segment = AudioSegment.from_file(p)

        Raises:
            FileNotFoundError: ``path`` does not exist on the backend.
        """
        yield self.get_local_path(path)
