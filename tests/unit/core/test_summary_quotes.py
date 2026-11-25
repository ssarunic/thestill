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

"""Spec #120: the summary's Best Quotes, checked before a public page shows them."""

from __future__ import annotations

import pytest

from thestill.core.summary_quotes import BestQuote, SpeakerRef, locate, names_match, parse_best_quotes, verify_quotes
from thestill.models.annotated_transcript import AnnotatedSegment

SUMMARY = """## 1. 🎙️ The Gist
George explains why AI makes attacks faster.

## 5. 💬 Best Quotes
* "We're minting more adversaries and you're democratising destruction." - George Kurtz [10:17](?t=617&cite=c12)
* “Why break in when you can log in?” – George Kurtz [43:30](?t=2610&cite=c14)
* **"OpenAI took the airbags off the agents..."** - George Kurtz [04:07](?t=247&cite=c11)
* "Do we really have to get rid of them?" - Anonymous HR Manager (via George Kurtz) [49:15](?t=2955&cite=c15)
* "No timestamp at all." - Jason Calacanis
Not a bullet, ignored.

## 6. 🎧 Worth a Listen?
* "This is not a quote section." - Someone [01:00](?t=60&cite=c99)
"""


def test_parse_reads_only_the_quotes_section_in_order():
    quotes = parse_best_quotes(SUMMARY)
    assert [q.text for q in quotes] == [
        "We're minting more adversaries and you're democratising destruction.",
        "Why break in when you can log in?",
        "OpenAI took the airbags off the agents...",
        "Do we really have to get rid of them?",
        "No timestamp at all.",
    ]
    assert quotes[0].speaker == "George Kurtz"
    assert (quotes[0].cite_id, quotes[0].label_seconds) == ("c12", 617.0)
    assert quotes[3].speaker == "Anonymous HR Manager (via George Kurtz)"
    assert (quotes[4].cite_id, quotes[4].label_seconds) == (None, None)


def test_parse_without_a_quotes_section_is_empty():
    assert parse_best_quotes("## 1. The Gist\nNothing quoted.\n") == []


@pytest.mark.parametrize(
    "said,entity,expected",
    [
        ("George Kurtz", "George Kurtz", True),
        ("Kurtz", "George Kurtz", True),
        ("George", "George Kurtz", True),
        ("Jason (host)", "Jason Calacanis", True),
        ("Dr. Andrew Huberman", "Andrew Huberman", True),
        ("Andrew Huberman", "Andrew D. Huberman", False),
        ("George Kurtz", "Jason Calacanis", False),
        ("Anonymous HR Manager (via George Kurtz)", "George Kurtz", False),
        ("Lloyd Griffith & Rick Edwards", "Rick Edwards", False),
        ("Speaker 4", "George Kurtz", False),
        ("Al", "Al Gore", False),
    ],
)
def test_names_match(said, entity, expected):
    assert names_match(said, entity) is expected


def _segments(*texts: str, speaker: str = "George Kurtz", kind: str = "content") -> list:
    return [
        AnnotatedSegment(id=10 + i, start=60.0 * i, end=60.0 * i + 50.0, speaker=speaker, text=text, kind=kind)
        for i, text in enumerate(texts)
    ]


def test_locate_allows_an_ellipsis_and_a_tidied_word():
    segments = _segments(
        "Intro chatter here.",
        "So I'd say OpenAI took the airbags off the agents, honestly, and then everyone else followed.",
    )
    found = locate("OpenAI took the airbags off the agents... everyone else followed.", segments, 1)
    assert found is not None and found.first == 1 and found.coverage == 1.0
    # One word changed out of eight still passes.
    assert locate("OpenAI pulled the airbags off the agents and everyone", segments, 1) is not None


def test_locate_refuses_a_paraphrase():
    segments = _segments("We are creating many new attackers and spreading destruction everywhere.")
    assert locate("We're minting more adversaries and you're democratising destruction.", segments, 0) is None


def test_editorial_insertions_are_not_checked():
    segments = _segments("You really don't understand how out of shape you are until you try.")
    assert locate("You really don't understand how out of shape you are until you try [to play soccer].", segments, 0)


def _verify(quotes, segments, speakers, citations=None):
    citations = citations or {}
    return verify_quotes(quotes, citations=citations, segments=segments, speakers=speakers)


KURTZ = SpeakerRef("person:george-kurtz", "George Kurtz")


def test_verify_keeps_a_checked_quote_with_its_segment_speaker_and_time():
    segments = _segments("Hello there, welcome.", "Why break in when you can log in? That's the shift.")
    kept, dropped = _verify(
        [BestQuote("Why break in when you can log in?", "George Kurtz", "c1", 60.0)],
        segments,
        {11: KURTZ},
        {"c1": {"id": "c1", "segment_id_hint": 11}},
    )
    assert dropped == []
    assert [(q.text, q.speaker, q.segment_id, q.start_ms) for q in kept] == [
        ("Why break in when you can log in?", KURTZ, 11, 60_000)
    ]


def test_verify_finds_words_the_citation_points_away_from():
    segments = _segments(
        "Completely unrelated words about the weather and football scores.",
        "Nothing here either, just more chatter about lunch plans today.",
        "Still nothing relevant in this one at all, honestly nothing.",
        "Then much later: why break in when you can log in? Exactly.",
    )
    kept, _ = _verify(
        [BestQuote("Why break in when you can log in?", "Kurtz", "c1", 0.0)],
        segments,
        {13: KURTZ},
        {"c1": {"id": "c1", "segment_id_hint": 10}},
    )
    assert [q.segment_id for q in kept] == [13]
    # Mid-turn: playback starts near the words, a little before them.
    assert 180_000 < kept[0].start_ms < 195_000


def test_verify_prefers_the_moment_over_a_cold_open_preview():
    segments = [
        AnnotatedSegment(
            id=1, start=0, end=10, speaker="George Kurtz", text="Why break in when you can log in?", kind="intro"
        ),
        AnnotatedSegment(id=2, start=500, end=530, speaker="George Kurtz", text="Why break in when you can log in?"),
    ]
    kept, _ = _verify(
        [BestQuote("Why break in when you can log in?", "George Kurtz", "c1", 0.0)],
        segments,
        {1: KURTZ, 2: KURTZ},
        {"c1": {"id": "c1", "segment_id_hint": 1}},
    )
    assert [q.segment_id for q in kept] == [2]


@pytest.mark.parametrize(
    "quote,speakers,reason",
    [
        (BestQuote("Why break in when you can log in?", "Jason Calacanis", "c1", 0.0), {10: KURTZ}, "speaker_mismatch"),
        (BestQuote("Why break in when you can log in?", "George Kurtz", "c1", 0.0), {}, "speaker_unresolved"),
        (BestQuote("A line nobody said on this show.", "George Kurtz", "c1", 0.0), {10: KURTZ}, "not_in_transcript"),
        (BestQuote("A line nobody said on this show.", "George Kurtz", None, None), {10: KURTZ}, "no_citation"),
        (BestQuote("x" * 400, "George Kurtz", "c1", 0.0), {10: KURTZ}, "too_long"),
    ],
)
def test_verify_drops_what_it_cannot_stand_behind(quote, speakers, reason):
    segments = _segments("Why break in when you can log in?")
    kept, dropped = _verify([quote], segments, speakers, {"c1": {"id": "c1", "segment_id_hint": 10}})
    assert kept == []
    assert [d.reason for d in dropped] == [reason]


def test_verify_drops_a_quote_inside_an_ad_break():
    segments = _segments("Use code KURTZ for ten percent off your first order today.", kind="ad_break")
    kept, dropped = _verify(
        [BestQuote("Use code KURTZ for ten percent off your first order today.", "George Kurtz", "c1", 0.0)],
        segments,
        {10: KURTZ},
        {"c1": {"id": "c1", "segment_id_hint": 10}},
    )
    assert kept == [] and [d.reason for d in dropped] == ["not_content"]


def test_verify_applies_the_playback_offset():
    segments = _segments("Why break in when you can log in?")
    kept, _ = verify_quotes(
        [BestQuote("Why break in when you can log in?", "George Kurtz", "c1", 0.0)],
        citations={"c1": {"id": "c1", "segment_id_hint": 10}},
        segments=segments,
        speakers={10: KURTZ},
        playback_offset_s=12.0,
    )
    assert kept[0].start_ms == 12_000 and kept[0].end_ms == 62_000


ALICE = SpeakerRef("person:alice", "Alice Smith")
BOB = SpeakerRef("person:bob", "Bob Jones")


def test_a_quote_running_into_another_speakers_turn_is_dropped():
    """Codex review on PR #561: the words of two people are not one quote."""
    segments = [
        AnnotatedSegment(id=1, start=0, end=2, speaker="Alice Smith", text="I think"),
        AnnotatedSegment(
            id=2, start=2, end=9, speaker="Bob Jones", text="we should never release untested software to our users."
        ),
    ]
    kept, dropped = _verify(
        [BestQuote("I think we should never release untested software to our users.", "Alice Smith", "c1", 0.0)],
        segments,
        {1: ALICE, 2: BOB},
        {"c1": {"id": "c1", "segment_id_hint": 1}},
    )
    assert kept == [] and [d.reason for d in dropped] == ["speakers_differ"]


def test_a_quote_over_one_speakers_split_turn_is_kept():
    segments = [
        AnnotatedSegment(id=1, start=0, end=4, speaker="Alice Smith", text="We should never release"),
        AnnotatedSegment(id=2, start=4, end=9, speaker="Alice Smith", text="untested software to our users, ever."),
        AnnotatedSegment(id=3, start=9, end=12, speaker="Bob Jones", text="Right, the users."),
    ]
    kept, _ = _verify(
        [BestQuote("We should never release untested software to our users.", "Alice", "c1", 0.0)],
        segments,
        {1: ALICE, 2: ALICE, 3: BOB},
        {"c1": {"id": "c1", "segment_id_hint": 1}},
    )
    assert [(q.segment_id, q.speaker) for q in kept] == [(1, ALICE)]


def test_a_stray_common_word_in_a_neighbouring_turn_does_not_veto():
    segments = [
        AnnotatedSegment(id=1, start=0, end=3, speaker="Bob Jones", text="Okay, the next one."),
        AnnotatedSegment(id=2, start=3, end=9, speaker="Alice Smith", text="Shipping is the only thing that matters."),
    ]
    kept, dropped = _verify(
        [BestQuote("Shipping is the only thing that matters.", "Alice Smith", "c1", 3.0)],
        segments,
        {1: BOB, 2: ALICE},
        {"c1": {"id": "c1", "segment_id_hint": 2}},
    )
    assert dropped == [] and [q.segment_id for q in kept] == [2]


@pytest.mark.parametrize(
    "spoken,quoted,said,entity",
    [
        # Codex review on PR #561: any script, not ASCII only.
        (
            "Ну, мы создаём больше противников, чем когда-либо.",
            "Мы создаём больше противников, чем когда-либо.",
            "Олег Петров",
            "Олег Петров",
        ),
        (
            "Λοιπόν, η δημοκρατία χρειάζεται υπομονή και χρόνο.",
            "Η δημοκρατία χρειάζεται υπομονή και χρόνο.",
            "Νίκος",
            "Νίκος Παππάς",
        ),
        (
            "つまり、私たちは毎日新しい攻撃者を生み出しています。",
            "私たちは毎日新しい攻撃者を生み出しています。",
            "山田太郎",
            "山田太郎",
        ),
    ],
)
def test_quotes_in_other_scripts_are_checked_and_kept(spoken, quoted, said, entity):
    speaker = SpeakerRef("person:x", entity)
    segments = [AnnotatedSegment(id=1, start=0, end=9, speaker=entity, text=spoken)]
    kept, dropped = _verify(
        [BestQuote(quoted, said, "c1", 0.0)], segments, {1: speaker}, {"c1": {"id": "c1", "segment_id_hint": 1}}
    )
    assert dropped == [] and kept[0].coverage == 1.0


def test_a_paraphrase_in_another_script_is_still_refused():
    segments = [AnnotatedSegment(id=1, start=0, end=9, text="Мы создаём больше противников, чем когда-либо.")]
    assert locate("Мир становится безопаснее с каждым годом, говорят эксперты.", segments, 0) is None


def test_a_misplaced_quote_with_a_tidied_word_is_still_found():
    """Prod, CrowdStrike episode: the citation pointed elsewhere and the
    summary changed one word, so no long exact run matched."""
    segments = _segments(
        "Well I felt I needed some slides because you guys always have very good slides.",
        "Unrelated chatter about the weather and the football and lunch plans.",
        "So we're minting more adversaries and we're democratising destruction, frankly.",
    )
    kept, dropped = _verify(
        [BestQuote("We're minting more adversaries and you're democratising destruction.", "George Kurtz", "c1", 0.0)],
        segments,
        {10: KURTZ, 11: KURTZ, 12: KURTZ},
        {"c1": {"id": "c1", "segment_id_hint": 10}},
    )
    assert dropped == [] and [q.segment_id for q in kept] == [12]


def test_a_line_said_back_by_the_other_speaker_is_found_where_its_speaker_said_it():
    """Prod, CrowdStrike episode: the host repeats the guest's line first."""
    jason = SpeakerRef("person:jason-calacanis", "Jason Calacanis")
    segments = [
        AnnotatedSegment(
            id=1, start=0, end=5, speaker="Jason Calacanis", text="Why break in when you can log in? Say more."
        ),
        AnnotatedSegment(
            id=2, start=5, end=60, speaker="Jason Calacanis", text="Filler about the sponsors and the weather today."
        ),
        AnnotatedSegment(
            id=3, start=60, end=70, speaker="Jason Calacanis", text="More chatter, nothing quoted here at all."
        ),
        AnnotatedSegment(
            id=4, start=70, end=80, speaker="Jason Calacanis", text="Still the host talking about other things."
        ),
        AnnotatedSegment(
            id=5, start=80, end=90, speaker="George Kurtz", text="Like I always say, why break in when you can log in?"
        ),
    ]
    kept, dropped = _verify(
        [BestQuote("Why break in when you can log in?", "George Kurtz", "c1", 0.0)],
        segments,
        {1: jason, 2: jason, 3: jason, 4: jason, 5: KURTZ},
        {"c1": {"id": "c1", "segment_id_hint": 1}},
    )
    assert dropped == [] and [(q.segment_id, q.speaker) for q in kept] == [(5, KURTZ)]


def test_words_only_the_other_speaker_said_are_still_refused():
    jason = SpeakerRef("person:jason-calacanis", "Jason Calacanis")
    segments = [
        AnnotatedSegment(id=1, start=0, end=5, speaker="Jason Calacanis", text="Why break in when you can log in?")
    ]
    kept, dropped = _verify(
        [BestQuote("Why break in when you can log in?", "George Kurtz", "c1", 0.0)],
        segments,
        {1: jason},
        {"c1": {"id": "c1", "segment_id_hint": 1}},
    )
    assert kept == [] and [d.reason for d in dropped] == ["speaker_mismatch"]
