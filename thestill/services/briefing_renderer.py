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

"""
Briefing script renderer (spec #36, Phase 1.5).

Composes the inbox-window episode IDs the BriefingService computed into a
markdown script via the existing ``DigestGenerator``. Kept separate from
the state machine so the cursor / throttle logic stays free of file-IO
and so callers that want a row-only briefing (tests, future async paths)
can omit rendering by passing ``renderer=None`` to ``BriefingService``.
"""

from pathlib import Path
from typing import List, Tuple

from structlog import get_logger

from ..models.briefing import Briefing
from ..models.podcast import Episode, Podcast
from ..repositories.podcast_repository import PodcastRepository
from ..utils.path_manager import PathManager
from .digest_generator import DigestGenerator

logger = get_logger(__name__)

# Filename inside ``briefings/<user_id>/<briefing_id>/``. Audio (when #34
# lands) lives next to the script as ``audio.mp3`` per spec §Migration.
_SCRIPT_FILENAME = "script.md"


class BriefingRenderer:
    """Render a briefing's script.md from its inbox-window episode IDs."""

    def __init__(
        self,
        digest_generator: DigestGenerator,
        podcast_repository: PodcastRepository,
        path_manager: PathManager,
    ) -> None:
        self._generator = digest_generator
        self._repository = podcast_repository
        self._paths = path_manager

    def render(self, briefing: Briefing, episode_ids: List[str]) -> Path:
        """Render the briefing script to disk and return its path.

        Episodes that no longer exist in the repository (e.g. deleted
        between inbox delivery and render) are silently skipped — the
        briefing covers what's still resolvable. The cursor still
        advances either way.
        """
        episodes: List[Tuple[Podcast, Episode]] = []
        for episode_id in episode_ids:
            row = self._repository.get_episode(episode_id)
            if row is None:
                logger.warning(
                    "briefing_render_skipped_missing_episode",
                    briefing_id=briefing.id,
                    episode_id=episode_id,
                )
                continue
            episodes.append(row)

        content = self._generator.generate(episodes)

        output_path = self._paths.briefing_dir(briefing.user_id, briefing.id) / _SCRIPT_FILENAME
        self._generator.write(content, output_path)
        return output_path
