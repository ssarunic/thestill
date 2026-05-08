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

"""Tests for the narration markdown renderer (spec #33 §"Output Format")."""

from datetime import datetime, timezone

import pytest
from pydantic import HttpUrl

from thestill.models.podcast import Episode, Podcast
from thestill.services.narration.markdown_renderer import NarrationMarkdownRenderer
from thestill.services.narration.models import (
    NarrationStats,
    QuoteCandidate,
    ScriptBlock,
    Segment,
    ThemePlan,
)
from thestill.utils.url_generator import UrlGenerator


def _episode(*, id_: str, slug: str, title: str = "Episode title") -> Episode:
    ep = Episode(
        external_id=f"guid-{id_}",
        podcast_id="pod-001",
        title=title,
        description="…",
        pub_date=datetime(2026, 5, 6, tzinfo=timezone.utc),
        audio_url=HttpUrl("https://example.com/a.mp3"),
        slug=slug,
    )
    ep.id = id_
    return ep


def _podcast(*, id_: str = "pod-001", title: str = "Test Podcast", slug: str = "test-podcast") -> Podcast:
    return Podcast(
        id=id_,
        title=title,
        description="…",
        rss_url=HttpUrl("https://example.com/feed.xml"),
        slug=slug,
    )


def _quote() -> QuoteCandidate:
    return QuoteCandidate(
        quote_id="q1",
        episode_id="ep-1",
        podcast_title="Test Podcast",
        speaker="Alex Anchor",
        speaker_role="host",
        text="Best time to be a junior, contrary to what a lot of people are saying.",
        start_seconds=59.0,
        duration_seconds=12.0,
    )


def _stats() -> NarrationStats:
    return NarrationStats(
        target_duration_seconds=300,
        actual_duration_seconds=292.0,
        narration_words=620,
        quote_seconds=72.0,
        episodes_covered=1,
        episodes_in_tail=1,
        quote_count=1,
    )


def test_renders_header_runtime_and_quote_attribution() -> None:
    plan = ThemePlan(
        segments=(
            Segment(
                theme="AI coding agents",
                angle="Two takes on shipping by non-engineers",
                episode_ids=("ep-1",),
                rank=1,
            ),
        ),
        tail_ids=("ep-2",),
    )
    blocks = [
        ScriptBlock(kind="narration", section="opener", text="Headline tease."),
        ScriptBlock(
            kind="narration",
            section="segment-1",
            text="Lead-segment narration.",
        ),
        ScriptBlock(
            kind="quote", section="segment-1", quote_id="q1", duration_seconds=12.0
        ),
        ScriptBlock(
            kind="narration",
            section="segment-1",
            text="Transition narration.",
        ),
        ScriptBlock(
            kind="narration", section="tail", text="Also today, shorter mentions."
        ),
        ScriptBlock(kind="narration", section="signoff", text="Back tomorrow."),
    ]

    podcast = _podcast()
    ep1 = _episode(id_="ep-1", slug="ep-one", title="Lead Episode")
    ep2 = _episode(id_="ep-2", slug="ep-two", title="Tail Episode")
    out = NarrationMarkdownRenderer(UrlGenerator()).render(
        blocks=blocks,
        quotes=[_quote()],
        plan=plan,
        episodes=[(podcast, ep1), (podcast, ep2)],
        stats=_stats(),
        generated_at=datetime(2026, 5, 6, 7, 0, 0, tzinfo=timezone.utc),
    )

    assert out.startswith("# Morning Briefing — May 06, 2026")
    assert "Target: 5 minutes · Actual: 4m 52s" in out
    assert "## Lead — AI coding agents" in out
    assert "Headline tease." in out
    # Quote rendered as block-quote with attribution + listen link.
    assert "> Best time to be a junior" in out
    assert "— Alex Anchor, Test Podcast (00:59)" in out
    assert "[▶ Listen at 00:59](/podcasts/test-podcast/episodes/ep-one?t=59)" in out
    # Tail heading + appendix with both episodes.
    assert "## Also today" in out
    assert "## Episodes covered" in out
    assert "[Lead Episode](/podcasts/test-podcast/episodes/ep-one)" in out
    assert "[Tail Episode](/podcasts/test-podcast/episodes/ep-two)" in out


def test_renderer_drops_listen_link_when_episode_pair_unknown() -> None:
    plan = ThemePlan(
        segments=(Segment(theme="t", angle="a", episode_ids=("ep-1",), rank=1),),
        tail_ids=(),
    )
    blocks = [
        ScriptBlock(kind="narration", section="opener", text="Open."),
        ScriptBlock(
            kind="quote", section="segment-1", quote_id="q1", duration_seconds=12.0
        ),
        ScriptBlock(kind="narration", section="signoff", text="Bye."),
    ]
    # No episodes passed in: the quote text + speaker attribution still
    # render (they are useful on their own), but the deep-link is dropped
    # rather than emitted as a broken URL.
    out = NarrationMarkdownRenderer().render(
        blocks=blocks,
        quotes=[_quote()],
        plan=plan,
        episodes=[],
        stats=_stats(),
        generated_at=datetime(2026, 5, 6, tzinfo=timezone.utc),
    )
    assert "▶ Listen" not in out
    assert "Best time to be a junior" in out
    assert "— Alex Anchor, Test Podcast (00:59)" in out
