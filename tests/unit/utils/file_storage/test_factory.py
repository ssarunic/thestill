# Copyright 2025-2026 Thestill
#
# Licensed under the Apache License, Version 2.0 (the "License");
# you may not use this file except in compliance with the License.
# You may obtain a copy of the License at
#
#     http://www.apache.org/licenses/LICENSE-2.0

"""Spec #35 — factory + Config-wiring behaviour.

Verifies:
- ``make_storage(config)`` selects the right backend by ``storage_backend``
- Missing ``S3_BUCKET`` when ``storage_backend=s3`` fails fast at construction
- Unknown backend names raise with a clear message
- ``Config.file_storage`` is populated automatically (single-construction
  pattern mirroring ``Config.path_manager``)
"""

from __future__ import annotations

from types import SimpleNamespace

import pytest

from thestill.utils.file_storage import LocalFileStorage, make_storage
from thestill.utils.file_storage.s3 import S3FileStorage


def _fake_config(**overrides):
    """Minimal duck-typed Config — make_storage only reads a handful of
    fields, so a SimpleNamespace is enough to exercise the factory in
    isolation. Tests that exercise the real Config use ``load_config``."""
    defaults = {
        "storage_backend": "local",
        "storage_path": "/tmp/thestill-test",
        "s3_bucket": "",
        "s3_region": "us-east-1",
        "s3_prefix": "",
        "s3_endpoint_url": "",
        "s3_kms_key_id": "",
    }
    defaults.update(overrides)
    return SimpleNamespace(**defaults)


class TestFactorySelection:
    def test_local_backend_returns_localfilestorage(self, tmp_path):
        config = _fake_config(storage_backend="local", storage_path=str(tmp_path))
        storage = make_storage(config)
        assert isinstance(storage, LocalFileStorage)

    def test_s3_backend_returns_s3filestorage(self):
        # No endpoint_url means real S3; moto isn't loaded here but the
        # construction itself doesn't make any API calls so this is fine.
        config = _fake_config(
            storage_backend="s3",
            s3_bucket="some-bucket",
            s3_region="eu-west-1",
        )
        storage = make_storage(config)
        assert isinstance(storage, S3FileStorage)
        assert storage.bucket == "some-bucket"
        assert storage.region == "eu-west-1"

    def test_uppercase_backend_normalised(self, tmp_path):
        # Operator-set env var might be ``STORAGE_BACKEND=LOCAL`` — accept it.
        config = _fake_config(storage_backend="LOCAL", storage_path=str(tmp_path))
        storage = make_storage(config)
        assert isinstance(storage, LocalFileStorage)


class TestFactoryFailsFast:
    def test_s3_without_bucket_raises(self):
        config = _fake_config(storage_backend="s3", s3_bucket="")
        with pytest.raises(ValueError, match="S3_BUCKET"):
            make_storage(config)

    def test_unknown_backend_raises(self):
        config = _fake_config(storage_backend="azure")
        with pytest.raises(ValueError, match="unknown STORAGE_BACKEND"):
            make_storage(config)

    def test_empty_backend_falls_back_to_local(self, tmp_path):
        # Empty string is treated as "local" via the (config.storage_backend
        # or "local") fallback — operator who unsets the env var still gets
        # a working backend.
        config = _fake_config(storage_backend="", storage_path=str(tmp_path))
        storage = make_storage(config)
        assert isinstance(storage, LocalFileStorage)


class TestConfigWiring:
    def test_config_constructs_file_storage_automatically(self, tmp_path, monkeypatch):
        # Through load_config — verifies the Config.__init__ wiring.
        monkeypatch.setenv("STORAGE_PATH", str(tmp_path))
        monkeypatch.setenv("STORAGE_BACKEND", "local")
        from thestill.utils.config import load_config

        cfg = load_config()
        assert cfg.file_storage is not None
        assert isinstance(cfg.file_storage, LocalFileStorage)
        # And it's actually usable
        cfg.file_storage.write_text("test.txt", "wired-up")
        assert cfg.file_storage.read_text("test.txt") == "wired-up"

    def test_config_with_s3_backend_fails_fast_without_bucket(self, tmp_path, monkeypatch):
        # Critical: a deployment that flips STORAGE_BACKEND=s3 but forgets
        # S3_BUCKET must crash at startup, not silently fall back to local
        # or fail later on the first write.
        monkeypatch.setenv("STORAGE_PATH", str(tmp_path))
        monkeypatch.setenv("STORAGE_BACKEND", "s3")
        monkeypatch.setenv("S3_BUCKET", "")
        from thestill.utils.config import load_config

        with pytest.raises(ValueError, match="S3_BUCKET"):
            load_config()
