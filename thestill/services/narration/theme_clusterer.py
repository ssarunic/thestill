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

"""Theme clustering LLM call (spec #33 Pipeline Stage 1).

Takes the day's episode briefs and asks the model to group them into
2–4 theme-grouped segments plus a tail bucket of episodes that don't
fold into any segment. Output is schema-validated; episode IDs the
model invents (or omits) are reconciled against the input set with a
deterministic post-pass before the plan is handed to script generation.
"""

from typing import Dict, List, Sequence, Tuple

from pydantic import BaseModel, Field
from structlog import get_logger

from ...core.llm_provider import LLMProvider
from .models import EpisodeBrief, Segment, ThemePlan

logger = get_logger(__name__)


_MAX_SEGMENTS = 4
_MIN_SEGMENTS = 1  # spec allows single-episode lead segments on light news days


class _ThemeSegmentOut(BaseModel):
    theme: str = Field(..., min_length=1, max_length=120)
    angle: str = Field(..., min_length=1, max_length=240)
    episode_ids: List[str] = Field(default_factory=list, min_length=1)
    rank: int = Field(..., ge=1)


class _ThemePlanOut(BaseModel):
    segments: List[_ThemeSegmentOut] = Field(default_factory=list)
    tail: List[str] = Field(default_factory=list)


_SYSTEM_PROMPT = """\
You are a news editor preparing a daily podcast briefing. You are given a
list of episodes with light metadata (podcast, title, guests, topic
keywords, a 2-sentence Gist). Group them into 2–4 theme-grouped lead
segments plus a tail bucket for episodes that do not fit a multi-show
theme.

Rules:

- 2–4 lead segments. Single-episode segments are allowed on light news
  days when the story is a clear lead.
- Each segment names a concrete angle, not just a topic — "disagreement
  on X", "what changed about Y", or "two takes on Z" rather than just
  "AI coding".
- Episodes that do not fold into a segment go to the tail.
- Every input episode_id must appear exactly once across segments and
  tail. Do not invent ids; do not skip episodes.
- Segments are ranked 1..N with 1 the lead.

Output schema: {"segments": [{"theme":..., "angle":..., "episode_ids":[…], "rank":N}], "tail": [...]}
"""


def _format_episode_brief(brief: EpisodeBrief) -> str:
    parts = [
        f"- episode_id: {brief.episode_id}",
        f"  podcast: {brief.podcast_title}",
        f"  title: {brief.episode_title}",
    ]
    if brief.guests:
        parts.append(f"  guests: {', '.join(brief.guests)}")
    if brief.topics:
        parts.append(f"  topics: {', '.join(brief.topics)}")
    if brief.sponsors:
        parts.append(f"  sponsors: {', '.join(brief.sponsors)}")
    if brief.gist:
        parts.append(f"  gist: {brief.gist}")
    return "\n".join(parts)


class ThemeClusterer:
    """Theme clustering via one structured-output LLM call."""

    def __init__(self, provider: LLMProvider):
        self.provider = provider

    def cluster(
        self, briefs: Sequence[EpisodeBrief], target_duration_seconds: int
    ) -> ThemePlan:
        """Group ``briefs`` into segments + tail.

        On any LLM error, an empty plan is returned (every episode goes
        to the tail) — the generator treats that as a soft failure the
        same way it treats two failed regenerations of the script:
        every episode lands in the tail and the markdown reverts to the
        link-index fallback.
        """
        if not briefs:
            return ThemePlan(segments=(), tail_ids=())

        user_prompt = (
            f"Target spoken duration: {target_duration_seconds}s.\n"
            f"Episodes ({len(briefs)}):\n\n"
            + "\n\n".join(_format_episode_brief(b) for b in briefs)
        )
        try:
            result = self.provider.generate_structured(
                messages=[
                    {"role": "system", "content": _SYSTEM_PROMPT},
                    {"role": "user", "content": user_prompt},
                ],
                response_model=_ThemePlanOut,
                temperature=0.2,
            )
        except Exception as exc:  # noqa: BLE001 — degrade to empty plan + tail-only
            logger.warning(
                "narration: theme clustering failed; routing all episodes to tail",
                error=str(exc),
            )
            return ThemePlan(segments=(), tail_ids=tuple(b.episode_id for b in briefs))

        return self._reconcile(result, briefs)

    def _reconcile(
        self, result: _ThemePlanOut, briefs: Sequence[EpisodeBrief]
    ) -> ThemePlan:
        """Fold the LLM output back onto the input set.

        Every input episode_id appears exactly once in the returned plan
        regardless of model drift. Unknown ids the model invented are
        dropped; ids the model omitted are added to the tail. Segment
        count is capped at four and ordered by ``rank`` ascending.
        """
        valid_ids = {b.episode_id for b in briefs}
        seen: Dict[str, str] = {}  # episode_id → "segment-{rank}" or "tail"

        segments: List[Tuple[int, Segment]] = []
        for raw in result.segments:
            kept_ids: List[str] = []
            for eid in raw.episode_ids:
                if eid not in valid_ids or eid in seen:
                    continue
                seen[eid] = f"segment-{raw.rank}"
                kept_ids.append(eid)
            if not kept_ids:
                continue
            segments.append(
                (
                    raw.rank,
                    Segment(
                        theme=raw.theme.strip(),
                        angle=raw.angle.strip(),
                        episode_ids=tuple(kept_ids),
                        rank=raw.rank,
                    ),
                )
            )

        # Stable order: rank ascending, ties broken by first-mentioned
        # episode_id within the segment.
        segments.sort(key=lambda pair: (pair[0], pair[1].episode_ids))
        # Cap to top-N segments after sort so a model emitting >4
        # segments still gives us the most-prominent four. Episode
        # IDs that lived in the dropped segments fall through to the
        # tail rather than disappearing from the run.
        kept_segments = segments[:_MAX_SEGMENTS]
        kept_ids = {eid for _, seg in kept_segments for eid in seg.episode_ids}

        renumbered: List[Segment] = []
        for new_rank, (_, seg) in enumerate(kept_segments, start=1):
            renumbered.append(
                Segment(
                    theme=seg.theme,
                    angle=seg.angle,
                    episode_ids=seg.episode_ids,
                    rank=new_rank,
                )
            )

        tail_ids: List[str] = [
            eid for eid in result.tail if eid in valid_ids and eid not in kept_ids
        ]
        for eid in valid_ids:
            if eid not in kept_ids and eid not in tail_ids:
                tail_ids.append(eid)

        if len(renumbered) < _MIN_SEGMENTS:
            # Plan emerged empty — fall through; the script-generation
            # path can still produce an opener+tail+signoff over the
            # tail-only set.
            logger.info(
                "narration: theme clusterer returned no usable segments; tail-only plan",
                input_count=len(briefs),
                tail_count=len(tail_ids),
            )

        return ThemePlan(segments=tuple(renumbered), tail_ids=tuple(tail_ids))
