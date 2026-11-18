# file_storage

## Overview

Pluggable storage backends (spec #35). Every caller reads and writes through
the cloud-shaped `FileStorage` ABC, so the underlying store can be local disk
or AWS S3 (or an S3-compatible endpoint) without touching call sites.
`make_storage` picks the backend from `Config` at startup. `LocalFileStorage`
adapts the local filesystem to S3 semantics, rather than the other way round.

## Interface

- `__init__.py` - re-exports `FileMetadata`, `FileStorage`, `LocalFileStorage`,
  `StorageError`, `make_storage`. `S3FileStorage` is deliberately **not**
  re-exported; import it from `thestill.utils.file_storage.s3`.
- `base.py:FileStorage` - abstract backend. Abstract methods: `read_text`,
  `write_text`, `read_bytes`, `write_bytes`, `exists`, `delete`,
  `delete_batch`, `get_metadata`, `list_files(prefix, pattern)`,
  `upload_file(local_path, remote_path)`, `download_file(remote_path, local_path)`,
  `get_local_path`. Default helpers: `get_size`, `get_modified_time`,
  `get_public_url` (returns `None`), `ensure_directory` (no-op), and
  `local_copy` (context manager that yields `get_local_path`).
- `base.py:FileMetadata` - frozen dataclass: `path`, `size`, `modified_time`
  (tz-aware UTC), `content_type`, `etag`, and the `modified_timestamp` property.
- `base.py:StorageError` - backend failure other than not-found. Subclasses
  `thestill.utils.exceptions.TransientError`.
- `base.py:_normalize_key` - shared key normaliser that both backends use.
- `factory.py:make_storage(config)` - returns `LocalFileStorage(config.storage_path)`
  or `S3FileStorage(...)` built from `config.s3_bucket`, `s3_region`,
  `s3_prefix`, `s3_endpoint_url`, `s3_kms_key_id`.
- `local.py:LocalFileStorage(base_path)` - filesystem backend anchored at
  `base_path`.
- `s3.py:S3FileStorage(bucket, region, prefix, endpoint_url, kms_key_id, access_key_id, secret_access_key)` -
  S3 backend. It overrides `get_public_url` (presigned `get_object` URL) and
  `local_copy` (deletes its temp file on exit).

## Invariants

- Keys are forward-slash strings with no leading slash. Backslashes are
  rewritten and a non-`str` path raises `TypeError` (`base.py:_normalize_key`).
- A missing object raises the stdlib `FileNotFoundError` on both backends:
  `local.py:LocalFileStorage.read_text`, `read_bytes`, `get_metadata`,
  `get_local_path` and `download_file`. On S3, `s3.py:S3FileStorage._is_not_found`
  maps `NoSuchKey`, `404` and `NotFound` to it.
- Other S3 errors (`ClientError` and `BotoCoreError`) are wrapped in
  `StorageError`, so the task worker's retry layer can retry them as
  `TransientError` (`s3.py:S3FileStorage.__init__` `_wrapped_errors`).
- `delete` is idempotent: `LocalFileStorage.delete` uses
  `unlink(missing_ok=True)`, and S3 `delete_object` already behaves this way.
- `local.py:LocalFileStorage.delete_batch` returns the number of paths
  *processed*. `s3.py:S3FileStorage.delete_batch` returns the length of
  `Deleted`, sending at most 1000 keys per request (`_DELETE_BATCH_SIZE`).
- Every path must resolve inside the storage root; a path that escapes it
  raises `ValueError` (`local.py:LocalFileStorage._resolve`). This mirrors
  `PathManager._assert_inside_root`.
- `list_files` builds `FileMetadata` from the listing itself, with no
  per-entry `get_metadata` call. `pattern` is matched with fnmatch against
  both the relative key and the basename (`local.py:LocalFileStorage.list_files`,
  `s3.py:S3FileStorage.list_files`).
- The S3 list prefix always ends in `/`, so `prod` does not match
  `production/` (`s3.py:S3FileStorage.list_files`). Keys outside the prefix
  map to `""` and are skipped (`s3.py:S3FileStorage._from_key`).
- `upload_file` must not delete `local_path` (`base.py:FileStorage.upload_file`).
  When the source and destination are the same file, the local backend
  short-circuits (`local.py:LocalFileStorage.upload_file`).
- `make_storage` fails fast. `STORAGE_BACKEND=s3` without `S3_BUCKET`, or an
  unknown backend name, raises `ValueError`; it never falls back to local
  (`factory.py:make_storage`).
- `boto3` is imported only when `S3FileStorage` is constructed. If it is
  missing, the `ImportError` names the `thestill[s3]` extra
  (`s3.py:S3FileStorage.__init__`). An empty `bucket` raises `ValueError`.

## Side Effects

- Local disk: `write_*`, `upload_file`, `download_file` and `ensure_directory`
  create parent directories and write files under `base_path`. `delete` and
  `delete_batch` unlink files (`local.py:LocalFileStorage`).
- S3 network calls: `get_object`, `put_object`, `head_object`,
  `delete_object`, `delete_objects`, `list_objects_v2` (paginated),
  `generate_presigned_url`, plus the transfer manager's `upload_file` and
  `download_file`, which use multipart above 8 MB (`s3.py:_MULTIPART_THRESHOLD_BYTES`).
- Every S3 write sends SSE headers: `AES256` by default, or `aws:kms` plus
  `SSEKMSKeyId` when `kms_key_id` is set (`s3.py:S3FileStorage._put_kwargs`).
- `s3.py:S3FileStorage.get_local_path` writes a `thestill_s3_*` temp file that
  the caller must delete. `local_copy` deletes it and logs
  `s3_local_copy_cleanup_failed` if that fails.
- Logs `file_storage_backend` at construction (`factory.py:make_storage`) and
  `s3_delete_batch_partial_failure` for partial batch errors, without raising
  (`s3.py:S3FileStorage.delete_batch`).

## Verification

- Run: `./venv/bin/pytest tests/unit/utils/file_storage`
