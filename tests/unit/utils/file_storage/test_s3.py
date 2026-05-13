# Copyright 2025-2026 Thestill
#
# Licensed under the Apache License, Version 2.0 (the "License");
# you may not use this file except in compliance with the License.
# You may obtain a copy of the License at
#
#     http://www.apache.org/licenses/LICENSE-2.0

"""Spec #35 — S3FileStorage-specific behaviour.

Covers things that don't apply to local:
- ``boto3`` lazy-import error message when the extra isn't installed
- Bucket prefix join logic
- SSE-S3 default encryption header on writes
- SSE-KMS encryption header when ``kms_key_id`` is set
- Presigned URL generation
- ``delete_batch`` chunking across the 1000-key boundary
- ``local_copy`` cleans up the temp file on exit
- ``upload_file`` / ``download_file`` extras
"""

from __future__ import annotations

import sys
from unittest.mock import patch

import boto3
import pytest
from moto import mock_aws

from thestill.utils.file_storage.s3 import S3FileStorage

_BUCKET = "thestill-test-bucket"
_REGION = "us-east-1"


class TestLazyImportError:
    def test_actionable_error_when_boto3_missing(self, monkeypatch):
        # Simulate boto3 not being installed by removing it from sys.modules
        # and from the import system. Forces the lazy `import boto3` inside
        # S3FileStorage.__init__ to fail.
        for name in list(sys.modules):
            if name == "boto3" or name.startswith("boto3."):
                monkeypatch.delitem(sys.modules, name, raising=False)

        original_find_spec = __import__("importlib").util.find_spec

        def fake_find_spec(name, *args, **kwargs):
            if name == "boto3":
                return None
            return original_find_spec(name, *args, **kwargs)

        with patch("importlib.util.find_spec", side_effect=fake_find_spec):
            # The simpler approach — patch __builtins__.__import__ to raise on
            # 'boto3' — also works. Using a sentinel meta-path import block:
            import builtins

            real_import = builtins.__import__

            def blocking_import(name, *args, **kwargs):
                if name == "boto3" or name.startswith("boto3."):
                    raise ImportError("boto3 simulated missing")
                return real_import(name, *args, **kwargs)

            with patch.object(builtins, "__import__", side_effect=blocking_import):
                with pytest.raises(ImportError, match=r"pip install thestill\[s3\]"):
                    S3FileStorage(bucket=_BUCKET, region=_REGION)


class TestPrefixJoin:
    @mock_aws
    def test_writes_under_prefix(self):
        # When prefix="prod", writing "audio/x.mp3" produces key "prod/audio/x.mp3"
        # in S3. The caller still sees "audio/x.mp3" — the prefix is hidden.
        boto3.client("s3", region_name=_REGION).create_bucket(Bucket=_BUCKET)
        storage = S3FileStorage(bucket=_BUCKET, region=_REGION, prefix="prod")

        storage.write_text("audio/x.mp3", "fake")

        raw = boto3.client("s3", region_name=_REGION)
        resp = raw.list_objects_v2(Bucket=_BUCKET)
        keys = [obj["Key"] for obj in resp.get("Contents", [])]
        assert keys == ["prod/audio/x.mp3"]
        # And the read goes back through the prefix transparently
        assert storage.read_text("audio/x.mp3") == "fake"

    @mock_aws
    def test_list_files_strips_prefix(self):
        # Listing returns relative paths (sans prefix) — callers see the
        # same shape they wrote.
        boto3.client("s3", region_name=_REGION).create_bucket(Bucket=_BUCKET)
        storage = S3FileStorage(bucket=_BUCKET, region=_REGION, prefix="prod")
        storage.write_text("a.txt", "1")
        storage.write_text("nested/b.txt", "2")

        paths = sorted(m.path for m in storage.list_files())
        assert paths == ["a.txt", "nested/b.txt"]

    @mock_aws
    def test_prefix_with_trailing_slash_normalised(self):
        # Operator-set prefix with a trailing slash — must not produce
        # double slashes in the resulting key.
        boto3.client("s3", region_name=_REGION).create_bucket(Bucket=_BUCKET)
        storage = S3FileStorage(bucket=_BUCKET, region=_REGION, prefix="prod/")
        storage.write_text("x.txt", "x")
        raw = boto3.client("s3", region_name=_REGION)
        resp = raw.list_objects_v2(Bucket=_BUCKET)
        keys = [obj["Key"] for obj in resp.get("Contents", [])]
        assert keys == ["prod/x.txt"]

    @mock_aws
    def test_list_does_not_leak_sibling_prefix(self):
        # Regression for the prefix false-positive bug: S3 prefix matching
        # is a raw string match, so prefix="prod" without a trailing slash
        # would match "production/x.txt" as well. Confirm that two
        # storages with prefix="prod" and prefix="production" each see ONLY
        # their own keys via list_files.
        boto3.client("s3", region_name=_REGION).create_bucket(Bucket=_BUCKET)
        raw = boto3.client("s3", region_name=_REGION)
        raw.put_object(Bucket=_BUCKET, Key="prod/a.txt", Body=b"a")
        raw.put_object(Bucket=_BUCKET, Key="production/b.txt", Body=b"b")

        prod = S3FileStorage(bucket=_BUCKET, region=_REGION, prefix="prod")
        production = S3FileStorage(bucket=_BUCKET, region=_REGION, prefix="production")

        assert sorted(m.path for m in prod.list_files()) == ["a.txt"]
        assert sorted(m.path for m in production.list_files()) == ["b.txt"]


class TestServerSideEncryption:
    @mock_aws
    def test_sse_s3_aes256_by_default(self):
        boto3.client("s3", region_name=_REGION).create_bucket(Bucket=_BUCKET)
        storage = S3FileStorage(bucket=_BUCKET, region=_REGION)
        storage.write_text("x.txt", "x")
        raw = boto3.client("s3", region_name=_REGION)
        head = raw.head_object(Bucket=_BUCKET, Key="x.txt")
        # moto echoes the SSE header we sent; on real S3 the bucket default
        # encryption would also stamp this, but here we're asserting our
        # explicit header.
        assert head.get("ServerSideEncryption") == "AES256"

    @mock_aws
    def test_sse_kms_when_key_set(self):
        boto3.client("s3", region_name=_REGION).create_bucket(Bucket=_BUCKET)
        storage = S3FileStorage(
            bucket=_BUCKET,
            region=_REGION,
            kms_key_id="arn:aws:kms:us-east-1:111122223333:key/abcd-efgh",
        )
        storage.write_text("x.txt", "x")
        raw = boto3.client("s3", region_name=_REGION)
        head = raw.head_object(Bucket=_BUCKET, Key="x.txt")
        assert head.get("ServerSideEncryption") == "aws:kms"
        assert head.get("SSEKMSKeyId") == "arn:aws:kms:us-east-1:111122223333:key/abcd-efgh"


class TestPresignedUrl:
    @mock_aws
    def test_get_public_url_returns_presigned(self):
        boto3.client("s3", region_name=_REGION).create_bucket(Bucket=_BUCKET)
        storage = S3FileStorage(bucket=_BUCKET, region=_REGION)
        storage.write_text("audio/x.mp3", "fake")
        url = storage.get_public_url("audio/x.mp3", expires_in=600)
        assert url is not None
        assert _BUCKET in url
        # Presigned URLs include AWS signing query params
        assert "X-Amz-Signature" in url or "Signature" in url

    @mock_aws
    def test_get_public_url_with_prefix(self):
        # The presigned URL must reference the FULL key (prefix + path),
        # not just the relative path.
        boto3.client("s3", region_name=_REGION).create_bucket(Bucket=_BUCKET)
        storage = S3FileStorage(bucket=_BUCKET, region=_REGION, prefix="prod")
        url = storage.get_public_url("audio/x.mp3")
        assert "prod/audio/x.mp3" in url or "prod%2Faudio%2Fx.mp3" in url


class TestDeleteBatchChunking:
    @mock_aws
    def test_chunks_above_1000_keys(self):
        # S3 hard caps DeleteObjects at 1000 keys per request. The backend
        # must transparently chunk — caller doesn't care.
        boto3.client("s3", region_name=_REGION).create_bucket(Bucket=_BUCKET)
        storage = S3FileStorage(bucket=_BUCKET, region=_REGION)

        keys = [f"k/{i}.txt" for i in range(2500)]
        for k in keys:
            storage.write_text(k, "x")

        deleted = storage.delete_batch(keys)
        # moto reports all keys deleted as "successful" in the chunked response
        assert deleted == 2500
        # And confirm nothing's left
        assert list(storage.list_files()) == []


class TestLocalCopyCleanup:
    @mock_aws
    def test_local_copy_unlinks_tempfile_on_exit(self):
        boto3.client("s3", region_name=_REGION).create_bucket(Bucket=_BUCKET)
        storage = S3FileStorage(bucket=_BUCKET, region=_REGION)
        storage.write_text("audio/x.mp3", "fake")

        with storage.local_copy("audio/x.mp3") as p:
            assert p.is_file()
            captured = p
        # After exit the temp file should be gone
        assert not captured.exists()

    @mock_aws
    def test_local_copy_extension_preserved(self):
        # Critical for pydub/ffmpeg — picks decoder from file extension.
        boto3.client("s3", region_name=_REGION).create_bucket(Bucket=_BUCKET)
        storage = S3FileStorage(bucket=_BUCKET, region=_REGION)
        storage.write_bytes("audio/clip.mp3", b"\xff\xfb\x90\x00")
        with storage.local_copy("audio/clip.mp3") as p:
            assert p.suffix == ".mp3"

    @mock_aws
    def test_get_local_path_caller_managed(self):
        # Without the context manager the caller owns cleanup. Verify the
        # tempfile exists after get_local_path returns — the test cleans
        # it up explicitly.
        boto3.client("s3", region_name=_REGION).create_bucket(Bucket=_BUCKET)
        storage = S3FileStorage(bucket=_BUCKET, region=_REGION)
        storage.write_text("x.txt", "x")
        p = storage.get_local_path("x.txt")
        try:
            assert p.is_file()
            assert p.read_text() == "x"
        finally:
            p.unlink(missing_ok=True)


class TestUploadDownloadExtras:
    @mock_aws
    def test_upload_file_round_trip(self, tmp_path):
        boto3.client("s3", region_name=_REGION).create_bucket(Bucket=_BUCKET)
        storage = S3FileStorage(bucket=_BUCKET, region=_REGION)
        # Write a fake "large" file locally and upload via the high-level
        # transfer manager
        local = tmp_path / "big.bin"
        local.write_bytes(b"x" * 1024)  # well under multipart threshold

        storage.upload_file(local, "uploads/big.bin")

        assert storage.read_bytes("uploads/big.bin") == b"x" * 1024

    @mock_aws
    def test_download_file_round_trip(self, tmp_path):
        boto3.client("s3", region_name=_REGION).create_bucket(Bucket=_BUCKET)
        storage = S3FileStorage(bucket=_BUCKET, region=_REGION)
        storage.write_bytes("uploads/big.bin", b"x" * 1024)

        target = tmp_path / "download.bin"
        storage.download_file("uploads/big.bin", target)
        assert target.read_bytes() == b"x" * 1024

    @mock_aws
    def test_download_file_missing_raises_filenotfound(self, tmp_path):
        boto3.client("s3", region_name=_REGION).create_bucket(Bucket=_BUCKET)
        storage = S3FileStorage(bucket=_BUCKET, region=_REGION)
        target = tmp_path / "x.bin"
        with pytest.raises(FileNotFoundError):
            storage.download_file("never-uploaded.bin", target)


class TestEmptyBucketGuard:
    def test_empty_bucket_rejected(self):
        with pytest.raises(ValueError, match="non-empty bucket"):
            S3FileStorage(bucket="", region=_REGION)


class TestBotoCoreErrorWrapping:
    """Spec #35 — transient network failures (``BotoCoreError``) must surface
    as ``StorageError`` (a ``TransientError``) so the task worker's retry
    layer kicks in. Without this, network blips escape unwrapped and bypass
    the abstraction's retry semantics.
    """

    @mock_aws
    def test_endpoint_connection_error_wrapped_as_storage_error(self):
        from botocore.exceptions import EndpointConnectionError

        from thestill.utils.exceptions import TransientError
        from thestill.utils.file_storage.base import StorageError

        boto3.client("s3", region_name=_REGION).create_bucket(Bucket=_BUCKET)
        storage = S3FileStorage(bucket=_BUCKET, region=_REGION)

        # Patch the internal client so the next call raises a network-side
        # botocore exception. This is the exact failure shape that bypassed
        # the wrap before (reviewer P2).
        def _explode(*_args, **_kwargs):
            raise EndpointConnectionError(endpoint_url="https://s3.example.invalid/")

        with patch.object(storage._client, "put_object", side_effect=_explode):
            with pytest.raises(StorageError) as exc_info:
                storage.write_bytes("x.txt", b"x")

        # StorageError extends TransientError so the worker's retry/DLQ
        # layer treats S3 network failures as transient.
        assert isinstance(exc_info.value, TransientError)

    @mock_aws
    def test_read_failure_with_botocore_error_surfaces_as_storage_error(self):
        from botocore.exceptions import ConnectionClosedError

        from thestill.utils.file_storage.base import StorageError

        boto3.client("s3", region_name=_REGION).create_bucket(Bucket=_BUCKET)
        storage = S3FileStorage(bucket=_BUCKET, region=_REGION)
        storage.write_text("x.txt", "v")

        def _explode(*_args, **_kwargs):
            raise ConnectionClosedError(endpoint_url="https://s3.example.invalid/")

        with patch.object(storage._client, "get_object", side_effect=_explode):
            with pytest.raises(StorageError):
                storage.read_bytes("x.txt")
