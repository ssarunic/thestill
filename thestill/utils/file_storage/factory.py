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

"""Spec #35 — ``FileStorage`` factory.

One entry point so ``Config`` (and tests, and the CLI / web / MCP bootstraps)
can construct the right backend from env-driven config without importing
the concrete classes everywhere. Selecting ``s3`` triggers the lazy ``boto3``
import — ``local`` stays free of the cloud deps.
"""

from __future__ import annotations

from typing import TYPE_CHECKING

import structlog

from .base import FileStorage
from .local import LocalFileStorage

if TYPE_CHECKING:
    # Import only at type-check time so the factory module stays cheap.
    from ..config import Config

logger = structlog.get_logger(__name__)


def make_storage(config: "Config") -> FileStorage:
    """Construct the configured ``FileStorage`` backend.

    Selection is driven by ``STORAGE_BACKEND``. Fails fast on missing
    required fields — no silent fallback to local, because a missing
    ``S3_BUCKET`` in production almost always means a misconfigured
    deployment, and we want that surfaced at startup.
    """
    backend = (config.storage_backend or "local").lower()

    if backend == "local":
        logger.info("file_storage_backend", backend="local", path=str(config.storage_path))
        return LocalFileStorage(base_path=str(config.storage_path))

    if backend == "s3":
        if not config.s3_bucket:
            raise ValueError("STORAGE_BACKEND=s3 requires S3_BUCKET to be set")
        # Imported lazily — keeps ``thestill.utils.file_storage`` importable
        # without boto3 for local-backend deployments.
        from .s3 import S3FileStorage  # noqa: WPS433

        logger.info(
            "file_storage_backend",
            backend="s3",
            bucket=config.s3_bucket,
            region=config.s3_region,
            prefix=config.s3_prefix or "(root)",
            endpoint_url=config.s3_endpoint_url or "(aws)",
            kms="kms" if config.s3_kms_key_id else "sse-s3",
        )
        return S3FileStorage(
            bucket=config.s3_bucket,
            region=config.s3_region,
            prefix=config.s3_prefix,
            endpoint_url=config.s3_endpoint_url or None,
            kms_key_id=config.s3_kms_key_id or None,
        )

    raise ValueError(f"unknown STORAGE_BACKEND={backend!r}; must be one of: local, s3")
