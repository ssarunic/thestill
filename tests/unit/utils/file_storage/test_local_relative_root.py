"""LocalFileStorage with a relative root, as ``STORAGE_PATH=./data`` gives it.

``get_metadata`` (and so ``get_modified_time`` / ``get_size``) computed the
key of an unresolved path against the resolved root and raised
``ValueError: ... is not in the subpath of ...``. That broke every caller on
a default local install: the reprocess freshness check (spec #106) and the
sync track backfill (spec #116).
"""

from __future__ import annotations

import pytest

from thestill.utils.file_storage.local import LocalFileStorage


@pytest.fixture
def storage(tmp_path, monkeypatch) -> LocalFileStorage:
    (tmp_path / "data").mkdir()
    monkeypatch.chdir(tmp_path)
    return LocalFileStorage(base_path="./data")


def test_metadata_with_a_relative_root(storage):
    storage.write_text("raw_transcripts/show/ep.json", "{}")

    meta = storage.get_metadata("raw_transcripts/show/ep.json")

    assert meta.path == "raw_transcripts/show/ep.json"
    assert meta.size == 2
    assert storage.get_size("raw_transcripts/show/ep.json") == 2
    assert storage.get_modified_time("raw_transcripts/show/ep.json") > 0


def test_missing_file_is_still_file_not_found(storage):
    with pytest.raises(FileNotFoundError):
        storage.get_modified_time("raw_transcripts/show/nope.json")
