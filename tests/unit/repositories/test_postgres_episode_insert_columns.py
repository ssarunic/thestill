"""Spec #76 — the shared Postgres episode INSERT, its parameter tuple and the
podcast-side row mapper stay in step (no database needed).

``save()`` re-inserts every episode through ``_EPISODE_INSERT_SQL``; if a
column such as ``canonical_id`` is missing from any of the three, import
provenance is silently dropped on the next full save.
"""

from datetime import datetime, timezone

from thestill.models.podcast import Episode
from thestill.repositories.postgres_podcast_repository_podcasts import (
    _EPISODE_INSERT_SQL,
    _episode_from_row,
    _episode_insert_params,
)


def _episode(**overrides) -> Episode:
    base = dict(
        podcast_id="p-1",
        external_id="e-1",
        title="T",
        description="d",
        audio_url="https://example.com/a.mp3",
    )
    base.update(overrides)
    return Episode(**base)


def test_insert_sql_columns_match_params_and_carry_canonical_id():
    columns = _EPISODE_INSERT_SQL.split("(", 1)[1].split(")", 1)[0]
    names = [c.strip() for c in columns.split(",")]
    params = _episode_insert_params("p-1", _episode(canonical_id="youtube:abc"), datetime.now(timezone.utc))
    assert len(names) == _EPISODE_INSERT_SQL.count("%s") == len(params)
    assert names[-1] == "canonical_id"
    assert params[-1] == "youtube:abc"


def test_podcast_side_row_mapper_hydrates_canonical_id():
    now = datetime.now(timezone.utc)
    row = {
        "id": "e-1",
        "podcast_id": "p-1",
        "created_at": now,
        "updated_at": now,
        "external_id": "x",
        "title": "T",
        "slug": "t",
        "description": "d",
        "pub_date": None,
        "audio_url": "https://example.com/a.mp3",
        "duration": None,
        "image_url": None,
        "explicit": None,
        "episode_type": None,
        "episode_number": None,
        "season_number": None,
        "website_url": None,
        "audio_file_size": None,
        "audio_mime_type": None,
        "audio_path": None,
        "downsampled_audio_path": None,
        "raw_transcript_path": None,
        "clean_transcript_path": None,
        "summary_path": None,
        "failed_at_stage": None,
        "failure_reason": None,
        "failure_type": None,
        "failed_at": None,
        "canonical_id": "apple:42",
    }
    assert _episode_from_row(row).canonical_id == "apple:42"
    row.pop("canonical_id")
    assert _episode_from_row(row).canonical_id is None
