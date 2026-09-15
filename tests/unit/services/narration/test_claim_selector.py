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

"""One claim + one colour per episode (spec #77 Phase 2b)."""

from thestill.services.narration.claim_selector import select_claim
from thestill.services.narration.models import EpisodeBrief


def _brief(**kw) -> EpisodeBrief:
    base = dict(episode_id="e1", podcast_title="Pod", episode_title="Ep")
    base.update(kw)
    return EpisodeBrief(**base)


def test_claim_is_the_takeaway_closest_to_the_angle() -> None:
    brief = _brief(
        takeaways=(
            "AI revenue is real: $110 billion in the last 12 months.",
            "Chinese AI models are only 4-8 months behind top US models.",
            "Electricity, not chips, is the bottleneck.",
        ),
        drama=(
            "Round 1: Peston calls open-weight models industrial vandalism. Tense.",
            "Round 3: Steph asks whether Nvidia financing its customers is Enron. Sharp.",
        ),
    )
    chosen = select_claim(brief, "how far behind Chinese models really are")
    assert chosen is not None
    assert chosen.claim.startswith("Chinese AI models")
    assert chosen.colour is not None and chosen.colour.startswith("Round 1")  # overlap on "models"


def test_ties_and_no_overlap_fall_to_the_first_takeaway() -> None:
    brief = _brief(takeaways=("First point here.", "Second point there."), drama=())
    chosen = select_claim(brief, "something completely unrelated")
    assert chosen is not None and chosen.claim == "First point here." and chosen.colour is None


def test_falls_back_to_first_gist_sentence_without_takeaways() -> None:
    brief = _brief(gist="Host interviews Guest about X. They also cover Y.")
    chosen = select_claim(brief, "anything")
    assert chosen is not None and chosen.claim == "Host interviews Guest about X."
    assert select_claim(_brief(), "anything") is None


def test_colour_is_capped_to_whole_sentences_within_the_word_budget() -> None:
    long_round = " ".join(f"Sentence number {i} has exactly six words." for i in range(10))
    brief = _brief(takeaways=("A short claim.",), drama=(long_round,))
    chosen = select_claim(brief, "claim", max_words=20)
    assert chosen is not None and chosen.colour is not None
    assert chosen.colour.endswith("words.") and len(chosen.colour.split()) <= 20
    assert chosen.colour.count("Sentence number") == 2  # 3 claim words + 2×6 ≤ 20, a third would not fit
