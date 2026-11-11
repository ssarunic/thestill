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
"""Sync tracks for episodes transcribed before spec #116 shipped.

Dalston keeps the audio of a job for a while after it finishes. For an
episode whose raw transcript names a Dalston job, the track can still be
cut from the exact bytes that were transcribed, with no re-transcription.

Two things have to be true before a reader may use such a track, and both
are checked here rather than assumed:

- the track describes the stored raw transcript (it is cut from the audio
  of the job that transcript names);
- the cleaned transcript was made from that raw transcript. It is stamped
  with the track's generation only when it was written after the raw
  transcript and carries no stamp yet. A cleaned transcript that is older
  than the raw one (a reprocess is under way) is left alone and gets its
  stamp when the clean stage runs.
"""

from __future__ import annotations

import json
from dataclasses import dataclass
from typing import Any, Iterator, Optional

from structlog import get_logger

from ..core.dalston_transcriber import iter_job_audio
from ..models.podcast import Episode, Podcast
from .sync_track_service import SyncTrackService

logger = get_logger(__name__)

HAS_TRACK = "has_track"
NO_TRANSCRIPT = "no_transcript"
NOT_DALSTON = "not_dalston"
WOULD_BUILD = "would_build"
BUILT = "built"
AUDIO_GONE = "audio_gone"
UNSUPPORTED = "unsupported_codec"
FAILED = "failed"
BUSY = "busy"
SUPERSEDED = "superseded"


@dataclass(frozen=True)
class BackfillResult:
    """What happened to one episode."""

    episode_id: str
    outcome: str
    generation: Optional[str] = None
    stamped: bool = False
    detail: Optional[str] = None


def _read_json(config: Any, relative_path: str) -> Optional[dict]:
    try:
        return json.loads(config.file_storage.read_text(relative_path))
    except FileNotFoundError:
        return None


def _job_audio(config: Any, job_id: str) -> Iterator[bytes]:
    """The job's stored audio, fetched only once iteration starts, so every
    failure (also an immediate one) surfaces inside the service's build step
    and is reported as an outcome instead of aborting the run."""
    yield from iter_job_audio(
        job_id,
        base_url=config.dalston_base_url or None,
        api_key=config.dalston_api_key or None,
        max_bytes=config.max_audio_bytes,
    )


def stamp_cleaned_transcript(config: Any, episode: Episode, generation: str, *, dry_run: bool = False) -> bool:
    """Write ``generation`` into the episode's cleaned transcript when that
    transcript was made from the stored raw transcript and has no stamp.

    Returns True when the stamp was (or, with ``dry_run``, would be) written.
    """
    if not episode.clean_transcript_json_path or not episode.raw_transcript_path:
        return False
    paths = config.path_manager
    sidecar_key = paths.to_relative(paths.clean_transcript_file(episode.clean_transcript_json_path))
    raw_key = paths.to_relative(paths.raw_transcript_file(episode.raw_transcript_path))
    storage = config.file_storage
    try:
        if storage.get_modified_time(sidecar_key) < storage.get_modified_time(raw_key):
            return False  # cleaned from an earlier raw transcript
    except FileNotFoundError:
        return False
    sidecar = _read_json(config, sidecar_key)
    if sidecar is None or sidecar.get("sync_track_generation"):
        return False
    if dry_run:
        return True
    sidecar["sync_track_generation"] = generation
    storage.write_text(sidecar_key, json.dumps(sidecar, ensure_ascii=False, indent=2))
    return True


def _dalston_job_id(config: Any, episode: Episode) -> Optional[str]:
    """The Dalston job named by the episode's stored raw transcript."""
    if not episode.raw_transcript_path:
        return None
    paths = config.path_manager
    raw = _read_json(config, paths.to_relative(paths.raw_transcript_file(episode.raw_transcript_path)))
    metadata = (raw or {}).get("provider_metadata") or {}
    job_id = metadata.get("job_id") if metadata.get("provider") == "dalston" else None
    return str(job_id) if job_id else None


def backfill_episode(
    config: Any,
    service: SyncTrackService,
    podcast: Podcast,
    episode: Episode,
    *,
    dry_run: bool = False,
    queue_manager: Any = None,
) -> BackfillResult:
    """Give one already-transcribed episode a sync track, if it still can have one.

    The pipeline may process the same episode at the same time. An episode
    with a task in flight is skipped (``queue_manager``), and the track is
    published only if, when the audio has been fetched, the raw transcript
    still names the same job and the episode still has no track.
    """
    log = logger.bind(episode_id=episode.id, podcast_id=podcast.id)
    if queue_manager is not None and queue_manager.has_active_task_for_episode(episode.id):
        return BackfillResult(episode.id, BUSY)

    current = service.current(episode.id)
    if current is not None:
        # A track exists; the cleaned transcript may still lack its stamp
        # (a backfill interrupted between the two steps).
        stamped = stamp_cleaned_transcript(config, episode, current.generation, dry_run=dry_run)
        return BackfillResult(episode.id, HAS_TRACK, generation=current.generation, stamped=stamped)

    if not episode.raw_transcript_path:
        return BackfillResult(episode.id, NO_TRANSCRIPT)
    paths = config.path_manager
    raw = _read_json(config, paths.to_relative(paths.raw_transcript_file(episode.raw_transcript_path)))
    if raw is None:
        return BackfillResult(episode.id, NO_TRANSCRIPT)
    metadata = raw.get("provider_metadata") or {}
    job_id = metadata.get("job_id") if metadata.get("provider") == "dalston" else None
    if not job_id:
        return BackfillResult(episode.id, NOT_DALSTON)
    if dry_run:
        return BackfillResult(episode.id, WOULD_BUILD, detail=str(job_id))

    record, reason = service.build_pending_detailed(
        episode_id=episode.id,
        podcast_id=podcast.id,
        podcast_slug=podcast.slug,
        episode_slug=episode.slug,
        chunks=_job_audio(config, str(job_id)),
        source="dalston",
    )
    if record is None:
        outcome = {"audio_gone": AUDIO_GONE, "unsupported_codec": UNSUPPORTED}.get(reason or "", FAILED)
        return BackfillResult(episode.id, outcome, detail=reason)

    ends = [segment.get("end") for segment in raw.get("segments") or []]
    transcript_end_s = max((end for end in ends if isinstance(end, (int, float))), default=None)
    # Fetching the audio took a while. If the episode was re-transcribed
    # meanwhile, the stored transcript names another job and this track is
    # not its track.
    if _dalston_job_id(config, episode) != str(job_id):
        service.discard_pending_generation(episode.id, record.generation)
        return BackfillResult(episode.id, SUPERSEDED, detail="raw transcript changed")
    published = service.publish_if_untracked(
        episode_id=episode.id,
        generation=record.generation,
        podcast_id=podcast.id,
        transcript_end_s=transcript_end_s,
    )
    if published is None:
        return BackfillResult(episode.id, SUPERSEDED)

    stamped = stamp_cleaned_transcript(config, episode, published.generation)
    log.info("sync_track_backfilled", generation=published.generation, stamped=stamped, job_id=str(job_id))
    return BackfillResult(episode.id, BUILT, generation=published.generation, stamped=stamped)
