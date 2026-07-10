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

"""Deterministic summary checks against TranscriptSummarizer's contract."""

from thestill.evals.summary_checks import run_summary_checks

COMPLIANT_SUMMARY = """## 1. 🎙️ The Gist
Host interviews Guest about testing.

## 2. ⏱️ Timeline
* [00:00 - 02:30] **Intro:** Who the guest is.
* [02:30 - 05:00] **Middle:** The main argument.
* [05:00 - End] **Wrap:** Final thoughts.

## 3. 🧠 Key Takeaways
* Testing matters. [01:15]

## 4. 🌶️ The Drama
Nothing spicy happened.

## 5. 💬 Best Quotes
* "Test early." - Guest [03:20]

## 6. ✍️ Blog Ideas
* **Title:** Why Test
  * **Source:** [02:45]

## 7. 📱 Social Snippets
* Post one.

## 8. 📚 Resource List
* A book. [04:10]

## 9. 💩 The "BS" Test
* Nothing weak.
"""


def _checks(markdown, duration=600):
    return run_summary_checks({"summary": markdown, "clean_transcript": "irrelevant"}, duration)


def test_compliant_summary_passes_all_checks():
    result = _checks(COMPLIANT_SUMMARY)
    assert result["ok"] is True
    assert result["sections"]["missing"] == []
    assert result["timestamps"]["invalid"] == []
    assert result["timeline"]["problems"] == []


def test_missing_section_is_reported():
    truncated = COMPLIANT_SUMMARY.replace('## 9. 💩 The "BS" Test\n* Nothing weak.\n', "")
    result = _checks(truncated)
    assert result["ok"] is False
    assert result["sections"]["missing"] == ["9. BS"]


def test_out_of_bounds_timestamp_fails():
    # Episode is 600s; [59:59] is far past the end (beyond the 30s tolerance)
    bad = COMPLIANT_SUMMARY.replace("[01:15]", "[59:59]")
    result = _checks(bad)
    assert result["ok"] is False
    assert "59:59" in result["timestamps"]["out_of_bounds"]


def test_slightly_past_duration_is_tolerated():
    # 600s episode; 10:20 = 620s is within the 30s rounding tolerance
    result = _checks(COMPLIANT_SUMMARY.replace("[04:10]", "[10:20]"))
    assert result["timestamps"]["ok"] is True


def test_unknown_duration_skips_bounds_check():
    result = _checks(COMPLIANT_SUMMARY.replace("[01:15]", "[59:59]"), duration=None)
    assert result["timestamps"]["ok"] is True
    assert result["timestamps"]["duration_known"] is False


def test_unparseable_timestamp_is_invalid():
    # seconds >= 60 parses as None per parse_timestamp_label
    result = _checks(COMPLIANT_SUMMARY.replace("[01:15]", "[01:75]"))
    assert result["ok"] is False
    assert "01:75" in result["timestamps"]["invalid"]


def test_non_monotonic_timeline_fails():
    shuffled = COMPLIANT_SUMMARY.replace(
        "* [02:30 - 05:00] **Middle:** The main argument.",
        "* [01:00 - 05:00] **Middle:** The main argument.",
    )
    result = _checks(shuffled)
    assert result["timeline"]["ok"] is True  # 00:00 -> 01:00 -> 05:00 still ascends
    reversed_order = COMPLIANT_SUMMARY.replace(
        "* [02:30 - 05:00] **Middle:** The main argument.",
        "* [00:00 - 05:00] **Middle:** The main argument.",
    )
    result = _checks(reversed_order)
    assert result["timeline"]["ok"] is False
    assert any("non-ascending" in problem for problem in result["timeline"]["problems"])


def test_range_start_after_end_fails():
    bad = COMPLIANT_SUMMARY.replace("* [02:30 - 05:00]", "* [05:30 - 05:00]")
    result = _checks(bad)
    assert result["timeline"]["ok"] is False
    assert any("start >= end" in problem for problem in result["timeline"]["problems"])


def test_chunked_summary_with_repeated_sections_passes():
    # Long episodes produce all 9 sections repeated per chunk, joined by ---;
    # timeline monotonicity applies per block, not globally.
    second_chunk = COMPLIANT_SUMMARY.replace("[00:00 - 02:30]", "[00:10 - 02:30]")
    chunked = COMPLIANT_SUMMARY + "\n\n---\n\n" + second_chunk
    result = _checks(chunked)
    assert result["ok"] is True
    assert result["timeline"]["blocks"] == 2
