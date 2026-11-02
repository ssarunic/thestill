"""Spec #110 §3 — the effective transcript: replay, conflicts, reverts, cache."""

from __future__ import annotations

import re
from pathlib import Path

import pytest

from tests.unit.transcript_edit_fixtures import (
    EPISODE_ID,
    RAW_REL,
    SIDECAR_REL,
    FakeEditRepository,
    make_edit,
    make_episode,
    make_raw,
    make_sidecar,
    segment_anchor,
    write_transcripts,
)
from thestill.core import transcript_edits as module
from thestill.core.transcript_edits import (
    EDITED_KEY,
    SPEAKER_ENTITY_KEY,
    WORDS_STALE_KEY,
    EditLogUnavailable,
    clear_cache,
    live_edits,
    load_effective_transcript,
    raw_sha256,
    unnamed_content_segments,
)
from thestill.models.transcript_edits import text_sha256

PACKAGE = Path(module.__file__).resolve().parents[1]


@pytest.fixture(autouse=True)
def _fresh_cache():
    clear_cache()
    yield
    clear_cache()


@pytest.fixture
def world(tmp_path):
    raw = make_raw()
    sidecar = make_sidecar(raw)
    paths = write_transcripts(tmp_path, raw, sidecar)
    repo = FakeEditRepository()
    raw_hash = raw_sha256(paths.raw_transcript_file(RAW_REL).read_text(encoding="utf-8"))
    return {
        "raw": raw,
        "sidecar": sidecar,
        "paths": paths,
        "repo": repo,
        "raw_sha": raw_hash,
        "episode": make_episode(),
    }


def _load(world, **kwargs):
    return load_effective_transcript(world["episode"], edits=world["repo"], path_manager=world["paths"], **kwargs)


def _add(world, op, anchor, args, **kwargs):
    row = make_edit(op, anchor, args, raw_sha256=world["raw_sha"], **kwargs)
    return world["repo"].append(row, expected_last_seq=world["repo"].last_seq(EPISODE_ID))


def test_loader_is_the_only_sidecar_parse_in_the_package():
    """Spec #110 §3 — path drift guard: no reader opens the sidecar directly."""
    offenders = []
    for path in PACKAGE.rglob("*.py"):
        if path.name == "transcript_edits.py" and path.parent.name == "core":
            continue
        text = path.read_text(encoding="utf-8")
        if re.search(r"AnnotatedTranscript\.model_validate_json\(", text):
            offenders.append(str(path.relative_to(PACKAGE)))
    assert offenders == [], f"sidecar parsed outside the loader: {offenders}"


def test_unedited_episode_is_the_sidecar_as_written(world):
    effective = _load(world)
    assert effective is not None
    assert effective.transcript.model_dump() == world["sidecar"].model_dump()
    assert effective.applied == [] and effective.conflicts == [] and effective.last_seq == 0
    assert not effective.has_applied_edits


def test_missing_sidecar_is_none(world):
    assert (
        load_effective_transcript(
            make_episode(clean_transcript_json_path=None), edits=world["repo"], path_manager=world["paths"]
        )
        is None
    )
    world["paths"].clean_transcript_file(SIDECAR_REL).unlink()
    assert _load(world) is None


def test_include_labels_lists_diarization_labels_without_ops(world):
    effective = _load(world, include_labels=True)
    labels = {s.label: s for s in effective.speakers}
    assert set(labels) == {"SPEAKER_00", "SPEAKER_01"}
    assert labels["SPEAKER_00"].name == "Ada Lovelace"
    assert labels["SPEAKER_01"].name == "SPEAKER_01"
    assert len(labels["SPEAKER_01"].segment_ids) == 2
    assert unnamed_content_segments(effective.transcript) == 2


def test_rename_label_names_every_segment_of_that_voice(world):
    _add(
        world,
        "speaker.rename_label",
        {"label": "SPEAKER_01"},
        {"name": "Grace Hopper", "entity_id": "person:grace-hopper"},
    )
    effective = _load(world)
    named = [s for s in effective.transcript.segments if s.speaker == "Grace Hopper"]
    assert len(named) == 2
    assert all(s.metadata[EDITED_KEY] and s.metadata[SPEAKER_ENTITY_KEY] == "person:grace-hopper" for s in named)
    assert unnamed_content_segments(effective.transcript) == 0
    assert effective.transcript.algorithm_version == "v1"  # not structural
    panel = {s.label: s for s in effective.speakers}["SPEAKER_01"]
    assert panel.name == "Grace Hopper" and panel.entity_id == "person:grace-hopper"


def test_merge_labels_moves_segments_and_drops_the_label(world):
    _add(world, "speaker.merge_labels", {"label": "SPEAKER_01"}, {"into_label": "SPEAKER_00"})
    effective = _load(world)
    assert all(s.speaker == "Ada Lovelace" for s in effective.transcript.segments if s.kind == "content")
    assert [s.label for s in effective.speakers] == ["SPEAKER_00"]
    assert len(effective.speakers[0].segment_ids) == 4


def test_set_speaker_to_a_label_and_to_a_name(world):
    sidecar = world["sidecar"]
    target = sidecar.segments[1]  # SPEAKER_01's first turn
    _add(world, "segment.set_speaker", segment_anchor(target), {"label": "SPEAKER_00"})
    effective = _load(world)
    assert effective.transcript.segments[1].speaker == "Ada Lovelace"
    assert effective.labels_by_segment[1] == "SPEAKER_00"

    other = effective.transcript.segments[4]
    _add(world, "segment.set_speaker", segment_anchor(other), {"name": "Guest Voice", "entity_id": "person:guest"})
    effective = _load(world)
    assert effective.transcript.segments[4].speaker == "Guest Voice"
    assert effective.transcript.segments[4].metadata[SPEAKER_ENTITY_KEY] == "person:guest"
    assert 4 not in effective.labels_by_segment


def test_set_text_marks_words_stale_and_keeps_timing(world):
    target = world["sidecar"].segments[0]
    _add(
        world,
        "segment.set_text",
        segment_anchor(target),
        {"old_text": target.text, "new_text": "Welcome back to the show, everyone."},
    )
    effective = _load(world)
    seg = effective.transcript.segments[0]
    assert seg.text == "Welcome back to the show, everyone."
    assert seg.metadata[WORDS_STALE_KEY] is True and seg.metadata[EDITED_KEY] is True
    assert (seg.start, seg.end) == (target.start, target.end)
    assert seg.user_segment_id


def test_set_kind_retags(world):
    target = world["sidecar"].segments[0]
    _add(world, "segment.set_kind", segment_anchor(target), {"kind": "intro"})
    assert _load(world).transcript.segments[0].kind == "intro"


def test_split_renumbers_and_lands_on_a_raw_word(world):
    target = world["sidecar"].segments[3]  # "Today we talk about OKRs and why they fail" (raw seg 2)
    _add(world, "segment.split", segment_anchor(target), {"text_word_index": 4})
    effective = _load(world)
    segments = effective.transcript.segments
    assert [s.id for s in segments] == list(range(len(segments)))
    left, right = segments[3], segments[4]
    assert left.text == "Today we talk about" and right.text == "OKRs and why they fail"
    assert left.end == right.start == pytest.approx(10.0)  # raw word 4 of segment 2 starts at 8.0 + 4*0.5
    assert left.source_word_span.end_word_index == 3 and right.source_word_span.start_word_index == 4
    assert left.user_segment_id and right.user_segment_id and left.user_segment_id != right.user_segment_id
    assert effective.transcript.algorithm_version == "v1+edits.1"
    # Each child is addressable on its own by its word span.
    _add(
        world,
        "segment.set_text",
        segment_anchor(right),
        {"old_text": right.text, "new_text": "OKRs, and why they fail"},
    )
    effective = _load(world)
    assert effective.transcript.segments[4].text == "OKRs, and why they fail"
    assert effective.transcript.segments[3].text == "Today we talk about"


def test_split_children_without_word_anchors_stay_addressable(world):
    """Children of a split share ids and span when the segment had no word
    span; their text hashes tell them apart on later ops."""
    ad = world["sidecar"].segments[2]
    assert ad.kind == "ad_break" and ad.source_word_span is None
    _add(world, "segment.split", segment_anchor(ad), {"text_word_index": 4})
    effective = _load(world)
    left, right = effective.transcript.segments[2], effective.transcript.segments[3]
    assert left.source_segment_ids == right.source_segment_ids and left.source_word_span is None
    _add(
        world,
        "segment.set_text",
        segment_anchor(right),
        {"old_text": right.text, "new_text": "by Sponsor Corp, really"},
    )
    _add(world, "segment.set_kind", segment_anchor(left), {"kind": "intro"})
    effective = _load(world)
    assert effective.conflicts == []
    assert effective.transcript.segments[2].kind == "intro"
    assert effective.transcript.segments[3].text == "by Sponsor Corp, really"
    # An anchor whose text matches neither child is a text change, not ambiguity.
    stale = effective.transcript.segments[2].model_copy(update={"text": "something else entirely"})
    row = _add(world, "segment.set_kind", segment_anchor(stale), {"kind": "outro"})
    assert [c.reason for c in _load(world).conflicts if c.edit_id == row.id] == ["text_changed"]


def test_merge_joins_adjacent_segments_and_rejects_others(world):
    sidecar = world["sidecar"]
    a, b = sidecar.segments[3], sidecar.segments[4]
    _add(world, "segment.merge", {"first": segment_anchor(a), "second": segment_anchor(b)}, {})
    effective = _load(world)
    merged = effective.transcript.segments[3]
    assert merged.text == f"{a.text} {b.text}"
    assert merged.source_segment_ids == [2, 3]
    assert merged.source_word_span.end_segment_id == 3
    assert len(effective.transcript.segments) == len(sidecar.segments) - 1

    first, other = sidecar.segments[0], sidecar.segments[1]
    row = _add(world, "segment.merge", {"first": segment_anchor(other), "second": segment_anchor(first)}, {})
    effective = _load(world)
    assert [c.reason for c in effective.conflicts if c.edit_id == row.id] == ["not_adjacent"]


def test_revert_and_revert_of_revert(world):
    target = world["sidecar"].segments[0]
    rename = _add(world, "segment.set_kind", segment_anchor(target), {"kind": "intro"})
    undo = _add(world, "revert", {"edit_id": rename.id}, {}, reverts=rename.id)
    effective = _load(world)
    assert effective.transcript.segments[0].kind == "content"
    assert rename.id in effective.reverted_ids and effective.applied == []

    _add(world, "revert", {"edit_id": undo.id}, {}, reverts=undo.id)
    effective = _load(world)
    assert effective.transcript.segments[0].kind == "intro"
    assert rename.id not in effective.reverted_ids
    live, reverted = live_edits(world["repo"].list_for_episode(EPISODE_ID))
    assert [r.id for r in live] == [rename.id] and reverted == {undo.id}


def test_text_changed_conflict_after_a_reclean(world):
    target = world["sidecar"].segments[0]
    row = _add(world, "segment.set_kind", segment_anchor(target), {"kind": "intro"})
    # A re-clean rewrote the segment text but kept its anchors.
    recleaned = make_sidecar(world["raw"])
    recleaned.segments[0].text = "Welcome back to the programme, everyone"
    write_transcripts(world["paths"].storage_path, world["raw"], recleaned)
    effective = _load(world)
    conflict = effective.conflicts[0]
    assert (conflict.edit_id, conflict.reason) == (row.id, "text_changed")
    assert conflict.detail["current_text"] == "Welcome back to the programme, everyone"
    assert effective.transcript.segments[0].kind == "content"


def test_anchor_missing_conflict(world):
    row = _add(
        world,
        "segment.set_kind",
        {"source_segment_ids": [42], "source_word_span": None, "start_s": 0, "end_s": 1, "text": "x"},
        {"kind": "intro"},
    )
    assert [(c.edit_id, c.reason) for c in _load(world).conflicts] == [(row.id, "anchor_missing")]


def test_label_missing_conflict(world):
    row = _add(world, "speaker.rename_label", {"label": "SPEAKER_07"}, {"name": "Nobody"})
    assert [(c.edit_id, c.reason) for c in _load(world).conflicts] == [(row.id, "label_missing")]


def test_retranscribe_conflicts_every_op_and_busts_the_cache(world):
    _add(world, "speaker.rename_label", {"label": "SPEAKER_01"}, {"name": "Grace Hopper"})
    assert _load(world).applied  # cached as applied
    # A re-transcribe replaces the raw JSON; the old sidecar stays until the clean lands.
    raw2 = make_raw()
    raw2.segments[0].text = "Welcome back to the show everyone again"
    world["paths"].raw_transcript_file(RAW_REL).write_text(raw2.model_dump_json(), encoding="utf-8")
    effective = _load(world)
    assert effective.applied == []
    assert [c.reason for c in effective.conflicts] == ["retranscribed"]
    assert all(s.speaker != "Grace Hopper" for s in effective.transcript.segments)


def test_cached_result_is_not_shared_by_reference(world):
    _add(world, "speaker.rename_label", {"label": "SPEAKER_01"}, {"name": "Grace Hopper"})
    first = _load(world)
    first.transcript.playback_time_offset_seconds = 42.0
    first.transcript.segments[0].text = "mutated"
    second = _load(world)
    assert second.transcript.playback_time_offset_seconds == 0.0
    assert second.transcript.segments[0].text != "mutated"


def test_unreadable_log_raises_or_falls_back(world):
    class Broken(FakeEditRepository):
        def list_for_episode(self, episode_id):
            raise RuntimeError("db down")

    world["repo"] = Broken()
    with pytest.raises(EditLogUnavailable):
        _load(world)
    effective = _load(world, on_log_error="fallback")
    assert effective.overlay_failed and effective.transcript.model_dump() == world["sidecar"].model_dump()


def test_summarizer_markdown_strips_ads_like_the_cleaned_file(world):
    _add(world, "speaker.rename_label", {"label": "SPEAKER_01"}, {"name": "Grace Hopper"})
    markdown = _load(world).to_summarizer_markdown()
    assert "Sponsor Corp" not in markdown
    assert "Grace Hopper" in markdown


def test_anchor_hash_matches_the_text_the_editor_showed(world):
    segment = world["sidecar"].segments[0]
    from thestill.models.transcript_edits import SegmentAnchor

    anchor = SegmentAnchor.model_validate(segment_anchor(segment))
    assert anchor.text_sha256 == text_sha256(segment.text)
