"""Spec #110 §2, §4, §6 — the editor's write side over a real SQLite op log."""

from __future__ import annotations

from datetime import timedelta
from unittest.mock import MagicMock

import pytest

from tests.unit.transcript_edit_fixtures import (
    EPISODE_ID,
    NOW,
    PODCAST_ID,
    SIDECAR_REL,
    make_episode,
    make_raw,
    make_sidecar,
    seed_episode_rows,
    segment_anchor,
    write_transcripts,
)
from thestill.core.facts_manager import FactsManager
from thestill.core.queue_manager import Task, TaskStage, TaskStatus
from thestill.core.transcript_edits import clear_cache
from thestill.models.podcast import Podcast
from thestill.repositories.sqlite_podcast_repository import SqlitePodcastRepository
from thestill.repositories.sqlite_transcript_edit_repository import SqliteTranscriptEditRepository
from thestill.services.abuse.actor import Actor
from thestill.services.transcript_edit_service import IDLE_REFRESH_AFTER, TranscriptEditError, TranscriptEditService
from thestill.utils.file_storage import LocalFileStorage

ADMIN = Actor.web("aaaaaaaa-aaaa-4aaa-8aaa-aaaaaaaaaaaa")


class FakeQueue:
    """Just enough of the queue manager: tasks per episode and the idle guard."""

    def __init__(self) -> None:
        self.tasks: list[Task] = []
        self.added: list = []

    def get_tasks_for_episode(self, episode_id):
        return [t for t in self.tasks if t.episode_id == episode_id]

    def has_active_task_for_episode(self, episode_id):
        return any(
            t.status.value in ("pending", "processing", "retry_scheduled")
            for t in self.get_tasks_for_episode(episode_id)
        )

    def add_task_if_episode_idle(self, episode_id, stage, metadata=None, **_):
        if self.has_active_task_for_episode(episode_id):
            return None
        task = Task(
            id=f"task-{len(self.added) + 1}",
            episode_id=episode_id,
            stage=stage,
            status=TaskStatus.PENDING,
            metadata=metadata or {},
        )
        self.tasks.append(task)
        self.added.append(task)
        return task


@pytest.fixture(autouse=True)
def _fresh_cache():
    clear_cache()
    yield
    clear_cache()


@pytest.fixture
def world(tmp_path):
    storage = tmp_path / "data"
    raw = make_raw()
    sidecar = make_sidecar(raw)
    paths = write_transcripts(storage, raw, sidecar)
    db = str(storage / "podcasts.db")
    SqlitePodcastRepository(db_path=db)
    seed_episode_rows(db)
    edits = SqliteTranscriptEditRepository(db_path=db)
    episode = make_episode()
    podcast = Podcast(id=PODCAST_ID, rss_url="https://x.test/f.xml", title="Show", slug="show", description="")
    repository = MagicMock()
    repository.get_episode.return_value = (podcast, episode)
    repository.get_all.return_value = [podcast.model_copy(update={"episodes": [episode]})]
    queue = FakeQueue()
    audit = MagicMock()
    entities = MagicMock()
    entities.get_podcast_anchors.return_value = {"hosts": [], "recurring": []}
    config = MagicMock()
    config.file_storage = LocalFileStorage(base_path=str(storage))
    config.transcription_provider = "whisper"
    clock = {"now": NOW}
    service = TranscriptEditService(
        repository=repository,
        edits=edits,
        entities=entities,
        queue_manager=queue,
        config=config,
        path_manager=paths,
        file_storage=config.file_storage,
        audit=audit,
        facts_manager=FactsManager(paths),
        clock=lambda: clock["now"],
    )
    return {
        "service": service,
        "edits": edits,
        "queue": queue,
        "audit": audit,
        "entities": entities,
        "sidecar": sidecar,
        "raw": raw,
        "paths": paths,
        "podcast": podcast,
        "clock": clock,
        "repository": repository,
    }


def _rename(world, name="Grace Hopper", **kwargs):
    return world["service"].apply(
        EPISODE_ID,
        actor=ADMIN,
        session_id="s1",
        base_seq=kwargs.pop("base_seq", world["edits"].last_seq(EPISODE_ID)),
        ops=[{"op": "speaker.rename_label", "anchor": {"label": "SPEAKER_01"}, "args": {"name": name}}],
        **kwargs,
    )


def test_apply_appends_rows_with_before_snapshots(world):
    result = _rename(world)
    assert result.last_seq == 1 and [e.op for e in result.applied] == ["speaker.rename_label"]
    stored = world["edits"].list_for_episode(EPISODE_ID)[0]
    assert stored.before == {"name": "SPEAKER_01", "entity_id": None}
    assert stored.actor_kind == "web" and stored.actor_user_id == ADMIN.user_id
    state = world["service"].get_state(EPISODE_ID)
    assert state.effective.transcript.segments[1].speaker == "Grace Hopper"
    assert state.summary_stale_edits == 1  # no completed summarize task is newer


def test_split_records_the_raw_word_it_landed_on(world):
    target = world["sidecar"].segments[3]
    result = world["service"].apply(
        EPISODE_ID,
        actor=ADMIN,
        session_id="s1",
        base_seq=0,
        ops=[{"op": "segment.split", "anchor": segment_anchor(target), "args": {"text_word_index": 4}}],
    )
    assert result.applied[0].args["at"] == {"raw_segment_id": 2, "word_index": 4}
    assert result.applied[0].before["segment"]["text"] == target.text


def test_stale_base_seq_is_refused_with_the_newer_ops(world):
    _rename(world)
    with pytest.raises(TranscriptEditError) as exc:
        _rename(world, name="Someone Else", base_seq=0)
    assert exc.value.kind == "stale"
    assert exc.value.detail["last_seq"] == 1 and [e.seq for e in exc.value.detail["edits"]] == [1]


def test_editing_is_refused_while_a_clean_is_in_flight(world):
    world["queue"].tasks.append(
        Task(id="t", episode_id=EPISODE_ID, stage=TaskStage.CLEAN, status=TaskStatus.PROCESSING)
    )
    with pytest.raises(TranscriptEditError) as exc:
        _rename(world)
    assert exc.value.kind == "busy"
    assert world["service"].get_state(EPISODE_ID).busy is True


def test_entity_branch_in_flight_does_not_block(world):
    world["queue"].tasks.append(
        Task(id="t", episode_id=EPISODE_ID, stage=TaskStage.REINDEX, status=TaskStatus.PROCESSING)
    )
    assert _rename(world).last_seq == 1


def test_an_op_that_does_not_apply_is_rejected_and_nothing_written(world):
    with pytest.raises(TranscriptEditError) as exc:
        world["service"].apply(
            EPISODE_ID,
            actor=ADMIN,
            session_id="s1",
            base_seq=0,
            ops=[
                {"op": "speaker.rename_label", "anchor": {"label": "SPEAKER_01"}, "args": {"name": "Grace Hopper"}},
                {"op": "speaker.rename_label", "anchor": {"label": "SPEAKER_09"}, "args": {"name": "Nobody"}},
            ],
        )
    assert exc.value.kind == "rejected" and exc.value.detail["conflict"]["reason"] == "label_missing"
    # The first op of the batch was written; the refused one was not.
    assert [e.op for e in world["edits"].list_for_episode(EPISODE_ID)] == ["speaker.rename_label"]


def test_malformed_op_is_invalid(world):
    with pytest.raises(TranscriptEditError) as exc:
        world["service"].apply(
            EPISODE_ID,
            actor=ADMIN,
            session_id="s1",
            base_seq=0,
            ops=[{"op": "segment.set_text", "anchor": {}, "args": {}}],
        )
    assert exc.value.kind == "invalid"


def test_revert_then_redo(world):
    rename = _rename(world).applied[0]
    undo = world["service"].revert(EPISODE_ID, rename.id, actor=ADMIN, session_id="s1", base_seq=1).applied[0]
    state = world["service"].get_state(EPISODE_ID)
    assert state.effective.transcript.segments[1].speaker == "SPEAKER_01"
    world["service"].revert(EPISODE_ID, undo.id, actor=ADMIN, session_id="s1", base_seq=2)
    state = world["service"].get_state(EPISODE_ID)
    assert state.effective.transcript.segments[1].speaker == "Grace Hopper"


def test_revert_of_unknown_edit_is_invalid(world):
    with pytest.raises(TranscriptEditError) as exc:
        world["service"].revert(EPISODE_ID, "nope", actor=ADMIN, session_id="s1", base_seq=0)
    assert exc.value.kind == "invalid"


def test_finish_audits_the_session_and_queues_the_entity_refresh(world):
    _rename(world)
    result = world["service"].finish(EPISODE_ID, actor=ADMIN, session_id="s1")
    assert result.refresh == "queued" and result.task_id == "task-1" and result.session_ops == 1
    task = world["queue"].added[0]
    assert task.stage == TaskStage.EXTRACT_ENTITIES
    assert task.metadata["reprocess"]["from_stage"] == "entities" and task.metadata["run_full_pipeline"]
    world["audit"].record.assert_called_once()
    action = world["audit"].record.call_args.args[1]
    assert action == "transcript.edit_session"
    assert world["audit"].record.call_args.kwargs["ops"] == {"speaker.rename_label": 1}
    assert world["edits"].episodes_pending_refresh(idle_before=NOW + timedelta(days=1)) == []


def test_finish_defers_when_the_episode_is_busy_and_the_sweep_catches_up(world):
    _rename(world)
    world["queue"].tasks.append(
        Task(id="t", episode_id=EPISODE_ID, stage=TaskStage.REINDEX, status=TaskStatus.PROCESSING)
    )
    assert world["service"].finish(EPISODE_ID, actor=ADMIN, session_id="s1").refresh == "deferred"
    assert world["service"].sweep_refreshes() == 0  # too recent to count as idle
    world["clock"]["now"] = NOW + IDLE_REFRESH_AFTER + timedelta(minutes=1)
    assert world["service"].sweep_refreshes() == 0  # still busy
    world["queue"].tasks.clear()
    assert world["service"].sweep_refreshes() == 1
    assert world["queue"].added[-1].stage == TaskStage.EXTRACT_ENTITIES
    assert world["service"].sweep_refreshes() == 0  # marked; nothing left


def test_summary_stale_counts_ops_newer_than_the_last_summary(world):
    _rename(world)
    world["queue"].tasks.append(
        Task(
            id="s",
            episode_id=EPISODE_ID,
            stage=TaskStage.SUMMARIZE,
            status=TaskStatus.COMPLETED,
            completed_at=NOW + timedelta(hours=1),
        )
    )
    assert world["service"].get_state(EPISODE_ID).summary_stale_edits == 0
    world["clock"]["now"] = NOW + timedelta(hours=2)
    _rename(world, name="Grace B. Hopper")
    assert world["service"].get_state(EPISODE_ID).summary_stale_edits == 1


def test_save_as_host_writes_facts_and_links_the_entity(world):
    hosts = world["service"].save_as_host(EPISODE_ID, name="Grace Hopper", entity_id="person:grace-hopper", actor=ADMIN)
    assert hosts == ["Grace Hopper"]
    facts = FactsManager(world["paths"]).load_podcast_facts("show")
    assert facts.hosts == ["Grace Hopper"] and facts.podcast_title == "Show"
    world["entities"].set_podcast_hosts.assert_called_once_with(PODCAST_ID, ["person:grace-hopper"])
    assert world["audit"].record.call_args.args[1] == "facts.add_host"
    # Idempotent on the name.
    assert world["service"].save_as_host(EPISODE_ID, name="grace hopper", entity_id=None, actor=ADMIN) == [
        "Grace Hopper"
    ]


def test_revert_by_actor_reverts_live_ops_since(world):
    _rename(world)
    world["clock"]["now"] = NOW + timedelta(hours=1)
    _rename(world, name="Grace B. Hopper")
    reverted = world["service"].revert_by_actor(
        ADMIN.user_id, since=NOW + timedelta(minutes=30), actor=Actor.cli(), reason="test"
    )
    assert reverted == 1
    state = world["service"].get_state(EPISODE_ID)
    assert state.effective.transcript.segments[1].speaker == "Grace Hopper"
    assert world["audit"].record.call_args.args[1] == "transcript.revert_bulk"


def test_conflicts_and_speaker_stats(world):
    _rename(world)
    assert world["service"].conflicts() == []
    recleaned = make_sidecar(world["raw"])
    recleaned.segments[1].source_segment_ids = [1]  # unchanged; label op needs only the raw hash
    write_transcripts(world["paths"].storage_path, world["raw"], recleaned)
    stats = world["service"].speaker_stats()
    assert (stats.episodes, stats.episodes_with_unnamed, stats.unnamed_segments, stats.edited_episodes) == (1, 0, 0, 1)
    world["paths"].clean_transcript_file(SIDECAR_REL).unlink()
    with pytest.raises(TranscriptEditError) as exc:
        world["service"].get_state(EPISODE_ID)
    assert exc.value.kind == "no_transcript"
