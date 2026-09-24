# Summary Entity Mentions in the Pipeline

> **Status:** 💡 Proposal — follow-up to [#82](82-summary-entity-links.md); not scheduled
> **Created:** 2026-09-24
> **Updated:** 2026-09-24
> **Priority:** Low until #82 ships and its gaps are measured
> **Author:** Product & Engineering
> **Related:** [#82 summary-entity-links](82-summary-entity-links.md), [#28 corpus-search-and-entities](28-corpus-search-and-entities.md), [#54 summary-segment-citations](54-summary-segment-citations.md), [#81 live-wikidata-entity-linking](81-live-wikidata-entity-linking.md), [#78 remote-mcp-access](78-remote-mcp-access.md)

---

## Executive Summary

[#82](82-summary-entity-links.md) links entity names in the summary by
matching, in the browser, against the entities the transcript already
resolved. That is the right first cut and it needs no data. It has three
limits by construction:

1. A name the summary uses but the transcript extraction did not resolve
   never links.
2. The links exist only in one React component. MCP tools, briefings,
   narration and any future consumer cannot ask "which entities does this
   summary mention?".
3. Matching is lexical. Two same-named entities are dropped rather than
   disambiguated, and a name spelled differently in the summary ("Lloyd")
   is missed.

This proposal moves summary mentions into the pipeline: **run the existing
extractor and resolver over the summary markdown**, store the results as
mentions with `source = 'summary'` and character offsets, and let the
frontend render from stored offsets instead of re-matching. The
transcript path is unchanged; summary mentions are a second, clearly
labelled source that defaults out of every existing count.

Not to be built until #82 has shipped and the size of its gaps is known —
the measure is in §Trigger.

---

## Trigger

Build this when, after #82, a sample of episodes shows either:

- ≥ 10% of proper names in summaries not linked because the transcript
  extraction lacks the entity (measure with the extractor run over 50
  summaries offline against the stored entity sets), or
- a concrete consumer needs summary mentions outside the reader (an MCP
  `find_mentions(source="summary")` request, briefing item entities).

Until then #82 is sufficient.

## Goals

1. Every entity mention in a summary is a row the whole system can read,
   with the same resolution quality as transcript mentions (anchors,
   overrides, blacklist, the live linker of #81).
2. The reader renders summary links from stored offsets — no client-side
   matching, so #82's `rehypeEntityMentions` becomes a thin "wrap these
   ranges" pass.
3. Summary mentions are invisible to existing counts unless asked for:
   `mention_count`, salience, the rail, `[`/`]`, cooccurrence, related
   rails and search chunks stay transcript-only.
4. Invalidation is automatic: a re-summarised episode re-extracts.

## Non-goals

- Changing what the summarizer writes. The LLM keeps producing plain
  markdown; this stage reads it afterwards, exactly as #54's citation
  resolver does.
- Extracting from narration or briefing scripts. Same mechanism could
  apply later; separate decision.
- Replacing #82's client fallback immediately. It stays for episodes not
  yet backfilled (`source='summary'` rows absent → match client-side).

## Design

### Where it plugs in

The chain today (spec #28 §1, [task_handlers.py:1063](../thestill/core/task_handlers.py#L1063)):

```text
SUMMARIZE → EXTRACT_ENTITIES → RESOLVE_ENTITIES → REINDEX → COMPUTE_RELATED → ENRICH_ENTITIES
```

`EXTRACT_ENTITIES` ([task_handlers.py:1023](../thestill/core/task_handlers.py#L1023))
reads the cleaned-transcript JSON and calls
`EntityExtractor.extract()` ([entity_extractor.py:198](../thestill/core/entity_extractor.py#L198)).
This proposal adds a second input to the same stage: the summary markdown
for the episode ([summary_artifacts.py](../thestill/core/summary_artifacts.py)),
converted to plain-text blocks. No new stage, no new queue row; the
summary is a few hundred words and adds a fraction of a second to a stage
that already runs GLiNER over the whole transcript.

`RESOLVE_ENTITIES` then resolves summary mentions with the transcript's:
same names, same episode anchors, same cached decisions
([#81 §Stage 4](81-live-wikidata-entity-linking.md)), so in the common
case a summary mention costs no Wikidata or LLM call at all — the name
was decided for the transcript minutes earlier.

### Schema

`entity_mentions` gains three nullable columns (alembic migration, both
backends):

| Column | Type | Meaning |
|---|---|---|
| `source` | `text NOT NULL DEFAULT 'transcript'` | `transcript` \| `summary` |
| `char_start`, `char_end` | `bigint` | Offsets into the **plain-text block** (see below), summary only |
| `block_key` | `text` | Which summary block: `p:<n>` / `li:<n>` in document order, summary only |
| `summary_sha256` | `text` | The summary these offsets are valid for (same digest as the citations sidecar, [summary_citations.py:137](../thestill/core/summary_citations.py#L137)) |

Existing rows get `source='transcript'` by the default. `segment_id`,
`start_ms`, `end_ms` on a summary mention carry the **nearest citation's**
segment and time (resolved through the citations sidecar), or NULL when
the block has no citation; `speaker` is NULL.

Indexes: extend `idx_mentions_episode` to `(episode_id, source)`; the
partial indexes on `entity_id`/`role` need no change.

### Offsets that survive rendering

Markdown offsets are fragile (the renderer changes them). The stage
therefore tokenises the summary into blocks the same way the frontend's
hast tree does — one block per paragraph, list item, blockquote
paragraph; headings and code skipped — and stores offsets into each
block's **plain text** (markdown syntax stripped, same rules as the
rehype pass). The frontend rebuilds identical block keys while walking the
hast tree, so `block_key` + offsets address the same characters. A
mismatch (the summary changed) is caught by `summary_sha256`.

### API

`GET /api/episodes/{id}/entities` gains an optional
`summary_mentions: MentionLite[]` (with `block_key`, `char_start`,
`char_end`) when rows with `source='summary'` exist for the current
summary digest. `EpisodeEntity.mentions` and `mention_count` remain
transcript-only.

`find_mentions` (MCP, [entity_tools.py](../thestill/mcp/entity_tools.py))
gains `source: transcript | summary | any` (default `transcript`, so no
existing caller changes behaviour). `list_quotes_by` and
`get_episode_clip` are transcript-only by nature and ignore summary rows.

### Frontend

`rehypeEntityMentions` from #82 gets a second mode: given
`summary_mentions`, wrap exactly those ranges (matched by `block_key` and
offsets) and skip lexical matching. When the response has no
`summary_mentions` (not backfilled, or the digest moved), it falls back
to the matching of #82. The peek is the same.

### Invalidation and backfill

- A re-run of `summarize` changes the digest; the next
  `extract-entities` deletes `source='summary'` rows for the episode and
  re-extracts. Same trigger as the citations sidecar re-resolve.
- Backfill: `thestill entities extract --summaries-only [--podcast-id]`,
  enqueueing `EXTRACT_ENTITIES` with a `summary_only` flag so the
  transcript is not re-run. Order by most recent first; the reader's
  fallback covers the rest meanwhile.

### Failure handling

- No summary yet: skip, log `summary_mentions_skipped`, transcript
  extraction proceeds.
- Extractor error on the summary: log and continue; the transcript result
  is committed regardless (the summary is an addition, never a gate —
  spec #42's "no silent degradation" applies: the skip is a WARNING with
  the episode id).
- Citations missing for a block: mention stored with NULL segment; the
  peek's "Show in transcript" falls back to the first transcript mention
  as in #82.

## Cost

- Compute: GLiNER on ~400 words per episode, ≈ 1–2% of the transcript
  pass. No new model load.
- Resolution: cache hits for names the transcript already decided; only
  summary-only names reach Wikidata/LLM (#81 pricing applies, expected a
  handful per episode).
- Storage: ~15–40 rows per episode.

## Testing

- Extractor: summary blocks tokenised with stable keys; offsets point at
  the surface form in the block text; headings/code skipped.
- Handler: summary rows written with `source='summary'` and the digest;
  re-summarise → old rows removed; transcript failure paths unchanged.
- Repository (SQLite and Postgres): `find_mentions(source=…)` filter; the
  `(episode_id, source)` index used.
- API: `summary_mentions` present only for the current digest;
  `mention_count` unchanged by summary rows.
- Frontend: offset mode wraps exactly the given ranges; digest mismatch
  → lexical fallback.
- Eval: extend the `entity-linking` rubric ([#53](53-eval-runs-and-summary-rubric.md))
  with a summary slice on the same 20 pinned episodes before enabling the
  backfill.

## Phases

1. Migration + model (`source`, offsets, digest) and repository filters;
   default keeps everything transcript-only. Tests on both backends.
2. Extractor block tokeniser + handler integration behind
   `ENTITY_SUMMARY_MENTIONS_ENABLED` (default `false`).
3. API field + MCP `source` parameter + frontend offset mode.
4. Eval slice, then enable by default and backfill.

## Open questions

- Should summary mentions ever feed salience? A name the summarizer chose
  to mention is a strong signal of importance. Proposed: not in v1;
  revisit with data.
- Block tokenisation must be byte-identical between Python and the hast
  walk. Alternative: have the frontend send its block texts to the API
  for matching — simpler contract, but a round-trip per render. Proposed:
  shared, fixture-tested tokeniser rules; the digest guards drift.
- Whether briefings ([#36](36-per-user-digest-from-inbox.md)) want the same for
  their items; if so the `source` enum grows rather than a new table.
