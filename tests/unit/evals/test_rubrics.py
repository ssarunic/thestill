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

"""Rubric registry: prompt-hash pinning and internal consistency."""

import pytest
from pydantic import ValidationError

from thestill.evals.rubrics import RUBRICS, get_rubric

# Editing a rubric prompt without bumping its version silently poisons
# comparability across runs. These hashes pin (name, version) -> prompt
# text: if you changed a prompt, bump the rubric's version AND update the
# hash here — never just the hash.
PINNED_PROMPT_HASHES = {
    ("raw-transcript", "1"): "8ef093c39df4f2eea9fa4eb70527debad8aef89670a8622a9de5eb2e1f92c8a9",
    ("clean-transcript", "1"): "7d47e7ed70a24dd5680d1c7213a901d505d1fb00cd623896540f5b2855fa6f90",
    ("summary", "1"): "ac8c836313d3f3bc85bc4588d4b11d9f118b9ba6d8c91df769ce39ef3ba9080e",
}


def test_every_rubric_prompt_hash_is_pinned():
    actual = {(rubric.name, rubric.version): rubric.prompt_sha256 for rubric in RUBRICS.values()}
    assert actual == PINNED_PROMPT_HASHES


@pytest.mark.parametrize("rubric", RUBRICS.values(), ids=lambda r: r.name)
def test_dimensions_match_report_model(rubric):
    scores_model = rubric.report_model.model_fields["scores"].annotation
    assert set(rubric.dimensions) == set(scores_model.model_fields)


@pytest.mark.parametrize("rubric", RUBRICS.values(), ids=lambda r: r.name)
def test_render_user_message_includes_required_artifacts(rubric):
    artifacts = {kind: f"<<{kind}-content>>" for kind in (*rubric.inputs, *rubric.optional_inputs)}
    message = rubric.render_user_message(artifacts)
    for kind in rubric.inputs:
        assert f"<<{kind}-content>>" in message


def test_clean_transcript_message_omits_missing_optional_original():
    rubric = get_rubric("clean-transcript")
    message = rubric.render_user_message({"clean_transcript": "CLEANED"})
    assert "CLEANED" in message
    assert "comparison" not in message


def test_get_rubric_unknown_name_lists_available():
    with pytest.raises(ValueError, match="clean-transcript"):
        get_rubric("nope")


def test_report_model_rejects_out_of_range_scores():
    rubric = get_rubric("summary")
    with pytest.raises(ValidationError):
        rubric.report_model.model_validate(
            {
                "coverage": {"missing_topics": {"count": 0, "examples": []}},
                "faithfulness": {
                    "invented_claims": {"count": 0, "examples": []},
                    "distorted_claims": {"count": 0, "examples": []},
                },
                "attribution": {"misattributions": {"count": 0, "examples": []}},
                "scores": {"coverage": 11, "faithfulness": 5, "attribution": 5, "insight_value": 5},
                "summary": {"strengths": [], "weaknesses": [], "verdict": "x"},
            }
        )
