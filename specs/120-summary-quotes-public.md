# Summary Quotes on Public Pages

> **Status:** 🚧 Phase 1 implemented on `feat/120-summary-quotes-public` (2026-11-24); pending PR + merge. Phase 2 (person and entity pages) proposed
> **Created:** 2026-11-24
> **Updated:** 2026-11-24
> **Priority:** High. The public episode page is the first page most readers see (spec #111), and its quote list is mostly sentence fragments with a link to YouTube on every line.
> **Author:** Product & Engineering
> **Related:** [94-public-pages.md](94-public-pages.md) (the page and its "no anonymous playback" non-goal), [100-quote-ranking-and-featured-quotes.md](100-quote-ranking-and-featured-quotes.md) (the quote gate this replaces on the episode page), [114-quote-cards-and-clips.md](114-quote-cards-and-clips.md) (short links, Rule A), [54-summary-segment-citations.md](54-summary-segment-citations.md) (the citations sidecar), [116-timestamp-drift-alignment.md](116-timestamp-drift-alignment.md) (why playback stays signed-in), [105-server-rendered-public-pages.md](105-server-rendered-public-pages.md) (the server-written copy), [102-sign-in-page.md](102-sign-in-page.md) (sign-in intents)

---

## Executive summary

The public episode page lists "Quotes from this episode". They are
transcript excerpts that spec #94's stand-in gate let through: the first
sentence of a turn, or about 200 characters around a name. Most of them
read as fragments. Every one of them ends in "Hear it on YouTube", so the
page's main call to action on each quote sends the reader to another site.

The summary already has a better list. Its **Best Quotes** section is 5-7
lines the summarizer chose because they "actually land", each with a
speaker and a timestamp citation into the transcript. This spec makes those
the episode page's quotes, after two checks: the words must be in the
transcript, and the person the summary names must be the one speaking
there. Each quote's action becomes **Play from mm:ss**. For a signed-out
visitor it signs in and lands in the signed-in reader at that moment, where
the player applies drift correction. Public pages still never play audio.

## Problem and evidence

All-In, "CrowdStrike CEO: AI Cyberattack Wave…" (2026-11-24). The public
page served 57 quotes in transcript order. The first three:

- "As they're running to Costco to get toilet paper and running to the bank
  and getting $100 bills."
- "We had the FAA outage, which to me seemed like an IT thing and upgrading
  to fibre. We'll discuss that. Obviously, Chase, United, Apple."
- "So when you saw that Hugging Face as somebody whose job it is to service
  essentially Fortune 500 and beyond."

The same episode's summary, Best Quotes:

- "We're minting more adversaries and you're democratising destruction."
  (George Kurtz, 10:17)
- "The bill's come due now for all the tech debt that's built up over the
  last twenty-five, thirty years." (23:03)
- "Why break in when you can log in?" (43:30)

Each public quote linked to `youtube.com/watch?v=…&t=…` as "Hear it on
YouTube" ([ui.tsx](../thestill/web/frontend/src/components/public/ui.tsx),
before this change). The header's Listen-on row already credits the
publisher's platforms; a link on every quote makes leaving the main action.

## Current state (before this spec)

- **Episode quotes** came from `public_quote_queries().quotes(episode_id=…)`
  ([public_quotes.py](../thestill/repositories/public_quotes.py)): a resolved
  NER mention in a segment whose speaking row resolves to a person, at least
  60 characters, not in the first 90 s, no sponsor wording. That is spec
  #100's read-time stand-in; its candidates table and score were never built.
- **Best Quotes** are slot 5 of every summary profile
  ([summary_profiles.py](../thestill/core/summary_profiles.py)):
  `* "Quote text..." - Speaker [timestamp]`. Citation resolution (spec #54)
  rewrites each label to `[mm:ss](?t=…&cite=cN)` and stores a sidecar that
  maps `cN` to a transcript segment.
- **Spec #94 holds Best Quotes behind sign-in** with the rest of the summary
  below the key takeaways.
- **Spec #114 short links** are minted for every public quote shown, keyed by
  episode, start and words, and re-anchored by their words after an entity
  refresh (`reconcile_episode`). The signed-in transcript's share icons
  (Rule A) offer only quotes that are already public.

## Goals

| # | Goal | Check |
|---|------|-------|
| G1 | The episode page's quotes are the summary's Best Quotes | integration test, prerender test |
| G2 | No quote whose words are not in the transcript, or whose speaker is not the named person, reaches a public page | unit tests per rejection reason; §2 measurement |
| G3 | No quote links off thestill; "Play from" signs in and plays in the signed-in reader | Vitest (card and page), prerender test |
| G4 | Links shared before keep working: `/q/<id>` and plain `#t=` | quote-link and public API tests |
| G5 | The share icons in the signed-in transcript offer the same quotes (Rule A) | reader shares test |

## Non-goals

- **Anonymous playback.** Seeking to a summary timestamp in a listener's copy
  of the audio needs the spec #116 timeline map, which only the signed-in
  player has. Public pages keep spec #94's rule.
- **Changing the summarizer prompt.** The quotes are checked as they come.
  A prompt that asks for verbatim words and full names is Phase 3, judged by
  the §2 rejection counts.
- **Person and entity pages** (Phase 2): they still list transcript excerpts.
- **Translated summaries.** Only the canonical summary's quotes are used; a
  translation's quotes are not the words that were said.

## Design

### 1. Source

[summary_quotes.py](../thestill/core/summary_quotes.py) parses the first
section whose heading names quotes (`parse_best_quotes`): every bullet of the
form `"text" - Speaker [label](?t=…&cite=cN)`, with straight or curly quotes,
any dash, optional bold, and an optional link. It is pure: the service
[public_summary_quotes.py](../thestill/services/public_summary_quotes.py)
loads the canonical summary, the effective transcript (spec #110 edits
applied), the valid citations, and each segment's resolved speaker, then
calls `verify_quotes`.

### 2. The words are in the transcript

`locate` looks around the cited segment (one before, two after): the label
can fall mid-quote, and a quote can run into the next turn. The quote is split
at each ellipsis, and bracketed editorial insertions ("[to play soccer]") are
left out. Each fragment of three or more words must have at least 85% of its
words found in order (`difflib` matching blocks), and so must the quote as a
whole. Verbatim quotes score 1.0, a tidied word or two about 0.9, paraphrases
well under 0.7.

When the cited segment fails, or the words sit in a cold open, the quote's
longest fragment (five words or more) is searched word for word across the
whole transcript (`TranscriptIndex`), and the check runs again from each hit,
looking no earlier than the hit. Content segments win: an intro that previews
a line is never the line.

Words are compared in any script: letters and digits of every alphabet,
with accents folded, and scripts written without spaces (Han, kana, Hangul,
Thai) split into single characters so a quote matches inside a longer run.

Measured on 299 local episodes (the SQLite copy from 2026-07, its summaries
and transcripts), 1,493 Best Quotes:

| Outcome | Quotes | Share |
|---------|-------:|------:|
| Kept | 1,070 | 72% |
| Not in the transcript (paraphrased or misplaced beyond reach) | 161 | 11% |
| Speaker disagrees with the summary | 120 | 8% |
| Speaker has no resolved person | 85 | 6% |
| The words span two speakers' turns (§3) | 37 | 2% |
| No citation and not found by search | 18 | 1% |
| In an intro, ad or outro | 2 | 0% |

Per episode: 81% keep three or more quotes and 6% keep none. Checking takes
about 10 ms per episode once the transcript is loaded. A random sample of 14
kept quotes and 14 low-coverage ones (0.85-0.95) read correctly against
their segments, with the right speaker. The `speaker_mismatch` rejections
are mostly the summarizer attributing a line to the person quoted rather
than the person speaking ("Benjamin Franklin", read by Tom Holland). Those
are the cases this check exists for.

### 3. The speaker is who the summary says

Every segment that holds at least two of the quote's matched words is part of
the quote (a single stray "the" in a neighbouring turn is not). Each of them
must have a resolved `speaking` row for the same person, who is not
`public_hidden` (`segment_speakers_sql` in
[public_quotes.py](../thestill/repositories/public_quotes.py)). `names_match`
accepts the entity's full name, a single first name or surname of three
letters or more, or the full name with one extra word ("Dr. Jane Smith"). It
ignores a parenthetical ("Jason (host)"). It never accepts attributions that
name no one or several people: "Anonymous …", "Speaker 4", "A & B", "host".
A disagreement drops the quote; it is never shown under the transcript's
name either, because the summarizer may know something the diarization got
wrong.

Words that run from one speaker's turn into another's are dropped too
(`speakers_differ`; Codex review on PR #561). In the local sample, about half
are true stitches: a host's question joined to the guest's answer ("Moore's
Law has been dead for years"). The other half are diarization moving a
sentence's last words into the next turn ("I'm like Mr. | Pringles").
Accepting a minority share of the words in another turn would recover the
second kind, but it reopens the first, so it stays out until the cost is
measured on prod. The page shows the entity's name, linked to their person page.

### 4. Where "Play from" starts

At the start of the segment where the words start. Inside a long turn, the
start is estimated from the share of the segment's words before the quote,
less two seconds, and never earlier than the segment's start. Times are
playback times (transcript time plus `playback_time_offset_seconds`), the
same as summary citation chips.

### 5. Play from, through sign-in

`QuoteCard`'s `play` prop replaces the timestamp with **Play from mm:ss**
(`playFromPath` in
[ui.tsx](../thestill/web/frontend/src/components/public/ui.tsx)): a
`rel="nofollow"` link to
`/login#next=/podcasts/<show>/episodes/<ep>?view=transcript&t=<s>&intent=play`.
`next` is the same form `returnPath` already uses for the transcript sign-in.
After Google sign-in the reader opens on the transcript at that line. A cold
load has no user gesture, so `useDeepLinkSeek` offers "Play from" there and
the player plays with its drift map. The login page gets the copy "Sign in to
listen from this moment, with the transcript alongside." The YouTube link is
gone from quotes. The header's Listen-on row is unchanged.

The server-written page (spec #105) renders the same link text and href
([public_prerender.py](../thestill/web/public_prerender.py), `_quote(…, play=True)`).

### 6. Short links

Summary quotes are minted through the same `QuoteShareService.mint` with
`source = 'summary'` (the column existed with default `'gated'`; no
migration). Two consequences:

- **Re-anchoring skips them.** `reconcile_episode` finds a gated share by its
  words among mention excerpts. A summary quote's words are the summary's, so
  that search would find nothing and strip the name. Instead, every build of
  the episode's quotes re-mints them, and the upsert refreshes the cached
  speaker and segment. A quote that no longer passes the checks stops being
  shown. Its link keeps resolving to the name it had until it is revoked
  (`thestill quote-share revoke`) or the speaker is hidden (410 either way).
- **A person page's excerpt of the same turn has its own link.** Different
  words and start, so a different id. Phase 2 removes the excerpts.

### 7. Links shared before

- `/q/<id>` is a stored row and resolves as before. The page shows the shared
  words as the linked quote.
- A plain `#t=` from before this spec names a moment, not a summary quote.
  When no summary quote starts within 5 s, the payload answers `linked` with
  the transcript excerpt at that time, built on demand from the old query,
  cached separately, never listed and never minted (`_episode_moments`).

### 8. Rule A

`GET /api/podcasts/{slug}/episodes/{ep}/quote-shares` (spec #114, signed-in
transcript share icons) now returns the episode's summary quotes with their
segment and short link, and nothing when the episode is not public. Sharing
from the transcript still makes nothing public that was not already.

### 9. Payload shape

Unchanged except: `about` is `null` on episode quotes (a summary quote is
about the episode), and the page size is 8 so the server-written page and
the client agree on one page (the summarizer writes at most seven). The
episode quote cache is keyed by `summary_path` too, so a re-summarize shows
new quotes once the cache entry expires.

## Phases

### Phase 1: episode page (implemented)

Everything in §1-§9.

### Phase 2: person and entity pages (proposed)

The person page's "said" stream and featured quote, the entity page's
"about" stream, "Who's talking", "Who said what" and the sitemap's indexing
counts (spec #105 decision 3) all read `public_quotes.py`. Replace "said" and
the featured quote with summary quotes across episodes. Keep a per-episode
`public_summary_quotes` table written after summarize and entity extraction,
because a per-request transcript load per episode does not scale to a person
page. "About" has no summary equivalent: drop it, or show the episode list.
The indexing rule needs a new count.

### Phase 3: summarizer prompt (proposed)

Ask for verbatim words, the speaker's full name, and no quotes the speaker
is only reading or relaying. Judge by the §2 table on the same episodes
before and after, with the 10-random-episode check (prompt-change rule).

## Testing

- [test_summary_quotes.py](../tests/unit/core/test_summary_quotes.py):
  parsing, name matching, ellipsis and editorial insertions, paraphrase
  refusal, search past a wrong citation, cold open vs moment, every
  rejection reason, playback offset.
- [test_public_api.py](../tests/integration/web/test_public_api.py): a real
  summary with a citations sidecar and annotated transcript; unnamed,
  hidden, misattributed and invented quotes are dropped; no transcript means
  no quotes; an old `#t=` link still finds its words.
- [test_quote_links.py](../tests/integration/web/test_quote_links.py): summary
  shares stored with `source = 'summary'`, the `/q` landing at the new time,
  reconcile skipping summary shares, Rule A returning the summary quotes.
- [test_public_prerender.py](../tests/integration/web/test_public_prerender.py):
  quotes in the HTML with the Play from link; held-back sections and
  excerpts stay out; hostile quote text stays inert.
- Vitest: `QuoteCard` play link and its sign-in fragment, the episode page
  with no YouTube link, the `play` intent.

Not yet checked: the signed-in landing in a browser after a real Google
sign-in (that `#t` survives and "Play from" plays through the drift map), and
prod numbers for §2 (the local copy is from July).

## Risks

- **Fewer quotes on some pages.** 6% of episodes show none, so the section
  hides. That is better than fragments, but those pages lose a block of
  indexable text.
- **Older summaries** written before citations (spec #54) rely on the
  whole-transcript search alone, and profiles that rename the section need a
  heading containing "quote".
- **Cost per cache miss.** Building the quote set now loads the transcript
  JSON once (the citation loader reuses it). It is cached for 10 minutes per
  episode, but cold crawls of many episodes pay it each time.
- **A summary quote's link keeps its old name** after a speaker fix until the
  page rebuilds (§6). Hiding or revoking still withdraws it at once.

## Rollback

Revert the branch. Summary shares already minted stay resolvable (stored
rows); `thestill quote-share list` shows them, and `revoke` withdraws one.

## Open questions

1. Should the summary's Best Quotes section also stay in the "Also:" list
   under "Read the full summary", now that its quotes are public?
2. Phase 2's table: written at summarize time, or lazily on first public
   build and invalidated by `summary_path`?
