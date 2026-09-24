# Summary Entity Links Specification

> **Status:** 📝 Draft — solution designed, not yet scheduled
> **Created:** 2026-09-24
> **Updated:** 2026-09-24
> **Priority:** Medium — the transcript has entity peeks (#251, #252); the summary, which most readers see first, has none
> **Author:** Product & Engineering
> **Related:** [#28 corpus-search-and-entities](28-corpus-search-and-entities.md), [#45 entity-page-enrichment](45-entity-page-enrichment.md), [#54 summary-segment-citations](54-summary-segment-citations.md), [#76 episode-detail-page-hierarchy](76-episode-detail-page-hierarchy.md), [#83 summary-entity-mentions-pipeline](83-summary-entity-mentions-pipeline.md) (follow-up)

---

## Executive Summary

The transcript tab links every recognised entity: a name in the text, and
since PR #252 the speaker label, opens a *peek* — type, gloss, photo,
host/guest badge, other episodes, a way to the entity page. The summary
tab, the first thing a reader sees, has none of this. "Claire Vo
interviews Zach Lloyd, CEO of Warp" is inert text.

This spec adds the same links to the summary **without touching the
pipeline**: the reader already holds this episode's resolved entities,
with the names actually spoken. Match those names in the summary's text
and wrap each match in the existing `EntityHighlight`. A match borrows the
nearest citation's transcript segment, so the peek's ▶ and "Show in
transcript" land on the passage the sentence came from.

1. **Terms** — canonical names plus the surface forms of this episode's
   mentions, longest first, word-bounded.
2. **Rehype plugin** — splits markdown text nodes around the matches;
   headings, code and existing links (the citation buttons) are left
   alone.
3. **Peek** — the summary variant of `EntityHighlight`: same card, with
   "Show in transcript" in place of prev/next.

**Key principle:** only entities already resolved for *this episode* are
candidates. The summary never links a name the transcript did not
establish, so precision is that of the transcript's own extraction, and a
summary that invents a name links nothing. Frontend only; works on every
existing summary the moment it ships. [#83](83-summary-entity-mentions-pipeline.md)
is the pipeline-side follow-up for the cases this design cannot cover.

---

## Problem

- **The summary is where readers start** ([#76](76-episode-detail-page-hierarchy.md)
  puts it first), and it names the same people, companies and products
  the transcript does, but none of them are actionable. A reader who
  wants to know who Zach Lloyd is must switch to the transcript and find a
  mention — or, for a host who is never named in the text, the speaker
  label ([#252](https://github.com/ssarunic/thestill/pull/252)).
- **The data to do it already exists client-side.** `EpisodeReader`
  fetches the episode's entities once
  ([EpisodeReader.tsx:288](../thestill/web/frontend/src/components/EpisodeReader.tsx#L288))
  and hands them to the transcript viewer; the summary viewer gets only
  the markdown and the citations
  ([EpisodeReader.tsx:663](../thestill/web/frontend/src/components/EpisodeReader.tsx#L663)).
- **The summary already has one kind of live link.** `SummaryViewer`
  overrides react-markdown's `a` renderer to turn `?t=…&cite=cN` into
  citation buttons
  ([SummaryViewer.tsx](../thestill/web/frontend/src/components/SummaryViewer.tsx)).
  Entity links are the second kind, and citations are also the bridge
  that gives an entity match a transcript position ([#54](54-summary-segment-citations.md)).
- **The pipeline cannot help quickly.** The summarizer runs before
  `extract-entities` in the chain
  ([task_handlers.py:1063](../thestill/core/task_handlers.py#L1063)), so
  entity ids do not exist when the summary is written; storing summary
  mentions server-side is a schema and backfill project ([#83](83-summary-entity-mentions-pipeline.md)).

## Goals

1. Every name in the summary that resolves to one of the episode's
   entities is an `EntityHighlight` with the same peek as in the
   transcript: badge, gloss, photo, name link, other episodes.
2. From the peek, "Show in transcript" switches tab and scrolls to the
   passage the sentence cites, with Back returning to the summary — the
   existing citation path.
3. Zero pipeline changes, zero new endpoints, zero migration. Existing
   summaries gain links on deploy.
4. No false links: a word that is not the entity ("warp" the verb) is
   never wrapped.
5. The `E` highlight toggle and the entity-type filter (spec #28 §5.2)
   apply to the summary as they do to the transcript.

## Non-goals

- Linking names the transcript did not resolve (the summary may name a
  company the extractor missed). That is [#83](83-summary-entity-mentions-pipeline.md).
- Counting summary matches as mentions anywhere: `mention_count`,
  salience, the rail, `[`/`]` navigation and MCP tools are unchanged.
- Narration and briefing markdown. The plugin is written so they can adopt
  it later; wiring them is out of scope.
- Wikidata-level disambiguation in the summary. The candidate set is the
  episode's entities, already disambiguated.

## Design

### Where it plugs in

```text
EpisodeReader
  ├─ entities (useEpisodeEntities)        ── already here
  ├─ SummaryViewer  ←── + entities, + onShowInTranscript
  │    └─ ReactMarkdown
  │         ├─ rehypePlugins: [rehypeEntityMentions(terms)]   ← new
  │         └─ components.span: EntityHighlight when data-entity-id  ← new
  └─ SegmentedTranscriptViewer            ── unchanged
```

`SummaryViewer` gains two props: `entities: EpisodeEntity[]` and
`onShowInTranscript(segmentId, seconds)`. Everything else is inside the
`episode-entities/` folder.

### Module layout

| File | Role |
|---|---|
| `episode-entities/entityTerms.ts` (new) | Build the term index from `EpisodeEntity[]`: `{ term, entityId, caseSensitive }[]`, longest first |
| `episode-entities/rehypeEntityMentions.ts` (new) | Rehype plugin: walks `text` nodes, emits `span[data-entity-id][data-term]` around matches, skips excluded ancestors |
| `episode-entities/SummaryEntityMention.tsx` (new) | The `span` renderer: synthesises a `MentionLite`, renders `EntityHighlight variant="summary"` |
| `episode-entities/EntityHighlight.tsx` | `variant` gains `'summary'` (styling + which peek actions show) |
| `episode-entities/EntityHoverCard.tsx` | `onShowInTranscript` prop; prev/next hidden when set |
| `components/SummaryViewer.tsx` | Accept `entities`, register the plugin and the `span` renderer |
| `components/EpisodeReader.tsx` | Pass `entities` and a handler built on `handleSummaryCitation` |

### Stage 1 — Terms

From each `EpisodeEntity`:

- `entity.canonical_name`, and
- every distinct `surface_form` among its mentions with
  `confidence ≥ INLINE_HIGHLIGHT_CONFIDENCE_FLOOR`
  ([entityColors.ts:92](../thestill/web/frontend/src/utils/entityColors.ts#L92)),
  excluding `speaking` mentions (their surface form is the speaker
  label, already covered by the canonical name).

Rules:

- Minimum three characters; drop terms that are a single common English
  word (a small stoplist: `the`, `warp`-class collisions are handled by
  the case rule below, not by the list).
- Longest term first, so "Andrej Karpathy" wins over "Karpathy" at the
  same position — the same greedy rule as `buildSpans` in
  [applyHighlights.tsx](../thestill/web/frontend/src/components/episode-entities/applyHighlights.tsx).
- Word boundaries on both sides (Unicode-aware: `(?<![\p{L}\p{N}])` /
  `(?![\p{L}\p{N}])`), so "Warp" never matches "Warped".
- **Case**: multi-word terms match case-insensitively; single-token terms
  match case-sensitively. "Warp" links, "warp speed" does not. Acronyms
  (`MCP`, `PR`) are single tokens and therefore exact-case only.
- One term maps to one entity. If two entities share a term (two people
  called "Alex"), the term is dropped — the summary has no context to
  pick, and a wrong link is worse than none.

The index is built once per entity list with `useMemo`; the summary is a
few kilobytes, so a linear scan per text node is fine.

### Stage 2 — The rehype plugin

`rehypeEntityMentions(terms)` visits every hast `text` node whose ancestor
chain contains none of: `a`, `code`, `pre`, `h1`–`h6`. For each node it
runs the term scan and, when there are matches, replaces the node with a
sequence of `text` and `element{tagName:'span', properties:{'data-entity-id',
'data-term'}}` nodes. Nothing else in the tree is touched, so the
citation buttons, blockquotes and lists render exactly as today.

Why rehype and not a custom `p`/`li` renderer: text is nested arbitrarily
(`li > p > strong > text`), and a renderer-level approach would need to
recurse into children of every element type. A tree pass is the honest
tool, and it is what remark-gfm already is.

### Stage 3 — The mention behind the peek

`EntityHoverCard` and `EntityHighlight` take a `MentionLite`. A summary
match synthesises one:

| Field | Value |
|---|---|
| `id` | `0` (the serializer's own fallback; never used as a key) |
| `entity_id` | from the term index |
| `segment_id`, `start_ms` | from the **nearest citation in the same block**: the closest `?cite=` link at or before the match inside the enclosing `p`/`li`, resolved through the citations sidecar — `segment_id_hint` and `cited_playback_s` ([types.ts `SummaryCitation`](../thestill/web/frontend/src/api/types.ts)). Blocks without a citation fall back to the entity's `first_mention_ms` and the segment of its first transcript mention |
| `role` | `'summary'` — a frontend-only marker (never persisted) so the card can tell the context |
| `surface_form` | the matched text |
| `confidence` | `1` |

The citation lookup happens in `SummaryEntityMention` (the renderer), not
the plugin: the plugin stays a pure text transform, and the renderer has
the citations map `SummaryViewer` already builds (`citationById`).

### Stage 4 — The peek in the summary

`EntityHighlight variant="summary"`:

- Same anchor id scheme with a `:summary` suffix
  (`m=<entity>:<segment>:summary`), so a summary match never collides
  with a transcript anchor and `[`/`]` navigation (which reads transcript
  anchors) is unaffected.
- Styling: the inline underline in the entity's colour, as in the
  transcript, inside `prose` (so `not-prose` on the anchor to keep the
  entity colour rather than the prose link colour).
- The card shows the badge, gloss, photo, name link and "Also mentioned
  on" as today. **Prev/next are replaced by "Show in transcript"**: it
  calls `onShowInTranscript(segment_id, start_ms/1000)`, which
  `EpisodeReader` implements with the citation path
  ([EpisodeReader.tsx:474](../thestill/web/frontend/src/components/EpisodeReader.tsx#L474)):
  switch to the transcript tab with a pushed history entry, scroll to the
  segment, no seek. The ▶ button seeks, as everywhere.
- Hover on desktop, tap → bottom sheet on phones, Esc/scroll/outside-click
  semantics: inherited from PR #251.

### Highlight toggle and type filter

`SummaryViewer` receives `entityHighlightsEnabled` and `hiddenEntityTypes`
(the reader already keeps both for the transcript). Disabled → the plugin
is not registered and the markdown renders as today. Hidden types are
dropped from the term index.

### Failure handling

- No entities (older episode, extraction pending): no plugin, no change.
- Citations missing or unresolved for a block: the mention falls back to
  the first transcript mention; "Show in transcript" still works, it just
  lands on the first mention rather than the cited passage.
- A term that matches inside a citation label cannot happen: `a` is
  excluded.
- Markdown that puts a name in a heading ("## Zach Lloyd on factories")
  is intentionally not linked; headings are navigation, not prose.

## Cost and capacity

None server-side. Client: one term index per entity list (≤ ~100 terms),
one regex pass per text node (~50 nodes). Sub-millisecond on a phone; the
summary re-renders only when the markdown, entities or toggle change.

## Testing

- `entityTerms.test.ts`: longest-first order; boundary rules ("Warp" vs
  "Warped"); case rules ("warp" not linked, "zach lloyd" linked); shared
  term dropped; `speaking` surface forms excluded; hidden types dropped.
- `rehypeEntityMentions.test.ts`: splits a text node into text/span/text;
  leaves `a`, `code`, headings untouched; nested `li > p > strong`;
  no double-wrap when a term appears twice.
- `SummaryViewer.test.tsx`: given entities and citations, a name renders
  as an `EntityHighlight` with the summary anchor; the synthesised
  mention takes the nearest earlier citation's segment; a block without a
  citation falls back to the first mention; "Show in transcript" calls the
  handler with that segment; citation buttons still render and still
  fire `onCite`.
- `EntityHighlight.test.tsx`: the `summary` variant hides prev/next and
  shows "Show in transcript".
- Existing transcript tests are unaffected (no shared code changes beyond
  the new variant).

## Phases

One PR, frontend only:

1. `entityTerms` + `rehypeEntityMentions` with unit tests.
2. `SummaryEntityMention` + the `summary` variant of the peek.
3. Wire `SummaryViewer` / `EpisodeReader`; toggle and filter.
4. Check on the Warp episode (How I AI): "Claire Vo", "Zach Lloyd",
   "Warp", "Figma" link; "warp" in lower case does not; "Show in
   transcript" from the Drama section lands on the cited round.

## Alternatives considered

- **Custom `p`/`li`/`strong` renderers instead of a rehype plugin.** Needs
  every text-bearing element enumerated and recursed; the tree pass is
  simpler and cannot miss a nesting.
- **Linking against the whole corpus' entities** (a name→entity map from
  the API). Higher recall, far lower precision — "Alex" alone has dozens
  of candidates — and a network round-trip the episode-scoped set avoids.
- **Asking the summarizer to emit entity links.** Impossible in the
  current chain order (entities do not exist yet at summarize time), and
  fragile even if reordered: the LLM would have to reproduce ids exactly.
- **Storing summary mentions server-side.** Right long-term answer for
  recall and MCP; too much machinery for the first cut. See
  [#83](83-summary-entity-mentions-pipeline.md).

## Open questions / follow-ups

- Should a summary match that has no citation in its block show "Show in
  transcript" at all, or only ▶? Proposed: show it, landing on the first
  mention, since Back is cheap.
- Whether to link inside blockquotes (the summarizer's quotes section).
  Proposed: yes; quotes name speakers.
- Narration and briefing markdown can register the same plugin once they
  have an entity list per item; not planned here.
