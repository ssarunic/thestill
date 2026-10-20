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

"""Genre summary profiles: one nine-section contract, many profiles (spec #98).

Every profile emits the same nine ``## N.`` sections in the same order.
Slots 1 (Gist), 2 (Timeline), 5 (Best Quotes) and 8 (Resource List) are
fixed because code parses them; a profile fills slots 3, 4, 6, 7 and 9
inside a fixed role per slot, and brings its own rules and worked
example. ``general`` is the original prompt, byte for byte.

The profile is chosen per podcast: operator override, then the Apple
category map, then ``general``. Profiles other than ``general`` are only
chosen when listed in ``SUMMARY_PROFILES_ENABLED``.
"""

from __future__ import annotations

from dataclasses import dataclass
from types import MappingProxyType
from typing import TYPE_CHECKING, Iterable, Mapping, Optional

from structlog import get_logger

from thestill.utils.config import get_summary_profiles_enabled
from thestill.utils.podcast_categories import normalize_category_name

if TYPE_CHECKING:
    from thestill.models.podcast import Podcast

logger = get_logger(__name__)

PROFILE_SLOTS = frozenset({3, 4, 6, 7, 9})  # slots 1, 2, 5 and 8 are FIXED_SLOT_SPECS


@dataclass(frozen=True)
class SlotSpec:
    """One ``## N. <emoji> <Title>`` section and its instructions."""

    emoji: str
    title: str  # English; localised by the spec #58 rule like every other heading
    instructions: str

    def render(self, number: int, addendum: str = "") -> str:
        body = f"{self.instructions}\n{addendum}" if addendum else self.instructions
        return f"## {number}. {self.emoji} {self.title}\n{body}"


@dataclass(frozen=True)
class SummaryProfile:
    """A genre's fill of the profile slots plus its guardrails and example."""

    key: str
    version: int  # bump on any prompt text change
    slots: Mapping[int, SlotSpec]  # exactly PROFILE_SLOTS
    example: str  # worked example in this profile's shape
    rules: str = ""  # genre guardrails and tone, placed after the style guide
    quote_guidance: str = ""  # optional addition to slot 5
    resource_hint: str = ""  # optional addition to slot 8: which kinds matter most, never new kinds

    def __post_init__(self) -> None:
        if set(self.slots) != PROFILE_SLOTS:
            raise ValueError(
                f"Profile {self.key!r} must define slots {sorted(PROFILE_SLOTS)}, got {sorted(self.slots)}"
            )


# --- Fixed prompt parts (shared by every profile) ---------------------------

STYLE_GUIDE = """You are a sharp, no-nonsense research assistant. You are not a corporate consultant; you are a smart friend giving me the "too long; didn't read" version.

**Style Guide:**
* **Tone:** Conversational, friendly, and direct. Use British English.
* **Sentence Structure:** Simple and short. Avoid academic jargon (e.g., don't use words like "multifaceted," "underscores," or "necessitating").
* **Brevity:** Get to the point immediately.
* **Formatting:**
    * NO PREAMBLE. Do not say "Here is the analysis." Start directly with the first header.
    * **Bullet points:** Use `* ` (asterisk + ONE space) for bullets. NEVER add extra spaces after the asterisk.
    * **Indentation:** Use minimal indentation for nested bullets: 2 spaces per level. Example: `  * nested item`
    * **Emojis:** Use them to make the text scannable.
* **Citations:** Every claim must have a timestamp. Use [MM:SS] for episodes under 60 minutes, [HH:MM:SS] for longer episodes."""

FIXED_SLOT_SPECS: Mapping[int, SlotSpec] = MappingProxyType(
    {
        1: SlotSpec(
            "🎙️",
            "The Gist",
            "[Host] interviews [Guest(s) with roles/titles]\n\n"
            "A 2-sentence summary of the episode. Do NOT include the episode title, date, or duration"
            " - these are displayed elsewhere.",
        ),
        2: SlotSpec(
            "⏱️",
            "Timeline",
            """Break the episode into 3-6 segments showing the flow of conversation:
* [00:00 - XX:XX] **[Segment Title]:** 1 sentence summary
* [XX:XX - XX:XX] **[Segment Title]:** 1 sentence summary
* [XX:XX - End] **[Segment Title]:** 1 sentence summary""",
        ),
        5: SlotSpec(
            "💬",
            "Best Quotes",
            """* Pick 5-7 quotes that actually land (adjust based on episode length - fewer for short episodes, more for long ones).
* **Format:** "Quote text..." - Speaker [timestamp]""",
        ),
        # Spec #92 parses this bullet shape and the English kind words.
        8: SlotSpec(
            "📚",
            "Resource List",
            """* Things mentioned in the episode that a listener might look up: books, films, TV shows, podcasts, articles, papers, people, companies, products, tools.
* One bullet per thing, in exactly this shape: `* **Name** (kind): one-line description [mm:ss]`
* **Name:** its full proper name as it is known ("Paul Kedrosky", not "Kedrosky"). No author or year inside a title; list the author as a separate bullet if they matter. Never two things in one bullet ("A / B", "A & B").
* **kind:** exactly one of book, film, tv, podcast, article, paper, person, company, product, tool, place, event, other. Always these English words, whatever language the summary is in.
* **Timestamp:** where it is first discussed in substance.
* Leave out the episode's own hosts and guests, and anything nobody in the episode actually mentioned.
* At most 12 bullets, the most useful to a listener first.""",
        ),
    }
)

EXAMPLE_SEPARATOR = "---"
EXAMPLE_HEADER = "## Example Output (follow this formatting exactly)"


# --- Profiles ----------------------------------------------------------------

GENERAL = SummaryProfile(
    key="general",
    version=1,
    slots=MappingProxyType(
        {
            3: SlotSpec(
                "🧠",
                "Key Takeaways",
                "* The 3-5 things that actually matter. Short bullet points. (Cite timestamps)",
            ),
            4: SlotSpec(
                "🌶️",
                "The Drama",
                """Look for INTERPERSONAL tension, not just intellectual disagreement:

**What to catch:**
* **Deflection/Defensiveness:** Does someone dodge a question by turning it back on the questioner? ("If YOU don't like it, sell YOUR shares")
* **Personal vs. abstract:** Is someone making it personal when the question was professional? (Attacking the host vs. addressing critics generally)
* **Evasion:** Did a direct question get a non-answer? What question was avoided?
* **Tone shifts:** Does the conversation suddenly get cold/awkward/tense?
* **Power dynamics:** Is a guest challenging their host? Is someone punching down?
* **Uncomfortable moments:** Silences, subject changes, someone talking over another
* **Corporate hedging vs. straight talk:** Is someone giving PR answers when pressed?

**Format your findings (IMPORTANT: separate each drama moment clearly):**

If there are multiple drama moments, format them like this:

* **Round 1: [Short descriptive title]** [Timestamp]
  * **What happened:** Describe the moment factually (who said what to whom)
  * **Why it matters:** What does this reveal? Insecurity? Genuine disagreement? PR mode?
  * **The temperature:** Was this awkward? Hostile? Just tense?
* **Round 2: [Short descriptive title]** [Timestamp]
  * **What happened:** ...
  * **Why it matters:** ...
  * **The temperature:** ...

Number drama moments as "Round 1", "Round 2", etc. Give each round a short, punchy title that captures the conflict.

If there's only one drama moment, skip the "Round" numbering and just use the title format.

If nothing spicy happened, say so briefly. Don't force drama where there isn't any.""",
            ),
            6: SlotSpec(
                "✍️",
                "Blog Ideas",
                """* Give me 3 angles I could write about.
* **Title:** Catchy, not academic.
* **The Angle:** Why should I care? (1 sentence).
* **Main Points:** 3 rapid-fire bullets.
* **Source:** Where in the audio did this come from? [timestamp]""",
            ),
            7: SlotSpec(
                "📱",
                "Social Snippets",
                """* 3 posts for LinkedIn/X.
* Make them sound human, not like a bot.""",
            ),
            9: SlotSpec(
                "💩",
                'The "BS" Test',
                "* Did anything sound weak, circular, or overly hyped? Call it out.",
            ),
        }
    ),
    example="""## 1. 🎙️ The Gist
Sarah Chen interviews Dr. James Miller, Chief AI Officer at Stanford Medicine

A deep dive into how machine learning is transforming diagnostics and why doctors shouldn't fear the robots just yet.

## 2. ⏱️ Timeline
* [00:00 - 08:30] **Introductions:** Background on Dr. Miller's journey from radiologist to AI researcher.
* [08:30 - 22:15] **AI in Diagnostics:** How neural networks now spot tumours faster than humans.
* [22:15 - 35:00] **The Human Element:** Why AI won't replace doctors, but augment them.
* [35:00 - End] **Future Predictions:** What's coming in the next 5 years.

## 3. 🧠 Key Takeaways
* AI catches 94% of early-stage cancers vs 88% for human radiologists. [12:45]
* The FDA has approved 500+ AI medical devices since 2020. [18:30]
* Biggest barrier isn't tech—it's getting doctors to trust the black box. [28:15]

## 6. ✍️ Blog Ideas
* **Title:** Why Your Next Diagnosis Might Come From a Machine
  * **The Angle:** AI is already better than doctors at spotting certain diseases.
  * **Main Points:**
    * Radiology AI now matches expert-level accuracy.
    * Early detection rates have jumped 15% in pilot programmes.
    * Patient outcomes improve when AI assists (not replaces) doctors.
  * **Source:** [12:45, 15:20, 28:15]

## 8. 📚 Resource List
* **The Emperor of All Maladies** (book): Siddhartha Mukherjee's history of cancer, which Dr. Miller credits with his switch to oncology imaging [31:10]
* **Siddhartha Mukherjee** (person): Oncologist and author of the book above [31:10]
* **Aidoc** (company): Radiology AI startup used as the main example of an FDA-cleared tool [18:30]""",
)

PROFILES: Mapping[str, SummaryProfile] = MappingProxyType({GENERAL.key: GENERAL})


def compose_system_prompt(profile: SummaryProfile) -> str:
    """Build the summary system prompt for ``profile``.

    Order: style guide → profile rules → slots 1–9 → example. Composing
    ``general`` reproduces the original single prompt exactly.
    """
    addenda = {5: profile.quote_guidance, 8: profile.resource_hint}
    sections = [
        (FIXED_SLOT_SPECS.get(number) or profile.slots[number]).render(number, addenda.get(number, ""))
        for number in range(1, 10)
    ]
    parts = [STYLE_GUIDE, profile.rules, *sections, EXAMPLE_SEPARATOR, EXAMPLE_HEADER, profile.example]
    return "\n\n".join(part for part in parts if part)


# --- Choosing a profile --------------------------------------------------------

# Apple Podcasts category or subcategory → profile key. Keys are compared via
# ``normalize_category_name``. Subcategories are tried before categories, so
# "News / Tech News" is decided by Tech News. The explicit ``general`` rows
# stop a show whose primary category suits the original prompt from being
# moved to a genre profile by its secondary category.
CATEGORY_PROFILES: Mapping[str, str] = MappingProxyType(
    {
        normalize_category_name(name): key
        for key, names in {
            "true_crime": ["True Crime"],
            "sports": ["Sports", "Sports News"],
            "news": ["News", "Daily News", "Politics", "News Commentary", "Government", "Business News"],
            "investing": ["Investing"],
            "comedy": ["Comedy", "Comedy Interviews", "Improv", "Stand-Up"],
            "history": ["History", "Documentary"],
            "health": ["Health & Fitness", "Self-Improvement"],
            "general": ["Technology", "Tech News", "Science", "Business", "Entrepreneurship"],
        }.items()
        for name in names
    }
)


@dataclass(frozen=True)
class ProfileResolution:
    """The profile a podcast's summaries use, and why."""

    profile: SummaryProfile
    reason: str  # "override", "category: True Crime" or "default"
    candidate: Optional[str] = None  # the key that matched but is not defined or enabled

    def describe(self) -> str:
        """One-line explanation, e.g. ``general (category: True Crime → true_crime, not defined yet)``."""
        if self.candidate:
            status = "not enabled" if self.candidate in PROFILES else "not defined yet"
            return f"{self.profile.key} ({self.reason} → {self.candidate}, {status})"
        return f"{self.profile.key} ({self.reason})"


def available_profile_keys(enabled: Iterable[str]) -> frozenset[str]:
    """Defined profiles that may be chosen: ``general`` plus the enabled ones."""
    return frozenset({GENERAL.key, *(key for key in enabled if key in PROFILES)})


def resolve_summary_profile(podcast: "Podcast", enabled: Optional[Iterable[str]] = None) -> ProfileResolution:
    """Pick the summary profile for ``podcast``; first match wins.

    1. ``podcast.summary_profile`` when it names a defined profile.
    2. ``CATEGORY_PROFILES`` on primary subcategory, primary category,
       secondary subcategory, secondary category.
    3. ``general``.

    A match that is not defined or not enabled resolves to ``general``
    and is reported as ``candidate``. ``enabled`` defaults to
    ``SUMMARY_PROFILES_ENABLED``. Never raises.
    """
    if enabled is None:
        enabled = get_summary_profiles_enabled()
    key, reason = GENERAL.key, "default"
    override = podcast.summary_profile
    if override and override in PROFILES:
        key, reason = override, "override"
    else:
        if override:
            logger.warning("summary_profile_override_unknown", podcast_id=podcast.id, summary_profile=override)
        for category in (
            podcast.primary_subcategory,
            podcast.primary_category,
            podcast.secondary_subcategory,
            podcast.secondary_category,
        ):
            mapped = CATEGORY_PROFILES.get(normalize_category_name(category))
            if mapped:
                key, reason = mapped, f"category: {category}"
                break

    if key in available_profile_keys(enabled):
        return ProfileResolution(profile=PROFILES[key], reason=reason)
    return ProfileResolution(profile=GENERAL, reason=reason, candidate=key)
