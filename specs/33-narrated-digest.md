# Narrated Digest Specification

> **Status:** 🚧 In progress (Phases 1 + 2 complete)
> **Created:** 2026-05-06
> **Updated:** 2026-05-08
> **Author:** Product & Engineering
> **Related:** [#29 per-user-inbox-fanout](29-per-user-inbox-fanout.md), [digest_generator.py](../thestill/services/digest_generator.py)

---

## Executive Summary

Replace (and complement) the current concatenated digest with a **single-anchor news-style readout**: one coherent script that weaves the day's episodes into theme-grouped segments, with verbatim spoken quotes from hosts and guests inserted as cited clips. The output is a markdown read-through plus a structured JSON script ready for TTS once that lands.

**Mental model:** A radio/TV evening briefing rendered as a script — readable on the page today, synthesisable to audio later. One anchor — informed, slightly wry, never breathless — opens with a 1–2 sentence headline tease, walks through 2–4 lead stories grouped by theme (not by podcast), drops in 1–3 verbatim quote clips per segment for texture and authority, finishes with a rapid-fire tail of "also today…" mentions and a sign-off. Length is bounded by a user-chosen target spoken duration.

**Read or listen.** Reading the briefing is a first-class mode and the v1 default — the markdown is designed to be skimmed in a minute or read end-to-end in five. Audio synthesis via TTS is the eventual pinnacle of thestill (a real "morning radio for your podcasts" experience), but it is always optional. A user who only ever wants to read the briefing should never feel the audio path is the canonical one and the text is a transcript of it; the script is the canonical artefact, audio is one rendering of it.

**Key principle:** **Quotes are extracted, never generated.** The LLM writes the connective tissue between known-good quote spans; the quote text itself is pulled verbatim from [clean_transcripts/](../data/clean_transcripts/) at known timestamps. This is the central fidelity guarantee — and it pays off equally in both modes: in reading, quotes are visibly attributed block quotes; in listening, they're the cue points where original audio can later be spliced in.

---

## Table of Contents

1. [Motivation](#motivation)
2. [Product Requirements](#product-requirements)
3. [Architecture Overview](#architecture-overview)
4. [Pipeline Stages](#pipeline-stages)
5. [Time Budget Model](#time-budget-model)
6. [Quote Selection](#quote-selection)
7. [Script Generation](#script-generation)
8. [Anchor Voice & Prompts](#anchor-voice--prompts)
9. [Output Format](#output-format)
10. [Database & Storage](#database--storage)
11. [CLI & API](#cli--api)
12. [Frontend UX](#frontend-ux)
13. [Migration Strategy](#migration-strategy)
14. [Out of Scope](#out-of-scope)
15. [Open Questions](#open-questions)
16. [Implementation Phases](#implementation-phases)

---

## Motivation

The current digest at [digest_generator.py:261](../thestill/services/digest_generator.py#L261) is a deterministic template: episodes grouped by podcast, each rendered as `### Title` + the 2-sentence "Gist" extracted from the summary. It's useful as a link index and as a what-was-processed log, but it does not answer what users actually want when they wake up to it: *what happened today, across all the shows I follow, and what should I care about?*

A briefing format gives:

- **Cross-show synthesis.** AI-regulation chatter from three different shows folds into one segment, instead of three stub paragraphs in three different sections.
- **Editorial attention curve.** The biggest 2–4 stories get real treatment; the long tail collapses into a "also today" sweep. This matches how real bulletins handle volume.
- **Authentic voice.** Verbatim quoted clips ("here's how Lex put it…") give the briefing texture, prevent paraphrase drift, and once TTS lands, naturally swap to original audio for a true broadcast feel.
- **Bounded runtime.** A user-set target spoken duration ("the 5-minute briefing", "the 10-minute briefing") gives a real promise readers/listeners can plan around — unlike "N stories" which doesn't tell them how long it is, or "N words" which doesn't account for quoted-clip duration.

Stylistic touchstones: the hosts of *The Artificial Intelligence Show* (informed, paced, opinionated without being polemical), traditional news-anchor cadence (lead, body, sign-off), and NotebookLM's audio overviews (energy, conversational asides) — but with a single voice, not the two-host back-and-forth, since (a) it's cleaner to TTS, (b) it avoids the synthetic-banter feel, and (c) the texture comes from real quotes instead of fake co-host disagreement.

---

## Product Requirements

### User Stories

| As a... | I want to... | So that... |
|---------|--------------|------------|
| User | Read a daily briefing that flows like real news, not a list | I can skim it over coffee and feel oriented in 5 minutes |
| User | Pick the briefing length (e.g. 3 / 5 / 10 minutes) | The briefing fits the time I actually have, whether I read or listen |
| User | See verbatim quotes from guests and hosts, not paraphrases | The briefing has authority and isn't a paraphrase soup |
| User | See cross-show themes called out together | I notice when the same story is moving across multiple shows |
| User | Have less-prominent episodes still get a mention | I don't miss the long tail just because the lead stories were big |
| User | Eventually listen to the same briefing as audio, with original quote clips | I can consume it during commute or while cooking — same content, different mode |
| User | Always be able to fall back to the link index | I still need a "where do I click to read the full summary" view, especially on days the narration falls back |

### Core Behaviors

1. **Single anchor, single voice.** No multi-host banter. The anchor speaks; selected quotes punctuate.
2. **Theme-grouped, not podcast-grouped.** The same theme from two podcasts becomes one segment with both attributed.
3. **Time-budgeted.** User picks a target spoken duration; the script lands at or near that runtime, accounting for both narration and quote-clip duration (see [Time Budget Model](#time-budget-model)).
4. **Quote integrity.** All quoted text is verbatim from a cleaned transcript with a known speaker, episode, and start timestamp. The LLM never authors quote content.
5. **Editorial cap with rapid-fire tail.** The model picks 2–4 lead segments, then sweeps remaining episodes into a brief "also today…" tail. The user does not control segment count directly; they control runtime, and the model self-allocates depth vs breadth.
6. **Reproducible.** Given the same episodes and the same target duration, runs produce equivalent scripts (within LLM nondeterminism). Selected quotes are deterministic given the same episode set.
7. **Additive to the existing digest.** The narrated digest does not replace [digest_generator.py](../thestill/services/digest_generator.py); it consumes the same episode selection and runs alongside. The link-index digest stays as the appendix and as the cheap fallback when narration is disabled or fails.
8. **Cheap fallback on partial input.** Episodes missing summaries or facts files are excluded from segment treatment but listed in the rapid-fire tail. The narrator never invents content for episodes that don't yet have summaries.

### Non-Goals

- **TTS itself.** This spec produces the text + structured script; voice synthesis is the eventual pinnacle of thestill but a separate follow-up that consumes the JSON script. The text/markdown rendering is the v1 delivery surface and remains a first-class mode after audio lands — users who prefer to read are never second-class citizens.
- **Per-user briefings.** v1 produces one narration per digest run, matching the current `thestill digest`. Per-user narration plugs in once [#29](29-per-user-inbox-fanout.md) lands and the inbox becomes the per-user trigger source.
- **Multi-language.** English-only for v1, since transcripts are English-only today.
- **Live/streaming generation.** v1 is batch; the user runs `thestill narrate` (or it runs after `digest` in the morning batch).
- **Editing the narrative after generation.** No in-place rewrites in v1; if the user dislikes a run, they regenerate.

---

## Architecture Overview

### Layered View

```
┌──────────────────────────────────────────────────────────────┐
│  CLI / Web                                                   │
│    thestill narrate [--target-duration 5m] [--digest <id>]   │
│    POST /api/narrations { digest_id, target_duration }       │
└──────────────────────────────────────────────────────────────┘
                            │
┌──────────────────────────────────────────────────────────────┐
│  Services                                                    │
│    NarrationGenerator                                        │
│      .generate(episodes, target_seconds) → NarrationContent  │
│        1. cluster_themes()      → list[Segment]              │
│        2. select_quotes()       → list[QuoteCandidate]       │
│        3. compute_word_budget() → int (narration words)      │
│        4. generate_script()     → list[ScriptBlock]          │
│        5. render_markdown()     → str                        │
│        6. render_json_script()  → dict                       │
└──────────────────────────────────────────────────────────────┘
                            │
┌──────────────────────────────────────────────────────────────┐
│  Inputs (existing — no changes)                              │
│    summaries/<podcast>/<episode>.md                          │
│    episode_facts/<podcast>/<episode>.facts.md                │
│    clean_transcripts/<podcast>/<episode>.md                  │
└──────────────────────────────────────────────────────────────┘
                            │
┌──────────────────────────────────────────────────────────────┐
│  Outputs                                                     │
│    data/narrations/YYYY-MM-DD-<slug>.md   (read-through)     │
│    data/narrations/YYYY-MM-DD-<slug>.json (TTS-ready script) │
└──────────────────────────────────────────────────────────────┘
```

### Data Flow

```
DigestSelector (existing)              ┐
   selects episodes                    │
                                       ▼
NarrationGenerator.generate(episodes, target_seconds=300)
   │
   ├── Theme clustering (LLM call #1)
   │     in: facts files (guests, topics, speakers) + summary headlines
   │     out: 2–4 segments + a "tail" bucket, each with episodes & angle
   │
   ├── Quote selection (deterministic)
   │     for each (segment, episode):
   │       scan clean_transcripts → score candidate utterances
   │       on (relevance, length, self-containment, attribution)
   │       pick 0–2 per episode, 1–3 per segment
   │       compute total quote_seconds
   │
   ├── Word-budget computation
   │     remaining = target_seconds − quote_seconds
   │     enforce quote share ≤ 40% of target_seconds
   │     narration_words = remaining × wpm / 60
   │
   ├── Script generation (LLM call #2)
   │     in: segment plan, summaries, selected quotes (verbatim, with ids),
   │         narration word budget, anchor system prompt
   │     out: ordered ScriptBlocks — narration | quote-cue
   │     constraint: model emits <<QUOTE q1>> placeholders only;
   │                 renderer substitutes verbatim text
   │
   └── Render → markdown + JSON script
```

---

## Pipeline Stages

### 1. Theme Clustering

A single LLM call. Inputs (compact, well under any model's context):

- Per episode: title, podcast title, guests, top 8–12 topic keywords, the 2-sentence Gist, sponsors (so they can be filtered out of the narrative).
- The target duration as a hint for how aggressive to be about consolidation.

Output (structured JSON, validated):

```json
{
  "segments": [
    {
      "theme": "AI coding agents in production",
      "angle": "Two senior PMs disagree on whether non-engineers should ship code at work",
      "episode_ids": ["ep-123", "ep-456"],
      "rank": 1
    },
    ...
  ],
  "tail": ["ep-789", "ep-790"]
}
```

Constraints encoded in the prompt:

- 2–4 segments. Episodes that don't fit a multi-show theme go to the tail.
- Each segment names a concrete angle, not just a topic ("disagreement on X" or "what changed about Y" — not just "AI coding").
- Single-episode segments are allowed when the story is a clear lead.

### 2. Quote Selection

Deterministic — no LLM. For each (segment, episode):

1. Parse [clean_transcripts/](../data/clean_transcripts/) into turns: `[MM:SS] **Speaker:** text`.
2. Score each turn on:
   - **Relevance** — embedding similarity (or keyword overlap as a v1 fallback) between the turn text and the segment's `theme + angle`.
   - **Length fit** — prefer 12–35 seconds of speech (≈ 30–90 words at standard rate).
   - **Self-containment** — penalise turns that start with a pronoun, contain dangling references ("that thing we just talked about"), or end mid-sentence.
   - **Attribution clarity** — speaker must resolve via the [episode_facts speaker-mapping](../data/episode_facts/lenny-s-podcast-product-career-growth/head-of-claude-code-what-happens-after-coding-is-solved-boris-cherny.facts.md) section. Turns from `SPEAKER_UNKNOWN` are skipped.
   - **Diversity** — once a turn is picked, suppress neighbours within ±60s and turns from the same speaker beyond a per-speaker cap.
3. Take 0–2 per episode, 1–3 per segment, never more than 1 per speaker per segment in v1.
4. Quote duration = `next_turn_start - this_turn_start` (seconds), since cleaned transcripts are turn-timestamped. When a turn is longer than 35s the quote is *truncated to a sentence-bounded prefix* and the duration is recomputed at the WPM rate (since we don't have word timestamps yet — finer slicing comes when [#18](18-segment-preserving-transcript-cleaning.md) lands word-level timing).

Each candidate becomes:

```python
@dataclass
class QuoteCandidate:
    quote_id: str           # "q1", "q2"… stable within a run
    episode_id: str
    podcast_title: str
    speaker: str            # resolved name, e.g. "Boris Cherny"
    speaker_role: str       # "host" | "guest" | "unknown"
    text: str               # verbatim
    start_seconds: float    # for future audio-clip use
    duration_seconds: float
    score: float            # for diagnostics/logging
```

### 3. Word-Budget Computation

```python
quote_share = sum(q.duration_seconds for q in selected_quotes)
quote_share = min(quote_share, target_seconds * MAX_QUOTE_SHARE)  # 0.40 default
narration_seconds = target_seconds - quote_share
narration_words = int(narration_seconds * WPM / 60)               # WPM 150 default
```

If quotes exceed the share cap, the lowest-scoring quotes are dropped first until under the cap. The cap is config (`narration.max_quote_share`, default `0.40`).

### 4. Script Generation

A single LLM call. Inputs:

- Anchor system prompt (see [Anchor Voice & Prompts](#anchor-voice--prompts)).
- Segment plan from stage 1.
- Per-segment: summaries of the constituent episodes + the **selected quote IDs** with their verbatim text and attribution.
- The narration word budget.
- A per-segment soft word target (`narration_words / num_segments`, with 20% reserved for opener + tail + sign-off).

Output (structured JSON, schema-validated):

```json
{
  "blocks": [
    {"kind": "narration", "text": "Today's briefing… [opener]", "section": "opener"},
    {"kind": "narration", "text": "Our lead story…",            "section": "segment-1"},
    {"kind": "quote",     "quote_id": "q1",                     "section": "segment-1"},
    {"kind": "narration", "text": "Cross-cutting against that…","section": "segment-1"},
    {"kind": "quote",     "quote_id": "q3",                     "section": "segment-1"},
    {"kind": "narration", "text": "Onto our second story…",     "section": "segment-2"},
    ...
    {"kind": "narration", "text": "Also today…",                "section": "tail"},
    {"kind": "narration", "text": "That's it for the briefing.","section": "signoff"}
  ]
}
```

Hard contract enforced post-generation:

- Every `kind: "quote"` block references a quote_id from the selection set; unknown ids fail validation and trigger one regeneration retry.
- No quote text appears inside `kind: "narration"` blocks. (Heuristic check: if a narration block contains a substring ≥ 8 words long that matches verbatim against any quote candidate's text, flag and regenerate.)
- Narration word count is within ±15% of the budget. Outside that, regenerate once with a tightened budget.

After two failed regenerations, fall back to the existing concatenated digest and log a `narration.fallback` event.

### 5. Render

- **Markdown** (`.md`): human-readable read-through with quotes inline as block quotes attributed `— Speaker, Podcast Title (HH:MM)`. Clickable links to episode pages and to the deep-linked timestamp via [spec #23](23-transcript-playback-sync.md)'s timestamp URLs.
- **JSON** (`.json`): the block list verbatim plus per-quote `episode_id`, `start_seconds`, `duration_seconds`, `speaker`. This is the TTS-ready form — narration blocks get synthesised, quote blocks ideally get swapped for the original audio clip when that pipeline lands.

---

## Time Budget Model

Cap on **time spoken**, not segment count or word count. Rationale was litigated in design:

- *Segment count* is exact but doesn't tell the user how long the briefing is — "top 3 stories" can be 90 seconds or 8 minutes.
- *Word count* is closer but ignores quoted-clip duration; a quote-heavy day silently runs longer than a quote-light day with the same word budget.
- *Time spoken* maps directly to what the user buys ("the 5-minute briefing"). It's computable up front because quote durations are exact (we have transcript timestamps) and narration duration is deterministic at a fixed TTS rate.

Configuration:

| Setting | Default | Notes |
|---|---|---|
| `narration.target_durations` | `{short: 180, medium: 300, long: 600}` (seconds) | User-selectable presets |
| `narration.wpm` | `150` | News-anchor rate; tunable per TTS voice |
| `narration.max_quote_share` | `0.40` | Cap on fraction of total runtime spent in quotes |
| `narration.tail_share` | `0.15` | Fraction of narration words reserved for the rapid-fire tail |
| `narration.opener_share` | `0.05` | Fraction reserved for headline tease |
| `narration.signoff_share` | `0.03` | Fraction reserved for sign-off |

Per-run override via CLI: `--target-duration 5m` (parses `120s`, `5m`, `0:05:00`).

---

## Quote Selection

See stage 2 above for the algorithm. A few additional notes:

- **Speaker resolution** depends on the `## Speaker Mapping` section already produced by the facts pipeline. Episodes without a speaker mapping are eligible for narration but their turns are not eligible as quotes.
- **Sponsor-read filtering.** The facts file's `## Ad Sponsors This Episode` is used to filter out turns that are likely ad reads — turns whose text contains a sponsor name and which sit near transition timestamps (within the first 5% or last 5% of the episode, or near known ad-segment markers if those exist).
- **Quote IDs are stable within a run** but not across runs. The JSON script's `episode_id` + `start_seconds` is the durable identifier for clip retrieval.
- **Embedding-based scoring** is preferred but a keyword-overlap fallback (TF-IDF over the segment angle vs the turn) is acceptable for v1 and is the path of least resistance given that embeddings aren't yet a stable repo-wide capability.

---

## Script Generation

See stage 4 above for inputs/outputs. The validation contract is the load-bearing piece — without it, the model will paraphrase quotes, drift past the budget, or invent episodes. Failures regenerate once, then fall back.

The model is given:

- Stable quote IDs and the verbatim text of each, so the model can read them, decide where to cue them, and emit `<<QUOTE q3>>` placeholders. The renderer substitutes; the model never types the quote text.
- The per-segment word target and the explicit instruction to bridge sources within a segment ("X says Y; on a different show, Z pushes back…"), not to summarise each episode in turn.
- An explicit "do not invent" clause naming the failure mode: "If a fact isn't in the inputs, don't say it. If two sources disagree, name the disagreement and attribute both."

---

## Anchor Voice & Prompts

The anchor's voice is configured by a system prompt. v1 ships one default; future work can add user-selectable voices.

**Default anchor — "the briefing":**

> You are the anchor of a daily podcast briefing. Single voice. Tone: informed, slightly wry, never breathless. Pacing: news-anchor measured, not radio-DJ excited. You assume the listener is smart and busy — get to the point, name names, surface disagreement explicitly, don't editorialise beyond what the sources support.
>
> Your job is to weave the day's episodes into a coherent readout, not to summarise each one in turn. When two episodes touch the same theme, bridge between them ("On the same morning that X argued Y, over on Z's show, A said the opposite…"). Name guests when you cite their views. Cue quotes naturally ("here's how she put it" / "his words, not mine") — never paraphrase a quoted line.
>
> Format: open with a 1–2 sentence headline tease. Then the lead segments in priority order, each ending on a clean transition. Then a brief "also today…" rapid-fire tail. Close with a one-line sign-off. You will be given quote slot IDs in the form `<<QUOTE qN>>` — emit those placeholders verbatim where you want the quote to play; do not type the quote text yourself.

The prompt is stored at [`thestill/services/narration_prompts/default_anchor.md`](../thestill/services/narration_prompts/default_anchor.md) (new file) and loaded at runtime so it can be tuned without code changes.

---

## Output Format

### Markdown (`data/narrations/2026-05-06-morning.md`)

```markdown
# Morning Briefing — May 6, 2026
*Target: 5 minutes · Actual: 4m 52s · 7 episodes covered*

---

Today on the briefing: AI coding agents are starting to ship at non-technical
companies, Anthropic's coding lead admits the scaling laws still hold, and a
quiet disagreement is opening up about whether junior PMs should be writing
production code at all.

## Lead — AI coding agents in the wild

Two of today's interviews land on the same fault line. On Lenny's Podcast,
Zevi Arnovitz — a non-technical PM at Meta — argued the bar has fundamentally
shifted:

> It's the best time to be a junior, contrary to what a lot of people are
> saying… when else in history could you get out of school and just build
> a startup on your own?
> — Zevi Arnovitz, Lenny's Podcast (00:59) · [Listen](…)

…

---

## Also today

- *The AI Daily Brief* on the new Anthropic enterprise pricing — see [summary](…).
- *BG2Pod* with Brad Gerstner on Q1 capital flows — see [summary](…).

That's the briefing for May 6th. Back tomorrow.
```

### JSON Script (`data/narrations/2026-05-06-morning.json`)

```json
{
  "generated_at": "2026-05-06T07:00:00Z",
  "target_duration_seconds": 300,
  "actual_duration_seconds": 292,
  "wpm": 150,
  "blocks": [
    {
      "kind": "narration",
      "section": "opener",
      "text": "Today on the briefing: AI coding agents…",
      "duration_seconds": 12
    },
    {
      "kind": "narration",
      "section": "segment-1",
      "text": "Two of today's interviews land on the same fault line…",
      "duration_seconds": 24
    },
    {
      "kind": "quote",
      "quote_id": "q1",
      "episode_id": "lenny-zevi-arnovitz",
      "podcast_title": "Lenny's Podcast",
      "speaker": "Zevi Arnovitz",
      "speaker_role": "guest",
      "text": "It's the best time to be a junior…",
      "start_seconds": 59,
      "duration_seconds": 12
    },
    …
  ],
  "episodes_covered": [ "lenny-zevi-arnovitz", … ],
  "episodes_in_tail": [ … ]
}
```

The JSON is the contract for downstream TTS. It is intentionally thin so future voices can be plugged in without schema churn.

---

## Database & Storage

Pure-additive. No schema changes required for v1.

- Output files live under `data/narrations/`. Filename pattern `YYYY-MM-DD-<slug>.{md,json}` where slug defaults to `morning` and can be overridden by the caller.
- A future `narrations` table may track runs (id, generated_at, target_duration, episode_ids, output_path, fallback_reason) once the web UI surfaces a history view. Out of scope for v1.
- The existing [SqliteDigestRepository](../thestill/repositories/sqlite_digest_repository.py) is unchanged. The narrated digest is a sibling artefact, not a replacement.

---

## CLI & API

### CLI

```bash
# Standalone
thestill narrate                          # uses last digest, default duration
thestill narrate --target-duration 10m
thestill narrate --digest 2026-05-06-morning
thestill narrate --dry-run                # plan + quote selection, no LLM script call

# Chained from digest (preferred morning workflow)
thestill digest --narrate
thestill digest --narrate --target-duration short
```

### API

```http
POST /api/narrations
Content-Type: application/json

{
  "digest_id": "2026-05-06-morning",
  "target_duration_seconds": 300
}
```

```json
HTTP/1.1 201 Created
{
  "status": "ok",
  "narration": {
    "id": "2026-05-06-morning",
    "target_duration_seconds": 300,
    "actual_duration_seconds": 292,
    "markdown_path": "data/narrations/2026-05-06-morning.md",
    "script_path":   "data/narrations/2026-05-06-morning.json",
    "fallback":      false
  }
}
```

```http
GET /api/narrations/{id}                  # → markdown + JSON
GET /api/narrations/{id}/script.json      # → JSON only (TTS consumer)
```

---

## Frontend UX

A read view, not a heavy interaction surface for v1.

- **Inbox-empty / morning hook.** A "Today's briefing" card appears at the top of the inbox when a recent narration exists. Click → reader view.
- **Reader view.** Renders the markdown with quotes as styled block quotes. Each quote has a `▶ Listen at HH:MM` link that deep-links to the episode page at the timestamp ([spec #23](23-transcript-playback-sync.md)).
- **Length switcher.** A small chip group at the top — `Short · Medium · Long` — re-runs the narration at a different duration. The previous version is preserved (filenames are keyed on duration).
- **Fallback state.** If narration failed or fell back, the reader view says "We couldn't generate a briefing for today; here's the link index instead" and embeds the existing digest output.

The frontend is read-only against the markdown + JSON files via the API; no rendering logic in the browser.

---

## Migration Strategy

Pure-additive. No data backfill required.

1. New service `NarrationGenerator` (sibling of [DigestGenerator](../thestill/services/digest_generator.py)).
2. New CLI command `thestill narrate` and `--narrate` flag on `digest`.
3. New API endpoints under `/api/narrations`.
4. New output directory `data/narrations/` (created lazily on first run).
5. Existing digest behaviour is unchanged. The narrated digest is opt-in until the team is confident; the morning batch flips to `--narrate` when fallback rates are low enough in production.

---

## Out of Scope

- **TTS audio synthesis.** This spec produces the JSON script that a TTS stage consumes; voice selection, audio rendering, and clip splicing belong to a follow-up.
- **Original-audio quote splicing.** v1 emits quote text only; future TTS work can swap quote blocks for original-audio clips using the `episode_id + start_seconds + duration_seconds` triple already in the JSON.
- **Per-user narrations.** v1 is a single narration per digest run. Once [#29](29-per-user-inbox-fanout.md) lands and the inbox becomes the per-user trigger source, the same generator runs per user with their own preset.
- **Multi-language.** English-only.
- **Multi-anchor / co-host format.** Single voice in v1; the prompt is structured to allow alternative anchor personas (regional, topical, parodic) without code changes.
- **Live edit of generated narrations.** No in-place rewrites; regenerate to change.
- **Word-level quote slicing.** Quote granularity is the speaker turn (or a sentence-bounded prefix when the turn is long). Finer slicing requires word-level timestamps from [#18](18-segment-preserving-transcript-cleaning.md) / [#24](24-word-level-transcript-highlighting.md).

---

## Open Questions

| # | Question | Recommendation |
|---|---|---|
| O1 | Quote duration cap as a fraction of target — is 40% right? | Start at 40%; instrument and tune. Anything below 25% feels too narrated; above 50% the anchor disappears. |
| O2 | Embedding-based quote scoring vs keyword fallback for v1 | Ship the keyword fallback first; promote to embeddings once they exist as a repo-wide capability. The scoring interface should be pluggable. |
| O3 | When a segment has only one episode, is that still a "segment" or should it be merged into the tail? | Allow single-episode lead segments — the model decides editorially. Forcing a 2+-episode rule produces awkward groupings on light news days. |
| O4 | Should the user be able to pin a "must cover this episode" hint? | Not in v1. The selection is whatever `digest` selected. Pin/unpin belongs to the per-user inbox model in [#29](29-per-user-inbox-fanout.md). |
| O5 | Where does the anchor prompt live — code, config, or DB? | File on disk under `thestill/services/narration_prompts/`. Easy to diff, ship, and override per environment. DB-stored prompts come if/when users get to author their own. |
| O6 | What's the cost ceiling per run we're comfortable with? | Two LLM calls (cluster + script) per run is bounded; the script call dominates and scales with quote count + summary count. Estimate at ~$0.02–$0.05 per 5-minute briefing on Sonnet-tier; revisit when actuals land. |
| O7 | If narration fails twice and falls back, do we surface that to the user or silently log? | Surface a soft banner ("link-index briefing today — narration unavailable") and log structured. Hiding fallbacks erodes trust. |
| O8 | Does the rapid-fire tail have its own cap, or flush every leftover episode? | Soft cap from the tail-share budget (~15% of words ≈ 8–12 episodes at medium length); past that, truncate with a "…and N more in the link index" line. |

---

## Implementation Phases

### Phase 1 — Quote selection + JSON script (no LLM-narrated prose) ✅ Complete

- Parse cleaned transcripts into turns with speaker resolution from facts files. _(`thestill/services/narration/transcript_loader.py` reads the `AnnotatedTranscript` JSON sidecar and pairs each `content` segment with the resolved name from the episode-facts `Speaker Mapping` section.)_
- Implement deterministic quote scoring + selection. _(`thestill/services/narration/quote_selector.py` — keyword-overlap relevance with neutral fallback, length-fit triangle, self-containment penalties, neighbour suppression, per-speaker cap.)_
- Emit a "skeleton" JSON script: per-episode chrome blocks + selected quote blocks, no anchor narration yet — proves the data path and the validation contract. _(`thestill/services/narration/narration_generator.py`; output at `data/narrations/YYYY-MM-DD-<slug>.json` with `schema_version: "phase1"`.)_
- Tests: quote selection is deterministic, sponsor-read filter works, attribution is always resolved. _(`tests/unit/services/narration/`, 28 tests.)_

### Phase 2 — Theme clustering + script generation ✅ Complete

- Theme clustering LLM call with structured-output validation. _(`thestill/services/narration/theme_clusterer.py` — Pydantic-validated `_ThemePlanOut`; reconciliation drops invented ids, caps at 4 segments, routes overflow to the tail; LLM error degrades to tail-only.)_
- Anchor system prompt + script-generation LLM call. _(`thestill/services/narration_prompts/default_anchor.md` loaded at runtime; `thestill/services/narration/script_writer.py`.)_
- Validation contract: quote-id enforcement, no-verbatim-leak check (8-word n-gram match against any quote, normalised lowercase + collapsed punctuation), word-budget tolerance (±15%), regenerate-once-then-fallback. _(See `_validate` and `_tighten_prompt` in `script_writer.py`.)_
- Markdown renderer. _(`thestill/services/narration/markdown_renderer.py` — date header + runtime byline, segment headings, block-quote attribution with `▶ Listen at HH:MM` deep links via the new `UrlGenerator.episode_at`, link-index appendix.)_
- Fallback to the link-index digest on validation failure. _(`NarrationGenerator._build_fallback_narration`: produces `mode="fallback"` content with the existing `DigestGenerator` markdown prefixed by a "narration unavailable" banner; emits a `narration.fallback` structured log with the failure reasons.)_

### Phase 3 — CLI + API + opt-in morning workflow

- `thestill narrate` standalone command.
- `thestill digest --narrate` chained command.
- `POST /api/narrations` and `GET /api/narrations/{id}`.
- Structured logging + a `narration.fallback` metric.

### Phase 4 — Frontend reader + length switcher

- "Today's briefing" card on the inbox.
- Reader view with deep-linked quote timestamps.
- Length switcher chips (Short / Medium / Long).
- Fallback banner when narration is missing.

### Phase 5 — Polish + docs

- `docs/narration.md` covering the prompt, the time-budget model, and how to tune `wpm` / `max_quote_share`.
- Cost + latency dashboard tile.
- Sample narration committed to the repo for easy demoing.

---

## Cross-References

- **Spec #29** — Per-user inbox fanout. v1 narration is global; once #29 lands, narration runs per user from their own inbox selection with their own duration preset.
- **Spec #18 / #24** — Segment-preserving cleaning + word-level highlighting. Both unlock finer-grained quote slicing than the per-turn granularity in v1.
- **Spec #23** — Transcript playback sync. The deep-linked `▶ Listen at HH:MM` quote links use the timestamp URLs introduced there.
- **Spec #28** — Corpus search and entities. The entity index is a future input to theme clustering — "every episode mentioning Anthropic this week" becomes a free segment angle once entities are first-class.
- **[digest_generator.py](../thestill/services/digest_generator.py)** — The current concatenated digest, retained as the appendix and as the fallback when narration validation fails.
- **[digest_selector.py](../thestill/services/digest_selector.py)** — Episode selection. Unchanged; narration consumes whatever it selects.
