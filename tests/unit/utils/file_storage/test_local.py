# Copyright 2025-2026 Thestill
#
# Licensed under the Apache License, Version 2.0 (the "License");
# you may not use this file except in compliance with the License.
# You may obtain a copy of the License at
#
#     http://www.apache.org/licenses/LICENSE-2.0

"""Spec #35 — LocalFileStorage-specific behaviour.

Covers things that don't apply to S3:
- Escape-resolution guard (_assert_inside_root mirror)
- get_local_path returns the actual filesystem path (no temp file)
- ensure_directory creates real directories
- No-op get_public_url for the streaming-through-app fallback path
"""

from __future__ import annotations

from pathlib import Path

import pytest

from thestill.utils.file_storage import LocalFileStorage


class TestEscapeGuard:
    """Defence-in-depth — even if PathManager isn't in the call chain,
    LocalFileStorage refuses paths that resolve outside ``base_path``."""

    def test_parent_traversal_rejected(self, tmp_path):
        storage = LocalFileStorage(base_path=str(tmp_path))
        with pytest.raises(ValueError, match="escapes storage root"):
            storage.write_text("../escape.txt", "evil")

    def test_deep_parent_traversal_rejected(self, tmp_path):
        storage = LocalFileStorage(base_path=str(tmp_path))
        with pytest.raises(ValueError, match="escapes storage root"):
            storage.read_text("../../../../etc/passwd")

    def test_absolute_path_outside_root_rejected(self, tmp_path):
        storage = LocalFileStorage(base_path=str(tmp_path))
        # An absolute path that obviously isn't under base_path. The leading
        # slash is stripped by _resolve's lstrip("/"), so the path becomes
        # relative — and "tmp/something" inside base_path is fine, which
        # means the guard's real job is to catch ``..`` traversal post-join.
        # Verify the explicit ``..`` form here.
        with pytest.raises(ValueError, match="escapes storage root"):
            storage.write_text("../../outside.txt", "x")

    def test_symlink_escape_rejected(self, tmp_path):
        # Create a symlink inside base_path that points outside it.
        # On most filesystems the resolve() step follows the link.
        storage = LocalFileStorage(base_path=str(tmp_path))
        external = tmp_path.parent / "external"
        external.mkdir(exist_ok=True)
        link_target = tmp_path / "evil_link"
        link_target.symlink_to(external)
        # Read-through-symlink to a file outside the root should be rejected.
        with pytest.raises(ValueError, match="escapes storage root"):
            storage.read_text("evil_link/some_file.txt")


class TestNonStringPath:
    def test_non_string_path_rejected(self, tmp_path):
        storage = LocalFileStorage(base_path=str(tmp_path))
        with pytest.raises(TypeError):
            storage.read_text(Path("file.txt"))  # type: ignore[arg-type]


class TestGetLocalPath:
    def test_returns_real_filesystem_path(self, tmp_path):
        storage = LocalFileStorage(base_path=str(tmp_path))
        storage.write_text("audio/clip.mp3", "fake")
        local = storage.get_local_path("audio/clip.mp3")
        # Must be the actual on-disk path, not a copy.
        assert local.is_file()
        assert local == (tmp_path / "audio/clip.mp3").resolve()

    def test_local_copy_does_not_unlink_real_file(self, tmp_path):
        # Critical: LocalFileStorage.local_copy must NOT delete the real
        # file when the context exits — that's only the cloud-backend
        # behaviour.
        storage = LocalFileStorage(base_path=str(tmp_path))
        storage.write_text("real.txt", "value")
        with storage.local_copy("real.txt") as p:
            real_path = p
        assert real_path.exists()
        assert real_path.read_text() == "value"


class TestEnsureDirectory:
    def test_creates_directory(self, tmp_path):
        storage = LocalFileStorage(base_path=str(tmp_path))
        storage.ensure_directory("a/b/c")
        assert (tmp_path / "a/b/c").is_dir()

    def test_idempotent_on_existing_directory(self, tmp_path):
        storage = LocalFileStorage(base_path=str(tmp_path))
        storage.ensure_directory("a/b/c")
        storage.ensure_directory("a/b/c")  # should not raise
        assert (tmp_path / "a/b/c").is_dir()


class TestPublicUrl:
    def test_returns_none_for_local(self, tmp_path):
        storage = LocalFileStorage(base_path=str(tmp_path))
        storage.write_text("x.txt", "x")
        assert storage.get_public_url("x.txt") is None


class TestBasePathLifecycle:
    def test_first_write_creates_missing_parents(self, tmp_path):
        # ``Config._ensure_directories`` owns root creation; the backend
        # itself only creates parent dirs on demand at write time.
        base = tmp_path / "fresh"
        storage = LocalFileStorage(base_path=str(base))
        storage.write_text("nested/file.txt", "hi")
        assert (base / "nested" / "file.txt").read_text() == "hi"
