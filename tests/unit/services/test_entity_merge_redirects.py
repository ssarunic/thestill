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

"""A folded entity's URL answers 301: merges record where the loser went,
and ``backfill_merge_redirects`` recovers merges made before they did."""

from __future__ import annotations

from datetime import datetime, timedelta, timezone

import pytest

from thestill.models.entities import EntityRecord, EntityType
from thestill.repositories.audit_log_repository import AuditEntry, AuditLogRepository
from thestill.repositories.sqlite_entity_repository import SqliteEntityRepository
from thestill.repositories.sqlite_podcast_repository import SqlitePodcastRepository
from thestill.services.abuse.actor import Actor
from thestill.services.abuse.audit_service import AuditLog
from thestill.services.entity_curation import EntityCurationService

ACTOR = Actor.cli()
T0 = datetime(2026, 11, 19, 10, 0, tzinfo=timezone.utc)


class _Entries(AuditLogRepository):
    """Newest first, filtered and paged like the real repositories."""

    def __init__(self):
        self.entries: list[AuditEntry] = []

    def append(self, entry):
        self.entries.append(entry)

    def list(self, *, action=None, target_user_id=None, limit=50, offset=0):
        rows = [e for e in reversed(self.entries) if action is None or e.action == action]
        return rows[offset : offset + limit]

    def count(self, *, action=None, target_user_id=None):
        return len(self.list(action=action, limit=10**9))


@pytest.fixture
def world(tmp_path):
    db = str(tmp_path / "podcasts.db")
    SqlitePodcastRepository(db_path=db)  # owns the SQLite DDL
    entities = SqliteEntityRepository(db_path=db)
    entries = _Entries()
    return entities, entries, EntityCurationService(entities, AuditLog(entries))


def _person(repo, entity_id, name=None):
    repo.upsert_entity(EntityRecord(id=entity_id, type=EntityType.PERSON, canonical_name=name or entity_id))


def _legacy_merge(entries, keeper, loser, minutes):
    """An ``entity.merge`` row as merges wrote them before redirects."""
    entries.append(
        AuditEntry(
            action="entity.merge",
            actor="cli",
            created_at=T0 + timedelta(minutes=minutes),
            details={"keeper": keeper, "loser": loser},
        )
    )


def test_merge_records_where_the_loser_went(world):
    repo, _, curation = world
    _person(repo, "person:sahar-yousef", "Sahar Yousef")
    _person(repo, "person:dr-sahar-yousef", "Dr Sahar Yousef")
    curation.merge("person:sahar-yousef", "person:dr-sahar-yousef", actor=ACTOR)
    assert repo.get_entity("person:dr-sahar-yousef") is None
    assert repo.get_entity_redirect("person:dr-sahar-yousef") == "person:sahar-yousef"


def test_a_dry_run_merge_records_nothing(world):
    repo, _, curation = world
    _person(repo, "person:a")
    _person(repo, "person:b")
    curation.merge("person:a", "person:b", actor=ACTOR, dry_run=True)
    assert repo.get_entity_redirect("person:b") is None


def test_moving_every_mention_and_deleting_the_emptied_source_redirects_it(world):
    repo, _, curation = world
    _person(repo, "person:a")
    _person(repo, "person:b")
    # No mentions: the move is a no-op, but the emptied source is deleted.
    curation.move_mentions([1], from_entity_id="person:a", to_entity_id="person:b", actor=ACTOR, delete_emptied=True)
    assert repo.get_entity("person:a") is None
    assert repo.get_entity_redirect("person:a") == "person:b"


def test_backfill_reports_then_records_audited_merges_in_order(world):
    repo, entries, curation = world
    _person(repo, "person:c")
    _legacy_merge(entries, keeper="person:b", loser="person:a", minutes=0)
    _legacy_merge(entries, keeper="person:c", loser="person:b", minutes=5)

    report = curation.backfill_merge_redirects(actor=ACTOR)
    assert not report.applied
    assert report.redirects == [("person:a", "person:b"), ("person:b", "person:c")]
    assert repo.get_entity_redirect("person:a") is None
    assert all(e.action == "entity.merge" for e in entries.entries)

    applied = curation.backfill_merge_redirects(actor=ACTOR, apply=True)
    assert applied.applied
    # Oldest first, so the chain ends at the last keeper.
    assert repo.get_entity_redirect("person:a") == "person:c"
    assert repo.get_entity_redirect("person:b") == "person:c"
    assert entries.entries[-1].action == "entity.backfill_redirects"
    assert entries.entries[-1].details["recorded"] == 2


def test_backfill_skips_a_loser_that_exists_again(world):
    repo, entries, curation = world
    _person(repo, "person:keeper")
    _person(repo, "person:back-again")
    _legacy_merge(entries, keeper="person:keeper", loser="person:back-again", minutes=0)
    result = curation.backfill_merge_redirects(actor=ACTOR, apply=True)
    assert result.redirects == [] and result.recreated == ["person:back-again"]
    assert repo.get_entity_redirect("person:back-again") is None


def test_backfill_reads_past_one_page_of_audit_rows(world):
    repo, entries, curation = world
    for n in range(620):
        _legacy_merge(entries, keeper="person:keeper", loser=f"person:loser-{n}", minutes=n)
    assert len(curation.backfill_merge_redirects(actor=ACTOR).redirects) == 620
