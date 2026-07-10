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

"""Compare two eval runs of the same rubric.

The headline of every comparison is its *classification* — what actually
differs between the runs — because that states what the deltas measure:

- identical artifacts, different judge/prompt  -> judge comparison
- different artifacts, identical judge/prompt  -> pipeline comparison
- both differ                                  -> confounded (deltas
  attribute to nothing; loud warning)
- neither differs                              -> reproducibility check
"""

import statistics
from dataclasses import dataclass, field
from typing import Dict, List, Literal, Optional, Tuple

from .models import ManifestItem, RunManifest

Classification = Literal["judge", "pipeline", "confounded", "reproducibility"]

# Deltas smaller than this many judge standard deviations are flagged as
# within judge noise (descriptive, not a hypothesis test).
NOISE_SIGMA_FACTOR = 2.0


@dataclass(frozen=True)
class DimensionDelta:
    dimension: str
    mean_a: float
    mean_b: float
    delta: float
    median_delta: float
    improved: int
    regressed: int
    unchanged: int
    # None when neither run sampled more than once (no noise estimate).
    within_noise: Optional[bool] = None


@dataclass(frozen=True)
class EpisodeDelta:
    podcast_slug: str
    episode_slug: str
    deltas: Dict[str, float]
    artifacts_match: bool


@dataclass(frozen=True)
class RunComparison:
    run_a: str
    run_b: str
    rubric: str
    classification: Classification
    judge_differs: bool
    prompt_differs: bool
    artifacts_differ: bool
    dimensions: List[DimensionDelta] = field(default_factory=list)
    episodes: List[EpisodeDelta] = field(default_factory=list)
    only_in_a: List[str] = field(default_factory=list)
    only_in_b: List[str] = field(default_factory=list)
    failed_excluded: int = 0

    def to_dict(self) -> dict:
        return {
            "run_a": self.run_a,
            "run_b": self.run_b,
            "rubric": self.rubric,
            "classification": self.classification,
            "judge_differs": self.judge_differs,
            "prompt_differs": self.prompt_differs,
            "artifacts_differ": self.artifacts_differ,
            "dimensions": [vars(d) for d in self.dimensions],
            "episodes": [
                {
                    "podcast_slug": e.podcast_slug,
                    "episode_slug": e.episode_slug,
                    "deltas": e.deltas,
                    "artifacts_match": e.artifacts_match,
                }
                for e in self.episodes
            ],
            "only_in_a": self.only_in_a,
            "only_in_b": self.only_in_b,
            "failed_excluded": self.failed_excluded,
        }


def _episode_key(item: ManifestItem) -> Tuple[str, str]:
    return (item.podcast_slug, item.episode_slug)


def _artifacts_match(a: ManifestItem, b: ManifestItem) -> bool:
    hashes_a = {kind: ref.sha256 for kind, ref in a.artifacts.items()}
    hashes_b = {kind: ref.sha256 for kind, ref in b.artifacts.items()}
    return hashes_a == hashes_b


def _classify(judge_or_prompt_differs: bool, artifacts_differ: bool) -> Classification:
    if artifacts_differ and judge_or_prompt_differs:
        return "confounded"
    if artifacts_differ:
        return "pipeline"
    if judge_or_prompt_differs:
        return "judge"
    return "reproducibility"


def _mean_std(items: List[ManifestItem], dimension: str) -> Optional[float]:
    stds = [item.scores_std[dimension] for item in items if item.scores_std and dimension in item.scores_std]
    return statistics.fmean(stds) if stds else None


def compare_runs(manifest_a: RunManifest, manifest_b: RunManifest) -> RunComparison:
    """Join two runs on episode identity and compute per-dimension deltas."""
    if manifest_a.rubric.name != manifest_b.rubric.name:
        raise ValueError(
            f"cannot compare rubric {manifest_a.rubric.name!r} against {manifest_b.rubric.name!r}: "
            "score dimensions are not commensurable"
        )

    ok_a = {_episode_key(item): item for item in manifest_a.items if item.status == "ok"}
    ok_b = {_episode_key(item): item for item in manifest_b.items if item.status == "ok"}
    failed_excluded = sum(1 for item in (*manifest_a.items, *manifest_b.items) if item.status == "failed")

    joined_keys = sorted(ok_a.keys() & ok_b.keys())
    only_in_a = sorted("/".join(key) for key in ok_a.keys() - ok_b.keys())
    only_in_b = sorted("/".join(key) for key in ok_b.keys() - ok_a.keys())

    judge_differs = (
        manifest_a.judge.provider != manifest_b.judge.provider
        or manifest_a.judge.model != manifest_b.judge.model
        or manifest_a.judge.temperature != manifest_b.judge.temperature
    )
    prompt_differs = manifest_a.rubric.prompt_sha256 != manifest_b.rubric.prompt_sha256
    artifacts_differ = any(not _artifacts_match(ok_a[key], ok_b[key]) for key in joined_keys)

    episodes: List[EpisodeDelta] = []
    for key in joined_keys:
        item_a, item_b = ok_a[key], ok_b[key]
        deltas = {
            dimension: round(item_b.scores[dimension] - item_a.scores[dimension], 3)
            for dimension in (item_a.scores or {})
            if item_b.scores and dimension in item_b.scores
        }
        episodes.append(
            EpisodeDelta(
                podcast_slug=key[0],
                episode_slug=key[1],
                deltas=deltas,
                artifacts_match=_artifacts_match(item_a, item_b),
            )
        )

    dimensions: List[DimensionDelta] = []
    dimension_names = sorted({name for episode in episodes for name in episode.deltas})
    joined_a = [ok_a[key] for key in joined_keys]
    joined_b = [ok_b[key] for key in joined_keys]
    for dimension in dimension_names:
        per_episode = [episode.deltas[dimension] for episode in episodes if dimension in episode.deltas]
        mean_a = statistics.fmean(item.scores[dimension] for item in joined_a if item.scores)
        mean_b = statistics.fmean(item.scores[dimension] for item in joined_b if item.scores)
        noise = None
        stds = [std for std in (_mean_std(joined_a, dimension), _mean_std(joined_b, dimension)) if std is not None]
        if stds:
            noise = abs(mean_b - mean_a) < NOISE_SIGMA_FACTOR * max(stds)
        dimensions.append(
            DimensionDelta(
                dimension=dimension,
                mean_a=round(mean_a, 3),
                mean_b=round(mean_b, 3),
                delta=round(mean_b - mean_a, 3),
                median_delta=round(statistics.median(per_episode), 3) if per_episode else 0.0,
                improved=sum(1 for delta in per_episode if delta > 0),
                regressed=sum(1 for delta in per_episode if delta < 0),
                unchanged=sum(1 for delta in per_episode if delta == 0),
                within_noise=noise,
            )
        )

    return RunComparison(
        run_a=manifest_a.run_id,
        run_b=manifest_b.run_id,
        rubric=manifest_a.rubric.name,
        classification=_classify(judge_differs or prompt_differs, artifacts_differ),
        judge_differs=judge_differs,
        prompt_differs=prompt_differs,
        artifacts_differ=artifacts_differ,
        dimensions=dimensions,
        episodes=episodes,
        only_in_a=only_in_a,
        only_in_b=only_in_b,
        failed_excluded=failed_excluded,
    )
