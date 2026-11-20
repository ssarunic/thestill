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

"""Search titles and descriptions, on cases taken from real episodes."""

import pytest

from thestill.web.search_titles import (
    TITLE_MAX,
    clean_episode_title,
    episode_search_title,
    person_search_description,
    plain_text,
    short_show_name,
    usable_guest_names,
)


@pytest.mark.parametrize(
    "show, short",
    [
        ("Lenny's Podcast: Product | Career | Growth", "Lenny's Podcast"),
        ("The Twenty Minute VC (20VC): Venture Capital | Startup Funding | The Pitch", "The Twenty Minute VC"),
        ("The AI Daily Brief: Artificial Intelligence News and Analysis", "The AI Daily Brief"),
        # A one- or two-word suffix names an edition.
        ("The Rest Is Politics: US", "The Rest Is Politics: US"),
        ("The Rest Is Politics: Leading", "The Rest Is Politics: Leading"),
        ("Deep Questions with Cal Newport", "Deep Questions with Cal Newport"),
    ],
)
def test_short_show_name(show, short):
    assert short_show_name(show) == short


@pytest.mark.parametrize(
    "title, cleaned",
    [
        ("579. Trump’s Public Execution Order", "Trump’s Public Execution Order"),
        ("Epizoda 223: Charles Manson: Dio Prvi", "Charles Manson: Dio Prvi"),
        ("S10 EP22: Sam Campbell", "Sam Campbell"),
        ("Google Invests $40B Into Anthropic | EP #252", "Google Invests $40B Into Anthropic"),
        ("Episode 1", "Episode 1"),
    ],
)
def test_clean_episode_title(title, cleaned):
    assert clean_episode_title(title) == cleaned


def test_a_guest_on_the_show_says_what_the_page_is():
    title = episode_search_title(
        "Vinnie Jones on getting sober, mediating with Steven Seagal, and the Gazza incident",
        "The Louis Theroux Podcast",
        ["Vinnie Jones"],
    )
    # The whole title is too long, and a part of it that names him would be
    # a fragment, so the guest and the show carry it.
    assert title == "Vinnie Jones on The Louis Theroux Podcast — summary & key quotes"


def test_a_guest_the_publisher_does_not_name_is_left_out():
    # "Pearson v. Chung" was stored as a guest; the publisher names only Fan-yun Sun.
    title = episode_search_title(
        "Wild Ideas in AI",
        "Latent Space: The AI Engineer Podcast",
        ["Pearson v. Chung", "Fan-yun Sun"],
        description="With Fan-yun Sun of Stanford.",
    )
    assert "Pearson" not in title and title.startswith("Fan-yun Sun")


def test_the_topic_stays_with_the_guest():
    title = episode_search_title(
        "OpenAI buys TBPN",
        "Big Technology Podcast",
        ["Liz Hoffman"],
        description="Liz Hoffman of Semafor joins.",
    )
    assert title == "Liz Hoffman: OpenAI buys TBPN — Big Technology Podcast"


def test_without_a_guest_the_topic_and_show_stay():
    title = episode_search_title("Something Weird Is Going On With Placebos", "The Rest Is Science")
    assert title == "Something Weird Is Going On With Placebos — The Rest Is Science"


def test_a_long_title_is_cut_at_a_clause_and_the_show_shortened():
    title = episode_search_title(
        "Google Invests $40B Into Anthropic, GPT 5.5 Drops, and Google Cloud Dominates | EP #252",
        "Moonshots with Peter Diamandis",
    )
    assert title == "Google Invests $40B Into Anthropic — Moonshots summary"
    assert "…" not in title


def test_a_title_that_already_has_a_dash_uses_a_bar():
    title = episode_search_title("How To Actually Tax The Rich — ft. Ray Madoff", "Prof G Markets", ["Ray Madoff"])
    assert title == "How To Actually Tax The Rich — ft. Ray Madoff | Prof G Markets"


def test_a_non_english_show_gets_no_english_words():
    title = episode_search_title("Epizoda 223: Charles Manson: Dio Prvi", "Mjesto Zločina", language="hr")
    assert title == "Charles Manson: Dio Prvi — Mjesto Zločina"
    assert "summary" not in title


def test_a_title_that_is_only_the_guests_name_is_not_used_alone():
    title = episode_search_title("Dylan Patel — Inside the chip wars", "Dwarkesh Podcast", ["Dylan Patel"])
    assert title != "Dylan Patel — Dwarkesh Podcast"
    assert "Dylan Patel" in title


def test_speaker_labels_are_never_guests():
    assert usable_guest_names(["Unknown speaker", "Intro narrator", "Madhavan Ramanujam"]) == ["Madhavan Ramanujam"]


@pytest.mark.parametrize(
    "episode, show, guests",
    [
        ("Best Tools for Gut Health & Weight Loss | Dr. Chris Thompson", "Huberman Lab", ["Dr. Chris Thompson"]),
        ("Short", "The Twenty Minute VC (20VC): Venture Capital | Startup Funding | The Pitch", ["Jane Doe"]),
    ],
)
def test_fits_the_limit_when_anything_can(episode, show, guests):
    assert len(episode_search_title(episode, show, guests, description=" ".join(guests))) <= TITLE_MAX


def test_when_nothing_fits_the_wording_stays_whole():
    """Codex round 3: a made-up "And…" fragment is worse than a long title."""
    episode = "Trump's Destroying JD Vance And Rubio's Next Move Against the Establishment Right"
    title = episode_search_title(episode, "The Rest Is Politics: US")
    assert title == f"{episode} — The Rest Is Politics: US"
    assert "…" not in title


def test_both_guests_with_a_shorter_show_name_before_one_guest():
    title = episode_search_title(
        "Fetterman & McCormick",
        "All-In with Chamath, Jason, Sacks & Friedberg",
        ["John Fetterman", "Dave McCormick"],
        description="Senators John Fetterman and Dave McCormick.",
    )
    assert title == "John Fetterman & Dave McCormick on All-In — summary & key quotes"


def test_a_trailing_number_without_a_marker_stays():
    assert clean_episode_title("George Orwell — 1984") == "George Orwell — 1984"


def test_person_description_is_whole_clauses():
    description = person_search_description(
        "Darby Saxbe",
        headline="American clinical psychologist",
        guest_on=["The Diary Of A CEO with Steven Bartlett"],
        episodes=1,
    )
    assert description == (
        "Darby Saxbe, American clinical psychologist, guest on The Diary Of A CEO with Steven Bartlett. "
        "Read the episode summary and key quotes."
    )


def test_person_description_drops_a_long_or_slashed_credential():
    assert person_search_description(
        "James Holland",
        headline="Historian/Host of We Have Ways of Making You Talk",
        guest_on=["The Rest Is Politics", "The Rest Is History"],
        episodes=2,
    ) == ("James Holland, guest on The Rest Is Politics and The Rest Is History. Summaries and quotes from 2 episodes.")


def test_a_host_is_described_as_the_host():
    assert person_search_description(
        "Angela Duckworth", hosts=["No Stupid Questions"], guest_on=["Freakonomics Radio"], episodes=5
    ) == ("Angela Duckworth, host of No Stupid Questions. Summaries and quotes from 5 episodes.")


def test_plain_text_strips_publisher_html():
    assert plain_text("<p>The BBC&#x27;s daily news podcast.</p><p>More</p>") == "The BBC's daily news podcast. More"


# Codex review, round 2.


@pytest.mark.parametrize(
    "title, cleaned",
    [
        ("Why COVID-19 Changed Offices", "Why COVID-19 Changed Offices"),
        ("1984: Orwell and surveillance", "1984: Orwell and surveillance"),
        ("Ep 12 - Pricing", "Pricing"),
        ("#123: Pricing", "Pricing"),
    ],
)
def test_only_episode_markers_are_stripped(title, cleaned):
    assert clean_episode_title(title) == cleaned


@pytest.mark.parametrize(
    "name, evidence",
    [
        ("Fan-yun Sun", "We talk about the sun and the moon."),
        ("Ann", "Our Annual Review."),
    ],
)
def test_a_name_must_appear_as_whole_words(name, evidence):
    assert usable_guest_names([name], evidence=evidence) == []


def test_a_surname_counts_when_capitalised():
    assert usable_guest_names(["Dr. Chris Thompson"], evidence="Gut Health | Dr. Thompson") == ["Dr. Chris Thompson"]


def test_a_question_keeps_its_question_mark():
    title = episode_search_title(
        "Was the Mythos Ban Justified? Plus listener questions", "Deep Questions with Cal Newport"
    )
    assert title.startswith("Was the Mythos Ban Justified? — Deep Questions")


def test_guest_and_topic_beat_guest_alone_with_a_shorter_show_name():
    title = episode_search_title(
        "AI is Already Building AI",
        "The MAD Podcast with Matt Turck",
        ["Mostafa Dehghani"],
        description="Mostafa Dehghani of Google DeepMind.",
    )
    assert title == "Mostafa Dehghani: AI is Already Building AI — The MAD Podcast"


def test_a_long_role_falls_back_to_one_show_not_a_cut():
    description = person_search_description(
        "A Person",
        guest_on=["A" * 70, "B" * 70],
        episodes=2,
    )
    assert "…" not in description and len(description) <= 160


def test_a_summary_led_by_a_first_name_gets_the_full_name():
    from thestill.web.search_titles import lead_with_full_name

    assert lead_with_full_name("Koray argues that usefulness beats benchmarks.", ["Koray Kavukcuoglu"]) == (
        "Koray Kavukcuoglu argues that usefulness beats benchmarks."
    )
    assert (
        lead_with_full_name("Koray’s view is simple.", ["Koray Kavukcuoglu"]) == "Koray Kavukcuoglu’s view is simple."
    )
    assert lead_with_full_name("Chris explains gut health.", ["Dr. Chris Thompson"]) == (
        "Chris Thompson explains gut health."
    )
    # Not a guest's first name: unchanged.
    assert lead_with_full_name("The pair discuss chips.", ["Koray Kavukcuoglu"]) == "The pair discuss chips."
