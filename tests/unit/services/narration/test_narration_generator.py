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

"""Tests for ``NarrationGenerator`` skeleton output (spec #33 Phase 1)."""

import json
from datetime import datetime, timezone
from pathlib import Path
from typing import List, Optional

import pytest
from pydantic import HttpUrl

from thestill.models.podcast import Episode, Podcast
from thestill.services.narration.models import QuoteCandidate, ScriptBlock
from thestill.services.narration.narration_generator import NarrationConfig, NarrationGenerator
from thestill.services.narration.quote_selector import QuoteSelector, QuoteSelectorConfig
from thestill.services.narration.transcript_loader import ResolvedTurn, TranscriptTurnLoader
from thestill.utils.path_manager import PathManager


class _StaticLoader:
    """Stand-in loader: returns a hard-coded turn list per episode id."""

    def __init__(
        self, turns_by_episode: dict[str, List[ResolvedTurn]], facts_by_episode: dict[str, object] | None = None
    ) -> None:
        self._turns = turns_by_episode
        self._facts = facts_by_episode or {}

    def load(self, podcast: Podcast, episode: Episode) -> List[ResolvedTurn]:
        return list(self._turns.get(episode.id, []))

    def load_episode_facts(self, podcast: Podcast, episode: Episode):
        return self._facts.get(episode.id)

    def sidecar_exists(self, podcast: Podcast, episode: Episode) -> bool:
        # A static episode "has a transcript" exactly when turns were staged for it.
        return episode.id in self._turns


def _make_episode(
    *,
    id_: str,
    podcast_id: str,
    slug: str,
    title: str = "Title",
    duration: int = 3600,
) -> Episode:
    ep = Episode(
        external_id=f"guid-{id_}",
        podcast_id=podcast_id,
        title=title,
        description="…",
        pub_date=datetime(2026, 5, 6, tzinfo=timezone.utc),
        audio_url=HttpUrl("https://example.com/a.mp3"),
        slug=slug,
        duration=duration,
    )
    ep.id = id_
    return ep


def _make_podcast(*, id_: str, title: str, slug: str) -> Podcast:
    return Podcast(
        id=id_,
        title=title,
        description="…",
        rss_url=HttpUrl("https://example.com/feed.xml"),
        slug=slug,
    )


def _good_turn(
    *,
    episode_id: str,
    segment_id: int,
    start: float,
    speaker: str,
    role: str = "host",
    text: str | None = None,
) -> ResolvedTurn:
    body = text or (
        "The shipping pipeline cleared every test in the new run, "
        "the monitoring dashboard finally went green, and the on-call "
        "rotation reported zero pages overnight."
    )
    return ResolvedTurn(
        episode_id=episode_id,
        podcast_title="Test Podcast",
        segment_id=segment_id,
        speaker_label=f"SPEAKER_0{segment_id}",
        speaker_name=speaker,
        speaker_role=role,
        text=body,
        start_seconds=start,
        end_seconds=start + 18.0,
        is_ad_adjacent=False,
    )


@pytest.fixture
def storage(tmp_path: Path) -> PathManager:
    data_root = tmp_path / "data"
    data_root.mkdir()
    pm = PathManager(storage_path=str(data_root))
    pm.ensure_directories_exist()
    return pm


@pytest.fixture
def file_storage(storage: PathManager):
    """Spec #35 — LocalFileStorage rooted at the same data root the
    PathManager uses, so reads/writes round-trip naturally."""
    from thestill.utils.file_storage import LocalFileStorage

    return LocalFileStorage(base_path=str(storage.storage_path))


def test_generator_skeleton_emits_opener_segments_and_signoff(
    storage: PathManager,
    file_storage,
) -> None:
    podcast = _make_podcast(id_="p1", title="Test Podcast", slug="test-podcast")
    ep1 = _make_episode(id_="e1", podcast_id="p1", slug="ep-one", title="Ep One")
    ep2 = _make_episode(id_="e2", podcast_id="p1", slug="ep-two", title="Ep Two")

    loader = _StaticLoader(
        turns_by_episode={
            "e1": [_good_turn(episode_id="e1", segment_id=1, start=60.0, speaker="A")],
            "e2": [_good_turn(episode_id="e2", segment_id=1, start=60.0, speaker="B")],
        }
    )
    gen = NarrationGenerator(path_manager=storage, file_storage=file_storage, loader=loader, selector=QuoteSelector())
    content = gen.generate([(podcast, ep1), (podcast, ep2)])
    sections = [b.section for b in content.blocks]
    assert sections[0] == "opener"
    assert sections[-1] == "signoff"
    assert "segment-1" in sections
    assert "segment-2" in sections
    # Two episodes, each contributed one quote.
    quote_blocks = [b for b in content.blocks if b.kind == "quote"]
    assert len(quote_blocks) == 2
    assert content.stats.episodes_covered == 2
    assert content.stats.episodes_in_tail == 0


def test_episodes_without_quotes_route_to_tail(storage: PathManager, file_storage) -> None:
    podcast = _make_podcast(id_="p1", title="Test Podcast", slug="test-podcast")
    covered = _make_episode(id_="e1", podcast_id="p1", slug="ep-one")
    barren = _make_episode(id_="e2", podcast_id="p1", slug="ep-two", title="Quiet One")
    loader = _StaticLoader(
        turns_by_episode={
            "e1": [_good_turn(episode_id="e1", segment_id=1, start=60.0, speaker="A")],
            "e2": [],
        }
    )
    gen = NarrationGenerator(path_manager=storage, file_storage=file_storage, loader=loader, selector=QuoteSelector())
    content = gen.generate([(podcast, covered), (podcast, barren)])
    assert content.episode_ids_covered == ["e1"]
    assert content.episode_ids_in_tail == ["e2"]
    tail_blocks = [b for b in content.blocks if b.section == "tail"]
    assert len(tail_blocks) == 1
    assert "Quiet One" in (tail_blocks[0].text or "")


def test_quote_share_cap_drops_lowest_scoring(storage: PathManager, file_storage) -> None:
    podcast = _make_podcast(id_="p1", title="Test Podcast", slug="test-podcast")
    eps = [_make_episode(id_=f"e{i}", podcast_id="p1", slug=f"ep-{i}") for i in range(1, 6)]
    # Each episode contributes one quote of 18s (clean text). Five quotes
    # = 90s of speech — well over the 40% cap on a 60-second target.
    turns_by_ep = {
        ep.id: [_good_turn(episode_id=ep.id, segment_id=1, start=60.0, speaker=f"S{i}")]
        for i, ep in enumerate(eps, start=1)
    }
    loader = _StaticLoader(turns_by_episode=turns_by_ep)
    cfg = NarrationConfig(target_duration_seconds=60, max_quote_share=0.40)
    gen = NarrationGenerator(path_manager=storage, file_storage=file_storage, loader=loader, selector=QuoteSelector())
    content = gen.generate([(podcast, ep) for ep in eps], cfg)
    cap_seconds = cfg.target_duration_seconds * cfg.max_quote_share
    assert content.stats.quote_seconds <= cap_seconds + 0.01


def test_json_script_round_trips_through_disk(storage: PathManager, file_storage) -> None:
    podcast = _make_podcast(id_="p1", title="Test Podcast", slug="test-podcast")
    ep1 = _make_episode(id_="e1", podcast_id="p1", slug="ep-one", title="First")
    loader = _StaticLoader(
        turns_by_episode={"e1": [_good_turn(episode_id="e1", segment_id=1, start=60.0, speaker="Alex")]}
    )
    gen = NarrationGenerator(path_manager=storage, file_storage=file_storage, loader=loader, selector=QuoteSelector())
    content = gen.generate([(podcast, ep1)])
    out_path = gen.write_json_script(content)
    assert out_path.exists()
    assert out_path.parent == storage.storage_path / "narrations"
    payload = json.loads(out_path.read_text(encoding="utf-8"))
    assert payload["schema_version"] == "phase2"
    assert payload["target_duration_seconds"] == 300
    assert payload["wpm"] == 150.0
    assert payload["episodes_covered"] == ["e1"]
    quote_blocks = [b for b in payload["blocks"] if b["kind"] == "quote"]
    assert len(quote_blocks) == 1
    block = quote_blocks[0]
    # Quote block must be self-describing for downstream TTS.
    assert block["episode_id"] == "e1"
    assert block["speaker"] == "Alex"
    assert block["start_seconds"] == 60.0
    assert block["text"]
    assert block["duration_seconds"] > 0


class _StubClusterer:
    def __init__(self, plan):
        self._plan = plan
        self.calls = 0

    def cluster(self, briefs, target_duration_seconds):  # type: ignore[no-untyped-def]
        self.calls += 1
        return self._plan


class _StubScriptWriter:
    def __init__(self, result):
        self._result = result
        self.calls = 0

    def write(self, **kwargs):  # type: ignore[no-untyped-def]
        self.calls += 1
        return self._result


def test_full_phase2_path_renders_markdown_and_marks_narrated(
    storage: PathManager,
    file_storage,
) -> None:
    from thestill.services.narration.models import Segment, ThemePlan
    from thestill.services.narration.script_writer import ScriptResult

    podcast = _make_podcast(id_="p1", title="Test Podcast", slug="test-podcast")
    ep1 = _make_episode(id_="e1", podcast_id="p1", slug="ep-one", title="Lead Episode")
    loader = _StaticLoader(
        turns_by_episode={
            "e1": [_good_turn(episode_id="e1", segment_id=1, start=60.0, speaker="Alex Anchor")],
        }
    )
    plan = ThemePlan(
        segments=(Segment(theme="AI agents", angle="Lead angle", episode_ids=("e1",), rank=1),),
        tail_ids=(),
    )
    blocks = [
        ScriptBlock(kind="narration", section="opener", text="Today's lead." * 1),
        ScriptBlock(
            kind="narration",
            section="segment-1",
            text=("On Test Podcast, the host argues the bar shifted. " "Two takes from the same morning. " * 5),
        ),
        ScriptBlock(kind="quote", section="segment-1", quote_id="q1", duration_seconds=12.0),
        ScriptBlock(
            kind="narration",
            section="segment-1",
            text="A quick transition before we move on to the next story." * 2,
        ),
        ScriptBlock(kind="narration", section="signoff", text="That's the briefing."),
    ]
    script_result = ScriptResult(blocks=tuple(blocks), failures=(), raw_word_count=80)

    gen = NarrationGenerator(
        path_manager=storage,
        file_storage=file_storage,
        loader=loader,
        selector=QuoteSelector(),
        clusterer=_StubClusterer(plan),
        script_writer=_StubScriptWriter(script_result),
    )
    content = gen.generate([(podcast, ep1)])

    assert content.mode == "narrated"
    assert content.markdown is not None
    assert "# Morning Briefing —" in content.markdown
    assert "## Lead — AI agents" in content.markdown
    # Validate the JSON schema bumped to phase2 and includes mode.
    out_path = gen.write_json_script(content)
    payload = json.loads(out_path.read_text(encoding="utf-8"))
    assert payload["schema_version"] == "phase2"
    assert payload["mode"] == "narrated"
    md_path = gen.write_markdown(content)
    assert md_path is not None and md_path.exists()


def test_fallback_path_emits_link_index_when_validation_fails(
    storage: PathManager,
    file_storage,
) -> None:
    from thestill.services.narration.models import ThemePlan, ValidationFailure
    from thestill.services.narration.script_writer import ScriptResult

    podcast = _make_podcast(id_="p1", title="Test Podcast", slug="test-podcast")
    ep1 = _make_episode(id_="e1", podcast_id="p1", slug="ep-one", title="Lead Episode")
    loader = _StaticLoader(
        turns_by_episode={
            "e1": [_good_turn(episode_id="e1", segment_id=1, start=60.0, speaker="Alex")],
        }
    )
    plan = ThemePlan(segments=(), tail_ids=("e1",))
    failures = (
        ValidationFailure(reason="word_budget_high", detail="too long"),
        ValidationFailure(reason="word_budget_high", detail="still too long"),
    )
    script_result = ScriptResult(blocks=(), failures=failures, raw_word_count=900)

    gen = NarrationGenerator(
        path_manager=storage,
        file_storage=file_storage,
        loader=loader,
        selector=QuoteSelector(),
        clusterer=_StubClusterer(plan),
        script_writer=_StubScriptWriter(script_result),
    )
    content = gen.generate([(podcast, ep1)])

    assert content.mode == "fallback"
    assert content.stats.fallback_reason and "word_budget_high" in content.stats.fallback_reason
    assert content.markdown is not None
    assert "Today's narration is unavailable" in content.markdown
    assert "Morning Briefing" in content.markdown  # link-index header
    assert content.episode_ids_covered == []
    assert content.episode_ids_in_tail == ["e1"]


def test_run_is_deterministic(storage: PathManager, file_storage) -> None:
    podcast = _make_podcast(id_="p1", title="Test Podcast", slug="test-podcast")
    eps = [_make_episode(id_=f"e{i}", podcast_id="p1", slug=f"ep-{i}") for i in range(1, 4)]
    turns_by_ep = {
        ep.id: [
            _good_turn(episode_id=ep.id, segment_id=1, start=60.0, speaker=f"A{i}"),
            _good_turn(episode_id=ep.id, segment_id=2, start=200.0, speaker=f"B{i}"),
        ]
        for i, ep in enumerate(eps, start=1)
    }
    loader_a = _StaticLoader(turns_by_episode=turns_by_ep)
    loader_b = _StaticLoader(turns_by_episode=turns_by_ep)
    gen_a = NarrationGenerator(
        path_manager=storage, file_storage=file_storage, loader=loader_a, selector=QuoteSelector()
    )
    gen_b = NarrationGenerator(
        path_manager=storage, file_storage=file_storage, loader=loader_b, selector=QuoteSelector()
    )
    content_a = gen_a.generate([(podcast, ep) for ep in eps])
    content_b = gen_b.generate([(podcast, ep) for ep in eps])
    assert [b.section for b in content_a.blocks] == [b.section for b in content_b.blocks]
    assert [q.quote_id for q in content_a.quotes] == [q.quote_id for q in content_b.quotes]
    assert [q.text for q in content_a.quotes] == [q.text for q in content_b.quotes]


class _RecordingLogger:
    """Minimal structlog stand-in that records ``warning`` calls."""

    def __init__(self) -> None:
        self.warnings: list[tuple[str, dict]] = []

    def warning(self, event: str, **kw) -> None:
        self.warnings.append((event, kw))

    def info(self, event: str, **kw) -> None:  # pragma: no cover - noise
        pass

    def debug(self, event: str, **kw) -> None:  # pragma: no cover - noise
        pass


def test_empty_quote_pool_with_sidecars_warns_and_is_recorded(storage: PathManager, file_storage, monkeypatch) -> None:
    """Spec #77 §6: transcripts present but nothing selected is never silent."""
    from thestill.services.narration import narration_generator as ng

    rec = _RecordingLogger()
    monkeypatch.setattr(ng, "logger", rec)
    podcast = _make_podcast(id_="p1", title="Test Podcast", slug="test-podcast")
    ep1 = _make_episode(id_="e1", podcast_id="p1", slug="ep-one")
    # A sidecar exists (turns staged) but every turn is unresolved → ineligible.
    unresolved = _good_turn(episode_id="e1", segment_id=1, start=60.0, speaker=None)  # type: ignore[arg-type]
    loader = _StaticLoader(turns_by_episode={"e1": [unresolved]})
    gen = NarrationGenerator(path_manager=storage, file_storage=file_storage, loader=loader, selector=QuoteSelector())
    content = gen.generate([(podcast, ep1)])

    assert content.stats.quote_pool_size == 0
    assert content.stats.episodes_with_sidecar == 1
    events = [e for e, _ in rec.warnings]
    assert events == ["narration.quote_pool_empty"]
    assert rec.warnings[0][1]["episodes_with_sidecar"] == 1
    payload = json.loads(gen.write_json_script(content).read_text(encoding="utf-8"))
    assert payload["quote_pool_size"] == 0
    assert payload["episodes_with_sidecar"] == 1
    assert payload["schema_version"] == "phase2"


def test_empty_quote_pool_without_sidecars_is_quiet(storage: PathManager, file_storage, monkeypatch) -> None:
    from thestill.services.narration import narration_generator as ng

    rec = _RecordingLogger()
    monkeypatch.setattr(ng, "logger", rec)
    podcast = _make_podcast(id_="p1", title="Test Podcast", slug="test-podcast")
    ep1 = _make_episode(id_="e1", podcast_id="p1", slug="ep-one")
    gen = NarrationGenerator(
        path_manager=storage, file_storage=file_storage, loader=_StaticLoader({}), selector=QuoteSelector()
    )
    content = gen.generate([(podcast, ep1)])
    assert content.stats.episodes_with_sidecar == 0
    assert rec.warnings == []


def test_pool_stats_survive_the_narrated_path(storage: PathManager, file_storage) -> None:
    from thestill.services.narration.models import Segment, ThemePlan
    from thestill.services.narration.script_writer import ScriptResult

    podcast = _make_podcast(id_="p1", title="Test Podcast", slug="test-podcast")
    ep1 = _make_episode(id_="e1", podcast_id="p1", slug="ep-one")
    loader = _StaticLoader({"e1": [_good_turn(episode_id="e1", segment_id=1, start=60.0, speaker="Alex Anchor")]})
    plan = ThemePlan(segments=(Segment(theme="T", angle="A", episode_ids=("e1",), rank=1),), tail_ids=())
    blocks = [
        ScriptBlock(kind="narration", section="opener", text="Lead in."),
        ScriptBlock(kind="quote", section="segment-1", quote_id="q1", duration_seconds=12.0),
        ScriptBlock(kind="narration", section="signoff", text="Bye."),
    ]
    gen = NarrationGenerator(
        path_manager=storage,
        file_storage=file_storage,
        loader=loader,
        selector=QuoteSelector(),
        clusterer=_StubClusterer(plan),
        script_writer=_StubScriptWriter(ScriptResult(blocks=tuple(blocks), failures=(), raw_word_count=3)),
    )
    content = gen.generate([(podcast, ep1)])
    assert content.mode == "narrated"
    assert content.stats.quote_pool_size == 1
    assert content.stats.episodes_with_sidecar == 1


def test_episode_brief_carries_sanitised_takeaways_and_drama(storage: PathManager, file_storage) -> None:
    """Spec #77 Phase 2b: takeaways + drama rounds, control bytes stripped, terms unquoted."""
    podcast = _make_podcast(id_="p1", title="Test Podcast", slug="test-podcast")
    ep1 = _make_episode(id_="e1", podcast_id="p1", slug="ep-one")
    ep1.summary_path = "ep-one_summary.md"
    summary = (
        "## 1. 🎙️ The Gist\nHost talks to Guest.\n\nA second gist sentence here.\n\n"
        "## 3. 🧠 Key Takeaways\n* Models are four months behind. [02:46](?t=166&cite=c5)\n"
        "* He calls them 'spaghetti' org charts. [03:00](?t=180&cite=c6)\n\n"
        "## 4. 🌶️ The Drama\n* **Round 1: The row** [07:04](?t=424&cite=c9)\n"
        "  * **What happened:** Someone left\x00 the party.\n"
    )
    file_storage.write_text(storage.to_relative(storage.summary_file(ep1.summary_path)), summary)
    gen = NarrationGenerator(path_manager=storage, file_storage=file_storage, loader=_StaticLoader({}))
    brief = gen._build_episode_brief(podcast, ep1)
    assert brief.gist is not None and "Host talks to Guest." in brief.gist
    assert brief.takeaways == ("Models are four months behind.", "He calls them spaghetti org charts.")
    assert len(brief.drama) == 1 and brief.drama[0].startswith("Round 1: The row")
    assert "\x00" not in brief.drama[0] and "Someone left the party." in brief.drama[0]
    assert "cite=" not in " ".join(brief.takeaways + brief.drama)


def test_episode_brief_legacy_summary_has_gist_only(storage: PathManager, file_storage) -> None:
    podcast = _make_podcast(id_="p1", title="Test Podcast", slug="test-podcast")
    ep1 = _make_episode(id_="e1", podcast_id="p1", slug="ep-one")
    ep1.summary_path = "ep-one_summary.md"
    file_storage.write_text(
        storage.to_relative(storage.summary_file(ep1.summary_path)),
        "Executive Summary\n\nA short overview of the show. Another sentence.\n",
    )
    gen = NarrationGenerator(path_manager=storage, file_storage=file_storage, loader=_StaticLoader({}))
    brief = gen._build_episode_brief(podcast, ep1)
    assert brief.gist is not None and brief.takeaways == () and brief.drama == ()


def test_reaction_blocks_render_in_italics_and_serialise_with_their_kind(storage: PathManager, file_storage) -> None:
    from thestill.services.narration.models import Segment, ThemePlan
    from thestill.services.narration.script_writer import ScriptResult

    podcast = _make_podcast(id_="p1", title="Test Podcast", slug="test-podcast")
    ep1 = _make_episode(id_="e1", podcast_id="p1", slug="ep-one")
    loader = _StaticLoader({"e1": [_good_turn(episode_id="e1", segment_id=1, start=60.0, speaker="Alex Anchor")]})
    plan = ThemePlan(segments=(Segment(theme="T", angle="A", episode_ids=("e1",), rank=1),), tail_ids=())
    blocks = [
        ScriptBlock(kind="narration", section="opener", text="Lead in."),
        ScriptBlock(kind="quote", section="segment-1", quote_id="q1", duration_seconds=12.0),
        ScriptBlock(kind="reaction", section="segment-1", text="Which, fair.", duration_seconds=1.0),
        ScriptBlock(kind="narration", section="signoff", text="Bye."),
    ]
    result = ScriptResult(blocks=tuple(blocks), failures=(), raw_word_count=5, reaction_count=1)
    gen = NarrationGenerator(
        path_manager=storage,
        file_storage=file_storage,
        loader=loader,
        selector=QuoteSelector(),
        clusterer=_StubClusterer(plan),
        script_writer=_StubScriptWriter(result),
    )
    content = gen.generate([(podcast, ep1)])
    assert "*Which, fair.*" in (content.markdown or "")
    assert content.stats.reaction_count == 1 and content.stats.narration_words == 5
    payload = json.loads(gen.write_json_script(content).read_text(encoding="utf-8"))
    assert [b["kind"] for b in payload["blocks"]] == ["narration", "quote", "reaction", "narration"]
    assert payload["reaction_count"] == 1 and payload["reactions_missing"] == 0
