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

"""The transcript editor's write side (spec #110 §2, §4, §6, §7).

Validates each op against the episode's current effective transcript,
appends it to the op log, and queues the downstream refresh (spec #106
``--from entities``) when a session finishes or goes idle. Shared by the
``/api/episodes/{id}/transcript/edits`` routes and ``thestill transcript``.

Rows are never updated or deleted here: undo is a ``revert`` row, and a
bulk revert is a loop of them.
"""

from __future__ import annotations

import uuid
from dataclasses import dataclass, field
from datetime import datetime, timedelta
from pathlib import Path
from typing import TYPE_CHECKING, Any, Callable, Dict, List, Literal, Optional, Sequence, Tuple

from pydantic import ValidationError
from structlog import get_logger

from ..core.queue_manager import TaskStage
from ..core.transcript_edits import (
    EffectiveTranscript,
    _Replayer,
    live_edits,
    load_effective_transcript,
    raw_word_ref_for_split,
    unnamed_content_segments,
)
from ..models.facts import PodcastFacts, strip_role_annotation
from ..models.podcast import Episode, Podcast
from ..models.transcript import Transcript
from ..models.transcript_edits import STRUCTURAL_OPS, SegmentSplit, TranscriptEdit, parse_op
from ..utils.datetime_utils import now_utc
from .abuse.actor import Actor
from .episode_reprocess import ReprocessRejected, queue_reprocess

if TYPE_CHECKING:
    from ..core.facts_manager import FactsManager
    from ..repositories.entity_repository import EntityRepository
    from ..repositories.transcript_edit_repository import TranscriptEditRepository
    from ..utils.config import Config
    from ..utils.file_storage import FileStorage
    from ..utils.path_manager import PathManager
    from .abuse.audit_service import AuditLog

logger = get_logger(__name__)

#: A session that has gone quiet this long gets its refresh queued by the
#: periodic sweep, so a closed tab never leaves an episode half-applied (§4).
IDLE_REFRESH_AFTER = timedelta(minutes=15)

#: Stages whose in-flight task makes editing unsafe: they rewrite the raw
#: or cleaned transcript the anchors point at. The entity branch (our own
#: refresh) does not block an editor.
_BLOCKING_STAGES = frozenset({TaskStage.DOWNLOAD, TaskStage.DOWNSAMPLE, TaskStage.TRANSCRIBE, TaskStage.CLEAN})
_ACTIVE_STATUSES = frozenset({"pending", "processing", "retry_scheduled"})


class TranscriptEditError(Exception):
    """The service refused; ``kind`` maps to the HTTP status."""

    def __init__(
        self,
        kind: Literal["not_found", "no_transcript", "busy", "stale", "rejected", "invalid"],
        message: str,
        **detail: Any,
    ) -> None:
        super().__init__(message)
        self.kind = kind
        self.detail = detail


@dataclass
class EditorState:
    episode: Episode
    podcast: Podcast
    effective: EffectiveTranscript
    busy: bool
    summary_stale_edits: int
    # Spec #110 §5.6 — our downloaded copy of the audio is still on disk, so
    # the editor can play the file the timestamps were made from.
    local_audio: bool = False


@dataclass
class ApplyResult:
    applied: List[TranscriptEdit]
    last_seq: int


@dataclass
class FinishResult:
    session_ops: int
    refresh: Literal["queued", "deferred", "nothing"]
    task_id: Optional[str] = None


@dataclass
class ConflictRow:
    episode_id: str
    podcast_title: str
    episode_title: str
    conflicts: List[Dict[str, Any]] = field(default_factory=list)


@dataclass
class SpeakerStats:
    episodes: int
    episodes_with_unnamed: int
    unnamed_segments: int
    edited_episodes: int


class TranscriptEditService:
    def __init__(
        self,
        *,
        repository,
        edits: "TranscriptEditRepository",
        entities: Optional["EntityRepository"],
        queue_manager,
        config: "Config",
        path_manager: "PathManager",
        file_storage: Optional["FileStorage"],
        audit: Optional["AuditLog"],
        facts_manager: Optional["FactsManager"] = None,
        clock: Callable[[], datetime] = now_utc,
    ) -> None:
        self._repo = repository
        self._edits = edits
        self._entities = entities
        self._queue = queue_manager
        self._config = config
        self._paths = path_manager
        self._storage = file_storage
        self._audit = audit
        self._facts = facts_manager
        self._clock = clock

    # -- reads ---------------------------------------------------------------

    def _episode(self, episode_id: str) -> Tuple[Podcast, Episode]:
        found = self._repo.get_episode(episode_id)
        if not found:
            raise TranscriptEditError("not_found", f"Episode not found: {episode_id}")
        return found

    def _load(self, episode: Episode, *, include_labels: bool = True) -> EffectiveTranscript:
        effective = load_effective_transcript(
            episode,
            edits=self._edits,
            path_manager=self._paths,
            file_storage=self._storage,
            include_labels=include_labels,
        )
        if effective is None:
            raise TranscriptEditError("no_transcript", "Episode has no segmented transcript to edit")
        return effective

    def is_busy(self, episode_id: str) -> bool:
        """A transcribe or clean (or the audio stages before them) is in flight."""
        for task in self._queue.get_tasks_for_episode(episode_id):
            status = task.status.value if hasattr(task.status, "value") else str(task.status)
            if status in _ACTIVE_STATUSES and task.stage in _BLOCKING_STAGES:
                return True
        return False

    def get_state(self, episode_id: str) -> EditorState:
        podcast, episode = self._episode(episode_id)
        effective = self._load(episode)
        return EditorState(
            episode=episode,
            podcast=podcast,
            effective=effective,
            busy=self.is_busy(episode.id),
            summary_stale_edits=self._summary_stale_edits(episode, effective),
            local_audio=self.local_audio_path(episode) is not None,
        )

    def local_audio_path(self, episode: Episode) -> Optional[Path]:
        """The episode's ``original_audio`` file when it is still on local disk
        (§5.6). ``delete_audio_after_processing`` removes it after transcription
        and S3 deployments never hold it locally, so ``None`` is the common case."""
        if not episode.audio_path:
            return None
        # Serve only what lives under original_audio: the stored path is
        # ours, but a stray absolute path, ``..`` or a symlink out of the
        # directory must never turn this route into a file reader.
        root = self._paths.original_audio_dir().resolve()
        try:
            path = self._paths.original_audio_file(episode.audio_path).resolve(strict=True)
        except (OSError, RuntimeError, ValueError):
            return None
        if not path.is_file() or not path.is_relative_to(root):
            logger.warning("transcript_edit_audio_outside_root", episode_id=episode.id)
            return None
        return path

    def _summary_stale_edits(self, episode: Episode, effective: EffectiveTranscript) -> int:
        """Changes to the transcript since the last completed summary (§4 "Stale badge").

        Counts the live rows written after the summary that change what it
        saw: an op that is still live, and an undo (a live revert) of an op
        the summary included. An op undone again after the summary cancels
        out; a redo of an undo the summary saw counts as a change. Splits
        and merges never count: citations re-resolve on their own.
        """
        structural = {"segment.split", "segment.merge"}
        if not episode.summary_path or not effective.edits:
            return 0
        summarized_at: Optional[datetime] = None
        for task in self._queue.get_tasks_for_episode(episode.id):
            status = task.status.value if hasattr(task.status, "value") else str(task.status)
            if task.stage == TaskStage.SUMMARIZE and status == "completed" and task.completed_at:
                if summarized_at is None or task.completed_at > summarized_at:
                    summarized_at = task.completed_at
        if summarized_at is not None and summarized_at.tzinfo is None:
            summarized_at = summarized_at.replace(tzinfo=effective.edits[0].created_at.tzinfo)

        # The ops the summary saw are the ones applied when it ran: replay
        # the log as it stood then and compare with what applies now. Any
        # undo and redo chain reduces to the two sets, and a row that
        # conflicts (then or now) changed nothing at that time.
        now = {e.id for e in effective.applied if e.op not in structural}
        seen = [e for e in effective.edits if summarized_at is not None and e.created_at <= summarized_at]
        then = {e.id for e in self._applied_rows(episode, effective, seen) if e.op not in structural}
        return len(then ^ now)

    def _applied_rows(
        self, episode: Episode, effective: EffectiveTranscript, rows: List[TranscriptEdit]
    ) -> List[TranscriptEdit]:
        """The rows of ``rows`` that apply when that log is replayed alone."""
        if not rows:
            return []
        replayer = self._replayer_for(episode, effective, [], rows=rows)
        return replayer.applied_rows

    # -- writes --------------------------------------------------------------

    def apply(
        self,
        episode_id: str,
        *,
        actor: Actor,
        session_id: str,
        base_seq: int,
        ops: Sequence[Dict[str, Any]],
        report_id: Optional[str] = None,
    ) -> ApplyResult:
        """Validate and append ``ops`` in order against base ``base_seq``.

        Each op must apply cleanly to the effective transcript as it stands
        after the ops before it; the first that does not is refused and
        nothing after it is written (the ones before it are).
        """
        if not ops:
            raise TranscriptEditError("invalid", "No ops to apply")
        _podcast, episode = self._episode(episode_id)
        if self.is_busy(episode.id):
            raise TranscriptEditError("busy", "This episode already has a task queued or running")
        effective = self._load(episode)
        if effective.last_seq != base_seq:
            raise TranscriptEditError(
                "stale",
                "The transcript changed since you loaded it",
                last_seq=effective.last_seq,
                edits=[e for e in effective.edits if e.seq > base_seq],
            )
        if effective.raw_sha256 is None:
            raise TranscriptEditError("no_transcript", "Episode has no raw transcript to anchor edits to")

        # Every op is validated against the batch so far before anything is
        # written: a bad op in the middle refuses the whole batch instead of
        # leaving the ones before it in the log.
        rows: List[TranscriptEdit] = []
        replayer = self._replayer_for(episode, effective, rows)
        last_seq = effective.last_seq
        for index, payload in enumerate(ops):
            try:
                op = parse_op(dict(payload))
            except ValidationError as exc:
                raise TranscriptEditError("invalid", f"op {index}: {exc.errors()[0]['msg']}", index=index) from exc
            if op.op in STRUCTURAL_OPS and op.op == "segment.adjust_boundary":
                raise TranscriptEditError("invalid", "segment.adjust_boundary is not available yet", index=index)
            if isinstance(op, SegmentSplit) and op.args.at is None:
                self._fill_split_ref(op, replayer)
            row = TranscriptEdit(
                id=str(uuid.uuid4()),
                episode_id=episode.id,
                seq=last_seq + 1,
                op=op.op,
                raw_sha256=effective.raw_sha256,
                anchor=op.anchor.model_dump(),
                args=op.args.model_dump(),
                reverts=op.anchor.edit_id if op.op == "revert" else None,
                session_id=session_id,
                report_id=report_id,
                actor_user_id=actor.user_id,
                actor_kind=actor.kind,
                created_at=self._clock(),
            )
            if op.op == "revert":
                before = self._check_revert(row, effective, rows)
            else:
                conflict, before = replayer.apply(row)
                if conflict is not None:
                    raise TranscriptEditError(
                        "rejected",
                        f"op {index} does not apply: {conflict.reason}",
                        index=index,
                        conflict=conflict.to_dict(),
                    )
            rows.append(row.model_copy(update={"before": before}))
            last_seq += 1
            if op.op == "revert":
                # A revert changes the transcript the next op in the batch
                # is validated against; replay the log as it would stand.
                replayer = self._replayer_for(episode, effective, rows)
        applied: List[TranscriptEdit] = []
        last_seq = effective.last_seq
        for row in rows:
            stored = self._edits.append(row, expected_last_seq=last_seq)
            if stored is None:
                raise TranscriptEditError(
                    "stale", "Another editor wrote to this transcript", last_seq=self._edits.last_seq(episode.id)
                )
            applied.append(stored)
            last_seq = stored.seq
        logger.info(
            "transcript_edits_applied",
            episode_id=episode.id,
            session_id=session_id,
            ops=[e.op for e in applied],
            last_seq=last_seq,
            actor=actor.kind,
            user_id=actor.user_id,
        )
        return ApplyResult(applied=applied, last_seq=last_seq)

    def _replayer_for(
        self,
        episode: Episode,
        effective: EffectiveTranscript,
        pending: Sequence[TranscriptEdit],
        *,
        rows: Optional[List[TranscriptEdit]] = None,
    ) -> _Replayer:
        """A replayer positioned after the stored log (or ``rows`` in its
        place) plus ``pending`` rows written in this batch, for validating
        the next op. ``applied_rows`` lists the rows that applied."""
        raw = self._read_raw(episode)
        base = load_effective_transcript(episode, edits=None, path_manager=self._paths, file_storage=self._storage)
        assert base is not None  # _load succeeded a moment ago
        replayer = _Replayer(base.transcript, raw, effective.raw_sha256)
        replayer.applied_rows = []
        log = effective.edits if rows is None else rows
        for row in live_edits([*log, *pending])[0]:
            conflict, _before = replayer.apply(row)
            if conflict is None:
                replayer.applied_rows.append(row)
        return replayer

    def _read_raw(self, episode: Episode) -> Optional[Transcript]:
        if not episode.raw_transcript_path:
            return None
        path = self._paths.raw_transcript_file(episode.raw_transcript_path)
        try:
            text = (
                self._storage.read_text(self._paths.to_relative(path))
                if self._storage is not None
                else path.read_text(encoding="utf-8")
            )
        except FileNotFoundError:
            return None
        return Transcript.model_validate_json(text)

    @staticmethod
    def _fill_split_ref(op: SegmentSplit, replayer: _Replayer) -> None:
        index, _conflict = replayer._find(op.anchor)  # pylint: disable=protected-access
        if index is None:
            return
        segment = replayer.segments[index]
        op.args.at = raw_word_ref_for_split(segment, op.args.text_word_index, replayer.raw_by_id, replayer.raw_order)

    def _check_revert(
        self, row: TranscriptEdit, effective: EffectiveTranscript, pending: List[TranscriptEdit]
    ) -> Dict[str, Any]:
        target_id = row.reverts or ""
        log = [*effective.edits, *pending]
        target = next((e for e in log if e.id == target_id), None)
        if target is None or target.episode_id != row.episode_id:
            raise TranscriptEditError("invalid", f"No edit {target_id!r} on this episode")
        _live, reverted = live_edits(log)
        if target.id in reverted:
            # Already undone (or an undo already redone): a second revert
            # would pin the row dead against a later redo.
            raise TranscriptEditError("rejected", f"Edit {target_id!r} is already reverted", conflict=None)
        return {"op": target.op, "seq": target.seq}

    def revert(self, episode_id: str, edit_id: str, *, actor: Actor, session_id: str, base_seq: int) -> ApplyResult:
        return self.apply(
            episode_id,
            actor=actor,
            session_id=session_id,
            base_seq=base_seq,
            ops=[{"op": "revert", "anchor": {"edit_id": edit_id}, "args": {}}],
        )

    def revert_by_actor(
        self, actor_user_id: str, *, since: datetime, actor: Actor, reason: Optional[str] = None
    ) -> int:
        """Revert every live op one user wrote since ``since`` (§2). One audit row."""
        rows = [e for e in self._edits.list_by_actor(actor_user_id, since=since) if e.op != "revert"]
        reverted = 0
        session_id = str(uuid.uuid4())
        by_episode: Dict[str, List[TranscriptEdit]] = {}
        for row in rows:
            by_episode.setdefault(row.episode_id, []).append(row)
        for episode_id, episode_rows in by_episode.items():
            effective = self._load(self._episode(episode_id)[1], include_labels=False)
            live_ids = {e.id for e in effective.live_edits}
            # Newest first, so a split or merge is undone after the ops that
            # anchored to its children.
            for row in sorted(episode_rows, key=lambda e: e.seq, reverse=True):
                if row.id not in live_ids:
                    continue
                self.revert(
                    episode_id, row.id, actor=actor, session_id=session_id, base_seq=self._edits.last_seq(episode_id)
                )
                reverted += 1
        if self._audit is not None:
            self._audit.record(
                actor,
                "transcript.revert_bulk",
                target_user_id=actor_user_id,
                since=since.isoformat(),
                reverted=reverted,
                episodes=sorted(by_episode),
                reason=reason,
            )
        return reverted

    # -- refresh ---------------------------------------------------------------

    def finish(self, episode_id: str, *, actor: Actor, session_id: str) -> FinishResult:
        """Close an editing session: audit it and queue the downstream refresh."""
        _podcast, episode = self._episode(episode_id)
        rows = [e for e in self._edits.list_for_episode(episode.id) if e.session_id == session_id]
        if self._audit is not None and rows:
            counts: Dict[str, int] = {}
            for row in rows:
                counts[row.op] = counts.get(row.op, 0) + 1
            self._audit.record(
                actor,
                "transcript.edit_session",
                episode_id=episode.id,
                session_id=session_id,
                ops=counts,
                report_ids=sorted({r.report_id for r in rows if r.report_id}),
            )
        last_seq = self._edits.last_seq(episode.id)
        if last_seq == 0:
            return FinishResult(session_ops=0, refresh="nothing")
        task_id = self._queue_refresh(episode.id, last_seq, requested_by=actor.user_id)
        return FinishResult(session_ops=len(rows), refresh="queued" if task_id else "deferred", task_id=task_id)

    def _queue_refresh(self, episode_id: str, last_seq: int, *, requested_by: Optional[str]) -> Optional[str]:
        try:
            queued = queue_reprocess(
                repository=self._repo,
                queue_manager=self._queue,
                config=self._config,
                episode_id=episode_id,
                from_stage="entities",
                requested_by=requested_by,
            )
        except ReprocessRejected as rejected:
            # Busy (the previous refresh still running) or no sidecar: the
            # sweep tries again once the episode is idle.
            logger.info("transcript_edits_refresh_deferred", episode_id=episode_id, reason=rejected.kind)
            return None
        # Marked only after the task row exists (spec #42 "checkpoint before durability").
        self._edits.mark_refreshed(episode_id, seq=last_seq, at=self._clock())
        return queued.task_id

    def sweep_refreshes(self) -> int:
        """Queue the refresh for every idle episode whose newest op is newer
        than its last refresh. Runs periodically; safe to run anywhere."""
        queued = 0
        for episode_id, last_seq in self._edits.episodes_pending_refresh(
            idle_before=self._clock() - IDLE_REFRESH_AFTER
        ):
            if self._queue_refresh(episode_id, last_seq, requested_by=None):
                queued += 1
        if queued:
            logger.info("transcript_edits_refresh_swept", queued=queued)
        return queued

    # -- speakers ----------------------------------------------------------------

    def save_as_host(self, episode_id: str, *, name: str, entity_id: Optional[str], actor: Actor) -> List[str]:
        """Append ``name`` to the podcast's facts hosts (so future cleans name
        them) and, with an entity, to its host anchors. Audited."""
        name = name.strip()
        if not name or self._facts is None:
            raise TranscriptEditError("invalid", "A host name is required")
        podcast, _episode = self._episode(episode_id)
        facts = self._facts.load_podcast_facts(podcast.slug) or PodcastFacts(podcast_title=podcast.title)
        known = {strip_role_annotation(h.split(" - ", 1)[0]).strip().lower() for h in facts.hosts}
        added = name.lower() not in known
        if added:
            facts.hosts.append(name)
            self._facts.save_podcast_facts(podcast.slug, facts)
        if entity_id and self._entities is not None:
            anchors = self._entities.get_podcast_anchors(podcast.id)
            hosts = list(anchors.get("hosts", []))
            if entity_id not in hosts:
                hosts.append(entity_id)
                self._entities.set_podcast_hosts(podcast.id, hosts)
        if self._audit is not None:
            self._audit.record(
                actor, "facts.add_host", podcast_id=podcast.id, name=name, entity_id=entity_id, added=added
            )
        return list(facts.hosts)

    def search_people(self, query: str, *, limit: int = 8) -> List[Dict[str, Any]]:
        """Person entities for the rename-or-link picker."""
        if self._entities is None or not query.strip():
            return []
        hits = self._entities.search_entities_by_prefix(query.strip(), types=("person",), limit_per_type=limit)
        return [
            {"id": h.id, "canonical_name": h.canonical_name, "matched_alias": getattr(h, "matched_alias", None)}
            for h in hits
        ]

    # -- operator views -----------------------------------------------------------

    def conflicts(self, *, podcast_id: Optional[str] = None) -> List[ConflictRow]:
        out: List[ConflictRow] = []
        for episode_id, _last_seq, _newest in self._edits.episodes_with_edits():
            found = self._repo.get_episode(episode_id)
            if not found:
                continue
            podcast, episode = found
            if podcast_id and podcast.id != podcast_id and podcast.slug != podcast_id:
                continue
            effective = load_effective_transcript(
                episode, edits=self._edits, path_manager=self._paths, file_storage=self._storage
            )
            if effective is None or not effective.conflicts:
                continue
            out.append(
                ConflictRow(
                    episode_id=episode.id,
                    podcast_title=podcast.title,
                    episode_title=episode.title,
                    conflicts=[c.to_dict() for c in effective.conflicts],
                )
            )
        return out

    def speaker_stats(self) -> SpeakerStats:
        """The unnamed-speaker share across episodes with a sidecar (success metric)."""
        edited = {episode_id for episode_id, _seq, _at in self._edits.episodes_with_edits()}
        episodes = episodes_with_unnamed = unnamed = 0
        for podcast in self._repo.get_all():
            for episode in podcast.episodes or []:
                if not episode.clean_transcript_json_path:
                    continue
                effective = load_effective_transcript(
                    episode, edits=self._edits, path_manager=self._paths, file_storage=self._storage
                )
                if effective is None:
                    continue
                episodes += 1
                count = unnamed_content_segments(effective.transcript)
                if count:
                    episodes_with_unnamed += 1
                    unnamed += count
        return SpeakerStats(
            episodes=episodes,
            episodes_with_unnamed=episodes_with_unnamed,
            unnamed_segments=unnamed,
            edited_episodes=len(edited),
        )
