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

"""Spec #35 — S3-backed ``FileStorage`` (the v1 cloud target).

``boto3`` is imported lazily inside ``__init__`` so installing ``thestill``
without the ``[s3]`` extra still imports this module without ImportError.
The error only fires when someone constructs ``S3FileStorage`` — the same
moment they need it.

Credentials follow the boto3 chain. In AWS production rely on EC2 instance
profile / ECS task role / IRSA — never bake explicit keys into images.
``endpoint_url`` enables LocalStack / MinIO / DigitalOcean Spaces for tests
or self-hosted S3-compatible deployments.
"""

from __future__ import annotations

import fnmatch
import os
import tempfile
from contextlib import contextmanager
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Iterable, Iterator, Optional

import structlog

from .base import FileMetadata, FileStorage, StorageError, _normalize_key

logger = structlog.get_logger(__name__)


# S3 ``DeleteObjects`` hard cap. The API rejects > 1000 keys per request;
# we chunk to this size in ``delete_batch``.
_DELETE_BATCH_SIZE = 1000

# Multipart threshold for high-level transfers. boto3's TransferManager
# already picks sensible defaults; we override only to keep ``upload_file``
# / ``download_file`` predictable across versions.
_MULTIPART_THRESHOLD_BYTES = 8 * 1024 * 1024


class S3FileStorage(FileStorage):
    """AWS S3 (or S3-compatible) storage backend.

    Optional ``endpoint_url`` swings the client to LocalStack / MinIO /
    DigitalOcean Spaces — in production on AWS, leave it ``None``.

    Optional ``kms_key_id`` switches server-side encryption from
    SSE-S3 (AES256, the bucket default) to SSE-KMS with the given customer
    managed key. Required only when compliance demands customer-managed
    keys; SSE-S3 is fine for the default case.
    """

    def __init__(
        self,
        bucket: str,
        region: str = "us-east-1",
        prefix: str = "",
        endpoint_url: Optional[str] = None,
        kms_key_id: Optional[str] = None,
        access_key_id: Optional[str] = None,
        secret_access_key: Optional[str] = None,
    ):
        # Lazy import — ``boto3`` is in the ``[s3]`` extra, not in base deps.
        # The hint mentions the extra by name so the error is actionable.
        try:
            import boto3
            import botocore.exceptions  # noqa: F401  (referenced in error mapping)
        except ImportError as exc:
            raise ImportError("S3FileStorage requires boto3. Install with `pip install thestill[s3]`.") from exc

        if not bucket:
            raise ValueError("S3FileStorage requires a non-empty bucket")

        self.bucket = bucket
        self.region = region
        # Normalise prefix to ``""`` or ``"foo/bar"`` — no leading slash,
        # exactly one trailing slash if present. Avoids the double-slash
        # bug class entirely.
        self.prefix = prefix.strip("/")
        self.endpoint_url = endpoint_url
        self.kms_key_id = kms_key_id

        client_kwargs: dict[str, Any] = {"region_name": region}
        if endpoint_url:
            client_kwargs["endpoint_url"] = endpoint_url
        # Explicit credentials are honoured (local dev), but boto3's chain
        # is the production path — IAM role on EC2/ECS/EKS.
        if access_key_id and secret_access_key:
            client_kwargs["aws_access_key_id"] = access_key_id
            client_kwargs["aws_secret_access_key"] = secret_access_key

        self._client = boto3.client("s3", **client_kwargs)
        self._boto3 = boto3  # held only for transfer-manager access in upload/download_file
        self._botocore_exceptions = __import__("botocore.exceptions", fromlist=["ClientError"])
        # Catch ``ClientError`` (4xx/5xx from S3) AND ``BotoCoreError``
        # (network/endpoint/timeout, parser failures — anything botocore
        # raises that isn't a server response). Without ``BotoCoreError``,
        # transient network failures escape unwrapped and bypass the
        # ``StorageError`` → ``TransientError`` retry hook described by
        # the abstraction.
        self._wrapped_errors = (
            self._botocore_exceptions.ClientError,
            self._botocore_exceptions.BotoCoreError,
        )
        # Built once and reused by ``get_local_path`` / ``upload_file`` /
        # ``download_file`` — three identical inline constructions otherwise.
        self._transfer_config = boto3.s3.transfer.TransferConfig(multipart_threshold=_MULTIPART_THRESHOLD_BYTES)

    # --- Internal helpers ----------------------------------------------------

    def _key(self, path: str) -> str:
        """Map a relative path to a full S3 key.

        Joins the configured ``prefix`` with the caller's path; both halves
        are normalised so we never emit double slashes or a leading slash.
        """
        normalized = _normalize_key(path)
        if self.prefix:
            return f"{self.prefix}/{normalized}" if normalized else self.prefix
        return normalized

    def _from_key(self, key: str) -> str:
        """Inverse of ``_key`` — strip the prefix so callers see the same
        relative paths they wrote."""
        if not self.prefix:
            return key
        marker = f"{self.prefix}/"
        if key.startswith(marker):
            return key[len(marker) :]
        # Listing should never return keys outside the prefix — ``list_files``
        # restricts the paginator with a trailing-slash prefix. If a stray
        # key slips through (e.g. a zero-byte directory marker keyed exactly
        # at ``self.prefix``), return ``""`` so callers can filter it out
        # rather than receive the raw S3 key as a "relative" path.
        return ""

    def _put_kwargs(self) -> dict[str, Any]:
        """Per-write kwargs — encryption headers go here.

        Default: SSE-S3 (AES256). If ``kms_key_id`` is set, switch to
        SSE-KMS with that key. Bucket default encryption belts-and-braces
        this on the AWS side, but explicit headers make intent visible
        in CloudTrail.
        """
        if self.kms_key_id:
            return {"ServerSideEncryption": "aws:kms", "SSEKMSKeyId": self.kms_key_id}
        return {"ServerSideEncryption": "AES256"}

    def _is_not_found(self, exc: Exception) -> bool:
        """Whether a boto3 error is a not-found.

        S3 returns ``NoSuchKey`` on ``GetObject`` and ``404`` on ``HeadObject``
        for the same missing-object condition. We map both to
        ``FileNotFoundError`` so callers handle one exception type.
        Only ``ClientError`` carries an ``Error.Code``; ``BotoCoreError``
        subclasses (network failures) never indicate a not-found.
        """
        ClientError = self._botocore_exceptions.ClientError
        if not isinstance(exc, ClientError):
            return False
        err = exc.response.get("Error", {}) if hasattr(exc, "response") else {}
        code = err.get("Code", "")
        return code in {"NoSuchKey", "404", "NotFound"}

    # --- FileStorage surface -------------------------------------------------

    def read_bytes(self, path: str) -> bytes:
        key = self._key(path)
        try:
            response = self._client.get_object(Bucket=self.bucket, Key=key)
        except self._wrapped_errors as exc:
            if self._is_not_found(exc):
                raise FileNotFoundError(path) from None
            raise StorageError(f"s3 get_object failed for {key!r}: {exc}") from exc
        return response["Body"].read()

    def read_text(self, path: str, *, encoding: str = "utf-8") -> str:
        return self.read_bytes(path).decode(encoding)

    def write_bytes(self, path: str, content: bytes) -> None:
        key = self._key(path)
        try:
            self._client.put_object(Bucket=self.bucket, Key=key, Body=content, **self._put_kwargs())
        except self._wrapped_errors as exc:
            raise StorageError(f"s3 put_object failed for {key!r}: {exc}") from exc

    def write_text(self, path: str, content: str, *, encoding: str = "utf-8") -> None:
        self.write_bytes(path, content.encode(encoding))

    def exists(self, path: str) -> bool:
        key = self._key(path)
        try:
            self._client.head_object(Bucket=self.bucket, Key=key)
            return True
        except self._wrapped_errors as exc:
            if self._is_not_found(exc):
                return False
            raise StorageError(f"s3 head_object failed for {key!r}: {exc}") from exc

    def delete(self, path: str) -> None:
        # S3 ``DeleteObject`` is idempotent — succeeds on missing keys.
        key = self._key(path)
        try:
            self._client.delete_object(Bucket=self.bucket, Key=key)
        except self._wrapped_errors as exc:
            raise StorageError(f"s3 delete_object failed for {key!r}: {exc}") from exc

    def delete_batch(self, paths: Iterable[str]) -> int:
        # Realise into a list so we can chunk. Callers that pass large
        # generators are explicitly trading memory for batch efficiency.
        keys: List[str] = [self._key(p) for p in paths]
        if not keys:
            return 0

        total_deleted = 0
        for i in range(0, len(keys), _DELETE_BATCH_SIZE):
            chunk = keys[i : i + _DELETE_BATCH_SIZE]
            try:
                response = self._client.delete_objects(
                    Bucket=self.bucket,
                    Delete={"Objects": [{"Key": k} for k in chunk], "Quiet": False},
                )
            except self._wrapped_errors as exc:
                raise StorageError(f"s3 delete_objects failed: {exc}") from exc
            total_deleted += len(response.get("Deleted", []))
            errors = response.get("Errors", [])
            if errors:
                # Log but don't raise — partial batch failures are expected
                # under heavy concurrency; the caller's retry layer
                # (spec #16 DLQ) handles it.
                logger.warning("s3_delete_batch_partial_failure", bucket=self.bucket, errors=errors)
        return total_deleted

    def get_metadata(self, path: str) -> FileMetadata:
        key = self._key(path)
        try:
            head = self._client.head_object(Bucket=self.bucket, Key=key)
        except self._wrapped_errors as exc:
            if self._is_not_found(exc):
                raise FileNotFoundError(path) from None
            raise StorageError(f"s3 head_object failed for {key!r}: {exc}") from exc

        last_modified = head["LastModified"]
        # boto3 returns tz-aware datetimes already; normalise to UTC
        # defensively in case a future version changes that.
        if last_modified.tzinfo is None:
            last_modified = last_modified.replace(tzinfo=timezone.utc)

        return FileMetadata(
            # ``_from_key`` round-trips through the same normalisation pipeline
            # ``list_files`` uses, so both methods return identically-shaped
            # paths.
            path=self._from_key(key),
            size=head["ContentLength"],
            modified_time=last_modified.astimezone(timezone.utc),
            content_type=head.get("ContentType"),
            etag=head.get("ETag"),
        )

    def list_files(self, prefix: str = "", pattern: Optional[str] = None) -> Iterator[FileMetadata]:
        # Build the full S3 prefix including the backend prefix, and ALWAYS
        # terminate with ``/`` when non-empty. S3 prefix matching is a raw
        # string prefix — without the slash, ``Prefix="prod"`` matches both
        # ``prod/x`` AND ``production/x``, leaking sibling-prefix keys.
        full_prefix = self._key(prefix) if prefix else self.prefix
        if full_prefix and not full_prefix.endswith("/"):
            full_prefix = full_prefix + "/"

        paginator = self._client.get_paginator("list_objects_v2")
        page_iter = paginator.paginate(Bucket=self.bucket, Prefix=full_prefix)

        for page in page_iter:
            for obj in page.get("Contents", []) or []:
                relative = self._from_key(obj["Key"])
                # ``_from_key`` returns ``""`` for stray keys outside the
                # prefix (e.g. zero-byte directory markers). Skip them so
                # callers never see a phantom entry.
                if not relative:
                    continue
                if pattern is not None:
                    basename = relative.rsplit("/", 1)[-1]
                    if not (fnmatch.fnmatch(relative, pattern) or fnmatch.fnmatch(basename, pattern)):
                        continue
                last_modified = obj["LastModified"]
                if last_modified.tzinfo is None:
                    last_modified = last_modified.replace(tzinfo=timezone.utc)
                yield FileMetadata(
                    path=relative,
                    size=obj["Size"],
                    modified_time=last_modified.astimezone(timezone.utc),
                    content_type=None,  # list_objects_v2 doesn't return ContentType
                    etag=obj.get("ETag"),
                )

    def get_public_url(self, path: str, expires_in: int = 3600) -> Optional[str]:
        # Presigned URL — caller can hand this to the browser for direct
        # download without proxying through the application.
        key = self._key(path)
        try:
            return self._client.generate_presigned_url(
                "get_object",
                Params={"Bucket": self.bucket, "Key": key},
                ExpiresIn=expires_in,
            )
        except self._wrapped_errors as exc:
            raise StorageError(f"s3 generate_presigned_url failed for {key!r}: {exc}") from exc

    # ``ensure_directory`` is not overridden — the base class default is
    # already a no-op, which is exactly the right behaviour for S3 (no real
    # directories, prefixes exist implicitly).

    def get_local_path(self, path: str) -> Path:
        # Download to a tempfile. **Caller is responsible for cleanup** —
        # prefer ``local_copy`` (auto-cleanup) where possible.
        key = self._key(path)
        # Suffix preserved so tools that pick decoder based on extension
        # (pydub/ffmpeg/whisper) see ``.mp3`` / ``.wav`` etc.
        suffix = Path(path).suffix
        tmp = tempfile.NamedTemporaryFile(delete=False, suffix=suffix, prefix="thestill_s3_")
        tmp.close()
        try:
            self._client.download_file(
                Bucket=self.bucket,
                Key=key,
                Filename=tmp.name,
                Config=self._transfer_config,
            )
        except self._wrapped_errors as exc:
            # Cleanup in ``except`` rather than ``finally`` so the file
            # stays on disk on success. ``self._wrapped_errors`` now covers
            # both ``ClientError`` (server-side responses) and
            # ``BotoCoreError`` (network/endpoint/timeout), so the broad
            # ``except Exception`` clause that used to follow is no longer
            # needed for cleanup correctness.
            os.unlink(tmp.name)
            if self._is_not_found(exc):
                raise FileNotFoundError(path) from None
            raise StorageError(f"s3 download_file failed for {key!r}: {exc}") from exc
        return Path(tmp.name)

    @contextmanager
    def local_copy(self, path: str) -> Iterator[Path]:
        """Auto-cleaning override of the base context manager."""
        tmp_path = self.get_local_path(path)
        try:
            yield tmp_path
        finally:
            try:
                os.unlink(tmp_path)
            except OSError as exc:
                # Cleanup failure shouldn't fail the operation — log loud
                # so disk leaks are visible.
                logger.warning("s3_local_copy_cleanup_failed", path=str(tmp_path), error=str(exc))

    def upload_file(self, local_path: Path | str, remote_path: str) -> None:
        """High-level multipart-aware upload from a local file.

        boto3's TransferManager auto-multiparts files above the 8 MB
        threshold, which matters for original audio (50–200 MB per episode).
        On the ABC because audio callers need this portably — ``write_bytes``
        would otherwise force loading the whole file into memory.
        """
        key = self._key(remote_path)
        try:
            self._client.upload_file(
                Filename=str(local_path),
                Bucket=self.bucket,
                Key=key,
                ExtraArgs=self._put_kwargs(),
                Config=self._transfer_config,
            )
        except self._wrapped_errors as exc:
            raise StorageError(f"s3 upload_file failed for {key!r}: {exc}") from exc

    def download_file(self, remote_path: str, local_path: Path | str) -> None:
        """High-level multipart-aware download to an explicit local path.

        Mirror of ``upload_file``. Use this when the caller already has a
        destination path in mind (vs. ``get_local_path`` which mints a tempfile).
        """
        key = self._key(remote_path)
        try:
            self._client.download_file(
                Bucket=self.bucket,
                Key=key,
                Filename=str(local_path),
                Config=self._transfer_config,
            )
        except self._wrapped_errors as exc:
            if self._is_not_found(exc):
                raise FileNotFoundError(remote_path) from None
            raise StorageError(f"s3 download_file failed for {key!r}: {exc}") from exc
