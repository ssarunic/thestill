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

"""Spec #94 — the public visibility predicate and quote SQL, on both backends.

The quote queries in :mod:`thestill.repositories.public_quotes` are one SQL
text run by SQLite and Postgres; the visibility methods are implemented
twice. Every assertion below must hold on both. Postgres runs when
``TEST_DATABASE_URL`` points at a scratch database (it is TRUNCATEd).
"""

from __future__ import annotations

import os
from datetime import datetime, timedelta, timezone
from typing import Iterator, Tuple

import pytest

from thestill.models.entities import EntityMention, EntityRecord, EntityType, MentionRole, ResolutionStatus
from thestill.models.podcast import Episode, Podcast
from thestill.repositories.public_quotes import QuoteFilter, days_ago

PG_DSN = os.getenv("TEST_DATABASE_URL", "")

POD_PUBLIC = "aaaaaaaa-aaaa-4aaa-8aaa-aaaaaaaaaaaa"
POD_HIDDEN = "bbbbbbbb-bbbb-4bbb-8bbb-bbbbbbbbbbbb"
EP_SUMMARISED = "11111111-1111-4111-8111-111111111111"
EP_NO_SUMMARY = "22222222-2222-4222-8222-222222222222"
EP_HIDDEN_SHOW = "33333333-3333-4333-8333-333333333333"

LONG = "and that is the whole reason the market keeps getting this particular company wrong"


def _pg_reachable() -> bool:
    if not PG_DSN:
        return False
    try:
        import psycopg

        with psycopg.connect(PG_DSN, connect_timeout=3) as conn:
            conn.execute("SELECT 1")
        return True
    except Exception:
        return False


@pytest.fixture(params=["sqlite", "postgres"])
def repos(request, tmp_path) -> Iterator[Tuple[object, object]]:
    if request.param == "sqlite":
        from thestill.repositories.sqlite_entity_repository import SqliteEntityRepository
        from thestill.repositories.sqlite_podcast_repository import SqlitePodcastRepository

        db = str(tmp_path / "public.db")
        podcasts, entities = SqlitePodcastRepository(db_path=db), SqliteEntityRepository(db_path=db)
    else:
        if not _pg_reachable():
            pytest.skip("Postgres not reachable — set TEST_DATABASE_URL to include this backend")
        import psycopg

        from thestill.repositories.postgres_entity_repository import PostgresEntityRepository
        from thestill.repositories.postgres_podcast_repository import PostgresPodcastRepository
        from thestill.repositories.postgres_schema import ensure_schema

        ensure_schema(PG_DSN)
        with psycopg.connect(PG_DSN) as conn:
            conn.execute("TRUNCATE entities, entity_mentions, episodes, podcasts CASCADE")
        podcasts, entities = PostgresPodcastRepository(PG_DSN), PostgresEntityRepository(PG_DSN)
    _seed(podcasts, entities)
    yield podcasts, entities


def _seed(podcasts, entities) -> None:
    now = datetime.now(timezone.utc)

    def episode(ep_id: str, podcast_id: str, slug: str, days: int, summarised: bool) -> Episode:
        return Episode(
            id=ep_id,
            podcast_id=podcast_id,
            external_id=slug,
            title=slug.replace("-", " ").title(),
            description="",
            audio_url=f"https://example.com/{slug}.mp3",
            pub_date=now - timedelta(days=days),
            slug=slug,
            summary_path=f"{slug}_summary.md" if summarised else None,
        )

    podcasts.save_podcast(
        Podcast(id=POD_PUBLIC, rss_url="https://example.com/a.xml", title="Public", description="", slug="public")
    )
    podcasts.save_podcast(
        Podcast(id=POD_HIDDEN, rss_url="https://example.com/b.xml", title="Hidden", description="", slug="hidden")
    )
    podcasts.save_episodes(
        [
            episode(EP_SUMMARISED, POD_PUBLIC, "summarised", 2, True),
            episode(EP_NO_SUMMARY, POD_PUBLIC, "no-summary", 1, False),
            episode(EP_HIDDEN_SHOW, POD_HIDDEN, "on-hidden-show", 3, True),
        ]
    )
    for ep_id in (EP_SUMMARISED, EP_HIDDEN_SHOW):
        podcasts.set_episode_summary_preview(ep_id, "A gist.")
    podcasts.set_podcast_public_pages(POD_HIDDEN, False)

    for entity_id, kind, name in (
        ("person:alice", EntityType.PERSON, "Alice"),
        ("person:bob", EntityType.PERSON, "Bob"),
        ("person:ghost", EntityType.PERSON, "Ghost"),
        ("company:acme", EntityType.COMPANY, "Acme"),
    ):
        entities.upsert_entity(EntityRecord(id=entity_id, type=kind, canonical_name=name))
    entities.set_entity_public_hidden("person:ghost", True)

    def mention(episode_id: str, segment: int, entity_id: str, role: MentionRole, quote: str) -> EntityMention:
        return EntityMention(
            entity_id=entity_id,
            resolution_status=ResolutionStatus.RESOLVED,
            episode_id=episode_id,
            segment_id=segment,
            start_ms=segment * 60_000,
            end_ms=segment * 60_000 + 20_000,
            speaker=None,
            role=role,
            surface_form=entity_id.split(":")[1],
            quote_excerpt=quote,
            confidence=1.0,
            extractor="test",
        )

    def quote(episode_id: str, segment: int, speaker: str, about: str, text: str = LONG) -> list:
        return [
            mention(episode_id, segment, speaker, MentionRole.SPEAKING, "First sentence."),
            mention(episode_id, segment, about, MentionRole.MENTIONED, text),
        ]

    entities.insert_mentions(
        [
            *quote(EP_SUMMARISED, 1, "person:alice", "company:acme"),  # inside the 90 s intro: skipped
            *quote(EP_SUMMARISED, 2, "person:alice", "company:acme"),
            *quote(EP_SUMMARISED, 3, "person:bob", "company:acme"),
            mention(EP_SUMMARISED, 3, "person:alice", MentionRole.MENTIONED, LONG),  # same segment, second subject
            *quote(EP_SUMMARISED, 4, "person:ghost", "company:acme"),  # hidden speaker
            *quote(EP_SUMMARISED, 5, "person:alice", "company:acme", "this episode is brought to you by acme " + LONG),
            *quote(EP_NO_SUMMARY, 2, "person:alice", "company:acme"),  # unsummarised episode
            *quote(EP_HIDDEN_SHOW, 2, "person:bob", "company:acme"),  # hidden show
        ]
    )


def test_visibility_predicate(repos):
    podcasts, entities = repos
    rows, total = podcasts.list_public_episode_rows(limit=10)
    assert [r["slug"] for r in rows] == ["summarised"] and total == 1
    assert rows[0]["podcast_slug"] == "public"
    assert dict(podcasts.list_hidden_podcasts()) == {POD_HIDDEN: "hidden"}
    assert podcasts.is_podcast_public(POD_PUBLIC) and not podcasts.is_podcast_public(POD_HIDDEN)
    assert set(entities.list_hidden_entity_ids()) == {"person:ghost"}
    podcasts.set_podcast_public_pages(POD_HIDDEN, True)
    assert dict(podcasts.list_hidden_podcasts()) == {}
    assert podcasts.list_public_episode_rows(limit=10)[1] == 2


def test_quote_queries(repos):
    _, entities = repos
    queries = entities.public_quote_queries()

    rows, total = queries.quotes(QuoteFilter(about_id="company:acme"), order="recent", limit=10)
    assert total == 2
    assert sorted(str(r["speaker_id"]) for r in rows) == ["person:alice", "person:bob"]

    said, said_total = queries.quotes(QuoteFilter(speaker_id="person:alice", since=days_ago(30)), limit=10)
    assert said_total == 1 and said[0]["podcast_slug"] == "public"

    about, about_total = queries.quotes(
        QuoteFilter(about_id="person:alice", exclude_speaker_id="person:alice"), limit=10
    )
    assert about_total == 1 and str(about[0]["speaker_id"]) == "person:bob"

    speakers = {str(r["speaker_id"]): int(r["quotes"]) for r in queries.speakers("company:acme", since=days_ago(365))}
    assert speakers == {"person:alice": 1, "person:bob": 1}

    topics = {str(r["about_id"]): int(r["quotes"]) for r in queries.topics("person:bob")}
    assert topics == {"company:acme": 1, "person:alice": 1}

    (top,) = queries.trending(since=days_ago(7))
    assert str(top["about_id"]) == "company:acme" and int(top["speakers"]) == 2 and int(top["shows"]) == 1

    indexable = {str(r["entity_id"]): int(r["quotes"]) for r in queries.indexable_entities(min_quotes=1)}
    assert indexable == {"company:acme": 2, "person:alice": 2, "person:bob": 1}

    presence = queries.presence("company:acme")
    assert [str(r["episode_id"]) for r in presence] == [EP_SUMMARISED]
