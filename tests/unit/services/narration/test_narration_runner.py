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

"""Tests for ``NarrationRunner`` (spec #33 Phase 3)."""

import json
from datetime import datetime, timezone
from pathlib import Path
from typing import List, Optional

import pytest
from pydantic import HttpUrl

from thestill.models.digest import Digest
from thestill.models.podcast import Episode, Podcast
from thestill.services.narration import (
    NarrationConfig,
    NarrationGenerator,
    NarrationRunner,
    NarrationRunnerError,
    QuoteSelector,
)
from thestill.services.narration.models import ScriptBlock
from thestill.services.narration.script_writer import ScriptResult
from thestill.utils.path_manager import PathManager

# Re-use the test fixtures already defined for the generator tests.
from .test_narration_generator import (
    _StaticLoader,
    _StubClusterer,
    _StubScriptWriter,
    _good_turn,
    _make_episode,
    _make_podcast,
)


class _DigestRepo:
    """In-memory stub of ``DigestRepository`` for the runner tests."""

    def __init__(self, digests: Optional[List[Digest]] = None) -> None:
        self._by_id = {d.id: d for d in digests or []}
        self._latest = digests[-1] if digests else None

    def get_by_id(self, digest_id: str) -> Optional[Digest]:
        return self._by_id.get(digest_id)

    def get_latest(self) -> Optional[Digest]:
        return self._latest


class _PodcastRepo:
    """In-memory stub of ``PodcastRepository.get_episode``."""

    def __init__(self, episodes: dict[str, tuple[Podcast, Episode]]) -> None:
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


def _digest(*, id_: str, episode_ids: list[str]) -> Digest:
    return Digest(
        id=id_,
        user_id="user-1",
        period_start=datetime(2026, 5, 6, tzinfo=timezone.utc),
        period_end=datetime(2026, 5, 6, tzinfo=timezone.utc),
        episode_ids=episode_ids,
        episodes_total=len(episode_ids),
    )


def _generator(storage: PathManager, plan, script_blocks):
    podcast = _make_podcast(id_="p1", title="Test Podcast", slug="test-podcast")  # noqa: F841

    loader = _StaticLoader(
        turns_by_episode={
            "e1": [_good_turn(episode_id="e1", segment_id=1, start=60.0, speaker="Alex Anchor")],
        }
    )
    return NarrationGenerator(
        path_manager=storage,
        loader=loader,
        selector=QuoteSelector(),
        clusterer=_StubClusterer(plan),
        script_writer=_StubScriptWriter(
            ScriptResult(blocks=tuple(script_blocks), failures=(), raw_word_count=80)
        ),
    )


def _good_blocks() -> list[ScriptBlock]:
    long_text = (
        "On Test Podcast, the host argues the bar shifted. "
        "Two takes from the same morning. " * 5
    )
    return [
        ScriptBlock(kind="narration", section="opener", text="Today's lead."),
        ScriptBlock(kind="narration", section="segment-1", text=long_text),
        ScriptBlock(kind="quote", section="segment-1", quote_id="q1", duration_seconds=12.0),
        ScriptBlock(kind="narration", section="signoff", text="Bye."),
    ]


def test_runner_resolves_latest_digest_and_writes_artefacts(storage: PathManager) -> None:
    from thestill.services.narration.models import Segment, ThemePlan

    podcast = _make_podcast(id_="p1", title="Test Podcast", slug="test-podcast")
    ep1 = _make_episode(id_="e1", podcast_id="p1", slug="ep-one", title="Lead Episode")
    plan = ThemePlan(
        segments=(Segment(theme="Lead", angle="Lead angle", episode_ids=("e1",), rank=1),),
        tail_ids=(),
    )
    digest = _digest(id_="digest-001", episode_ids=["e1"])
    runner = NarrationRunner(
        generator=_generator(storage, plan, _good_blocks()),
        digest_repository=_DigestRepo([digest]),
        podcast_repository=_PodcastRepo({"e1": (podcast, ep1)}),
    )
    run = runner.run(target_duration_seconds=300, slug="morning")
    assert run.digest_id == "digest-001"
    assert run.narration_id == "digest-001-morning"
    assert run.json_path is not None and run.json_path.exists()
    assert run.json_path.name == "digest-001-morning.json"
    assert run.markdown_path is not None and run.markdown_path.exists()
    assert run.markdown_path.name == "digest-001-morning.md"

    payload = json.loads(run.json_path.read_text(encoding="utf-8"))
    assert payload["mode"] == "narrated"
    assert payload["episodes_covered"] == ["e1"]


def test_runner_captures_latency_ms_and_digest_id(storage: PathManager) -> None:
    """Phase 5 instrumentation: ``content.latency_ms`` is populated by
    the runner around ``generate()``, and ``digest_id`` is persisted in
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
    digest = _digest(id_="digest-uuid-001", episode_ids=["e1"])
    runner = NarrationRunner(
        generator=_generator(storage, plan, _good_blocks()),
        digest_repository=_DigestRepo([digest]),
        podcast_repository=_PodcastRepo({"e1": (podcast, ep1)}),
    )
    run = runner.run(target_duration_seconds=300, slug="medium")

    assert run.content.latency_ms is not None
    assert isinstance(run.content.latency_ms, int)
    assert run.content.latency_ms >= 0

    payload = json.loads(run.json_path.read_text(encoding="utf-8"))
    assert payload["latency_ms"] == run.content.latency_ms
    assert payload["digest_id"] == "digest-uuid-001"
    assert payload["slug"] == "medium"


def test_runner_resolves_specific_digest_id(storage: PathManager) -> None:
    from thestill.services.narration.models import Segment, ThemePlan

    podcast = _make_podcast(id_="p1", title="Test Podcast", slug="test-podcast")
    ep1 = _make_episode(id_="e1", podcast_id="p1", slug="ep-one")
    plan = ThemePlan(
        segments=(Segment(theme="Lead", angle="ang", episode_ids=("e1",), rank=1),),
        tail_ids=(),
    )
    older = _digest(id_="older", episode_ids=["e1"])
    newer = _digest(id_="newer", episode_ids=["e1"])
    runner = NarrationRunner(
        generator=_generator(storage, plan, _good_blocks()),
        digest_repository=_DigestRepo([older, newer]),
        podcast_repository=_PodcastRepo({"e1": (podcast, ep1)}),
    )
    run = runner.run(digest_id="older", target_duration_seconds=300)
    assert run.digest_id == "older"


def test_runner_raises_when_no_digest(storage: PathManager) -> None:
    runner = NarrationRunner(
        generator=_generator(
            storage,
            __import__("thestill.services.narration.models", fromlist=["ThemePlan"]).ThemePlan(
                segments=(), tail_ids=()
            ),
            _good_blocks(),
        ),
        digest_repository=_DigestRepo([]),
        podcast_repository=_PodcastRepo({}),
    )
    with pytest.raises(NarrationRunnerError):
        runner.run()


def test_runner_raises_when_unknown_digest_id(storage: PathManager) -> None:
    digest = _digest(id_="known", episode_ids=["e1"])
    runner = NarrationRunner(
        generator=_generator(
            storage,
            __import__("thestill.services.narration.models", fromlist=["ThemePlan"]).ThemePlan(
                segments=(), tail_ids=()
            ),
            _good_blocks(),
        ),
        digest_repository=_DigestRepo([digest]),
        podcast_repository=_PodcastRepo({}),
    )
    with pytest.raises(NarrationRunnerError):
        runner.run(digest_id="nope")


def test_runner_raises_when_all_episodes_missing(storage: PathManager) -> None:
    digest = _digest(id_="d1", episode_ids=["gone-1", "gone-2"])
    runner = NarrationRunner(
        generator=_generator(
            storage,
            __import__("thestill.services.narration.models", fromlist=["ThemePlan"]).ThemePlan(
                segments=(), tail_ids=()
            ),
            _good_blocks(),
        ),
        digest_repository=_DigestRepo([digest]),
        podcast_repository=_PodcastRepo({}),
    )
    with pytest.raises(NarrationRunnerError):
        runner.run(digest_id="d1")
