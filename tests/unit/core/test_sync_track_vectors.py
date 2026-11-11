"""The alignment vectors shared with the browser aligner (spec #116, Phase 2)."""

import json
import os

from tests.unit.core.sync_track_vectors import VECTORS_PATH, build_vectors


def _dump(vectors) -> str:
    return json.dumps(vectors, indent=1, sort_keys=True) + "\n"


def test_committed_vectors_match_the_python_aligner():
    """The fixture the frontend tests read is what this code produces now.

    A change to the parser, the digest or the fitter changes the vectors;
    regenerate them (see ``sync_track_vectors``) and the frontend tests then
    hold the TypeScript aligner to the new behaviour.
    """
    current = _dump(build_vectors())
    if os.environ.get("SYNC_TRACK_WRITE_VECTORS") == "1":
        VECTORS_PATH.parent.mkdir(parents=True, exist_ok=True)
        VECTORS_PATH.write_text(current, encoding="utf-8")
    assert VECTORS_PATH.read_text(encoding="utf-8") == current


def test_vectors_exercise_what_they_claim():
    """Guard the recipes themselves: each case shows the behaviour it names."""
    by_name = {case["name"]: case["expected"] for case in build_vectors()["cases"]}

    identical = by_name["identical copy"]["spans"]
    assert len(identical) == 1 and abs(identical[0]["offset_s"]) < 0.01

    spliced = by_name["pre-roll added and a longer mid-roll"]["spans"]
    assert len(spliced) == 2
    assert abs(spliced[0]["offset_s"] - 8) < 0.1 and abs(spliced[1]["offset_s"] - 17) < 0.1

    removed = by_name["ad only in the transcribed copy"]["spans"]
    assert len(removed) == 2 and abs(removed[0]["offset_s"]) < 0.01 and abs(removed[1]["offset_s"] + 10) < 0.1

    swapped = by_name["ad replaced by another of the same length"]["spans"]
    assert len(swapped) == 2 and all(abs(span["offset_s"]) < 0.01 for span in swapped)

    twice = by_name["promo played twice in the listener copy"]["spans"]
    # The promo's second playing (16 s later) is not taken for the first.
    assert [round(span["offset_s"]) for span in twice] == [0, 8]
    assert twice[0]["src_start_s"] == 0

    messy = by_name["tags, junk and silence between segments"]["spans"]
    assert messy and all(abs(span["offset_s"] - 5) < 0.1 for span in messy)

    assert by_name["re-encoded copy"]["spans"] == []
    assert by_name["re-encoded copy"]["anchors_hit"] == 0
