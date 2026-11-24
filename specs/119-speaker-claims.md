# Speaker Claims: What Was Said About Whom

> **Status:** 📝 Draft (2026-11-24). Phase 0 spike done; results in
> [Phase 0 spike](#phase-0-spike-2026-11-24)
> **Created:** 2026-11-24
> **Updated:** 2026-11-24
> **Priority:** Medium. Depth for the connector and the entity pages; it
> does not fix activation (spec #111), which stays the first priority.
> **Related:** [#28 corpus-search-and-entities](28-corpus-search-and-entities.md)
> (entities, mentions, `list_quotes_by`), [#81 live-wikidata-entity-linking](81-live-wikidata-entity-linking.md)
> (resolution), [#67 metadata-speaker-priors](67-metadata-speaker-priors.md)
> and [#110 transcript-editor](110-transcript-editor.md) (who is speaking),
> [#106 episode-reprocess](106-episode-reprocess.md) (re-running a stage),
> [#96 prompt-injection-hardening](96-prompt-injection-hardening.md),
> [#53 eval-runs-and-summary-rubric](53-eval-runs-and-summary-rubric.md),
> [#100 quote-ranking-and-featured-quotes](100-quote-ranking-and-featured-quotes.md)
> (the public gate), [#111 product-health-metrics](111-product-health-metrics.md)

---

## Overview

Thestill knows that a person and a company were mentioned in the same
episode, and it can return the sentences where a name was said. It
cannot say what position a speaker took: that David Sacks accused
Anthropic of regulatory capture, or that Josh Browder now expects
Anthropic to reach a trillion dollars in revenue after calling AI a
bubble a year earlier.

This spec adds **claims**: one row per substantive position a named
speaker takes, tied to the exact transcript words and the entities it is
about. A claim has the speaker, a one-sentence statement of the
position, a kind (opinion, prediction or factual assertion), a stance
toward each entity it is about, and evidence that points into the
transcript.

Claims are a layer on the entity system that already exists. There is
no second entity store, no graph database and no separate "graph
engine" package. Two questions become answerable, first over MCP and
then on entity pages:

- **What does X say?** Every claim by a speaker, grouped by what it is
  about.
- **What do people say about Y?** Every claim about an entity, by
  stance, with who said it and where.

## Where this came from

The starting point was a product brief for a "Temporal Claim & Entity
Knowledge Graph" built as a domain-agnostic engine with pluggable
ontologies, meant to be lifted out later as a B2B SDK. This spec keeps
the claim layer from that brief and rejects the rest:

| Brief | This spec | Why |
|---|---|---|
| New `entities` table with embedding-threshold resolution and automatic alias appends | Claims reference the existing `entities.id` | Resolution already runs on aliases plus Wikidata (#81). Automatic alias appends are how the alias pollution happened |
| Subject–predicate–object triples from a fixed predicate list (`PRAISED`, `CRITIQUED`, …) | A sentence stating the position, plus stance per target | "Altman CRITIQUED OpenAI" drops what the criticism was |
| The model returns `start_ms` / `end_ms` and a verbatim quote | The model returns a segment id and a quote used only as a pointer; code finds the words and takes times from the segment | The spike shows the model's quote text is not reliable enough to display |
| Stored `entity_transitions` table | Stance changes are computed when asked for | A stored table goes stale each time claims are re-extracted |
| `graph_multi_hop` over `INVESTED_IN`, `COMPETES_WITH` | Not built | Those are facts about the world, which Wikidata holds, not things a speaker claimed |
| `tenant_id` and `acl_tags` on every query; a separate `core/graph` package | Not built | One public corpus. Nothing would test a second tenant, so the code would be wrong on the day it was needed |
| Customer-discovery and support ontologies | Not built | No customer has asked. Revisit after a paying pilot |

## Phase 0 spike (2026-11-24)

**Question.** Can one cheap model call per episode produce claims that
are attributed to the right speaker, say what the speaker said, and can
be tied to transcript words by code?

**Method.** 16 cleaned transcripts, two each from eight tech and
investing shows (20VC, The a16z Show, Big Technology, Latent Space,
Dwarkesh, The MAD Podcast, Training Data, All-In), picked at random
(seed 119) from episodes of 20–150 minutes where at least 90% of
segments have a named speaker. One `gemini-3-flash-preview` call per
episode, thinking level low, the whole transcript as `[segment_id]
Speaker: text` lines, structured output. Scripts:
[claims_spike.py](../scripts/spikes/claims_spike.py) and
[claims_anchor.py](../scripts/spikes/claims_anchor.py).

**Cost and volume.**

| Measure | Result |
|---|---|
| Audio | 18.3 hours |
| Tokens | 298 k in, 38 k out |
| Cost | $0.26 total, **$0.014 per audio-hour** |
| Time per episode | median 11 s, longest 18 s |
| Claims | 266, or 14.6 per audio-hour (8 to 35 per episode-hour) |
| Kinds (anchored claims) | 120 opinion, 92 factual assertion, 46 prediction |

**Tying claims to transcript words.**

| Outcome | Claims | Share |
|---|---|---|
| Quote found word for word in the claimed segment or a neighbour | 183 | 69% |
| Found after token alignment (quote runs across consecutive segments of the same speaker, or the model dropped filler words) | 75 | 28% |
| Dropped (the model joined two passages with "...", or the quote ran across a question and its answer) | 8 | 3% |

No quote was invented. Every failure of the exact match was the model
tidying up: 34 quotes crossed a segment boundary, 23 used an ellipsis
and 9 removed disfluencies. So the model's quote can locate the passage
but cannot be the text that is shown.

**Quality.** Claude read a random sample of 96 anchored claims (6 per
episode) against the transcript segment and the one before it. This was
one judge and one pass; there is no second rater.

| Finding | Claims | Notes |
|---|---|---|
| Right speaker, a real claim, stated faithfully | 86 of 96 (90%) | 3 more are uncertain, so the range is 86–90% |
| Reported speech attributed to the person reading it | 5 | A host reading Satya Nadella's email, a New York Times line on Dario Amodei, a tweet. 4 of the 5 are in one news-roundup show |
| Hedge lost | 3 | "maybe that's what Elon's doing" and "this is just speculation" became firm predictions; a stated goal became a prediction |
| Not a claim | 1 | A question |
| Content garbled | 1 | "the current administration is a lot better" came out as the opposite comparison |
| Kind wrong (among correct claims) | about 6 | Mostly opinion labelled factual assertion |
| Stance wrong for a target | about 5 | A company used as an example marked positive |
| Evidence in the cold-open montage | 3 | The teaser repeats a line from the body, so the claim is found twice |
| Speaker boundary splits a sentence | 2 | Diarization put half a sentence on the next speaker; one of the two attributions is wrong upstream |

**Targets.** 255 distinct target names in 258 claims. Companies recur
across episodes (Anthropic 24 claims, OpenAI 20, Google 14, Microsoft
8), so "what people say about Anthropic" already has material from 16
episodes. Topics do not: 195 names appear once, and one concept arrives
under three spellings ("AI", "artificial intelligence", "Artificial
Intelligence"). Only 20 of 439 targets are people.

**Not measured.** Recall (what share of an episode's important claims
were found), how targets resolve against the real entity tables, and
anything outside tech and investing shows.

**Second run, with instructions for the known mistakes.** The same 16
episodes, the same model, and a prompt that adds `holder` and `hedged`
fields plus a list of mistakes to avoid (reported speech, lost hedges,
goals as predictions, questions, cold-open montages, joined quotes).
Cost was unchanged at $0.014 per audio-hour.

| Check | First run | Second run |
|---|---|---|
| The 10 claims judged wrong | 10 wrong | 9 fixed: 4 now name the person quoted as holder, 1 is marked hedged, 4 are no longer extracted. 1 still garbled ("the current administration") |
| Quote matches the transcript word for word | 69% | 78% |
| Anchored after alignment | 97% | 98% |
| Claims with evidence in an intro segment | 5 | 2 |
| Claims marked as someone else's position | not asked | 34 of 266 (13%) |
| Claims marked hedged | not asked | 46 of 266 (17%); 16 of them still labelled prediction |

This checks only that the old errors went away. A fresh sample was not
judged, so new kinds of error and wrongly set `holder` or `hedged` flags
are not ruled out. Phase 1's golden set settles that.

**What the spike changes in the design.**

1. Evidence is a segment range plus character offsets found by code.
   The displayed quote is cut from the transcript.
2. The extractor must say who holds the position. A claim whose holder
   is not the speaker is dropped in v1.
3. The extractor must mark hedged statements, and a hedged statement is
   never a prediction.
4. Topic targets resolve to existing entities or are kept as plain text.
   Extraction never creates a topic entity.
5. Intro segments are evidence only when no later segment carries the
   same claim.
6. At $0.014 per audio-hour there is room for a second pass (a checker
   on attribution and faithfulness) inside a $0.03 budget.

## Design

### 1. Data model

Two tables, in Postgres and in `SCHEMA_SQL` for SQLite, written through
a `ClaimRepository`.

**`claims`**

| Column | Notes |
|---|---|
| `id` | Primary key |
| `episode_id` | Foreign key to `episodes` |
| `speaker_label` | The diarization label of the first evidence segment, as stored in the raw sidecar. The durable link to the speaker; survives a rename |
| `speaker_entity_id` | Nullable foreign key to `entities`. Filled from the same speaker-to-entity resolution that `list_quotes_by` uses |
| `claim_text` | One sentence, readable without the transcript |
| `kind` | `opinion`, `prediction`, `factual_assertion` |
| `hedged` | Boolean. True when the speaker marks it as speculation |
| `horizon` | Free text, predictions only ("by 2030") |
| `first_segment_id`, `last_segment_id` | Cleaned-transcript segment ids |
| `char_start`, `char_end` | Offsets of the evidence within the first and last segment |
| `start_ms`, `end_ms` | Copied from the segments at write time |
| `extractor` | Model and prompt version |
| `transcript_version` | The cleaned transcript's content hash, to detect staleness |
| `created_at` | ISO-8601 with `+00:00` |

**`claim_targets`**

| Column | Notes |
|---|---|
| `claim_id` | Foreign key to `claims`, cascade delete |
| `entity_id` | Nullable foreign key to `entities` |
| `surface_name` | The name the extractor gave, always kept |
| `entity_type` | `person`, `company`, `product`, `topic` |
| `stance` | `positive`, `negative`, `neutral`, `mixed` |

There is no `evidence` table: a claim has exactly one evidence span.
The same position stated twice in an episode is two claims, and grouping
them is a read-time concern.

The quote shown to a reader is always cut from the transcript at read
time through the one transcript loader (spec #110), so transcript edits
apply to it like to every other reader.

### 2. Extraction

A new pipeline stage, `extract-claims`, on the entity branch after
`resolve-entities`. One call per episode on the cleaning model.

**Input.** Segments of kind `content`, `intro` and `outro` from the
cleaned sidecar with the edit log replayed, as `[segment_id] Speaker:
text`. The transcript is fenced as untrusted data (spec #96) and the
call binds no tools.

**Output per claim.** `segment_id`, `quote` (pointer only), `claim`,
`kind`, `hedged`, `holder` (`speaker`, or the name of whoever holds the
position), `targets` with stance, `horizon`.

**Checks in code, in this order.** A claim that fails one is dropped
and counted in the stage's structured log:

1. **Anchor.** Align the quote's tokens against the claimed segment and
   up to two neighbours on each side with the same speaker. Keep the
   claim when at least 80% of the quote's tokens land in one contiguous
   window. Store the window, not the quote.
2. **Named speaker.** The first evidence segment's speaker is a name,
   not `SPEAKER_nn` or `Unknown`.
3. **Holder.** `holder` is the speaker. Reported speech is dropped in
   v1. Attributing it to the person quoted is a later option.
4. **Hedge.** A hedged claim with kind `prediction` becomes `opinion`.
5. **Teaser.** When two claims in one episode have the same speaker and
   overlapping evidence text, and one is in an `intro` segment, drop the
   intro one.

**Episodes that are skipped.** Fewer than 90% of segments with a named
speaker; non-English transcripts in v1.

**Targets.** Each target name goes through the existing resolver in
lookup-only mode: match an existing entity by name or alias, of the
given type. A match fills `entity_id`. No match leaves it null and keeps
`surface_name`. Extraction never creates an entity and never adds an
alias.

**Idempotency.** Re-running the stage for an episode deletes that
episode's claims and writes the new set in one transaction.

### 3. Staying correct when other things change

| Event | What happens to claims |
|---|---|
| `entity merge KEEPER LOSER` | `speaker_entity_id` and `claim_targets.entity_id` move to the keeper, inside the same audited service call |
| `entity split`, `move-mentions`, `relink`, `set-type` | The affected episodes' claims are re-resolved (targets and speaker), not re-extracted |
| `transcript rename-speaker` (#110) | `speaker_entity_id` is re-resolved for claims with that `speaker_label` in that episode, as part of the entity refresh the rename already queues |
| Re-clean or re-transcribe (#106) | Segment ids change, so the episode's claims are deleted and `extract-claims` is queued. `reprocess --from entities` includes the stage; a new `--from claims` runs it alone |
| Entity deleted or pruned | `claim_targets.entity_id` and `speaker_entity_id` are set to null; the claim stays |

All data changes go through the repository and the curation service.
There is a `thestill claims` CLI group for inspection and backfill
(`show`, `backfill`, `stats`), not raw SQL.

### 4. Read paths

**MCP first.** Two tools on the existing server, read-only:

- `list_claims_by(speaker, about?, kind?, date_range?, limit?)`: what a
  person has said, newest first.
- `list_claims_about(entity, stance?, kind?, date_range?, limit?)`: what
  people say about an entity, with a count of distinct speakers per
  stance.

Each row carries the claim sentence, the transcript quote, speaker,
show, episode date, the stance per target, and the same deep link
`list_quotes_by` returns. `hedged` is returned so the client can say so.

**Stance over time** is the same query sorted by date for one speaker
and one target. A change is reported only when the two claims are both
unhedged, at least 30 days apart and of opposite stance, and the tool
returns both quotes side by side. It never uses the word
"contradiction".

**Entity pages, later.** A person page gains "What they say", grouped
by target. A company, product or topic page gains "What people say",
split by stance with speaker counts. The transcript quote and the audio
jump lead; the claim sentence is the label. Public pages show a claim
only when it passes the public gate of spec #100 (named speaker linked
to a person entity, not a sponsor read, entity not hidden).

### 5. Quality gates

A golden set of 100 hand-judged claims from the spike's 16 episodes is
kept in `data/evals` and run through the eval harness (spec #53).
Thresholds before any phase after 1 ships:

| Metric | Gate |
|---|---|
| Right speaker, a real claim, stated faithfully | ≥ 95% (spike: 86–90% before the holder and hedge checks) |
| Stance right, of correct claims | ≥ 95% |
| Recall against a hand-made list of each episode's main positions, 5 episodes | ≥ 70% |
| Cost | ≤ $0.03 per audio-hour, checker pass included |

If the single pass cannot reach 95%, add a checker pass: a second call
that sees each claim with its evidence passage and answers whether the
speaker holds this position and whether the sentence states it. The
spike's cost leaves room for it.

Prompt changes follow the standing rule: old against new on 10 random
episodes, three repeats on the ones that move.

## Failure modes

| Failure | Guard |
|---|---|
| A claim pinned on the wrong person | Named-speaker and holder checks; the 90% named-segment floor per episode; public pages require the #100 gate |
| A wrong diarization boundary gives half a sentence to the next speaker | Not solved here. The anchor window never crosses a speaker change, so such claims tend to fail the 80% check; the rest are a known residual counted in the eval |
| The model follows an instruction inside a transcript | Fenced input, no tools, output validated against the schema, every claim re-anchored in code |
| A re-clean leaves claims pointing at segments that moved | `transcript_version` is compared at read time; stale claims are not served and the stage is queued |
| Topic names fragment ("AI" three ways) | Targets resolve to existing entities or stay as text; unresolved targets are not aggregated |
| A stage failure blocks the entity branch | `extract-claims` is last on the branch and its failure does not fail the episode |
| Bulk backfill overloads the queue or the provider | Backfill is paced and resumable, newest first, with `--max-episodes` |

## Non-goals

- A graph database, multi-hop path queries, or a graph visualisation.
- A reusable engine, tenants, access-control tags, or other verticals.
- Facts about the world that nobody said on a show.
- Checking whether predictions came true.
- Attributing reported speech to the person quoted.
- Non-English episodes.

## Phases

| Phase | Deliverable | Ships to users |
|---|---|---|
| 0 | The spike above and this spec | No |
| 1 | Tables, repository, `extract-claims` stage with the five checks, `thestill claims` CLI, golden set and eval; run on the 16 spike episodes plus 50 more across genres | No |
| 2 | MCP tools `list_claims_by` and `list_claims_about`; curation and reprocess hooks; backfill of the last 90 days | Yes, connector only |
| 3 | Entity page sections behind the #100 public gate; stance over time in MCP | Yes |

Phase 2 starts only when Phase 1 meets the gates in §5.

## Open questions

1. **Which Scorecard number is this for?** The honest answer today is
   connector days and public entity-page traffic, not activation. If
   neither moves within a month of Phase 2, stop before Phase 3.
2. **Factual assertions.** A third of the spike's claims are news
   read-outs ("Anthropic settled for $1.5 billion"). They are true to
   the transcript but are not a position. Keep them, or restrict v1 to
   opinions and predictions?
3. **Hosts.** On roundup shows most claims are the hosts'. Is "what
   Jason Calacanis says" worth a page, or should host claims be
   down-ranked like host mentions are?
4. **Genres.** The spike covers interview and roundup shows about tech.
   News, history and comedy shows are untested and may need to be
   excluded by summary profile (spec #98).
