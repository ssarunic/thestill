"""Spec #92 — loading the Resource List plan from real storage."""

from __future__ import annotations

import json
from types import SimpleNamespace

import pytest

from thestill.core.resource_seeds import ResourceSource, make_resource_source, plan_context
from thestill.models.entities import EntityRecord, EntityType
from thestill.models.podcast import Episode
from thestill.utils.file_storage.local import LocalFileStorage
from thestill.utils.path_manager import PathManager

SUMMARY = "## 8. 📚 Resource List\n* Mad Men (TV Show) [12:09](?t=729&cite=c1)\n* Ramp (Tool) [02:10]\n"
TRANSCRIPT = {
    "episode_id": "",
    "segments": [
        {"id": 0, "start": 100, "end": 110, "speaker": "A", "text": "The Ramp data is in.", "kind": "content"},
        {"id": 1, "start": 700, "end": 720, "speaker": "B", "text": "Watching Mad Men.", "kind": "content"},
    ],
}


@pytest.fixture
def storage(tmp_path):
    pm = PathManager(storage_path=str(tmp_path))
    (pm.summaries_dir() / "show").mkdir(parents=True)
    (pm.summaries_dir() / "show" / "ep_summary.md").write_text(SUMMARY, encoding="utf-8")
    clean = pm.clean_transcript_file("show/ep_cleaned.json")
    clean.parent.mkdir(parents=True, exist_ok=True)
    clean.write_text(json.dumps(TRANSCRIPT), encoding="utf-8")
    return pm, LocalFileStorage(str(tmp_path))


def _episode(**overrides):
    base = dict(
        id="ep-1",
        podcast_id="pod-1",
        external_id="e1",
        title="T",
        description="",
        audio_url="https://example.com/a.mp3",
        summary_path="show/ep_summary.md",
        clean_transcript_json_path="show/ep_cleaned.json",
        playback_time_offset_seconds=30.0,
    )
    base.update(overrides)
    return Episode(**base)


def test_plan_reads_summary_and_transcript_with_the_episode_offset(storage):
    pm, fs = storage
    plan = ResourceSource(path_manager=pm, file_storage=fs).plan(_episode(), plan_context([], []), stage="extract")
    # Ramp is said at raw 100 s = playback 130 s = the cited 02:10, so the one-word item is near.
    assert {(g.name, g.outcome) for g in plan.grounded} == {("Mad Men", "near"), ("Ramp", "near")}


def test_a_transcript_the_caller_holds_gets_the_offset_too(storage):
    from thestill.models.annotated_transcript import AnnotatedTranscript

    pm, fs = storage
    held = AnnotatedTranscript.model_validate(TRANSCRIPT)  # offset 0, as the extract handler loads it
    plan = ResourceSource(path_manager=pm, file_storage=fs, window_s=5).plan(
        _episode(), plan_context([], []), transcript=held, stage="extract"
    )
    assert [g.name for g in plan.grounded if g.name == "Ramp"] == ["Ramp"]
    assert held.playback_time_offset_seconds == 0.0  # the caller's object is not changed


def test_no_summary_is_no_plan(storage):
    pm, fs = storage
    source = ResourceSource(path_manager=pm, file_storage=fs)
    assert source.plan(_episode(summary_path=None), plan_context([], []), stage="extract") is None
    assert source.plan(_episode(summary_path="show/missing.md"), plan_context([], []), stage="extract") is None


class _Repo:
    def __init__(self, anchors=(), names=(), fail=False):
        self._anchors = {a.id: a for a in anchors}
        self._names = list(names)
        self._fail = fail

    def get_episode_anchors(self, episode_id):
        return list(self._anchors)

    def get_entity(self, entity_id):
        return self._anchors.get(entity_id)

    def list_extracted_names(self, episode_id):
        if self._fail:
            raise RuntimeError("db down")
        return self._names


def test_hints_for_uses_the_episode_anchors(storage):
    pm, fs = storage
    host = EntityRecord(id="tv:mad-men", type=EntityType.PRODUCT, canonical_name="Mad Men")
    hints = ResourceSource(path_manager=pm, file_storage=fs).hints_for(_Repo(anchors=[host]), _episode())
    assert hints == {"ramp": ("tool", "")}


def test_hints_for_never_raises(storage):
    pm, fs = storage
    assert ResourceSource(path_manager=pm, file_storage=fs).hints_for(_Repo(fail=True), _episode()) == {}


def test_the_flag_off_means_no_source(storage):
    pm, fs = storage
    assert make_resource_source(SimpleNamespace(entity_resource_seeds_enabled=False, file_storage=fs), pm) is None
    on = make_resource_source(SimpleNamespace(entity_resource_seeds_enabled=True, file_storage=fs), pm)
    assert isinstance(on, ResourceSource)
