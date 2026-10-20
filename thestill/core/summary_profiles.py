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
example. ``general`` v1 was the original prompt byte for byte; v2 adds the
shared rules and the format-aware Gist line.

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
    # The exact line the instructions tell the model to write when the slot
    # has nothing for this episode ("Nothing contested"). Consumers skip it;
    # a test checks it appears in the instructions.
    empty: str = ""

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

# Spec #98 Phase 2: rules every profile shares, placed after the style guide.
# Written in the style guide's own voice: a stiffer, compliance-style wording
# measurably pulled the summaries towards a reporting register. The
# publication date the second bullet refers to is sent with the transcript
# (``TranscriptSummarizer``), not in the system prompt.
SHARED_RULES = """**Stick to the episode:**
* Only say what the episode says. Don't top it up with things you know, and don't "fix" facts you think are out of date.
* Scores, prices, polls and who holds which job are as of the publication date (it's above the transcript), so give them the way the episode does. "Next spring" stays "next spring"; don't turn it into a year.
* Being accurate doesn't mean being formal. Keep the smart-friend voice."""

FIXED_SLOT_SPECS: Mapping[int, SlotSpec] = MappingProxyType(
    {
        1: SlotSpec(
            "🎙️",
            "The Gist",
            # Spec #98 Phase 2: one line on who is speaking and how, chosen from
            # explicit cases. A topic in this line turned it into a listings
            # blurb; a blended list of examples produced "X and Y with guest Z".
            # The preview drops this line.
            "One short line with just who is speaking and how. Pick the case that fits:\n"
            '* A guest is being questioned: "[host names] interview [guest name, role]"'
            ' (one host: "[host name] interviews [guest name, role]").\n'
            '* The host or hosts on their own, no guest: "[host names] chat" (one host: "[host name] talks it through").\n'
            '* An anchor with a panel of pundits: "[host name] with a panel: [names]".\n'
            '* A narrated story: "Narrated by [host name]".\n'
            '* An audio drama: "A full-cast audio drama, introduced by [name]".\n'
            "Leave the topic for the summary below. Replace every bracketed part with the real names; never leave"
            " brackets in. Use only names and roles the episode gives.\n\n"
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

# Every profile's example is made up and tied to made-up names, because the model
# copied generic example quotes into real episodes (spec #98 Phase 3).
_MADE_UP_EXAMPLE = (
    "* The example at the end is made up. Never reuse its names, quotes, scores, numbers or facts:"
    " everything you write comes from this episode."
)

# general v4 (spec #98 ablation): sections that work for any format. v3's
# The Drama, Blog Ideas and Social Snippets lost 19–1 to these on a mixed
# 20-episode set: they invited invented tension, quotes and facts on shows
# with no drama to report.
GENERAL = SummaryProfile(
    key="general",
    version=4,  # v2: shared rules + format-aware Gist; v3: explicit interview / no-guest cases; v4: format-neutral sections
    rules="\n".join(
        [
            "**Read the format first:**",
            _MADE_UP_EXAMPLE,
            "* Notice how people are talking: an interview, hosts chatting, a panel, a narrated story, or a mix that"
            " changes part-way through. Every section below works for any format.",
            "* If a section has nothing real to say for this episode, say so in one line. Never invent tension,"
            " scepticism or a flop to fill it.",
        ]
    ),
    slots=MappingProxyType(
        {
            3: SlotSpec(
                "🧠",
                "Key Takeaways",
                "* The 3-5 things a listener should come away with, whatever the format: the main points of an"
                " interview, the stories in a round-up, how a narrated story unfolds and ends, the results in a sports"
                " show. Short bullets, each with a timestamp.",
            ),
            4: SlotSpec(
                "🌶️",
                "Where It Got Interesting",
                """Real tension or disagreement, matched to how the episode works:
* **An interview:** pushback, a dodged question, a guest going into PR mode [timestamp].
* **Hosts or a panel:** where they genuinely disagree, and who said what [timestamp].
* **A narrated story:** where accounts, witnesses or sources conflict [timestamp].

Friendly banter and teasing aren't conflict. If nothing was contested, say "Nothing contested" and move on.""",
                empty="Nothing contested",
            ),
            6: SlotSpec(
                "🎧",
                "Worth a Listen?",
                "* In two or three lines: what kind of listen it is (a deep interview, a chatty catch-up, a story), who'll"
                " enjoy it, and the best part to skip to.",
            ),
            7: SlotSpec(
                "🗣️",
                "Talking Points",
                "* Three or four things from this episode worth bringing up with friends, a line each, in your own words"
                " [timestamp].",
            ),
            9: SlotSpec(
                "🔍",
                "Reality Check",
                """* Only where someone actually makes a claim: anything weak, circular, overhyped or one-sided, and speculation presented as fact [timestamp].
* If nobody makes claims worth checking, say "Nothing to check".""",
                empty="Nothing to check",
            ),
        }
    ),
    # Made-up names and deliberately plain quotes: the ablation draft's punchy
    # example quote turned up verbatim in real summaries.
    example="""## 1. 🎙️ The Gist
Nadia Fenwick and Owen Tull interview economist Rhiannon Bale

Rhiannon reckons the four-day week works in offices and falls apart on hospital wards, and Owen isn't having it. Nadia keeps them both honest with the numbers from the Calderbrook trial.

## 2. ⏱️ Timeline
* [00:00 - 08:10] **Catch-Up:** Nadia and Owen on their own week, briefly.
* [08:10 - 29:40] **The Calderbrook Trial:** Rhiannon walks through how the trial was run.
* [29:40 - End] **Hospitals:** Where Rhiannon and Owen fall out.

## 3. 🧠 Key Takeaways
* Rhiannon says the Calderbrook offices kept output flat on four days [12:20]
* She thinks shift work like nursing can't copy it without more staff [31:05]

## 4. 🌶️ Where It Got Interesting
* **Owen v Rhiannon on hospitals:** Owen says rotas could absorb it; Rhiannon says the trial never covered shift work [33:40]

## 5. 💬 Best Quotes
* "We measured output in the same way before and after, so the comparison is fair." - Rhiannon Bale [14:05]

## 6. 🎧 Worth a Listen?
* A proper interview with a real disagreement in the last third. If you're short on time, start at 29:40.

## 7. 🗣️ Talking Points
* Office output didn't drop on four days, according to the trial Rhiannon describes [12:20]
* Nobody in the room could say how a hospital would do it [33:40]

## 8. 📚 Resource List
* **Calderbrook Four-Day Week Trial** (event): The trial Rhiannon's numbers come from [09:05]

## 9. 🔍 Reality Check
* Owen's "rotas will absorb it" has no numbers behind it, and Rhiannon says so [34:20]""",
)

# Spec #98 Phase 3 genre profiles. Instructions and examples are written in
# the style guide's voice, and each profile keeps one slot with personality
# (Talking Points, Who's Who): list-shaped sections alone lost every voice
# comparison. Each example shows all nine sections (a slot the example skips
# is a slot the model drops) and is tied to made-up names, because the model
# copied a generic example quote into real episodes.

NEWS = SummaryProfile(
    key="news",
    version=1,
    rules=f"""**News rules:**
{_MADE_UP_EXAMPLE}
* Say who said what: "the minister said", "according to the BBC", "Emily reckons". Keep what's reported apart from what the hosts think.
* Stories are as of the publication date. Don't fill in what happened next.""",
    slots=MappingProxyType(
        {
            3: SlotSpec(
                "📰",
                "The Stories",
                """* One bullet per story, in the order the episode covers them. Bold the story's name, then tell it in two or three plain sentences: what happened, why it matters and what's coming next, and who says so [timestamp].
* A single-story episode gets one bullet with a bit more room.""",
            ),
            4: SlotSpec(
                "⚔️",
                "Contested",
                """* Where sides, sources or the people in the room disagree, and what each one says [timestamp].
* If the episode only gives one side, say "Nothing contested here" and name the side it gives.""",
                empty="Nothing contested here",
            ),
            6: SlotSpec(
                "📅",
                "Watch List",
                """* Upcoming votes, deadlines, court dates, releases or announcements the episode mentions, with the timing as the episode puts it ("next spring", "Thursday") [timestamp].
* If nothing is coming up, say so in one line.""",
            ),
            7: SlotSpec(
                "🗣️",
                "Talking Points",
                """* Three or four things from this episode worth bringing up over dinner, a line each, in your own words [timestamp].""",
            ),
            9: SlotSpec(
                "🌀",
                "Spin Check",
                """* Loaded framing, talking points nobody challenged, claims resting on a single unnamed source, and counter-arguments the episode skips.
* If the show has an obvious slant, say so plainly. If it was straight down the middle, say that.""",
            ),
        }
    ),
    example="""## 1. 🎙️ The Gist
Priya Shah and Tom Ellis interview reporter Dan Moore

Northfield council's budget hole turns out to be twice what it told everyone in March, and Dan has the papers to prove it. Plus the tram strike that's back on, which is bad news if you were planning to get anywhere on Thursday.

## 2. ⏱️ Timeline
* [00:00 - 14:20] **The Budget Hole:** Dan walks through the leaked figures.
* [14:20 - 26:05] **Who Knew What:** The hosts pick apart the council's timeline.
* [26:05 - End] **Strike Watch:** What Thursday's walkout means for commuters.

## 3. 📰 The Stories
* **Northfield's missing millions:** Leaked papers put the gap at £84m, not the £40m the council announced, Dan says [03:10]. Priya reckons libraries and the night buses go first [09:45], and the full council votes on a new budget next month [12:30].
* **Tram strike, take three:** The union confirmed Thursday's walkout after talks collapsed, Tom says, and it's the third this year on the same line [26:40]. Talks start again Monday [30:05].

## 4. ⚔️ Contested
* **When did the council know?** Dan says finance officers flagged the gap in January; the council insists it only found out last week [17:20].

## 5. 💬 Best Quotes
* "Nobody at Northfield can tell me who read the January memo." - Dan Moore [18:02]

## 6. 📅 Watch List
* The budget vote, "next month" [12:30]
* Tram talks resume Monday [30:05]

## 7. 🗣️ Talking Points
* Northfield's January memo: who read it, and why did nobody say anything? [17:20]
* Three tram strikes in a year on one line, and the operator still won't comment [28:15]

## 8. 📚 Resource List
* **Northfield Budget Report** (article): The council's own March report that Dan picks apart [04:10]

## 9. 🌀 Spin Check
* "Unforeseen pressures" went unchallenged, though the January memo suggests the pressures were very much foreseen [18:55]
* The memo itself comes from one unnamed source in the finance team [19:30]""",
)

SPORTS = SummaryProfile(
    key="sports",
    version=1,
    rules=f"""**Sports rules:**
{_MADE_UP_EXAMPLE}
* Scores, results, injuries and transfers are exactly what the episode says, as of the publication date. Never add or correct one from your own knowledge, never give a score the episode doesn't, and if the hosts aren't sure, say they aren't sure.""",
    slots=MappingProxyType(
        {
            3: SlotSpec(
                "🏟️",
                "Results & News",
                """* First bullet: **Spoilers:** the matches or events whose results this episode gives away.
* Then the scores, standings, injuries, transfers and trades that come up, each as the episode tells it [timestamp].""",
            ),
            4: SlotSpec(
                "🔥",
                "Hot Takes & Clashes",
                """The arguments and feuds between the pundits, players or managers.

* **Round 1: [Short punchy title]** [Timestamp]
  * **The take:** who said what
  * **The pushback:** who disagreed, and how
* **Round 2: ...**

If everyone agreed, say so in one line.""",
            ),
            6: SlotSpec(
                "🔮",
                "Predictions on Record",
                """* Who predicted what, and when we'll know if they were right [timestamp].
* No predictions? Say so in one line.""",
            ),
            7: SlotSpec(
                "🍻",
                "Talking Points",
                """* Three or four things worth bringing up in the group chat, a line each, in your own words [timestamp].""",
            ),
            9: SlotSpec(
                "🧢",
                "Hype Check",
                """* Homerism, recency bias, and takes that ignore the numbers the episode itself quotes.
* Nothing overcooked? Say so.""",
            ),
        }
    ),
    example="""## 1. 🎙️ The Gist
Sam Carter with a panel: Leon Hughes and Maria Lopes

Ashford snatch it at the death to keep the title race alive, and the panel can't agree whether Kowalski's half-time switch was genius or luck. Plus the rumour that has everyone checking their phones.

## 2. ⏱️ Timeline
* [00:00 - 18:30] **Ashford 4-3 Millbrook:** The match, the red card and the winner.
* [18:30 - 31:10] **The Run-In:** Who has the easier last five.
* [31:10 - End] **Transfer Talk:** The striker rumour and the injury news.

## 3. 🏟️ Results & News
* **Spoilers:** Ashford v Millbrook, and the result of Saturday's derby.
* Ashford beat Millbrook 4-3 with a 94th-minute winner [02:15]
* Millbrook's keeper is out for six weeks with a hamstring injury, Leon says [22:40]
* Ashford are two points clear with five to play, per Sam [19:05]

## 4. 🔥 Hot Takes & Clashes
* **Round 1: Genius or Luck?** [08:30]
  * **The take:** Leon says Anton Kowalski's triple change won it.
  * **The pushback:** Maria reckons Millbrook were just out on their feet.

## 5. 💬 Best Quotes
* "Kowalski's switch was the only thing that worked all afternoon." - Leon Hughes [09:12]

## 6. 🔮 Predictions on Record
* Maria: Ashford drop points at Kingsbury in a fortnight [27:50]
* Leon: the striker signs before the window shuts [33:10]

## 7. 🍻 Talking Points
* Was the red card actually a red? The panel split two to one [05:40]
* Ashford's last five look kinder on paper, and Maria isn't buying it [24:30]

## 8. 📚 Resource List
* **Anton Kowalski** (person): Millbrook's manager, whose triple change the panel argues about [09:12]

## 9. 🧢 Hype Check
* "Best side in the country" got thrown around after a game Maria herself said Ashford were lucky to win [14:05]""",
)

HISTORY = SummaryProfile(
    key="history",
    version=1,
    rules=f"""**History rules:**
{_MADE_UP_EXAMPLE}
* Keep to the history the episode tells. Use only the dates, numbers and names the hosts actually say: if you know a date they don't give, leave it out.
* Where the hosts say historians disagree or the record is thin, say so rather than picking a side.""",
    resource_hint="* For history, the people, places, events and books the episode leans on matter most.",
    slots=MappingProxyType(
        {
            3: SlotSpec(
                "📜",
                "Story Arc",
                """* Tell the story in four to six bullets, in order, each one a vivid sentence you'd say out loud to a friend: where we are, what's at stake, what turns, how it ends [timestamp].
* Finish with where this part leaves off and what's next, if it's part of a series.""",
            ),
            4: SlotSpec(
                "🏛️",
                "Myth vs Record",
                """* Popular beliefs the episode corrects, and where historians disagree: the myth, then what the episode says the record shows [timestamp].
* If no myths come up, say so in one line.""",
            ),
            6: SlotSpec(
                "🗓️",
                "Key Dates & Places",
                """* A short chronology, using only the dates and places the hosts actually say out loud [timestamp].""",
            ),
            7: SlotSpec(
                "🎭",
                "Who's Who",
                """* The central figures, a line each, as the hosts portray them: what they wanted, what they did, and what the hosts make of them [timestamp].
* Only people the episode actually talks about.""",
            ),
            9: SlotSpec(
                "🔍",
                "Source Check",
                """* Claims with no source given, dramatised dialogue presented as fact, and judging the past by today's standards without saying so.
* All above board? Say so.""",
            ),
        }
    ),
    example="""## 1. 🎙️ The Gist
Anna Clarke and Mark Webb chat

Part two of the siege of Harrowby, and the town is starving while General Ross waits for a relief army that never turns up. Mark makes a good case that the famous surrender speech was cooked up decades later.

## 2. ⏱️ Timeline
* [00:00 - 12:40] **Previously:** A quick recap of how the siege started.
* [12:40 - 35:15] **The Long Winter:** Hunger, desertions and the failed night raid.
* [35:15 - End] **The Surrender:** The terms, and the myth that grew up around them.

## 3. 📜 Story Arc
* Harrowby's walls have held since the autumn, but by midwinter the garrison is eating its horses, then its boots [03:20]
* Ross is out of options and bets everything on a night raid on the siege guns [18:40]
* It's a disaster: a third of his men never come back, and the town knows it's finished [21:45]
* In March he hands Harrowby over, on terms so generous his own officers smell a bribe [38:10]
* That's the end of part two; part three follows Ross to his trial [47:50]

## 4. 🏛️ Myth vs Record
* **The myth:** Ross gave a defiant speech from the walls. **The record:** Mark says it first appears in a memoir written forty years later, by a man who wasn't there [41:30]

## 5. 💬 Best Quotes
* "Nobody who was actually on Harrowby's walls ever mentions that speech." - Mark Webb [42:05]

## 6. 🗓️ Key Dates & Places
* Autumn: the siege of Harrowby begins [04:10]
* Midwinter: the night raid on the siege guns [21:45]
* March: the surrender [38:10]

## 7. 🎭 Who's Who
* **General Ross:** Stubborn, proud and, by Anna's reading, far better at surrendering than fighting [36:20]
* **The mayor of Harrowby:** Played both sides all winter; Mark calls him "the only real winner of the siege" [27:15]

## 8. 📚 Resource List
* **The Harrowby Winter** (book): The modern history Anna recommends on the siege [46:20]

## 9. 🔍 Source Check
* The death toll for the night raid comes from one diary, and Mark thinks it's inflated [23:10]""",
)

TRUE_CRIME = SummaryProfile(
    key="true_crime",
    version=1,
    rules=f"""**True crime rules (these override the tone and emoji guidance above):**
{_MADE_UP_EXAMPLE}
* Still a smart friend, but a sober one. These are real people and real harm: no jokes about victims, no emojis outside the section headings, no "temperature" ratings.
* Use "alleged" or "accused" for anyone not convicted. Never state or imply guilt beyond what the episode says the record shows.
* Name private individuals only as the episode names them, and never bring in a suspect the episode doesn't.
* Tips, rumours, listener theories and the hosts' own hunches are speculation, never fact. Refer to victims by name, with respect.""",
    quote_guidance="* Pick quotes that explain the case or the people in it, never ones that dwell on violence.",
    resource_hint="* For true crime, the case itself (event), the people, places, documentaries, books and court documents matter most.",
    slots=MappingProxyType(
        {
            3: SlotSpec(
                "🗂️",
                "Case File",
                """* The victim or victims, the suspect or accused, where it happened and the key dates, as bullets [timestamp].
* Always end with a **Status:** bullet: unsolved, charged, convicted, acquitted, on appeal, or new evidence, as the episode describes it.""",
            ),
            4: SlotSpec(
                "⚖️",
                "Facts vs Theories",
                """* Each contested point, tagged with how solid it is and who says so [timestamp]:
  * **Established:** court record, official statement, evidence the episode cites
  * **Alleged:** claimed by police, prosecution or a party, but not proven
  * **Speculation:** a host's, guest's or listener's theory""",
            ),
            6: SlotSpec(
                "🧩",
                "Where This Episode Fits",
                """* Part of a series? Say which part, what's new compared with earlier episodes, the open threads and any cliffhanger.
* A one-off? Say so in one line.""",
            ),
            7: SlotSpec(
                "🕯️",
                "Content Notes",
                """* What the episode describes that a listener may want warning of: violence, abuse, harm to children, suicide [timestamp].
* "None" if there's nothing.""",
            ),
            9: SlotSpec(
                "🔍",
                "Speculation Check",
                """* Leaps the hosts make, accusations the evidence doesn't support, and one-sided sourcing.
* If they stuck to the record, say so.""",
            ),
        }
    ),
    example="""## 1. 🎙️ The Gist
Narrated by Clare Dunn

How Ellen Price's disappearance went cold for eleven years, and why one witness statement that was filed and never followed up is back in front of the police. Clare is careful to keep what's on file apart from what Ellen's family believes.

## 2. ⏱️ Timeline
* [00:00 - 11:30] **The Disappearance:** The last confirmed sighting of Ellen Price.
* [11:30 - 29:45] **The First Investigation:** What the police did, and didn't, do.
* [29:45 - End] **The Statement:** The witness account that was filed and forgotten.

## 3. 🗂️ Case File
* **Victim:** Ellen Price, 24, a nurse [01:15]
* **Accused:** No one has been charged [27:40]
* **Where and when:** Last seen leaving Harlow bus station, per the case file [03:05]
* **Status:** Unsolved; reopened this year after the family's appeal [31:20]

## 4. ⚖️ Facts vs Theories
* **Established:** CCTV shows Ellen leaving the station alone at 22:14, the police report says [05:30]
* **Alleged:** A former neighbour told police he saw her get into a car; he later withdrew the statement [33:10]
* **Speculation:** Clare's guest thinks the car belonged to someone Ellen knew, which no record supports [41:05]

## 5. 💬 Best Quotes
* "Ellen's statement file was eleven pages, and page nine was never read." - Clare Dunn [30:02]

## 6. 🧩 Where This Episode Fits
* Part one of two. Part two follows the reopened investigation [47:50]

## 7. 🕯️ Content Notes
* A missing-person case and a family's grief; no graphic description [00:30]

## 8. 📚 Resource List
* **Disappearance of Ellen Price** (event): The case the episode covers [01:15]

## 9. 🔍 Speculation Check
* The guest's theory about the car is presented with more confidence than one withdrawn statement can carry [41:05]""",
)

COMEDY = SummaryProfile(
    key="comedy",
    version=1,
    rules=f"""**Comedy rules:**
{_MADE_UP_EXAMPLE}
* Playful is good: this is a comedy show, so have fun with it. But only report jokes and bits that actually happen, and credit each line to whoever said it.
* Give a bit enough setup to work out of context, then get out of the way. Don't explain the joke.""",
    quote_guidance="* Pick the lines that are funniest out of context, with a few words of setup where they need it.",
    slots=MappingProxyType(
        {
            3: SlotSpec(
                "😂",
                "Best Bits",
                """* The funniest moments, a line of setup each, so this works as a way into the audio [timestamp].""",
            ),
            4: SlotSpec(
                "🌶️",
                "Banter & Roasts",
                """* Who got roasted, by whom, and whether it landed [timestamp].
* "Friendly all round" is a fine answer.""",
                empty="Friendly all round",
            ),
            6: SlotSpec(
                "🔁",
                "Running Jokes & Segments",
                """* Callbacks and recurring segments (listener mail, games, regular bits), and what they are for someone new to the show [timestamp].
* None? Say so in one line.""",
            ),
            7: SlotSpec(
                "🎧",
                "Worth a Full Listen?",
                """* The energy, the chemistry with any guest, which part to skip to, and who'll enjoy it, in two or three lines.""",
            ),
            9: SlotSpec(
                "🙃",
                "What Fell Flat",
                """* Only bits the people in the room treat as not landing: a groan, "that didn't work", someone moving swiftly on [timestamp]. Don't judge what's funny yourself, and a deliberate or self-deprecating bit isn't a flop.
* If nothing like that happens, say "Everything landed".""",
                empty="Everything landed",
            ),
        }
    ),
    example="""## 1. 🎙️ The Gist
Jess Harper and Dan Kerr interview comedian Priya Nair

Priya arrives with a list of every hotel she's ever been thrown out of, and Dan is horrified to learn he's on it. Jess spends most of the episode trying, and failing, to get anyone back on topic.

## 2. ⏱️ Timeline
* [00:00 - 09:40] **Warm-Up:** Dan's disastrous new haircut gets the full treatment.
* [09:40 - 31:20] **Priya's Hotel List:** Five hotels, five ejections, one fire alarm.
* [31:20 - End] **Listener Mail:** A question about Dan's haircut, inevitably.

## 3. 😂 Best Bits
* Priya explains the Brighton fire alarm was "technically the toaster's idea" [14:05]
* Dan realises halfway through Priya's story that he was the one who called reception [22:40]
* Jess reads out a listener email that is just a drawing of Dan's haircut, described in words [33:15]

## 4. 🌶️ Banter & Roasts
* Jess and Priya team up on Dan's haircut for a solid five minutes; he takes it well, then badly [04:30]

## 5. 💬 Best Quotes
* "It was the Seaview in Hove, room two-one-four, and yes, the toaster was in the wardrobe." - Priya Nair [16:20]

## 6. 🔁 Running Jokes & Segments
* **The Haircut:** Dan's haircut keeps coming back, and every callback is worse for him [04:30, 28:10, 33:15]
* **Listener Mail:** The last ten minutes, where listeners ask the questions nobody should answer [31:20]

## 7. 🎧 Worth a Full Listen?
* High energy and Priya fits right in. If you only have ten minutes, skip to the hotel list at 9:40.

## 8. 📚 Resource List
* **The Seaview** (place): The Hove hotel in Priya's toaster story [16:20]

## 9. 🙃 What Fell Flat
* Dan's airport-lounge bit gets a groan from both of them, and Jess moves swiftly on [24:50]""",
)

# Spec #98 category sweep (2026-10-20). Each was judged blind against
# general v4 on its own shows before it was added. This rule helps inside
# a profile whose sections carry claims and caveats; added to general on
# its own it made attribution worse (lost 5–14), so it stays here.
_ATTRIBUTION_RULE = (
    '* Keep who said it and how sure they were: "Rhiannon thinks", "one small trial found", "he\'s guessing".'
    " Don't turn a hunch into a fact, drop a caveat, or round a number the episode gives exactly."
)


HEALTH = SummaryProfile(
    key="health",
    version=1,
    rules="\n".join(
        [
            "**This is a health show:**",
            _MADE_UP_EXAMPLE,
            _ATTRIBUTION_RULE,
            "* Say what the episode suggests, never \"you should\". It isn't medical advice and shouldn't read like it.",
            "* Doses, amounts, timings and protocols only exactly as the episode gives them, with the conditions and"
            " who it isn't for. Never add your own.",
            "* Mental health, abuse, grief and illness: be kind and plain. No jokes at the expense of anyone"
            " struggling. If the episode points people to support, include it.",
        ]
    ),
    slots=MappingProxyType(
        {
            3: SlotSpec(
                "🩺",
                "What They Say",
                "* The 3-5 main claims, each with who makes it and what it rests on, the way the episode puts it (a"
                " trial, a study in mice, clinical experience, their own story) [timestamp].",
            ),
            4: SlotSpec(
                "⚖️",
                "Debated or Unsure",
                "* Where the speakers disagree, push back, or say the evidence is thin or mixed [timestamp].\n"
                '* If nothing was debated, say "Nothing debated" and move on.',
            ),
            6: SlotSpec(
                "🛠️",
                "Try This",
                "* The concrete things the episode suggests doing, one bullet each: what, how much or how often, and"
                " for whom, exactly as said [timestamp].\n"
                '* If the episode gives no practical steps, say "No practical steps in this one".',
            ),
            7: SlotSpec(
                "🎧",
                "Worth a Listen?",
                "* In two or three lines: who'll get the most out of it, how practical or how deep it goes, and the"
                " best part to skip to.",
            ),
            9: SlotSpec(
                "🔍",
                "Evidence Check",
                "* Only from what the episode itself says: claims that rest on one story or a single small study,"
                " things the speakers admit aren't settled, and products or sponsors tied to a claim [timestamp].\n"
                "* Don't judge claims with your own knowledge.\n"
                '* If there is nothing to flag, say "Nothing to flag".',
            ),
        }
    ),
    example="""## 1. 🎙️ The Gist
Hollie Marsh interviews sleep researcher Dr. Tomasz Weir

Tomasz walks through why shift workers sleep worse and what his clinic tries first. Hollie keeps asking what the evidence actually is, and he's straight about where it's thin.

## 2. ⏱️ Timeline
* [00:00 - 06:30] **Intro:** Hollie on her own night shifts years ago.
* [06:30 - 24:10] **Why Shifts Hurt Sleep:** Light, timing and the body clock.
* [24:10 - End] **What to Try:** Tomasz's clinic routine and its limits.

## 3. 🩺 What They Say
* Tomasz says light at the wrong time is the main problem for night workers, based on his clinic's patients [09:40]
* He cites one trial of 60 nurses where blackout blinds helped daytime sleep [15:20]

## 4. ⚖️ Debated or Unsure
* **Naps:** Hollie thinks a pre-shift nap helps; Tomasz says the evidence is mixed and it depends on the person [19:05]

## 5. 💬 Best Quotes
* "Our night-shift patients keep a sleep diary for two weeks before we change anything." - Dr. Tomasz Weir [25:30]

## 6. 🛠️ Try This
* Blackout blinds for daytime sleep after a night shift, as in the nurses' trial [15:20]
* Keep the same sleep window on days off if you can, which his clinic asks of everyone first [27:45]

## 7. 🎧 Worth a Listen?
* Practical and honest about the evidence. Shift workers should start at 24:10.

## 8. 📚 Resource List
* **Tomasz Weir** (person): Sleep researcher who runs a shift-work clinic [01:10]

## 9. 🔍 Evidence Check
* The blinds advice rests on one trial of 60 nurses, and Tomasz says it hasn't been repeated [16:00]""",
)


INVESTING = SummaryProfile(
    key="investing",
    version=1,
    rules="\n".join(
        [
            "**This is an investing show:**",
            _MADE_UP_EXAMPLE,
            _ATTRIBUTION_RULE,
            '* Every view is the speaker\'s, never yours: "Scott thinks Meta has bottomed", not "Meta has bottomed".'
            " Don't turn an opinion into a buy or sell call the episode didn't make.",
            "* Prices, returns, rates and valuations exactly as said and as of the publication date. Name the"
            " companies, funds and tickers the way the episode does.",
            "* Call-in shows: each caller's situation and the advice they got, one bullet each.",
        ]
    ),
    slots=MappingProxyType(
        {
            3: SlotSpec(
                "📈",
                "The Calls",
                "* The main views, predictions and positions, told like you'd tell a friend: who holds them, why, and the"
                " numbers behind them exactly as said, with the time frame [timestamp].\n"
                "* On a call-in show, one bullet per caller: their situation and the advice they got [timestamp].",
            ),
            4: SlotSpec(
                "⚖️",
                "Bull vs Bear",
                "* Where the speakers disagree, push back, or make the other side's case [timestamp].\n"
                '* If nobody pushed back, say "No pushback" and move on.',
            ),
            6: SlotSpec(
                "🎧",
                "Worth a Listen?",
                "* In two or three lines: who'll get the most out of it (a markets junkie, someone sorting out their"
                " own money), how deep it goes, and the best part to skip to.",
            ),
            7: SlotSpec(
                "🗣️",
                "Talking Points",
                "* Three or four things from this episode worth bringing up with friends, a line each, in your own"
                " words [timestamp].",
            ),
            9: SlotSpec(
                "🧯",
                "Risks They Flag",
                '* The risks, caveats and "what would change my mind" the speakers themselves give [timestamp].\n'
                "* Don't add your own market views.\n"
                '* If nobody names a risk, say "No risks flagged".',
            ),
        }
    ),
    example="""## 1. 🎙️ The Gist
Dev Arora and Nell Price chat

Dev thinks UK small caps are cheap enough to buy now; Nell isn't convinced while rates stay where they are. They also take a listener question on paying off a mortgage early.

## 2. ⏱️ Timeline
* [00:00 - 04:20] **Markets This Week:** A quick run through the week's moves.
* [04:20 - 22:40] **Small Caps:** Dev's case and Nell's doubts.
* [22:40 - End] **Listener Question:** Overpaying a mortgage versus investing.

## 3. 📈 The Calls
* Dev thinks UK small caps are worth buying for the next two to three years: they've lagged the big companies for ages and sit on about 11 times earnings [06:15]
* Nell would wait for rate cuts before adding any [14:30]
* Listener Ruth, 41, asked about overpaying her 4.5% mortgage; Nell said keep the emergency fund first, then overpay [24:10]

## 4. ⚖️ Bull vs Bear
* **Small caps:** Dev points to the discount; Nell says the discount has been there for years and might not close [15:45]

## 5. 💬 Best Quotes
* "I'd want to see two rate cuts before I add any UK small caps." - Nell Price [14:40]

## 6. 🎧 Worth a Listen?
* A friendly argument more than a lecture. Small-cap curious? Start at 04:20; mortgage worriers, skip to 22:40.

## 7. 🗣️ Talking Points
* The small-cap discount has been there for years, and Nell isn't sure it ever closes [15:45]

## 8. 📚 Resource List
* **FTSE SmallCap Index** (other): The index Dev's valuation figure refers to [07:00]

## 9. 🧯 Risks They Flag
* Dev admits small caps would get hit hardest if a recession comes [10:50]""",
)


FAITH = SummaryProfile(
    key="faith",
    version=1,
    rules="\n".join(
        [
            "**This is a faith show:**",
            _MADE_UP_EXAMPLE,
            "* Give the teaching the way the speaker gives it, in their terms. Don't fact-check, argue with or rank"
            " anyone's beliefs.",
            "* Name passages, readings and texts exactly as the episode cites them. Never add a reference it doesn't"
            " give.",
            '* Warm and respectful. No snark and no "drama" framing. In a conversation or debate, credit each view'
            " to the person who holds it.",
        ]
    ),
    slots=MappingProxyType(
        {
            3: SlotSpec(
                "📖",
                "The Message",
                "* The 3-5 main points of the teaching, reading or conversation, in the speaker's own terms, with the"
                " passages they use [timestamp].",
            ),
            4: SlotSpec(
                "💭",
                "Questions Raised",
                "* The hard questions, doubts or disagreements the episode itself takes on, and how it answers them"
                " [timestamp].\n"
                '* If it raises none, say "No hard questions in this one".',
            ),
            6: SlotSpec(
                "🙏",
                "To Take Away",
                "* The reflection, prayer, practice or challenge the episode leaves listeners with, as given"
                " [timestamp].\n"
                '* If it gives none, say "Nothing specific to take away".',
            ),
            7: SlotSpec(
                "🎧",
                "Worth a Listen?",
                "* In two or three lines: what kind of listen it is (a sermon, a daily reading, a conversation), who'll"
                " get the most from it, and the best part to start at.",
            ),
            9: SlotSpec(
                "📜",
                "Passages and Readings",
                "* Each scripture passage, reading or text the episode cites, with what it's used for [timestamp].\n"
                '* If none are cited, say "None cited".',
            ),
        }
    ),
    example="""## 1. 🎙️ The Gist
Narrated by Fr. Colm Hardy

Fr. Colm reads the day's two passages and stays with one line about the lost coin. His point is that being searched for matters more than doing the searching.

## 2. ⏱️ Timeline
* [00:00 - 01:30] **Opening Prayer:** A short prayer to start.
* [01:30 - 11:20] **The Readings:** Both passages, read in full.
* [11:20 - End] **Reflection:** Fr. Colm on the lost coin.

## 3. 📖 The Message
* Fr. Colm says the parable is about the one doing the searching, not the coin [12:40]
* He links it to the first reading's promise to gather the scattered [15:10]

## 4. 💭 Questions Raised
* **Why bother with one coin?** Fr. Colm admits the woman's effort looks out of proportion, and says that's the point [14:05]

## 5. 💬 Best Quotes
* "I always slow down at verse nine, where she calls the neighbours in." - Fr. Colm Hardy [13:20]

## 6. 🙏 To Take Away
* Notice one person today who might feel overlooked, and look for them on purpose [17:30]

## 7. 🎧 Worth a Listen?
* A gentle twenty minutes for a daily habit. If you've heard the readings, start at 11:20.

## 8. 📚 Resource List
* **Fr. Colm Hardy** (person): The priest who leads the daily readings [00:10]

## 9. 📜 Passages and Readings
* Luke 15:8-10, the parable of the lost coin, the centre of the reflection [06:40]
* Ezekiel 34:11-16, read first and tied to the parable [01:45]""",
)


FICTION = SummaryProfile(
    key="fiction",
    version=1,
    rules="\n".join(
        [
            "**This is a story (fiction or audio drama):**",
            _MADE_UP_EXAMPLE,
            "* Retell it as a story. Don't treat its events as real, and don't fact-check it.",
            "* Spoilers: the setup is fine everywhere, but how it ends goes only in How It Ends.",
            "* Use the characters' names as the episode does. The narrator or cast reading it aren't characters.",
            "* Stories for children: say the age it suits and anything a parent would want to know, only from what"
            " the episode shows.",
        ]
    ),
    slots=MappingProxyType(
        {
            3: SlotSpec(
                "📖",
                "The Story",
                "* What happens, in order, up to the turn, without the ending: the setup, the trouble, what the"
                " characters try [timestamp].",
            ),
            4: SlotSpec(
                "🎭",
                "Who's Who",
                "* The main characters, a line each: who they are and what they want, as the episode shows them.",
            ),
            6: SlotSpec(
                "🔚",
                "How It Ends (spoilers)",
                "* How this episode ends or the cliffhanger it leaves, in one or two lines [timestamp].",
            ),
            7: SlotSpec(
                "🎧",
                "Worth a Listen?",
                "* In two or three lines: the mood (creepy, cosy, funny), who it suits (for children, the age), and"
                " whether you need earlier episodes first.",
            ),
            9: SlotSpec(
                "🧵",
                "Threads and Series",
                "* Where it sits in the series, mysteries left open, and callbacks to earlier episodes, only as the"
                " episode gives them.\n"
                '* If it stands alone, say "A standalone story".',
            ),
        }
    ),
    example="""## 1. 🎙️ The Gist
Narrated by Ivy Rennick

A lighthouse keeper on a small island starts getting letters addressed to someone who left years ago. It's a slow, creepy one, and it doesn't explain itself.

## 2. ⏱️ Timeline
* [00:00 - 03:10] **Arrival:** Arlo takes over the lighthouse.
* [03:10 - 17:40] **The Letters:** A letter every week, all for Maren.
* [17:40 - End] **The Reply:** Arlo finally writes back.

## 3. 📖 The Story
* Arlo arrives for a winter posting and finds the lamp room oddly tidy [01:50]
* Letters keep coming for Maren, the keeper before him, each one more urgent [06:20]
* Arlo asks the boatman about her, and he won't say [12:45]

## 4. 🎭 Who's Who
* **Arlo:** The new keeper, practical and a bit lonely.
* **Maren:** The keeper before him, only ever seen through the letters.
* **The boatman:** Brings the post and changes the subject.

## 5. 💬 Best Quotes
* "I'll leave the kettle on the stove, same as always." - from one of the letters [09:30]

## 6. 🔚 How It Ends (spoilers)
* Arlo writes back as Maren, and the next letter is addressed to him [21:05]

## 7. 🎧 Worth a Listen?
* Quiet and creepy rather than scary, good for a dark evening. Fine to start here.

## 8. 📚 Resource List
* **Ivy Rennick** (person): Narrator of the series [00:05]

## 9. 🧵 Threads and Series
* The boatman's silence and the tidy lamp room are left open for later episodes [12:45]""",
)


LANGUAGE_LEARNING = SummaryProfile(
    key="language_learning",
    version=1,
    rules="\n".join(
        [
            "**This is a language lesson:**",
            _MADE_UP_EXAMPLE,
            "* Words, phrases, grammar and translations exactly as the episode gives them, in their original"
            " spelling. Never add your own examples or translations. If the episode names a word or idiom but"
            " doesn't explain it, list it without a meaning.",
            '* Give the level only if the episode says it. Previewed or announced material isn\'t "taught".',
            "* Write like a friendly teacher talking to a learner, not a syllabus.",
        ]
    ),
    slots=MappingProxyType(
        {
            3: SlotSpec(
                "🎯",
                "What You'll Learn",
                "* The 3-5 things the lesson teaches: the topic, the grammar point, the situation it prepares you for"
                " [timestamp].",
            ),
            4: SlotSpec(
                "🗝️",
                "Key Words and Phrases",
                "* The words and phrases taught, each with the meaning the episode gives [timestamp].",
            ),
            6: SlotSpec(
                "🧩",
                "Tricky Bits",
                "* Mistakes, exceptions or confusions the episode warns about [timestamp].\n"
                '* If it flags none, say "Nothing tricky flagged".',
            ),
            7: SlotSpec(
                "🎧",
                "Worth a Listen?",
                "* In two or three lines, as you'd tell a friend who's learning: how fast and how hard it feels, who it"
                " suits, and whether you need earlier lessons first. Don't name a level (beginner, intermediate, B1)"
                " unless the episode does.",
            ),
            9: SlotSpec(
                "✍️",
                "Practise",
                "* The exercises, prompts or homework the episode gives, as given [timestamp].\n"
                '* If there are none, say "No exercises in this one".',
            ),
        }
    ),
    example="""## 1. 🎙️ The Gist
Rosa Delgado and Finn Carey chat

A beginner Spanish lesson on ordering at a café. Finn makes the mistakes so you don't have to, and Rosa explains why "quisiera" sounds politer than "quiero".

## 2. ⏱️ Timeline
* [00:00 - 02:30] **Warm-Up:** A quick review of numbers.
* [02:30 - 14:10] **At the Café:** Ordering, step by step.
* [14:10 - End] **Your Turn:** Rosa sets the practice.

## 3. 🎯 What You'll Learn
* How to order a drink and ask for the bill in Spanish [03:15]
* Why "quisiera" is politer than "quiero" [08:40]

## 4. 🗝️ Key Words and Phrases
* **quisiera:** "I would like", the polite way to order [08:40]
* **la cuenta, por favor:** "the bill, please" [11:20]

## 5. 💬 Best Quotes
* "Try it with café con leche first, then with té con limón." - Rosa Delgado [12:05]

## 6. 🧩 Tricky Bits
* Finn keeps saying "un café con leche" with "una"; Rosa explains café is masculine [05:30]

## 7. 🎧 Worth a Listen?
* Slow and friendly, and Finn's mistakes make it easy to follow. Fine as a first lesson.

## 8. 📚 Resource List
* **Rosa Delgado** (person): The teacher who leads the lesson [00:10]

## 9. ✍️ Practise
* Order your next coffee in Spanish in your head before you order it for real [14:30]""",
)


PROFILES: Mapping[str, SummaryProfile] = MappingProxyType(
    {
        profile.key: profile
        for profile in (
            GENERAL,
            NEWS,
            SPORTS,
            HISTORY,
            TRUE_CRIME,
            COMEDY,
            HEALTH,
            INVESTING,
            FAITH,
            FICTION,
            LANGUAGE_LEARNING,
        )
    }
)


def empty_slot_lines(number: int) -> frozenset[str]:
    """Every profile's "nothing here" line for slot ``number``, lower-cased."""
    return frozenset(
        spec.empty.lower() for profile in PROFILES.values() if (spec := profile.slots.get(number)) and spec.empty
    )


def compose_system_prompt(profile: SummaryProfile) -> str:
    """Build the summary system prompt for ``profile``.

    Order: style guide → shared rules → profile rules → slots 1–9 → example.
    """
    addenda = {5: profile.quote_guidance, 8: profile.resource_hint}
    sections = [
        (FIXED_SLOT_SPECS.get(number) or profile.slots[number]).render(number, addenda.get(number, ""))
        for number in range(1, 10)
    ]
    parts = [STYLE_GUIDE, SHARED_RULES, profile.rules, *sections, EXAMPLE_SEPARATOR, EXAMPLE_HEADER, profile.example]
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
            "fiction": ["Fiction", "Drama", "Science Fiction", "Comedy Fiction", "Stories for Kids"],
            "language_learning": ["Language Learning"],
            "history": ["History", "Documentary"],
            "health": [
                "Health & Fitness",
                "Self-Improvement",
                "Mental Health",
                "Medicine",
                "Fitness",
                "Nutrition",
                "Alternative Health",
            ],
            "faith": [
                "Religion & Spirituality",
                "Religion",
                "Spirituality",
                "Christianity",
                "Buddhism",
                "Hinduism",
                "Islam",
                "Judaism",
            ],
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
