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

"""The ``entity-linking`` rubric: the live linker against the stored baseline.

Spec #81's cutover gate. For each episode the live linker decides the same
names the pipeline's linker already decided, and the two answers are
compared name by name. Agreement is free; every disagreement goes to the
judge, blind to which linker said what.

The baseline is what is stored in ``entity_mentions`` - what ReFinED
actually wrote in production - not a fresh ReFinED run. That needs no
several-GB model on the eval host and measures the thing being replaced.

The framework scores 0-10 per dimension per episode, so the per-name
verdicts are turned into four derived scores for ``eval list/show/compare``.
The cutover decision is made on the corpus-level counts in ``totals.json``:
a mean of per-episode ratios would let a three-name episode outvote a
ninety-name one.
"""

from __future__ import annotations

import hashlib
import time
from collections import Counter
from dataclasses import asdict, dataclass, field
from datetime import datetime, timezone
from pathlib import Path
from typing import Callable, Dict, List, Optional, Tuple

import structlog

from ..core.entity_linking.live_linker import LinkOutcome, LiveWikidataLinker, group_mentions
from ..core.entity_linking.types import LinkContext, NameGroup
from ..core.entity_linking.validator import meets_confidence
from ..core.feed_manager import PodcastFeedManager
from ..models.entities import EntityMention, ResolutionMethod
from ..models.podcast import Episode, Podcast
from ..repositories.link_decision_repository import LinkDecisionRepository, StoredLinkDecision
from ..utils.path_manager import PathManager
from ..utils.prompt_safety import wrap_untrusted
from .models import ITEM_REPORT_SCHEMA_VERSION, ManifestItem, RunManifest
from .rubrics import ENTITY_LINKING, LinkingJudgeReport, Rubric
from .runner import ITEMS_DIRNAME, EvalError, EvalRunner, JudgeResolution, _atomic_write_json

logger = structlog.get_logger(__name__)

RUBRIC_NAME = ENTITY_LINKING
TOTALS_FILENAME = "totals.json"
JUDGE_BATCH_SIZE = 30
EXCERPT_MAX_CHARS = 400

# Spec #81 "Evaluation" - the starting position; the first real run revises it.
MAX_REGRESSION_RATE = 0.02
MIN_RECALL_GAIN = 0.25
MIN_NEW_LINK_PRECISION = 0.90

# How the two linkers' answers for one name relate.
SAME_LINK = "same_link"
BOTH_NONE = "both_none"
DIFFERENT_LINK = "different_link"
LIVE_ONLY = "live_only"
BASELINE_ONLY = "baseline_only"
UNANSWERED = "unanswered"
_JUDGED = (DIFFERENT_LINK, LIVE_ONLY, BASELINE_ONLY)

# Who the judge said was right.
LIVE_RIGHT = "live_right"
BASELINE_RIGHT = "baseline_right"
BOTH_RIGHT = "both_right"
NEITHER_RIGHT = "neither_right"
UNCLEAR = "unclear"


@dataclass(frozen=True)
class Answer:
    """One linker's answer for one name. ``qid is None`` is "no link"."""

    qid: Optional[str] = None
    label: str = ""
    description: str = ""


@dataclass
class NameComparison:
    surface_key: str
    surface_form: str
    excerpts: List[str]
    baseline: Answer
    live: Answer
    outcome: str
    live_confidence: str = ""
    # ReFinED decides per mention; the live linker decides per name. True
    # when the baseline gave this one name more than one answer - the
    # evidence for or against that simplification.
    baseline_split: bool = False
    verdict: Optional[str] = None
    judge_reason: str = ""


@dataclass
class EpisodeCounts:
    names: int = 0
    skipped_no_baseline: int = 0
    baseline_split: int = 0
    outcomes: Dict[str, int] = field(default_factory=dict)
    # verdicts[outcome][verdict]
    verdicts: Dict[str, Dict[str, int]] = field(default_factory=dict)

    def outcome(self, kind: str) -> int:
        return self.outcomes.get(kind, 0)

    def verdict(self, kind: str, *verdicts: str) -> int:
        return sum(self.verdicts.get(kind, {}).get(v, 0) for v in verdicts)

    def add(self, other: "EpisodeCounts") -> None:
        self.names += other.names
        self.skipped_no_baseline += other.skipped_no_baseline
        self.baseline_split += other.baseline_split
        for kind, n in other.outcomes.items():
            self.outcomes[kind] = self.outcomes.get(kind, 0) + n
        for kind, by_verdict in other.verdicts.items():
            mine = self.verdicts.setdefault(kind, {})
            for verdict, n in by_verdict.items():
                mine[verdict] = mine.get(verdict, 0) + n


class _NoMemory(LinkDecisionRepository):
    """The eval judges what the linker decides now, not what it remembered,
    and must leave no trace: nothing is read, nothing is kept."""

    def get(self, surface_key: str, podcast_id: Optional[str]) -> Optional[StoredLinkDecision]:
        return None

    def upsert(self, decision: StoredLinkDecision) -> None:
        return None

    def record_hit(self, surface_key: str, podcast_id: Optional[str]) -> None:
        return None

    def podcast_decisions(self, surface_key: str) -> List[StoredLinkDecision]:
        return []

    def delete(self, surface_key: str) -> int:
        return 0


def no_memory() -> LinkDecisionRepository:
    return _NoMemory()


# ---------------------------------------------------------------------------
# Comparison
# ---------------------------------------------------------------------------


def baseline_answer(members: List[EntityMention], entity_lookup: Callable[[str], object]) -> Tuple[Answer, bool]:
    """What the stored resolutions say about one name, and whether they agree.

    The majority answer stands, ties going to "no link": a name ReFinED
    linked in a minority of its mentions was, on balance, left unlinked.
    """
    per_mention: List[Optional[str]] = []
    answers: Dict[str, Answer] = {}
    for mention in members:
        entity = entity_lookup(mention.entity_id) if mention.entity_id else None
        qid = getattr(entity, "wikidata_qid", None)
        per_mention.append(qid)
        if qid and qid not in answers:
            answers[qid] = Answer(
                qid, getattr(entity, "canonical_name", "") or "", getattr(entity, "description", "") or ""
            )
    split = len(set(per_mention)) > 1
    linked = [qid for qid in per_mention if qid]
    if len(linked) * 2 <= len(per_mention):
        return Answer(), split
    return answers[Counter(linked).most_common(1)[0][0]], split


def compare_names(
    groups: List[NameGroup],
    mentions: List[EntityMention],
    outcome: LinkOutcome,
    entity_lookup: Callable[[str], object],
    *,
    min_confidence: str,
) -> Tuple[List[NameComparison], int]:
    """One comparison per name both linkers can be compared on; the second
    value counts names skipped because no ReFinED baseline exists for them."""
    by_id = {m.id: m for m in mentions}
    comparisons: List[NameComparison] = []
    skipped = 0
    for group in groups:
        members = [by_id[i] for i in group.mention_ids]
        if any(m.resolution_method == ResolutionMethod.LLM_LINKED for m in members):
            skipped += 1  # already decided by the live linker: nothing to compare against
            continue
        baseline, split = baseline_answer(members, entity_lookup)
        decision = outcome.decisions.get(group.surface_key)
        live = Answer()
        if decision is None:
            kind = UNANSWERED
        else:
            if decision.qid and meets_confidence(decision.confidence, min_confidence):
                candidate = decision.candidate
                live = Answer(
                    decision.qid, candidate.label if candidate else "", candidate.description if candidate else ""
                )
            if live.qid and baseline.qid:
                kind = SAME_LINK if live.qid == baseline.qid else DIFFERENT_LINK
            elif live.qid:
                kind = LIVE_ONLY
            elif baseline.qid:
                kind = BASELINE_ONLY
            else:
                kind = BOTH_NONE
        comparisons.append(
            NameComparison(
                surface_key=group.surface_key,
                surface_form=group.surface_form,
                excerpts=group.excerpts,
                baseline=baseline,
                live=live,
                outcome=kind,
                live_confidence=decision.confidence if decision else "",
                baseline_split=split,
            )
        )
    return comparisons, skipped


def live_is_side_a(surface_key: str) -> bool:
    """Which side the live linker's answer is shown on: stable per name, so
    a rerun asks the same question, and even across names, so the judge
    cannot learn that one side is the newer system."""
    return hashlib.sha256(surface_key.encode("utf-8")).digest()[0] % 2 == 0


def _render_answer(answer: Answer) -> str:
    if not answer.qid:
        return "no link"
    description = " ".join(answer.description.split())[:160] or "(no description)"
    return f"{answer.qid} | {' '.join(answer.label.split())} | {description}"


def render_judge_message(by_id: Dict[str, NameComparison], context: LinkContext) -> str:
    header = f"Podcast: {' '.join(context.podcast_title.split())}\nEpisode: {' '.join(context.episode_title.split())}"
    blocks = [wrap_untrusted(header, label="EPISODE")]
    for item_id, comparison in by_id.items():
        live_first = live_is_side_a(comparison.surface_key)
        side_a, side_b = (
            (comparison.live, comparison.baseline) if live_first else (comparison.baseline, comparison.live)
        )
        lines = [f"name: {' '.join(comparison.surface_form.split())}"]
        lines += [f"excerpt: {' '.join(e.split())[:EXCERPT_MAX_CHARS]}" for e in comparison.excerpts]
        lines += [f"A: {_render_answer(side_a)}", f"B: {_render_answer(side_b)}"]
        blocks.append(f"[{item_id}]\n" + wrap_untrusted("\n".join(lines), label="ITEM"))
    return "\n\n".join(blocks)


def verdict_for(comparison: NameComparison, correct: str) -> str:
    if correct in ("both", "neither", "unclear"):
        return {"both": BOTH_RIGHT, "neither": NEITHER_RIGHT, "unclear": UNCLEAR}[correct]
    return LIVE_RIGHT if (correct == "a") == live_is_side_a(comparison.surface_key) else BASELINE_RIGHT


def count(comparisons: List[NameComparison], skipped: int) -> EpisodeCounts:
    counts = EpisodeCounts(names=len(comparisons), skipped_no_baseline=skipped)
    for c in comparisons:
        counts.outcomes[c.outcome] = counts.outcomes.get(c.outcome, 0) + 1
        counts.baseline_split += 1 if c.baseline_split else 0
        if c.verdict:
            by_verdict = counts.verdicts.setdefault(c.outcome, {})
            by_verdict[c.verdict] = by_verdict.get(c.verdict, 0) + 1
    return counts


def _ratio(numerator: int, denominator: int) -> Optional[float]:
    return numerator / denominator if denominator else None


def metrics(counts: EpisodeCounts) -> Dict[str, Optional[float]]:
    """Ratios in 0..1, ``None`` when there is nothing to measure.

    - agreement: names both linkers answered the same way;
    - regression_rate: of the names the baseline linked, those where the
      judge found the baseline right and the live linker wrong;
    - new_link_precision: of the live linker's links on names the baseline
      left unlinked, those the judge found right ("unclear" left out);
    - recall_gain: of the names the baseline left unlinked, those the live
      linker linked correctly.
    """
    compared = counts.names - counts.outcome(UNANSWERED)
    baseline_linked = counts.outcome(SAME_LINK) + counts.outcome(DIFFERENT_LINK) + counts.outcome(BASELINE_ONLY)
    baseline_unlinked = counts.outcome(BOTH_NONE) + counts.outcome(LIVE_ONLY)
    regressions = counts.verdict(DIFFERENT_LINK, BASELINE_RIGHT) + counts.verdict(BASELINE_ONLY, BASELINE_RIGHT)
    new_right = counts.verdict(LIVE_ONLY, LIVE_RIGHT, BOTH_RIGHT)
    new_wrong = counts.verdict(LIVE_ONLY, BASELINE_RIGHT, NEITHER_RIGHT)
    return {
        "agreement": _ratio(counts.outcome(SAME_LINK) + counts.outcome(BOTH_NONE), compared),
        "regression_rate": _ratio(regressions, baseline_linked),
        "new_link_precision": _ratio(new_right, new_right + new_wrong),
        "recall_gain": _ratio(new_right, baseline_unlinked),
    }


def derived_scores(counts: EpisodeCounts) -> Dict[str, float]:
    """The 0-10 view for ``eval list/show/compare``. A dimension with
    nothing to measure in this episode is left out, not scored."""
    m = metrics(counts)
    scores: Dict[str, float] = {}
    if m["agreement"] is not None:
        scores["agreement"] = round(10 * m["agreement"], 2)
    if m["regression_rate"] is not None:
        scores["no_regression"] = round(10 * (1 - m["regression_rate"]), 2)
    if m["new_link_precision"] is not None:
        scores["new_link_precision"] = round(10 * m["new_link_precision"], 2)
    if m["recall_gain"] is not None:
        scores["recall_gain"] = round(10 * m["recall_gain"], 2)
    return scores


def gate(totals: EpisodeCounts, checks_ok: bool) -> Dict[str, object]:
    """Spec #81's pass criteria, on the corpus-level counts."""
    m = metrics(totals)
    criteria = {
        "regression_rate_under_2pct": m["regression_rate"] is not None and m["regression_rate"] < MAX_REGRESSION_RATE,
        "recall_gain_at_least_25pct": m["recall_gain"] is not None and m["recall_gain"] >= MIN_RECALL_GAIN,
        "new_link_precision_at_least_90pct": m["new_link_precision"] is not None
        and m["new_link_precision"] >= MIN_NEW_LINK_PRECISION,
        "deterministic_checks_ok": checks_ok,
    }
    return {"metrics": m, "criteria": criteria, "passed": all(criteria.values())}


# ---------------------------------------------------------------------------
# Runner
# ---------------------------------------------------------------------------


class LinkingEvalRunner(EvalRunner):
    """Runs the ``entity-linking`` rubric. Reuses the framework's run,
    manifest and summary; what differs is where an episode's evidence comes
    from (the database, not artifact files) and how it is judged."""

    def __init__(
        self,
        config,
        path_manager: PathManager,
        feed_manager: PodcastFeedManager,
        *,
        entity_repository,
        linker: LiveWikidataLinker,
        context_builder: Callable[[object, Podcast, Episode], LinkContext],
    ):
        super().__init__(config, path_manager, feed_manager)
        self.entity_repository = entity_repository
        self.linker = linker
        self.context_builder = context_builder
        self.min_confidence = getattr(config, "entity_linking_min_confidence", "medium")
        self._episode_counts: List[EpisodeCounts] = []
        self._checks_ok = True

    # -- discovery -----------------------------------------------------------

    def discover(
        self,
        rubric: Rubric,
        podcast_rss_url: Optional[str] = None,
        episode_external_id: Optional[str] = None,
        max_episodes: Optional[int] = None,
        episodes_file: Optional[Path] = None,
    ) -> List[Tuple[Podcast, Episode]]:
        """Episodes with mentions a linker decided, newest first."""
        import json

        candidates: List[Tuple[Podcast, Episode]] = [
            (podcast, episode) for podcast in self.feed_manager.list_podcasts() for episode in podcast.episodes
        ]
        if podcast_rss_url:
            candidates = [(p, e) for p, e in candidates if str(p.rss_url) == podcast_rss_url]
        if episode_external_id:
            candidates = [(p, e) for p, e in candidates if e.external_id == episode_external_id]
        wanted = None
        if episodes_file:
            pinned = json.loads(Path(episodes_file).read_text(encoding="utf-8"))
            wanted = {(entry["podcast_slug"], entry["episode_slug"]) for entry in pinned["episodes"]}
            candidates = [(p, e) for p, e in candidates if (p.slug, e.slug) in wanted]
        candidates.sort(key=lambda pair: pair[1].pub_date or datetime.min.replace(tzinfo=timezone.utc), reverse=True)

        selected: List[Tuple[Podcast, Episode]] = []
        for podcast, episode in candidates:
            if max_episodes and len(selected) >= max_episodes:
                break
            if self.entity_repository.list_linker_decided_mentions(episode.id):
                selected.append((podcast, episode))
        if wanted is not None:
            for missing in sorted(wanted - {(p.slug, e.slug) for p, e in selected}):
                # No silent truncation of a pinned set.
                logger.warning("eval_pinned_episode_unavailable", podcast_slug=missing[0], episode_slug=missing[1])
        return selected

    # -- run -----------------------------------------------------------------

    def run(self, rubric, judge, items, label=None, note=None, samples=1, on_item=None) -> RunManifest:
        if samples != 1:
            raise EvalError("the entity-linking rubric judges each disagreement once; use --samples 1")
        self._episode_counts = []
        self._checks_ok = True
        manifest = super().run(rubric, judge, items, label=label, note=note, samples=1, on_item=on_item)
        totals = EpisodeCounts()
        for counts in self._episode_counts:
            totals.add(counts)
        _atomic_write_json(
            self.path_manager.evaluation_run_dir(manifest.run_id) / TOTALS_FILENAME,
            {"run_id": manifest.run_id, "counts": asdict(totals), **gate(totals, self._checks_ok)},
        )
        return manifest

    def _run_item(self, rubric, judge, podcast, episode, samples, items_dir) -> ManifestItem:
        started = time.monotonic()
        structlog.contextvars.bind_contextvars(episode_id=episode.external_id)
        try:
            repo = self.entity_repository
            mentions = repo.list_linker_decided_mentions(episode.id)
            if not mentions:
                raise EvalError("no linker-decided mentions for this episode")
            context = self.context_builder(repo, podcast, episode)
            groups = group_mentions(mentions)
            outcome = self.linker.link(groups, context, is_blacklisted=repo.is_blacklisted)
            comparisons, skipped = compare_names(
                groups, mentions, outcome, repo.get_entity, min_confidence=self.min_confidence
            )
            self._judge(rubric, judge, [c for c in comparisons if c.outcome in _JUDGED], context)

            counts = count(comparisons, skipped)
            checks = self._checks(comparisons, outcome, repo)
            self._episode_counts.append(counts)
            self._checks_ok = self._checks_ok and checks["ok"]

            filename = f"{podcast.slug}_{episode.slug}.json"
            _atomic_write_json(
                items_dir / filename,
                {
                    "schema_version": ITEM_REPORT_SCHEMA_VERSION,
                    "rubric": {"name": rubric.name, "version": rubric.version},
                    "podcast_slug": podcast.slug,
                    "episode_slug": episode.slug,
                    "linker_version": self.linker.version,
                    "counts": asdict(counts),
                    "metrics": metrics(counts),
                    "checks": checks,
                    "names": [asdict(c) for c in comparisons],
                },
            )
            return ManifestItem(
                podcast_slug=podcast.slug,
                episode_slug=episode.slug,
                external_id=episode.external_id,
                status="ok",
                report_file=f"{ITEMS_DIRNAME}/{filename}",
                scores=derived_scores(counts),
                checks_ok=checks["ok"],
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

    def _judge(
        self, rubric: Rubric, judge: JudgeResolution, disputed: List[NameComparison], context: LinkContext
    ) -> None:
        """Fill in ``verdict`` on each disputed name. A name the judge leaves
        out stays without one and counts towards no metric."""
        for start in range(0, len(disputed), JUDGE_BATCH_SIZE):
            by_id = {f"n{i + 1}": c for i, c in enumerate(disputed[start : start + JUDGE_BATCH_SIZE])}
            messages = [
                {"role": "system", "content": rubric.system_prompt},
                {"role": "user", "content": render_judge_message(by_id, context)},
            ]
            # Native structured output where the provider has it: in JSON
            # mode Gemini once answered with two JSON objects and the whole
            # episode failed. The FM-7 chat path stays for providers without.
            if judge.provider.supports_structured_output():
                temperature = judge.info.temperature if judge.provider.supports_temperature() else None
                report = judge.provider.generate_structured(
                    messages=messages, response_model=LinkingJudgeReport, temperature=temperature
                )
            else:
                report = LinkingJudgeReport.model_validate(self._judge_once(rubric, judge, messages))
            for verdict in report.verdicts:
                comparison = by_id.get(verdict.id)
                if comparison is not None and comparison.verdict is None:
                    comparison.verdict = verdict_for(comparison, verdict.correct)
                    comparison.judge_reason = verdict.reason[:200]

    @staticmethod
    def _checks(comparisons: List[NameComparison], outcome: LinkOutcome, repo) -> dict:
        """Exact checks a judge would grade noisily."""
        linked = [c for c in comparisons if c.live.qid]
        blacklisted = [c.surface_form for c in linked if repo.is_blacklisted(c.surface_form, c.live.qid)]
        unanswered = sum(1 for c in comparisons if c.outcome == UNANSWERED)
        return {
            # The validator discards these, so above zero means the model is
            # inventing identifiers and the chooser prompt needs attention.
            "rejected_not_offered": outcome.rejected_not_offered,
            "blacklisted_links": blacklisted,
            "unanswered_names": unanswered,
            "linker_unreachable": outcome.unreachable,
            "ok": not blacklisted and not outcome.unreachable,
        }
