"""Unit tests for the role linker.

Two-layer test: the parser (pure-text, no DB) and the linker (DB +
filesystem) using ``tmp_path`` as a sandboxed storage root.
"""

from __future__ import annotations

import sqlite3
import uuid
from pathlib import Path

from thestill.repositories.sqlite_entity_repository import SqliteEntityRepository
from thestill.repositories.sqlite_podcast_repository import SqlitePodcastRepository
from thestill.services.role_linker import backfill_all_roles, link_episode_roles, link_podcast_roles, parse_facts_file
from thestill.utils.path_manager import PathManager

# ---------------------------------------------------------------------------
# Parser
# ---------------------------------------------------------------------------


def test_parse_extracts_hosts_guests_recurring(tmp_path):
    f = tmp_path / "podcast.facts.md"
    f.write_text(
        """# Test Podcast

## Hosts
- Alex Kantrowitz - Silicon Valley journalist
- Bill Gurley - Investor

## Guest(s)
- Sarah Paine - Naval War College professor

## Recurring Roles
- Ad Narrator - sponsor reads
- Co-host Bob - regular voice

## Some Other Section
- this should be ignored
"""
    )
    roles = parse_facts_file(f)
    assert roles.hosts == [
        ("Alex Kantrowitz", "Silicon Valley journalist"),
        ("Bill Gurley", "Investor"),
    ]
    assert roles.guests == [("Sarah Paine", "Naval War College professor")]
    assert roles.recurring == [
        ("Ad Narrator", "sponsor reads"),
        ("Co-host Bob", "regular voice"),
    ]


def test_parse_strips_trailing_role_annotation(tmp_path):
    f = tmp_path / "ep.facts.md"
    f.write_text(
        """# Episode

## Guest(s)
- Greg Brockman (Guest) - President of OpenAI
"""
    )
    roles = parse_facts_file(f)
    assert roles.guests == [("Greg Brockman", "President of OpenAI")]


def test_parse_handles_missing_file(tmp_path):
    roles = parse_facts_file(tmp_path / "nope.facts.md")
    assert roles.hosts == []
    assert roles.guests == []
    assert roles.recurring == []


def test_parse_treats_unknown_heading_as_terminator(tmp_path):
    f = tmp_path / "p.facts.md"
    f.write_text(
        """## Hosts
- Alex Kantrowitz - intro

## Topics
- Stuff
- more stuff

## Guest(s)
- Greg Brockman - bio
"""
    )
    roles = parse_facts_file(f)
    assert roles.hosts == [("Alex Kantrowitz", "intro")]
    # The Topics bullets must NOT leak into the next bucket.
    assert roles.guests == [("Greg Brockman", "bio")]


# ---------------------------------------------------------------------------
# Linker (DB + filesystem)
# ---------------------------------------------------------------------------


def _seed_minimal_db(tmp_path: Path) -> tuple[Path, str, str, str, str]:
    """Bring up an empty schema with one podcast + one episode.

    Returns (db_path, podcast_id, podcast_slug, episode_id, episode_slug).
    """
    db_path = tmp_path / "podcasts.db"
    SqlitePodcastRepository(db_path=str(db_path))  # runs migrations
    podcast_id = str(uuid.uuid4())
    podcast_slug = "test-show"
    episode_id = "11111111-2222-3333-4444-555555555555"
    episode_slug = "first-episode"
    with sqlite3.connect(str(db_path)) as conn:
        conn.execute(
            "INSERT INTO podcasts (id, rss_url, title, slug) VALUES (?, ?, ?, ?)",
            (podcast_id, "https://example.com/rss", "Test Show", podcast_slug),
        )
        conn.execute(
            """INSERT INTO episodes (id, podcast_id, external_id, title, audio_url, slug)
               VALUES (?, ?, ?, ?, ?, ?)""",
            (episode_id, podcast_id, "ext-1", "First Episode", "https://example.com/a.mp3", episode_slug),
        )
    return db_path, podcast_id, podcast_slug, episode_id, episode_slug


def _write_facts_files(storage: Path, podcast_slug: str, episode_slug: str) -> None:
    pm = PathManager(str(storage))
    pm.podcast_facts_dir().mkdir(parents=True, exist_ok=True)
    (pm.episode_facts_dir() / podcast_slug).mkdir(parents=True, exist_ok=True)
    pm.podcast_facts_file(podcast_slug).write_text(
        """## Hosts
- Alex Host - the regular

## Recurring Roles
- Ad Narrator - reads ads
"""
    )
    pm.episode_facts_file(podcast_slug, episode_slug).write_text(
        """## Guest(s)
- Sarah Guest - the visitor
"""
    )


def test_link_creates_entities_and_writes_role_columns(tmp_path):
    db_path, podcast_id, podcast_slug, episode_id, episode_slug = _seed_minimal_db(tmp_path)
    _write_facts_files(tmp_path, podcast_slug, episode_slug)
    pm = PathManager(str(tmp_path))
    er = SqliteEntityRepository(db_path=str(db_path))

    podcast_result = link_podcast_roles(
        podcast_id=podcast_id,
        podcast_slug=podcast_slug,
        entity_repo=er,
        path_manager=pm,
    )
    episode_result = link_episode_roles(
        episode_id=episode_id,
        podcast_slug=podcast_slug,
        episode_slug=episode_slug,
        entity_repo=er,
        path_manager=pm,
    )

    assert podcast_result.hosts == ["person:alex-host"]
    # "Ad Narrator" filtered out as a generic role label
    assert podcast_result.recurring == []
    assert "Ad Narrator" in podcast_result.skipped_names
    assert episode_result.guests == ["person:sarah-guest"]

    # The DB should now hold the role ids on the right columns.
    with sqlite3.connect(str(db_path)) as conn:
        conn.row_factory = sqlite3.Row
        p = conn.execute("SELECT host_entity_ids, recurring_entity_ids FROM podcasts").fetchone()
        e = conn.execute("SELECT guest_entity_ids FROM episodes").fetchone()
    assert p["host_entity_ids"] == '["person:alex-host"]'
    assert p["recurring_entity_ids"] == "[]"
    assert e["guest_entity_ids"] == '["person:sarah-guest"]'

    # Created entities should be retrievable by name.
    assert er.find_entity_by_name("Sarah Guest") is not None
    assert er.find_entity_by_name("Alex Host") is not None


def test_link_is_idempotent(tmp_path):
    db_path, podcast_id, podcast_slug, episode_id, episode_slug = _seed_minimal_db(tmp_path)
    _write_facts_files(tmp_path, podcast_slug, episode_slug)
    pm = PathManager(str(tmp_path))
    er = SqliteEntityRepository(db_path=str(db_path))

    link_episode_roles(
        episode_id=episode_id,
        podcast_slug=podcast_slug,
        episode_slug=episode_slug,
        entity_repo=er,
        path_manager=pm,
    )
    second = link_episode_roles(
        episode_id=episode_id,
        podcast_slug=podcast_slug,
        episode_slug=episode_slug,
        entity_repo=er,
        path_manager=pm,
    )
    # Second pass shouldn't create duplicate entities or rows.
    assert second.guests == ["person:sarah-guest"]
    assert second.created_entities == []


def test_search_boosts_guest_above_zero_mention_lookalikes(tmp_path):
    """End-to-end: after linking, ``search_entities_by_prefix`` ranks
    a guest above an unflagged entity even with mention_count=0."""
    db_path, podcast_id, podcast_slug, episode_id, episode_slug = _seed_minimal_db(tmp_path)
    _write_facts_files(tmp_path, podcast_slug, episode_slug)
    pm = PathManager(str(tmp_path))
    er = SqliteEntityRepository(db_path=str(db_path))

    # Pre-create a competing entity that shares the prefix but has no role.
    from thestill.models.entities import EntityRecord, EntityType

    er.upsert_entity(
        EntityRecord(
            id="person:sarah-other",
            type=EntityType.PERSON,
            canonical_name="Sarah Other",
        )
    )
    link_episode_roles(
        episode_id=episode_id,
        podcast_slug=podcast_slug,
        episode_slug=episode_slug,
        entity_repo=er,
        path_manager=pm,
    )
    hits = er.search_entities_by_prefix("Sarah", types=("person",), limit_per_type=5)
    assert len(hits) == 2
    assert hits[0].canonical_name == "Sarah Guest"
    assert hits[0].role == "guest"
    assert hits[0].role_episode_count == 1
    assert hits[1].canonical_name == "Sarah Other"
    assert hits[1].role is None


def test_backfill_all_iterates_podcasts_and_episodes(tmp_path):
    db_path, _podcast_id, podcast_slug, _episode_id, episode_slug = _seed_minimal_db(tmp_path)
    _write_facts_files(tmp_path, podcast_slug, episode_slug)
    pm = PathManager(str(tmp_path))
    pr = SqlitePodcastRepository(db_path=str(db_path))
    er = SqliteEntityRepository(db_path=str(db_path))

    summary = backfill_all_roles(podcast_repo=pr, entity_repo=er, path_manager=pm)
    assert summary.podcasts_processed == 1
    assert summary.podcasts_with_hosts == 1
    assert summary.episodes_processed == 1
    assert summary.episodes_with_guests == 1
    # 2 person entities created (Alex Host, Sarah Guest) — Ad Narrator was filtered.
    assert summary.entities_created == 2


# ---------------------------------------------------------------------------
# Host evidence guardrail (episode facts speaker mappings)
# ---------------------------------------------------------------------------


def _write_episode_host_facts(storage: Path, podcast_slug: str, slug: str, lines: list[str]) -> None:
    pm = PathManager(str(storage))
    ep_dir = pm.episode_facts_dir() / podcast_slug
    ep_dir.mkdir(parents=True, exist_ok=True)
    body = "\n".join(f"- {line}" for line in lines)
    (ep_dir / f"{slug}.facts.md").write_text(
        f"""# Episode

## Speaker Mapping
{body}
"""
    )


def _podcast_facts_with_hosts(storage: Path, podcast_slug: str, host_lines: list[str]) -> None:
    pm = PathManager(str(storage))
    pm.podcast_facts_dir().mkdir(parents=True, exist_ok=True)
    body = "\n".join(f"- {line}" for line in host_lines)
    pm.podcast_facts_file(podcast_slug).write_text(
        f"""## Hosts
{body}
"""
    )


def test_one_off_cohost_demoted_by_episode_evidence(tmp_path):
    """The Cheeky Pint failure: a guest co-host on the first-cleaned episode
    lands in ``## Hosts`` and would otherwise read as host of every episode."""
    db_path, podcast_id, podcast_slug, _, _ = _seed_minimal_db(tmp_path)
    _podcast_facts_with_hosts(tmp_path, podcast_slug, ["Alex Host - the regular", "Elad Guest - one-off co-host"])
    # Six episode facts corroborate Alex; Elad appears as host in only one.
    for i in range(6):
        lines = ["SPEAKER_01: Alex Host (Host)"]
        if i == 0:
            lines.append("SPEAKER_03: Elad Guest (Host)")
        _write_episode_host_facts(tmp_path, podcast_slug, f"ep-{i}", lines)

    er = SqliteEntityRepository(db_path=str(db_path))
    result = link_podcast_roles(
        podcast_id=podcast_id, podcast_slug=podcast_slug, entity_repo=er, path_manager=PathManager(str(tmp_path))
    )

    assert result.hosts == ["person:alex-host"]
    assert result.demoted_hosts == ["person:elad-guest"]
    with sqlite3.connect(str(db_path)) as conn:
        row = conn.execute("SELECT host_entity_ids FROM podcasts").fetchone()
    assert row[0] == '["person:alex-host"]'


def test_hosts_kept_when_too_few_annotated_episodes(tmp_path):
    """Below the evidence minimum the LLM's host list is trusted as-is."""
    db_path, podcast_id, podcast_slug, _, _ = _seed_minimal_db(tmp_path)
    _podcast_facts_with_hosts(tmp_path, podcast_slug, ["Alex Host - the regular", "Elad Guest - one-off co-host"])
    for i in range(3):
        _write_episode_host_facts(tmp_path, podcast_slug, f"ep-{i}", ["SPEAKER_01: Alex Host (Host)"])

    er = SqliteEntityRepository(db_path=str(db_path))
    result = link_podcast_roles(
        podcast_id=podcast_id, podcast_slug=podcast_slug, entity_repo=er, path_manager=PathManager(str(tmp_path))
    )

    assert result.hosts == ["person:alex-host", "person:elad-guest"]
    assert result.demoted_hosts == []


def test_hosts_kept_when_demotion_would_empty_list(tmp_path):
    """If no listed host survives the evidence check, the evidence itself is
    suspect for this podcast — keep the original list rather than emptying it."""
    db_path, podcast_id, podcast_slug, _, _ = _seed_minimal_db(tmp_path)
    _podcast_facts_with_hosts(tmp_path, podcast_slug, ["Alex Host - the regular"])
    # Six annotated episodes, but they all corroborate someone not listed.
    er = SqliteEntityRepository(db_path=str(db_path))
    from thestill.models.entities import EntityRecord, EntityType

    er.upsert_entity(EntityRecord(id="person:real-host", type=EntityType.PERSON, canonical_name="Real Host"))
    for i in range(6):
        _write_episode_host_facts(tmp_path, podcast_slug, f"ep-{i}", ["SPEAKER_01: Real Host (Host)"])

    result = link_podcast_roles(
        podcast_id=podcast_id, podcast_slug=podcast_slug, entity_repo=er, path_manager=PathManager(str(tmp_path))
    )

    assert result.hosts == ["person:alex-host"]
    assert result.demoted_hosts == []


def test_collect_host_evidence_counts_and_ignores_guests(tmp_path):
    from thestill.models.entities import EntityRecord, EntityType
    from thestill.services.role_linker import collect_host_evidence

    db_path, _, podcast_slug, _, _ = _seed_minimal_db(tmp_path)
    er = SqliteEntityRepository(db_path=str(db_path))
    for eid, name in [("person:jane-doe", "Jane Doe"), ("person:john-roe", "John Roe")]:
        er.upsert_entity(EntityRecord(id=eid, type=EntityType.PERSON, canonical_name=name))

    _write_episode_host_facts(
        tmp_path,
        podcast_slug,
        "ep-0",
        ["SPEAKER_01: Jane Doe (Host)", "SPEAKER_02: John Roe (Co-Host)", "SPEAKER_03: Guesty McGuest (Guest)"],
    )
    _write_episode_host_facts(tmp_path, podcast_slug, "ep-1", ["Jane Doe (Host)"])

    files, counts = collect_host_evidence(
        podcast_slug=podcast_slug, entity_repo=er, path_manager=PathManager(str(tmp_path))
    )
    assert files == 2
    assert counts == {"person:jane-doe": 2, "person:john-roe": 1}
