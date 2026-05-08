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

"""Markdown read-through renderer for narrated digests (spec #33 §"Output Format").

Walks the validated script blocks and the verbatim quote pool, emits a
human-readable Markdown read-through with quotes as block-quoted spans
attributed to ``— Speaker, Podcast Title (HH:MM)``. Each quote carries
a deep-linked ``▶ Listen at HH:MM`` link onto the episode page (spec
#23's timestamp URLs).
"""

from datetime import datetime, timezone
from typing import Dict, Iterable, List, Sequence, Tuple

from ...models.podcast import Episode, Podcast
from ...utils.url_generator import UrlGenerator
from .models import (
    NarrationStats,
    QuoteCandidate,
    ScriptBlock,
    Segment,
    ThemePlan,
)


def _format_clock(seconds: float) -> str:
    """Format seconds as ``MM:SS`` or ``HH:MM:SS`` for inline display."""
    total = max(0, int(seconds))
    hours = total // 3600
    minutes = (total % 3600) // 60
    secs = total % 60
    if hours > 0:
        return f"{hours:02d}:{minutes:02d}:{secs:02d}"
    return f"{minutes:02d}:{secs:02d}"


def _format_runtime_label(target_seconds: int, actual_seconds: float) -> str:
    """Build the ``Target: 5 minutes · Actual: 4m 52s`` byline."""
    target_min = max(1, round(target_seconds / 60))
    actual_int = int(round(actual_seconds))
    if actual_int < 60:
        actual_label = f"{actual_int}s"
    else:
        m, s = divmod(actual_int, 60)
        actual_label = f"{m}m {s:02d}s"
    return f"Target: {target_min} minutes · Actual: {actual_label}"


class NarrationMarkdownRenderer:
    """Render the read-through Markdown for a Phase 2 narration."""

    def __init__(self, url_generator: UrlGenerator | None = None):
        self.url_generator = url_generator or UrlGenerator()

    def render(
        self,
        *,
        blocks: Sequence[ScriptBlock],
        quotes: Sequence[QuoteCandidate],
        plan: ThemePlan,
        episodes: Sequence[Tuple[Podcast, Episode]],
        stats: NarrationStats,
        generated_at: datetime,
    ) -> str:
        """Compose the full Markdown document.

        ``episodes`` is the original (Podcast, Episode) selection in
        whatever order the caller wants the link-index to follow. The
        renderer picks slugs from these tuples to build deep links.
        """
        episode_lookup: Dict[str, Tuple[Podcast, Episode]] = {
            ep.id: (pod, ep) for pod, ep in episodes
        }
        quote_lookup: Dict[str, QuoteCandidate] = {q.quote_id: q for q in quotes}
        section_titles = self._segment_section_titles(plan)

        date_label = generated_at.astimezone(timezone.utc).strftime("%B %d, %Y")
        episodes_label = self._episodes_label(stats)
        runtime_label = _format_runtime_label(
            stats.target_duration_seconds, stats.actual_duration_seconds
        )

        lines: List[str] = [
            f"# Morning Briefing — {date_label}",
            f"*{runtime_label} · {episodes_label}*",
            "",
            "---",
            "",
        ]

        current_section = ""
        for block in blocks:
            if block.section != current_section:
                heading = self._heading_for_section(block.section, section_titles)
                if heading:
                    lines.append(heading)
                    lines.append("")
                current_section = block.section
            self._render_block(block, quote_lookup, episode_lookup, lines)

        appendix = list(self._render_appendix(plan, episode_lookup))
        if appendix:
            lines.extend(["", "---", ""])
            lines.extend(appendix)

        # Drop any trailing blank lines so the file ends in a single newline.
        while lines and lines[-1] == "":
            lines.pop()
        return "\n".join(lines) + "\n"

    @staticmethod
    def _segment_section_titles(plan: ThemePlan) -> Dict[str, str]:
        return {
            f"segment-{seg.rank}": (
                f"## Lead — {seg.theme}" if seg.rank == 1 else f"## {seg.theme}"
            )
            for seg in plan.segments
        }

    @staticmethod
    def _heading_for_section(
        section: str, segment_headings: Dict[str, str]
    ) -> str:
        if section.startswith("segment-"):
            return segment_headings.get(section, f"## {section.replace('-', ' ').title()}")
        if section == "tail":
            return "## Also today"
        return ""

    def _render_block(
        self,
        block: ScriptBlock,
        quote_lookup: Dict[str, QuoteCandidate],
        episode_lookup: Dict[str, Tuple[Podcast, Episode]],
        out: List[str],
    ) -> None:
        if block.kind == "narration":
            text = (block.text or "").strip()
            if not text:
                return
            out.append(text)
            out.append("")
            return

        quote = quote_lookup.get(block.quote_id or "")
        if quote is None:
            return
        episode_pair = episode_lookup.get(quote.episode_id)
        clock = _format_clock(quote.start_seconds)
        listen_link = self._listen_link(episode_pair, quote)
        for line in quote.text.splitlines() or [quote.text]:
            line = line.strip()
            if line:
                out.append(f"> {line}")
        attribution = f"> — {quote.speaker}, {quote.podcast_title} ({clock})"
        if listen_link is not None:
            attribution += f" · {listen_link}"
        out.append(attribution)
        out.append("")

    def _listen_link(
        self,
        episode_pair: Tuple[Podcast, Episode] | None,
        quote: QuoteCandidate,
    ) -> str | None:
        if episode_pair is None:
            return None
        podcast, episode = episode_pair
        if not podcast.slug or not episode.slug:
            return None
        url = self.url_generator.episode_at(
            podcast.slug, episode.slug, quote.start_seconds
        )
        return f"[▶ Listen at {_format_clock(quote.start_seconds)}]({url})"

    @staticmethod
    def _episodes_label(stats: NarrationStats) -> str:
        total = stats.episodes_covered + stats.episodes_in_tail
        if total == 1:
            return "1 episode covered"
        return f"{total} episodes covered"

    def _render_appendix(
        self,
        plan: ThemePlan,
        episode_lookup: Dict[str, Tuple[Podcast, Episode]],
    ) -> Iterable[str]:
        """Always-on link index of every episode in the run.

        Spec §"Product Requirements" guarantees a fall-back-to-the-link-
        index option even on a successful narrated run, so users can
        click through to a full summary without scanning the prose.
        """
        all_ids: List[str] = []
        for seg in plan.segments:
            all_ids.extend(seg.episode_ids)
        all_ids.extend(plan.tail_ids)
        seen: set = set()
        emitted_any = False
        out: List[str] = ["## Episodes covered"]
        for eid in all_ids:
            if eid in seen:
                continue
            seen.add(eid)
            pair = episode_lookup.get(eid)
            if pair is None:
                continue
            podcast, episode = pair
            if not podcast.slug or not episode.slug:
                out.append(f"- *{podcast.title}* — {episode.title}")
            else:
                url = self.url_generator.episode(podcast.slug, episode.slug)
                out.append(f"- *{podcast.title}* — [{episode.title}]({url})")
            emitted_any = True
        if not emitted_any:
            return []
        return out
