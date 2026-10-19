# Genre-Aware Summaries: One Contract, Many Profiles

> **Status:** 🚧 In progress — Phase 1 (refactor) and Phase 2 (shared rules, `general` v2) implemented 2026-10-19; Phases 3–4 open
> **Created:** 2026-10-15
> **Priority:** Medium — the summary is the product's main artifact, and for about a third of the corpus it asks the wrong questions
> **Author:** Product & Engineering
> **Related:** [#53 eval-runs-and-summary-rubric](53-eval-runs-and-summary-rubric.md) (rubrics), [#54 summary-segment-citations](54-summary-segment-citations.md) (timestamps), [#58 original-language summaries](58-original-language-summaries.md) (localised headings, number anchors), [#77 conversational-briefing-narration](77-conversational-briefing-narration.md) (reads sections 1, 3, 4), [#92 resource-list-entities](92-resource-list-entities.md) (reads section 8), [#96 prompt-injection-hardening](96-prompt-injection-hardening.md) (system prompt is ours)

---

## Executive Summary

`TranscriptSummarizer.SYSTEM_PROMPT`
([post_processor.py](../thestill/core/post_processor.py)) is one prompt
for every podcast. It was written for, and works well on, expert
interviews about technology and science: "[Host] interviews [Guest]", a
Drama section that hunts for evasions under questioning, a BS Test for
hyped claims, Blog Ideas and Social Snippets for someone who writes
about what they hear. Its only worked example is a medical-AI interview.

Other genres need different things from a summary:

- **True crime** needs a case file and a strict line between what is
  established and what is alleged or speculated. A "Drama" section with
  "temperature" ratings and emojis around real victims is the wrong
  register.
- **Sports** needs results, injuries and transfers anchored to the
  episode date, predictions on record, and a spoiler warning.
- **News and politics** is a list of stories, not one arc. Each claim
  needs a source, and "what to watch next" matters more than blog ideas.
- **Investing** needs the numbers, who holds which position and why,
  and whether the guest is talking their own book.
- **Comedy** gets almost nothing from Key Takeaways or a BS Test. It
  needs the best bits with timestamps, running jokes and a vibe check.
- **History and narrative documentary** needs the story arc, a
  chronology, and where the episode corrects a popular myth.
- **Health and self-help** needs actionable advice tagged by evidence
  level and a stricter BS Test.

This spec keeps **one summary contract**: nine numbered sections, where
sections 1 (Gist), 2 (Timeline), 5 (Best Quotes) and 8 (Resource List)
are fixed for every podcast because code parses them. Each genre becomes
a **profile** that fills the other five numbered slots (3, 4, 6, 7, 9)
with its own title and instructions, inside a fixed role for each slot,
and adds its own guardrails and worked example. The profile is chosen
per podcast: an operator override first, then the feed's Apple
category, then `general`. `general` is today's prompt, byte for byte.
Every summary records which profile and version produced it.

**Key principle:** genres change what the summary *looks for*, never
the *shape* that downstream code reads.

## Problem

### What the corpus looks like

Local corpus, 2026-10-15, summarised episodes by the podcast's primary
category:

| Category | Podcasts | Summaries |
| --- | ---: | ---: |
| Technology | 34 | 640 |
| Investing | 3 | 244 |
| Business | 10 | 199 |
| Daily News | 4 | 152 |
| Entrepreneurship | 4 | 105 |
| Politics | 3 | 90 |
| Sports | 2 | 88 |
| Management | 2 | 70 |
| Tech News | 3 | 65 |
| Comedy + Comedy Interviews | 7 | 73 |
| Science + Physics | 3 | 97 |
| History + Documentary | 3 | 31 |
| Health & Fitness + Self-Improvement | 3 | 38 |
| True Crime | 1 | 17 |

About 60% is technology, science, business and entrepreneurship, which
the current prompt suits. The rest, roughly 750 summaries, is news,
politics, investing, sports, comedy, history, health and true crime.
Public pages ([#94](94-public-pages.md)) will put these summaries in
front of people who never chose the corpus, so the long tail will grow.

### Where the current prompt fits badly

Section by section:

- **Gist, "[Host] interviews [Guest(s)]".** This is wrong for panel
  shows, daily news bulletins, narrated documentaries and solo
  monologues. The model either invents an interview or ignores the line.
- **3 Key Takeaways.** This assumes one argument. News episodes have
  five unrelated stories, comedy has none, true crime needs facts about
  a case, not lessons.
- **4 The Drama.** The interpersonal lens (deflection, power dynamics,
  tone shifts) is built for an interviewer pressing a guest. On true
  crime it turns real events into entertainment. On news it reports the
  drama *in* the story as if it happened between the hosts.
- **6 Blog Ideas / 7 Social Snippets.** These serve a writer
  republishing the episode. For a subscriber deciding whether to listen
  they are dead space in most genres.
- **9 BS Test.** This fits claim-heavy shows. For news the risk is spin
  and missing counter-arguments, for true crime speculation presented as
  fact, for comedy it does not apply.
- **The worked example.** Models copy the example's shape and tone
  closely. A medical-AI interview example pulls every genre toward
  "expert explains a technology".
- **No date anchoring.** Nothing stops the model from "correcting" a
  sports result or a news development from its own training data. For
  time-sensitive genres that is a faithfulness bug, not a style issue.

### What reads the summary today

The summary is parsed in several places, and all of them key on
**section numbers**, not words (spec [#58](58-original-language-summaries.md)
localised the headings):

| Consumer | Reads | How |
| --- | --- | --- |
| `extract_gist` / `summary_preview` ([podcast_service.py](../thestill/services/podcast_service.py)) | §1 | section number |
| `extract_summary_sections` ([briefing_script_generator.py](../thestill/services/briefing_script_generator.py)) → narration claim selector | §1 gist, §3 as claims, §4 as colour | section number |
| `summary_citations` ([summary_citations.py](../thestill/core/summary_citations.py)) | §2 timeline ranges, every `[MM:SS]` | timestamp syntax |
| `summary_resources` ([summary_resources.py](../thestill/core/summary_resources.py)) → entity seeds | §8 bullets | `## 8.` + bullet shape |
| `summary_checks` ([summary_checks.py](../thestill/evals/summary_checks.py)) | all nine | number **and** English title |
| Web reader, export, MCP `get_summary` | whole markdown | generic rendering |

The frontend renders the summary as generic markdown. No component
depends on a section's title.

## Goals

1. Each supported genre gets a summary that answers what a subscriber
   of that genre wants to know, with genre-specific guardrails where
   accuracy or tone matters (true crime, news, health, investing).
2. Every consumer in the table above keeps working on every profile
   with no profile-specific code, except the eval's title check.
3. `general` stays exactly as it is today, so the 60% of the corpus
   that works well is not touched.
4. Every summary records the profile and profile version that produced
   it, so mixed corpora are explainable and a profile can be re-run
   selectively.
5. Each profile ships only after an old-vs-new comparison on real
   episodes of its genre shows it is better and no less faithful.

## Non-goals

- **Per-episode genre detection.** The profile is per podcast. A news
  show that runs an occasional long interview gets the news profile.
  See open questions.
- **Fiction and audio drama, TV & Film recaps, kids, religion.** These
  fall back to `general` until there is enough corpus to evaluate.
- **User-chosen profiles.** Subscribers do not pick a summary style.
  The summary is shared across users and generated once.
- **Changing the clean-transcript or facts prompts.** Only the summary
  prompt changes.
- **Regenerating the existing corpus automatically.** Re-summarising is
  an explicit, capped operator action.
- **New section numbers.** Ten sections, optional sections or
  reordering are out. The contract stays at nine.

## Design

### The slot contract

Every profile emits the same nine `## N. <emoji> <Title>` headings in
the same order. Each slot has a **role** that consumers can rely on:

| Slot | Role | Fixed or profile | Consumer contract |
| --- | --- | --- | --- |
| 1 | **Gist**: who is speaking, in what format, plus two sentences | Fixed | First section, prose, no title/date/duration |
| 2 | **Timeline**: 3–6 ranged segments | Fixed | `* [start - end] **Title:** sentence` |
| 3 | **Core**: the bullets that matter most for this genre | Profile | Bullets, each with a citation; narration reads them as claims |
| 4 | **Friction**: what is contested, disputed or clashing | Profile | Bullets or rounds; narration reads them as colour; may say "nothing" |
| 5 | **Best Quotes** | Fixed (profile may add guidance) | `"Quote" - Speaker [timestamp]` |
| 6 | **Extra A** for the subscriber | Profile | Free bullets with citations |
| 7 | **Extra B** for the subscriber | Profile | Free bullets with citations |
| 8 | **Resource List** | Fixed (profile may reorder kind preferences) | Spec #92 bullet shape, fixed English kinds |
| 9 | **Scrutiny**: what deserves scepticism, in this genre's terms | Profile | Bullets; may say "nothing" |

Slot roles are what make narration genre-agnostic. Slot 3 is always
"what a listener should come away with" and slot 4 is always "where
there is disagreement or tension". So the claim selector does not need
to know whether slot 4 is called The Drama or Facts vs Theories.

The citation rule (every claim has a timestamp), the bullet syntax
rules and the "NO PREAMBLE" rule stay in the shared style guide.

### Shared rule changes (all profiles, Phase 2)

Two changes apply to every profile, `general` included, so they ship
as their own evaluated step after the refactor:

- **Format-aware Gist line.** Replace "[Host] interviews [Guest(s) with
  roles/titles]" with: describe who is speaking and how, e.g. "[Host]
  interviews [Guest, role]", "[Host] and [co-hosts] discuss…",
  "Narrated by [Host]", "[Host]'s daily round-up". This handles format
  (interview, panel, narrative, solo, bulletin) without a format
  classifier.
- **Episode-date anchoring.** Add: report only what the episode says.
  Do not update, correct or extend facts, results or developments from
  your own knowledge. Time-sensitive facts are as of the episode's
  publication date. The publication date is already in
  `EpisodeMetadata` but is no longer put in the prompt. It goes back in
  as a single fenced metadata line.

### Profiles

Each profile is a frozen dataclass in a new module
`thestill/core/summary_profiles.py`:

```python
@dataclass(frozen=True)
class SlotSpec:
    emoji: str
    title: str            # English; localised by the #58 rule like every other heading
    instructions: str

@dataclass(frozen=True)
class SummaryProfile:
    key: str              # "general", "true_crime", ...
    version: int          # bump on any prompt text change
    slots: Mapping[int, SlotSpec]   # exactly {3, 4, 6, 7, 9}
    rules: str            # genre guardrails and tone, appended to the style guide
    quote_guidance: str   # optional addition to slot 5
    resource_hint: str    # optional: which kinds matter most, never new kinds
    example: str          # worked example in this profile's shape
```

`TranscriptSummarizer` builds the system prompt as style guide → shared
rules → profile rules → slots 1–9 (fixed text for 1, 2, 5, 8; profile
text for the rest) → profile example. A unit test asserts that composing
`general` at version 1 reproduces today's `SYSTEM_PROMPT` exactly. The
spec #58 localisation replacement ("Use British English.") keeps working
because the style guide sentence is unchanged.

The first set of profiles is below. Titles and wording are the starting
point for the Phase 3 evals, not final copy.

#### `general` (today's prompt)

| Slot | Title |
| --- | --- |
| 3 | 🧠 Key Takeaways |
| 4 | 🌶️ The Drama |
| 6 | ✍️ Blog Ideas |
| 7 | 📱 Social Snippets |
| 9 | 💩 The "BS" Test |

No change to rules or example.

#### `true_crime`

| Slot | Title | Content |
| --- | --- | --- |
| 3 | 🗂️ Case File | Victim(s), suspect(s) or accused, location, key dates, and a required **Status** bullet: unsolved / charged / convicted / acquitted / on appeal / new evidence |
| 4 | ⚖️ Facts vs Theories | Each contested point tagged **Established** (court record, official statement), **Alleged**, or **Speculation** (host or guest theory), with who says so |
| 6 | 🧩 Where This Episode Fits | Series position, what is new compared with earlier episodes, open threads, the cliffhanger |
| 7 | 🕯️ Content Notes | What the episode describes that a listener may want warning of (violence, abuse, children, suicide); "None" when nothing |
| 9 | 🔍 Speculation Check | Leaps the host makes, unsupported accusations, one-sided sourcing |

Rules:

- Use "alleged" or "accused" for anyone not convicted. Never state or
  imply guilt beyond the record the episode describes.
- Name private individuals only as the episode names them, and never
  introduce a suspect the episode does not.
- Sober tone: no emojis outside headings, no jokes, no "temperature"
  ratings. Refer to victims by name with respect.
- Tips, rumours and listener theories are Speculation, never fact.

Resource hint: cases (`event`), people, places, documentaries, books,
court documents (`article` / `other`).

#### `sports`

| Slot | Title | Content |
| --- | --- | --- |
| 3 | 🏟️ Results & News | First bullet **Spoilers:** the matches or events whose results the episode reveals. Then scores, standings, injuries, transfers, trades, each "as of" the episode date |
| 4 | 🔥 Hot Takes & Clashes | The existing Round format, aimed at pundit arguments and feuds |
| 6 | 🔮 Predictions on Record | Who predicted what, and by when it can be checked |
| 7 | 🍻 Talking Points | Three or four things worth bringing to the group chat |
| 9 | 🧢 Hype Check | Homerism, recency bias, takes that ignore the numbers quoted |

Rules: never add or correct a score, result or transfer from your own
knowledge. If the episode is unsure, say so.

#### `news`

| Slot | Title | Content |
| --- | --- | --- |
| 3 | 📰 The Stories | One bullet per story, with nested **What happened**, **Why it matters**, **What's next** |
| 4 | ⚔️ Contested | Where sides or sources disagree, and what each says. "Nothing contested" when the episode is one-sided |
| 6 | 📅 Watch List | Upcoming votes, deadlines, court dates, releases the episode mentions, with dates |
| 7 | 🔗 Who Said It | The sources the episode leans on (agencies, officials, outlets, documents) per story |
| 9 | 🌀 Spin Check | Loaded framing, unchallenged talking points, missing counter-arguments; name the show's slant where it is obvious |

Rules: attribute every claim ("according to…", "the minister said").
Keep reporting and the host's opinion apart. Developments are as of the
publication date. Never fill in what happened later.

#### `investing`

| Slot | Title | Content |
| --- | --- | --- |
| 3 | 🧠 Key Takeaways | As `general` |
| 4 | 🐂 Bull vs Bear | The opposing cases, who makes each |
| 6 | 📊 The Numbers | Metrics, valuations, forecasts, rates, verbatim with speaker and timestamp |
| 7 | 🎯 Calls & Theses | Who is long or short what, why, over what horizon, and the risks they named |
| 9 | 💼 Conflicts & BS Test | Talking their own book, undisclosed positions, weak or circular reasoning |

Rules: report numbers exactly as spoken, never computed or rounded.
Never frame anything as advice to the reader.

#### `comedy`

| Slot | Title | Content |
| --- | --- | --- |
| 3 | 😂 Best Bits | The funniest moments, each a one-line setup plus timestamp, so the summary is a way into the audio |
| 4 | 🌶️ Banter & Roasts | Who got roasted, by whom, and whether it landed. "Friendly all round" is fine |
| 6 | 🔁 Running Jokes & Segments | Callbacks, recurring segments (listener mail, games), what they are for a new listener |
| 7 | 🎧 Worth a Full Listen? | Energy, guest chemistry, which part to skip to, who will enjoy it |
| 9 | 🙃 What Fell Flat | Bits that dragged or did not land; "nothing" is a valid answer |

Rules: playful tone allowed. Quotes need enough setup to work out of
context.

#### `history`

| Slot | Title | Content |
| --- | --- | --- |
| 3 | 📜 Story Arc | Setting, central figures, turning points, outcome |
| 4 | 🏛️ Myth vs Record | Popular beliefs the episode corrects; where historians disagree |
| 6 | 🗓️ Key Dates & Places | A short chronology |
| 7 | 🧩 Where This Episode Fits | Series position, what came before, what is next |
| 9 | 🔍 Source Check | Unsourced claims, dramatised dialogue presented as fact, presentism |

Resource hint: people, places, events, books.

#### `health`

| Slot | Title | Content |
| --- | --- | --- |
| 3 | ✅ What to Try | Actionable advice, each tagged by the evidence the speaker gave: **Study**, **Clinical experience**, **Anecdote** or **Opinion** |
| 4 | 🥊 Contested Claims | Where the episode, or the experts it cites, disagree |
| 6 | 🔬 Evidence Cited | Studies and trials named, with what was said about them (size, population) |
| 7 | 🚩 Before You Try It | Caveats and who the advice is not for, as stated in the episode |
| 9 | 💊 The "BS" Test | Weak evidence, supplements and protocols sold by the speaker, extrapolation from mice or tiny samples |

Rules: never add medical advice the episode does not give. No
boilerplate "consult your doctor" unless the episode or a specific risk
calls for it.

### Choosing a profile

Resolution, first match wins:

1. `podcasts.summary_profile` (new nullable column) when set to a
   profile key.
2. The category map in `summary_profiles.py`, tried on primary
   subcategory, primary category, secondary subcategory, secondary
   category. Subcategory before category, so `News / Business News`
   is decided by its subcategory.
3. `general`.

A profile that is not enabled (see rollout) resolves to `general`.

Initial category map (Apple Podcasts taxonomy):

| Apple category or subcategory | Profile |
| --- | --- |
| True Crime | `true_crime` |
| Sports and every subcategory | `sports` |
| News, Daily News, Politics, News Commentary, Government, Business News | `news` |
| Investing | `investing` |
| Comedy, Comedy Interviews, Improv, Stand-Up | `comedy` |
| History, Documentary | `history` |
| Health & Fitness and every subcategory, Self-Improvement | `health` |
| Everything else, including Technology, Tech News, Science, Business, Entrepreneurship | `general` |

Apple categories are self-declared and often wrong, so the override
exists. `thestill set-summary-profile --podcast-id X --profile
<key|auto>` sets or clears it, and `thestill summary-profiles` prints
each podcast's effective profile and why ("override", "category: True
Crime", "default"). Phase 4 adds the same field to the admin podcast
settings page.

A publisher can steer which profile its show gets by changing its
category. That only selects among our own prompts, all of which are
trusted text, so it is not an injection vector under
[#96](96-prompt-injection-hardening.md).

### Provenance

`SummaryManifest` ([summary_artifacts.py](../thestill/core/summary_artifacts.py))
gains two optional fields, `profile: str = "general"` and
`profile_version: int = 1`, written by both summarise paths (task
handler and CLI) through the existing `write_summary_manifest`. The
fields have defaults, so existing manifests stay valid and
`SUMMARY_MANIFEST_SCHEMA_VERSION` does not change. A summary with no
manifest or a stale one is treated as `general` v1.

### Consumers

- **Narration** (#77): no change. It reads slot 3 as claims and slot 4
  as colour, which the slot roles guarantee. Phase 4 checks briefing
  narrations that mix profiles before closing.
- **Resource List / entity seeds** (#92): no change. Slot 8 text,
  bullet shape and English kind words are fixed for all profiles.
- **Citations** (#54): no change. Every profile keeps the timestamp
  syntax and the slot 2 format.
- **Gist preview**: no change. Slot 1 is still first.
- **Translation** (#58): no change. Headings are translated as before.
- **`summary_checks`**: `REQUIRED_SECTIONS` becomes
  `required_sections(profile)`, which returns the nine titles for the
  profile in the manifest. The number check is unchanged.
- **Chunked summaries**: every chunk gets the same profile prompt, so
  the repeated-sections behaviour is unchanged.

### Rollout control

`SUMMARY_PROFILES_ENABLED` (comma-separated profile keys, default empty)
lists the profiles that can be chosen. `general` is always on. Each
profile is turned on separately once its eval passes, and removing it
from the list sends new summaries back to `general` without a deploy of
prompt code.

### Failure modes ([#42](42-robustness-and-failure-mode-hardening.md))

- **Unknown override key** (a profile was renamed or removed): log a
  warning with `podcast_id` and fall back to category resolution. Never
  fail the summarise task.
- **Model drops or renames a slot under a new profile**: the summary is
  still stored. `summary_checks` reports it against the profile's
  titles, and the Phase 3 gate requires that rate to be no worse than
  `general`'s on the same episodes.
- **Silent degradation**: the profile and version are logged on every
  summarise (`summary_profile`, `summary_profile_version`, `episode_id`),
  so "this show suddenly looks different" is answerable from logs.
- **Mixed-profile corpus**: expected. Consumers depend only on slot
  roles, and the manifest says which profile produced each summary.

## Evaluation

Following the prompt-change rule: old vs new on 10 random episodes,
three repeats on the ones that moved.

For each profile, before it is added to `SUMMARY_PROFILES_ENABLED`:

1. **Sample.** 10 random summarised episodes from podcasts that resolve
   to the profile, spread across at least two podcasts where the corpus
   allows. True crime has one podcast and 17 episodes locally, and
   health and history are thin too. Add two or three representative
   shows per thin genre to the corpus first.
2. **Generate.** `general` and the candidate profile on the same clean
   transcripts with the same model. Episodes whose verdict moves get
   three repeats.
3. **Judge.** A new `summary-profile` rubric for the #53 runner scores
   both outputs on shared criteria (faithfulness, citation validity,
   no unsupported claims) plus per-profile criteria:
   - true crime: guilt discipline, Established/Alleged/Speculation
     tagging correct
   - news: every claim attributed, no post-publication facts
   - sports: results match the transcript, spoiler line present
   - investing: numbers verbatim
   - health: evidence tags match what the speaker said
   - comedy and history: usefulness to a would-be listener
4. **Gate.** The profile ships when the judge prefers it on at least 7
   of 10 episodes, faithfulness is no lower than `general`'s, and
   `summary_checks` structural failures are no higher. The operator
   reads all 10 side by side before flipping the flag.

The judge model must differ from the summariser model where keys allow
(see the Gemini-judging-Gemini caveat from #81). Until a second provider
key is configured, the operator read in step 4 is the tiebreaker.

## Cost and capacity

No new LLM calls: one call per chunk, as today. Profile prompts are
within about 15% of today's length, mostly from the worked example.
Re-summarising is opt-in and capped (`thestill summarize --podcast-id X
--max-episodes N --force` already exists). A backfill of the roughly
750 non-general summaries is a few hundred Flash calls if the operator
chooses to run it.

## Testing

- `general` v1 composition equals the current `SYSTEM_PROMPT`
  byte for byte.
- Every profile defines exactly slots {3, 4, 6, 7, 9}, and each
  composed prompt contains all nine `## N.` headings in order.
- Category resolution table tests, including subcategory-before-category,
  secondary fallback, unknown category, disabled profile and unknown
  override key.
- The #58 language replacement still applies to every profile.
- Manifest round-trip with and without the new fields.
- `required_sections(profile)` against a fixture summary per profile.
- `extract_summary_sections` and `parse_resource_list` on one fixture
  summary per profile, so narration and entity seeds are proven
  genre-agnostic.
- The override column contract test on both SQLite and Postgres.

## Phases

1. **Refactor, no behaviour change.** Add `summary_profiles.py` with
   `general` only, compose the prompt from parts, add the byte-equality
   test, add the manifest fields, the override column (Alembic
   migration plus SQLite schema), resolution, logging and the two CLI
   commands. **Done 2026-10-19.** Notes from the implementation:
   - The byte-equality test compares against a fixture frozen from the
     old literal (`tests/unit/core/fixtures/`), not against itself.
   - Alembic `0023`. The worker loads its podcast through the
     episode-join `get_episode`, so that projection carries the column
     too, with its own contract test.
   - Callers (worker, CLI, MCP) resolve the profile and pass it to
     `summarize()` and `write_summary_manifest()`; the summarizer stays
     podcast-agnostic. The legacy-manifest backfill writes `general` v1.
   - The full category map ships now. A category that maps to a profile
     not yet defined or enabled resolves to `general`, and
     `summary-profiles` shows the candidate ("category: True Crime →
     true_crime, not defined yet").
   - Two map decisions the table above leaves open: News / Sports News
     maps to `sports`, and the `general` categories are explicit rows,
     so a show whose primary category is Technology is not moved to a
     genre profile by its secondary category.
   - `set-summary-profile` accepts only defined profiles (Phase 1:
     `general`) or `auto`.
2. **Shared rules.** Format-aware Gist line and episode-date anchoring
   for all profiles. `general` becomes v2. Evaluated on 10 random
   episodes across the whole corpus, against v1. **Done 2026-10-19**,
   evaluated on 20 episodes instead of 10 (see [Phase 2 evaluation](#phase-2-evaluation-2026-10-19)).
   Notes from the implementation:
   - `SHARED_RULES` ("Stick to the episode") sits after the style
     guide and is written in the style guide's own voice. A first,
     compliance-style wording ("Report only what the episode says. Do
     not update…") pulled the whole summary towards a reporting
     register, which the operator noticed. It also says to keep
     relative times ("next spring") in the episode's words: once the
     model sees the date it started turning them into wrong calendar
     years.
   - The publication date goes in the user message, as
     `EPISODE PUBLISHED: 15 Jun 2026` above the transcript and outside
     the untrusted sentinels. It is rendered from our datetime, and the
     feed's title stays out (#96).
   - The Gist's first line is only who is speaking and how ("Co-hosts
     …", "[host name] with a panel: …", "Narrated by …"), never the
     topic. A "discuss [topic]" example turned it into a listings blurb
     and left the two sentences below with no hook. The brackets are
     named, there is an audio-drama example, and it says never to leave
     brackets in: an earlier wording leaked "[Host]" into every
     audio-drama summary.
   - The legacy-manifest backfill no longer passes a profile, so old
     summaries stay `general` v1 instead of being stamped with today's
     version.
3. **Profiles, one at a time.** Add each profile with its example and
   rubric criteria, run the evaluation, enable it. Suggested order by
   corpus size and risk: `news`, `investing`, `sports`, `comedy`,
   `true_crime`, `history`, `health`. True crime goes after the corpus
   has more than one show.
4. **Consumers and UI.** `summary_checks` per profile, the admin
   override field on the podcast settings page, a mixed-profile
   briefing narration check, and an optional backfill of each enabled
   profile's newest N episodes per podcast.

## Phase 2 evaluation (2026-10-19)

20 episodes from 20 podcasts, one per show, spread across interview,
co-host, panel, news, sports results, narrated, solo, audio drama and
two Croatian shows (one of them true crime). v1 is the Phase 1 commit's
code and v2 the final prompt. Both use `gemini-3-flash-preview` on the
same clean transcripts, four runs each (80 summaries per version).

Deterministic, over the four runs:

| Check | v1 | v2 |
| --- | --- | --- |
| Co-host, solo, narrated, panel or drama show called an "interview" | 32/40 | 0/40 |
| Template placeholder in the Gist line | 0/80 | 0/80 |
| Preview, narration gist or resource list fails to parse | 0/80 | 0/80 |
| Years in the summary that are not in the transcript | 19 | 12 |
| …of which 2023 or later | 7 | 4 |
| "Next spring" written as a calendar year (The News Agents) | 0/4 | 0/4 |
| Section 7 dropped (English shows) | 8/72 | 4/72 |
| Gist section contractions per 1,000 words | 17.3 | 19.6 |

Blind pairwise judging by Claude (a different model family from the
summariser), A/B order randomised, reading the full transcript, with
voice ("reads like a smart friend, not a press release") as its own
criterion:

| | v1 | v2 |
| --- | --- | --- |
| Preferred overall | 4 | 14 (2 ties) |
| More faithful | 3 | 12 (5 ties) |
| Better voice | 6 | 4 (10 ties) |
| Gist line correct | 9/20 | 17/20 |

The four episodes v2 lost got three more runs each. Dwarkesh and Off
Menu came out even. The two news shows stayed with v1 (The News Agents
3–1, Newscast 4–0), mostly on attribution: a host's paraphrase
presented as a minister's quote, one faction's line credited to
another. v1 makes the same kind of error, less often on these two
shows. The `news` profile's "attribute every claim" rule is aimed at
exactly this, and `news` is first in the Phase 3 order.

How the prompt got here, each step measured on the same 20 episodes:

- The first wording called nothing an interview but leaked "[Host]"
  into the audio-drama summary on 4 of 4 runs.
- With the brackets fixed, the publication date made the model write
  "spring 2025" for the episode's "next spring" (2 of 8 runs). The
  relative-time clause fixed it.
- That version won on accuracy (preferred 15–4) but read more formal:
  36 of 72 first lines were "X and Y discuss [topic]", followed by
  "The episode covers…". Taking the topic out of the first line and
  rewriting the shared rules in the style guide's voice brought voice
  back level with v1.
- A `gemini-3.1-pro-preview` judge on the first wording agreed on
  accuracy (10 v2, 2 v1, 6 ties over 18), with rubric faithfulness
  8.72 → 9.06 and attribution 9.89 → 9.39.

Open after Phase 2, none caused by it:

- Guest roles the transcript never states ("CEO of Convergent
  Research", "Chair of Neurological Surgery at UCSF") appear in every
  version. "Use only names and roles the episode gives" in the Gist
  instruction did not stop them; they come from the model's own
  knowledge of the guest.
- Section 7 is dropped in about one summary in ten in every version.
  The worked example shows sections 1, 2, 3, 6 and 8 only.
- `summary_checks` reports every section missing on localised (#58)
  summaries because it matches English titles. Phase 4's
  `required_sections(profile)` should match on number.
- Resource List descriptions add background (a book's author, a
  company's founder) by design (#92). Judges count it as outside
  knowledge, so per-profile rubrics should exclude section 8 from that
  count.

## Alternatives considered

- **A separate prompt per genre with its own sections.** Most freedom,
  but narration, entity seeds and the eval would all need per-genre
  parsers, and every new genre would touch them. Rejected for the slot
  contract.
- **One prompt with "adapt to the genre" instructions.** Cheapest, but
  the model already sees the episode and still writes a Drama section
  for a murder case. Instructions without a structure and an example
  do not move it. It also cannot be evaluated or rolled out per genre.
- **An LLM classifier per episode.** Handles mixed shows, but adds a
  call per episode and a second thing to be wrong. The category plus
  override gets most of the benefit. Kept as an open question.
- **Optional sections (drop 4 and 9 for comedy).** Breaks the number
  anchors that #58 introduced and that narration and #92 rely on.
  "Nothing notable" inside a present section costs a line and keeps
  every consumer simple.

## Open questions

1. **Should `general` change slots 6 and 7 to serve the subscriber?**
   Blog Ideas and Social Snippets suit the operator's own use. A
   "Should you listen?" slot may serve public-page readers better.
   This is a product call, not a genre one, so it is left out of this
   spec until #94 ships.
2. **Tech News.** It is mapped to `general` to protect the corpus that
   works. Daily tech round-ups are structurally news. Measure after
   `news` ships, and consider mapping it then.
3. **Per-episode detection for mixed shows.** Worth a cheap classifier
   only if the override list grows past a handful of shows. Revisit
   after Phase 3.
4. **TV & Film and fiction.** Recaps need spoiler handling like sports.
   Decide after the corpus has enough of either.
