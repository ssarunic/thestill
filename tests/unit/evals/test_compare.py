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

"""compare_runs: classification, deltas, unmatched-item accounting."""

import pytest

from thestill.evals.compare import compare_runs
from thestill.evals.models import ArtifactRef, JudgeInfo, ManifestItem, RubricInfo, RunManifest


def make_manifest(run_id, *, model="judge-a", prompt_sha="p1", items=()):
    return RunManifest(
        run_id=run_id,
        created_at="2026-07-10T12:00:00+00:00",
        rubric=RubricInfo(name="raw-transcript", version="1", prompt_sha256=prompt_sha),
        judge=JudgeInfo(provider="mock", model=model, temperature=0.0, pinned=True),
        items=list(items),
        counts={
            "ok": sum(1 for item in items if item.status == "ok"),
            "failed": sum(1 for item in items if item.status == "failed"),
        },
    )


def make_item(episode, *, sha="abc", accuracy=5.0, status="ok", std=None):
    return ManifestItem(
        podcast_slug="pod",
        episode_slug=episode,
        artifacts={"raw_transcript": ArtifactRef(path=f"raw/{episode}.json", sha256=sha)},
        status=status,
        scores={"accuracy": accuracy} if status == "ok" else None,
        scores_std={"accuracy": std} if std is not None else None,
        report_file=None if status == "failed" else f"items/pod_{episode}.json",
        error="boom" if status == "failed" else None,
    )


class TestClassification:
    def test_same_artifacts_different_judge_is_judge_comparison(self):
        a = make_manifest("a", model="judge-a", items=[make_item("e1", sha="s1")])
        b = make_manifest("b", model="judge-b", items=[make_item("e1", sha="s1")])
        assert compare_runs(a, b).classification == "judge"

    def test_different_artifacts_same_judge_is_pipeline_comparison(self):
        a = make_manifest("a", items=[make_item("e1", sha="s1")])
        b = make_manifest("b", items=[make_item("e1", sha="s2")])
        assert compare_runs(a, b).classification == "pipeline"

    def test_both_differ_is_confounded(self):
        a = make_manifest("a", model="judge-a", items=[make_item("e1", sha="s1")])
        b = make_manifest("b", model="judge-b", items=[make_item("e1", sha="s2")])
        assert compare_runs(a, b).classification == "confounded"

    def test_nothing_differs_is_reproducibility(self):
        a = make_manifest("a", items=[make_item("e1", sha="s1")])
        b = make_manifest("b", items=[make_item("e1", sha="s1")])
        assert compare_runs(a, b).classification == "reproducibility"

    def test_prompt_change_counts_as_judge_difference(self):
        a = make_manifest("a", prompt_sha="p1", items=[make_item("e1", sha="s1")])
        b = make_manifest("b", prompt_sha="p2", items=[make_item("e1", sha="s1")])
        comparison = compare_runs(a, b)
        assert comparison.prompt_differs is True
        assert comparison.classification == "judge"


class TestDeltas:
    def test_dimension_deltas_and_direction_counts(self):
        a = make_manifest(
            "a", items=[make_item("e1", accuracy=5.0), make_item("e2", accuracy=6.0), make_item("e3", accuracy=7.0)]
        )
        b = make_manifest(
            "b", items=[make_item("e1", accuracy=7.0), make_item("e2", accuracy=5.0), make_item("e3", accuracy=7.0)]
        )
        comparison = compare_runs(a, b)
        (dim,) = comparison.dimensions
        assert dim.mean_a == pytest.approx(6.0)
        assert dim.mean_b == pytest.approx(6.333)
        assert dim.delta == pytest.approx(0.333)
        assert (dim.improved, dim.regressed, dim.unchanged) == (1, 1, 1)

    def test_unmatched_and_failed_items_are_accounted_not_silently_dropped(self):
        a = make_manifest("a", items=[make_item("e1"), make_item("only-a"), make_item("bad", status="failed")])
        b = make_manifest("b", items=[make_item("e1"), make_item("only-b")])
        comparison = compare_runs(a, b)
        assert comparison.only_in_a == ["pod/only-a"]
        assert comparison.only_in_b == ["pod/only-b"]
        assert comparison.failed_excluded == 1
        assert len(comparison.episodes) == 1

    def test_noise_flag_uses_sample_std(self):
        a = make_manifest("a", items=[make_item("e1", accuracy=5.0, std=1.5)])
        b = make_manifest("b", items=[make_item("e1", accuracy=6.0, std=1.5)])
        comparison = compare_runs(a, b)
        (dim,) = comparison.dimensions
        assert dim.within_noise is True  # |1.0| < 2 * 1.5

    def test_no_samples_means_no_noise_estimate(self):
        a = make_manifest("a", items=[make_item("e1", accuracy=5.0)])
        b = make_manifest("b", items=[make_item("e1", accuracy=6.0)])
        (dim,) = compare_runs(a, b).dimensions
        assert dim.within_noise is None


def test_rubric_mismatch_is_refused():
    a = make_manifest("a", items=[make_item("e1")])
    b = make_manifest("b", items=[make_item("e1")])
    b.rubric = RubricInfo(name="summary", version="1", prompt_sha256="p9")
    with pytest.raises(ValueError, match="not commensurable"):
        compare_runs(a, b)
