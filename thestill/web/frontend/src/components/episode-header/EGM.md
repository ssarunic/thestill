# components/episode-header

## Overview

The episode page's hero (spec #76): artwork, one dominant play action, the show row, description, People chips and the Information list. Presentational — the episode reader owns the data and the player and passes callbacks in.

## Interface

- `EpisodeHeader` (default) — hero, show row with an optional `follow` slot, action row (play, optional Watch video icon, `EpisodeMoreMenu` which holds Share, and the `inboxAction`) and description [src/components/episode-header/EpisodeHeader.tsx:EpisodeHeader]
- `EpisodeFollow` (default) — spec #117 Follow beside the show name, with "Following" + Undo after a follow made here [src/components/episode-header/EpisodeFollow.tsx:EpisodeFollow]
- `EpisodeHeaderSkeleton` — loading skeleton matching the loaded anatomy [src/components/episode-header/EpisodeHeader.tsx:EpisodeHeaderSkeleton]
- `People` (default) — host/guest/speaker chips; entity chips link, speaker chips call `onSpeakerSelect` [src/components/episode-header/People.tsx:People]
- `buildPeople`, `normalizePersonName`, `PersonChip` — merge person entities with transcript speaker labels [src/components/episode-header/buildPeople.ts:buildPeople]
- `buildEpisodeInformationRows` — rows for the Information `DefinitionList` (type, length, published, source, platform links) [src/components/episode-header/episodeInformation.tsx:buildEpisodeInformationRows]

## Invariants

- Name de-duplication is trim + collapse whitespace + case-fold, nothing else; alias merging belongs to the entity index [src/components/episode-header/buildPeople.ts:normalizePersonName]
- Entity people first (person type tagged host/guest, by salience, capped at `MAX_ENTITY_PEOPLE` = 8), then uncovered speaker labels [src/components/episode-header/buildPeople.ts:buildPeople]
- Placeholder labels (`SPEAKER_n`, unknown) and role-only labels (ad narrator, archival clip, …) never become chips [src/components/episode-header/buildPeople.ts:ROLE_ONLY_SPEAKER]
- Only `content` / `filler` segments feed the speaker colour map, so chip colours match the transcript viewer's [src/components/episode-header/buildPeople.ts:buildPeople]
- No pipeline state above the fold; "Reprocess…" (admin) and "Edit transcript" (`transcript.edit`) appear only when the parent passes their callbacks [src/components/episode-header/EpisodeHeader.tsx:EpisodeHeaderProps]
- Unknown platform links render nothing rather than a bare id [src/components/episode-header/episodeInformation.tsx:listenOn]
- The play button uses `busy` while `isLoading`: spinner in place of the icon, still pressable so a second press cancels the wait [src/components/episode-header/EpisodeHeader.tsx:EpisodeHeader]
- `EpisodeFollow` renders nothing when the show is not `followable` (the synthetic parent of bare audio imports) or already followed, unless the follow was made here; its local state is keyed by slug so it never carries over to another show [src/components/episode-header/EpisodeFollow.tsx:EpisodeFollow]
- Undo lives in the header, not the toast: toasts render outside the reader overlay's modal focus trap [src/components/episode-header/EpisodeFollow.tsx:EpisodeFollow]
- A pending follow stored on `/login` (spec #117) is consumed once per show and completed with source `episode_pending_follow` only when the show is followable and not yet followed; a click follows with `episode_page` [src/components/episode-header/EpisodeFollow.tsx:EpisodeFollow]

## Side Effects

- `EpisodeHeader`: none directly; play, mark-played, reprocess and edit actions are delegated to callbacks supplied by the reader [src/components/episode-header/EpisodeHeader.tsx:EpisodeHeaderProps]
- `EpisodeFollow`: `POST|DELETE /api/podcasts/{slug}/follow` via `useFollowPodcast` / `useUnfollowPodcast`, invalidates the show's three-part `['episodes', slug, …]` episode queries, toasts (the follow confirmation stays 8 s), reads and clears `sessionStorage` `thestill.pendingFollow` [src/components/episode-header/EpisodeFollow.tsx:EpisodeFollow]

## Verification

- Run: `cd thestill/web/frontend && npm run test:run && npm run lint`
