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

"""
Transcript parsers for external podcast transcripts.

Parses various transcript formats (SRT, VTT, JSON, plain text, HTML) into
thestill's internal Segment format for evaluation and comparison against
locally-generated transcripts.
"""

import html
import json
import logging
import re
from pathlib import Path
from typing import Any, Dict, List, Optional

from ..models.transcript import Segment
from ..utils.path_manager import PathManager

logger = logging.getLogger(__name__)


def parse_srt(content: str) -> List[Segment]:
    """
    Parse SubRip (SRT) format to segments.

    SRT format example:
        1
        00:00:00,000 --> 00:00:05,500
        Hello, welcome to the show.

        2
        00:00:05,600 --> 00:00:10,000
        Today we're discussing podcasts.

    Args:
        content: Raw SRT file content

    Returns:
        List of Segment objects with timestamps
    """
    segments: List[Segment] = []

    # Pattern to match SRT blocks
    # Group 1: sequence number
    # Group 2: start time
    # Group 3: end time
    # Group 4: text content
    pattern = re.compile(
        r"(\d+)\s*\n"
        r"(\d{2}:\d{2}:\d{2}[,\.]\d{3})\s*-->\s*(\d{2}:\d{2}:\d{2}[,\.]\d{3})\s*\n"
        r"((?:(?!\n\n|\n\d+\n).)+)",
        re.MULTILINE | re.DOTALL,
    )

    for match in pattern.finditer(content):
        seq_num = int(match.group(1))
        start_str = match.group(2)
        end_str = match.group(3)
        text = match.group(4).strip()

        # Convert timecode to seconds
        start = _parse_srt_timecode(start_str)
        end = _parse_srt_timecode(end_str)

        # Clean up text (remove HTML tags if any, normalize whitespace)
        text = _clean_subtitle_text(text)

        if text:
            segment = Segment(
                id=seq_num - 1,  # 0-indexed
                start=start,
                end=end,
                text=text,
                words=[],
            )
            segments.append(segment)

    logger.debug(f"Parsed {len(segments)} segments from SRT")
    return segments


def _parse_srt_timecode(timecode: str) -> float:
    """Convert SRT timecode (00:00:00,000) to seconds."""
    # Replace comma with dot for milliseconds
    timecode = timecode.replace(",", ".")
    parts = timecode.split(":")
    hours = int(parts[0])
    minutes = int(parts[1])
    seconds = float(parts[2])
    return hours * 3600 + minutes * 60 + seconds


def parse_vtt(content: str) -> List[Segment]:
    """
    Parse WebVTT (VTT) format to segments.

    VTT format example:
        WEBVTT

        00:00.000 --> 00:05.500
        Hello, welcome to the show.

        00:05.600 --> 00:10.000
        <v John>Today we're discussing podcasts.</v>

    Args:
        content: Raw VTT file content

    Returns:
        List of Segment objects with timestamps and optional speaker info
    """
    segments: List[Segment] = []

    # Skip WEBVTT header and any metadata
    lines = content.split("\n")
    content_start = 0
    for i, line in enumerate(lines):
        if line.strip() == "WEBVTT":
            content_start = i + 1
            break

    # Skip any header metadata (lines with colons before first empty line)
    for i in range(content_start, len(lines)):
        line = lines[i].strip()
        if not line:
            content_start = i + 1
            break
        if ":" not in line or "-->" in line:
            break

    content = "\n".join(lines[content_start:])

    # Pattern to match VTT cues
    # Optional cue identifier, timecode line, then text
    pattern = re.compile(
        r"(?:^|\n\n)"
        r"(?:[\w-]+\s*\n)?"  # Optional cue identifier
        r"(\d{2}:\d{2}[\.,:]\d{3}|\d{2}:\d{2}:\d{2}[\.,:]\d{3})\s*-->\s*"
        r"(\d{2}:\d{2}[\.,:]\d{3}|\d{2}:\d{2}:\d{2}[\.,:]\d{3})"
        r"[^\n]*\n"  # Skip any cue settings
        r"((?:(?!\n\n|\n\d{2}:).)*)",  # Text content
        re.MULTILINE | re.DOTALL,
    )

    segment_id = 0
    for match in pattern.finditer(content):
        start_str = match.group(1)
        end_str = match.group(2)
        text = match.group(3).strip()

        # Convert timecode to seconds
        start = _parse_vtt_timecode(start_str)
        end = _parse_vtt_timecode(end_str)

        # Extract speaker from voice tags <v Speaker>text</v>
        speaker = None
        voice_match = re.search(r"<v\s+([^>]+)>", text)
        if voice_match:
            speaker = voice_match.group(1).strip()

        # Clean up text
        text = _clean_subtitle_text(text)

        if text:
            segment = Segment(
                id=segment_id,
                start=start,
                end=end,
                text=text,
                speaker=speaker,
                words=[],
            )
            segments.append(segment)
            segment_id += 1

    logger.debug(f"Parsed {len(segments)} segments from VTT")
    return segments


def _parse_vtt_timecode(timecode: str) -> float:
    """Convert VTT timecode (00:00.000 or 00:00:00.000) to seconds."""
    # Replace comma with dot for consistency
    timecode = timecode.replace(",", ".")
    parts = timecode.split(":")

    if len(parts) == 2:
        # MM:SS.mmm format
        minutes = int(parts[0])
        seconds = float(parts[1])
        return minutes * 60 + seconds
    elif len(parts) == 3:
        # HH:MM:SS.mmm format
        hours = int(parts[0])
        minutes = int(parts[1])
        seconds = float(parts[2])
        return hours * 3600 + minutes * 60 + seconds

    return 0.0


def _clean_subtitle_text(text: str) -> str:
    """Clean subtitle text by removing tags and normalizing whitespace."""
    # Remove VTT voice tags <v Speaker>...</v>
    text = re.sub(r"<v\s+[^>]*>", "", text)
    text = re.sub(r"</v>", "", text)

    # Remove other HTML-like tags
    text = re.sub(r"<[^>]+>", "", text)

    # Decode HTML entities
    text = html.unescape(text)

    # Normalize whitespace
    text = " ".join(text.split())

    return text.strip()


def parse_json(content: str) -> List[Segment]:
    """
    Parse JSON transcript format to segments.

    Supports common JSON transcript schemas:
    - Buzzsprout style: {"transcript": [...], "paragraphs": [...]}
    - Simple array: [{"start": 0, "end": 5, "text": "..."}, ...]
    - Segments style: {"segments": [...]}

    Args:
        content: Raw JSON file content

    Returns:
        List of Segment objects
    """
    try:
        data = json.loads(content)
    except json.JSONDecodeError as e:
        logger.warning(f"Failed to parse JSON transcript: {e}")
        return []

    segments: List[Segment] = []

    # Try different JSON schemas
    if isinstance(data, list):
        # Simple array of segments
        segments = _parse_json_segments(data)
    elif isinstance(data, dict):
        # Try various common keys
        if "segments" in data:
            segments = _parse_json_segments(data["segments"])
        elif "transcript" in data:
            # Could be Buzzsprout style with word-level data
            segments = _parse_buzzsprout_json(data)
        elif "results" in data:
            # Google Speech-to-Text style
            segments = _parse_google_json(data)
        elif "words" in data:
            # Word-level transcript, group into segments
            segments = _group_words_to_segments(data["words"])

    logger.debug(f"Parsed {len(segments)} segments from JSON")
    return segments


def _parse_json_segments(items: List[Dict[str, Any]]) -> List[Segment]:
    """Parse array of segment objects."""
    segments: List[Segment] = []

    for i, item in enumerate(items):
        # Common field names for start time
        start = item.get("start") or item.get("startTime") or item.get("start_time") or 0

        # Common field names for end time
        end = item.get("end") or item.get("endTime") or item.get("end_time") or start

        # Common field names for text
        text = item.get("text") or item.get("content") or item.get("body") or ""

        # Common field names for speaker
        speaker = item.get("speaker") or item.get("speakerId") or item.get("speaker_id")

        if text:
            segment = Segment(
                id=i,
                start=float(start),
                end=float(end),
                text=str(text).strip(),
                speaker=str(speaker) if speaker else None,
                words=[],
            )
            segments.append(segment)

    return segments


def _parse_buzzsprout_json(data: Dict[str, Any]) -> List[Segment]:
    """Parse Buzzsprout-style JSON transcript."""
    segments: List[Segment] = []

    # Buzzsprout uses paragraphs with sentences
    paragraphs = data.get("paragraphs") or []

    segment_id = 0
    for para in paragraphs:
        sentences = para.get("sentences") or []
        speaker = para.get("speaker")

        for sent in sentences:
            start = sent.get("startTime") or sent.get("start") or 0
            end = sent.get("endTime") or sent.get("end") or start

            # Sentences may have word-level data
            words = sent.get("words") or []
            text = " ".join(w.get("text", "") for w in words) if words else sent.get("text", "")

            if text:
                segment = Segment(
                    id=segment_id,
                    start=float(start),
                    end=float(end),
                    text=text.strip(),
                    speaker=str(speaker) if speaker else None,
                    words=[],
                )
                segments.append(segment)
                segment_id += 1

    return segments


def _parse_google_json(data: Dict[str, Any]) -> List[Segment]:
    """Parse Google Speech-to-Text style JSON."""
    segments: List[Segment] = []
    results = data.get("results") or []

    segment_id = 0
    for result in results:
        alternatives = result.get("alternatives") or []
        if not alternatives:
            continue

        alt = alternatives[0]  # Use best alternative
        text = alt.get("transcript", "")

        # Get timing from words if available
        words = alt.get("words") or []
        if words:
            start = float(words[0].get("startTime", "0s").rstrip("s"))
            end = float(words[-1].get("endTime", "0s").rstrip("s"))
        else:
            start = 0
            end = 0

        if text:
            segment = Segment(
                id=segment_id,
                start=start,
                end=end,
                text=text.strip(),
                words=[],
            )
            segments.append(segment)
            segment_id += 1

    return segments


def _group_words_to_segments(words: List[Dict[str, Any]], gap_threshold: float = 1.0) -> List[Segment]:
    """Group word-level data into segments based on gaps."""
    if not words:
        return []

    segments: List[Segment] = []
    current_words: List[str] = []
    current_start: Optional[float] = None
    current_end: float = 0
    segment_id = 0

    for i, word in enumerate(words):
        word_text = word.get("text") or word.get("word") or ""
        word_start = float(word.get("start") or word.get("startTime") or 0)
        word_end = float(word.get("end") or word.get("endTime") or word_start)

        # Check for gap (start new segment)
        if current_words and word_start - current_end > gap_threshold:
            # Save current segment
            segments.append(
                Segment(
                    id=segment_id,
                    start=current_start or 0,
                    end=current_end,
                    text=" ".join(current_words),
                    words=[],
                )
            )
            segment_id += 1
            current_words = []
            current_start = None

        current_words.append(word_text)
        if current_start is None:
            current_start = word_start
        current_end = word_end

    # Save final segment
    if current_words:
        segments.append(
            Segment(
                id=segment_id,
                start=current_start or 0,
                end=current_end,
                text=" ".join(current_words),
                words=[],
            )
        )

    return segments


def parse_plain_text(content: str) -> List[Segment]:
    """
    Parse plain text transcript to a single segment.

    Plain text has no timing or speaker information.

    Args:
        content: Raw text content

    Returns:
        List with single Segment containing all text
    """
    text = content.strip()
    if not text:
        return []

    segment = Segment(
        id=0,
        start=0.0,
        end=0.0,  # Unknown duration
        text=text,
        words=[],
    )

    logger.debug("Parsed plain text into single segment")
    return [segment]


def parse_html(content: str) -> List[Segment]:
    """
    Parse HTML transcript to segments.

    Attempts to extract paragraphs as segments. Falls back to plain text
    extraction if no clear structure found.

    Args:
        content: Raw HTML content

    Returns:
        List of Segment objects
    """
    segments: List[Segment] = []

    # Try to find paragraph tags
    para_pattern = re.compile(r"<p[^>]*>(.*?)</p>", re.DOTALL | re.IGNORECASE)
    paragraphs = para_pattern.findall(content)

    if paragraphs:
        for i, para in enumerate(paragraphs):
            # Clean HTML from paragraph
            text = _clean_html_text(para)
            if text:
                segments.append(
                    Segment(
                        id=i,
                        start=0.0,
                        end=0.0,
                        text=text,
                        words=[],
                    )
                )
    else:
        # Fall back to extracting all text
        text = _clean_html_text(content)
        if text:
            segments = parse_plain_text(text)

    logger.debug(f"Parsed {len(segments)} segments from HTML")
    return segments


def _clean_html_text(html_content: str) -> str:
    """Remove HTML tags and clean text."""
    # Remove script and style elements
    html_content = re.sub(r"<(script|style)[^>]*>.*?</\1>", "", html_content, flags=re.DOTALL | re.IGNORECASE)

    # Remove HTML tags
    text = re.sub(r"<[^>]+>", " ", html_content)

    # Decode HTML entities
    text = html.unescape(text)

    # Normalize whitespace
    text = " ".join(text.split())

    return text.strip()


def load_best_external_transcript(
    episode_id: str,
    podcast_slug: str,
    episode_slug: str,
    path_manager: PathManager,
) -> Optional[List[Segment]]:
    """
    Load the best available external transcript for an episode.

    Tries formats in order of richness: JSON > VTT > SRT > plain text > HTML

    Args:
        episode_id: Episode UUID (unused, kept for API consistency)
        podcast_slug: Slugified podcast title
        episode_slug: Slugified episode title
        path_manager: Path manager for locating files

    Returns:
        List of Segment objects, or None if no transcript found
    """
    # Priority order: richest format first
    format_parsers = [
        ("json", parse_json),
        ("vtt", parse_vtt),
        ("srt", parse_srt),
        ("txt", parse_plain_text),
        ("html", parse_html),
    ]

    for extension, parser in format_parsers:
        file_path = path_manager.external_transcript_file(podcast_slug, episode_slug, extension)
        if file_path.exists():
            try:
                content = file_path.read_text(encoding="utf-8")
                segments = parser(content)
                if segments:
                    logger.info(f"Loaded external transcript from {file_path} ({len(segments)} segments)")
                    return segments
            except Exception as e:
                logger.warning(f"Failed to parse {extension} transcript at {file_path}: {e}")
                continue

    logger.debug(f"No external transcript found for {podcast_slug}/{episode_slug}")
    return None
