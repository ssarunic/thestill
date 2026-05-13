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

"""Spec #35 — pluggable storage backends.

Public surface:

- ``FileStorage`` — abstract base class. All callers depend on this; concrete
  backends are constructed by ``make_storage(config)`` at startup.
- ``FileMetadata`` — dataclass returned by ``get_metadata`` / yielded by
  ``list_files``. Single API round-trip per object.
- ``LocalFileStorage`` — local-disk backend, anchored at ``base_path``.
- ``S3FileStorage`` — AWS S3 (or S3-compatible via ``endpoint_url``).
  Imported lazily; ``ImportError`` surfaces if ``boto3`` is not installed.
- ``make_storage`` — env-driven factory; switches on ``STORAGE_BACKEND``.

S3 is the v1 cloud target. GCS is deferred (spec section "GCSFileStorage
(deferred)"). The ABC stays cloud-shaped (idempotent delete, metadata in
listings) so GCS or another object store can be added later without changes
to callers.
"""

from .base import FileMetadata, FileStorage, StorageError
from .factory import make_storage
from .local import LocalFileStorage

# S3 is intentionally NOT re-exported here. Callers that want the concrete
# class import ``from thestill.utils.file_storage.s3 import S3FileStorage``,
# which is the only path where the ``boto3`` lazy import runs. Re-exporting
# would force every importer to pay the boto3 import (or fail without
# ``[s3]`` installed), defeating the optional-dep design.

__all__ = [
    "FileMetadata",
    "FileStorage",
    "LocalFileStorage",
    "StorageError",
    "make_storage",
]
