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
"""Sync tracks in the pipeline (spec #116, Phase 1).

A sync track must be cut from the exact bytes its transcript came from, and
a transcript must never be paired with a track cut from other bytes. The
pipeline therefore works in two steps:

1. ``build_pending`` — as soon as the transcribed bytes are in hand, build a
   track and store it as *pending*. Readers cannot see it.
2. ``publish`` — once the raw transcript is stored, make the pending track
   the episode's current one and remove the previous one. When there is no
   pending track, the previous one is removed anyway: it described a
   transcript that has just been replaced.

Nothing in here may fail the pipeline stage that calls it. Every public
method catches its own errors and logs ``sync_track_failed``; the episode
then simply has no track and the reader keeps its drift banner.
"""

from __future__ import annotations

import time
import uuid
from typing import Iterable, Optional, Tuple

from structlog import get_logger

from ..core.sync_track import SyncTrack, SyncTrackError, build_sync_track
from ..repositories.sync_track_repository import STATE_CURRENT, STATE_PENDING, SyncTrackRecord, SyncTrackRepository

logger = get_logger(__name__)

#: The track and the transcript must describe audio of the same length.
#: Two seconds, because the episode's measured duration is stored in whole
#: seconds and the downsampled file differs from the MP3 by encoder padding.
DURATION_TOLERANCE_S = 2.0
#: Fewer frames than this is not an episode (or not MP3 at all).
MIN_FRAMES = 100


class SyncTrackService:
    """Builds, stores and publishes sync tracks. Never raises to callers."""

    def __init__(self, config, repository: SyncTrackRepository):
        self._config = config
        self._repo = repository

    @property
    def enabled(self) -> bool:
        return bool(getattr(self._config, "sync_tracks_enabled", True))

    def build_pending(
        self,
        *,
        episode_id: str,
        podcast_id: Optional[str],
        podcast_slug: str,
        episode_slug: str,
        chunks: Iterable[bytes],
        source: str,
    ) -> Optional[SyncTrackRecord]:
        """Build a track from ``chunks`` and store it as the episode's pending
        track. Returns its record, or None when no track was written.
        """
        record, _reason = self.build_pending_detailed(
            episode_id=episode_id,
            podcast_id=podcast_id,
            podcast_slug=podcast_slug,
            episode_slug=episode_slug,
            chunks=chunks,
            source=source,
        )
        return record

    def build_pending_detailed(
        self,
        *,
        episode_id: str,
        podcast_id: Optional[str],
        podcast_slug: str,
        episode_slug: str,
        chunks: Iterable[bytes],
        source: str,
    ) -> Tuple[Optional[SyncTrackRecord], Optional[str]]:
        """``build_pending`` that also says why no track was written:
        ``disabled``, ``unsupported_codec``, ``no_unique_anchors``,
        ``audio_gone`` (the audio source answered 404 or 410) or ``error``.

        An earlier pending track is dropped first, whatever happens next:
        it was cut from audio that this call's audio replaces, so a build
        that is disabled or fails must not leave it to be published.
        """
        self.discard_pending(episode_id)
        if not self.enabled:
            return None, "disabled"
        generation = uuid.uuid4().hex
        log = logger.bind(episode_id=episode_id, podcast_id=podcast_id, generation=generation, source=source)
        started = time.monotonic()
        try:
            try:
                track = build_sync_track(chunks, generation)
            except SyncTrackError as exc:
                log.info("sync_track_failed", reason="unsupported_codec", detail=str(exc))
                return None, "unsupported_codec"
            if track.frame_count < MIN_FRAMES or not track.anchors:
                reason = "unsupported_codec" if track.frame_count < MIN_FRAMES else "no_unique_anchors"
                log.info("sync_track_failed", reason=reason, frames=track.frame_count)
                return None, reason
            payload = track.to_bytes()
            path = self._config.path_manager.to_relative(
                self._config.path_manager.sync_track_file(podcast_slug, episode_slug, generation)
            )
            self._config.file_storage.write_bytes(path, payload)
            record = SyncTrackRecord(
                episode_id=episode_id,
                generation=generation,
                state=STATE_PENDING,
                path=path,
                source=source,
                frames=track.frame_count,
                anchors=len(track.anchors),
                audio_bytes=track.audio_bytes,
                audio_duration_s=track.duration_s,
            )
            replaced = self._repo.put_pending(record)
            self._delete_files([replaced] if replaced else [], log)
            log.info(
                "sync_track_written",
                frames=track.frame_count,
                anchors_kept=len(track.anchors),
                anchors_dropped_duplicate=track.anchors_dropped_duplicate,
                unique_ratio=round(track.unique_ratio, 4),
                audio_bytes=track.audio_bytes,
                audio_duration_s=round(track.duration_s, 3),
                track_bytes=len(payload),
                duration_ms=int((time.monotonic() - started) * 1000),
            )
            return record, None
        except Exception as exc:  # noqa: BLE001 — must never fail the stage
            status = getattr(getattr(exc, "response", None), "status_code", None)
            if status in (404, 410):
                log.info("sync_track_failed", reason="audio_gone", status=status)
                return None, "audio_gone"
            log.warning("sync_track_failed", reason="error", error=str(exc), exc_info=True)
            return None, "error"

    def publish(
        self,
        *,
        episode_id: str,
        podcast_id: Optional[str] = None,
        transcribed_duration_s: Optional[float] = None,
        transcript_end_s: Optional[float] = None,
    ) -> Optional[SyncTrackRecord]:
        """Pair the episode's just-stored transcript with its pending track.

        Call right after the raw transcript is stored. The previous current
        track is removed whether or not a pending one takes its place.

        ``transcribed_duration_s`` is the length of the audio that was
        transcribed, when the caller knows it. A pending track of a different
        length was cut from other bytes and is discarded. ``transcript_end_s``
        is the end of the last transcribed segment: a weaker check that works
        for every provider, since no word can end after the audio does.
        """
        log = logger.bind(episode_id=episode_id, podcast_id=podcast_id)
        try:
            pending = self._repo.get(episode_id, STATE_PENDING)
            if pending is not None:
                delta = 0.0
                mismatch = False
                if transcribed_duration_s is not None:
                    delta = pending.audio_duration_s - transcribed_duration_s
                    mismatch = abs(delta) > DURATION_TOLERANCE_S
                if not mismatch and transcript_end_s is not None:
                    delta = pending.audio_duration_s - transcript_end_s
                    mismatch = delta < -DURATION_TOLERANCE_S
                if mismatch:
                    log.error(
                        "sync_track_transcript_mismatch",
                        generation=pending.generation,
                        audio_duration_s=round(pending.audio_duration_s, 3),
                        transcribed_input_duration_s=transcribed_duration_s,
                        transcript_end_s=transcript_end_s,
                        delta_s=round(delta, 3),
                    )
                    self._delete_files(self._repo.retire(episode_id, STATE_PENDING), log)
                    pending = None
            previous = self._repo.get(episode_id, STATE_CURRENT)
            if pending is None:
                retired = self._repo.retire(episode_id, STATE_CURRENT)
                self._delete_files(retired, log)
                if retired:
                    log.info(
                        "sync_track_switched",
                        old_generation=previous.generation if previous else None,
                        new_generation=None,
                    )
                return None
            retired = self._repo.promote(episode_id, pending.generation)
            if retired is None:
                return None
            self._delete_files(retired, log)
            log.info(
                "sync_track_switched",
                old_generation=previous.generation if previous else None,
                new_generation=pending.generation,
                duration_checked=transcribed_duration_s is not None,
                transcript_end_checked=transcript_end_s is not None,
            )
            return self._repo.get(episode_id, STATE_CURRENT)
        except Exception as exc:  # noqa: BLE001 — must never fail the stage
            log.warning("sync_track_failed", reason="publish_error", error=str(exc), exc_info=True)
            return None

    def discard_pending(self, episode_id: str) -> None:
        """Drop a pending track that no longer matches what is being
        transcribed (the episode is taking a path that did not build it)."""
        log = logger.bind(episode_id=episode_id)
        try:
            self._delete_files(self._repo.retire(episode_id, STATE_PENDING), log)
        except Exception as exc:  # noqa: BLE001 — must never fail the stage
            log.warning("sync_track_failed", reason="discard_error", error=str(exc), exc_info=True)

    def usable_generation(self, episode_id: str, stamped: Optional[str]) -> Optional[str]:
        """The generation a reader may use for a cleaned transcript stamped
        with ``stamped``: the stamp itself when it names the episode's
        current track, otherwise None.

        The two differ between re-transcribing and re-cleaning an episode
        (the visible cleaned transcript is still the old one), and on
        cleaned transcripts written before sync tracks existed. Never
        raises: a reader without a sync track is a supported state.
        """
        if not stamped:
            return None
        try:
            current = self._repo.get(episode_id, STATE_CURRENT)
        except Exception as exc:  # noqa: BLE001
            logger.warning("sync_track_failed", episode_id=episode_id, reason="lookup_error", error=str(exc))
            return None
        return stamped if current is not None and current.generation == stamped else None

    def read_current(self, episode_id: str, generation: str) -> Optional[bytes]:
        """The bytes of the episode's current track when it is ``generation``,
        otherwise None (unknown episode, superseded generation, missing file)."""
        current = self._repo.get(episode_id, STATE_CURRENT)
        if current is None or current.generation != generation:
            return None
        try:
            return self._config.file_storage.read_bytes(current.path)
        except FileNotFoundError:
            logger.warning("sync_track_file_missing", episode_id=episode_id, generation=generation, path=current.path)
            return None

    def current(self, episode_id: str) -> Optional[SyncTrackRecord]:
        """The episode's published track record, or None."""
        return self._repo.get(episode_id, STATE_CURRENT)

    def load(self, record: SyncTrackRecord) -> SyncTrack:
        """Read a stored track."""
        return SyncTrack.from_bytes(self._config.file_storage.read_bytes(record.path))

    def _delete_files(self, paths, log) -> None:
        for path in paths:
            try:
                self._config.file_storage.delete(path)
            except Exception as exc:  # noqa: BLE001 — an orphan file is harmless
                log.warning("sync_track_file_delete_failed", path=path, error=str(exc))
