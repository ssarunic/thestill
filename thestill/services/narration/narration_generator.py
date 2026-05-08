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

Phase 1 ships:
  - Quote selection with deterministic scoring and speaker resolution.
  - Skeleton JSON script: chrome blocks + verbatim quote cues.

Subsequent phases layer on top without disturbing this skeleton:
  - Phase 2: theme clustering + anchor-prose script generation, markdown
    renderer, validation contract, fallback.
  - Phase 3: ``thestill narrate`` CLI + ``/api/narrations`` endpoints.
  - Phase 4: frontend reader + length switcher.
"""

import json
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path
from typing import List, Optional, Tuple

from structlog import get_logger

from ...core.facts_manager import FactsManager
from ...models.podcast import Episode, Podcast
from ...utils.path_manager import PathManager
from .models import (
    NarrationContent,
    NarrationStats,
    QuoteCandidate,
    ScriptBlock,
    word_count,
)
from .quote_selector import QuoteSelector, QuoteSelectorConfig
from .transcript_loader import TranscriptTurnLoader

logger = get_logger(__name__)


DEFAULT_WPM = 150.0
DEFAULT_MAX_QUOTE_SHARE = 0.40
DEFAULT_TARGET_DURATION_SECONDS = 300
DEFAULT_BOUNDARY_TRIM_FRACTION = 0.05


@dataclass
class NarrationConfig:
    """Run-level configuration. Defaults match spec #33 §"Time Budget Model"."""

    target_duration_seconds: int = DEFAULT_TARGET_DURATION_SECONDS
    wpm: float = DEFAULT_WPM
    max_quote_share: float = DEFAULT_MAX_QUOTE_SHARE
    boundary_trim_fraction: float = DEFAULT_BOUNDARY_TRIM_FRACTION
    slug: str = "morning"


class NarrationGenerator:
    """Generate a narrated-digest skeleton from a list of selected episodes."""

    def __init__(
        self,
        path_manager: PathManager,
        facts_manager: Optional[FactsManager] = None,
        loader: Optional[TranscriptTurnLoader] = None,
        selector: Optional[QuoteSelector] = None,
    ):
        self.path_manager = path_manager
        self.facts_manager = facts_manager or FactsManager(path_manager)
        self.loader = loader or TranscriptTurnLoader(path_manager, self.facts_manager)
        self.selector = selector or QuoteSelector()

    def generate(
        self,
        episodes: List[Tuple[Podcast, Episode]],
        config: Optional[NarrationConfig] = None,
    ) -> NarrationContent:
        """Build a Phase-1 narration from ``episodes`` (in selection order).

        Each episode is offered to the quote selector independently with
        its own facts-derived keywords and sponsor list. Episodes that
        yield no quote (no sidecar, no resolvable speakers, every turn
        filtered) are routed to the rapid-fire tail.
        """
        cfg = config or NarrationConfig()
        per_episode_picks: List[Tuple[Podcast, Episode, List[QuoteCandidate]]] = []

        next_quote_id = 1
        for podcast, episode in episodes:
            picked = self._select_quotes_for_episode(
                podcast, episode, cfg, starting_id=next_quote_id
            )
            next_quote_id += len(picked)
            per_episode_picks.append((podcast, episode, picked))

        kept_ids = self._enforce_quote_share_cap(
            [q for _, _, picked in per_episode_picks for q in picked],
            cfg.target_duration_seconds,
            cfg.max_quote_share,
        )
        per_episode_picks = [
            (podcast, episode, [q for q in picked if q.quote_id in kept_ids])
            for podcast, episode, picked in per_episode_picks
        ]

        all_quotes = [q for _, _, picked in per_episode_picks for q in picked]
        episode_ids_covered = [
            episode.id for _, episode, picked in per_episode_picks if picked
        ]
        episode_ids_in_tail = [
            episode.id for _, episode, picked in per_episode_picks if not picked
        ]

        blocks = self._render_skeleton_blocks(per_episode_picks, cfg)
        narration_words = sum(
            word_count(b.text) for b in blocks if b.kind == "narration" and b.text
        )
        quote_seconds = sum(q.duration_seconds for q in all_quotes)
        narration_seconds = (
            (narration_words / cfg.wpm) * 60.0 if cfg.wpm else 0.0
        )

        stats = NarrationStats(
            target_duration_seconds=cfg.target_duration_seconds,
            actual_duration_seconds=narration_seconds + quote_seconds,
            narration_words=narration_words,
            quote_seconds=quote_seconds,
            episodes_covered=len(episode_ids_covered),
            episodes_in_tail=len(episode_ids_in_tail),
            quote_count=len(all_quotes),
        )

        logger.info(
            "narration generated (phase 1 skeleton)",
            episodes_total=len(per_episode_picks),
            episodes_covered=stats.episodes_covered,
            episodes_in_tail=stats.episodes_in_tail,
            quote_count=stats.quote_count,
            target_seconds=stats.target_duration_seconds,
            actual_seconds=round(stats.actual_duration_seconds, 1),
        )

        return NarrationContent(
            blocks=blocks,
            quotes=all_quotes,
            stats=stats,
            episode_ids_covered=episode_ids_covered,
            episode_ids_in_tail=episode_ids_in_tail,
        )

    def write_json_script(
        self,
        content: NarrationContent,
        config: Optional[NarrationConfig] = None,
    ) -> Path:
        """Write the JSON-script artefact under ``data/narrations/``.

        Filename pattern: ``YYYY-MM-DD-<slug>.json`` (UTC date). Slug
        defaults to ``morning`` and is overridable via
        ``NarrationConfig.slug``.
        """
        cfg = config or NarrationConfig()
        narrations_dir = self.path_manager.narrations_dir()
        narrations_dir.mkdir(parents=True, exist_ok=True)
        date_str = content.generated_at.astimezone(timezone.utc).strftime("%Y-%m-%d")
        path = narrations_dir / f"{date_str}-{cfg.slug}.json"
        payload = {
            "generated_at": content.generated_at.astimezone(timezone.utc).isoformat(),
            "target_duration_seconds": content.stats.target_duration_seconds,
            "actual_duration_seconds": round(content.stats.actual_duration_seconds, 2),
            "wpm": cfg.wpm,
            "schema_version": "phase1",
            "blocks": [
                self._block_to_dict(b, content.quotes) for b in content.blocks
            ],
            "episodes_covered": list(content.episode_ids_covered),
            "episodes_in_tail": list(content.episode_ids_in_tail),
        }
        path.write_text(
            json.dumps(payload, indent=2, ensure_ascii=False), encoding="utf-8"
        )
        content.json_script_path = path
        logger.info(
            "narration json script written",
            path=str(path),
            quote_count=len(content.quotes),
        )
        return path

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
            tuple(episode_facts.topics_keywords)
            if episode_facts and episode_facts.topics_keywords
            else ()
        )
        sponsors: Tuple[str, ...] = (
            tuple(episode_facts.ad_sponsors)
            if episode_facts and episode_facts.ad_sponsors
            else ()
        )
        selector_cfg = QuoteSelectorConfig(
            keywords=keywords,
            sponsors=sponsors,
            episode_duration_seconds=float(episode.duration or 0),
            boundary_trim_fraction=cfg.boundary_trim_fraction,
            wpm=cfg.wpm,
        )
        return self.selector.select(turns, selector_cfg, starting_id=starting_id)

    @staticmethod
    def _enforce_quote_share_cap(
        quotes: List[QuoteCandidate],
        target_duration_seconds: int,
        max_quote_share: float,
    ) -> set:
        """Return the set of ``quote_id`` values that fit under the share cap.

        Greedy: rank by score descending, accept while we're under the
        cap, drop otherwise — the spec #33 §"Word-Budget" rule
        ("lowest-scoring quotes are dropped first") restated as a
        forward pack.
        """
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

    def _render_skeleton_blocks(
        self,
        per_episode_picks: List[Tuple[Podcast, Episode, List[QuoteCandidate]]],
        cfg: NarrationConfig,
    ) -> List[ScriptBlock]:
        """Phase 1 chrome: opener, per-episode segment, signoff.

        Phase 2 replaces every narration block here with anchor-voiced
        prose generated by the script-generation LLM call. Quote blocks
        already carry the durable identifier triple
        (``episode_id`` + ``start_seconds`` + ``duration_seconds``) so
        the downstream TTS swap can splice in original audio without
        further schema work.
        """
        date_label = datetime.now(timezone.utc).strftime("%B %d, %Y")
        blocks: List[ScriptBlock] = [
            self._narration_block(
                "opener",
                f"Briefing skeleton for {date_label}. "
                "Anchor-voiced prose lands in Phase 2.",
                cfg.wpm,
            )
        ]
        seg_counter = 0
        for podcast, episode, picked in per_episode_picks:
            if not picked:
                continue
            seg_counter += 1
            section = f"segment-{seg_counter}"
            blocks.append(
                self._narration_block(
                    section,
                    f"From {podcast.title}: {episode.title}.",
                    cfg.wpm,
                )
            )
            for q in picked:
                blocks.append(
                    ScriptBlock(
                        kind="quote",
                        section=section,
                        quote_id=q.quote_id,
                        duration_seconds=q.duration_seconds,
                    )
                )

        tail_entries = [
            f"{podcast.title}: {episode.title}"
            for podcast, episode, picked in per_episode_picks
            if not picked
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
                "signoff", "That's the skeleton briefing.", cfg.wpm,
            )
        )
        return blocks

    @staticmethod
    def _narration_block(section: str, text: str, wpm: float) -> ScriptBlock:
        duration = (word_count(text) / wpm) * 60.0 if wpm else 0.0
        return ScriptBlock(
            kind="narration", section=section, text=text, duration_seconds=duration,
        )

    @staticmethod
    def _block_to_dict(block: ScriptBlock, quotes: List[QuoteCandidate]) -> dict:
        if block.kind == "narration":
            return {
                "kind": "narration",
                "section": block.section,
                "text": block.text or "",
                "duration_seconds": round(block.duration_seconds, 2),
            }
        # Denormalise the quote: TTS will read this JSON in isolation,
        # so the block needs to be self-describing.
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
