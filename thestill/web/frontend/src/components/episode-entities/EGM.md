# components/episode-entities

## Overview

Entity surfaces of the episode reader (spec #28 inline highlights, spec #82 summary entity links): inline transcript highlights with a peek card / bottom sheet, the right-rail entity index, the key-entities strip, the filter bar, and the rehype plugin plus term index that link entity names inside rendered summary markdown. All matching is client-side over the episode's `EpisodeEntity` list.

## Interface

- `EntityHighlight` (default), `EntityHighlightProps` — inline / speaker / index / summary mention that opens a peek (hover card on desktop, sheet on phones) [src/components/episode-entities/EntityHighlight.tsx:EntityHighlight]
- `EntityHoverCard` (default) — peek body: entity summary, guest episodes or recent mentions, prev/next mention, "Open entity page" [src/components/episode-entities/EntityHoverCard.tsx:EntityHoverCard]
- `EntityPeekSheet` (default) — phone bottom sheet wrapper for the peek [src/components/episode-entities/EntityPeekSheet.tsx:EntityPeekSheet]
- `EntityRail` (default) — right-rail entity index ("In this episode", "People mentioned", companies, …) plus related episodes; from `lg` up it also carries the type toggles when `hiddenTypes` / `onToggleType` are passed [src/components/episode-entities/EntityRail.tsx:EntityRail]
- `RailScrollFrame` (default) — pinned, height-capped rail container with bottom fade [src/components/episode-entities/RailScrollFrame.tsx:RailScrollFrame]
- `KeyEntitiesStrip` (default) — above-the-fold top-entity pills with the type toggles; the reader shows it below `lg` only [src/components/episode-entities/KeyEntitiesStrip.tsx:KeyEntitiesStrip]
- `EntityTypeToggles` (default) — the P / C / Pr / T buttons that hide a whole entity type from the text highlights [src/components/episode-entities/EntityTypeToggles.tsx:EntityTypeToggles]
- `EntityFilterBar` (default) — multi-select chips: show only segments mentioning the selected entities [src/components/episode-entities/EntityFilterBar.tsx:EntityFilterBar]
- `SummaryEntityMention` (default) — renders the rehype spans in a summary as `EntityHighlight` [src/components/episode-entities/SummaryEntityMention.tsx:SummaryEntityMention]
- `applyEntityHighlights`, `highlightMatches`, `findSpeakerMention`, `SegmentMentionSet`, `SpeakerMention` — place entity anchors and search `<mark>`s inside segment text [src/components/episode-entities/applyHighlights.tsx:applyEntityHighlights]
- `buildEntityTerms`, `buildEntityTermIndex`, `matchEntityTerms`, `EntityTerm*` — summary term index [src/components/episode-entities/entityTerms.ts:buildEntityTermIndex]
- `rehypeEntityMentions` — rehype plugin wrapping term matches in `span[data-entity-id][data-term][data-cite-id]` [src/components/episode-entities/rehypeEntityMentions.ts:rehypeEntityMentions]
- `mentionPermalinkHash`, `speakerAnchorId`, `summaryAnchorId`, `findMentionAnchor`, `isSpeakingMention` — `#m=<entity_id>:<segment_id>` anchors [src/components/episode-entities/mentionPermalink.ts:mentionPermalinkHash]
- `synthesizeIndexMention`, `NO_SEGMENT` — synthetic mention behind index (rail / strip) peeks [src/components/episode-entities/indexMention.ts:synthesizeIndexMention]; `synthesizeSummaryMention` — the one behind summary peeks [src/components/episode-entities/summaryMention.ts:synthesizeSummaryMention]

## Invariants

- A mention never navigates on a plain click; only the peek's "Open entity page" router link leaves the transcript [src/components/episode-entities/EntityHighlight.tsx:EntityHighlight]
- Clicks and keys inside the portalled peek stop React propagation so they never seek playback in the owning segment [src/components/episode-entities/EntityHighlight.tsx:stopReactPropagation]
- The entity summary is fetched only after a hover has settled (one Postgres query per entity) [src/components/episode-entities/EntityHighlight.tsx:hoverSettled]
- Mentions below `INLINE_HIGHLIGHT_CONFIDENCE_FLOOR` (0.5) are not highlighted or indexed [src/components/episode-entities/applyHighlights.tsx:applyEntityHighlights]
- Mentions carry no character offsets; spans are located by case-insensitive `surface_form` search, non-overlapping, longest wins [src/components/episode-entities/applyHighlights.tsx:applyEntityHighlights]
- Summary terms: ≥ 3 characters; phrases case-insensitive; single tokens case-sensitive and must contain an uppercase letter; a term claimed by two entities is dropped; `speaking` mentions contribute nothing [src/components/episode-entities/entityTerms.ts:buildEntityTerms]
- Term matching uses Unicode word boundaries on both sides ("Warp" never matches "Warped") [src/components/episode-entities/entityTerms.ts:buildEntityTermIndex]
- Inside summary blocks, text under links, code or headings is never matched [src/components/episode-entities/rehypeEntityMentions.ts:UNMATCHED_TAGS]
- `role: 'index'` and `role: 'summary'` are frontend-only markers, never persisted; an index mention with `segment_id` = `NO_SEGMENT` (-1) must not offer a jump [src/components/episode-entities/indexMention.ts:NO_SEGMENT]
- Speaker and summary anchors use distinct ids so they never collide with the in-text permalink anchor [src/components/episode-entities/mentionPermalink.ts:speakerAnchorId]
- The rail shows `DEFAULT_VISIBLE_COUNT` (8) per section; the full bucket is still delivered and revealed by "Show all" [src/components/episode-entities/EntityRail.tsx:DEFAULT_VISIBLE_COUNT]
- People who take part (`takes_part`, or for a payload without it `speaker_kind !== 'unknown'`) go in "In this episode", ordered guest, host, recurring, never folded and shown without a mention count; other people go in "People mentioned" [src/components/episode-entities/EntityRail.tsx:takesPart]
- The rail always lists every entity type (it is the index); the type toggles only change text highlights [src/components/episode-entities/EntityTypeToggles.tsx:EntityTypeToggles]

## Side Effects

- `GET /api/entities/{type}/{id}` via `useEntitySummary` while a peek is open [src/components/episode-entities/EntityHoverCard.tsx:EntityHoverCard]
- Document-level `keydown`, `mousedown` and capture `scroll` listeners plus settle/close timers while a peek is open [src/components/episode-entities/EntityHighlight.tsx:EntityHighlight]
- Body scroll lock, swipe-to-close and a Tab focus trap on the phone sheet [src/components/episode-entities/EntityPeekSheet.tsx:EntityPeekSheet]
- `findMentionAnchor` reads the DOM by anchor id [src/components/episode-entities/mentionPermalink.ts:findMentionAnchor]

## Verification

- Run: `cd thestill/web/frontend && npm run test:run && npm run lint`
