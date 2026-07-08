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

"""End-to-end runner for briefing narration (spec #33 Phase 3, rekeyed to
per-user briefings on digest retirement).

Resolves a briefing id into the underlying ``(Podcast, Episode)`` tuples
(via the briefing's inbox cursor window), hands them to
``NarrationGenerator``, writes the JSON + Markdown artefacts to disk, and
returns the ``NarrationContent`` plus the briefing id. Shared by the CLI
``thestill narrate`` command and the ``POST /api/briefings/{id}/narrate``
route so both surfaces produce identical artefacts.
"""

import time
from dataclasses import dataclass
from pathlib import Path
from typing import List, Optional, Tuple

from structlog import get_logger

from ...models.briefing import Briefing
from ...models.inbox import InboxState
from ...models.podcast import Episode, Podcast
from ...repositories.briefing_repository import BriefingRepository
from ...repositories.inbox_repository import InboxRepository
from ...repositories.podcast_repository import PodcastRepository
from .models import NarrationContent
from .narration_generator import NarrationConfig, NarrationGenerator

logger = get_logger(__name__)

# States considered when re-resolving a briefing's episodes for narration.
# The briefing was generated over ``unread``/``saved`` rows; ``read`` is
# included here so an episode the user read *after* generation still
# narrates (the briefing covered it). ``dismissed`` stays excluded — a
# negative signal (spec #36).
_NARRATION_STATES: tuple[InboxState, ...] = ("unread", "saved", "read")


class NarrationRunnerError(Exception):
    """Raised when the runner cannot resolve the requested briefing."""


@dataclass(frozen=True)
class NarrationRun:
    """Result envelope returned by ``NarrationRunner.run``.

    ``narration_id``, ``json_path``, and ``markdown_path`` are derived
    from ``content`` so the runner has a single source of truth for the
    on-disk artefacts; the properties are convenience accessors for
    callers that want them directly.
    """

    briefing_id: str
    slug: str
    content: NarrationContent

    @property
    def narration_id(self) -> str:
        return f"{self.briefing_id}-{self.slug}"

    @property
    def json_path(self) -> Optional[Path]:
        return self.content.json_script_path

    @property
    def markdown_path(self) -> Optional[Path]:
        return self.content.markdown_path


class NarrationRunner:
    """Convert a briefing reference into a written narration artefact."""

    def __init__(
        self,
        generator: NarrationGenerator,
        briefing_repository: BriefingRepository,
        inbox_repository: InboxRepository,
        podcast_repository: PodcastRepository,
    ):
        self.generator = generator
        self.briefing_repository = briefing_repository
        self.inbox_repository = inbox_repository
        self.podcast_repository = podcast_repository

    def artifact_exists(self, *, briefing_id: str, slug: str) -> bool:
        """True when a narration artefact for ``(briefing, slug)`` is on disk.

        Lets idempotent callers (the briefing scheduler, spec #50 Phase 4)
        skip re-narrating a briefing that already has this variant instead
        of re-spending the LLM call.
        """
        narrations_dir = self.generator.path_manager.narrations_dir()
        return (narrations_dir / f"{briefing_id}-{slug}.json").exists()

    def run(
        self,
        *,
        briefing_id: str,
        target_duration_seconds: int = 300,
        slug: str = "morning",
        wpm: float = 150.0,
        max_quote_share: float = 0.40,
    ) -> NarrationRun:
        briefing = self._resolve_briefing(briefing_id)
        episodes = self._resolve_episodes(briefing)
        if not episodes:
            raise NarrationRunnerError(f"briefing {briefing.id} contains no resolvable episodes")
        try:
            cfg = NarrationConfig(
                target_duration_seconds=target_duration_seconds,
                wpm=wpm,
                max_quote_share=max_quote_share,
                slug=slug,
                basename=f"{briefing.id}-{slug}",
                briefing_id=briefing.id,
            )
        except ValueError as exc:
            # ``NarrationConfig.__post_init__`` rejects slugs with path
            # separators or non-canonical chars. Surface as a runner
            # error so the CLI prints a friendly message instead of a
            # stack trace, and the API can convert to 400.
            raise NarrationRunnerError(str(exc)) from exc
        started = time.perf_counter()
        content = self.generator.generate(episodes, cfg)
        content.latency_ms = int((time.perf_counter() - started) * 1000)
        self.generator.write_json_script(content, cfg)
        self.generator.write_markdown(content, cfg)
        run = NarrationRun(briefing_id=briefing.id, slug=slug, content=content)
        logger.info(
            "narration.run",
            briefing_id=briefing.id,
            narration_id=run.narration_id,
            mode=content.mode,
            target_seconds=cfg.target_duration_seconds,
            actual_seconds=round(content.stats.actual_duration_seconds, 1),
            quote_count=content.stats.quote_count,
            latency_ms=content.latency_ms,
            fallback_reason=content.stats.fallback_reason,
        )
        return run

    def _resolve_briefing(self, briefing_id: str) -> Briefing:
        briefing = self.briefing_repository.get(briefing_id)
        if briefing is None:
            raise NarrationRunnerError(f"briefing not found: {briefing_id}")
        return briefing

    def _resolve_episodes(self, briefing: Briefing) -> List[Tuple[Podcast, Episode]]:
        episode_ids = self.inbox_repository.list_episode_ids_in_window(
            briefing.user_id,
            since=briefing.cursor_from,
            until=briefing.cursor_to,
            states=_NARRATION_STATES,
        )
        resolved: List[Tuple[Podcast, Episode]] = []
        for episode_id in episode_ids:
            pair = self.podcast_repository.get_episode(episode_id)
            if pair is None:
                logger.debug(
                    "narration.run: episode missing from briefing window",
                    briefing_id=briefing.id,
                    episode_id=episode_id,
                )
                continue
            resolved.append(pair)
        return resolved
