# Follow a Show from Its Episode Page

> **Status:** 🚧 Implemented on `feat/117-follow-from-episode-page` (2026-11-17); pending PR + merge
> **Created:** 2026-11-17
> **Updated:** 2026-11-17
> **Priority:** High. Every signup in the week to 2026-11-14 came from an
> episode page, and activation (spec #111) has been 0 of 11, 0 of 10 and
> 1 of 8 for the last three graded signup weeks. Following a show is the
> step that starts delivery, and the episode page has no way to do it.
> **Related:** [#102 sign-in-page](102-sign-in-page.md) (pending follow
> across sign-in), [#111 product-health-metrics](111-product-health-metrics.md)
> (activation, experiments log), [#95 product-analytics](95-product-analytics.md)
> (`follow` event `source`), [#88 import-outcomes](88-import-outcomes-and-arriving-soon.md)
> (imported episodes and their parent show), [#76 episode-detail-page-hierarchy](76-episode-detail-page-hierarchy.md)
> (episode header).

---

## Overview

A reader on an episode of a show they don't follow has to notice the
show name, open the show page and press Follow there. The episode page
itself never offers it, except for one case: since v1.31.0 a
first-follow panel appears for readers who follow nothing yet
(experiment 1 in [docs/experiments.md](../docs/experiments.md)). It
hasn't moved activation: 0 of 10 and 1 of 8 in the two signup weeks
graded since it shipped. And a reader who already follows one show never
sees it.

This spec puts a **Follow** button beside the show name in the episode
header, for every reader who doesn't follow that show, signed in or not.
The click is explained after the fact, in a confirmation toast that says
what Follow does ("new episodes arrive summarised in your inbox and
briefing"), with **Undo** beside the button. Signed out, the same button goes through
sign-in and comes back to the episode with the follow done, reusing the
spec #102 pending follow. The first-follow panel is retired; the inline
button replaces it.

Follows made here carry their own analytics `source`, so the change can
be read as experiment 2 against activation cohorts.

## Goals

1. A signed-in reader on an episode whose show they don't follow sees a
   Follow button beside the show name in the episode header, on the
   episode page and in the inbox reader, on phone and desktop.
2. Following shows a toast that says what happened and what it means,
   and an Undo beside the button (built: the plan had Undo in the toast;
   see Outcome); Undo unfollows.
3. A reader who already follows the show sees no follow control in the
   header (except "Following" right after following, while the reader
   stays on that show's episodes).
4. Imported episodes with a real parent show offer Follow for that show;
   bare audio imports (the synthetic `audio-imports` parent) never do.
5. A signed-out visitor on a public episode page sees the same Follow
   beside the show name; it signs them in and returns them to the
   episode, where the follow completes with the same toast.
6. Follows carry `source=episode_page` (signed in) or
   `source=episode_pending_follow` (finished after sign-in from a public
   episode page) on the spec #95 `follow` event.
7. The first-follow panel (`FollowPrompt`) is removed.

## Non-goals

- **Follow Guest** (follow a person, get every episode they appear in).
  It belongs on the People rail and the person's page, not the show name,
  and it needs entity-quality work first: inbox rows are never removed
  automatically, so a wrong guest match (a namesake, an ad voice, an
  unnamed `SPEAKER_n`) would put an episode in an inbox for good. Only
  Wikidata-linked people with a host or guest role should ever drive
  delivery. Separate spec.
- Unfollow from the episode page. Unfollow stays on the show page; the
  Undo right after following covers a mistaken click.
- An end-of-summary follow card. Considered for later, after this
  change has been read, so the two can be told apart.
- Showing how often a show publishes in the toast ("about 5 episodes a
  week"). Useful, but needs a cadence computation; later.
- Hover cards or popovers (see Alternatives).

## Background findings

- **Episode header.** The show name is the `identity` slot of
  `PageHero`: a `Link` to the show with its artwork and a chevron
  ([EpisodeHeader.tsx:117-128](../thestill/web/frontend/src/components/episode-header/EpisodeHeader.tsx#L117-L128)).
  The same header renders on the episode page and in the inbox reader
  overlay, through `EpisodeReader`.
- **First-follow panel.** `FollowPrompt` renders for feed episodes only
  (`origin === 'feed'`), when the reader's follow count is 0 and the
  panel hasn't been dismissed, above the tabs from `sm` up and inside the
  tab panel on phones
  ([EpisodeReader.tsx:346-357](../thestill/web/frontend/src/components/EpisodeReader.tsx#L346-L357),
  [FollowPrompt.tsx](../thestill/web/frontend/src/components/FollowPrompt.tsx)).
  Its follows carry `source=episode_prompt`.
- **Episode payload.** `GET /api/podcasts/{slug}/episodes/{slug}` returns
  `podcast_slug`, `podcast_title` and `origin`, but not whether the caller
  follows the show
  ([api_podcasts.py:334-414](../thestill/web/routes/api_podcasts.py#L334-L414)).
  The show page computes `is_following` with
  `state.follower_repository.exists(user.id, podcast_id)`
  ([api_podcasts.py:226](../thestill/web/routes/api_podcasts.py#L226)).
- **Imports.** Apple, YouTube, Spotify and RSS-episode imports attach to
  a real parent show (`upsert_auto_added_podcast`); bare audio URLs fall
  back to the synthetic parent `SYNTHETIC_AUDIO_IMPORTS_ID`, which has no
  follow target
  ([import_service.py:1081-1106](../thestill/services/import_service.py#L1081-L1106),
  [sqlite_podcast_repository.py:87-88](../thestill/repositories/sqlite_podcast_repository.py#L87-L88)).
- **Following.** `useFollowPodcast` posts
  `POST /api/podcasts/{slug}/follow?source=` and invalidates `podcasts`
  and `dashboard` ([useApi.ts:195-205](../thestill/web/frontend/src/hooks/useApi.ts#L195-L205)).
  `useUnfollowPodcast` is its mirror
  ([useApi.ts:268-278](../thestill/web/frontend/src/hooks/useApi.ts#L268-L278)).
  `FollowButton` already has idle / pending / done / error states
  ([FollowButton.tsx](../thestill/web/frontend/src/components/FollowButton.tsx)).
- **Analytics source.** The server maps `source` through
  `follow_source()`; unknown values become `other`
  ([analytics_sink.py:47-54](../thestill/services/analytics_sink.py#L47-L54)).
  The client type is `FollowSource`
  ([types.ts:660](../thestill/web/frontend/src/api/types.ts#L660)).
  Documented in [docs/analytics.md:82](../docs/analytics.md#L82).
- **Pending follow (spec #102).** `/login` stores a pending follow only
  when `intent=follow` and `next` is a show page (`showSlugFromNext`), and
  only the show page consumes it, with `source=pending_follow`
  ([signIn.ts:47-52](../thestill/web/frontend/src/utils/signIn.ts#L47-L52),
  [Login.tsx:85-86](../thestill/web/frontend/src/pages/Login.tsx#L85-L86),
  [PodcastDetail.tsx:76-89](../thestill/web/frontend/src/pages/PodcastDetail.tsx#L76-L89)).
  The record is `{slug, at}`, valid for 15 minutes, set by a click on
  `/login` and never by a URL alone.
- **Public episode page.** The show name is a link in the hero eyebrow;
  the page has sign-in buttons for the summary and transcript but none
  for following
  ([Episode.tsx:107-112](../thestill/web/frontend/src/pages/public/Episode.tsx#L107-L112)).
  `useSignIn(intent, next)` builds the `/login#next=…&intent=…` link
  ([helpers.ts:14-17](../thestill/web/frontend/src/components/public/helpers.ts#L14-L17)).
- **Toasts** take a message and a type and disappear after 5 seconds;
  there is no action button
  ([Toast.tsx](../thestill/web/frontend/src/components/Toast.tsx)).
- **Inbox rows are never removed automatically.** A follow can deliver
  recent episodes to the inbox (the PR #407 / #486 backfill cap); Undo unfollows
  but leaves any rows already delivered.

## Clarifications

Answered by the operator on 2026-11-17.

1. **How does the inline button live with the first-follow panel?**
   Retire the panel. The inline button and the explanatory toast replace
   it, so there is one Follow on the page.
   *Why:* the panel has not moved activation since it shipped, and two
   controls for the same action compete.
2. **What does the header show when the reader already follows the
   show?** Nothing. Right after following it shows "Following ✓" until
   the reader leaves that episode (built: until the reader moves to
   another show, see Outcome).
   *Why:* nearly every inbox episode is from a followed show; a permanent
   label on each one is noise.
3. **Do imported episodes offer Follow for their parent show?** Yes, when
   the parent is a real show; never for bare audio imports.
   *Why:* importing one episode is strong intent to hear more of the
   show.
4. **Where does the signed-out Follow go?** Beside the show name at the
   top of the public episode page, the same place as signed in. No extra
   card further down.

Decided while writing the spec (not asked):

- **Analytics sources.** `episode_page` for the inline button and
  `episode_pending_follow` for a follow finished after sign-in from a
  public episode page, so episode-page follows can be told apart from the
  show page's `show_page` and `pending_follow`. `episode_prompt` stays in
  the server's list so past events keep their meaning.
- **Toast copy.** "Following *Show*. New episodes will arrive summarised
  in your inbox and briefing." with **Undo**, shown for 8 seconds (5 is
  too short to read and act on).

## Execution plan

### Phase A: payload and analytics sources (backend)

| # | File | Change |
|---|---|---|
| A1 | [api_podcasts.py](../thestill/web/routes/api_podcasts.py) | Episode response gains `podcast_is_following` (`follower_repository.exists(user.id, podcast.id)`) and `podcast_followable` (`podcast.id != SYNTHETIC_AUDIO_IMPORTS_ID`) |
| A2 | [analytics_sink.py](../thestill/services/analytics_sink.py) | `FollowSource` gains `episode_page` and `episode_pending_follow` |
| A3 | tests | Episode response carries both fields (followed, not followed, synthetic parent); `follow_source` keeps the new values |

**Gate:** the touched route and analytics tests pass; ruff clean.

### Phase B: inline Follow for signed-in readers

| # | File | Change |
|---|---|---|
| B1 | [Toast.tsx](../thestill/web/frontend/src/components/Toast.tsx) | `showToast(message, type, options?)` with optional `action: {label, onClick}` (clicking runs it and closes the toast) and `durationMs` |
| B2 | `components/episode-header/EpisodeFollow.tsx` (new) | Renders nothing unless followable and not followed. Follow → `FollowButton` pending → "Following ✓" (local state, keyed by show slug so it doesn't carry over to another episode's show) + toast with Undo. Undo → unfollow, button back to Follow. Error → toast and `Retry`. Invalidates the episode queries of that show so their headers update |
| B3 | [EpisodeHeader.tsx](../thestill/web/frontend/src/components/episode-header/EpisodeHeader.tsx) | `identity` becomes the show link plus `EpisodeFollow` in one row; the link truncates, the button never wraps |
| B4 | [EpisodeReader.tsx](../thestill/web/frontend/src/components/EpisodeReader.tsx) | Remove the `FollowPrompt` placement |
| B5 | `FollowPrompt.tsx` + test | Delete; drop `episode_prompt` from the client `FollowSource`, add the two new values; remove any hook left unused |
| B6 | [types.ts](../thestill/web/frontend/src/api/types.ts) | Episode type gains `podcast_is_following`, `podcast_followable` |
| B7 | tests | `EpisodeFollow`: hidden when followed or not followable; follow sends `episode_page`, shows Following + toast; Undo unfollows; error path. Toast action test. Reader tests no longer expect the panel |

**Gate:** vitest for the touched components, `tsc --noEmit`, eslint on
touched files.

### Phase C: signed-out Follow through sign-in

| # | File | Change |
|---|---|---|
| C1 | [signIn.ts](../thestill/web/frontend/src/utils/signIn.ts) | `showSlugFromNext` becomes `followSlugFromNext`: the show slug from a show page or an episode page `next` |
| C2 | [Login.tsx](../thestill/web/frontend/src/pages/Login.tsx) | Uses `followSlugFromNext` |
| C3 | [Episode.tsx](../thestill/web/frontend/src/pages/public/Episode.tsx) | Follow button beside the show name in the hero eyebrow, linking to `useSignIn('follow', episodeHref)`, `rel="nofollow"` |
| C4 | `EpisodeFollow.tsx` | On first load of an episode whose show isn't followed, consume the pending follow for that show; if present, follow with `episode_pending_follow` and show the same toast (once, ref-guarded like the show page) |
| C5 | tests | `followSlugFromNext` for show, episode and other paths; Login stores the pending follow for an episode `next`; public episode Follow link; `EpisodeFollow` completes a pending follow |

**Gate:** vitest for the touched files; `npm run build`.

### Phase D: docs and experiment log

| # | File | Change |
|---|---|---|
| D1 | [docs/analytics.md](../docs/analytics.md) | `follow` sources: add `episode_page`, `episode_pending_follow`; note `episode_prompt` is historical |
| D2 | [docs/experiments.md](../docs/experiments.md) | Experiment 1: fill in shipped (v1.31.0, 2026-10-26) and the result (0 of 10, 1 of 8), decision iterate. Add experiment 2: this spec, activation, same guardrails, expected 0–13% → ≥30% |
| D3 | [specs/README.md](README.md) | Index row |

**Gate:** markdownlint clean on the touched docs (no line starting with
`#` followed by a number).

### Verification before PR

- Full frontend suite (`npm test -- --run`) and the backend tests for
  `api_podcasts` and analytics.
- In a browser against a local server: follow and Undo from the episode
  page and the inbox reader on desktop and phone width; signed out, Follow
  → sign in → back on the episode, following.

## Alternatives considered

- **Hover card on the show name** (the first idea). Hidden on the one
  thing it's meant to fix: nobody hovers a show name looking for an
  action, phones have no hover, and opening it on tap clashes with the
  name being a link to the show. A preview card may still suit people
  (Follow Guest), where the card is mostly information.
- **Follow in the action row** (beside Play and the ⋯ menu). Competes with
  Play, the page's one dominant action, and separates Follow from the show
  it applies to.
- **Keep the first-follow panel alongside the inline button.** Two
  controls for one action; the panel hasn't moved activation. Rejected by
  the operator.
- **Explain before the click** (a line under the button). Readers skip
  text before an action; a confirmation that lists what just changed gets
  read, and Undo makes the click safe to try.

## Files touched

**Modified:** [api_podcasts.py](../thestill/web/routes/api_podcasts.py),
[analytics_sink.py](../thestill/services/analytics_sink.py),
[Toast.tsx](../thestill/web/frontend/src/components/Toast.tsx),
[EpisodeHeader.tsx](../thestill/web/frontend/src/components/episode-header/EpisodeHeader.tsx),
[EpisodeReader.tsx](../thestill/web/frontend/src/components/EpisodeReader.tsx),
[types.ts](../thestill/web/frontend/src/api/types.ts),
[signIn.ts](../thestill/web/frontend/src/utils/signIn.ts),
[Login.tsx](../thestill/web/frontend/src/pages/Login.tsx),
[Episode.tsx](../thestill/web/frontend/src/pages/public/Episode.tsx),
[docs/analytics.md](../docs/analytics.md),
[docs/experiments.md](../docs/experiments.md), [README.md](README.md),
and their tests.

**Created:** `components/episode-header/EpisodeFollow.tsx` and its test.

**Deleted:** `components/FollowPrompt.tsx` and its test.

## Open questions

- **Does the public episode's server-rendered fallback need the button?**
  No: the spec #105 fallback only has to be a subset of the page, and a
  sign-in link adds nothing for crawlers. Confirmed in Phase C by leaving
  `public_prerender.py` untouched and running its tests.
- **Can a reader follow an auto-added import parent that is still
  loading its feed?** Expected yes (it is a normal podcast row); checked
  in Phase A against `follow_by_slug`.

## Rollback plan

Revert the PR. No migration, no stored data changes. Follow events
already sent with the new sources stay in PostHog under those names;
`follow_source` maps them to `other` if the values are removed.

## Outcome

Built on `feat/117-follow-from-episode-page` on 2026-11-17, phases A–D
as planned, plus e2e coverage.

### Deviations from the plan

- **Phone toasts are inset from both edges.** At 320 px the confirmation
  (the longest toast in the app) ran off the left edge, because
  `max-w-sm` is wider than the screen. The toast container now spans the
  width on phones (16 px insets) and passes taps through outside each
  toast; desktop is unchanged.
- **Undo sits beside "Following", not in the toast.** Codex (PR #527)
  found that the inbox reader is a modal dialog that keeps Tab inside its
  panel, while toasts render outside it: a toast's Undo was out of
  keyboard and screen-reader reach there, and gone after 8 s. Undo is now
  a button in the header row (`Undo following *Show*` for screen
  readers), focus moves Follow → Undo → Follow after each click, and it
  stays while the reader is on that show. The toast only explains, and
  toasts lost the action support added for it.
- **Toasts are announced** (`aria-live="polite"`).
- **Follow and Undo use `mutateAsync`, not `mutate` callbacks.** A
  pre-PR review found that TanStack Query drops per-call callbacks once
  the component unmounts, so a follow finishing after the reader closed
  the overlay never confirmed or refreshed. Awaiting the promise reports
  and refreshes either way; a test covers it.
- **"Following" lasts per show, not per episode.** The confirmation is
  keyed by show slug, so it stays on other episodes of the same show in
  the reader until it moves to another show. Consistent with the refetched
  state (the show is followed), so kept.
- The pending-follow effect carries an eslint suppression for
  `react-hooks/set-state-in-effect`: state is set only after the awaited
  request, which the rule cannot see.

### Tests added

- Backend: the episode response's `podcast_is_following` and
  `podcast_followable` (followed, not followed, synthetic parent); the new
  `follow` sources.
- Unit: `EpisodeFollow` (follow, Undo and where focus goes, a follow
  finishing after unmount, failures, pending follow once without moving
  focus, no carry-over between shows), toast duration, `followSlugFromNext`, `/login` remembering an episode's show,
  the public episode Follow link.
- E2E (hermetic): Follow on the show row at 393 and 320 px with the tabs
  in the first viewport; Follow, toast and the row's Undo at 320 px; the public
  episode's Follow through `/login` and back.

**Not yet verified:** the full signed-out round trip with a real Google
sign-in (needs prod or a multi-user local server with OAuth), and the
experiment 2 read (three weeks after shipping).

**Follow-ups:** Follow Guest (separate spec, after entity quality);
a publishing-cadence line in the toast.
