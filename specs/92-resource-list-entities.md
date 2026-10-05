# Resource List Entities

> **Status:** 💡 Proposal
> **Created:** 2026-10-05
> **Updated:** 2026-10-05
> **Priority:** Medium — the summary section that is *only* entities links the fewest of them
> **Author:** Product & Engineering
> **Related:** [#28 corpus-search-and-entities](28-corpus-search-and-entities.md), [#53 eval-runs-and-summary-rubric](53-eval-runs-and-summary-rubric.md), [#54 summary-segment-citations](54-summary-segment-citations.md), [#81 live-wikidata-entity-linking](81-live-wikidata-entity-linking.md), [#82 summary-entity-links](82-summary-entity-links.md), [#83 summary-entity-mentions-pipeline](83-summary-entity-mentions-pipeline.md)

---

## Executive Summary

Section 8 of every summary, the 📚 Resource List, is the summarizer's
own entity extraction. For each item it gives a name, usually a gloss
("The Nobel Prize winner who developed the lithium-ion battery"), and a
cited timestamp that #54 has already resolved to a transcript segment.
The summarizer read the whole episode to produce it. This is the
best-informed entity signal the pipeline creates, and nothing reads it.
The eval checks only that the heading exists. Narration skips the
section. Entity extraction runs after the summary but never opens it.

As a result, the Resource List links worse than the rest of the
summary. #82 can link a name only if GLiNER found it in the transcript.
Resource items are mostly the passing references GLiNER misses
("I've been going back and watching Mad Men"). And when one is found,
nothing in the type rules can file it correctly. For example:

- one Prof G Markets episode (2026-10-05) lists six resources;
- all six appear word for word in the transcript;
- only one, *Margin Call*, became an entity, and it is typed `topic`.

This spec makes the Resource List an **input to entity extraction**.
It does not add a source of mentions. The work is in four steps:

1. **Contract.** The prompt fixes one line shape per item: name, kind,
   gloss, timestamp. The parser also accepts the shapes that today's
   summaries already use, so the backfill does not need to re-run
   summarization.
2. **Grounding.** Every item must be found in the transcript, near the
   segment its citation points to. Items that cannot be found are
   dropped, so a hallucinated resource never becomes an entity.
3. **Seeding.** A grounded item works like the host/guest anchor scan:
   every occurrence of the name in the transcript becomes a mention.
   These are ordinary transcript mentions with a real segment, so the
   mentions schema does not change. The kind and gloss go to the #81 linker as
   context.
4. **Typing.** Fix the P31 rule that turns every film into a topic. In a
   later phase, give books, films, shows and podcasts a type of their
   own.

The reader needs no change. Once the transcript holds the entity, the
matcher from #82 links the Resource List line like any other summary text.

---

## Problem

### The section is unparsed prose

The prompt ([post_processor.py:141](../thestill/core/post_processor.py#L141))
asks for *"Bullet list of books, tools, or people mentioned. Include
timestamps."* The few-shot example leaves out section 8, so the model
chooses its own format. A sample of the 200 most recent local summaries
(1,002 items, 5.0 per episode, measured 2026-10-05) shows:

- **83% carry no kind.** The dominant shape is
  `* **Name:** gloss [12:09](?t=…&cite=c23)`. The `Name (TV Show) [12:09]`
  shape in the motivating episode is the minority.
- **Combined items are common**: "Vercel / GitHub",
  "Midjourney / ChatGPT (DALL-E)", "Nebius Group & CoreWeave".
- **Titles carry authors and decoration**: "*Die with Zero* (Book) by
  Bill Perkins", "The Ring Cycle* (Das Rheingold) by Richard Wagner".
- **Timestamps are present on 998 of 1,002 items**, already rewritten
  into #54 citation links that point at a transcript segment.

### The names are in the transcript, the entities are not

Even with a deliberately crude parser, **76%** of item names appear
verbatim in the cleaned transcript, and **65%** appear within 90 s of
the cited timestamp. Most of the remaining misses come from combined
items and "Title by Author" strings, which a contract and a slightly
better parser recover.

The transcript contains the words, but extraction does not keep them:

| Item (Prof G Markets, 2026-10-05) | Occurrences in transcript | Episode entity |
|---|---|---|
| Mad Men | 4 | none |
| Modern Family | 3 | none |
| Margin Call | 1 | `topic:margin-call` |
| Ramp | 1 | none |
| Vanguard | 2 | none |
| Paul Kedrosky | 3 | none (419 mentions in other episodes) |

GLiNER runs with four labels (person, company, product, topic;
[entity_extractor.py:72](../thestill/core/entity_extractor.py#L72)) and
a 0.65 threshold. That combination is weakest on this kind of passing
reference: a show mentioned in a joke, a fund recommended in one
sentence, a name dropped while introducing the next segment.

### Works are mis-typed when they are found

[entity_type_rules.py](../thestill/core/entity_type_rules.py) has two
problems:

- `TOPIC_P31` contains `Q11424`, commented as "genre". It is Wikidata's
  *film* (film genre is Q201658). The topic check runs before the
  product check, so **every film resolves to `topic`**, whatever GLiNER
  said.
- TV series (Q5398426) and books (Q571) are in `PRODUCT_P31`, but they
  stay products only when GLiNER's label was already `product`. If the
  label was `topic` or `company`, they become `topic`.

In prod, *Margin Call* is `topic:margin-call` and *Mad Men* is
`topic:mad-men` with the alias "advertising". Neither is wrong enough
to notice in one place, but together they make "works mentioned"
impossible to build.

### Nothing checks the section

`REQUIRED_SECTIONS` in
[summary_checks.py:42](../thestill/evals/summary_checks.py#L42) checks
only that the heading exists. Narration leaves the section out on
purpose (#77). No consumer means no feedback, which is how the format
drifted and how timestamp collisions like Mad Men and Ramp both citing
`[12:09]` go unnoticed.

## Goals

1. Every Resource List item that names something said in the episode
   becomes an episode entity with mentions at **every** place the
   transcript says it, not only at the cited timestamp.
2. An item that cannot be found in the transcript never becomes an
   entity, mention or link.
3. The linker gets the summarizer's kind and gloss as disambiguation
   context, e.g. "Margin Call (film)" or "Vanguard (fund manager)".
4. Books, films, TV series and podcasts get a consistent type. First
   they are consistently `product`; later they get a `work` type.
5. Works on every existing summary without re-summarizing, through the
   same backfill path as the other entity repairs.
6. The section has a measurable contract: parse rate and grounding rate
   per episode, in the eval.

## Non-goals

- **Summary mentions as a source.** Unlike [#83](83-summary-entity-mentions-pipeline.md),
  nothing here stores a mention whose position is in the summary. Every
  row this spec writes points at a transcript segment. #83 stays
  independent and still gated on its own trigger.
- **Reading other sections.** Takeaways, quotes and blog ideas also name
  things, but in prose. The Resource List is the one section whose only
  job is to name things.
- **Frontend work in Phases 0–3.** Once the entity exists, #82 links it.
  The `work` type in Phase 4 needs UI, and that phase lists it.
- **Linking things that have no Wikidata item.** "Archaeological
  Awareness Playing Cards" grounds, gets mentions and goes through the
  linker. When the linker says `none`, it ends up `unresolvable` like
  any other name.

## Design

### Where it plugs in

```text
SUMMARIZE ──► summary.md + citations sidecar          (prompt contract, Stage 1)
    │
EXTRACT_ENTITIES  (handle_extract_entities, task_handlers.py:1025)
    ├─ GLiNER over transcript                          unchanged
    ├─ anchor scan (hosts/guests)                      unchanged
    └─ resource seeds                                  new
         parse section 8 ──► ground in transcript ──► scan as seeds
    │
RESOLVE_ENTITIES  (resolve_pending_mentions, :1188)
    └─ build_link_context + resource hints (kind, gloss)  new
```

There is no new stage, queue row or table. The only schema change is
one boolean column on the #81 decision cache (Stage 5). The new code goes in
`thestill/core/summary_resources.py` (parser and grounding), plus small
hooks in the two handlers and the chooser prompt.

### Stage 1 — The contract

Replace the section 8 instruction with a fixed line shape. Add section 8
to the few-shot example, which is the only reliable way to hold the
format:

```text
## 8. 📚 Resource List
* **<Name>** (<kind>): <one-line gloss> [mm:ss]
```

Rules given to the model:

- **One thing per bullet.** Never "A / B" or "A & B". Two things are two
  bullets.
- **Name as it is known**, in its full proper form ("Paul Kedrosky", not
  "Kedrosky"). Titles carry no author and no italics. The author is a
  separate bullet if they matter.
- **Kind** comes from a closed list: `book`, `film`, `tv`, `podcast`,
  `article`, `paper`, `person`, `company`, `product`, `tool`, `place`,
  `event`, `other`.
- **Timestamp of the first substantive mention.**
- **Exclude the episode's own hosts and guests.** They are anchors
  already, and listing them is noise to the reader.
- Only things **mentioned in this episode**. Nothing recommended "for
  further reading" that nobody said.

This is a prompt change only. It affects new and re-run summaries. The
citation rewrite (#54) still turns `[mm:ss]` into a cite link.

### Stage 2 — Parse

`parse_resource_list(markdown) -> list[ResourceItem]`, with
`ResourceItem(name, kind, gloss, cite_id, raw_label)`. The parser is
deterministic, pure and fixture-tested. It is lenient by design,
because the backfill reads every existing free-form summary:

- Finds every `## 8.` section. Chunked summaries
  ([post_processor.py:299](../thestill/core/post_processor.py#L299))
  repeat all nine sections once per chunk, so items are unioned and
  deduplicated by case-folded name. The first cite wins.
- Accepts the contract shape, the current `**Name:** gloss [ts]` shape
  and `Name (Kind) [ts]`.
- **Name cleanup:**
  - strip markdown emphasis and quotes;
  - cut a trailing `by <Author>`, and emit the author as a `person`
    item with the same cite;
  - drop a trailing parenthetical that is not a known kind and use it as
    the gloss.
- **Kind:** a free-form kind ("TV Show", "Spending Data/Tool", "Book")
  maps onto the closed list through a small synonym table. Anything
  unmapped is `other`.
- **Splitting combined items:** "A / B", "A & B" and "A, B" split only
  when every side starts with a capital letter and is at most five
  words. This splits "Vercel / GitHub" but leaves "Bed, Bath & Beyond"
  intact.
- **Cite ids:** read from the `?cite=cN` link. A bare `[mm:ss]` that is
  not a cite link is kept as `raw_label`.

### Stage 3 — Grounding

Grounding decides whether an item is real. For each item:

1. **Variants:** the name, the name without a leading "The", and, for
   `person` items, the variants that `expand_anchor_variants`
   ([entity_anchor.py:42](../thestill/core/entity_anchor.py#L42))
   already builds for host and guest names. Reusing that function keeps
   the surname rule the same in both places.
2. **Locate the cited segment.** Look up `cite_id` in the citations
   sidecar ([summary_citations.py:228](../thestill/core/summary_citations.py#L228)).
   If there is no sidecar entry, fall back to `raw_label` through
   `parse_timestamp_label`.
3. **Search** the cleaned transcript, word-bounded and
   case-insensitive for multi-word variants. Single-token variants are
   case-sensitive, the same rule #82 uses for "Warp" and "warp".
4. **Classify the outcome:**
   - `near`: found within `RESOURCE_GROUNDING_WINDOW_S` (default 90 s)
     of the cited segment;
   - `elsewhere`: found in the transcript, but not near the citation;
   - `ungrounded`: not found at all.

`near` and `elsewhere` items become seeds. `ungrounded` items are
dropped and counted. The measured verbatim rate (76%) means roughly one
item in four is dropped today, before the contract improves names.

`elsewhere` is kept deliberately. The citation is an LLM's timestamp:
Mad Men cited at 12:09 is actually discussed elsewhere. A name the
transcript contains is still evidence. The outcome is logged so the
eval can track citation accuracy separately.

### Stage 4 — Seeding

Seeds go into `EntityExtractor.extract()` next to `anchor_variants`, as
a new `resource_seeds` argument. The extractor scans for them with the
same machinery as `_scan_anchors`
([entity_extractor.py:424](../thestill/core/entity_extractor.py#L424)).
The differences are in what it writes:

| | Anchor scan | Resource seed scan |
|---|---|---|
| Entity known up front | yes, pre-resolved (`ANCHOR`) | no, mentions are `pending` |
| `extractor` | `anchor:scan` | `summary:resource` |
| `surface_label` | the anchor's type | from the item's kind (below) |
| `confidence` | 1.0 | 0.9 (above the inline-highlight floor) |

Two lessons from earlier incidents carry over:

- **Never re-read a span GLiNER already found** (#297). If a seed
  occurrence overlaps a GLiNER mention, the GLiNER row stays. It is not
  duplicated, but the seed's hint (Stage 5) still applies to it at
  resolve time.
- **A seed never pre-resolves.** The Galloway namesake incident (fixed
  in #261 and #271) came from a name match that decided identity. Here a name match only
  proposes mentions. Identity is the linker's decision, made with
  context.

Kind → `surface_label` (Phases 1–2):

| Kind | `surface_label` |
|---|---|
| `person` | `person` |
| `company` | `company` |
| `product`, `tool`, `book`, `film`, `tv`, `podcast`, `article`, `paper` | `product` |
| `place`, `event`, `other` | `topic` |

The surface label is only the linker's fallback type. The P31 rules
(Stage 6) decide the final type when a QID is found.

### Stage 5 — Hints to the linker

`build_link_context`
([task_handlers.py:1286](../thestill/core/task_handlers.py#L1286))
re-parses the summary. The parse is pure and cheap, and redoing it avoids
a new column for carrying the hint. It adds
`resource_hints: {casefolded name variant: (kind, gloss)}` to the
context.

For a name that has a hint, the chooser's user message
([chooser.py:239](../thestill/core/entity_linking/chooser.py#L239))
gains one line after `tagged as`:

```text
the episode summary lists it as: <kind> — <gloss>
```

The gloss is LLM output that came from transcript text, so it is
sanitised with `sanitize_text` and fenced with `wrap_untrusted` like
every other excerpt. The hint reaches GLiNER-found mentions of the same
name too, which is where most of its value is. "Claude: Mentioned as a
catalyst for the 2023 AI boom" settles a name that GLiNER found but
could not place.

Cached decisions (#81 Stage 4) are reused as they are. A cached `none`
is the exception: one made **without** a hint is decided once more when
a hint exists. The hint is new evidence, and the cost is bounded at
about 5 names per episode. The decision row records `hinted=true`, so a
later run does not repeat the call. That is one nullable boolean column
on `entity_link_decisions`, added by an alembic migration with the SQLite
equivalent.

### Stage 6 — Types

**Phase 0 (standalone fix):**

- Remove `Q11424` from `TOPIC_P31` and add the actual film genre,
  Q201658.
- Add a `WORK_P31` set: film Q11424, TV series Q5398426, book Q571,
  literary work Q7725634, podcast Q24634210, album Q482994, video game
  Q7889. Matching that set returns `PRODUCT` **regardless of the
  fallback**, checked before `TOPIC_P31`.
- Re-bucket existing rows with the existing `backfill-entity-types`
  ([cli.py:3369](../thestill/cli.py#L3369)), after a `--dry-run` report.
  Ids keep their prefix. The #296 lookup fallback already serves
  `topic:margin-call` typed as `product`.

**Phase 4 (separate decision): a `work` type.** Phase 0 makes works
consistent, but as products they sit next to software and devices in
the rail's product section. A `work` type would cover:

- `EntityType.WORK` and the `work:` id prefix;
- the Postgres `CHECK (type IN …)` constraint
  ([postgres_schema.py:403](../thestill/repositories/postgres_schema.py#L403))
  through an alembic migration, plus the SQLite rebuild;
- `WORK_P31` returning `WORK` instead of `PRODUCT`;
- the `work` kinds mapping to a `work` surface label;
- a GLiNER `work` label ("book, film, TV show or podcast"), measured in
  the #81 eval before enabling it, since a fifth label shifts the other
  four;
- the rail, the filter bar and `entityColors`;
- MCP `entity_type` enums (`get_entity`, `find_mentions`) and API types;
- a re-type pass over the Phase 0 products, using the same command.

Phase 4 is listed so its scope is known, not because Phases 0–3 depend
on it.

### Measuring

`summary_checks.py` gains a `resource_list` check, reported per episode
and aggregated by the #53 runner:

- `items`: parsed items;
- `contract_rate`: share of bullets in the contract shape (new summaries
  only);
- `grounding_rate`: `(near + elsewhere) / items`;
- `citation_accuracy`: `near / (near + elsewhere)`;
- `linked_rate`: share of grounded items whose name maps to a resolved
  episode entity after `resolve-entities`.

The same numbers are logged once per episode at INFO
(`resource_seeds_grounded`, with `episode_id` and the counts), so the
prod baseline does not depend on an eval run.

## Failure handling

The Resource List is an addition, never a gate. The transcript
extraction commits regardless of what happens to seeds.

- **No summary, or no section 8:** skip, log `resource_seeds_skipped`
  with the reason. This is normal for unsummarised episodes, so it is
  INFO, not WARNING.
- **Parse error:** raised inside the parser, caught by the handler,
  logged at WARNING with `episode_id`, and no seeds. Never an empty
  result that looks like "no resources" (failure-mode catalogue:
  errors-as-empty-results).
- **No citations sidecar** (no annotated transcript): ground on
  `raw_label`. If that is also missing, use `elsewhere`-only grounding.
- **The summary changed since extraction:** the summary sha in the
  citations sidecar is recorded in the `resource_seeds_grounded` log.
  A re-summarised episode re-runs `extract-entities` through the normal
  chain, and seed mentions are replaced with the episode's other
  pending mentions. They are ordinary rows of that episode, so the
  existing re-extract deletes them.
- **Hostile summary text:** names and glosses are sanitised before they
  touch the database or a prompt. Grounding stops a summary from
  inventing an entity. Sanitising stops it from injecting one.

## Cost

- **Parse and grounding:** regex over the summary and the transcript,
  well under 100 ms per episode. No model load.
- **Extra mentions:** about 5 items per episode, with a few occurrences
  each. Roughly 10–20 extra rows, most of which GLiNER missed.
- **Linking:**
  - new names go through #81's normal per-name path;
  - names that already have a cached decision cost nothing;
  - each re-decided hinted `none` costs one chooser slot in the
    existing batched call.

  Expect a few extra Wikidata searches per episode and no extra LLM
  call in the common case.

## Testing

- **Parser:** fixtures taken from real summaries, covering every shape
  in §Problem: contract, `**Name:** gloss`, `Name (Kind)`, combined
  items, "by Author", italics, repeated chunk sections, missing
  timestamps. A malformed section raises; it never returns `[]`.
- **Grounding:** `near`, `elsewhere` and `ungrounded` on a synthetic
  transcript; case rules; person surname variants through
  `expand_anchor_variants`; the cite → segment path and the
  `raw_label` fallback.
- **Extractor:** seed occurrences become `pending` mentions with
  `extractor="summary:resource"`; a span already found by GLiNER is not
  duplicated; anchors still win over seeds on the same span.
- **Linker:** the hint line appears for hinted names (GLiNER and seed
  mentions alike) and is wrapped and sanitised; a cached `none` without
  a hint is re-decided once and then remembered as `hinted`.
- **Type rules:** film, TV series and book with every fallback →
  `PRODUCT`; film genre → `TOPIC`; existing person, company and topic
  cases unchanged.
- **Eval:** `resource_list` metrics on the 20 pinned `entity-linking`
  episodes (#81) before and after, with the #81 regression and precision
  numbers as the gate. Seed mentions must not lower link precision.

## Phases

- **Phase 0 — Type fix.** `Q11424` out of `TOPIC_P31`; `WORK_P31` → `PRODUCT`;
  tests; `backfill-entity-types --dry-run` report, then apply. Ships on
  its own.
- **Phase 1 — Parser, grounding and the measuring check.** Run
  `thestill eval` over the pinned set and a recent prod sample to record
  the baseline `grounding_rate` and `linked_rate` before anything
  writes mentions.
- **Phase 2 — Seeds and hints** behind `ENTITY_RESOURCE_SEEDS_ENABLED` (default
  `false`), gated on the #81 eval. Then enable it and backfill
  `extract-entities` for recent episodes, newest first, with the same
  batching as the #81 pending-mention backfill.
- **Phase 3 — Prompt contract.** New summaries only. Compare `contract_rate`
  and `grounding_rate` before and after on the same episodes. This can
  ship in parallel with Phase 1, because the parser handles both
  shapes.
- **Phase 4 — `work` type** (Stage 6). A separate go/no-go after Phase 2's
  numbers.

## Open questions

- **Should a grounded resource count toward salience?** The summarizer
  chose to list it, which is a signal of importance. Proposed: not in
  v1, the same answer #83 gives for summary mentions. Revisit once
  `linked_rate` is known.
- **Should `elsewhere` items be seeded?** The case for keeping them is
  above. If `citation_accuracy` turns out to be low, the problem is
  citations, and #54's resolver is where to fix it.
- **Are the other prose sections worth mining for hints?** Key
  Takeaways name things with context too. Not proposed; a hint from the
  Resource List is precise because the section has no other job.
- **Should the hosts-and-guests exclusion be enforced in code too?**
  The prompt may not always follow it. Dropping items that match an
  episode anchor is cheap, and anchors already produce better mentions.
  Proposed: yes, in the parser's caller.
