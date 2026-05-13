# Copyright 2025-2026 Thestill
#
# Licensed under the Apache License, Version 2.0 (the "License");
# you may not use this file except in compliance with the License.
# You may obtain a copy of the License at
#
#     http://www.apache.org/licenses/LICENSE-2.0

"""Shared fixtures for the FileStorage contract suite.

The ``storage`` fixture is parametrized over both backends so every test
in ``test_contract.py`` runs once against ``LocalFileStorage`` and once
against ``S3FileStorage`` (backed by moto's in-process S3 mock). If a new
contract test passes on local but fails on S3 (or vice versa), the backend
contract is what's drifting — the test won't silently pass on one side.
"""

from __future__ import annotations

from typing import Iterator

import boto3
import pytest
from moto import mock_aws

from thestill.utils.file_storage import FileStorage, LocalFileStorage
from thestill.utils.file_storage.s3 import S3FileStorage

_TEST_BUCKET = "thestill-test-bucket"
_TEST_REGION = "us-east-1"


@pytest.fixture(params=["local", "s3"])
def storage(request, tmp_path) -> Iterator[FileStorage]:
    """Yield each backend in turn.

    The ``params`` list controls which backends a given test runs against.
    Most contract tests use this fixture as-is. Tests that need to exercise
    a backend-specific feature live in ``test_local.py`` / ``test_s3.py``
    and construct the backend directly.
    """
    if request.param == "local":
        yield LocalFileStorage(base_path=str(tmp_path))
    else:
        with mock_aws():
            client = boto3.client("s3", region_name=_TEST_REGION)
            client.create_bucket(Bucket=_TEST_BUCKET)
            yield S3FileStorage(bucket=_TEST_BUCKET, region=_TEST_REGION)


@pytest.fixture
def local_storage(tmp_path) -> LocalFileStorage:
    """LocalFileStorage rooted at pytest's ``tmp_path``."""
    return LocalFileStorage(base_path=str(tmp_path))


@pytest.fixture
def s3_storage() -> Iterator[S3FileStorage]:
    """S3FileStorage against moto's in-process S3 mock."""
    with mock_aws():
        client = boto3.client("s3", region_name=_TEST_REGION)
        client.create_bucket(Bucket=_TEST_BUCKET)
        yield S3FileStorage(bucket=_TEST_BUCKET, region=_TEST_REGION)


@pytest.fixture
def s3_storage_with_prefix() -> Iterator[S3FileStorage]:
    """S3FileStorage with a non-empty key prefix — exercises the prefix-join logic."""
    with mock_aws():
        client = boto3.client("s3", region_name=_TEST_REGION)
        client.create_bucket(Bucket=_TEST_BUCKET)
        yield S3FileStorage(bucket=_TEST_BUCKET, region=_TEST_REGION, prefix="prod")
