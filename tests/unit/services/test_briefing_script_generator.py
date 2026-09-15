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

"""Summary-section extraction feeding the narration writer (spec #77 §2 / Phase 2b)."""

from thestill.services.briefing_script_generator import extract_gist, extract_summary_sections

SUMMARY = """## 1. 🎙️ The Gist
Steph McGovern and Robert Peston interview Azeem Azhar, founder of Exponential View.

Azeem shares insights from his trip to Chinese AI labs and explains why the spending might be backed by revenue.

## 2. ⏱️ Timeline
* [00:00 - 10:40](?t=0&cite=c0) **China’s AI Scene:** Azeem describes the culture at Moonshot.

## 3. 🧠 Key Takeaways
* Chinese AI models are now only 4-8 months behind top US models. [02:46](?t=166&cite=c5)
* AI revenue is real: $110 billion in the last 12 months, growing 3.5x. [24:04](?t=1444&cite=c7)
* He calls them 'spaghetti' org charts and the 'Oreo' problem. [30:00](?t=1800&cite=c8)

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


def test_sections_split_takeaways_and_drama_rounds_and_strip_markup() -> None:
    sections = extract_summary_sections(SUMMARY)
    assert sections is not None
    assert sections.gist.startswith("Steph McGovern and Robert Peston")
    assert len(sections.takeaways) == 3 and len(sections.drama) == 2
    assert sections.takeaways[0] == "Chinese AI models are now only 4-8 months behind top US models."
    assert sections.drama[1].startswith("Round 2: The Nightclub Exit")
    assert "1 a.m. to check on their AI agents" in sections.drama[1]
    joined = "\n".join(sections.takeaways + sections.drama) + sections.gist
    for noise in ("?t=", "cite=", "**", "[02:46]", "[07:04]"):
        assert noise not in joined
    assert "Nobody is coming to save you" not in joined  # section 5 never fed in
    assert "Moonshot" not in joined  # timeline skipped


def test_scare_quoted_terms_are_unquoted() -> None:
    sections = extract_summary_sections(SUMMARY)
    assert sections is not None
    assert sections.takeaways[2] == "He calls them spaghetti org charts and the Oreo problem."
    # Double-quoted titles in drama headers are left alone: only single-quoted terms are scare quotes.
    assert '"Industrial Vandalism"' in sections.drama[0]


def test_sections_return_none_for_legacy_summary_and_gist_still_works() -> None:
    legacy = "# Episode\n\nExecutive Summary\n\nA short overview. Another sentence here.\n\n**Takeaways**\n- one\n"
    assert extract_summary_sections(legacy) is None
    assert extract_gist(legacy) is not None


def test_sections_tolerate_missing_drama() -> None:
    text = "## 1. The Gist\nIntro [01:02:03] and range [10:00 - 12:30] done.\n\n## 3. Key Takeaways\n* Only one. [00:10](?t=10&cite=c1)\n"
    sections = extract_summary_sections(text)
    assert sections is not None
    assert sections.gist == "Intro and range done."
    assert sections.takeaways == ("Only one.",) and sections.drama == ()
