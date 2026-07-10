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

"""Eval run execution: judge resolution, episode discovery, per-item judging.

Failure model (spec #42):
- FM-1: a judge/provider failure on one episode records ``status: failed``
  with the error on that item and continues; a run with failures is still
  written (partial results are visible) and the CLI exits non-zero.
- FM-4: transcript truncation and unpinned judges are flagged, never silent.
- FM-7: judge output passes sanitize -> json.loads -> report-model
  validation (retry once) before anything is persisted as a success.

Interruption safety: item reports are written as each item completes; the
manifest is written atomically (temp file + rename) at the end. A killed
run leaves item files but no manifest — ``eval list`` ignores it.
"""

import hashlib
import json
import statistics
import subprocess
import time
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path
from typing import Callable, Dict, List, Optional, Tuple

import structlog
from pydantic import ValidationError

from thestill.core.feed_manager import PodcastFeedManager
from thestill.core.llm_provider import (
    LLMProvider,
    create_llm_provider,
    create_llm_provider_from_config,
    provider_kwargs_from_config,
)
from thestill.models.podcast import Episode, Podcast
from thestill.utils.path_manager import PathManager
from thestill.utils.slug import generate_slug
from thestill.utils.text_sanitizer import sanitize_text

from .models import (
    ITEM_REPORT_SCHEMA_VERSION,
    ArtifactRef,
    DimensionStats,
    JudgeInfo,
    ManifestItem,
    RubricInfo,
    RunManifest,
    RunSummary,
)
from .rubrics import CLEAN_TRANSCRIPT, RAW_TRANSCRIPT, SUMMARY, Rubric

logger = structlog.get_logger()

# Rough guard against blowing the judge's context window: inputs beyond
# this many characters (~150k tokens at 4 chars/token) are truncated and
# the item is flagged ``transcript_truncated`` (FM-4). Chunked judging is
# future work (spec #53 "Context limits").
MAX_INPUT_CHARS = 600_000

MANIFEST_FILENAME = "manifest.json"
SUMMARY_FILENAME = "summary.json"
ITEMS_DIRNAME = "items"


class EvalError(Exception):
    """Raised for run-level eval failures (bad input, duplicate run, ...)."""


@dataclass(frozen=True)
class JudgeResolution:
    """The judge provider plus the manifest record of how it was chosen."""

    provider: LLMProvider
    info: JudgeInfo


def resolve_judge(
    config,
    cli_provider: Optional[str] = None,
    cli_model: Optional[str] = None,
    cli_temperature: Optional[float] = None,
) -> JudgeResolution:
    """Resolve the judge with the spec's precedence: CLI flag -> EVAL_JUDGE_* -> pipeline.

    A judge is *pinned* when its provider or model came from a CLI flag or
    the EVAL_JUDGE_* config — i.e. it was chosen deliberately for judging
    rather than inherited from whatever the pipeline happens to use.
    """
    pinned = bool(cli_provider or cli_model or config.eval_judge_provider or config.eval_judge_model)

    if cli_temperature is not None:
        temperature = cli_temperature
    else:
        temperature = config.eval_judge_temperature

    if not pinned:
        provider = create_llm_provider_from_config(config)
        info = JudgeInfo(
            provider=config.llm_provider,
            model=provider.get_model_name(),
            temperature=temperature,
            pinned=False,
        )
        return JudgeResolution(provider=provider, info=info)

    provider_name = (cli_provider or config.eval_judge_provider or config.llm_provider).lower()

    # EVAL_JUDGE_PROVIDER/EVAL_JUDGE_MODEL are a coupled pair: the env model
    # only applies when the winning provider is the family it was written
    # for. A --judge-provider override of a different family must fall back
    # to that family's configured default model, not inherit an env model
    # id from another provider (e.g. AnthropicProvider with a gpt-* id).
    env_model_family = (config.eval_judge_provider or config.llm_provider).lower()
    if cli_model:
        model_name = cli_model
    elif config.eval_judge_model and provider_name == env_model_family:
        model_name = config.eval_judge_model
    else:
        model_name = ""

    provider_kwargs = provider_kwargs_from_config(config)
    if model_name:
        provider_kwargs[f"{provider_name}_model"] = model_name
    provider = create_llm_provider(provider_type=provider_name, **provider_kwargs)
    info = JudgeInfo(
        provider=provider_name,
        model=provider.get_model_name(),
        temperature=temperature,
        pinned=True,
    )
    return JudgeResolution(provider=provider, info=info)


def _git_commit() -> Optional[str]:
    try:
        result = subprocess.run(
            ["git", "rev-parse", "--short", "HEAD"],
            capture_output=True,
            text=True,
            timeout=5,
            check=True,
        )
        return result.stdout.strip() or None
    except (OSError, subprocess.SubprocessError):
        return None


def _sha256_file(path: Path) -> str:
    digest = hashlib.sha256()
    with open(path, "rb") as f:
        for block in iter(lambda: f.read(1 << 20), b""):
            digest.update(block)
    return digest.hexdigest()


def _atomic_write_json(path: Path, payload: dict) -> None:
    tmp = path.with_suffix(path.suffix + ".tmp")
    with open(tmp, "w", encoding="utf-8") as f:
        json.dump(payload, f, indent=2, ensure_ascii=False)
    tmp.replace(path)


class EvalRunner:
    """Executes one eval run over discovered (or pinned) episodes."""

    def __init__(self, config, path_manager: PathManager, feed_manager: PodcastFeedManager):
        self.config = config
        self.path_manager = path_manager
        self.feed_manager = feed_manager

    # -- discovery ---------------------------------------------------------

    def _artifact_path(self, kind: str, episode: Episode) -> Optional[Path]:
        """Resolve an artifact kind to its on-disk path, or None if unset.

        Episode ``*_path`` fields embed the podcast-slug subdirectory, so
        the flat PathManager variants are the correct resolvers here.
        """
        if kind == RAW_TRANSCRIPT and episode.raw_transcript_path:
            return self.path_manager.raw_transcript_file(episode.raw_transcript_path)
        if kind == CLEAN_TRANSCRIPT and episode.clean_transcript_path:
            return self.path_manager.clean_transcript_file(episode.clean_transcript_path)
        if kind == SUMMARY and episode.summary_path:
            return self.path_manager.summary_file(episode.summary_path)
        return None

    def discover(
        self,
        rubric: Rubric,
        podcast_rss_url: Optional[str] = None,
        episode_external_id: Optional[str] = None,
        max_episodes: Optional[int] = None,
        episodes_file: Optional[Path] = None,
    ) -> List[Tuple[Podcast, Episode]]:
        """Select episodes that have every required artifact on disk.

        Newest first across all podcasts (matching the legacy commands'
        cross-podcast prioritization for ``max_episodes``).
        ``podcast_rss_url`` is pre-resolved by the CLI via PodcastService
        (which accepts index, URL, or UUID).
        """
        candidates: List[Tuple[Podcast, Episode]] = []
        for podcast in self.feed_manager.list_podcasts():
            for episode in podcast.episodes:
                paths = [self._artifact_path(kind, episode) for kind in rubric.inputs]
                if all(path is not None and path.exists() for path in paths):
                    candidates.append((podcast, episode))
        candidates.sort(
            key=lambda pair: pair[1].pub_date or datetime.min.replace(tzinfo=timezone.utc),
            reverse=True,
        )

        if podcast_rss_url:
            candidates = [(p, e) for p, e in candidates if str(p.rss_url) == podcast_rss_url]

        if episode_external_id:
            candidates = [(p, e) for p, e in candidates if e.external_id == episode_external_id]

        if episodes_file:
            pinned = json.loads(Path(episodes_file).read_text(encoding="utf-8"))
            wanted = {(entry["podcast_slug"], entry["episode_slug"]) for entry in pinned["episodes"]}
            selected = [(p, e) for p, e in candidates if (p.slug, e.slug) in wanted]
            found = {(p.slug, e.slug) for p, e in selected}
            for missing in sorted(wanted - found):
                # No silent truncation of a pinned set — a golden episode
                # whose artifacts are gone must be loud.
                logger.warning("eval_pinned_episode_unavailable", podcast_slug=missing[0], episode_slug=missing[1])
            candidates = selected

        if max_episodes:
            candidates = candidates[:max_episodes]
        return candidates

    # -- judging -----------------------------------------------------------

    def _chat(self, judge: JudgeResolution, messages: List[Dict[str, str]]) -> str:
        temperature = judge.info.temperature if judge.provider.supports_temperature() else None
        return judge.provider.chat_completion(
            messages=messages, temperature=temperature, response_format={"type": "json_object"}
        )

    def _judge_once(self, rubric: Rubric, judge: JudgeResolution, messages: List[Dict[str, str]]) -> dict:
        """One judgement: sanitize -> parse -> validate, retrying once (FM-7)."""
        last_error: Optional[Exception] = None
        for attempt in (1, 2):
            raw = self._chat(judge, messages)
            clean, removed = sanitize_text(raw)
            if removed:
                logger.warning("eval_judge_control_chars_stripped", removed=removed, attempt=attempt)
            try:
                parsed = json.loads(clean)
                rubric.report_model.model_validate(parsed)
                return parsed
            except (json.JSONDecodeError, ValidationError) as exc:
                last_error = exc
                logger.warning("eval_judge_report_invalid", attempt=attempt, error=str(exc)[:500])
        raise EvalError(f"judge returned invalid report twice: {last_error}")

    def evaluate_texts(
        self,
        rubric: Rubric,
        judge: JudgeResolution,
        artifacts: Dict[str, str],
        samples: int = 1,
    ) -> Tuple[List[dict], bool]:
        """Judge one item's artifact texts ``samples`` times.

        Returns (reports, truncated). Shared by runs and the legacy
        single-file wrappers.
        """
        # The char budget bounds the COMBINED input (split evenly across
        # artifacts) so a multi-artifact rubric can't sum past the judge's
        # context window with every part individually under the cap.
        truncated = False
        bounded: Dict[str, str] = {}
        per_artifact_budget = MAX_INPUT_CHARS // max(1, len(artifacts))
        for kind, text in artifacts.items():
            if len(text) > per_artifact_budget:
                bounded[kind] = text[:per_artifact_budget]
                truncated = True
            else:
                bounded[kind] = text
        messages = [
            {"role": "system", "content": rubric.system_prompt},
            {"role": "user", "content": rubric.render_user_message(bounded)},
        ]
        reports = [self._judge_once(rubric, judge, messages) for _ in range(samples)]
        return reports, truncated

    # -- run orchestration ---------------------------------------------------

    @staticmethod
    def build_run_id(rubric_name: str, label: Optional[str], now: Optional[datetime] = None) -> str:
        now = now or datetime.now(timezone.utc)
        run_id = f"{now.strftime('%Y%m%d-%H%M%S')}-{rubric_name}"
        if label:
            run_id += f"-{generate_slug(label)}"
        return run_id

    def run(
        self,
        rubric: Rubric,
        judge: JudgeResolution,
        items: List[Tuple[Podcast, Episode]],
        label: Optional[str] = None,
        note: Optional[str] = None,
        samples: int = 1,
        on_item: Optional[Callable[[ManifestItem], None]] = None,
    ) -> RunManifest:
        """Execute a run and persist it under ``evaluations/runs/<run_id>/``."""
        if samples < 1:
            raise EvalError("--samples must be >= 1")
        run_id = self.build_run_id(rubric.name, label)
        run_dir = self.path_manager.evaluation_run_dir(run_id)
        if run_dir.exists():
            raise EvalError(f"run directory already exists: {run_dir}")
        items_dir = run_dir / ITEMS_DIRNAME
        items_dir.mkdir(parents=True)

        judge_info = judge.info.model_copy(update={"samples": samples})
        manifest = RunManifest(
            run_id=run_id,
            label=label,
            note=note,
            created_at=datetime.now(timezone.utc).isoformat(),
            git_commit=_git_commit(),
            rubric=RubricInfo(name=rubric.name, version=rubric.version, prompt_sha256=rubric.prompt_sha256),
            judge=judge_info,
        )

        structlog.contextvars.bind_contextvars(run_id=run_id)
        try:
            for podcast, episode in items:
                manifest.items.append(self._run_item(rubric, judge, podcast, episode, samples, items_dir))
                if on_item:
                    on_item(manifest.items[-1])
        finally:
            structlog.contextvars.unbind_contextvars("run_id")

        manifest.counts = {
            "ok": sum(1 for item in manifest.items if item.status == "ok"),
            "failed": sum(1 for item in manifest.items if item.status == "failed"),
        }
        _atomic_write_json(run_dir / SUMMARY_FILENAME, summarize_run(manifest).model_dump())
        _atomic_write_json(run_dir / MANIFEST_FILENAME, manifest.model_dump())
        logger.info("eval_run_completed", run_id=run_id, **manifest.counts)
        return manifest

    def _run_item(
        self,
        rubric: Rubric,
        judge: JudgeResolution,
        podcast: Podcast,
        episode: Episode,
        samples: int,
        items_dir: Path,
    ) -> ManifestItem:
        started = time.monotonic()
        structlog.contextvars.bind_contextvars(episode_id=episode.external_id)
        try:
            artifact_refs: Dict[str, ArtifactRef] = {}
            artifact_texts: Dict[str, str] = {}
            for kind in (*rubric.inputs, *rubric.optional_inputs):
                path = self._artifact_path(kind, episode)
                if path is None or not path.exists():
                    if kind in rubric.inputs:
                        raise EvalError(f"required artifact missing: {kind}")
                    continue
                artifact_texts[kind] = path.read_text(encoding="utf-8")
                relative = str(path.relative_to(self.path_manager.storage_path))
                artifact_refs[kind] = ArtifactRef(path=relative, sha256=_sha256_file(path))

            reports, truncated = self.evaluate_texts(rubric, judge, artifact_texts, samples=samples)

            checks = None
            if rubric.deterministic_checks is not None:
                checks = rubric.deterministic_checks(artifact_texts, episode.duration)

            report_file = f"{ITEMS_DIRNAME}/{podcast.slug}_{episode.slug}.json"
            _atomic_write_json(
                items_dir / f"{podcast.slug}_{episode.slug}.json",
                {
                    "schema_version": ITEM_REPORT_SCHEMA_VERSION,
                    "rubric": {"name": rubric.name, "version": rubric.version},
                    "podcast_slug": podcast.slug,
                    "episode_slug": episode.slug,
                    "reports": reports,
                    "checks": checks,
                },
            )

            scores: Dict[str, float] = {}
            scores_std: Optional[Dict[str, float]] = {} if samples > 1 else None
            for dimension in rubric.dimensions:
                values = [float(report["scores"][dimension]) for report in reports]
                scores[dimension] = statistics.fmean(values)
                if scores_std is not None:
                    scores_std[dimension] = statistics.stdev(values) if len(values) > 1 else 0.0

            return ManifestItem(
                podcast_slug=podcast.slug,
                episode_slug=episode.slug,
                external_id=episode.external_id,
                artifacts=artifact_refs,
                status="ok",
                report_file=report_file,
                scores=scores,
                scores_std=scores_std,
                checks_ok=None if checks is None else bool(checks.get("ok")),
                transcript_truncated=truncated,
                duration_s=round(time.monotonic() - started, 1),
            )
        except Exception as exc:  # noqa: BLE001 — FM-1: isolate per-item failures
            logger.error("eval_item_failed", podcast_slug=podcast.slug, episode_slug=episode.slug, error=str(exc))
            return ManifestItem(
                podcast_slug=podcast.slug,
                episode_slug=episode.slug,
                external_id=episode.external_id,
                status="failed",
                error=f"{type(exc).__name__}: {exc}",
                duration_s=round(time.monotonic() - started, 1),
            )
        finally:
            structlog.contextvars.unbind_contextvars("episode_id")


def summarize_run(manifest: RunManifest) -> RunSummary:
    """Aggregate per-dimension stats over a run's ok items."""
    by_dimension: Dict[str, List[float]] = {}
    for item in manifest.items:
        if item.status != "ok" or not item.scores:
            continue
        for dimension, value in item.scores.items():
            by_dimension.setdefault(dimension, []).append(value)
    dimensions = {
        name: DimensionStats(
            mean=statistics.fmean(values),
            median=statistics.median(values),
            min=min(values),
            max=max(values),
            n=len(values),
        )
        for name, values in by_dimension.items()
    }
    return RunSummary(run_id=manifest.run_id, dimensions=dimensions, counts=manifest.counts)


def load_manifest(path_manager: PathManager, run_id: str) -> RunManifest:
    """Load a run's manifest, raising EvalError when absent."""
    manifest_path = path_manager.evaluation_run_dir(run_id) / MANIFEST_FILENAME
    if not manifest_path.exists():
        raise EvalError(f"no manifest for run {run_id!r} (incomplete or unknown run)")
    return RunManifest.model_validate_json(manifest_path.read_text(encoding="utf-8"))


def list_manifests(path_manager: PathManager) -> List[RunManifest]:
    """All completed runs (those with a manifest), newest first."""
    runs_dir = path_manager.evaluation_runs_dir()
    if not runs_dir.exists():
        return []
    manifests = []
    for entry in sorted(runs_dir.iterdir(), reverse=True):
        manifest_path = entry / MANIFEST_FILENAME
        if manifest_path.exists():
            manifests.append(RunManifest.model_validate_json(manifest_path.read_text(encoding="utf-8")))
    return manifests
