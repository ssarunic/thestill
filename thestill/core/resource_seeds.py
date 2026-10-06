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

"""Spec #92 — load what the Resource List plan needs, and report on it.

``summary_resources`` is pure; this module reads the summary and the
transcript through ``FileStorage`` and logs. Extract, resolve and the eval
all plan through :class:`ResourceSource`, so the three see the same
admission decisions for the same inputs.

The source exists only when ``ENTITY_RESOURCE_SEEDS_ENABLED`` is on
(:func:`make_resource_source` returns ``None`` otherwise): off means no
summary is read and nothing is logged. The eval builds one directly to
measure regardless of the flag.
"""

from __future__ import annotations

from typing import Callable, Dict, Iterable, List, Optional, Sequence, Tuple

from structlog import get_logger

from ..models.annotated_transcript import AnnotatedTranscript
from ..models.entities import EntityMention
from ..models.podcast import Episode
from ..utils.file_storage import FileStorage
from ..utils.path_manager import PathManager
from .entity_anchor import AnchorVariant, expand_anchor_variants
from .entity_linking.types import surface_key
from .summary_citations import load_annotated_for_episode
from .summary_resources import (
    DEFAULT_GROUNDING_WINDOW_S,
    PlanContext,
    ResourceParseError,
    ResourcePlan,
    ResourceSeed,
    parse_resource_list,
    plan_resources,
)

logger = get_logger(__name__)


def plan_context(
    anchor_variants: Iterable[AnchorVariant], extracted_names: Sequence[Tuple[str, Optional[str]]]
) -> PlanContext:
    return PlanContext(
        anchor_surfaces=frozenset(surface_key(v.surface) for v in anchor_variants),
        extracted_names=tuple(extracted_names),
    )


class ResourceSource:
    """Plans an episode's Resource List from storage."""

    def __init__(
        self,
        *,
        path_manager: PathManager,
        file_storage: FileStorage,
        window_s: float = DEFAULT_GROUNDING_WINDOW_S,
    ):
        self._path_manager = path_manager
        self._file_storage = file_storage
        self._window_s = window_s

    def read_summary(self, episode: Episode) -> Optional[str]:
        if not episode.summary_path:
            return None
        key = self._path_manager.to_relative(self._path_manager.summary_file(episode.summary_path))
        try:
            return self._file_storage.read_text(key)
        except FileNotFoundError:
            return None

    def plan(
        self,
        episode: Episode,
        ctx: PlanContext,
        *,
        transcript: Optional[AnnotatedTranscript] = None,
        markdown: Optional[str] = None,
        stage: str,
    ) -> Optional[ResourcePlan]:
        """The episode's plan, or ``None`` when there is nothing to plan.

        ``transcript`` saves a second read when the caller already holds
        it; its playback offset is set from the episode either way. So does
        ``markdown`` (the summary text) for the eval. Raises
        :class:`ResourceParseError` for a section that cannot be read.
        """
        if markdown is None:
            markdown = self.read_summary(episode)
        if markdown is None:
            logger.info("resource_seeds_skipped", episode_id=episode.id, stage=stage, reason="no_summary")
            return None
        items = parse_resource_list(markdown)
        if not items:
            logger.info("resource_seeds_skipped", episode_id=episode.id, stage=stage, reason="no_resource_list")
            return None
        if transcript is None:
            transcript = load_annotated_for_episode(
                episode=episode, path_manager=self._path_manager, file_storage=self._file_storage
            )
            if transcript is None:
                logger.info("resource_seeds_skipped", episode_id=episode.id, stage=stage, reason="no_transcript")
                return None
        else:
            transcript = transcript.model_copy(
                update={"playback_time_offset_seconds": episode.playback_time_offset_seconds}
            )
        plan = plan_resources(items, transcript, ctx, window_s=self._window_s)
        logger.info(
            "resource_seeds_grounded",
            episode_id=episode.id,
            stage=stage,
            seeds=sum(len(g.scan_surfaces) for g in plan.grounded),
            dropped_surfaces=sum(len(g.dropped_surfaces) for g in plan.grounded),
            **plan.stats,
        )
        return plan

    def seed_provider(
        self,
        episode: Episode,
        transcript: AnnotatedTranscript,
        anchor_variants: Sequence[AnchorVariant],
    ) -> Callable[[List[EntityMention]], List[ResourceSeed]]:
        """The extractor's ``seed_provider`` for this episode.

        Called with GLiNER's mentions, so the plan sees the same names the
        resolve stage reads back with ``list_extracted_names``. Any failure
        is a WARNING and no seeds: the transcript's own mentions are
        committed regardless.
        """

        def provide(gliner_mentions: List[EntityMention]) -> List[ResourceSeed]:
            names = list(dict.fromkeys((m.surface_form, m.surface_label) for m in gliner_mentions))
            try:
                plan = self.plan(episode, plan_context(anchor_variants, names), transcript=transcript, stage="extract")
            except Exception as exc:  # noqa: BLE001 — never a gate on extraction
                logger.warning(
                    "resource_seeds_failed",
                    episode_id=episode.id,
                    stage="extract",
                    error=str(exc),
                    exc_info=not isinstance(exc, ResourceParseError),
                )
                return []
            return plan.seeds() if plan else []

        return provide

    def hints_for(self, repo, episode: Episode) -> Dict[str, Tuple[str, str]]:
        """Resolve-stage hints: surface_key → (kind, gloss).

        The Resource List is an addition, never a gate: any failure here is
        a WARNING and no hints, and resolution goes ahead.
        """
        try:
            anchors = [e for e in (repo.get_entity(i) for i in repo.get_episode_anchors(episode.id)) if e]
            ctx = plan_context(expand_anchor_variants(anchors), repo.list_extracted_names(episode.id))
            plan = self.plan(episode, ctx, stage="resolve")
        except ResourceParseError as exc:
            logger.warning("resource_seeds_failed", episode_id=episode.id, stage="resolve", error=str(exc))
            return {}
        except Exception as exc:  # noqa: BLE001 — never a gate on resolution
            logger.warning(
                "resource_seeds_failed", episode_id=episode.id, stage="resolve", error=str(exc), exc_info=True
            )
            return {}
        return plan.hints() if plan else {}


def make_resource_source(config, path_manager: PathManager) -> Optional[ResourceSource]:
    """The configured source, or ``None`` when ``ENTITY_RESOURCE_SEEDS_ENABLED`` is off."""
    if getattr(config, "entity_resource_seeds_enabled", False) is not True:
        return None
    return ResourceSource(
        path_manager=path_manager,
        file_storage=config.file_storage,
        window_s=float(getattr(config, "resource_grounding_window_s", DEFAULT_GROUNDING_WINDOW_S)),
    )
