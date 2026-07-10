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

"""Deterministic checks for the summary rubric.

These are cheap, exact validations of ``TranscriptSummarizer``'s output
contract (core/post_processor.py SYSTEM_PROMPT) — required sections,
timestamp validity, timeline monotonicity. They run in Python rather than
being delegated to the judge because a judge would grade them noisily.

Long episodes produce chunked summaries where the full section set repeats
per chunk (joined with ``---``): section presence tolerates repeats, and
timeline monotonicity is checked per Timeline block, not globally.
"""

import re
from typing import Dict, List, Optional

from thestill.core.summary_citations import _TIMESTAMP_RE, parse_timestamp_label

# The 9 numbered sections TranscriptSummarizer's prompt requires. Matched
# on number + title text so emoji or spacing drift doesn't false-negative.
REQUIRED_SECTIONS = (
    (1, "The Gist"),
    (2, "Timeline"),
    (3, "Key Takeaways"),
    (4, "The Drama"),
    (5, "Best Quotes"),
    (6, "Blog Ideas"),
    (7, "Social Snippets"),
    (8, "Resource List"),
    (9, "BS"),
)

# Timestamps cited a hair past the episode end are usually rounding (feed
# durations come from itunes:duration and are approximate); anything past
# this tolerance is a real out-of-bounds citation.
DURATION_TOLERANCE_SECONDS = 30.0

_HEADING_RE = re.compile(r"^##\s*(\d+)\.\s*(.*)$", re.MULTILINE)
# A timeline bullet's leading range: "* [00:00 - 08:30]" or "* [35:00 - End]"
_TIMELINE_RANGE_RE = re.compile(
    r"^\*\s*\[(\d{1,2}:\d{2}(?::\d{2})?)\s*[-–—]\s*(\d{1,2}:\d{2}(?::\d{2})?|End)\]",
    re.IGNORECASE | re.MULTILINE,
)


def _find_sections(markdown: str) -> Dict[int, List[str]]:
    """Map section number -> list of heading title texts found (repeats kept)."""
    found: Dict[int, List[str]] = {}
    for match in _HEADING_RE.finditer(markdown):
        found.setdefault(int(match.group(1)), []).append(match.group(2))
    return found


def _check_sections(markdown: str) -> dict:
    found = _find_sections(markdown)
    missing = []
    for number, title in REQUIRED_SECTIONS:
        titles = found.get(number, [])
        if not any(title.lower() in heading.lower() for heading in titles):
            missing.append(f"{number}. {title}")
    return {"ok": not missing, "missing": missing}


def _check_timestamps(markdown: str, duration_seconds: Optional[int]) -> dict:
    """Validate every inline timestamp: parseable and within episode bounds."""
    invalid: List[str] = []
    out_of_bounds: List[str] = []
    total = 0
    for match in _TIMESTAMP_RE.finditer(markdown):
        label = match.group(0)
        total += 1
        seconds = parse_timestamp_label(label)
        if seconds is None:
            invalid.append(label)
            continue
        if duration_seconds and seconds > duration_seconds + DURATION_TOLERANCE_SECONDS:
            out_of_bounds.append(label)
    return {
        "ok": not invalid and not out_of_bounds,
        "total": total,
        "invalid": invalid,
        "out_of_bounds": out_of_bounds,
        "duration_known": bool(duration_seconds),
    }


def _split_timeline_blocks(markdown: str) -> List[str]:
    """Return the body text of each Timeline section (repeats per chunk)."""
    blocks: List[str] = []
    headings = list(_HEADING_RE.finditer(markdown))
    for i, match in enumerate(headings):
        if int(match.group(1)) == 2 and "timeline" in match.group(2).lower():
            start = match.end()
            end = headings[i + 1].start() if i + 1 < len(headings) else len(markdown)
            blocks.append(markdown[start:end])
    return blocks


def _check_timeline(markdown: str) -> dict:
    """Timeline ranges must parse, ascend, and each range must start < end.

    ``End`` is the contract's open-ended terminator for the last segment
    and is treated as +infinity.
    """
    problems: List[str] = []
    blocks = _split_timeline_blocks(markdown)
    for block_index, block in enumerate(blocks):
        previous_start: Optional[float] = None
        for match in _TIMELINE_RANGE_RE.finditer(block):
            start_label, end_label = match.group(1), match.group(2)
            start = parse_timestamp_label(start_label)
            end = float("inf") if end_label.lower() == "end" else parse_timestamp_label(end_label)
            location = f"[{start_label} - {end_label}]"
            if block_index:
                location += f" (timeline block {block_index + 1})"
            if start is None or end is None:
                problems.append(f"unparseable range {location}")
                continue
            if start >= end:
                problems.append(f"range start >= end {location}")
            if previous_start is not None and start <= previous_start:
                problems.append(f"non-ascending segment start {location}")
            previous_start = start
    return {"ok": not problems, "blocks": len(blocks), "problems": problems}


def run_summary_checks(artifacts: Dict[str, str], duration_seconds: Optional[int]) -> dict:
    """Run all deterministic checks against a summary's markdown.

    Returns a JSON-serializable dict with a top-level ``ok`` plus one
    entry per check, stored in the item report under ``checks`` (separate
    from the judge's ``scores``).
    """
    markdown = artifacts["summary"]
    sections = _check_sections(markdown)
    timestamps = _check_timestamps(markdown, duration_seconds)
    timeline = _check_timeline(markdown)
    return {
        "ok": sections["ok"] and timestamps["ok"] and timeline["ok"],
        "sections": sections,
        "timestamps": timestamps,
        "timeline": timeline,
    }
