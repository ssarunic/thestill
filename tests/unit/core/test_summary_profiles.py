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

"""Spec #98 Phase 1 — summary profiles: composition, resolution, provenance."""

import json
import re
from pathlib import Path
from types import MappingProxyType

import pytest

from thestill.core import summary_profiles
from thestill.core.post_processor import EpisodeMetadata, TranscriptSummarizer
from thestill.core.summary_artifacts import (
    content_sha256,
    load_valid_summary_manifest,
    summary_manifest_key,
    write_summary_manifest,
)
from thestill.core.summary_profiles import (
    GENERAL,
    PROFILE_SLOTS,
    PROFILES,
    SlotSpec,
    SummaryProfile,
    compose_system_prompt,
    resolve_summary_profile,
)
from thestill.models.podcast import Podcast
from thestill.utils.file_storage import LocalFileStorage

# Today's single prompt, frozen before the refactor. The pre-commit
# end-of-file fixer adds one trailing newline the prompt does not have.
_FROZEN_GENERAL_V1 = (
    (Path(__file__).parent / "fixtures" / "summary_system_prompt_general_v1.txt")
    .read_text(encoding="utf-8")
    .removesuffix("\n")
)

_HEADING_RE = re.compile(r"^## (\d)\. ", re.MULTILINE)


def _profile(key: str, **overrides) -> SummaryProfile:
    slots = {n: SlotSpec("🧪", f"{key} slot {n}", f"* {key} instructions for slot {n}") for n in PROFILE_SLOTS}
    fields = {"key": key, "version": 1, "slots": MappingProxyType(slots), "example": f"## 1. 🎙️ The Gist\n{key}"}
    fields.update(overrides)
    return SummaryProfile(**fields)


def _podcast(**fields) -> Podcast:
    return Podcast(rss_url="https://example.com/feed.xml", title="Show", description="", **fields)


class _RecordingProvider:
    def __init__(self) -> None:
        self.calls: list[dict] = []

    def get_model_name(self) -> str:
        return "gpt-4o-mini"

    def chat_completion(self, **kwargs) -> str:
        self.calls.append(kwargs)
        return "## 1. 🎙️ The Gist\nok"


# --- Composition ---------------------------------------------------------------


def test_general_v1_reproduces_the_original_prompt_byte_for_byte():
    assert GENERAL.version == 1
    assert compose_system_prompt(GENERAL) == _FROZEN_GENERAL_V1


@pytest.mark.parametrize("profile", list(PROFILES.values()), ids=list(PROFILES))
def test_every_profile_fills_exactly_the_profile_slots_and_emits_nine_headings_in_order(profile):
    assert set(profile.slots) == PROFILE_SLOTS
    instructions = compose_system_prompt(profile).split(summary_profiles.EXAMPLE_HEADER, 1)[0]
    assert [int(n) for n in _HEADING_RE.findall(instructions)] == list(range(1, 10))


def test_profile_must_define_exactly_the_profile_slots():
    with pytest.raises(ValueError, match="must define slots"):
        _profile("broken", slots=MappingProxyType({3: SlotSpec("x", "x", "x")}))


def test_genre_profile_keeps_fixed_slots_and_adds_rules_guidance_and_example():
    profile = _profile(
        "true_crime",
        rules='* Use "alleged" for anyone not convicted.',
        quote_guidance="* Never quote a victim's family for colour.",
        resource_hint="* Cases are `event`.",
    )
    prompt = compose_system_prompt(profile)

    assert "## 1. 🎙️ The Gist\n[Host] interviews" in prompt
    assert "## 2. ⏱️ Timeline\nBreak the episode" in prompt
    assert "## 3. 🧪 true_crime slot 3\n* true_crime instructions for slot 3" in prompt
    assert prompt.index('"alleged"') < prompt.index("## 1. ")  # rules follow the style guide
    assert "[timestamp]\n* Never quote a victim's family" in prompt  # appended to slot 5
    assert "listener first.\n* Cases are `event`." in prompt  # appended to slot 8
    assert prompt.endswith("## 1. 🎙️ The Gist\ntrue_crime")
    assert "Key Takeaways" not in prompt


# --- Summarizer ----------------------------------------------------------------


@pytest.mark.parametrize("profile", [*PROFILES.values(), _profile("news")], ids=lambda p: p.key)
def test_language_replacement_applies_to_every_profile(profile):
    summarizer = TranscriptSummarizer(_RecordingProvider())  # type: ignore[arg-type]

    prompt = summarizer._get_formatted_system_prompt(EpisodeMetadata(title="E", language="hr"), profile)

    assert "Write the entire summary in Croatian." in prompt
    assert "Use British English." not in prompt


def test_summarize_sends_the_chosen_profiles_prompt():
    provider = _RecordingProvider()
    summarizer = TranscriptSummarizer(provider)  # type: ignore[arg-type]

    summarizer.summarize("Some transcript.", EpisodeMetadata(title="E", episode_id="ep-1"), profile=_profile("news"))

    assert "## 3. 🧪 news slot 3" in provider.calls[0]["messages"][0]["content"]


def test_summarize_defaults_to_general():
    provider = _RecordingProvider()
    TranscriptSummarizer(provider).summarize("Some transcript.")  # type: ignore[arg-type]

    assert provider.calls[0]["messages"][0]["content"].startswith(_FROZEN_GENERAL_V1)


# --- Resolution ------------------------------------------------------------------


@pytest.fixture
def with_news(monkeypatch):
    """A defined ``news`` profile, as Phase 3 will add."""
    news = _profile("news")
    monkeypatch.setattr(summary_profiles, "PROFILES", MappingProxyType({"general": GENERAL, "news": news}))
    return news


@pytest.fixture
def all_genres_defined(monkeypatch):
    """Every key the category map names, defined — independent of how many profiles have shipped."""
    keys = set(summary_profiles.CATEGORY_PROFILES.values()) - {GENERAL.key}
    profiles = {GENERAL.key: GENERAL, **{key: _profile(key) for key in keys}}
    monkeypatch.setattr(summary_profiles, "PROFILES", MappingProxyType(profiles))
    return sorted(keys)


@pytest.mark.parametrize(
    "fields, expected_key, expected_reason",
    [
        ({}, "general", "default"),
        ({"primary_category": "Arts", "primary_subcategory": "Books"}, "general", "default"),
        ({"primary_category": "True Crime"}, "true_crime", "category: True Crime"),
        ({"primary_category": "News", "primary_subcategory": "Daily News"}, "news", "category: Daily News"),
        ({"primary_category": "Government"}, "news", "category: Government"),
        # Subcategory before category: Tech News beats its parent News.
        ({"primary_category": "News", "primary_subcategory": "Tech News"}, "general", "category: Tech News"),
        ({"primary_category": "News", "primary_subcategory": "Sports News"}, "sports", "category: Sports News"),
        ({"primary_category": "Business", "primary_subcategory": "Investing"}, "investing", "category: Investing"),
        ({"primary_category": "Business", "primary_subcategory": "Management"}, "general", "category: Business"),
        # An unmapped subcategory falls through to its category.
        ({"primary_category": "Sports", "primary_subcategory": "Cricket"}, "sports", "category: Sports"),
        (
            {"primary_category": "Education", "primary_subcategory": "Self-Improvement"},
            "health",
            "category: Self-Improvement",
        ),
        # A general-suited primary category is not moved by the secondary one.
        ({"primary_category": "Technology", "secondary_category": "True Crime"}, "general", "category: Technology"),
        # Secondary fallback when the primary is unmapped.
        ({"primary_category": "Arts", "secondary_category": "Comedy"}, "comedy", "category: Comedy"),
        (
            {
                "primary_category": "Arts",
                "secondary_category": "Society & Culture",
                "secondary_subcategory": "Documentary",
            },
            "history",
            "category: Documentary",
        ),
        # Case and punctuation drift from feeds still matches.
        ({"primary_category": "health  & fitness"}, "health", "category: health  & fitness"),
    ],
)
def test_category_resolution(all_genres_defined, fields, expected_key, expected_reason):
    resolution = resolve_summary_profile(_podcast(**fields), enabled=all_genres_defined)

    assert resolution.profile.key == expected_key
    assert resolution.reason == expected_reason
    assert resolution.candidate is None


def test_undefined_profile_falls_back_to_general_and_names_the_candidate(monkeypatch):
    monkeypatch.setattr(summary_profiles, "PROFILES", MappingProxyType({GENERAL.key: GENERAL}))

    resolution = resolve_summary_profile(_podcast(primary_category="True Crime"), enabled=["true_crime"])

    assert resolution.profile is GENERAL
    assert resolution.candidate == "true_crime"
    assert resolution.describe() == "general (category: True Crime → true_crime, not defined yet)"


def test_enabled_defined_profile_is_chosen(with_news):
    podcast = _podcast(primary_category="News", primary_subcategory="Politics")

    resolution = resolve_summary_profile(podcast, enabled=["news"])

    assert resolution.profile is with_news
    assert resolution.candidate is None
    assert resolution.describe() == "news (category: Politics)"


def test_defined_but_disabled_profile_falls_back_to_general(with_news):
    resolution = resolve_summary_profile(_podcast(primary_category="News"), enabled=[])

    assert resolution.profile is GENERAL
    assert resolution.describe() == "general (category: News → news, not enabled)"


def test_override_wins_over_category(with_news):
    podcast = _podcast(primary_category="True Crime", summary_profile="news")

    resolution = resolve_summary_profile(podcast, enabled=["news"])

    assert resolution.profile is with_news
    assert resolution.reason == "override"


def test_general_override_pins_a_show_off_its_genre():
    resolution = resolve_summary_profile(_podcast(primary_category="Comedy", summary_profile="general"), enabled=[])

    assert resolution.describe() == "general (override)"


def test_unknown_override_key_falls_back_to_category_without_raising():
    podcast = _podcast(primary_category="True Crime", summary_profile="renamed_profile")

    resolution = resolve_summary_profile(podcast, enabled=[])

    assert resolution.profile is GENERAL
    assert resolution.reason == "category: True Crime"


def test_enabled_list_defaults_to_the_env(monkeypatch, with_news):
    podcast = _podcast(primary_category="News")
    monkeypatch.setenv("SUMMARY_PROFILES_ENABLED", " News , sports,")
    assert resolve_summary_profile(podcast).profile is with_news
    monkeypatch.setenv("SUMMARY_PROFILES_ENABLED", "")
    assert resolve_summary_profile(podcast).profile is GENERAL


# --- Provenance ------------------------------------------------------------------


def test_manifest_records_profile_and_version(tmp_path):
    storage = LocalFileStorage(str(tmp_path))
    profile = _profile("news", version=3)

    write_summary_manifest(
        storage, summary_key="s/ep_summary.md", summary_content="body", canonical_language="en", profile=profile
    )

    manifest = load_valid_summary_manifest(storage, summary_key="s/ep_summary.md", summary_content="body")
    assert manifest is not None
    assert (manifest.profile, manifest.profile_version) == ("news", 3)


def test_manifest_written_before_profiles_existed_reads_as_general_v1(tmp_path):
    storage = LocalFileStorage(str(tmp_path))
    storage.write_text(
        summary_manifest_key("s/ep_summary.md"),
        json.dumps({"schema_version": 1, "canonical_language": "en", "summary_sha256": content_sha256("body")}),
    )

    manifest = load_valid_summary_manifest(storage, summary_key="s/ep_summary.md", summary_content="body")
    assert manifest is not None
    assert (manifest.profile, manifest.profile_version) == ("general", 1)


def test_manifest_defaults_to_general_when_no_profile_is_passed(tmp_path):
    manifest = write_summary_manifest(
        LocalFileStorage(str(tmp_path)), summary_key="s/ep_summary.md", summary_content="body", canonical_language="en"
    )

    assert (manifest.profile, manifest.profile_version) == ("general", 1)
