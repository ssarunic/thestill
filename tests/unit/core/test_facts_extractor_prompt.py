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

from thestill.core.facts_extractor import FactsExtractor, _disambiguate_generic_labels
from thestill.core.segmented_transcript_cleaner import _apply_speaker_mapping
from thestill.models.annotated_transcript import AnnotatedSegment, AnnotatedTranscript


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


def test_prompt_requires_distinct_generic_labels() -> None:
    prompt = _system_prompt()
    assert '"Host 1", "Host 2"' in prompt
    assert "Never give two different voices the identical generic label." in prompt
    assert '"generic_label": true' in prompt


# --- Generic labels must not collapse distinct speakers ----------------------
#
# Downstream rendering substitutes speaker ids with the mapped label and merges
# consecutive turns that share a label, so two unnamed hosts both labelled
# "Host" would render as one person arguing with themselves.


def test_colliding_generic_labels_are_numbered() -> None:
    mapping = {"SPEAKER_01": "Host (Netokracija)", "SPEAKER_00": "Host (Netokracija)", "SPEAKER_02": "Ana Anić (Guest)"}

    assert _disambiguate_generic_labels(mapping) == {
        "SPEAKER_00": "Host 1 (Netokracija)",
        "SPEAKER_01": "Host 2 (Netokracija)",
        "SPEAKER_02": "Ana Anić (Guest)",
    }


def test_single_generic_label_is_left_alone() -> None:
    mapping = {"SPEAKER_00": "Voditelj", "SPEAKER_01": "Ana Anić (Guest)"}

    assert _disambiguate_generic_labels(mapping) == mapping


def test_same_person_on_two_ids_is_not_numbered() -> None:
    # Diarization splitting one voice across ids: merging those turns is correct.
    mapping = {"SPEAKER_00": "Scott Galloway (Host)", "SPEAKER_03": "Scott Galloway (Host)"}

    assert _disambiguate_generic_labels(mapping) == mapping


def test_model_flag_marks_unlisted_role_words_generic() -> None:
    # "Gastgeber" is not in the built-in role-word list; the model's flag covers it.
    mapping = {"SPEAKER_00": "Gastgeber", "SPEAKER_01": "Gastgeber"}

    assert _disambiguate_generic_labels(mapping, {"SPEAKER_00", "SPEAKER_01"}) == {
        "SPEAKER_00": "Gastgeber 1",
        "SPEAKER_01": "Gastgeber 2",
    }


def test_non_string_and_empty_names_are_tolerated() -> None:
    mapping = {"SPEAKER_00": "", "SPEAKER_01": None, "SPEAKER_02": "Host", "SPEAKER_03": "Host"}

    out = _disambiguate_generic_labels(mapping)

    assert out["SPEAKER_02"] == "Host 1"
    assert out["SPEAKER_03"] == "Host 2"
    assert out["SPEAKER_00"] == "" and out["SPEAKER_01"] is None


class _TwoUnnamedHostsProvider(_StubProvider):
    """Returns the same generic label for two different voices."""

    def generate_structured(self, messages, response_model, **kwargs):
        return response_model(
            speaker_mapping=[
                {"speaker_id": "SPEAKER_00", "name": "Host", "generic_label": True},
                {"speaker_id": "SPEAKER_01", "name": "Host", "generic_label": True},
            ]
        )


class _LegacyTwoUnnamedHostsProvider(_StubProvider):
    """Structured output fails; JSON mode returns the legacy dict shape."""

    def generate_structured(self, messages, response_model, **kwargs):
        raise RuntimeError("structured output unavailable")

    def chat_completion(self, messages, **kwargs):
        return '{"speaker_mapping": {"SPEAKER_00": "Voditelj", "SPEAKER_01": "Voditelj"}}'


_TRANSCRIPT = {
    "segments": [
        {"id": 0, "start": 0.0, "end": 2.0, "speaker": "SPEAKER_00", "text": "I support this."},
        {"id": 1, "start": 2.0, "end": 4.0, "speaker": "SPEAKER_01", "text": "I disagree."},
    ]
}


def _extract(provider) -> dict:
    facts = FactsExtractor(provider=provider).extract_episode_facts(
        transcript_data=_TRANSCRIPT,
        podcast_title="Netokracija",
        podcast_description="",
        episode_title="Episode",
        episode_description="",
        language="en",
    )
    return facts.speaker_mapping


def test_two_unnamed_hosts_stay_distinct_in_rendered_transcript() -> None:
    mapping = _extract(_TwoUnnamedHostsProvider())
    assert mapping == {"SPEAKER_00": "Host 1", "SPEAKER_01": "Host 2"}

    segments = [
        AnnotatedSegment(id=0, start=0.0, end=2.0, speaker="SPEAKER_00", text="I support this."),
        AnnotatedSegment(id=1, start=2.0, end=4.0, speaker="SPEAKER_01", text="I disagree."),
    ]
    rendered = AnnotatedTranscript(
        episode_id="ep", segments=_apply_speaker_mapping(segments, mapping)
    ).to_blended_markdown()

    # The reviewer's repro was "Host: I support this. I disagree."
    assert "I support this. I disagree." not in rendered
    assert "Host 1" in rendered and "Host 2" in rendered


def test_legacy_json_path_also_numbers_generic_labels() -> None:
    assert _extract(_LegacyTwoUnnamedHostsProvider()) == {"SPEAKER_00": "Voditelj 1", "SPEAKER_01": "Voditelj 2"}
