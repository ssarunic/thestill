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

"""``EpisodeCurationService.fold_import_twins``: which pairs fold, and the audit trail."""

from datetime import datetime, timezone

from thestill.services.abuse.actor import Actor
from thestill.services.episode_curation import EpisodeCurationService

_DAY = "2026-10-04T02:00:00+00:00"


def _row(keeper_id, twin_id, keeper_pub=_DAY, twin_pub=_DAY):
    return {
        "keeper_id": keeper_id,
        "twin_id": twin_id,
        "podcast_slug": "generation-do-it-yourself",
        "episode_slug": f"ep-{keeper_id}",
        "keeper_pub_date": keeper_pub,
        "twin_pub_date": twin_pub,
        "twin_external_id": f"guid-{twin_id}",
        "twin_audio_url": f"https://cdn.example.com/{twin_id}.mp3",
    }


class _Repo:
    def __init__(self, rows, refuse=()):
        self.rows = rows
        self.refuse = set(refuse)
        self.folded = []

    def find_import_twin_candidates(self, podcast_id=None):
        return list(self.rows)

    def fold_import_twin(self, keeper_id, twin_id):
        if twin_id in self.refuse:
            return False
        self.folded.append((keeper_id, twin_id))
        return True


class _Audit:
    def __init__(self):
        self.entries = []

    def record(self, actor, action, **details):
        self.entries.append((actor.kind, action, details))


def test_report_only_by_default():
    repo, audit = _Repo([_row("k1", "t1")]), _Audit()
    result = EpisodeCurationService(repo, audit).fold_import_twins(actor=Actor.cli())

    assert [t.twin_id for t in result.twins] == ["t1"]
    assert not result.applied and result.folded == 0
    assert repo.folded == [] and audit.entries == []


def test_apply_folds_and_audits_each_pair():
    repo, audit = _Repo([_row("k1", "t1"), _row("k2", "t2")], refuse={"t2"}), _Audit()
    result = EpisodeCurationService(repo, audit).fold_import_twins(actor=Actor.cli(), apply=True)

    assert result.folded == 1
    assert repo.folded == [("k1", "t1")]
    ((kind, action, details),) = audit.entries
    assert (kind, action) == ("cli", "episode.fold_import_twin")
    assert details["twin_id"] == "t1" and details["twin_external_id"] == "guid-t1"


def test_publish_dates_must_agree():
    # A rerun sharing the slug ("Best of: …") is not a twin.
    rows = [
        _row("k1", "t1", twin_pub="2024-10-04T02:00:00+00:00"),
        _row("k2", "t2", keeper_pub=datetime(2026, 10, 4, 3, tzinfo=timezone.utc), twin_pub=_DAY),
        _row("k3", "t3", twin_pub=None),
    ]
    twins = EpisodeCurationService(_Repo(rows), _Audit()).find_import_twins()
    assert [t.twin_id for t in twins] == ["t2"]


def test_a_row_in_two_pairs_is_left_alone():
    rows = [_row("k1", "t1"), _row("k1", "t2"), _row("k3", "t3")]
    twins = EpisodeCurationService(_Repo(rows), _Audit()).find_import_twins()
    assert [t.twin_id for t in twins] == ["t3"]
