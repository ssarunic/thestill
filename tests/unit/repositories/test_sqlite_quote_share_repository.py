"""Spec #114 — QuoteShareRepository on SQLite. The Postgres contract suite
re-runs these cases (``tests/integration/test_quote_share_repository_contract.py``)."""

from __future__ import annotations

from datetime import date

import pytest

from thestill.repositories.sqlite_podcast_repository import SqlitePodcastRepository
from thestill.repositories.sqlite_quote_share_repository import SqliteQuoteShareRepository
from thestill.utils.sqlite_ext import connect

EPISODE = "11111111-1111-4111-8111-111111111111"
PODCAST = "22222222-2222-4222-8222-222222222222"
QUOTE = "The first thing anyone should know is that this sentence stands alone."


@pytest.fixture
def repo(tmp_path) -> SqliteQuoteShareRepository:
    db = str(tmp_path / "podcasts.db")
    SqlitePodcastRepository(db_path=db)  # creates the schema
    with connect(db) as conn:
        conn.execute(
            "INSERT INTO podcasts (id, rss_url, title, slug, created_at, updated_at)"
            " VALUES (?, 'https://example.com/rss', 'Show', 'show', '2026-11-10T00:00:00+00:00',"
            " '2026-11-10T00:00:00+00:00')",
            (PODCAST,),
        )
        conn.execute(
            "INSERT INTO episodes (id, podcast_id, external_id, title, slug, audio_url, created_at, updated_at)"
            " VALUES (?, ?, 'ext-1', 'Episode', 'episode', 'https://example.com/a.mp3',"
            " '2026-11-10T00:00:00+00:00', '2026-11-10T00:00:00+00:00')",
            (EPISODE, PODCAST),
        )
        conn.execute("INSERT INTO entities (id, type, canonical_name) VALUES ('person:jane-doe', 'person', 'Jane Doe')")
    return SqliteQuoteShareRepository(db_path=db)


def _share(share_id: str = "aaaaaaaaaa", **over) -> dict:
    values = dict(
        id=share_id,
        episode_id=EPISODE,
        podcast_id=PODCAST,
        segment_id=4,
        start_ms=120_000,
        end_ms=150_000,
        text=QUOTE,
        speaker_entity_id="person:jane-doe",
    )
    values.update(over)
    return values


def test_upsert_inserts_once_and_reports_the_id_usable(repo):
    assert repo.upsert_gated([_share()]) == {"aaaaaaaaaa": True}
    assert repo.upsert_gated([_share()]) == {"aaaaaaaaaa": True}
    assert [row["id"] for row in repo.list_for_episode(EPISODE)] == ["aaaaaaaaaa"]
    assert repo.upsert_gated([]) == {}


def test_get_joins_what_a_card_needs(repo):
    repo.upsert_gated([_share()])
    row = repo.get("aaaaaaaaaa")
    assert (row["text"], row["start_ms"], row["end_ms"]) == (QUOTE, 120_000, 150_000)
    assert (row["podcast_slug"], row["episode_slug"], row["speaker_name"]) == ("show", "episode", "Jane Doe")
    assert not row["speaker_hidden"] and row["revoked_at"] is None
    assert repo.get("bbbbbbbbbb") is None


def test_upsert_refreshes_the_cached_speaker_and_segment(repo):
    repo.upsert_gated([_share()])
    repo.upsert_gated([_share(speaker_entity_id=None, segment_id=5)])
    row = repo.get("aaaaaaaaaa")
    assert (row["speaker_entity_id"], row["segment_id"], row["speaker_name"]) == (None, 5, None)


def test_an_id_that_holds_another_quote_is_a_collision_and_is_left_alone(repo):
    repo.upsert_gated([_share()])
    assert repo.upsert_gated([_share(text="Another sentence entirely, with the same id.")]) == {"aaaaaaaaaa": False}
    assert repo.get("aaaaaaaaaa")["text"] == QUOTE


def test_revoke_is_once_and_a_revoked_id_is_not_usable(repo):
    repo.upsert_gated([_share()])
    assert repo.revoke("aaaaaaaaaa", "wrong speaker") is True
    assert repo.revoke("aaaaaaaaaa", "again") is False
    assert repo.revoke("bbbbbbbbbb", "unknown") is False
    row = repo.get("aaaaaaaaaa")
    assert row["revoked_at"] is not None and row["revoked_reason"] == "wrong speaker"
    assert repo.upsert_gated([_share()]) == {"aaaaaaaaaa": False}


def test_revoke_for_podcast_returns_the_live_ids(repo):
    repo.upsert_gated([_share("aaaaaaaaaa"), _share("bbbbbbbbbb", start_ms=200_000, text=QUOTE + " Again.")])
    repo.revoke("aaaaaaaaaa", "first")
    assert repo.revoke_for_podcast(PODCAST, "show made private") == ["bbbbbbbbbb"]
    assert repo.revoke_for_podcast(PODCAST, "again") == []


def test_hits_add_up_per_share(repo):
    repo.upsert_gated([_share()])
    repo.record_hit("aaaaaaaaaa", date(2026, 11, 24), "www.linkedin.com")
    repo.record_hit("aaaaaaaaaa", date(2026, 11, 24), "www.linkedin.com")
    repo.record_hit("aaaaaaaaaa", date(2026, 11, 25), "")
    assert repo.list_for_episode(EPISODE)[0]["opens"] == 3


def test_reconcile_queries(repo):
    repo.upsert_gated([_share()])
    assert [row["id"] for row in repo.live_for_episode(EPISODE)] == ["aaaaaaaaaa"]
    assert repo.ids_for_speaker("person:jane-doe") == ["aaaaaaaaaa"]
    # No mention carries these words: nobody is found.
    assert repo.speakers_of_passage(EPISODE, QUOTE, 120_000, 150_000) == []
    # A share cached under an entity that was merged away is found under
    # the entity it was merged into.
    repo._execute(
        f"INSERT INTO entity_redirects (from_entity_id, to_entity_id) VALUES ({repo.placeholder}, {repo.placeholder})",
        ["person:jane-doe", "person:jane-roe"],
    )
    assert repo.ids_for_speaker("person:jane-roe") == ["aaaaaaaaaa"]
    repo._execute(f"DELETE FROM entity_redirects WHERE from_entity_id = {repo.placeholder}", ["person:jane-doe"])
    repo.set_speaker("aaaaaaaaaa", None, 9)
    row = repo.get("aaaaaaaaaa")
    assert (row["speaker_entity_id"], row["segment_id"]) == (None, 9)
    assert repo.ids_for_speaker("person:jane-doe") == []
    repo.revoke("aaaaaaaaaa", "gone")
    assert repo.live_for_episode(EPISODE) == []
