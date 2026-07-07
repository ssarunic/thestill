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

"""Tests for ``NarrationRunner`` (spec #33 Phase 3, rekeyed to briefings)."""

import json
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Dict, List, Optional

import pytest

from thestill.models.briefing import Briefing
from thestill.models.podcast import Episode, Podcast
from thestill.services.narration import NarrationGenerator, NarrationRunner, NarrationRunnerError, QuoteSelector
from thestill.services.narration.models import ScriptBlock
from thestill.services.narration.script_writer import ScriptResult
from thestill.utils.path_manager import PathManager

# Re-use the test fixtures already defined for the generator tests.
from .test_narration_generator import (
    _good_turn,
    _make_episode,
    _make_podcast,
    _StaticLoader,
    _StubClusterer,
    _StubScriptWriter,
)

_WINDOW_START = datetime(2026, 5, 5, 6, 0, tzinfo=timezone.utc)
_WINDOW_END = datetime(2026, 5, 6, 6, 0, tzinfo=timezone.utc)


class _BriefingRepo:
    """In-memory stub of ``BriefingRepository.get`` for the runner tests."""

    def __init__(self, briefings: Optional[List[Briefing]] = None) -> None:
        self._by_id = {b.id: b for b in briefings or []}

    def get(self, briefing_id: str) -> Optional[Briefing]:
        return self._by_id.get(briefing_id)


class _InboxRepo:
    """In-memory stub of ``InboxRepository.list_episode_ids_in_window``.

    Keyed per briefing window start so multi-briefing tests can vary the
    episode set; falls back to a single default list.
    """

    def __init__(self, episode_ids: List[str]) -> None:
        self._episode_ids = episode_ids
        self.calls: List[dict] = []

    def list_episode_ids_in_window(self, user_id, *, since, until, states=None) -> List[str]:
        self.calls.append({"user_id": user_id, "since": since, "until": until, "states": states})
        return list(self._episode_ids)


class _PodcastRepo:
    """In-memory stub of ``PodcastRepository.get_episode``."""

    def __init__(self, episodes: Dict[str, tuple[Podcast, Episode]]) -> None:
        self._episodes = episodes

    def get_episode(self, episode_id: str):
        return self._episodes.get(episode_id)


@pytest.fixture
def storage(tmp_path: Path) -> PathManager:
    data_root = tmp_path / "data"
    data_root.mkdir()
    pm = PathManager(storage_path=str(data_root))
    pm.ensure_directories_exist()
    return pm


@pytest.fixture
def file_storage(storage: PathManager):
    """Spec #35 — LocalFileStorage rooted at the data root."""
    from thestill.utils.file_storage import LocalFileStorage

    return LocalFileStorage(base_path=str(storage.storage_path))


def _briefing(*, id_: str, episode_count: int = 1) -> Briefing:
    return Briefing(
        id=id_,
        user_id="user-1",
        cursor_from=_WINDOW_START,
        cursor_to=_WINDOW_END,
        episode_count=episode_count,
        created_at=_WINDOW_END,
    )


def _generator(storage: PathManager, file_storage, plan, script_blocks):
    loader = _StaticLoader(
        turns_by_episode={
            "e1": [_good_turn(episode_id="e1", segment_id=1, start=60.0, speaker="Alex Anchor")],
        }
    )
    return NarrationGenerator(
        path_manager=storage,
        file_storage=file_storage,
        loader=loader,
        selector=QuoteSelector(),
        clusterer=_StubClusterer(plan),
        script_writer=_StubScriptWriter(ScriptResult(blocks=tuple(script_blocks), failures=(), raw_word_count=80)),
    )


def _good_blocks() -> list[ScriptBlock]:
    long_text = "On Test Podcast, the host argues the bar shifted. " "Two takes from the same morning. " * 5
    return [
        ScriptBlock(kind="narration", section="opener", text="Today's lead."),
        ScriptBlock(kind="narration", section="segment-1", text=long_text),
        ScriptBlock(kind="quote", section="segment-1", quote_id="q1", duration_seconds=12.0),
        ScriptBlock(kind="narration", section="signoff", text="Bye."),
    ]


def _empty_plan():
    from thestill.services.narration.models import ThemePlan

    return ThemePlan(segments=(), tail_ids=())


def test_runner_resolves_briefing_and_writes_artefacts(storage: PathManager, file_storage) -> None:
    from thestill.services.narration.models import Segment, ThemePlan

    podcast = _make_podcast(id_="p1", title="Test Podcast", slug="test-podcast")
    ep1 = _make_episode(id_="e1", podcast_id="p1", slug="ep-one", title="Lead Episode")
    plan = ThemePlan(
        segments=(Segment(theme="Lead", angle="Lead angle", episode_ids=("e1",), rank=1),),
        tail_ids=(),
    )
    briefing = _briefing(id_="briefing-001")
    runner = NarrationRunner(
        generator=_generator(storage, file_storage, plan, _good_blocks()),
        briefing_repository=_BriefingRepo([briefing]),
        inbox_repository=_InboxRepo(["e1"]),
        podcast_repository=_PodcastRepo({"e1": (podcast, ep1)}),
    )
    run = runner.run(briefing_id="briefing-001", target_duration_seconds=300, slug="morning")
    assert run.briefing_id == "briefing-001"
    assert run.narration_id == "briefing-001-morning"
    assert run.json_path is not None and run.json_path.exists()
    assert run.json_path.name == "briefing-001-morning.json"
    assert run.markdown_path is not None and run.markdown_path.exists()
    assert run.markdown_path.name == "briefing-001-morning.md"

    payload = json.loads(run.json_path.read_text(encoding="utf-8"))
    assert payload["mode"] == "narrated"
    assert payload["episodes_covered"] == ["e1"]


def test_runner_resolves_episodes_from_briefing_cursor_window(storage: PathManager, file_storage) -> None:
    """The episode set comes from the briefing's inbox cursor window,
    including ``read`` rows (an episode read after generation still
    narrates) but never ``dismissed``.
    """
    from thestill.services.narration.models import Segment, ThemePlan

    podcast = _make_podcast(id_="p1", title="Test Podcast", slug="test-podcast")
    ep1 = _make_episode(id_="e1", podcast_id="p1", slug="ep-one")
    plan = ThemePlan(
        segments=(Segment(theme="Lead", angle="ang", episode_ids=("e1",), rank=1),),
        tail_ids=(),
    )
    inbox = _InboxRepo(["e1"])
    runner = NarrationRunner(
        generator=_generator(storage, file_storage, plan, _good_blocks()),
        briefing_repository=_BriefingRepo([_briefing(id_="b1")]),
        inbox_repository=inbox,
        podcast_repository=_PodcastRepo({"e1": (podcast, ep1)}),
    )
    runner.run(briefing_id="b1", target_duration_seconds=300)

    assert len(inbox.calls) == 1
    call = inbox.calls[0]
    assert call["user_id"] == "user-1"
    assert call["since"] == _WINDOW_START
    assert call["until"] == _WINDOW_END
    assert call["states"] == ("unread", "saved", "read")


def test_runner_captures_latency_ms_and_briefing_id(storage: PathManager, file_storage) -> None:
    """Phase 5 instrumentation: ``content.latency_ms`` is populated by
    the runner around ``generate()``, and ``briefing_id`` is persisted in
    the JSON header so the dashboard tile doesn't have to parse the
    filename to recover the join key.
    """
    from thestill.services.narration.models import Segment, ThemePlan

    podcast = _make_podcast(id_="p1", title="Test Podcast", slug="test-podcast")
    ep1 = _make_episode(id_="e1", podcast_id="p1", slug="ep-one")
    plan = ThemePlan(
        segments=(Segment(theme="Lead", angle="ang", episode_ids=("e1",), rank=1),),
        tail_ids=(),
    )
    briefing = _briefing(id_="briefing-uuid-001")
    runner = NarrationRunner(
        generator=_generator(storage, file_storage, plan, _good_blocks()),
        briefing_repository=_BriefingRepo([briefing]),
        inbox_repository=_InboxRepo(["e1"]),
        podcast_repository=_PodcastRepo({"e1": (podcast, ep1)}),
    )
    run = runner.run(briefing_id="briefing-uuid-001", target_duration_seconds=300, slug="medium")

    assert run.content.latency_ms is not None
    assert isinstance(run.content.latency_ms, int)
    assert run.content.latency_ms >= 0

    payload = json.loads(run.json_path.read_text(encoding="utf-8"))
    assert payload["latency_ms"] == run.content.latency_ms
    assert payload["briefing_id"] == "briefing-uuid-001"
    assert payload["slug"] == "medium"


def test_runner_raises_when_unknown_briefing_id(storage: PathManager, file_storage) -> None:
    runner = NarrationRunner(
        generator=_generator(storage, file_storage, _empty_plan(), _good_blocks()),
        briefing_repository=_BriefingRepo([_briefing(id_="known")]),
        inbox_repository=_InboxRepo([]),
        podcast_repository=_PodcastRepo({}),
    )
    with pytest.raises(NarrationRunnerError):
        runner.run(briefing_id="nope")


def test_runner_raises_when_all_episodes_missing(storage: PathManager, file_storage) -> None:
    runner = NarrationRunner(
        generator=_generator(storage, file_storage, _empty_plan(), _good_blocks()),
        briefing_repository=_BriefingRepo([_briefing(id_="b1", episode_count=2)]),
        inbox_repository=_InboxRepo(["gone-1", "gone-2"]),
        podcast_repository=_PodcastRepo({}),
    )
    with pytest.raises(NarrationRunnerError):
        runner.run(briefing_id="b1")
