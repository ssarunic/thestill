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

"""Spec #81 - a QID is the identity; the slug id is only its handle.

Wikidata labels are not unique. Storing a second "Alex Smith" under
``person:alex-smith`` would let ``upsert_entity`` write its QID over the
first one's, silently changing who every existing mention points at.
"""

import sqlite3

import pytest

from thestill.core.entity_linking.shared import ResolutionResult
from thestill.core.task_handlers import _with_stable_identity, resolve_pending_mentions
from thestill.models.entities import EntityMention, EntityRecord, EntityType, ResolutionMethod
from thestill.repositories.sqlite_entity_repository import SqliteEntityRepository
from thestill.repositories.sqlite_podcast_repository import SqlitePodcastRepository

POD = "11111111-0000-4000-8000-000000000000"
EP = "22222222-0000-4000-8000-000000000000"


def _person(name, qid, entity_id=None):
    slug = name.lower().replace(" ", "-")
    return EntityRecord(id=entity_id or f"person:{slug}", type=EntityType.PERSON, canonical_name=name, wikidata_qid=qid)


@pytest.fixture
def repo(tmp_path):
    db = str(tmp_path / "identity.db")
    SqlitePodcastRepository(db_path=db)
    with sqlite3.connect(db) as conn:
        conn.execute(
            "INSERT INTO podcasts (id, rss_url, title, description) VALUES (?, 'https://x/y', 'Show', '')", (POD,)
        )
        conn.execute(
            "INSERT INTO episodes (id, podcast_id, external_id, title, description, audio_url) "
            "VALUES (?, ?, 'e1', 'Ep', '', 'https://x/e.mp3')",
            (EP, POD),
        )
    return SqliteEntityRepository(db_path=db)


def test_a_second_entity_with_the_same_label_does_not_take_over_the_first(repo):
    repo.upsert_entity(_person("Alex Smith", "Q111"))
    second = _with_stable_identity(repo, _person("Alex Smith", "Q222"))
    assert second.id == "person:alex-smith-q222"
    repo.upsert_entity(second)
    assert repo.get_entity("person:alex-smith").wikidata_qid == "Q111"
    assert repo.get_entity("person:alex-smith-q222").wikidata_qid == "Q222"


def test_the_disambiguated_id_is_stable_on_the_next_episode(repo):
    repo.upsert_entity(_person("Alex Smith", "Q111"))
    repo.upsert_entity(_with_stable_identity(repo, _person("Alex Smith", "Q222")))
    assert _with_stable_identity(repo, _person("Alex Smith", "Q222")).id == "person:alex-smith-q222"


def test_a_known_qid_keeps_its_row_even_under_another_name(repo):
    """ReFinED names by Wikipedia title, the live linker by Wikidata label."""
    repo.upsert_entity(_person("Amazon (company)", "Q3884", entity_id="company:amazon-company"))
    assert (
        _with_stable_identity(repo, _person("Amazon", "Q3884", entity_id="company:amazon")).id
        == "company:amazon-company"
    )


def test_a_local_entity_without_a_qid_is_grounded_not_duplicated(repo):
    repo.upsert_entity(_person("Dario Amodei", None))
    linked = _with_stable_identity(repo, _person("Dario Amodei", "Q100"))
    assert linked.id == "person:dario-amodei"


def test_an_entity_without_a_qid_is_left_alone(repo):
    local = _person("Nobody Known", None)
    assert _with_stable_identity(repo, local) is local


def test_the_resolve_core_keeps_existing_mentions_on_their_entity(repo):
    """The reported scenario, end to end through the shared core."""
    repo.upsert_entity(_person("Alex Smith", "Q111"))
    repo.insert_mentions(
        [
            EntityMention(
                episode_id=EP,
                segment_id=i,
                start_ms=i,
                end_ms=i + 1,
                surface_form="Alex Smith",
                surface_label="person",
                quote_excerpt="... Alex Smith ...",
                confidence=0.9,
                extractor="gliner:test",
            )
            for i in (1, 2)
        ]
    )
    first, second = repo.list_pending_mentions(episode_id=EP)
    repo.resolve_mention(mention_id=first.id, entity_id="person:alex-smith", status="resolved", method="direct")

    class OtherAlex:
        def resolve(self, mentions, *, is_blacklisted=None, context=None):
            return [
                ResolutionResult(m.id, _person("Alex Smith", "Q222"), "resolved", ResolutionMethod.LLM_LINKED)
                for m in mentions
            ]

    resolve_pending_mentions(repo, OtherAlex(), [second], episode_id=EP)

    assert repo.get_entity("person:alex-smith").wikidata_qid == "Q111"
    with sqlite3.connect(str(repo.db_path)) as conn:
        linked = dict(conn.execute("SELECT id, entity_id FROM entity_mentions"))
    assert linked == {first.id: "person:alex-smith", second.id: "person:alex-smith-q222"}
