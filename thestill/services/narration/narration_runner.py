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

"""End-to-end runner for narrated-digest generation (spec #33 Phase 3).

Resolves a digest reference (id or "latest") into the underlying
``(Podcast, Episode)`` tuples, hands them to ``NarrationGenerator``,
writes the JSON + Markdown artefacts to disk, and returns the
``NarrationContent`` plus the resolved digest id. Shared by the CLI
``thestill narrate`` command and the ``POST /api/narrations`` route so
both surfaces produce identical artefacts.
"""

import time
from dataclasses import dataclass
from pathlib import Path
from typing import List, Optional, Tuple

from structlog import get_logger

from ...models.digest import Digest
from ...models.podcast import Episode, Podcast
from ...repositories.digest_repository import DigestRepository
from ...repositories.podcast_repository import PodcastRepository
from .models import NarrationContent
from .narration_generator import NarrationConfig, NarrationGenerator

logger = get_logger(__name__)


class NarrationRunnerError(Exception):
    """Raised when the runner cannot resolve the requested digest."""


@dataclass(frozen=True)
class NarrationRun:
    """Result envelope returned by ``NarrationRunner.run``.

    ``narration_id``, ``json_path``, and ``markdown_path`` are derived
    from ``content`` so the runner has a single source of truth for the
    on-disk artefacts; the properties are convenience accessors for
    callers that want them directly.
    """

    digest_id: str
    slug: str
    content: NarrationContent

    @property
    def narration_id(self) -> str:
        return f"{self.digest_id}-{self.slug}"

    @property
    def json_path(self) -> Optional[Path]:
        return self.content.json_script_path

    @property
    def markdown_path(self) -> Optional[Path]:
        return self.content.markdown_path


class NarrationRunner:
    """Convert a digest reference into a written narration artefact."""

    def __init__(
        self,
        generator: NarrationGenerator,
        digest_repository: DigestRepository,
        podcast_repository: PodcastRepository,
    ):
        self.generator = generator
        self.digest_repository = digest_repository
        self.podcast_repository = podcast_repository

    def run(
        self,
        *,
        digest_id: Optional[str] = None,
        target_duration_seconds: int = 300,
        slug: str = "morning",
        wpm: float = 150.0,
        max_quote_share: float = 0.40,
    ) -> NarrationRun:
        digest = self._resolve_digest(digest_id)
        episodes = self._resolve_episodes(digest)
        if not episodes:
            raise NarrationRunnerError(f"digest {digest.id} contains no resolvable episodes")
        try:
            cfg = NarrationConfig(
                target_duration_seconds=target_duration_seconds,
                wpm=wpm,
                max_quote_share=max_quote_share,
                slug=slug,
                basename=f"{digest.id}-{slug}",
                digest_id=digest.id,
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
        run = NarrationRun(digest_id=digest.id, slug=slug, content=content)
        logger.info(
            "narration.run",
            digest_id=digest.id,
            narration_id=run.narration_id,
            mode=content.mode,
            target_seconds=cfg.target_duration_seconds,
            actual_seconds=round(content.stats.actual_duration_seconds, 1),
            quote_count=content.stats.quote_count,
            latency_ms=content.latency_ms,
            fallback_reason=content.stats.fallback_reason,
        )
        return run

    def _resolve_digest(self, digest_id: Optional[str]) -> Digest:
        if digest_id is None or digest_id == "latest":
            digest = self.digest_repository.get_latest()
            if digest is None:
                raise NarrationRunnerError("no digests found — run `thestill digest` before `thestill narrate`")
            return digest
        digest = self.digest_repository.get_by_id(digest_id)
        if digest is None:
            raise NarrationRunnerError(f"digest not found: {digest_id}")
        return digest

    def _resolve_episodes(self, digest: Digest) -> List[Tuple[Podcast, Episode]]:
        resolved: List[Tuple[Podcast, Episode]] = []
        for episode_id in digest.episode_ids:
            pair = self.podcast_repository.get_episode(episode_id)
            if pair is None:
                logger.debug(
                    "narration.run: episode missing from digest",
                    digest_id=digest.id,
                    episode_id=episode_id,
                )
                continue
            resolved.append(pair)
        return resolved
