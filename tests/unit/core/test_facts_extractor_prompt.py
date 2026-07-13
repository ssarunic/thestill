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

"""Guards for the speaker-mapping confidence gating in the facts-extraction prompt.

Regression coverage for the host-mislabeling case where an under-determined host
speaker was confidently assigned a specific name from the podcast host roster
(Netokracija episode: real host Ivan Brezak Brkan mislabeled as Mia Biberović).
The fix instructs the model to name a speaker only from spoken evidence and to
fall back to a generic host role otherwise.
"""

from thestill.core.facts_extractor import FactsExtractor


class _StubProvider:
    """Minimal LLMProvider stand-in for constructing a FactsExtractor."""

    def get_max_output_tokens(self) -> int:
        return 4096

    def get_model_name(self) -> str:
        return "stub-model"


def _system_prompt(language: str = "hr") -> str:
    extractor = FactsExtractor(provider=_StubProvider())
    return extractor._build_facts_extraction_system_prompt(language=language)


def test_prompt_forbids_guessing_host_from_roster() -> None:
    prompt = _system_prompt()
    # The roster must be framed as "might appear", never as evidence of presence.
    assert "MIGHT appear" in prompt
    assert "NEVER pick a specific host name from" in prompt
    assert "Presence in the roster is not evidence." in prompt


def test_prompt_requires_spoken_evidence_for_names() -> None:
    prompt = _system_prompt()
    assert "CONFIDENCE RULE" in prompt
    # Role may be inferred; name may not.
    assert "The ROLE (host vs guest vs ad narrator) may be" in prompt


def test_prompt_offers_generic_host_fallback() -> None:
    prompt = _system_prompt()
    # An unnamed host is the desired fallback, not a confidently wrong name.
    assert "generic host role" in prompt
    assert "Host (Netokracija)" in prompt
    assert "A visibly unnamed host" in prompt
