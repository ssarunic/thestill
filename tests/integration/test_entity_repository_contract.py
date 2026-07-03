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

"""Dual-backend contract suite for the entity repository (spec #44 Phase 2).

The same tests run against BOTH the SQLite and the PostgreSQL implementations
behind the ``EntityRepository`` ABC. This is the fidelity guarantee spec #42
FM-5 demands: the Postgres port is exercised against a *real* Postgres, never a
mock, and every behaviour is asserted identically on both engines so a dialect
divergence fails the build.

The Postgres cases are skipped (not failed) when no server is reachable, so the
suite still passes on a SQLite-only CI runner. Point ``TEST_DATABASE_URL`` at a
Postgres to include them:

    TEST_DATABASE_URL=postgresql://postgres@127.0.0.1:55432/thestill_entity \\
        ./venv/bin/python -m pytest tests/integration/test_entity_repository_contract.py
"""

from __future__ import annotations

import json
import os
from datetime import datetime, timedelta, timezone

import pytest

from thestill.models.enrichment import EnrichmentStatus, EntityAffiliation, EntityEnrichment, EntityFact
from thestill.models.entities import EntityMention, EntityRecord, EntityType, MentionRole, ResolutionStatus
from thestill.models.podcast import Episode, Podcast
from thestill.repositories.sqlite_entity_repository import SqliteEntityRepository
from thestill.repositories.sqlite_podcast_repository import SqlitePodcastRepository

PG_DSN = os.getenv("TEST_DATABASE_URL", "")


def _pg_reachable(dsn: str) -> bool:
    if not dsn:
        return False
    try:
        import psycopg

        with psycopg.connect(dsn, connect_timeout=3) as conn:
            conn.execute("SELECT 1")
        return True
    except Exception:
        return False


PG_OK = _pg_reachable(PG_DSN)

# Parent rows — production-shaped uuid ids (FM-5). Entity ids are slugs.
POD_1 = "aaaaaaaa-aaaa-4aaa-8aaa-aaaaaaaaaaaa"
POD_2 = "bbbbbbbb-bbbb-4bbb-8bbb-bbbbbbbbbbbb"
EP_1 = "11111111-1111-4111-8111-111111111111"  # pod 1, newest
EP_2 = "22222222-2222-4222-8222-222222222222"  # pod 1, oldest
EP_3 = "33333333-3333-4333-8333-333333333333"  # pod 2

PUB_1 = datetime(2026, 6, 20, 8, 0, 0, tzinfo=timezone.utc)
PUB_2 = datetime(2026, 6, 10, 8, 0, 0, tzinfo=timezone.utc)
PUB_3 = datetime(2026, 6, 15, 8, 0, 0, tzinfo=timezone.utc)

_ENTITY_TABLES = (
    "entities, entity_mentions, entity_cooccurrences, entity_enrichment, "
    "mention_overrides, resolution_blacklist, episodes, podcasts"
)


@pytest.fixture(params=["sqlite", "postgres"])
def repo(request, tmp_path):
    """Yield a clean entity repository (with parent podcasts/episodes) for
    each backend."""
    if request.param == "sqlite":
        db = str(tmp_path / "contract.db")
        # SqlitePodcastRepository owns the SQLite DDL; parents go through it.
        pod_repo = SqlitePodcastRepository(db_path=db)
        pod_repo.save_podcast(
            Podcast(
                id=POD_1,
                rss_url="https://example.com/feed1.xml",
                title="Prof G Markets",
                description="",
                slug="prof-g-markets",
            )
        )
        pod_repo.save_podcast(
            Podcast(id=POD_2, rss_url="https://example.com/feed2.xml", title="All-In", description="", slug="all-in")
        )
        pod_repo.save_episodes(
            [
                Episode(
                    id=EP_1,
                    podcast_id=POD_1,
                    external_id="e1",
                    title="SpaceX IPO",
                    description="",
                    audio_url="https://example.com/1.mp3",
                    pub_date=PUB_1,
                    slug="spacex-ipo",
                ),
                Episode(
                    id=EP_2,
                    podcast_id=POD_1,
                    external_id="e2",
                    title="AI Job Crisis",
                    description="",
                    audio_url="https://example.com/2.mp3",
                    pub_date=PUB_2,
                    slug="ai-job-crisis",
                ),
                Episode(
                    id=EP_3,
                    podcast_id=POD_2,
                    external_id="e3",
                    title="Market Wrap",
                    description="",
                    audio_url="https://example.com/3.mp3",
                    pub_date=PUB_3,
                    slug="market-wrap",
                ),
            ]
        )
        yield SqliteEntityRepository(db_path=db)
        return

    if not PG_OK:
        pytest.skip("Postgres not reachable — set TEST_DATABASE_URL to include this backend")
    import psycopg

    from thestill.repositories.postgres_entity_repository import PostgresEntityRepository
    from thestill.repositories.postgres_schema import ensure_schema

    ensure_schema(PG_DSN)  # idempotent typed-schema bootstrap
    with psycopg.connect(PG_DSN) as conn:
        conn.execute(f"TRUNCATE {_ENTITY_TABLES} CASCADE")
        conn.execute(
            "INSERT INTO podcasts (id, rss_url, title, slug) VALUES (%s, %s, %s, %s), (%s, %s, %s, %s)",
            (
                POD_1, "https://example.com/feed1.xml", "Prof G Markets", "prof-g-markets",
                POD_2, "https://example.com/feed2.xml", "All-In", "all-in",
            ),
        )
        conn.execute(
            "INSERT INTO episodes (id, podcast_id, external_id, title, audio_url, pub_date, slug) VALUES "
            "(%s, %s, %s, %s, %s, %s, %s), (%s, %s, %s, %s, %s, %s, %s), (%s, %s, %s, %s, %s, %s, %s)",
            (
                EP_1, POD_1, "e1", "SpaceX IPO", "https://example.com/1.mp3", PUB_1, "spacex-ipo",
                EP_2, POD_1, "e2", "AI Job Crisis", "https://example.com/2.mp3", PUB_2, "ai-job-crisis",
                EP_3, POD_2, "e3", "Market Wrap", "https://example.com/3.mp3", PUB_3, "market-wrap",
            ),
        )
    yield PostgresEntityRepository(PG_DSN)


# ---------------------------------------------------------------------------
# Factories
# ---------------------------------------------------------------------------
def _entity(
    id="person:elon-musk",
    type=EntityType.PERSON,
    name="Elon Musk",
    qid="Q317521",
    aliases=None,
    **overrides,
) -> EntityRecord:
    return EntityRecord(
        id=id,
        type=type,
        canonical_name=name,
        wikidata_qid=qid,
        aliases=aliases if aliases is not None else ["Musk"],
        **overrides,
    )


def _mention(
    episode_id=EP_1,
    surface="Elon Musk",
    segment_id=1,
    speaker="Scott Galloway",
    label="person",
    **overrides,
) -> EntityMention:
    base = dict(
        episode_id=episode_id,
        segment_id=segment_id,
        start_ms=segment_id * 10_000,
        end_ms=segment_id * 10_000 + 8_000,
        speaker=speaker,
        role=MentionRole.MENTIONED,
        surface_form=surface,
        surface_label=label,
        quote_excerpt=f"… {surface} …",
        confidence=0.95,
        extractor="gliner:test",
    )
    base.update(overrides)
    return EntityMention(**base)


def _resolved_mention(entity_id, **overrides) -> EntityMention:
    return _mention(
        entity_id=entity_id,
        resolution_status=ResolutionStatus.RESOLVED,
        resolved_at=datetime.now(timezone.utc),
        **overrides,
    )


def _seed_resolved_corpus(repo):
    """Three entities; resolved mentions across both podcasts.

    EP_1 (pod 1): musk (seg 1) + spacex (seg 1) + spacex (seg 2)
    EP_2 (pod 1): musk (seg 1, speaker Andrew Yang) + spacex (seg 1)
    EP_3 (pod 2): musk (seg 1)
    """
    repo.upsert_entity(_entity())
    repo.upsert_entity(_entity(id="company:spacex", type=EntityType.COMPANY, name="SpaceX", qid="Q193701", aliases=[]))
    repo.upsert_entity(_entity(id="topic:ai-jobs", type=EntityType.TOPIC, name="AI Jobs", qid=None, aliases=[]))
    repo.insert_mentions(
        [
            _resolved_mention("person:elon-musk", episode_id=EP_1, segment_id=1),
            _resolved_mention("company:spacex", episode_id=EP_1, surface="SpaceX", label="company", segment_id=1),
            _resolved_mention("company:spacex", episode_id=EP_1, surface="SpaceX", label="company", segment_id=2),
            _resolved_mention("person:elon-musk", episode_id=EP_2, segment_id=1, speaker="Andrew Yang"),
            _resolved_mention("company:spacex", episode_id=EP_2, surface="SpaceX", label="company", segment_id=1),
            _resolved_mention("person:elon-musk", episode_id=EP_3, segment_id=1, speaker="Chamath"),
        ]
    )


# ---------------------------------------------------------------------------
# Entity upsert / lookup
# ---------------------------------------------------------------------------
def test_upsert_and_get_entity_roundtrip(repo):
    e = _entity(aliases=["Musk", "Elon"], description="CEO of SpaceX", wikidata_instance_of=["Q5"])
    assert repo.upsert_entity(e) == "person:elon-musk"
    got = repo.get_entity("person:elon-musk")
    assert got is not None
    assert got.id == e.id
    assert got.type is EntityType.PERSON
    assert got.canonical_name == "Elon Musk"
    assert got.wikidata_qid == "Q317521"
    assert sorted(got.aliases) == ["Elon", "Musk"]
    assert got.description == "CEO of SpaceX"
    assert got.wikidata_instance_of == ["Q5"]
    assert got.created_at == e.created_at  # tz-aware instant round-trips
    assert got.created_at.tzinfo is not None
    assert got.updated_at.tzinfo is not None


def test_get_entity_missing_returns_none(repo):
    assert repo.get_entity("person:nobody") is None


def test_upsert_entity_merges_aliases_distinct_sorted(repo):
    repo.upsert_entity(_entity(aliases=["Musk", "Elon"]))
    repo.upsert_entity(_entity(aliases=["Elon", "Technoking"]))
    got = repo.get_entity("person:elon-musk")
    assert got.aliases == ["Elon", "Musk", "Technoking"]  # distinct union, sorted


def test_upsert_entity_preserves_qid_description_and_p31_on_empty_update(repo):
    repo.upsert_entity(_entity(description="CEO of SpaceX", wikidata_instance_of=["Q5"]))
    # Second upsert with NULL qid/description and empty P31 must not wipe.
    repo.upsert_entity(_entity(qid=None, description=None, wikidata_instance_of=[]))
    got = repo.get_entity("person:elon-musk")
    assert got.wikidata_qid == "Q317521"
    assert got.description == "CEO of SpaceX"
    assert got.wikidata_instance_of == ["Q5"]
    # A non-empty P31 replaces.
    repo.upsert_entity(_entity(wikidata_instance_of=["Q5", "Q43229"]))
    assert repo.get_entity("person:elon-musk").wikidata_instance_of == ["Q5", "Q43229"]


def test_find_entity_by_qid(repo):
    repo.upsert_entity(_entity())
    assert repo.find_entity_by_qid("Q317521").id == "person:elon-musk"
    assert repo.find_entity_by_qid("Q0") is None


def test_list_entities_by_type_ordered_by_name(repo):
    repo.upsert_entity(_entity())
    repo.upsert_entity(_entity(id="person:andrew-yang", name="Andrew Yang", qid="Q13561328", aliases=[]))
    repo.upsert_entity(_entity(id="company:spacex", type=EntityType.COMPANY, name="SpaceX", qid="Q193701"))
    people = repo.list_entities_by_type("person")
    assert [e.canonical_name for e in people] == ["Andrew Yang", "Elon Musk"]
    assert [e.id for e in repo.list_entities_by_type("company")] == ["company:spacex"]
    assert repo.list_entities_by_type("topic") == []


def test_delete_entity_cascades_mentions(repo):
    repo.upsert_entity(_entity())
    repo.insert_mentions([_resolved_mention("person:elon-musk")])
    assert repo.count_mentions_for_episode(EP_1) == 1
    assert repo.delete_entity("person:elon-musk") is True
    assert repo.get_entity("person:elon-musk") is None
    assert repo.count_mentions_for_episode(EP_1) == 0  # ON DELETE CASCADE
    assert repo.delete_entity("person:elon-musk") is False  # already gone


def test_repoint_mentions(repo):
    repo.upsert_entity(_entity(id="person:elon", name="Elon", aliases=[]))
    repo.upsert_entity(_entity(id="person:elon-musk"))
    repo.insert_mentions(
        [
            _resolved_mention("person:elon", segment_id=1),
            _resolved_mention("person:elon", segment_id=2),
        ]
    )
    moved = repo.repoint_mentions(from_entity_id="person:elon", to_entity_id="person:elon-musk")
    assert moved == 2
    assert [r.mention.entity_id for r in repo.find_mentions(entity_id="person:elon-musk")] == [
        "person:elon-musk",
        "person:elon-musk",
    ]
    assert repo.find_mentions(entity_id="person:elon") == []


# ---------------------------------------------------------------------------
# Mention insert / pending / resolve lifecycle
# ---------------------------------------------------------------------------
def test_insert_mentions_empty_is_noop(repo):
    assert repo.insert_mentions([]) == 0


def test_insert_count_and_delete_mentions_for_episode(repo):
    assert repo.insert_mentions([_mention(segment_id=1), _mention(segment_id=2)]) == 2
    assert repo.insert_mentions([_mention(episode_id=EP_2)]) == 1
    assert repo.count_mentions_for_episode(EP_1) == 2
    assert repo.count_mentions_for_episode(EP_2) == 1
    assert repo.delete_mentions_for_episode(EP_1) == 2
    assert repo.count_mentions_for_episode(EP_1) == 0
    assert repo.count_mentions_for_episode(EP_2) == 1  # untouched
    assert repo.delete_mentions_for_episode(EP_1) == 0


def test_list_pending_mentions_scoped_ordered_limited(repo):
    repo.insert_mentions(
        [
            _mention(segment_id=1, surface="A"),
            _mention(segment_id=2, surface="B"),
            _mention(episode_id=EP_2, surface="C"),
        ]
    )
    backlog = repo.list_pending_mentions()
    assert [m.surface_form for m in backlog] == ["A", "B", "C"]  # ordered by id
    assert all(m.id is not None for m in backlog)
    assert all(m.resolution_status is ResolutionStatus.PENDING for m in backlog)
    scoped = repo.list_pending_mentions(episode_id=EP_2)
    assert [m.surface_form for m in scoped] == ["C"]
    assert scoped[0].episode_id == EP_2
    assert len(repo.list_pending_mentions(limit=2)) == 2


def test_get_mention_roundtrips_all_fields(repo):
    repo.insert_mentions([_mention(sentiment=-0.25)])
    m = repo.list_pending_mentions()[0]
    got = repo.get_mention(m.id)
    assert got is not None
    assert got.episode_id == EP_1
    assert got.segment_id == 1
    assert got.start_ms == 10_000
    assert got.end_ms == 18_000
    assert got.speaker == "Scott Galloway"
    assert got.role is MentionRole.MENTIONED
    assert got.surface_form == "Elon Musk"
    assert got.surface_label == "person"
    assert got.quote_excerpt == "… Elon Musk …"
    assert got.sentiment == pytest.approx(-0.25)
    assert got.confidence == pytest.approx(0.95)
    assert got.extractor == "gliner:test"
    assert got.entity_id is None
    assert got.resolved_at is None
    assert got.created_at.tzinfo is not None
    assert repo.get_mention(999_999) is None


def test_resolve_mention_lifecycle(repo):
    repo.upsert_entity(_entity())
    repo.insert_mentions([_mention()])
    m = repo.list_pending_mentions()[0]
    ts = datetime(2026, 6, 21, 12, 0, 0, tzinfo=timezone.utc)
    assert (
        repo.resolve_mention(mention_id=m.id, entity_id="person:elon-musk", status="resolved", resolved_at=ts, method="direct")
        is True
    )
    got = repo.get_mention(m.id)
    assert got.resolution_status is ResolutionStatus.RESOLVED
    assert got.entity_id == "person:elon-musk"
    assert got.resolved_at == ts
    assert got.resolution_method is not None and got.resolution_method.value == "direct"
    assert repo.list_pending_mentions() == []
    # Missing id → False; invalid status → ValueError.
    assert repo.resolve_mention(mention_id=999_999, entity_id=None, status="dropped") is False
    with pytest.raises(ValueError):
        repo.resolve_mention(mention_id=m.id, entity_id=None, status="pending")


def test_resolve_mention_ambiguous_stores_candidates(repo):
    repo.insert_mentions([_mention(surface="Elon")])
    m = repo.list_pending_mentions()[0]
    repo.resolve_mention(
        mention_id=m.id,
        entity_id=None,
        status="ambiguous",
        candidate_entity_ids=["person:elon-musk", "person:elon-james"],
    )
    got = repo.get_mention(m.id)
    assert got.resolution_status is ResolutionStatus.AMBIGUOUS
    assert got.candidate_entity_ids == ["person:elon-musk", "person:elon-james"]
    assert got.entity_id is None


def test_find_mention_ids_by_surface_case_insensitive_with_status_filter(repo):
    repo.upsert_entity(_entity())
    repo.insert_mentions([_mention(segment_id=1), _mention(segment_id=2), _mention(episode_id=EP_2)])
    pending = repo.list_pending_mentions()
    repo.resolve_mention(mention_id=pending[0].id, entity_id="person:elon-musk", status="resolved")
    repo.resolve_mention(mention_id=pending[1].id, entity_id=None, status="dropped")
    # pending[2] stays pending — excluded by the default statuses too.
    hits = repo.find_mention_ids_by_surface("ELON MUSK")
    assert hits == [(pending[0].id, EP_1)]  # dropped + pending excluded
    # Explicit statuses + episode scoping.
    assert repo.find_mention_ids_by_surface("elon musk", statuses=("dropped",)) == [(pending[1].id, EP_1)]
    assert repo.find_mention_ids_by_surface("Elon Musk", episode_id=EP_2) == []
    assert repo.find_mention_ids_by_surface("Elon Musk", statuses=()) == []


def test_reset_mentions_to_pending_clears_resolution_fields(repo):
    repo.upsert_entity(_entity())
    repo.insert_mentions([_mention()])
    m = repo.list_pending_mentions()[0]
    repo.resolve_mention(mention_id=m.id, entity_id="person:elon-musk", status="resolved", method="direct")
    assert repo.reset_mentions_to_pending([]) == 0
    assert repo.reset_mentions_to_pending([m.id]) == 1
    got = repo.get_mention(m.id)
    assert got.resolution_status is ResolutionStatus.PENDING
    assert got.entity_id is None
    assert got.resolved_at is None
    assert got.resolution_method is None
    assert got.candidate_entity_ids == []


# ---------------------------------------------------------------------------
# Read-side queries: find_mentions / list_mentions_by_speaker / clip
# ---------------------------------------------------------------------------
def test_find_mentions_only_resolved_with_filters(repo):
    _seed_resolved_corpus(repo)
    repo.insert_mentions([_mention(surface="Pending Person")])  # must never surface

    by_entity = repo.find_mentions(entity_id="person:elon-musk")
    assert len(by_entity) == 3
    # Newest episode first (pub_date DESC): EP_1 (06-20), EP_3 (06-15), EP_2 (06-10).
    assert [r.episode_id for r in by_entity] == [EP_1, EP_3, EP_2]
    ctx = by_entity[0]
    assert ctx.episode_title == "SpaceX IPO"
    assert ctx.episode_pub_date == PUB_1
    assert ctx.podcast_id == POD_1
    assert ctx.podcast_title == "Prof G Markets"
    assert ctx.podcast_slug == "prof-g-markets"
    assert ctx.entity_type == "person"
    assert ctx.entity_canonical_name == "Elon Musk"

    assert {r.mention.surface_form for r in repo.find_mentions(entity_type="company")} == {"SpaceX"}
    assert len(repo.find_mentions(episode_id=EP_1)) == 3
    assert len(repo.find_mentions(podcast_id=POD_2)) == 1
    assert len(repo.find_mentions(role="mentioned", limit=100)) == 6
    assert repo.find_mentions(role="host") == []
    assert len(repo.find_mentions(limit=2)) == 2
    # date_range covering only EP_1 + EP_3 (06-12 .. 06-30).
    windowed = repo.find_mentions(
        entity_id="person:elon-musk",
        date_range=(datetime(2026, 6, 12, tzinfo=timezone.utc), datetime(2026, 6, 30, tzinfo=timezone.utc)),
    )
    assert [r.episode_id for r in windowed] == [EP_1, EP_3]


def test_list_mentions_by_speaker_substring_and_topic_segment_constraint(repo):
    _seed_resolved_corpus(repo)
    # Case-insensitive substring on the speaker label.
    got = repo.list_mentions_by_speaker(speaker="gallow")
    assert got and all(r.mention.speaker == "Scott Galloway" for r in got)
    # Topic constraint: SpaceX resolved in EP_1 seg 1 AND seg 2; Galloway's
    # musk mention sits in seg 1 → included. In EP_2 SpaceX is seg 1 too.
    with_topic = repo.list_mentions_by_speaker(speaker="Scott Galloway", topic_entity_id="company:spacex")
    assert {(r.episode_id, r.mention.segment_id) for r in with_topic} >= {(EP_1, 1)}
    # A topic never mentioned in Galloway's segments → empty.
    assert repo.list_mentions_by_speaker(speaker="Chamath", topic_entity_id="company:spacex") == []
    # podcast filter
    assert {r.podcast_id for r in repo.list_mentions_by_speaker(speaker="Chamath")} == {POD_2}


def test_get_mention_for_clip_straddle_then_nearest(repo):
    _seed_resolved_corpus(repo)
    # Straddling match: seg-1 mention spans 10_000..18_000.
    hit = repo.get_mention_for_clip(episode_id=EP_1, start_ms=12_000)
    assert hit is not None
    assert hit.mention.start_ms == 10_000
    # No straddle → nearest by absolute distance (seg-2 spans 20_000..28_000).
    near = repo.get_mention_for_clip(episode_id=EP_1, start_ms=500_000)
    assert near is not None
    assert near.mention.start_ms == 20_000
    # Episode with no resolved mentions → None.
    repo.delete_mentions_for_episode(EP_3)
    assert repo.get_mention_for_clip(episode_id=EP_3, start_ms=0) is None


# ---------------------------------------------------------------------------
# Co-occurrences
# ---------------------------------------------------------------------------
def test_rebuild_cooccurrences_full_canonical_pair_order(repo):
    _seed_resolved_corpus(repo)
    inserted = repo.rebuild_cooccurrences()
    # musk+spacex co-occur in EP_1 and EP_2 → one pair row, episode_count 2,
    # canonically ordered (company:spacex < person:elon-musk).
    assert inserted == 1
    summary = repo.get_entity_summary("person:elon-musk")
    assert len(summary["cooccurring"]) == 1
    assert summary["cooccurring"][0]["entity"].id == "company:spacex"
    assert summary["cooccurring"][0]["episode_count"] == 2


def test_rebuild_cooccurrences_scoped_and_empty(repo):
    _seed_resolved_corpus(repo)
    assert repo.rebuild_cooccurrences(episode_ids=[]) == 0
    # EP_3 has a single entity → no pairs from that scope.
    assert repo.rebuild_cooccurrences(episode_ids=[EP_3]) == 1  # musk touched → corpus-wide pair recount
    # Scoped rebuild for EP_1 recomputes the same corpus-wide aggregate.
    assert repo.rebuild_cooccurrences(episode_ids=[EP_1]) == 1
    summary = repo.get_entity_summary("company:spacex")
    assert summary["cooccurring"][0]["episode_count"] == 2


# ---------------------------------------------------------------------------
# Entity summary / roles / anchors / top speakers
# ---------------------------------------------------------------------------
def test_get_entity_summary_shape(repo):
    _seed_resolved_corpus(repo)
    repo.rebuild_cooccurrences()
    assert repo.get_entity_summary("person:nobody") is None
    summary = repo.get_entity_summary("person:elon-musk")
    assert summary["entity"].id == "person:elon-musk"
    assert summary["mention_count"] == 3
    assert [r.episode_id for r in summary["recent_mentions"]] == [EP_1, EP_3, EP_2]
    assert summary["enrichment"] is None  # not yet enriched
    # most_discussed_on: pod 1 has 2 musk mentions, pod 2 has 1.
    discussed = summary["most_discussed_on"]
    assert [(d["podcast_id"], d["mention_count"]) for d in discussed] == [(POD_1, 2), (POD_2, 1)]
    assert discussed[0]["podcast_slug"] == "prof-g-markets"
    assert summary["hosts_podcasts"] == []


def test_get_entity_roles_and_anchor_roundtrip(repo):
    repo.upsert_entity(_entity())
    repo.upsert_entity(_entity(id="person:chamath", name="Chamath", qid=None, aliases=[]))
    repo.set_podcast_hosts(POD_1, ["person:elon-musk"])
    repo.set_podcast_recurring(POD_1, ["person:chamath"])
    repo.set_episode_guests(EP_1, ["person:chamath"])

    anchors = repo.get_podcast_anchors(POD_1)
    assert anchors == {"hosts": ["person:elon-musk"], "recurring": ["person:chamath"]}
    assert repo.get_podcast_anchors(POD_2) == {"hosts": [], "recurring": []}
    # Episode anchors = union(host, recurring, guest), order-preserving dedup.
    assert repo.get_episode_anchors(EP_1) == ["person:elon-musk", "person:chamath"]
    assert repo.get_episode_anchors(EP_3) == []

    roles = repo.get_entity_roles("person:elon-musk")
    assert [p["podcast_id"] for p in roles["hosts_podcasts"]] == [POD_1]
    assert roles["hosts_podcasts"][0]["podcast_title"] == "Prof G Markets"
    assert roles["hosts_podcasts"][0]["episode_count"] == 2
    assert roles["recurring_podcasts"] == []
    assert roles["guest_episodes"] == []

    chamath = repo.get_entity_roles("person:chamath")
    assert [p["podcast_id"] for p in chamath["recurring_podcasts"]] == [POD_1]
    assert [g["episode_id"] for g in chamath["guest_episodes"]] == [EP_1]
    assert chamath["guest_episodes"][0]["podcast_id"] == POD_1
    assert chamath["guest_episodes"][0]["episode_title"] == "SpaceX IPO"


def test_detect_top_speakers_excludes_blank_and_unknown(repo):
    repo.insert_mentions(
        [
            _mention(segment_id=1, speaker="Scott Galloway"),
            _mention(segment_id=2, speaker="Scott Galloway"),
            _mention(segment_id=3, speaker="Ed Elson"),
            _mention(segment_id=4, speaker="Unknown"),
            _mention(segment_id=5, speaker="  "),
            _mention(segment_id=6, speaker=None),
            _mention(episode_id=EP_3, speaker="Chamath"),  # other podcast
        ]
    )
    top = repo.detect_top_speakers(POD_1)
    assert top == [("Scott Galloway", 2), ("Ed Elson", 1)]
    assert repo.detect_top_speakers(POD_1, limit=1) == [("Scott Galloway", 2)]


# ---------------------------------------------------------------------------
# Coreference helpers
# ---------------------------------------------------------------------------
def test_list_unresolved_person_mentions_includes_null_label(repo):
    repo.insert_mentions(
        [
            _mention(surface="Elon", label="person"),
            _mention(surface="Mystery", label=None, segment_id=2),
            _mention(surface="SpaceX", label="company", segment_id=3),
            _mention(surface="Other Episode", label="person", episode_id=EP_2),
        ]
    )
    for m in repo.list_pending_mentions():
        repo.resolve_mention(mention_id=m.id, entity_id=None, status="unresolvable")
    got = repo.list_unresolved_person_mentions(EP_1)
    assert {m.surface_form for m in got} == {"Elon", "Mystery"}  # company excluded
    assert all(m.resolution_status is ResolutionStatus.UNRESOLVABLE for m in got)


def test_list_resolved_persons_for_episode(repo):
    _seed_resolved_corpus(repo)
    got = repo.list_resolved_persons_for_episode(EP_1)
    assert [e.id for e in got] == ["person:elon-musk"]  # company filtered out, distinct
    assert repo.list_resolved_persons_for_episode(EP_2)[0].id == "person:elon-musk"


# ---------------------------------------------------------------------------
# Lookup / typeahead
# ---------------------------------------------------------------------------
def test_find_entity_by_name_id_canonical_alias(repo):
    repo.upsert_entity(_entity(aliases=["Musk", "Technoking"]))
    repo.upsert_entity(_entity(id="company:spacex", type=EntityType.COMPANY, name="SpaceX", qid="Q193701", aliases=[]))
    # Exact id match wins first.
    assert repo.find_entity_by_name("person:elon-musk").id == "person:elon-musk"
    # Case-insensitive canonical name.
    assert repo.find_entity_by_name("elon musk").id == "person:elon-musk"
    # Alias element match (case-insensitive).
    assert repo.find_entity_by_name("technoking").id == "person:elon-musk"
    # Type filter applies to name/alias matches.
    assert repo.find_entity_by_name("Elon Musk", entity_type="company") is None
    assert repo.find_entity_by_name("spacex", entity_type="company").id == "company:spacex"
    assert repo.find_entity_by_name("nobody") is None


def test_search_entities_by_prefix_names_aliases_types_and_ranking(repo):
    _seed_resolved_corpus(repo)
    assert repo.search_entities_by_prefix("") == []
    # Name hit — matched_alias stays None.
    hits = repo.search_entities_by_prefix("spac")
    assert [h.id for h in hits] == ["company:spacex"]
    assert hits[0].matched_alias is None
    assert hits[0].mention_count == 3
    # Alias hit — "musk" is inside canonical name too; use "mus" via alias-only entity.
    repo.upsert_entity(_entity(id="person:grimes", name="Grimes", qid=None, aliases=["Claire Boucher"]))
    alias_hits = repo.search_entities_by_prefix("boucher")
    assert [h.id for h in alias_hits] == ["person:grimes"]
    assert alias_hits[0].matched_alias == "Claire Boucher"
    # Type restriction.
    assert repo.search_entities_by_prefix("s", types=("company",))
    assert all(h.type == "company" for h in repo.search_entities_by_prefix("s", types=("company",)))


def test_search_entities_by_prefix_role_boost(repo):
    _seed_resolved_corpus(repo)
    # Zero-mention guest anchored on EP_1 must outrank the heavily-mentioned
    # musk for the same prefix.
    repo.upsert_entity(_entity(id="person:elona-guest", name="Elona Guest", qid=None, aliases=[]))
    repo.set_episode_guests(EP_1, ["person:elona-guest"])
    hits = repo.search_entities_by_prefix("elon")
    assert hits[0].id == "person:elona-guest"
    assert hits[0].role == "guest"
    assert hits[0].role_episode_count == 1
    assert hits[0].mention_count == 0
    musk = next(h for h in hits if h.id == "person:elon-musk")
    assert musk.role is None
    assert musk.mention_count == 3


# ---------------------------------------------------------------------------
# Overrides + blacklist
# ---------------------------------------------------------------------------
def test_add_override_validation(repo):
    with pytest.raises(ValueError):
        repo.add_override(surface_form="X", episode_id=None, kind="bogus")
    with pytest.raises(ValueError):
        repo.add_override(surface_form="X", episode_id=None, kind="force_entity")  # needs entity_id


def test_override_lookup_episode_scoped_beats_global(repo):
    repo.upsert_entity(_entity())
    gid = repo.add_override(surface_form="Elon", episode_id=None, kind="force_unresolvable", reason="global")
    eid = repo.add_override(
        surface_form="Elon",
        episode_id=EP_1,
        kind="force_entity",
        entity_id="person:elon-musk",
        created_by="admin",
    )
    assert gid > 0 and eid > gid
    # Episode-scoped wins over global; surface match is case-insensitive.
    hit = repo.lookup_override("ELON", EP_1)
    assert hit["id"] == eid
    assert hit["override_kind"] == "force_entity"
    assert hit["entity_id"] == "person:elon-musk"
    assert hit["episode_id"] == EP_1
    assert hit["created_by"] == "admin"
    # Other episode falls back to the global row.
    fallback = repo.lookup_override("elon", EP_2)
    assert fallback["id"] == gid
    assert fallback["episode_id"] is None
    assert repo.lookup_override("nobody", EP_1) is None
    # list_overrides: newest first.
    assert [o["id"] for o in repo.list_overrides()] == [eid, gid]
    assert [o["id"] for o in repo.list_overrides(limit=1)] == [eid]


def test_blacklist_add_check_list_idempotent(repo):
    assert repo.is_blacklisted("Prof G", "Q999") is False
    first = repo.add_blacklist_entry(surface_form="Prof G", wrong_qid="Q999", reason="wrong person")
    assert first > 0
    # Case-insensitive surface, exact qid.
    assert repo.is_blacklisted("prof g", "Q999") is True
    assert repo.is_blacklisted("Prof G", "Q1000") is False
    # Idempotent on the unique pair — no duplicate row, no error.
    repo.add_blacklist_entry(surface_form="Prof G", wrong_qid="Q999")
    entries = repo.list_blacklist()
    assert len(entries) == 1
    assert entries[0]["surface_form"] == "Prof G"
    assert entries[0]["wrong_qid"] == "Q999"
    assert entries[0]["reason"] == "wrong person"
    repo.add_blacklist_entry(surface_form="Prof G", wrong_qid="Q1000")
    assert [e["wrong_qid"] for e in repo.list_blacklist()] == ["Q1000", "Q999"]  # newest first
    assert len(repo.list_blacklist(limit=1)) == 1


# ---------------------------------------------------------------------------
# Enrichment (spec #45)
# ---------------------------------------------------------------------------
def _enrichment(**overrides) -> EntityEnrichment:
    base = dict(
        entity_id="person:elon-musk",
        image_url="https://img.example.com/elon.jpg",
        image_attribution="Wikimedia",
        image_license="CC-BY-4.0",
        headline="Business magnate",
        wikipedia_extract="Elon Musk is…",
        wikipedia_url="https://en.wikipedia.org/wiki/Elon_Musk",
        facts=[EntityFact(label="Born", value="June 28, 1971")],
        affiliations=[EntityAffiliation(qid="Q193701", label="SpaceX", relation="Founder")],
        wikidata_status=EnrichmentStatus.OK,
        wikidata_fetched_at=datetime(2026, 6, 1, tzinfo=timezone.utc),
        wikipedia_status=EnrichmentStatus.OK,
        wikipedia_fetched_at=datetime(2026, 6, 1, tzinfo=timezone.utc),
        schema_version=1,
    )
    base.update(overrides)
    return EntityEnrichment(**base)


def test_enrichment_upsert_get_delete_roundtrip(repo):
    repo.upsert_entity(_entity())
    assert repo.get_enrichment("person:elon-musk") is None
    e = _enrichment()
    repo.upsert_enrichment(e)
    got = repo.get_enrichment("person:elon-musk")
    assert got is not None
    assert got.image_url == e.image_url
    assert got.headline == "Business magnate"
    assert got.wikipedia_extract == "Elon Musk is…"
    assert [f.model_dump() for f in got.facts] == [f.model_dump() for f in e.facts]
    assert [a.model_dump() for a in got.affiliations] == [a.model_dump() for a in e.affiliations]
    assert got.wikidata_status is EnrichmentStatus.OK
    assert got.wikidata_fetched_at == e.wikidata_fetched_at
    assert got.created_at == e.created_at  # preserved verbatim on insert
    assert got.updated_at.tzinfo is not None
    assert repo.delete_enrichment("person:elon-musk") is True
    assert repo.get_enrichment("person:elon-musk") is None
    assert repo.delete_enrichment("person:elon-musk") is False


def test_enrichment_failed_run_preserves_prior_content(repo):
    """Spec #42 FM-1: a FAILED source must not wipe previously-cached content;
    statuses/timestamps/retry_after still advance."""
    repo.upsert_entity(_entity())
    repo.upsert_enrichment(_enrichment())
    retry = datetime(2026, 7, 3, tzinfo=timezone.utc)
    failed_run = _enrichment(
        image_url=None,
        image_attribution=None,
        image_license=None,
        headline=None,
        wikipedia_extract=None,
        wikipedia_url=None,
        facts=[],
        affiliations=[],
        wikidata_status=EnrichmentStatus.FAILED,
        wikipedia_status=EnrichmentStatus.FAILED,
        wikidata_fetched_at=datetime(2026, 7, 2, tzinfo=timezone.utc),
        wikipedia_fetched_at=datetime(2026, 7, 2, tzinfo=timezone.utc),
        retry_after=retry,
    )
    repo.upsert_enrichment(failed_run)
    got = repo.get_enrichment("person:elon-musk")
    # Content preserved from the earlier successful run…
    assert got.image_url == "https://img.example.com/elon.jpg"
    assert got.image_attribution == "Wikimedia"
    assert got.headline == "Business magnate"
    assert got.wikipedia_extract == "Elon Musk is…"
    assert len(got.facts) == 1 and len(got.affiliations) == 1
    # …while provenance advances.
    assert got.wikidata_status is EnrichmentStatus.FAILED
    assert got.wikipedia_status is EnrichmentStatus.FAILED
    assert got.retry_after == retry
    # A later successful (but empty) run DOES replace content.
    ok_empty = _enrichment(
        image_url=None,
        headline=None,
        wikipedia_extract=None,
        facts=[],
        affiliations=[],
        wikidata_status=EnrichmentStatus.EMPTY,
        wikipedia_status=EnrichmentStatus.EMPTY,
    )
    repo.upsert_enrichment(ok_empty)
    got = repo.get_enrichment("person:elon-musk")
    assert got.headline is None
    assert got.facts == []


def test_entity_ids_needing_enrichment_gating(repo):
    _seed_resolved_corpus(repo)  # musk + spacex have QIDs; topic:ai-jobs has none
    # Never enriched → both QID-bearing entities qualify; the QID-less never does.
    assert repo.entity_ids_needing_enrichment() == ["company:spacex", "person:elon-musk"]
    assert repo.entity_ids_needing_enrichment(limit=1) == ["company:spacex"]
    # Enrich musk OK → drops out at the same schema_version…
    repo.upsert_enrichment(_enrichment())
    assert repo.entity_ids_needing_enrichment() == ["company:spacex"]
    # …but re-qualifies under a bumped schema_version, and under force.
    assert repo.entity_ids_needing_enrichment(schema_version=2) == ["company:spacex", "person:elon-musk"]
    assert repo.entity_ids_needing_enrichment(force=True) == ["company:spacex", "person:elon-musk"]
    # Scoping: EP_3 only has musk mentions.
    assert repo.entity_ids_needing_enrichment(episode_id=EP_3, force=True) == ["person:elon-musk"]
    assert repo.entity_ids_needing_enrichment(podcast_id=POD_2, force=True) == ["person:elon-musk"]
    assert repo.entity_ids_needing_enrichment(entity_id="company:spacex") == ["company:spacex"]


def test_entity_ids_needing_enrichment_failed_retry_gate(repo):
    repo.upsert_entity(_entity())
    # Failed with retry_after in the future → gated out.
    repo.upsert_enrichment(
        _enrichment(
            wikidata_status=EnrichmentStatus.FAILED,
            retry_after=datetime.now(timezone.utc) + timedelta(hours=6),
        )
    )
    assert repo.entity_ids_needing_enrichment() == []
    # Failed with retry_after elapsed → back in the queue.
    repo.upsert_enrichment(
        _enrichment(
            wikidata_status=EnrichmentStatus.FAILED,
            retry_after=datetime.now(timezone.utc) - timedelta(hours=1),
        )
    )
    assert repo.entity_ids_needing_enrichment() == ["person:elon-musk"]


# ---------------------------------------------------------------------------
# Review scan + alias-merge aggregates
# ---------------------------------------------------------------------------
def test_fetch_resolution_review_rows_contract(repo):
    _seed_resolved_corpus(repo)
    repo.upsert_entity(_entity(wikidata_instance_of=["Q5"]))
    rows = repo.fetch_resolution_review_rows()
    # One row per (QID-bearing entity, surface_form); topic:ai-jobs (no QID) excluded.
    by_key = {(r["entity_id"], r["surface_form"]): r for r in rows}
    assert set(by_key) == {("person:elon-musk", "Elon Musk"), ("company:spacex", "SpaceX")}
    musk = by_key[("person:elon-musk", "Elon Musk")]
    assert musk["type"] == "person"
    assert musk["canonical_name"] == "Elon Musk"
    assert musk["wikidata_qid"] == "Q317521"
    assert musk["mention_count"] == 3
    # Cross-backend contract: wikidata_instance_of is a JSON *string*
    # (core.entity_review json.loads it).
    assert json.loads(musk["wikidata_instance_of"]) == ["Q5"]


def test_find_duplicate_qid_pairs_keeper_ranking(repo):
    # Two entities share Q317521; the person one has more mentions → keeper.
    repo.upsert_entity(_entity(id="person:elon-musk"))
    repo.upsert_entity(_entity(id="company:elon-musk", type=EntityType.COMPANY, name="Elon Musk Inc", aliases=[]))
    repo.upsert_entity(_entity(id="company:spacex", type=EntityType.COMPANY, name="SpaceX", qid="Q193701", aliases=[]))
    repo.insert_mentions(
        [
            _resolved_mention("person:elon-musk", segment_id=1),
            _resolved_mention("person:elon-musk", segment_id=2),
            _resolved_mention("company:elon-musk", segment_id=3),
        ]
    )
    pairs = repo.find_duplicate_qid_pairs()
    assert pairs == [("Q317521", "person:elon-musk", "company:elon-musk")]
    # Tie on mentions → EntityType declaration order (person < company) decides.
    repo.insert_mentions([_resolved_mention("company:elon-musk", segment_id=4)])
    assert repo.find_duplicate_qid_pairs() == [("Q317521", "person:elon-musk", "company:elon-musk")]


def test_find_mistyped_entities_majority_rule(repo):
    # Entity stored as company but its mentions are majority-labelled person.
    repo.upsert_entity(_entity(id="company:oprah", type=EntityType.COMPANY, name="Oprah", aliases=[]))
    repo.insert_mentions(
        [
            _resolved_mention("company:oprah", segment_id=1, surface="Oprah", label="person"),
            _resolved_mention("company:oprah", segment_id=2, surface="Oprah", label="person"),
            _resolved_mention("company:oprah", segment_id=3, surface="Oprah", label="person"),
            _resolved_mention("company:oprah", segment_id=4, surface="Oprah", label="company"),
        ]
    )
    out = repo.find_mistyped_entities()
    assert out == [("company:oprah", "company", "person", 3, 4)]
    # Below the mention floor → filtered out.
    assert repo.find_mistyped_entities(min_mentions=5) == []
    # Majority ratio not cleared → filtered out.
    assert repo.find_mistyped_entities(min_majority_ratio=0.9) == []
