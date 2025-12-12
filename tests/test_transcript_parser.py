# Copyright 2025 thestill.me
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

"""Unit tests for transcript parsers."""

import pytest

from thestill.core.transcript_parser import (
    _clean_html_text,
    _clean_subtitle_text,
    _group_words_to_segments,
    _parse_buzzsprout_json,
    _parse_google_json,
    _parse_json_segments,
    _parse_srt_timecode,
    _parse_vtt_timecode,
    load_best_external_transcript,
    parse_html,
    parse_json,
    parse_plain_text,
    parse_srt,
    parse_vtt,
)


class TestParseSRT:
    """Tests for SRT format parser."""

    def test_parse_basic_srt(self):
        """Test parsing basic SRT content."""
        content = """1
00:00:00,000 --> 00:00:05,500
Hello, welcome to the show.

2
00:00:05,600 --> 00:00:10,000
Today we're discussing podcasts.
"""
        segments = parse_srt(content)

        assert len(segments) == 2
        assert segments[0].id == 0
        assert segments[0].start == 0.0
        assert segments[0].end == 5.5
        assert segments[0].text == "Hello, welcome to the show."

        assert segments[1].id == 1
        assert segments[1].start == 5.6
        assert segments[1].end == 10.0
        assert segments[1].text == "Today we're discussing podcasts."

    def test_parse_srt_with_dot_milliseconds(self):
        """Test parsing SRT with dot separator for milliseconds."""
        content = """1
00:00:00.000 --> 00:00:05.500
Test with dot separator.
"""
        segments = parse_srt(content)

        assert len(segments) == 1
        assert segments[0].end == 5.5

    def test_parse_srt_with_html_tags(self):
        """Test parsing SRT with HTML formatting tags."""
        content = """1
00:00:00,000 --> 00:00:05,000
<b>Bold text</b> and <i>italic</i>.
"""
        segments = parse_srt(content)

        assert len(segments) == 1
        assert segments[0].text == "Bold text and italic."

    def test_parse_srt_multiline_text(self):
        """Test parsing SRT with multi-line subtitle text."""
        content = """1
00:00:00,000 --> 00:00:05,000
First line
Second line
"""
        segments = parse_srt(content)

        assert len(segments) == 1
        assert segments[0].text == "First line Second line"

    def test_parse_empty_srt(self):
        """Test parsing empty SRT returns empty list."""
        segments = parse_srt("")
        assert segments == []

    def test_parse_srt_hours_minutes_seconds(self):
        """Test parsing SRT with hours in timecode."""
        content = """1
01:30:45,123 --> 01:30:50,456
After an hour and a half.
"""
        segments = parse_srt(content)

        assert len(segments) == 1
        # 1*3600 + 30*60 + 45.123 = 5445.123
        assert abs(segments[0].start - 5445.123) < 0.001
        assert abs(segments[0].end - 5450.456) < 0.001


class TestParseVTT:
    """Tests for VTT format parser."""

    def test_parse_basic_vtt(self):
        """Test parsing basic VTT content."""
        content = """WEBVTT

00:00.000 --> 00:05.500
Hello, welcome to the show.

00:05.600 --> 00:10.000
Today we're discussing podcasts.
"""
        segments = parse_vtt(content)

        assert len(segments) == 2
        assert segments[0].start == 0.0
        assert segments[0].end == 5.5
        assert segments[0].text == "Hello, welcome to the show."

    def test_parse_vtt_with_hours(self):
        """Test parsing VTT with full HH:MM:SS.mmm format."""
        content = """WEBVTT

00:00:00.000 --> 00:00:05.500
With hours format.
"""
        segments = parse_vtt(content)

        assert len(segments) == 1
        assert segments[0].end == 5.5

    def test_parse_vtt_with_voice_tags(self):
        """Test parsing VTT with speaker voice tags."""
        content = """WEBVTT

00:00.000 --> 00:05.000
<v John>Hello, I'm John.</v>

00:05.000 --> 00:10.000
<v Jane>And I'm Jane.</v>
"""
        segments = parse_vtt(content)

        assert len(segments) == 2
        assert segments[0].speaker == "John"
        assert segments[0].text == "Hello, I'm John."
        assert segments[1].speaker == "Jane"
        assert segments[1].text == "And I'm Jane."

    def test_parse_vtt_with_metadata_header(self):
        """Test parsing VTT with metadata in header."""
        content = """WEBVTT
Kind: captions
Language: en

00:00.000 --> 00:05.000
After metadata header.
"""
        segments = parse_vtt(content)

        assert len(segments) == 1
        assert segments[0].text == "After metadata header."

    def test_parse_vtt_with_cue_identifiers(self):
        """Test parsing VTT with named cue identifiers."""
        content = """WEBVTT

intro-1
00:00.000 --> 00:05.000
Introduction text.
"""
        segments = parse_vtt(content)

        assert len(segments) == 1
        assert segments[0].text == "Introduction text."

    def test_parse_vtt_with_cue_settings(self):
        """Test parsing VTT with cue positioning settings."""
        content = """WEBVTT

00:00.000 --> 00:05.000 line:0 position:20%
Text with positioning.
"""
        segments = parse_vtt(content)

        assert len(segments) == 1
        assert segments[0].text == "Text with positioning."

    def test_parse_empty_vtt(self):
        """Test parsing empty VTT returns empty list."""
        segments = parse_vtt("WEBVTT\n\n")
        assert segments == []


class TestParseJSON:
    """Tests for JSON transcript parser."""

    def test_parse_simple_array(self):
        """Test parsing simple array of segments."""
        content = """[
            {"start": 0, "end": 5, "text": "Hello world"},
            {"start": 5.5, "end": 10, "text": "Second segment"}
        ]"""
        segments = parse_json(content)

        assert len(segments) == 2
        assert segments[0].start == 0.0
        assert segments[0].end == 5.0
        assert segments[0].text == "Hello world"

    def test_parse_segments_wrapper(self):
        """Test parsing JSON with segments wrapper."""
        content = """{
            "segments": [
                {"start": 0, "end": 5, "text": "Wrapped segment"}
            ]
        }"""
        segments = parse_json(content)

        assert len(segments) == 1
        assert segments[0].text == "Wrapped segment"

    def test_parse_with_speaker(self):
        """Test parsing JSON with speaker information."""
        content = """[
            {"start": 0, "end": 5, "text": "Hello", "speaker": "SPEAKER_01"}
        ]"""
        segments = parse_json(content)

        assert len(segments) == 1
        assert segments[0].speaker == "SPEAKER_01"

    def test_parse_alternative_field_names(self):
        """Test parsing JSON with alternative field names."""
        content = """[
            {"startTime": 0, "endTime": 5, "content": "Alt fields", "speakerId": "host"}
        ]"""
        segments = parse_json(content)

        assert len(segments) == 1
        assert segments[0].start == 0.0
        assert segments[0].end == 5.0
        assert segments[0].text == "Alt fields"
        assert segments[0].speaker == "host"

    def test_parse_invalid_json(self):
        """Test parsing invalid JSON returns empty list."""
        segments = parse_json("not valid json {")
        assert segments == []

    def test_parse_empty_json_array(self):
        """Test parsing empty JSON array returns empty list."""
        segments = parse_json("[]")
        assert segments == []


class TestParseBuzzsproutJSON:
    """Tests for Buzzsprout-style JSON parser."""

    def test_parse_buzzsprout_format(self):
        """Test parsing Buzzsprout transcript format."""
        content = """{
            "transcript": [],
            "paragraphs": [
                {
                    "speaker": "Host",
                    "sentences": [
                        {
                            "startTime": 0,
                            "endTime": 5,
                            "words": [
                                {"text": "Hello"},
                                {"text": "world"}
                            ]
                        }
                    ]
                }
            ]
        }"""
        segments = parse_json(content)

        assert len(segments) == 1
        assert segments[0].text == "Hello world"
        assert segments[0].speaker == "Host"


class TestParseGoogleJSON:
    """Tests for Google Speech-to-Text style JSON parser."""

    def test_parse_google_format(self):
        """Test parsing Google Speech-to-Text format."""
        content = """{
            "results": [
                {
                    "alternatives": [
                        {
                            "transcript": "Hello from Google",
                            "words": [
                                {"startTime": "0s", "endTime": "1s"},
                                {"startTime": "1s", "endTime": "2s"},
                                {"startTime": "2s", "endTime": "3s"}
                            ]
                        }
                    ]
                }
            ]
        }"""
        segments = parse_json(content)

        assert len(segments) == 1
        assert segments[0].text == "Hello from Google"
        assert segments[0].start == 0.0
        assert segments[0].end == 3.0


class TestGroupWordsToSegments:
    """Tests for word grouping function."""

    def test_group_words_basic(self):
        """Test basic word grouping."""
        words = [
            {"word": "Hello", "start": 0, "end": 0.5},
            {"word": "world", "start": 0.6, "end": 1.0},
        ]
        segments = _group_words_to_segments(words)

        assert len(segments) == 1
        assert segments[0].text == "Hello world"

    def test_group_words_with_gap(self):
        """Test word grouping splits on gaps."""
        words = [
            {"word": "First", "start": 0, "end": 0.5},
            {"word": "segment", "start": 0.6, "end": 1.0},
            {"word": "Second", "start": 3.0, "end": 3.5},  # 2 second gap
            {"word": "segment", "start": 3.6, "end": 4.0},
        ]
        segments = _group_words_to_segments(words, gap_threshold=1.0)

        assert len(segments) == 2
        assert segments[0].text == "First segment"
        assert segments[1].text == "Second segment"

    def test_group_empty_words(self):
        """Test grouping empty word list."""
        segments = _group_words_to_segments([])
        assert segments == []


class TestParsePlainText:
    """Tests for plain text parser."""

    def test_parse_plain_text(self):
        """Test parsing plain text content."""
        content = "This is a plain text transcript with no timing information."
        segments = parse_plain_text(content)

        assert len(segments) == 1
        assert segments[0].id == 0
        assert segments[0].start == 0.0
        assert segments[0].end == 0.0
        assert segments[0].text == content

    def test_parse_plain_text_multiline(self):
        """Test parsing multiline plain text."""
        content = "Line one.\nLine two.\nLine three."
        segments = parse_plain_text(content)

        assert len(segments) == 1
        assert "Line one." in segments[0].text
        assert "Line three." in segments[0].text

    def test_parse_empty_plain_text(self):
        """Test parsing empty plain text."""
        segments = parse_plain_text("")
        assert segments == []

    def test_parse_whitespace_only_plain_text(self):
        """Test parsing whitespace-only plain text."""
        segments = parse_plain_text("   \n\t  \n  ")
        assert segments == []


class TestParseHTML:
    """Tests for HTML transcript parser."""

    def test_parse_html_paragraphs(self):
        """Test parsing HTML with paragraph tags."""
        content = """<html>
        <body>
            <p>First paragraph.</p>
            <p>Second paragraph.</p>
        </body>
        </html>"""
        segments = parse_html(content)

        assert len(segments) == 2
        assert segments[0].text == "First paragraph."
        assert segments[1].text == "Second paragraph."

    def test_parse_html_strips_tags(self):
        """Test that HTML tags are stripped."""
        content = """<p><strong>Bold</strong> and <em>italic</em> text.</p>"""
        segments = parse_html(content)

        assert len(segments) == 1
        assert segments[0].text == "Bold and italic text."

    def test_parse_html_decodes_entities(self):
        """Test that HTML entities are decoded."""
        content = """<p>Tom &amp; Jerry &lt;3 cheese.</p>"""
        segments = parse_html(content)

        assert len(segments) == 1
        assert segments[0].text == "Tom & Jerry <3 cheese."

    def test_parse_html_removes_script_style(self):
        """Test that script and style tags are removed."""
        content = """<html>
        <head><style>body { color: red; }</style></head>
        <body>
            <script>alert('hi');</script>
            <p>Actual content.</p>
        </body>
        </html>"""
        segments = parse_html(content)

        assert len(segments) == 1
        assert segments[0].text == "Actual content."
        assert "alert" not in segments[0].text
        assert "color" not in segments[0].text

    def test_parse_html_no_paragraphs_fallback(self):
        """Test HTML without paragraphs falls back to text extraction."""
        content = """<html><body>Just some text without p tags.</body></html>"""
        segments = parse_html(content)

        assert len(segments) == 1
        assert segments[0].text == "Just some text without p tags."

    def test_parse_empty_html(self):
        """Test parsing empty HTML."""
        segments = parse_html("<html><body></body></html>")
        assert segments == []


class TestTimecodeParserHelpers:
    """Tests for timecode parsing helper functions."""

    def test_srt_timecode_with_comma(self):
        """Test SRT timecode parsing with comma."""
        result = _parse_srt_timecode("00:01:30,500")
        assert result == 90.5

    def test_srt_timecode_with_dot(self):
        """Test SRT timecode parsing with dot."""
        result = _parse_srt_timecode("00:01:30.500")
        assert result == 90.5

    def test_vtt_timecode_mm_ss(self):
        """Test VTT timecode MM:SS.mmm format."""
        result = _parse_vtt_timecode("01:30.500")
        assert result == 90.5

    def test_vtt_timecode_hh_mm_ss(self):
        """Test VTT timecode HH:MM:SS.mmm format."""
        result = _parse_vtt_timecode("01:01:30.500")
        assert result == 3690.5


class TestCleanTextHelpers:
    """Tests for text cleaning helper functions."""

    def test_clean_subtitle_text_voice_tags(self):
        """Test cleaning VTT voice tags."""
        text = "<v Speaker Name>Hello world</v>"
        result = _clean_subtitle_text(text)
        assert result == "Hello world"

    def test_clean_subtitle_text_html_tags(self):
        """Test cleaning HTML tags from subtitles."""
        text = "<b>Bold</b> and <i>italic</i>"
        result = _clean_subtitle_text(text)
        assert result == "Bold and italic"

    def test_clean_subtitle_text_entities(self):
        """Test decoding HTML entities."""
        text = "Tom &amp; Jerry"
        result = _clean_subtitle_text(text)
        assert result == "Tom & Jerry"

    def test_clean_subtitle_text_whitespace(self):
        """Test normalizing whitespace."""
        text = "Multiple   spaces\n\tand  tabs"
        result = _clean_subtitle_text(text)
        assert result == "Multiple spaces and tabs"

    def test_clean_html_text_scripts(self):
        """Test removing script tags."""
        text = "Before<script>bad code</script>After"
        result = _clean_html_text(text)
        assert result == "BeforeAfter"

    def test_clean_html_text_styles(self):
        """Test removing style tags."""
        text = "Before<style>.red{color:red}</style>After"
        result = _clean_html_text(text)
        assert result == "BeforeAfter"


class TestLoadBestExternalTranscript:
    """Tests for loading best available external transcript."""

    def test_load_json_priority(self, tmp_path):
        """Test that JSON format has priority over others."""
        from thestill.utils.path_manager import PathManager

        pm = PathManager(storage_path=str(tmp_path))

        # Create external transcripts directory
        ext_dir = pm.external_transcript_dir_for_podcast("test-podcast")
        ext_dir.mkdir(parents=True)

        # Create both JSON and SRT files
        json_file = ext_dir / "test-episode.json"
        json_file.write_text('[{"start": 0, "end": 5, "text": "From JSON"}]')

        srt_file = ext_dir / "test-episode.srt"
        srt_file.write_text("1\n00:00:00,000 --> 00:00:05,000\nFrom SRT\n")

        segments = load_best_external_transcript(
            episode_id="123",
            podcast_slug="test-podcast",
            episode_slug="test-episode",
            path_manager=pm,
        )

        assert segments is not None
        assert len(segments) == 1
        assert segments[0].text == "From JSON"

    def test_load_fallback_to_srt(self, tmp_path):
        """Test fallback to SRT when JSON not available."""
        from thestill.utils.path_manager import PathManager

        pm = PathManager(storage_path=str(tmp_path))

        ext_dir = pm.external_transcript_dir_for_podcast("test-podcast")
        ext_dir.mkdir(parents=True)

        srt_file = ext_dir / "test-episode.srt"
        srt_file.write_text("1\n00:00:00,000 --> 00:00:05,000\nFrom SRT\n")

        segments = load_best_external_transcript(
            episode_id="123",
            podcast_slug="test-podcast",
            episode_slug="test-episode",
            path_manager=pm,
        )

        assert segments is not None
        assert len(segments) == 1
        assert segments[0].text == "From SRT"

    def test_load_no_transcript_found(self, tmp_path):
        """Test returns None when no transcript exists."""
        from thestill.utils.path_manager import PathManager

        pm = PathManager(storage_path=str(tmp_path))

        segments = load_best_external_transcript(
            episode_id="123",
            podcast_slug="nonexistent-podcast",
            episode_slug="nonexistent-episode",
            path_manager=pm,
        )

        assert segments is None
