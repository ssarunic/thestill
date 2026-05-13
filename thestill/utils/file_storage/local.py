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

"""Spec #35 — local-disk ``FileStorage`` backend.

Adapts the local filesystem to the cloud-shaped ``FileStorage`` contract:
idempotent delete, single-call metadata, forward-slash paths, and a
``_resolve`` step that mirrors :class:`thestill.utils.path_manager.PathManager`'s
``_assert_inside_root`` so directly-constructed ``LocalFileStorage`` instances
get the same traversal-resistance as code going through ``PathManager``.
"""

from __future__ import annotations

import fnmatch
import mimetypes
import shutil
from datetime import datetime, timezone
from pathlib import Path
from typing import Iterable, Iterator, Optional

import structlog

from .base import FileMetadata, FileStorage, _normalize_key

logger = structlog.get_logger(__name__)


class LocalFileStorage(FileStorage):
    """Filesystem-backed storage anchored at ``base_path``.

    Every path passed in is joined with ``base_path``, resolved, and
    checked to stay under the resolved root — same guarantees as
    :meth:`PathManager._assert_inside_root` but enforced at the
    storage-layer boundary, so callers constructing ``LocalFileStorage``
    directly (e.g. tests) still get the traversal guard.
    """

    def __init__(self, base_path: str):
        # Resolve once at construction. ``Path.resolve()`` consults the FS
        # to expand ``..`` segments and follow any symlinks in the prefix —
        # caching means we don't pay that on every operation. The data root
        # is not expected to move under us; the same assumption ``PathManager``
        # already makes.
        self.base_path = Path(base_path)
        self._root_resolved = self.base_path.resolve()
        # The data root itself is owned by ``Config._ensure_directories`` (or
        # the test fixture that constructs the backend directly). Per-write
        # ``mkdir(parents=True, exist_ok=True)`` handles any nested subdirs.

    # --- Internal helpers ----------------------------------------------------

    def _resolve(self, path: str) -> Path:
        """Join + resolve + assert-inside-root.

        Defence-in-depth against direct construction without
        :class:`PathManager` having validated the input. Spec #25's guard
        lives in ``PathManager``; this is the storage-layer mirror.
        """
        candidate = self.base_path / _normalize_key(path)
        try:
            resolved = candidate.resolve()
        except (OSError, RuntimeError) as exc:
            raise ValueError(f"could not resolve path {path!r}: {exc}") from exc
        if not resolved.is_relative_to(self._root_resolved):
            raise ValueError(f"path {path!r} (resolves to {resolved!r}) escapes storage root {self._root_resolved!r}")
        return candidate

    @staticmethod
    def _to_relative_key(absolute: Path, root: Path) -> str:
        """Build the forward-slash relative key used in ``FileMetadata.path``."""
        return absolute.relative_to(root).as_posix()

    def _metadata_from_path(self, absolute: Path) -> FileMetadata:
        stat = absolute.stat()
        guess_type, _ = mimetypes.guess_type(absolute.name)
        return FileMetadata(
            path=self._to_relative_key(absolute, self._root_resolved),
            size=stat.st_size,
            modified_time=datetime.fromtimestamp(stat.st_mtime, tz=timezone.utc),
            content_type=guess_type,
            # Local has no etag concept; leave ``None``.
        )

    # --- FileStorage surface -------------------------------------------------

    def read_text(self, path: str, *, encoding: str = "utf-8") -> str:
        target = self._resolve(path)
        try:
            return target.read_text(encoding=encoding)
        except FileNotFoundError:
            raise FileNotFoundError(path) from None

    def write_text(self, path: str, content: str, *, encoding: str = "utf-8") -> None:
        target = self._resolve(path)
        target.parent.mkdir(parents=True, exist_ok=True)
        target.write_text(content, encoding=encoding)

    def read_bytes(self, path: str) -> bytes:
        target = self._resolve(path)
        try:
            return target.read_bytes()
        except FileNotFoundError:
            raise FileNotFoundError(path) from None

    def write_bytes(self, path: str, content: bytes) -> None:
        target = self._resolve(path)
        target.parent.mkdir(parents=True, exist_ok=True)
        target.write_bytes(content)

    def exists(self, path: str) -> bool:
        return self._resolve(path).is_file()

    def delete(self, path: str) -> None:
        # ``unlink(missing_ok=True)`` mirrors S3's idempotent ``DeleteObject``.
        self._resolve(path).unlink(missing_ok=True)

    def delete_batch(self, paths: Iterable[str]) -> int:
        # Return ``len(paths)`` to match S3's idempotent semantics — both
        # backends count "paths processed", not "paths that existed". Without
        # this alignment, callers using the return value as an audit number
        # would see backend-dependent counts (S3 says 47, local says 27 if
        # 20 were already gone). The contract suite enforces this match.
        count = 0
        for p in paths:
            self._resolve(p).unlink(missing_ok=True)
            count += 1
        return count

    def get_metadata(self, path: str) -> FileMetadata:
        target = self._resolve(path)
        if not target.is_file():
            raise FileNotFoundError(path)
        return self._metadata_from_path(target)

    def list_files(self, prefix: str = "", pattern: Optional[str] = None) -> Iterator[FileMetadata]:
        # Walk from the RESOLVED root, not ``self.base_path``. On macOS
        # ``base_path = /tmp/x`` resolves to ``/private/tmp/x``; iterating
        # ``base_path.rglob`` yields unresolved paths and then
        # ``relative_to(self._root_resolved)`` raises. Anchoring on the
        # resolved root keeps both sides aligned.
        if prefix:
            root = self._resolve(prefix).resolve()
        else:
            root = self._root_resolved
        if not root.exists():
            return
        for absolute in root.rglob("*"):
            if not absolute.is_file():
                continue
            rel = self._to_relative_key(absolute, self._root_resolved)
            if pattern is not None and not (fnmatch.fnmatch(rel, pattern) or fnmatch.fnmatch(absolute.name, pattern)):
                continue
            yield self._metadata_from_path(absolute)

    def get_public_url(self, path: str, expires_in: int = 3600) -> Optional[str]:
        # Local backend has no presigned URL story — callers fall back to
        # streaming through the web layer.
        return None

    def ensure_directory(self, path: str) -> None:
        # Real mkdir for local; S3 has no notion of directories.
        target = self._resolve(path)
        target.mkdir(parents=True, exist_ok=True)

    def get_local_path(self, path: str) -> Path:
        # Local backend returns the actual filesystem path — no temp file.
        target = self._resolve(path)
        if not target.is_file():
            raise FileNotFoundError(path)
        return target

    def upload_file(self, local_path: Path | str, remote_path: str) -> None:
        # ``shutil.copy`` preserves mode bits and works across filesystems.
        # If ``local_path`` is already the resolved destination (caller
        # streamed straight to the real path), copy is a no-op self-move
        # so we short-circuit. Audio downloader uses this pattern.
        target = self._resolve(remote_path)
        source = Path(local_path)
        if source.resolve() == target.resolve():
            return
        target.parent.mkdir(parents=True, exist_ok=True)
        shutil.copy(source, target)

    def download_file(self, remote_path: str, local_path: Path | str) -> None:
        source = self._resolve(remote_path)
        if not source.is_file():
            raise FileNotFoundError(remote_path)
        destination = Path(local_path)
        if source.resolve() == destination.resolve():
            return
        destination.parent.mkdir(parents=True, exist_ok=True)
        shutil.copy(source, destination)

    # ``local_copy`` inherits the base impl — it's a yield-only wrapper over
    # ``get_local_path``, which is the correct semantics for local (nothing
    # to clean up). S3 overrides it to clean up the temp file.
