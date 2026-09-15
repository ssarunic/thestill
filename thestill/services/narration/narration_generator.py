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

"""Top-level orchestrator for narrated-digest generation (spec #33).

The generator chains the fixed pipeline:

  episodes
   └─► quote selection (deterministic, spec §"Quote Selection")
       ├─► theme clustering (LLM #1, spec §"Pipeline Stage 1")
       └─► script generation (LLM #2, spec §"Pipeline Stage 4")
            ├─► validation contract (placeholder ids, no-verbatim-leak,
            │   word-budget tolerance) — regenerate once on failure
            └─► markdown read-through + JSON script

When the LLM stages are unavailable (no provider configured) or when
script-generation fails twice, the generator falls back to a Phase 1
skeleton script so callers always get a usable artefact. When the
fallback fires after a script-generation failure, the markdown is the
existing link-index digest with a "narration unavailable" banner so
users keep their morning briefing on a degraded day.
"""

import json
from dataclasses import dataclass, field
from datetime import datetime, timezone
from pathlib import Path
from typing import TYPE_CHECKING, Dict, List, Optional, Sequence, Tuple

from structlog import get_logger

from ...core.facts_manager import FactsManager
from ...core.llm_provider import LLMProvider
from ...models.podcast import Episode, Podcast
from ...utils.path_manager import PathManager, _validate_slug
from ...utils.text_sanitizer import sanitize_text
from ...utils.url_generator import UrlGenerator
from ..briefing_script_generator import BriefingScriptGenerator, extract_gist, extract_summary_sections

if TYPE_CHECKING:
    from ...utils.file_storage import FileStorage
from ..narration_prompts import load_default_anchor_prompt
from .markdown_renderer import NarrationMarkdownRenderer
from .models import (
    EpisodeBrief,
    NarrationContent,
    NarrationStats,
    QuoteCandidate,
    ScriptBlock,
    Segment,
    ThemePlan,
    ValidationFailure,
    word_count,
)
from .quote_selector import QuoteSelector, QuoteSelectorConfig
from .script_writer import ScriptWriter
from .theme_clusterer import ThemeClusterer
from .transcript_loader import TranscriptTurnLoader

logger = get_logger(__name__)


DEFAULT_WPM = 150.0
DEFAULT_MAX_QUOTE_SHARE = 0.40
DEFAULT_TARGET_DURATION_SECONDS = 300
DEFAULT_BOUNDARY_TRIM_FRACTION = 0.05
DEFAULT_TAIL_SHARE = 0.15
DEFAULT_OPENER_SHARE = 0.05
DEFAULT_SIGNOFF_SHARE = 0.03
# Spec #77 §2 — per-episode cap on the summary material fed to the writer.
DEFAULT_MATERIAL_MAX_WORDS = 400
# Spec #77 §4 — fraction of the word budget the writer is told (see
# script_writer.DEFAULT_STATED_TARGET_RATIO for the reasoning).
DEFAULT_STATED_TARGET_RATIO = 0.8


@dataclass
class NarrationConfig:
    """Run-level configuration. Defaults match spec #33 §"Time Budget Model"."""

    target_duration_seconds: int = DEFAULT_TARGET_DURATION_SECONDS
    wpm: float = DEFAULT_WPM
    max_quote_share: float = DEFAULT_MAX_QUOTE_SHARE
    boundary_trim_fraction: float = DEFAULT_BOUNDARY_TRIM_FRACTION
    tail_share: float = DEFAULT_TAIL_SHARE
    opener_share: float = DEFAULT_OPENER_SHARE
    signoff_share: float = DEFAULT_SIGNOFF_SHARE
    slug: str = "morning"
    # Overrides the default ``YYYY-MM-DD-<slug>`` basename used for the
    # JSON/Markdown artefacts. The runner sets this to ``<briefing_id>-<slug>``
    # so the briefing record is the durable join key for narrations.
    basename: Optional[str] = None
    # Persisted in the JSON header so consumers (dashboard tile, future
    # TTS) read the join key explicitly instead of parsing the filename
    # — slugs may contain ``-`` (e.g. ``custom-450s``) so the filename
    # alone is ambiguous.
    briefing_id: Optional[str] = None

    def __post_init__(self) -> None:
        # Defence-in-depth: the slug ends up in a filename so a value
        # containing path separators would let a caller write outside
        # ``data/narrations``. The API validates at the boundary; this
        # catches CLI / programmatic callers too.
        _validate_slug(self.slug, name="slug")

    def file_basename(self, generated_at) -> str:
        if self.basename:
            return self.basename
        date_str = generated_at.astimezone(timezone.utc).strftime("%Y-%m-%d")
        return f"{date_str}-{self.slug}"


@dataclass
class _PerEpisodeBucket:
    podcast: Podcast
    episode: Episode
    picked: List[QuoteCandidate]
    brief: EpisodeBrief
    has_sidecar: bool = False


@dataclass
class _Pipeline:
    """Mutable scratch state passed between the orchestrator's stages."""

    episodes: Sequence[Tuple[Podcast, Episode]]
    cfg: NarrationConfig
    buckets: List[_PerEpisodeBucket] = field(default_factory=list)
    plan: ThemePlan = field(default_factory=lambda: ThemePlan(segments=(), tail_ids=()))
    fallback_reason: Optional[str] = None
    # Human-readable detail behind ``fallback_reason`` (e.g. the word
    # counts of a budget overrun) so the fallback is diagnosable from
    # the log line alone (spec #77 §6).
    fallback_detail: Optional[str] = None
    # Filled by the script stage on success (spec #77 §3/§4 stats).
    noise_phrase_hits: int = 0
    stated_word_target: int = 0
    reaction_count: int = 0
    reactions_missing: int = 0

    @property
    def quote_pool_size(self) -> int:
        return sum(len(b.picked) for b in self.buckets)

    @property
    def episodes_with_sidecar(self) -> int:
        return sum(1 for b in self.buckets if b.has_sidecar)


class NarrationGenerator:
    """Generate a narrated-digest from a list of selected episodes.

    ``llm_provider`` is optional; when ``None`` the generator skips
    Phase 2 LLM stages and returns the Phase 1 skeleton (spec
    §"Migration Strategy" allows narration to be opt-in until fallback
    rates are measured in production).
    """

    def __init__(
        self,
        path_manager: PathManager,
        file_storage: "FileStorage",
        facts_manager: Optional[FactsManager] = None,
        loader: Optional[TranscriptTurnLoader] = None,
        selector: Optional[QuoteSelector] = None,
        llm_provider: Optional[LLMProvider] = None,
        clusterer: Optional[ThemeClusterer] = None,
        script_writer: Optional[ScriptWriter] = None,
        markdown_renderer: Optional[NarrationMarkdownRenderer] = None,
        script_generator: Optional[BriefingScriptGenerator] = None,
        url_generator: Optional[UrlGenerator] = None,
        anchor_prompt: Optional[str] = None,
        material_max_words: int = DEFAULT_MATERIAL_MAX_WORDS,
        stated_target_ratio: float = DEFAULT_STATED_TARGET_RATIO,
    ):
        self.path_manager = path_manager
        self.file_storage = file_storage
        self.facts_manager = facts_manager or FactsManager(path_manager)
        self.loader = loader or TranscriptTurnLoader(path_manager, self.facts_manager)
        self.selector = selector or QuoteSelector()
        self.url_generator = url_generator or UrlGenerator()
        self.markdown_renderer = markdown_renderer or NarrationMarkdownRenderer(url_generator=self.url_generator)
        # Spec #35 — `file_storage` is required so callers can't accidentally
        # default to local when ``STORAGE_BACKEND=s3``. The earlier fallback
        # let production paths read summaries from the wrong backend
        # (reviewer P2). The provided storage is also threaded into the
        # auto-constructed BriefingScriptGenerator.
        self.script_generator = script_generator or BriefingScriptGenerator(
            path_manager, file_storage, url_generator=self.url_generator
        )
        self.llm_provider = llm_provider
        self._anchor_prompt = anchor_prompt
        self._material_max_words = material_max_words
        self._stated_target_ratio = stated_target_ratio
        self.clusterer = clusterer
        self.script_writer = script_writer

    def generate(
        self,
        episodes: List[Tuple[Podcast, Episode]],
        config: Optional[NarrationConfig] = None,
    ) -> NarrationContent:
        """Run the full Phase 2 pipeline (or fall back when LLM is absent)."""
        cfg = config or NarrationConfig()
        pipeline = _Pipeline(episodes=episodes, cfg=cfg)
        self._stage_quote_selection(pipeline)

        if not self._llm_available():
            logger.info(
                "narration: no LLM provider configured; emitting phase-1 skeleton",
                episodes_total=len(pipeline.buckets),
            )
            return self._build_skeleton_narration(pipeline)

        self._stage_theme_clustering(pipeline)
        if pipeline.fallback_reason is not None:
            return self._build_fallback_narration(pipeline)

        narration_word_budget = self._narration_word_budget(pipeline)
        script = self._stage_script_generation(pipeline, narration_word_budget)
        if not script:
            return self._build_fallback_narration(pipeline)
        return self._build_narrated(pipeline, script)

    def write_json_script(
        self,
        content: NarrationContent,
        config: Optional[NarrationConfig] = None,
    ) -> Path:
        """Write the JSON-script artefact under ``data/narrations/``.

        Filename: ``<basename>.json``. The runner sets ``basename`` to
        ``<briefing_id>-<slug>`` so the briefing record is the join key.
        Standalone callers fall back to ``YYYY-MM-DD-<slug>``.
        """
        cfg = config or NarrationConfig()
        narrations_dir = self.path_manager.narrations_dir()
        path = self.path_manager._assert_inside_root(narrations_dir / f"{cfg.file_basename(content.generated_at)}.json")
        payload = {
            "generated_at": content.generated_at.astimezone(timezone.utc).isoformat(),
            "target_duration_seconds": content.stats.target_duration_seconds,
            "actual_duration_seconds": round(content.stats.actual_duration_seconds, 2),
            "wpm": cfg.wpm,
            "schema_version": "phase2",
            "mode": content.mode,
            "fallback_reason": content.stats.fallback_reason,
            "latency_ms": content.latency_ms,
            "briefing_id": cfg.briefing_id,
            "slug": cfg.slug,
            "blocks": [self._block_to_dict(b, content.quotes) for b in content.blocks],
            "episodes_covered": list(content.episode_ids_covered),
            "episodes_in_tail": list(content.episode_ids_in_tail),
            # Spec #77 §6 — additive diagnostics; consumers read the
            # header with ``.get`` so the schema version stays "phase2".
            "quote_pool_size": content.stats.quote_pool_size,
            "episodes_with_sidecar": content.stats.episodes_with_sidecar,
            "narration_words": content.stats.narration_words,
            "stated_word_target": content.stats.stated_word_target,
            "noise_phrase_hits": content.stats.noise_phrase_hits,
            "reaction_count": content.stats.reaction_count,
            "reactions_missing": content.stats.reactions_missing,
        }
        # Spec #35 — go through FileStorage so artefacts land on the
        # configured backend (was Path.write_text, missing S3 entirely).
        self.file_storage.write_text(
            self.path_manager.to_relative(path),
            json.dumps(payload, indent=2, ensure_ascii=False),
        )
        content.json_script_path = path
        logger.info(
            "narration json script written",
            path=str(path),
            mode=content.mode,
            quote_count=len(content.quotes),
        )
        return path

    def write_markdown(
        self,
        content: NarrationContent,
        config: Optional[NarrationConfig] = None,
    ) -> Optional[Path]:
        """Write the markdown read-through (or fallback link index) to disk."""
        if not content.markdown:
            return None
        cfg = config or NarrationConfig()
        narrations_dir = self.path_manager.narrations_dir()
        path = self.path_manager._assert_inside_root(narrations_dir / f"{cfg.file_basename(content.generated_at)}.md")
        # Spec #35 — route through FileStorage so the markdown lands on the
        # configured backend.
        self.file_storage.write_text(self.path_manager.to_relative(path), content.markdown)
        content.markdown_path = path
        logger.info(
            "narration markdown written",
            path=str(path),
            mode=content.mode,
        )
        return path

    # --- Pipeline stages -------------------------------------------------

    def _stage_quote_selection(self, pipeline: _Pipeline) -> None:
        next_quote_id = 1
        for podcast, episode in pipeline.episodes:
            picked = self._select_quotes_for_episode(podcast, episode, pipeline.cfg, starting_id=next_quote_id)
            next_quote_id += len(picked)
            pipeline.buckets.append(
                _PerEpisodeBucket(
                    podcast=podcast,
                    episode=episode,
                    picked=picked,
                    brief=self._build_episode_brief(podcast, episode),
                    has_sidecar=self.loader.sidecar_exists(podcast, episode),
                )
            )
        kept_ids = self._enforce_quote_share_cap(
            [q for b in pipeline.buckets for q in b.picked],
            pipeline.cfg.target_duration_seconds,
            pipeline.cfg.max_quote_share,
        )
        for bucket in pipeline.buckets:
            bucket.picked = [q for q in bucket.picked if q.quote_id in kept_ids]
        if pipeline.episodes_with_sidecar and not pipeline.quote_pool_size:
            # Transcripts were there to quote from and nothing survived
            # selection. Spec #77 §6: this must never be silent — it is
            # the exact shape of the loader drift that produced quote-less
            # briefings for months.
            logger.warning(
                "narration.quote_pool_empty",
                episodes_total=len(pipeline.buckets),
                episodes_with_sidecar=pipeline.episodes_with_sidecar,
            )

    def _stage_theme_clustering(self, pipeline: _Pipeline) -> None:
        clusterer = self._theme_clusterer()
        if clusterer is None:
            pipeline.fallback_reason = "llm_provider_missing"
            return
        plan = clusterer.cluster(
            briefs=[b.brief for b in pipeline.buckets],
            target_duration_seconds=pipeline.cfg.target_duration_seconds,
        )
        pipeline.plan = plan

    def _stage_script_generation(
        self, pipeline: _Pipeline, narration_word_budget: int
    ) -> Optional[Sequence[ScriptBlock]]:
        writer = self._script_writer()
        if writer is None:
            pipeline.fallback_reason = "llm_provider_missing"
            return None
        briefs_by_id = {b.brief.episode_id: b.brief for b in pipeline.buckets}
        quotes = [q for b in pipeline.buckets for q in b.picked]
        result = writer.write(
            plan=pipeline.plan,
            briefs_by_id=briefs_by_id,
            quotes=quotes,
            narration_word_budget=narration_word_budget,
        )
        if not result.blocks:
            pipeline.fallback_reason = self._summarise_failures(result.failures)
            pipeline.fallback_detail = "; ".join(f.detail for f in result.failures) or None
            return None
        pipeline.noise_phrase_hits = result.noise_phrase_hits
        pipeline.stated_word_target = result.stated_word_target
        pipeline.reaction_count = result.reaction_count
        pipeline.reactions_missing = result.reactions_missing
        return result.blocks

    # --- Outputs --------------------------------------------------------

    def _build_narrated(self, pipeline: _Pipeline, blocks: Sequence[ScriptBlock]) -> NarrationContent:
        all_quotes = [q for b in pipeline.buckets for q in b.picked]
        episode_ids_covered, episode_ids_in_tail = self._covered_and_tail(pipeline.plan, pipeline)
        stats = self._build_stats(
            pipeline,
            blocks,
            episode_ids_covered,
            episode_ids_in_tail,
        )

        content = NarrationContent(
            blocks=list(blocks),
            quotes=all_quotes,
            stats=stats,
            episode_ids_covered=episode_ids_covered,
            episode_ids_in_tail=episode_ids_in_tail,
            mode="narrated",
        )
        content.markdown = self.markdown_renderer.render(
            blocks=content.blocks,
            quotes=content.quotes,
            plan=pipeline.plan,
            episodes=pipeline.episodes,
            stats=content.stats,
            generated_at=content.generated_at,
        )

        logger.info(
            "narration generated",
            mode="narrated",
            episodes_total=len(pipeline.buckets),
            episodes_covered=stats.episodes_covered,
            episodes_in_tail=stats.episodes_in_tail,
            quote_count=stats.quote_count,
            quote_pool_size=stats.quote_pool_size,
            episodes_with_sidecar=stats.episodes_with_sidecar,
            narration_words=stats.narration_words,
            stated_word_target=stats.stated_word_target,
            noise_phrase_hits=stats.noise_phrase_hits,
            reaction_count=stats.reaction_count,
            reactions_missing=stats.reactions_missing,
            target_seconds=stats.target_duration_seconds,
            actual_seconds=round(stats.actual_duration_seconds, 1),
        )
        return content

    def _build_skeleton_narration(self, pipeline: _Pipeline) -> NarrationContent:
        all_quotes = [q for b in pipeline.buckets for q in b.picked]
        episode_ids_covered = [b.episode.id for b in pipeline.buckets if b.picked]
        episode_ids_in_tail = [b.episode.id for b in pipeline.buckets if not b.picked]
        blocks = self._render_skeleton_blocks(pipeline)
        stats = self._build_stats(
            pipeline,
            blocks,
            episode_ids_covered,
            episode_ids_in_tail,
        )
        return NarrationContent(
            blocks=blocks,
            quotes=all_quotes,
            stats=stats,
            episode_ids_covered=episode_ids_covered,
            episode_ids_in_tail=episode_ids_in_tail,
            mode="narrated",
        )

    def _build_fallback_narration(self, pipeline: _Pipeline) -> NarrationContent:
        all_quotes = [q for b in pipeline.buckets for q in b.picked]
        episode_ids_in_tail = [b.episode.id for b in pipeline.buckets]
        digest_content = self.script_generator.generate([(b.podcast, b.episode) for b in pipeline.buckets])
        markdown = (
            "> _Today's narration is unavailable; here is the link-index briefing"
            " instead._\n\n" + digest_content.markdown
        )
        stats = self._build_stats(
            pipeline,
            blocks=(),
            episode_ids_covered=[],
            episode_ids_in_tail=episode_ids_in_tail,
            fallback_reason=pipeline.fallback_reason or "unknown",
        )
        logger.warning(
            "narration.fallback",
            reason=stats.fallback_reason,
            detail=pipeline.fallback_detail,
            episodes_total=len(pipeline.buckets),
            quote_count=stats.quote_count,
            quote_pool_size=stats.quote_pool_size,
            episodes_with_sidecar=stats.episodes_with_sidecar,
        )
        return NarrationContent(
            blocks=[],
            quotes=all_quotes,
            stats=stats,
            episode_ids_covered=[],
            episode_ids_in_tail=episode_ids_in_tail,
            mode="fallback",
            markdown=markdown,
        )

    # --- Helpers --------------------------------------------------------

    def _llm_available(self) -> bool:
        return self.llm_provider is not None or (self.clusterer is not None and self.script_writer is not None)

    def _theme_clusterer(self) -> Optional[ThemeClusterer]:
        if self.clusterer is not None:
            return self.clusterer
        if self.llm_provider is None:
            return None
        return ThemeClusterer(self.llm_provider)

    def _script_writer(self) -> Optional[ScriptWriter]:
        if self.script_writer is not None:
            return self.script_writer
        if self.llm_provider is None:
            return None
        prompt = self._anchor_prompt or load_default_anchor_prompt()
        return ScriptWriter(
            self.llm_provider,
            system_prompt=prompt,
            wpm=DEFAULT_WPM,
            stated_target_ratio=self._stated_target_ratio,
            material_max_words=self._material_max_words,
        )

    def _select_quotes_for_episode(
        self,
        podcast: Podcast,
        episode: Episode,
        cfg: NarrationConfig,
        starting_id: int,
    ) -> List[QuoteCandidate]:
        turns = self.loader.load(podcast, episode)
        episode_facts = self.loader.load_episode_facts(podcast, episode)
        keywords: Tuple[str, ...] = (
            tuple(episode_facts.topics_keywords) if episode_facts and episode_facts.topics_keywords else ()
        )
        sponsors: Tuple[str, ...] = (
            tuple(episode_facts.ad_sponsors) if episode_facts and episode_facts.ad_sponsors else ()
        )
        selector_cfg = QuoteSelectorConfig(
            keywords=keywords,
            sponsors=sponsors,
            episode_duration_seconds=float(episode.duration or 0),
            boundary_trim_fraction=cfg.boundary_trim_fraction,
            wpm=cfg.wpm,
        )
        return self.selector.select(turns, selector_cfg, starting_id=starting_id)

    def _build_episode_brief(self, podcast: Podcast, episode: Episode) -> EpisodeBrief:
        facts = self.loader.load_episode_facts(podcast, episode)
        summary = self._read_summary(episode)
        takeaways, drama = self._writer_material(summary, episode)
        return EpisodeBrief(
            episode_id=episode.id,
            podcast_title=podcast.title,
            episode_title=episode.title,
            guests=tuple(facts.guests) if facts and facts.guests else (),
            topics=tuple(facts.topics_keywords) if facts and facts.topics_keywords else (),
            sponsors=tuple(facts.ad_sponsors) if facts and facts.ad_sponsors else (),
            gist=extract_gist(summary) if summary else None,
            takeaways=takeaways,
            drama=drama,
        )

    def _writer_material(self, summary: Optional[str], episode: Episode) -> Tuple[Tuple[str, ...], Tuple[str, ...]]:
        """Takeaways and drama rounds for the writer (spec #77 Phase 2b).

        The summary is LLM output; the control-byte guard from spec #42
        applies whenever such text is fed back to a model.
        """
        if not summary:
            return (), ()
        sections = extract_summary_sections(summary)
        if sections is None:
            return (), ()
        removed_total = 0

        def clean_all(items: Tuple[str, ...]) -> Tuple[str, ...]:
            nonlocal removed_total
            out = []
            for item in items:
                clean, removed = sanitize_text(item)
                removed_total += removed
                if clean.strip():
                    out.append(clean.strip())
            return tuple(out)

        takeaways, drama = clean_all(sections.takeaways), clean_all(sections.drama)
        if removed_total:
            logger.warning(
                "narration.material_sanitized",
                episode_id=episode.id,
                removed_count=removed_total,
            )
        return takeaways, drama

    def _read_summary(self, episode: Episode) -> Optional[str]:
        if not episode.summary_path:
            return None
        path = self.path_manager.summary_file(episode.summary_path)
        # Spec #35 — go through FileStorage so summaries are found on the
        # configured backend. ``StorageError`` (a ``TransientError``)
        # propagates so the task worker's retry/DLQ layer can act; only
        # genuine missing-summary cases return ``None``.
        try:
            return self.file_storage.read_text(self.path_manager.to_relative(path))
        except FileNotFoundError:
            return None

    @staticmethod
    def _build_stats(
        pipeline: _Pipeline,
        blocks: Sequence[ScriptBlock],
        episode_ids_covered: Sequence[str],
        episode_ids_in_tail: Sequence[str],
        fallback_reason: Optional[str] = None,
    ) -> NarrationStats:
        cfg = pipeline.cfg
        # Derive stats from the emitted blocks rather than the selected
        # pool: the LLM may drop a quote (or repeat one) and the stats
        # have to match what's actually in the script for downstream
        # TTS budgeting / UI display.
        narration_words = sum(word_count(b.text) for b in blocks if b.kind in ("narration", "reaction") and b.text)
        quote_blocks = [b for b in blocks if b.kind == "quote"]
        quote_seconds = sum(b.duration_seconds for b in quote_blocks)
        narration_seconds = narration_words / cfg.wpm * 60.0 if cfg.wpm else 0.0
        return NarrationStats(
            target_duration_seconds=cfg.target_duration_seconds,
            actual_duration_seconds=narration_seconds + quote_seconds,
            narration_words=narration_words,
            quote_seconds=quote_seconds,
            episodes_covered=len(episode_ids_covered),
            episodes_in_tail=len(episode_ids_in_tail),
            quote_count=len(quote_blocks),
            fallback_reason=fallback_reason,
            quote_pool_size=pipeline.quote_pool_size,
            episodes_with_sidecar=pipeline.episodes_with_sidecar,
            noise_phrase_hits=pipeline.noise_phrase_hits,
            stated_word_target=pipeline.stated_word_target,
            reaction_count=pipeline.reaction_count,
            reactions_missing=pipeline.reactions_missing,
        )

    @staticmethod
    def _enforce_quote_share_cap(
        quotes: List[QuoteCandidate],
        target_duration_seconds: int,
        max_quote_share: float,
    ) -> set:
        cap = target_duration_seconds * max_quote_share
        if not quotes or cap <= 0:
            return {q.quote_id for q in quotes}
        kept: set = set()
        used = 0.0
        for q in sorted(quotes, key=lambda q: (-q.score, q.quote_id)):
            if used + q.duration_seconds <= cap:
                kept.add(q.quote_id)
                used += q.duration_seconds
        return kept

    @staticmethod
    def _summarise_failures(failures: Sequence[ValidationFailure]) -> str:
        if not failures:
            return "unknown"
        return ",".join(f.reason for f in failures)

    @staticmethod
    def _covered_and_tail(plan: ThemePlan, pipeline: _Pipeline) -> Tuple[List[str], List[str]]:
        covered: List[str] = []
        seen: set = set()
        for seg in plan.segments:
            for eid in seg.episode_ids:
                if eid not in seen:
                    seen.add(eid)
                    covered.append(eid)
        tail = [eid for eid in plan.tail_ids if eid not in seen]
        # Anything still missing (model dropped an episode) joins the tail.
        for bucket in pipeline.buckets:
            if bucket.episode.id not in seen and bucket.episode.id not in tail:
                tail.append(bucket.episode.id)
        return covered, tail

    def _narration_word_budget(self, pipeline: _Pipeline) -> int:
        cfg = pipeline.cfg
        quote_seconds = sum(q.duration_seconds for b in pipeline.buckets for q in b.picked)
        narration_seconds = max(0.0, cfg.target_duration_seconds - quote_seconds)
        return max(1, int(narration_seconds * cfg.wpm / 60.0))

    def _render_skeleton_blocks(self, pipeline: _Pipeline) -> List[ScriptBlock]:
        cfg = pipeline.cfg
        date_label = datetime.now(timezone.utc).strftime("%B %d, %Y")
        blocks: List[ScriptBlock] = [
            self._narration_block(
                "opener",
                f"Briefing skeleton for {date_label}.",
                cfg.wpm,
            )
        ]
        seg_counter = 0
        for bucket in pipeline.buckets:
            if not bucket.picked:
                continue
            seg_counter += 1
            section = f"segment-{seg_counter}"
            blocks.append(
                self._narration_block(
                    section,
                    f"From {bucket.podcast.title}: {bucket.episode.title}.",
                    cfg.wpm,
                )
            )
            for q in bucket.picked:
                blocks.append(
                    ScriptBlock(
                        kind="quote",
                        section=section,
                        quote_id=q.quote_id,
                        duration_seconds=q.duration_seconds,
                    )
                )

        tail_entries = [
            f"{bucket.podcast.title}: {bucket.episode.title}" for bucket in pipeline.buckets if not bucket.picked
        ]
        if tail_entries:
            blocks.append(
                self._narration_block(
                    "tail",
                    "Also today: " + "; ".join(tail_entries) + ".",
                    cfg.wpm,
                )
            )
        blocks.append(
            self._narration_block(
                "signoff",
                "That's the skeleton briefing.",
                cfg.wpm,
            )
        )
        return blocks

    @staticmethod
    def _narration_block(section: str, text: str, wpm: float) -> ScriptBlock:
        duration = (word_count(text) / wpm) * 60.0 if wpm else 0.0
        return ScriptBlock(
            kind="narration",
            section=section,
            text=text,
            duration_seconds=duration,
        )

    @staticmethod
    def _block_to_dict(block: ScriptBlock, quotes: List[QuoteCandidate]) -> dict:
        if block.kind in ("narration", "reaction"):
            return {
                "kind": block.kind,
                "section": block.section,
                "text": block.text or "",
                "duration_seconds": round(block.duration_seconds, 2),
            }
        quote = next((q for q in quotes if q.quote_id == block.quote_id), None)
        if quote is None:
            return {
                "kind": "quote",
                "section": block.section,
                "quote_id": block.quote_id,
            }
        return {
            "kind": "quote",
            "section": block.section,
            "quote_id": quote.quote_id,
            "episode_id": quote.episode_id,
            "podcast_title": quote.podcast_title,
            "speaker": quote.speaker,
            "speaker_role": quote.speaker_role,
            "text": quote.text,
            "start_seconds": round(quote.start_seconds, 2),
            "duration_seconds": round(quote.duration_seconds, 2),
            "score": round(quote.score, 4),
        }
