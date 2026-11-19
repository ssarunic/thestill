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

"""Every curation write goes through an audited service.

The CLI and the admin API share ``EntityCurationService`` and
``PodcastCurationService``; the audit row is written by the service, so no
entry point can forget it. Runs against real SQLite repositories.
"""

from __future__ import annotations

import uuid
from datetime import datetime, timezone

import pytest

from thestill.core.entity_review import CorrectionResult
from thestill.models.entities import EntityMention, EntityRecord, EntityType, MentionRole
from thestill.models.podcast import Episode, Podcast
from thestill.repositories.audit_log_repository import AuditEntry, AuditLogRepository
from thestill.repositories.sqlite_entity_repository import SqliteEntityRepository
from thestill.repositories.sqlite_podcast_repository import SqlitePodcastRepository
from thestill.services import entity_curation
from thestill.services.abuse.actor import Actor
from thestill.services.abuse.audit_service import AuditLog
from thestill.services.abuse.errors import AdminActionError
from thestill.services.entity_curation import EntityCurationService
from thestill.services.podcast_curation import PodcastCurationService

PODCAST_ID = str(uuid.uuid4())
EPISODE_ID = str(uuid.uuid4())


class _Entries(AuditLogRepository):
    def __init__(self):
        self.entries: list[AuditEntry] = []

    def append(self, entry):
        self.entries.append(entry)

    def list(self, **_):  # pragma: no cover - unused
        return self.entries

    def count(self, **_):  # pragma: no cover - unused
        return len(self.entries)


@pytest.fixture
def world(tmp_path):
    db = str(tmp_path / "podcasts.db")
    podcasts = SqlitePodcastRepository(db_path=db)
    podcasts.save(
        Podcast(
            id=PODCAST_ID,
            rss_url="https://example.com/feed.xml",
            title="Show",
            description="",
            episodes=[
                Episode(
                    id=EPISODE_ID,
                    external_id="e1",
                    title="Ep",
                    description="",
                    audio_url="https://example.com/e1.mp3",
                    pub_date=datetime(2026, 1, 1, tzinfo=timezone.utc),
                )
            ],
        )
    )
    entities = SqliteEntityRepository(db_path=db)
    for eid, name in (("person:ada", "Ada Lovelace"), ("person:alan", "Alan Turing")):
        entities.upsert_entity(EntityRecord(id=eid, type=EntityType.PERSON, canonical_name=name))
    entities.insert_mentions(
        [
            EntityMention(
                episode_id=EPISODE_ID,
                segment_id=1,
                start_ms=1000,
                end_ms=2000,
                role=MentionRole.MENTIONED,
                surface_form="Ada",
                quote_excerpt="Ada wrote it.",
                confidence=0.9,
                extractor="test",
            )
        ]
    )
    mention_id = entities.list_pending_mentions()[0].id
    entries = _Entries()
    audit = AuditLog(entries)
    return {
        "entities": entities,
        "podcasts": podcasts,
        "entries": entries,
        "mention_id": mention_id,
        "curation": EntityCurationService(entities, audit),
        "podcast_curation": PodcastCurationService(podcasts, audit),
    }


def _last(world):
    entry = world["entries"].entries[-1]
    return entry.action, entry.actor, entry.details


ACTOR = Actor.cli()


class TestMentions:
    def test_drop_marks_dropped_stores_override_and_audits(self, world):
        world["curation"].drop_mention(world["mention_id"], actor=ACTOR, reason="ad read")
        assert world["entities"].get_mention(world["mention_id"]).resolution_status.value == "dropped"
        assert world["entities"].list_overrides()[0]["override_kind"] == "drop"
        action, actor, details = _last(world)
        assert (action, actor) == ("mention.drop", "cli")
        assert details["mention_id"] == world["mention_id"] and details["reason"] == "ad read"

    def test_repoint_resolves_to_the_target_and_audits(self, world):
        world["curation"].repoint_mention(world["mention_id"], "person:ada", actor=ACTOR)
        mention = world["entities"].get_mention(world["mention_id"])
        assert mention.entity_id == "person:ada"
        assert _last(world)[0] == "mention.repoint"

    def test_unknown_mention_or_entity_is_refused_without_audit(self, world):
        with pytest.raises(AdminActionError):
            world["curation"].drop_mention(999_999, actor=ACTOR)
        with pytest.raises(AdminActionError):
            world["curation"].repoint_mention(world["mention_id"], "person:nobody", actor=ACTOR)
        assert world["entries"].entries == []


class TestEntityEdits:
    def test_add_alias_audits_once(self, world):
        assert world["curation"].add_alias("person:ada", "Countess of Lovelace", actor=ACTOR) is True
        assert "Countess of Lovelace" in world["entities"].get_entity("person:ada").aliases
        assert _last(world)[0] == "entity.alias_add"
        assert world["curation"].add_alias("person:ada", "Countess of Lovelace", actor=ACTOR) is False
        assert len(world["entries"].entries) == 1

    def test_set_visibility_audits(self, world):
        world["curation"].set_visibility("person:ada", public=False, actor=ACTOR)
        assert "person:ada" in world["entities"].list_hidden_entity_ids()
        action, _, details = _last(world)
        assert action == "entity.set_visibility" and details["public"] is False


class TestRoles:
    def test_set_hosts_recurring_and_guests_audit(self, world):
        curation = world["curation"]
        curation.pin_hosts(PODCAST_ID, ["person:ada"], actor=ACTOR)
        curation.pin_recurring(PODCAST_ID, ["person:alan"], actor=ACTOR)
        curation.pin_guests(EPISODE_ID, ["person:alan"], actor=ACTOR)
        anchors = world["entities"].get_podcast_anchors(PODCAST_ID)
        assert anchors["hosts"] == ["person:ada"]
        assert anchors["recurring"] == ["person:alan"]
        assert world["entities"].get_episode_guests(EPISODE_ID) == ["person:alan"]
        assert [e.action for e in world["entries"].entries] == [
            "podcast.set_hosts",
            "podcast.set_recurring",
            "episode.set_guests",
        ]

    def test_unknown_entity_ids_are_refused_without_writing(self, world):
        with pytest.raises(AdminActionError) as exc:
            world["curation"].pin_hosts(PODCAST_ID, ["person:nobody"], actor=ACTOR)
        assert exc.value.code == "not_found"
        assert world["entries"].entries == []


class TestCorrection:
    def test_correction_is_audited(self, world, monkeypatch):
        captured = {}

        def fake_apply(**kwargs):
            captured.update(kwargs)
            return CorrectionResult(action="drop", surface_form="Ada", affected_mentions=1, episodes_enqueued=1)

        monkeypatch.setattr(entity_curation, "apply_correction", fake_apply)
        result = world["curation"].correct(
            actor=Actor.web("user-1"),
            queue_manager=object(),
            wikidata_client=None,
            action="drop",
            surface_form="Ada",
            reason="ad read",
        )
        assert result.affected_mentions == 1
        assert captured["repo"] is world["entities"]
        action, actor, details = _last(world)
        assert (action, actor) == ("entity.correction", "web")
        assert details["correction"] == "drop" and details["surface_form"] == "Ada"


class TestPodcastCuration:
    def test_show_link_is_curated_and_audited(self, world):
        world["podcast_curation"].set_show_link(PODCAST_ID, "spotify", "https://open.spotify.com/show/abc", actor=ACTOR)
        assert world["podcasts"].get_by_id(PODCAST_ID).spotify_url == "https://open.spotify.com/show/abc"
        action, _, details = _last(world)
        assert action == "podcast.set_show_link" and details["platform"] == "spotify"

    def test_show_link_must_look_like_the_platform(self, world):
        with pytest.raises(AdminActionError) as exc:
            world["podcast_curation"].set_show_link(PODCAST_ID, "spotify", "https://example.com/x", actor=ACTOR)
        assert exc.value.code == "invalid_url"
        assert world["entries"].entries == []

    def test_summary_profile_is_validated_and_audited(self, world):
        with pytest.raises(AdminActionError):
            world["podcast_curation"].set_summary_profile(PODCAST_ID, "no-such-profile", actor=ACTOR)
        world["podcast_curation"].set_summary_profile(PODCAST_ID, None, actor=ACTOR)
        assert _last(world)[0] == "podcast.set_summary_profile"

    def test_public_pages_switch_is_audited(self, world):
        world["podcast_curation"].set_public_pages(PODCAST_ID, False, actor=ACTOR)
        assert world["podcasts"].is_podcast_public(PODCAST_ID) is False
        action, _, details = _last(world)
        assert action == "podcast.set_public_pages" and details["public"] is False
