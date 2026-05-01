"""Spec #28 §2.5 — qmd_client pure-function helpers.

Doesn't spawn qmd; tests focus on the bits that can fail silently
in production: snippet line parsing and segmap binary lookup.
"""

from __future__ import annotations

from thestill.search.qmd_client import (
    QmdHit,
    _first_content_line,
    _hits_from_frame,
    _segment_for_line,
    _strip_collection_prefix,
)


class TestFirstContentLine:
    def test_returns_first_content_line_after_diff_hunk(self):
        snippet = "13: @@ -12,4 @@ (11 before, 7 after)\n14: <!-- seg id=0 t=0-1000 -->\n15: hello world"
        assert _first_content_line(snippet) == 14

    def test_handles_snippet_without_diff_hunk(self):
        snippet = "5: just a line\n6: another"
        assert _first_content_line(snippet) == 5

    def test_returns_none_on_empty(self):
        assert _first_content_line("") is None

    def test_skips_blank_lines(self):
        snippet = "\n\n  \n13: @@ -12,4 @@ (0 before, 5 after)\n14: real content"
        assert _first_content_line(snippet) == 14

    def test_handles_malformed_prefix(self):
        snippet = "no line prefix here\n14: content"
        assert _first_content_line(snippet) == 14


class TestSegmentForLine:
    def test_finds_segment_inside_range(self):
        segmap = [
            {"seg_id": 0, "line_start": 14, "line_end": 15, "start_ms": 0, "end_ms": 1000},
            {"seg_id": 1, "line_start": 17, "line_end": 18, "start_ms": 1000, "end_ms": 2000},
        ]
        result = _segment_for_line(segmap, 14)
        assert result is not None
        assert result["seg_id"] == 0

    def test_finds_segment_at_line_end(self):
        segmap = [{"seg_id": 0, "line_start": 14, "line_end": 15, "start_ms": 0, "end_ms": 1000}]
        result = _segment_for_line(segmap, 15)
        assert result is not None
        assert result["seg_id"] == 0

    def test_returns_none_for_line_in_gap(self):
        segmap = [
            {"seg_id": 0, "line_start": 14, "line_end": 15, "start_ms": 0, "end_ms": 1000},
            {"seg_id": 1, "line_start": 17, "line_end": 18, "start_ms": 1000, "end_ms": 2000},
        ]
        # Line 16 is the blank separator between blocks
        assert _segment_for_line(segmap, 16) is None

    def test_returns_none_for_line_before_first_segment(self):
        segmap = [{"seg_id": 0, "line_start": 14, "line_end": 15, "start_ms": 0, "end_ms": 1000}]
        # Line 5 is in frontmatter
        assert _segment_for_line(segmap, 5) is None

    def test_empty_segmap(self):
        assert _segment_for_line([], 14) is None


class TestStripCollectionPrefix:
    def test_strips_known_prefix(self):
        assert (
            _strip_collection_prefix("thestill-corpus/episodes/show/id.md", "thestill-corpus") == "episodes/show/id.md"
        )

    def test_returns_unchanged_when_no_prefix_match(self):
        assert _strip_collection_prefix("other/path.md", "thestill-corpus") == "other/path.md"


class TestHitsFromFrame:
    def test_parses_structured_results(self):
        frame = {
            "result": {
                "structuredContent": {
                    "results": [
                        {
                            "docid": "#abc",
                            "file": "thestill-corpus/episodes/show/id.md",
                            "title": "Some Episode",
                            "score": 0.85,
                            "snippet": "1: header\n2: body",
                        }
                    ]
                }
            }
        }
        hits = _hits_from_frame(frame)
        assert len(hits) == 1
        assert hits[0].docid == "#abc"
        assert hits[0].file == "thestill-corpus/episodes/show/id.md"
        assert hits[0].score == 0.85

    def test_handles_empty_results(self):
        assert _hits_from_frame({"result": {"structuredContent": {"results": []}}}) == []
        assert _hits_from_frame({}) == []

    def test_handles_missing_score(self):
        frame = {
            "result": {
                "structuredContent": {"results": [{"docid": "#abc", "file": "x.md", "title": "Y", "snippet": "z"}]}
            }
        }
        hits = _hits_from_frame(frame)
        assert len(hits) == 1
        assert hits[0].score == 0.0
