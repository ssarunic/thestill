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
Reprocess one episode from a chosen stage (spec #106).

Queues the chosen stage with ``run_full_pipeline`` so it chains through
summarize and the entity branch, and tags every task in the run with a
``reprocess`` metadata block. Stage handlers read that block (see
:mod:`thestill.core.reprocess_metadata`) to redo work they would otherwise skip:
transcribe calls the provider again and keeps the old transcript and
summary readable, clean can refresh episode facts, reindex re-embeds.

Shared by ``POST /api/episodes/{id}/reprocess`` and ``thestill reprocess``.
"""

from dataclasses import dataclass
from typing import Any, Dict, Literal, Optional

from structlog import get_logger

from ..core.queue_manager import TaskStage
from ..core.reprocess_metadata import REPROCESS_INITIATED_BY
from ..models.podcast import Episode
from ..utils.datetime_utils import now_utc
from ..utils.url_patterns import is_remote_fetchable_audio_url

logger = get_logger(__name__)

#: The stages an admin can reprocess from, by request name. ``entities``
#: starts the entity branch at extract-entities.
REPROCESS_FROM_STAGES: Dict[str, TaskStage] = {
    "transcribe": TaskStage.TRANSCRIBE,
    "clean": TaskStage.CLEAN,
    "summarize": TaskStage.SUMMARIZE,
    "entities": TaskStage.EXTRACT_ENTITIES,
}


class ReprocessRejected(Exception):
    """The request cannot be queued; ``kind`` maps to the HTTP status."""

    def __init__(self, kind: Literal["invalid_stage", "not_found", "missing_input", "busy"], message: str) -> None:
        super().__init__(message)
        self.kind = kind


@dataclass(frozen=True)
class ReprocessQueued:
    task_id: Optional[str]  # None on a dry run
    episode_id: str
    episode_title: str
    from_stage: str
    starting_stage: TaskStage


def queue_reprocess(
    *,
    repository,
    queue_manager,
    config,
    episode_id: str,
    from_stage: str,
    refresh_episode_facts: bool = False,
    requested_by: Optional[str] = None,
    dry_run: bool = False,
) -> ReprocessQueued:
    """Queue a reprocess of ``episode_id`` from ``from_stage``.

    ``dry_run`` runs every check and returns the starting stage without
    queueing anything.

    Raises:
        ReprocessRejected: unknown stage, unknown episode, the input for the
            stage is missing, or the episode already has a task in flight.
    """
    stage = REPROCESS_FROM_STAGES.get(from_stage)
    if stage is None:
        raise ReprocessRejected(
            "invalid_stage", f"Unknown stage {from_stage!r}; use one of {', '.join(REPROCESS_FROM_STAGES)}"
        )

    found = repository.get_episode(episode_id)
    if not found:
        raise ReprocessRejected("not_found", f"Episode not found: {episode_id}")
    _podcast, episode = found

    starting_stage = _starting_stage(episode, stage, config)

    # Episode-wide, not per stage: a request merged into a plain queued
    # task would lose its metadata (the existing row wins), and a request
    # next to a running stage would race it.
    if queue_manager.has_active_task_for_episode(episode.id):
        raise ReprocessRejected("busy", "This episode already has a task queued or running")
    if dry_run:
        return ReprocessQueued(
            task_id=None,
            episode_id=episode.id,
            episode_title=episode.title,
            from_stage=from_stage,
            starting_stage=starting_stage,
        )

    metadata: Dict[str, Any] = {
        "run_full_pipeline": True,
        "initiated_by": REPROCESS_INITIATED_BY,
        "reprocess": {
            "from_stage": from_stage,
            "refresh_episode_facts": bool(refresh_episode_facts),
            "requested_by": requested_by,
            "requested_at": now_utc().isoformat(),
        },
    }
    task = queue_manager.add_task(episode.id, starting_stage, metadata=metadata)

    logger.info(
        "episode_reprocess_requested",
        episode_id=episode.id,
        task_id=task.id,
        from_stage=from_stage,
        starting_stage=starting_stage.value,
        refresh_episode_facts=bool(refresh_episode_facts),
        user_id=requested_by,
    )
    return ReprocessQueued(
        task_id=task.id,
        episode_id=episode.id,
        episode_title=episode.title,
        from_stage=from_stage,
        starting_stage=starting_stage,
    )


def _starting_stage(episode: Episode, stage: TaskStage, config) -> TaskStage:
    """The stage the run actually starts at, or raise when its input is missing."""
    if stage == TaskStage.CLEAN:
        if not episode.raw_transcript_path:
            raise ReprocessRejected("missing_input", "Episode has no raw transcript to clean")
        return stage
    if stage == TaskStage.SUMMARIZE:
        if not episode.clean_transcript_path:
            raise ReprocessRejected("missing_input", "Episode has no clean transcript to summarize")
        return stage
    if stage == TaskStage.EXTRACT_ENTITIES:
        if not episode.clean_transcript_json_path:
            raise ReprocessRejected("missing_input", "Episode has no segmented transcript to extract entities from")
        return stage
    return _transcribe_entry_stage(episode, config)


def _transcribe_entry_stage(episode: Episode, config) -> TaskStage:
    """Start at transcribe when its audio is still there, else as early as needed.

    ``delete_audio_after_processing`` removes the downsampled WAV after the
    first transcription (and the original after downsampling), so a fully
    processed episode usually has neither. Dalston fetches the audio URL
    itself, which the transcribe handler falls back to.
    """
    storage = config.file_storage
    paths = config.path_manager
    url_fetchable = is_remote_fetchable_audio_url(str(episode.audio_url) if episode.audio_url else None)

    if config.transcription_provider == "dalston" and url_fetchable:
        return TaskStage.TRANSCRIBE
    if episode.downsampled_audio_path and storage.exists(
        paths.to_relative(paths.downsampled_audio_file(episode.downsampled_audio_path))
    ):
        return TaskStage.TRANSCRIBE
    if episode.audio_path and storage.exists(paths.to_relative(paths.original_audio_file(episode.audio_path))):
        return TaskStage.DOWNSAMPLE
    if episode.audio_url:
        return TaskStage.DOWNLOAD
    raise ReprocessRejected("missing_input", "Episode has no audio to transcribe")
