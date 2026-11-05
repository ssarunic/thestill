"""Adds run one at a time server-wide; a caller reads its own add by task_id."""

from types import SimpleNamespace

import pytest
from fastapi import HTTPException

from thestill.web.routes.api_commands import get_add_podcast_status
from thestill.web.task_manager import TaskManager, TaskType


def _state(manager: TaskManager) -> SimpleNamespace:
    return SimpleNamespace(task_manager=manager)


def test_task_id_reads_its_own_add_after_the_next_one_started():
    manager = TaskManager()
    first = manager.start_task(TaskType.ADD_PODCAST)
    manager.fail_task(TaskType.ADD_PODCAST, "No public feed")
    second = manager.start_task(TaskType.ADD_PODCAST)
    assert first is not None and second is not None
    assert first.task_id != second.task_id

    mine = get_add_podcast_status(task_id=first.task_id, state=_state(manager))
    latest = get_add_podcast_status(task_id=None, state=_state(manager))

    assert (mine.task_id, mine.status, mine.error) == (first.task_id, "failed", "No public feed")
    assert (latest.task_id, latest.status) == (second.task_id, "running")


def test_unknown_task_id_is_404():
    with pytest.raises(HTTPException) as exc:
        get_add_podcast_status(task_id="nope", state=_state(TaskManager()))
    assert exc.value.status_code == 404


def test_task_id_of_another_task_type_is_404():
    manager = TaskManager()
    refresh = manager.start_task(TaskType.REFRESH)
    assert refresh is not None
    with pytest.raises(HTTPException) as exc:
        get_add_podcast_status(task_id=refresh.task_id, state=_state(manager))
    assert exc.value.status_code == 404


def test_history_keeps_only_the_latest_runs():
    manager = TaskManager()
    ids = []
    for _ in range(TaskManager.HISTORY_SIZE + 1):
        task = manager.start_task(TaskType.ADD_PODCAST)
        assert task is not None
        ids.append(task.task_id)
        manager.complete_task(TaskType.ADD_PODCAST)

    assert manager.get_task_by_id(ids[0]) is None
    assert manager.get_task_by_id(ids[-1]) is not None
