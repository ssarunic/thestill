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

"""Summary-section extraction feeding the narration writer (spec #77 §2)."""

from thestill.services.briefing_script_generator import extract_gist, extract_summary_material

SUMMARY = """## 1. 🎙️ The Gist
Steph McGovern and Robert Peston interview Azeem Azhar, founder of Exponential View.

Azeem shares insights from his trip to Chinese AI labs and explains why the spending might be backed by revenue.

## 2. ⏱️ Timeline
* [00:00 - 10:40](?t=0&cite=c0) **China’s AI Scene:** Azeem describes the culture at Moonshot.
* [42:06 - End](?t=2526&cite=c4) **Financial Risks:** Nvidia’s lending practices.

## 3. 🧠 Key Takeaways
* Chinese AI models are now only 4-8 months behind top US models. [02:46](?t=166&cite=c5)
* AI revenue is real: $110 billion in the last 12 months, growing 3.5x. [24:04](?t=1444&cite=c7)

## 4. 🌶️ The Drama

* **Round 1: The "Industrial Vandalism" Accusation** [07:04](?t=424&cite=c9)
  * **What happened:** Peston suggests giving away open-weight models is industrial vandalism.
  * **The temperature:** Tense.

* **Round 2: The Nightclub Exit** [09:20](?t=560&cite=c10)
  * **What happened:** Founders ditched the party at 1 a.m. to check on their AI agents.
  * **The temperature:** Amusing but pointed.

## 5. 💬 Best Quotes
* "Nobody is coming to save you." [12:00](?t=720&cite=c11)
"""


def test_material_keeps_gist_takeaways_drama_in_order_and_strips_markup() -> None:
    out = extract_summary_material(SUMMARY)
    assert out is not None
    assert out.index("Gist:") < out.index("Key takeaways:") < out.index("Drama (")
    for noise in ("?t=", "cite=", "**", "[02:46]", "[07:04]", "[00:00 - 10:40]"):
        assert noise not in out
    assert "Nobody is coming to save you" not in out  # section 5 is never fed in
    assert "Timeline" not in out and "Moonshot" not in out  # section 2 skipped
    assert "- Round 1:" in out and "  - What happened: Peston suggests" in out
    assert "4-8 months behind" in out and "1 a.m." in out


def test_material_cap_drops_whole_drama_items_first() -> None:
    full = extract_summary_material(SUMMARY)
    assert full is not None
    words = len(full.split())
    capped = extract_summary_material(SUMMARY, max_words=words - 5)
    assert capped is not None
    # Round 2 (the last drama item) goes as a unit; Round 1 and every takeaway stay.
    assert "Round 2" not in capped and "Nightclub" not in capped
    assert "Round 1" in capped and "Tense" in capped
    assert "4-8 months behind" in capped and "$110 billion" in capped
    assert len(capped.split()) <= words - 5


def test_material_cap_falls_through_to_takeaways_then_gist() -> None:
    tiny = extract_summary_material(SUMMARY, max_words=25)
    assert tiny is not None
    assert "Drama (" not in tiny and "Key takeaways:" not in tiny
    assert tiny.startswith("Gist:")
    assert len(tiny.split()) <= 25


def test_material_returns_none_for_legacy_summary_and_gist_still_works() -> None:
    legacy = "# Episode\n\nExecutive Summary\n\nA short overview. Another sentence here.\n\n**Takeaways**\n- one\n"
    assert extract_summary_material(legacy) is None
    assert extract_gist(legacy) is not None


def test_material_bare_timestamps_and_ranges_are_removed() -> None:
    text = "## 1. The Gist\nIntro [01:02:03] and range [10:00 - 12:30] plus [42:06 - End] done.\n"
    out = extract_summary_material(text)
    assert out == "Gist:\nIntro and range plus done."
