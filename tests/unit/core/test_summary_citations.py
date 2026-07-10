from thestill.core.summary_citations import (
    backfill_summary_citations_for_episode,
    load_valid_citations_for_api,
    resolve_summary_citations,
    summary_citations_key,
)
from thestill.models.annotated_transcript import AnnotatedSegment, AnnotatedTranscript, WordSpan
from thestill.models.podcast import Episode
from thestill.utils.file_storage import LocalFileStorage
from thestill.utils.path_manager import PathManager


def segment(
    *,
    segment_id: int,
    start: float,
    end: float,
    source_segment_ids: list[int] | None = None,
    source_word_span: WordSpan | None = None,
) -> AnnotatedSegment:
    return AnnotatedSegment(
        id=segment_id,
        start=start,
        end=end,
        speaker="Host",
        text=f"segment {segment_id}",
        kind="content",
        source_segment_ids=source_segment_ids or [segment_id],
        source_word_span=source_word_span,
    )


def transcript(
    *,
    offset: float = 0.0,
    algorithm_version: str = "v1",
    segments: list[AnnotatedSegment] | None = None,
    duration: float | None = 120.0,
) -> AnnotatedTranscript:
    return AnnotatedTranscript(
        episode_id="ep1",
        playback_time_offset_seconds=offset,
        algorithm_version=algorithm_version,
        transcript_source_duration_s=duration,
        segments=segments
        or [
            segment(segment_id=1, start=0, end=20, source_segment_ids=[101]),
            segment(segment_id=2, start=40, end=70, source_segment_ids=[102]),
        ],
    )


def episode(**overrides) -> Episode:
    base = {
        "id": "ep1",
        "external_id": "feed-guid",
        "title": "Episode",
        "description": "",
        "audio_url": "https://example.com/audio.mp3",
    }
    base.update(overrides)
    return Episode(**base)


def test_resolves_timestamp_and_rewrites_summary_link():
    word_span = WordSpan(
        start_segment_id=101,
        start_word_index=2,
        end_segment_id=101,
        end_word_index=8,
    )
    result = resolve_summary_citations(
        "Source: [00:10]",
        transcript(
            segments=[
                segment(
                    segment_id=7,
                    start=0,
                    end=20,
                    source_segment_ids=[101],
                    source_word_span=word_span,
                )
            ]
        ),
    )

    assert result.markdown == "Source: [00:10](?t=10&cite=c0)"
    assert result.resolved_count == 1
    assert result.unresolved_count == 0
    citation = result.sidecar.citations[0]
    assert citation.id == "c0"
    assert citation.raw_label == "00:10"
    assert citation.cited_playback_s == 10
    assert citation.target_playback_s == 10
    assert citation.segment_id_hint == 7
    assert citation.source_segment_ids == [101]
    assert citation.source_word_span == word_span


def test_resolves_comma_separated_timestamp_group():
    result = resolve_summary_citations("Source: [00:10, 00:45]", transcript())

    assert result.markdown == "Source: [00:10](?t=10&cite=c0), [00:45](?t=45&cite=c1)"
    assert [c.segment_id_hint for c in result.sidecar.citations] == [1, 2]


def test_rebuilds_sidecar_from_existing_summary_citation_links():
    result = resolve_summary_citations("Source: [00:10](?t=10&cite=c0)", transcript())

    assert result.markdown == "Source: [00:10](?t=10&cite=c0)"
    assert result.resolved_count == 1
    assert result.sidecar.citations[0].raw_label == "00:10"


def test_resolves_against_raw_segment_time_after_playback_offset():
    result = resolve_summary_citations(
        "Source: [00:45]",
        transcript(
            offset=30,
            segments=[
                segment(segment_id=9, start=10, end=20, source_segment_ids=[900]),
            ],
            duration=120,
        ),
    )

    citation = result.sidecar.citations[0]
    assert result.markdown == "Source: [00:45](?t=45&cite=c0)"
    assert citation.segment_id_hint == 9
    assert citation.source_segment_ids == [900]


def test_skips_code_fences_code_spans_and_existing_links():
    markdown = (
        "Inline code: `[00:10]`\n" "Existing link: [00:10](https://example.com)\n" "```md\n" "Source: [00:10]\n" "```\n"
    )

    result = resolve_summary_citations(markdown, transcript())

    assert result.markdown == markdown
    assert result.sidecar.citations == []


def test_links_whole_range_as_single_seek_to_start():
    # Timeline chapter markers are ranges: the whole span is one link that
    # seeks to the start, so it reads consistently (not TS1-as-link, TS2-plain).
    result = resolve_summary_citations("Timeline: [00:00 - 00:20]", transcript())

    assert result.markdown == "Timeline: [00:00 - 00:20](?t=0&cite=c0)"
    assert result.resolved_count == 1
    assert result.unresolved_count == 0
    citation = result.sidecar.citations[0]
    assert citation.raw_label == "00:00"
    assert citation.cited_playback_s == 0
    assert citation.segment_id_hint == 1


def test_remerges_legacy_split_range_link():
    # An earlier backfill linked only the range start and left "- TS2" as text.
    # Re-running must heal it back into one whole-range link.
    md = "Timeline: [00:00](?t=0&cite=c0) - 00:20 **Chapter**"

    result = resolve_summary_citations(md, transcript())

    assert result.markdown == "Timeline: [00:00 - 00:20](?t=0&cite=c0) **Chapter**"
    assert result.resolved_count == 1
    assert result.sidecar.citations[0].raw_label == "00:00"


def test_does_not_merge_inline_citation_followed_by_non_range_dash():
    # A normal inline citation followed by " - <non-timestamp>" must not be
    # absorbed into a range.
    result = resolve_summary_citations("Point [00:10](?t=10&cite=c0) - see notes", transcript())

    assert result.markdown == "Point [00:10](?t=10&cite=c0) - see notes"


def test_range_starting_in_intro_resolves_to_first_content_in_span():
    # Episode opens with a stripped ad/intro (no content near 00:30), but a
    # content segment begins within the chapter span, so the range still links
    # and seeks to that first content segment rather than failing.
    tr = transcript(
        segments=[
            segment(segment_id=5, start=108, end=140, source_segment_ids=[500]),
            segment(segment_id=6, start=140, end=200, source_segment_ids=[600]),
        ],
        duration=600,
    )
    result = resolve_summary_citations("Timeline: [00:30 - 02:48]", tr)

    assert result.markdown == "Timeline: [00:30 - 02:48](?t=108&cite=c0)"
    citation = result.sidecar.citations[0]
    assert citation.resolved is True
    assert citation.raw_label == "00:30"
    assert citation.cited_playback_s == 108
    assert citation.segment_id_hint == 5


def test_leaves_range_untouched_when_no_content_in_span():
    # 00:25-00:35 straddles a gap with no content segment inside it (segments
    # at 0-20 and 40-70), so the range is left exactly as written.
    result = resolve_summary_citations("Timeline: [00:25 - 00:35]", transcript())

    assert result.markdown == "Timeline: [00:25 - 00:35]"
    assert result.resolved_count == 0
    assert result.unresolved_count == 1


def test_records_unresolved_citations_without_rewriting_markdown():
    result = resolve_summary_citations("Source: [09:59]", transcript(duration=120))

    assert result.markdown == "Source: [09:59]"
    assert result.resolved_count == 0
    assert result.unresolved_count == 1
    assert result.sidecar.citations[0].resolved is False


def test_resolves_adjacent_timestamp_brackets():
    # Two citation brackets written back-to-back must not be mistaken for
    # ``[text][ref]`` reference-link syntax and silently skipped.
    result = resolve_summary_citations("Timeline: [00:10][00:45]", transcript())

    assert result.markdown == "Timeline: [00:10](?t=10&cite=c0)[00:45](?t=45&cite=c1)"
    assert result.resolved_count == 2
    assert result.unresolved_count == 0
    assert [c.segment_id_hint for c in result.sidecar.citations] == [1, 2]


def test_mixed_group_keeps_brackets_on_unresolved_sibling():
    # ``00:10`` resolves to segment 1; ``00:30`` falls in the 20-40 gap beyond
    # the snap tolerance, so it stays an inert but still-bracketed marker rather
    # than losing the group's brackets and rendering as bare text.
    result = resolve_summary_citations("Source: [00:10, 00:30]", transcript())

    assert result.markdown == "Source: [00:10](?t=10&cite=c0), [00:30]"
    assert result.resolved_count == 1
    assert result.unresolved_count == 1
    assert result.sidecar.citations[0].resolved is True
    assert result.sidecar.citations[1].resolved is False
    assert result.sidecar.citations[1].raw_label == "00:30"


def test_load_valid_citations_re_resolves_segment_hint_after_algorithm_change(tmp_path):
    path_manager = PathManager(str(tmp_path))
    storage = LocalFileStorage(str(tmp_path))
    original = transcript(
        algorithm_version="old",
        segments=[segment(segment_id=3, start=0, end=20, source_segment_ids=[11, 12])],
    )
    resolved = resolve_summary_citations("Source: [00:10]", original, episode_id="ep1")
    resolved.sidecar.transcript_algorithm_version = "old"
    summary_path = path_manager.summary_file("ep.md")
    summary_key = path_manager.to_relative(summary_path)
    storage.write_text(summary_key, resolved.markdown)
    storage.write_text(
        summary_citations_key(summary_key),
        resolved.sidecar.model_dump_json(),
    )

    recleaned = transcript(
        algorithm_version="new",
        segments=[segment(segment_id=99, start=0, end=20, source_segment_ids=[12, 13])],
    )

    citations = load_valid_citations_for_api(
        summary_markdown=resolved.markdown,
        episode=episode(summary_path="ep.md"),
        transcript=recleaned,
        summary_path=summary_path,
        path_manager=path_manager,
        file_storage=storage,
    )

    assert citations == [
        {
            "id": "c0",
            "raw_label": "00:10",
            "cited_playback_s": 10.0,
            "target_playback_s": 10.0,
            "segment_id_hint": 99,
            "source_segment_ids": [11, 12],
            "resolved": True,
        }
    ]


def test_backfill_rewrites_existing_summary_and_writes_sidecar(tmp_path):
    path_manager = PathManager(str(tmp_path))
    storage = LocalFileStorage(str(tmp_path))
    ep = episode(
        summary_path="show/ep.md",
        clean_transcript_json_path="show/ep.json",
    )
    summary_key = path_manager.to_relative(path_manager.summary_file(ep.summary_path))
    transcript_key = path_manager.to_relative(path_manager.clean_transcript_file(ep.clean_transcript_json_path))
    storage.write_text(summary_key, "Source: [00:10]")
    storage.write_text(transcript_key, transcript().model_dump_json())

    result = backfill_summary_citations_for_episode(
        episode=ep,
        path_manager=path_manager,
        file_storage=storage,
        write=True,
    )

    assert result.changed is True
    assert result.written is True
    assert result.resolved_count == 1
    assert storage.read_text(summary_key) == "Source: [00:10](?t=10&cite=c0)"
    assert storage.exists(summary_citations_key(summary_key))
