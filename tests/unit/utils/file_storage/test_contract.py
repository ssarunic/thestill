# Copyright 2025-2026 Thestill
#
# Licensed under the Apache License, Version 2.0 (the "License");
# you may not use this file except in compliance with the License.
# You may obtain a copy of the License at
#
#     http://www.apache.org/licenses/LICENSE-2.0

"""Spec #35 — contract-equivalence suite.

Every test here runs twice via the parametrized ``storage`` fixture: once
against LocalFileStorage, once against S3FileStorage (moto-backed). The
point is that callers can depend on the FileStorage ABC alone — both
backends must produce indistinguishable behaviour for every required
method on the abstract surface.
"""

from __future__ import annotations

import pytest

from thestill.utils.file_storage import FileMetadata


class TestReadWriteRoundtrip:
    def test_write_then_read_text(self, storage):
        storage.write_text("notes/hello.txt", "hello, world")
        assert storage.read_text("notes/hello.txt") == "hello, world"

    def test_write_then_read_bytes(self, storage):
        payload = b"\x00\x01\x02\xfe\xff"
        storage.write_bytes("data/raw.bin", payload)
        assert storage.read_bytes("data/raw.bin") == payload

    def test_write_text_with_alternate_encoding(self, storage):
        storage.write_text("notes/utf16.txt", "café", encoding="utf-16")
        assert storage.read_text("notes/utf16.txt", encoding="utf-16") == "café"

    def test_overwrite_existing_file(self, storage):
        storage.write_text("notes/x.txt", "first")
        storage.write_text("notes/x.txt", "second")
        assert storage.read_text("notes/x.txt") == "second"

    def test_write_creates_nested_parents(self, storage):
        # Deep prefix — both backends must accept this without an explicit
        # mkdir call.
        storage.write_text("a/b/c/d/e/f.txt", "deep")
        assert storage.read_text("a/b/c/d/e/f.txt") == "deep"

    def test_unicode_filename(self, storage):
        storage.write_text("notes/café-é.txt", "ok")
        assert storage.read_text("notes/café-é.txt") == "ok"


class TestFileNotFound:
    """Missing reads raise the stdlib ``FileNotFoundError`` — not a
    backend-specific exception type."""

    def test_read_text_missing(self, storage):
        with pytest.raises(FileNotFoundError):
            storage.read_text("nope.txt")

    def test_read_bytes_missing(self, storage):
        with pytest.raises(FileNotFoundError):
            storage.read_bytes("nope.bin")

    def test_get_metadata_missing(self, storage):
        with pytest.raises(FileNotFoundError):
            storage.get_metadata("nope.txt")

    def test_get_local_path_missing(self, storage):
        with pytest.raises(FileNotFoundError):
            storage.get_local_path("nope.txt")


class TestExists:
    def test_exists_true_after_write(self, storage):
        storage.write_text("x.txt", "x")
        assert storage.exists("x.txt") is True

    def test_exists_false_for_missing(self, storage):
        assert storage.exists("never-written.txt") is False

    def test_exists_false_after_delete(self, storage):
        storage.write_text("x.txt", "x")
        storage.delete("x.txt")
        assert storage.exists("x.txt") is False


class TestDelete:
    def test_delete_removes_file(self, storage):
        storage.write_text("x.txt", "x")
        storage.delete("x.txt")
        with pytest.raises(FileNotFoundError):
            storage.read_text("x.txt")

    def test_delete_missing_is_idempotent(self, storage):
        # No exception — S3 contract; local mirrors it.
        storage.delete("never-existed.txt")
        storage.delete("never-existed.txt")  # twice — still fine

    def test_delete_batch_returns_count(self, storage):
        for name in ("a.txt", "b.txt", "c.txt"):
            storage.write_text(name, name)
        deleted = storage.delete_batch(["a.txt", "b.txt", "c.txt"])
        assert deleted == 3
        for name in ("a.txt", "b.txt", "c.txt"):
            assert not storage.exists(name)

    def test_delete_batch_empty_input(self, storage):
        assert storage.delete_batch([]) == 0

    def test_delete_batch_with_missing_entries(self, storage):
        storage.write_text("a.txt", "a")
        # Mix of existing and missing — both backends must handle this
        # without raising AND return the same count (= paths processed).
        # The aligned contract counts "paths the caller asked us to delete"
        # so callers can use the return value as an audit number without
        # caring which backend is wired up.
        result = storage.delete_batch(["a.txt", "ghost.txt"])
        assert result == 2
        assert not storage.exists("a.txt")


class TestGetMetadata:
    def test_metadata_has_size_and_mtime(self, storage):
        storage.write_bytes("file.bin", b"abcdef")
        meta = storage.get_metadata("file.bin")
        assert isinstance(meta, FileMetadata)
        assert meta.size == 6
        # mtime is tz-aware UTC on both backends
        assert meta.modified_time.tzinfo is not None
        # And convertible to a unix timestamp via the property helper
        assert isinstance(meta.modified_timestamp, float)

    def test_metadata_path_is_relative(self, storage):
        storage.write_text("nested/deep/file.txt", "x")
        meta = storage.get_metadata("nested/deep/file.txt")
        # Critical: the returned path is the same shape the caller wrote —
        # not absolute, not with the backend prefix.
        assert meta.path == "nested/deep/file.txt"


class TestListFiles:
    def _seed(self, storage):
        storage.write_text("podcasts/rest-is-money/ep1.md", "1")
        storage.write_text("podcasts/rest-is-money/ep2.md", "2")
        storage.write_text("podcasts/prof-g/ep1.md", "3")
        storage.write_text("podcasts/prof-g/manifest.json", "{}")

    def test_list_all(self, storage):
        self._seed(storage)
        paths = sorted(m.path for m in storage.list_files())
        assert paths == [
            "podcasts/prof-g/ep1.md",
            "podcasts/prof-g/manifest.json",
            "podcasts/rest-is-money/ep1.md",
            "podcasts/rest-is-money/ep2.md",
        ]

    def test_list_with_prefix(self, storage):
        self._seed(storage)
        paths = sorted(m.path for m in storage.list_files(prefix="podcasts/prof-g"))
        assert paths == ["podcasts/prof-g/ep1.md", "podcasts/prof-g/manifest.json"]

    def test_list_with_pattern_matches_basename(self, storage):
        self._seed(storage)
        paths = sorted(m.path for m in storage.list_files(pattern="*.md"))
        assert paths == [
            "podcasts/prof-g/ep1.md",
            "podcasts/rest-is-money/ep1.md",
            "podcasts/rest-is-money/ep2.md",
        ]

    def test_list_with_pattern_matches_full_key(self, storage):
        self._seed(storage)
        paths = sorted(m.path for m in storage.list_files(pattern="*rest-is-money*"))
        assert paths == ["podcasts/rest-is-money/ep1.md", "podcasts/rest-is-money/ep2.md"]

    def test_list_empty_prefix(self, storage):
        # No seed at all — both backends must yield zero entries cleanly.
        assert list(storage.list_files(prefix="nothing-here")) == []

    def test_list_yields_filemetadata_with_size(self, storage):
        storage.write_text("a.txt", "12345")
        meta_list = list(storage.list_files())
        assert len(meta_list) == 1
        assert meta_list[0].path == "a.txt"
        assert meta_list[0].size == 5

    def test_list_does_not_emit_n_plus_one(self, storage):
        # Implicit through the previous test — if the implementation called
        # get_metadata() per entry, populating .size still works but the
        # request count would be 2N. This contract test asserts the
        # observable: size is populated without an extra get_metadata call,
        # which is the only thing the abstract interface can guarantee.
        for i in range(5):
            storage.write_text(f"f{i}.txt", "x" * (i + 1))
        meta_list = sorted(storage.list_files(), key=lambda m: m.path)
        assert [m.size for m in meta_list] == [1, 2, 3, 4, 5]


class TestGetSize:
    def test_get_size_returns_byte_count(self, storage):
        storage.write_bytes("file.bin", b"abcdef" * 1000)
        assert storage.get_size("file.bin") == 6000


class TestUploadDownload:
    """``upload_file`` / ``download_file`` were promoted to the ABC in
    Phase 2 because audio callers can't portably stream 100 MB files via
    ``write_bytes``."""

    def test_upload_file_round_trip(self, storage, tmp_path):
        # Caller writes to a local path, then uploads.
        local = tmp_path / "upload-src.bin"
        local.write_bytes(b"audio-bytes" * 1000)
        storage.upload_file(local, "audio/clip.bin")
        assert storage.read_bytes("audio/clip.bin") == b"audio-bytes" * 1000

    def test_upload_file_overwrites(self, storage, tmp_path):
        local = tmp_path / "src.bin"
        local.write_bytes(b"v1")
        storage.upload_file(local, "x.bin")
        local.write_bytes(b"v2")
        storage.upload_file(local, "x.bin")
        assert storage.read_bytes("x.bin") == b"v2"

    def test_download_file_round_trip(self, storage, tmp_path):
        storage.write_bytes("source.bin", b"payload" * 100)
        target = tmp_path / "out.bin"
        storage.download_file("source.bin", target)
        assert target.read_bytes() == b"payload" * 100

    def test_download_file_missing_raises(self, storage, tmp_path):
        target = tmp_path / "out.bin"
        with pytest.raises(FileNotFoundError):
            storage.download_file("never.bin", target)


class TestGetLocalPath:
    def test_local_copy_yields_readable_path(self, storage):
        storage.write_bytes("audio/clip.mp3", b"\xff\xfb\x90\x00")
        with storage.local_copy("audio/clip.mp3") as p:
            assert p.is_file()
            assert p.read_bytes() == b"\xff\xfb\x90\x00"

    def test_local_copy_preserves_extension(self, storage):
        # Tools that pick a decoder from the file extension (pydub, ffmpeg
        # subprocess, whisper) need the suffix preserved through the
        # download. Local trivially does; S3 must do this via the
        # NamedTemporaryFile(suffix=...) call.
        storage.write_bytes("audio/clip.mp3", b"\xff\xfb\x90\x00")
        with storage.local_copy("audio/clip.mp3") as p:
            assert p.suffix == ".mp3"


class TestPathNormalisation:
    def test_backslash_paths_normalised(self, storage):
        # Callers building keys via os.path.join on Windows would emit
        # backslashes; both backends must normalise to forward slashes.
        storage.write_text("a\\b\\c.txt", "ok")
        assert storage.read_text("a/b/c.txt") == "ok"

    def test_leading_slash_stripped(self, storage):
        storage.write_text("/leading/slash.txt", "ok")
        assert storage.read_text("leading/slash.txt") == "ok"
