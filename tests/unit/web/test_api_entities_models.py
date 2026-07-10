"""Unit tests for response-model coercion in the entity API.

These guard backend-agnostic serialization: Postgres (spec #44) returns
``timestamptz`` columns as tz-aware ``datetime`` objects while SQLite returns
ISO strings, and the response models must accept both.
"""

from __future__ import annotations

from datetime import datetime, timezone

from thestill.web.routes.api_entities import CitationRow, EntityCooccurrenceRef, EntityRef, GuestEpisodeRef


def _entity() -> EntityRef:
    return EntityRef(id="e1", type="person", canonical_name="Ada Lovelace", wikidata_qid=None)


def test_last_seen_at_accepts_datetime_from_postgres():
    # Regression: a tz-aware datetime (as psycopg returns for timestamptz) must
    # not raise a ValidationError; it is normalized to an ISO string.
    dt = datetime(2026, 7, 9, 11, 6, 5, tzinfo=timezone.utc)
    ref = EntityCooccurrenceRef(entity=_entity(), episode_count=3, last_seen_at=dt)
    assert ref.last_seen_at == dt.isoformat()


def test_last_seen_at_passes_through_string_and_none():
    ref_str = EntityCooccurrenceRef(entity=_entity(), episode_count=1, last_seen_at="2026-07-09T11:06:05+00:00")
    assert ref_str.last_seen_at == "2026-07-09T11:06:05+00:00"

    ref_none = EntityCooccurrenceRef(entity=_entity(), episode_count=0, last_seen_at=None)
    assert ref_none.last_seen_at is None


def test_guest_episode_published_at_accepts_datetime():
    # Same coercion via the shared IsoTimestamp type — entity pages with guest
    # episodes (e.g. a recurring host) must not 500.
    dt = datetime(2026, 7, 9, 11, 6, 5, tzinfo=timezone.utc)
    ref = GuestEpisodeRef(
        episode_id="e",
        episode_title="Ep",
        podcast_id="p",
        podcast_title="Pod",
        published_at=dt,
    )
    assert ref.published_at == dt.isoformat()


def test_citation_row_published_at_accepts_datetime():
    dt = datetime(2026, 7, 9, 11, 6, 5, tzinfo=timezone.utc)
    row = CitationRow(
        episode_id="e",
        podcast_id="p",
        podcast_title="Pod",
        episode_title="Ep",
        start_ms=0,
        end_ms=1,
        quote="q",
        surface_form="s",
        published_at=dt,
    )
    assert row.published_at == dt.isoformat()
