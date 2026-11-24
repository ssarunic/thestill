# Quote Cards and Clips

> **Status:** 🚧 Phase 0 in progress. Built (2026-11-24, branch `feat/114-quote-cards-phase0`): the tables, short link ids on public quotes, `/q/{id}` and its card, the shared-quote lookup, the `quote_cards_enabled` switch, `thestill quote-share revoke|list` and revocation when a show is made private. Phase 0 still to come: the share menu and the linked quote on the episode page (frontend), `ts_ref` attribution and the `/analytics` panel
> **Created:** 2026-11-05
> **Updated:** 2026-11-23 (open questions decided: D9–D12; follow hook reuses spec #117; Q6 opened on D6)
> **Priority:** Medium-high. The first readers arrive from public pages and leave without following anything (spec #111); this is the first feature built to bring *more* of them, so it ships together with the hook that keeps them.
> **Author:** Product & Engineering
> **Related:** [#100 quote-ranking-and-featured-quotes](100-quote-ranking-and-featured-quotes.md) (the quote gate and candidates table this consumes), [#94 public-pages](94-public-pages.md) (the landing page, head injection, `public_pages` opt-out), [#105 server-rendered-public-pages](105-server-rendered-public-pages.md) (UA-blind rule, one URL per episode), [#104 abuse-prevention-and-user-admin](104-abuse-prevention-and-user-admin.md) (quotas, kill switches, audit), [#111 product-health-metrics](111-product-health-metrics.md) (the Scorecard this is read against), [#95 product-analytics](95-product-analytics.md) (events), [#34 briefing-audio-and-feeds](34-briefing-audio-and-feeds.md) (quote clip cutting, designed there, built here first), [#38 karaoke-word-highlighting](38-karaoke-word-highlighting.md) (word timestamps), [#45 entity-page-enrichment](45-entity-page-enrichment.md) (guest social handles), [#91 transcript-and-summary-export](91-transcript-and-summary-export.md) (Web Share with files), [#86 quality-feedback-loop](86-quality-feedback-loop.md) (reports), [#110 transcript-editor](110-transcript-editor.md) (speaker fixes invalidate cards), [#93 native-web-capabilities](93-native-web-capabilities.md) (permission rule), [#42 robustness-and-failure-mode-hardening](42-robustness-and-failure-mode-hardening.md) (FM-6, FM-7)

---

## Executive summary

A podcast is hard to pass on: "listen to this two-hour episode" is a big
ask. A quote is easy to pass on. Thestill already knows which turns in an
episode are quotable (spec #100's gate), who said them, and where they
start, and it already renders them on public pages with a `#t=` deep
link. This spec turns each such quote into things people actually post:

1. **A short link** (`/q/<id>`) whose link preview *is* the quote card.
   Paste it into X, LinkedIn, Slack, WhatsApp or iMessage and the card
   appears, with no image upload. Clicks are counted server-side.
2. **An image card** (quote, speaker, show, artwork, short URL, thestill
   mark) the reader can download or hand to the phone's share sheet, with
   a pre-written caption that tags the speaker's X handle when Wikidata
   has one.
3. **A clip**: the quote's own audio, up to 30 s, cut from the episode at
   the entity stage and rendered on demand into a square or vertical video
   with a waveform and word-timed captions, for Instagram, TikTok and
   Reels, where links are not clickable and a QR code does the linking.

Every card and clip points back to the episode page at that moment, and
that page gets the one thing it is missing today: a reason to follow the
show. Spec #111's evidence is that public-page visitors read and leave;
more visitors without a hook only makes that number bigger.

**Three rules decide most of the design.**

- **Cards are made from gated quotes, not from arbitrary highlights.**
  The gate (whole sentences, speaker resolved to an anchored person, no
  sponsor read, not ad-adjacent) is what makes a card good and keeps a
  famous name off a sentence they did not say. Free highlighting is the
  last phase, and it never puts a name on a card the gate would not.
- **Text is public, audio is narrower.** Short attributed quotes match
  what the public pages already show. Redistributing publisher audio as
  branded video is a different posture: RSS-sourced shows only, never
  YouTube-derived audio, 30 s cap, only for shows the operator has
  switched clips on for (an allowlist, Decision D3), revocable by id.
- **One canonical URL per episode stays true.** The short link is a
  `noindex` page with its own Open Graph tags that sends people to the
  canonical episode URL with a fragment. No `?ref=` or `?utm=` on episode
  URLs (spec #105 §3); attribution travels in a short-lived cookie.

## Problem and evidence

- **Discovery friction.** Spec #94's landing and public episode pages
  exist for search engines and shared links, but the only thing a reader
  can share today is a whole episode (`EpisodeMoreMenu` → `useShareAction`,
  `kind: 'episode'`) or a public quote's `#t=` link whose preview is the
  episode artwork and title, not the quote. Spec #95 records seven `share`
  call sites and one `kind`.
- **The landing cannot play the moment.** The anonymous episode page
  ([pages/public/Episode.tsx](../thestill/web/frontend/src/pages/public/Episode.tsx))
  shows the summary lead, the quotes and Listen-on links. It has no player,
  and the transcript is behind sign-in by decision (spec #94 §"Summary lead
  public"). A `#t=` link promises a moment the visitor cannot hear.
- **Visitors do not stay.** Spec #111 §"What the first new readers did":
  three readers from public episode pages read 5–8 summaries each and
  followed nothing. The activation loop (follow → delivery → return) never
  started. Any acquisition feature inherits this unless it ships the hook.
- **The data is already there.** Quotable turns, speaker identity, word
  timestamps, episode artwork, show artwork, the speaker's X handle from
  Wikidata, ffmpeg on every deployment. What is missing is the rendering,
  the link, the landing hook and the measurement.

## Current state

| Piece | State | Where |
|---|---|---|
| Public quotes (stream, featured, linked variants), native share on phones, `#t=` deep link | ✅ Shipped (spec #94 stand-in for #100) | [components/public/ui.tsx:138](../thestill/web/frontend/src/components/public/ui.tsx#L138) `QuoteCard`; [helpers.ts:49](../thestill/web/frontend/src/components/public/helpers.ts#L49) `quoteUrl` |
| Linked quote lands and scrolls into view on the public episode page | ✅ Shipped | [Episode.tsx:46](../thestill/web/frontend/src/pages/public/Episode.tsx#L46) (`t`, legacy `q`) |
| Quote gate SQL shared by every public quote query (`public_pages`, `public_hidden`, sponsor terms, intro skip, 60-char floor) | ✅ Shipped | [repositories/public_quotes.py:61](../thestill/repositories/public_quotes.py#L61) `_gate` |
| `public_quote_candidates` table, `quote_gate.py`, deterministic score | ⏳ Open (spec #100 Phase 1) | — |
| Narration quote selector (12–60 words, not ad-adjacent, whole sentences) | ✅ Shipped (spec #33) | [services/narration/quote_selector.py](../thestill/services/narration/quote_selector.py) |
| Word-level timestamps per segment | ✅ Shipped (spec #38) | [routes/api_transcript_words.py:71](../thestill/web/routes/api_transcript_words.py#L71) |
| "Copy link to this moment" on every transcript segment | ✅ Shipped (spec #23) | [SegmentedTranscriptViewer.tsx:230](../thestill/web/frontend/src/components/SegmentedTranscriptViewer.tsx#L230) `TimestampLink` |
| Share hook with Web Share fallback to clipboard and the `share` event | ✅ Shipped | [hooks/useShareAction.ts](../thestill/web/frontend/src/hooks/useShareAction.ts) |
| Web Share with files | 📝 Planned (spec #91 §7) | — |
| Open Graph head injection per public URL; `og-default.png`; `twitter:card summary_large_image` | ✅ Shipped (spec #94) | [web/public_shell.py:236](../thestill/web/public_shell.py#L236) `render_head` |
| Link-preview user agents classified separately from crawlers | ✅ Shipped | [utils/user_agent.py:30](../thestill/utils/user_agent.py#L30) `_PREVIEW` |
| Speaker's X and Instagram handles from Wikidata | ✅ Shipped (spec #45 T0) | [core/entity_enricher.py:276](../thestill/core/entity_enricher.py#L276) `_append_url_facts` |
| Original audio on local disk or S3 under PathManager; ffmpeg on PATH | ✅ | [utils/path_manager.py:273](../thestill/utils/path_manager.py#L273); `thestill/utils/ffmpeg_path.py` |
| Original audio deleted after processing; S3 deployments never hold it locally | ⚠️ Constraint | `DELETE_AUDIO_AFTER_PROCESSING` ([config.py:934](../thestill/utils/config.py#L934)); [transcript_edit_service.py:197](../thestill/services/transcript_edit_service.py#L197) `local_audio_path` returns `None` "in the common case" |
| Quotas with atomic reserve and commit, runtime kill switches, audit log | ✅ Shipped (spec #104) | [services/abuse/quota_service.py:199](../thestill/services/abuse/quota_service.py#L199) `reserve`; [settings_registry.py:197](../thestill/services/abuse/settings_registry.py#L197) `_switch` |
| Per-show public opt-out and per-entity hide, with CLI | ✅ Shipped (#94, #100) | `thestill set-podcast-visibility`, `thestill set-entity-visibility` |
| Scorecard metrics and the cohort experiment protocol | ✅ Shipped (spec #111) | `/analytics`, `docs/experiments.md` |

## Goals

| # | Goal | Verified by |
|---|---|---|
| G1 | Pasting a quote short link into X, LinkedIn, Slack, iMessage and WhatsApp previews as the quote card, not the episode artwork | Manual check per platform with the debuggers X and LinkedIn provide; pytest on the `/q/<id>` head (`og:url` is the short link itself, `og:image` the card) |
| G2 | Every card names a speaker only when the quote gate resolved one, and never a hidden entity | pytest: unresolved speaker renders "a guest on *Show*"; `public_hidden` entities get 404 on `/q`, 410 for existing ids |
| G3 | Every card and clip leads to the episode page at that moment, and that page offers Follow | Playwright: `/q/<id>` → episode URL `#t=`, linked quote highlighted, Follow CTA visible to anonymous visitors |
| G4 | Clips exist only for eligible shows and are never derived from YouTube audio | pytest on the eligibility predicate; no clip asset written for `audio_url` on a YouTube host or for a show with `audio_clips = false` |
| G5 | Operators can read the funnel without PostHog: links made, links opened, sign-ins that followed | `/analytics` Quote-cards panel from product tables; the quote-referred cohort visible in the spec #111 Scorecard |
| G6 | Anonymous visitors cannot make the server render or spend | pytest: `POST` card/clip routes 401 for anonymous; `GET /q/<id>.png` serves only cached or gated-public renders behind the public rate limit |
| G7 | A takedown is one command and takes effect within the cache TTL | pytest: `set-podcast-visibility --public false` and `quote-share revoke` make `/q/<id>` 410 and delete the assets |

## Non-goals

- Posting on the user's behalf through X, LinkedIn or Meta APIs. No OAuth
  apps for social networks. Sharing is a link, a file in the share sheet,
  or a download plus a copied caption.
- Anonymous playback of whole episodes (spec #94 keeps that out). This
  spec plays only the quote's own clip on the public page.
- Public transcripts, public search. Unchanged.
- Server-side "supercuts" of several quotes (spec #45 T2 lists one).
- A per-episode "best quotes" selection by an LLM. Spec #100's
  deterministic score picks; an LLM rerank is #100's call.
- Automated posting from an operator account (the "Most quoted this week"
  thread). Listed under ideas in §9, not built here.
- Cards for private content: briefings, inbox, transcripts of shows with
  `public_pages = false`.

## Options

### A. Short link with per-quote Open Graph image first, files second, video third (recommended)

The preview *is* the card, so the first phase needs no upload UX, no new
client flow beyond "Share quote", and works in every messenger. Image
download and Web Share with files add the Instagram and LinkedIn-native
path. Video last, because it has the storage, legal and compute weight.

### B. Client-rendered cards (canvas in the browser)

Renders on the user's device, no server cost. Rejected as the primary
path: link previews need a server-hosted image anyway, the fonts and
artwork would need CORS-clean fetches, and the card would differ by
device. Acceptable later for a live preview in the share sheet.

### C. Headless Chromium rendering of an HTML template

Pixel-perfect and reuses CSS. Rejected for this deployment: the server is
a small EC2 or an RPi5 (specs #05, #66), a Chromium process per render is
the heaviest thing it would run, and Pillow with a bundled font covers a
quote card. Revisit if card designs outgrow text-on-artwork.

### D. Free highlight first ("Genius")

Rejected as the first phase. Transcript segments are click-to-seek
buttons, so text selection on a phone fights the seek handler; users
select fragments; and an arbitrary span can cross speaker turns, which is
exactly how a wrong name ends up on a card. It is Phase 4, constrained to
one speaker turn and the gate's length rules.

## Design

### 1. Eligibility: what may become a card or a clip

One predicate, `quote_share_eligibility(episode, podcast, speaker)`, used
by the rendering service, the public routes and the CLI (FM-6: one
definition). It reuses `_gate`'s conditions:

| Condition | Card (text) | Clip (audio) |
|---|---|---|
| Show `public_pages` true and not synthetic | required | required |
| Episode has a summary (the public page exists) | required | required |
| Quote passes the gate: whole sentences, 60–320 chars, after the intro skip, no sponsor term, not ad-adjacent | required | required |
| Speaker resolved to a person entity, not `public_hidden` | name shown; otherwise "a guest on *Show*" | required (no anonymous-voice clips) |
| `podcasts.audio_clips` true (new column, default **false**: an allowlist, switched on per show; see Decision D3) | — | required |
| Episode audio is an RSS enclosure, not YouTube-derived (`audio_url` host not a YouTube or googlevideo host, and the episode has no YouTube-only source) | — | required |
| The cut clip, padding included, is ≤ 30 s (padding shrinks to fit; a quote over 30 s on its own is ineligible) | — | required |

Until spec #100's `public_quote_candidates` lands, the stand-in query in
`public_quotes.py` supplies the gated set; the id scheme in §2 does not
depend on which one is underneath.

### 2. Identity and the short link

**Table `quote_shares`** (one row per shareable quote; additive):

```sql
CREATE TABLE quote_shares (
    id              text PRIMARY KEY,            -- 10-char base32, see below
    episode_id      uuid NOT NULL REFERENCES episodes(id) ON DELETE CASCADE,
    podcast_id      uuid NOT NULL REFERENCES podcasts(id) ON DELETE CASCADE,
    segment_id      bigint NOT NULL,
    start_ms        bigint NOT NULL,
    end_ms          bigint NOT NULL,             -- end of the source turn at minting; with start_ms, the source span (§7)
    audio_key       text NULL,                   -- the cut clip audio, set in Phase 2; never derived from segment_id
    text            text NOT NULL,               -- the gated sentence block
    speaker_entity_id text NULL,                 -- re-resolved, see §7; no FK (merges delete rows)
    source          text NOT NULL,               -- 'gated' | 'custom'
    created_by      uuid NULL REFERENCES users(id) ON DELETE SET NULL,
    created_at      timestamptz NOT NULL DEFAULT now(),
    revoked_at      timestamptz NULL,
    template_version smallint NOT NULL DEFAULT 1
);
CREATE INDEX ON quote_shares (episode_id, segment_id);
```

- **Gated quotes get a deterministic id:** base32 of the first 50 bits of
  `sha256(episode_id || start_ms || text)`, so the same quote always
  has the same link. The text is in the hash because one turn can yield
  several excerpts: each entity page picks the excerpt around its own
  name, and on today's source they all carry the turn's `start_ms`. The
  segment id is not in it: it is positional and changes with edits (§7).
  The same words always get the same link and spec #100 can take over the source without
  changing ids. **The row is persisted before the link is shown**, never
  minted on `GET /q/<id>`: a truncated hash cannot be reversed, so an id
  with no row cannot be resolved to a quote without hashing the whole
  eligible set. Two writers mint rows:
  - the public quote payloads (`public_episode_quotes_payload` and the
    entity and show quote routes) upsert one `quote_shares` row per quote
    they return, in one `INSERT … ON CONFLICT (id) DO NOTHING` per
    response, before the response carries `share_id`. The write is
    idempotent and bounded by the gated set, which is why it is allowed
    on an anonymous read path; under `read_only` or on a failed upsert
    the payload carries no `share_id` and the share menu falls back to
    the `#t=` episode link;
  - spec #100's candidates step mints the row when it writes the
    candidate, so once #100 lands the public read path writes nothing.
  `GET /q/<id>` for an id with no row is a plain 404; it never searches
  quotes. Collisions are detected on insert (same id, different
  `(episode_id, start_ms, text)`). The newer quote then gets no share
  id and keeps its plain link to the moment; with 50 bits, two quotes
  of the whole catalog colliding is not expected to happen, and a rule
  that renames one side is more machinery than that deserves.
- **Custom selections (Phase 4) get a random id** and a `created_by`.
- **`text` is a snapshot; `speaker_entity_id` is a cache.** A re-clean or
  a spec #110 edit changes the transcript; the card keeps saying what it
  said when shared. The speaker is different: `speaker_entity_id` mirrors
  the segment's *current* speaking mention and is rewritten whenever that
  association changes (§7), so a fixed misattribution fixes the card. The
  on-read upsert is `ON CONFLICT (id) DO UPDATE SET speaker_entity_id =
  EXCLUDED.speaker_entity_id` where it differs, never `DO NOTHING` for
  that column.

**Route `GET /q/{id}`** (public router, rate-limited, UA-blind):

- 200 with a minimal HTML page: `<title>`, description, `og:title` (the
  quote, clipped), `og:description` (speaker on show, episode title),
  **`og:url` = the short link itself** (Decision D1: Facebook and LinkedIn
  re-fetch `og:url` and take *its* tags, so pointing it at the episode URL
  would replace the card with the episode artwork), `og:image` =
  `/q/{id}.png`, `twitter:card summary_large_image`,
  `<meta name="robots" content="noindex">`, `<link rel="canonical">` =
  the episode URL, `<meta http-equiv="refresh" content="0;url=…#t=…">`
  and a visible link for clients that do not follow refresh. The same
  bytes for every user agent; previewers read the tags, browsers move on.
- The redirect target is
  `/podcasts/{show}/episodes/{slug}#t={start_s}&s={id}`. The share id
  travels in the fragment because the time alone cannot say which quote
  was shared: several excerpts of one turn have the same `t`, and the
  episode page's own quote list keeps one excerpt per turn. The page
  reads `s` and shows the linked card from the share's stored snapshot,
  fetched from `GET /api/public/quote-shares/{id}` (the text, speaker
  and time of that share, under the same eligibility and revocation
  rules as `/q/{id}`: 404 or 410 otherwise). A snapshot whose words
  were later edited in the transcript still shows what was shared.
  Without `s`, or when the share is gone, the page falls back to today's
  match by `t`. No query string (spec #105 §3; Google indexed every
  `?t=` variant); a fragment is never sent to the server or indexed.
- Sets `ts_ref=q:{id}` (HttpOnly, `SameSite=Lax`, 7 days) for the
  attribution in §6. Nothing else is stored client-side.
- 404 for an unknown id or an ineligible quote; **410 Gone** for a revoked
  one, with the assets deleted. Hidden speaker: 404 if the row never
  existed, 410 if it did (G2).
- Writes one `quote_share_hits(share_id, ua_class, referrer_host, day)`
  row for `browser` user agents only (`classify_user_agent`), so opens are
  counted without PostHog and without bots. Previewer fetches are logged,
  not counted.

**Route `GET /q/{id}.png`** and `GET /q/{id}/{variant}.png`: the rendered
card from the cache, rendered on first request for gated quotes (sub-second,
behind a process-wide semaphore of 2), `Cache-Control: public, max-age=86400`,
ETag from `(id, template_version, speaker name hash)`. Custom quotes are
rendered at creation, never on `GET`.

### 3. Card rendering

A `QuoteCardRenderer` in `thestill/core/quote_cards.py` (Pillow; the
`Inter` family bundled under `thestill/web/static/fonts/`, licence file
beside it; Decision D2), producing:

| Variant | Size | Used by |
|---|---|---|
| `og` | 1200 × 630 | link previews (X, LinkedIn, Slack, iMessage, WhatsApp) |
| `square` | 1080 × 1080 | Instagram and LinkedIn image posts, downloads |
| `story` | 1080 × 1920 | Stories and Reels covers; the video's first frame |

Content: the quote (auto-sized between 28 and 56 px, hyphenation off,
balanced line breaks, up to 8 lines; the gate's 320-char cap keeps this
safe), the speaker line (name, or "a guest on *Show*"), show and episode
title, episode artwork (falls back to show artwork, then a neutral tile),
the short URL as text (`thestill.me/q/abcd…`), and the thestill mark.
**No QR code on image cards** (Decision D4): nobody scans a code on the
phone they are holding; the QR belongs on video (§4) where links do not
work. One dark template: the text sits on the card's own background,
never over the artwork, so contrast does not depend on the artwork.

Artwork is fetched through the existing guarded HTTP client with the
usual SSRF rules, cached under `PathManager.quote_cards_dir()` as
`<id>/<variant>.v<template_version>.png`, written through `FileStorage`
so S3 deployments work. Rendering of untrusted text (FM-7): the quote is
LLM-cleaned transcript text and the speaker name is entity data; both are
drawn as glyphs, never interpreted, and control characters are stripped.

Cost: one card ≈ 100–300 ms on the RPi5, ~150 KB. A cache miss storm is
bounded by the semaphore and the public rate limit; the switch
`quote_cards_enabled` (§8) turns on-demand rendering off while serving
the cache.

### 4. Clips

**4.1 Clip audio is cut at the entity stage, not on demand.** Original
audio is gone "in the common case" (`DELETE_AUDIO_AFTER_PROCESSING`, S3
deployments), and re-fetching the publisher's enclosure is unreliable:
dynamic ad insertion shifts timestamps by minutes (spec #113 measured
4.5–11.7 min of drift on one show). So the step that writes spec #100's
`public_quote_candidates` also cuts, for each eligible candidate, a mono
64 kbps MP3 of the quote range with up to 250 ms padding (less when the
file would pass 30 s) and sentence-snapped
boundaries, into `PathManager.quote_audio_dir()/<episode>/<share_id>.mp3`,
recorded on the share as `audio_key`. The key is the share's id, never
its segment id, which §7 rewrites after structural edits.
Twenty quotes × ~250 KB ≈ 5 MB per episode, deleted with the episode
or when the show turns `audio_clips` off. `ensure_ffmpeg_on_path` already
guarantees ffmpeg. A backfill command, `thestill quote-audio backfill`,
cuts for episodes whose audio still exists and reports those whose audio
is gone.

**Retention until the cut, when `DELETE_AUDIO_AFTER_PROCESSING` is on.**
Today that flag deletes the original right after downsampling
([task_handlers.py:381](../thestill/core/task_handlers.py#L381)) and the
downsampled WAV right after transcription
([task_handlers.py:577](../thestill/core/task_handlers.py#L577)), both
long before the entity branch runs, so "cut at the entity stage" alone
would find no audio on those deployments. The cut cannot move earlier:
it needs the speaker resolved to a person, which happens in
`RESOLVE_ENTITIES`. So the deletion moves, not the cut:

- the downsample handler **skips deleting the original** when the episode
  is clip-eligible by the show-level conditions in §1 (`public_pages`,
  `audio_clips`, RSS enclosure), and marks `episodes.audio_retained_for =
  'clips'` (new nullable text column); ineligible episodes are deleted
  there exactly as today;
- the candidates step cuts from the retained original, then deletes it
  and clears `audio_path`, as the downsample handler does now (it is the
  last consumer). The cut uses the original, not the 16 kHz WAV, for
  quality; the WAV's deletion after transcription is unchanged;
- a housekeeping job (next to `usage_housekeeping_jobs`) deletes retained
  originals older than 7 days whose episode never reached the cut (the
  entity branch is `_NON_USER_FAILING`, so it can fail without the user
  noticing) and clears the marker, logging each with `episode_id`. Spec
  #49's healer can retry the branch before then.

**The Dalston direct-URL path has no original to retain.** With
`transcription_provider = dalston`, an RSS episode skips download and
downsample (`_get_starting_stage` in `api_commands.py`): Dalston fetches
the enclosure itself, so the downsample hook above never runs. This is
production's path, for new episodes too and whatever
`DELETE_AUDIO_AFTER_PROCESSING` says. For these episodes the retained
audio is **Dalston's own job audio**, the exact bytes that were
transcribed: the transcribe handler already streams it through
`iter_job_audio` to write the sync track (`_publish_sync_track`), and for
a clip-eligible episode the same pass also writes it to the retained
original's place and sets `audio_retained_for = 'clips'`. Fetching the
enclosure separately is not an option: it is another copy with other
ads, the drift D6 rejects. The cut, the deletion after it and the 7-day
sweep are then the same as above. Q6 asks whether cutting on demand
through the sync track should replace all of this.

Cost: one original (50–150 MB) lives for the hours between downsample
and the entity branch instead of minutes, only for clip-eligible shows.
Deployments with the flag off keep every original and need none of this.
With `clips_enabled` off (§8) the handler deletes as today.

**4.2 Video is rendered on demand, queued, quota'd.** `POST
/api/quote-shares/{id}/clip` (signed in) reserves `clips_per_day`,
enqueues a render job, and returns a job id; the client polls
`GET /api/quote-shares/{id}/clip` for `pending | ready | failed` and a
URL, mirroring the narration job pattern (`api_narrations.py`). Rendering
is one ffmpeg invocation per variant:

- inputs: the `quote_audio` MP3, the `story` or `square` card PNG as
  background, the artwork;
- `showwaves` (or `showfreqs`) as a waveform band in the show's accent
  colour;
- captions from the words endpoint as an ASS subtitle track with `\k`
  karaoke timing when ffmpeg has libass (probed once at startup), else
  per-word `drawtext` filters (worse, still correct). A turn can have
  no word timings (no `source_word_span`, or stale after a spec #110
  text edit; the words endpoint then omits it): the video then shows the
  whole quote as static captions, and the cut in §4.1 falls back to the
  turn's own start and end. A turn longer than 30 s without word timings
  is clip-ineligible (`no_word_timings`), since the quote cannot be
  located inside it;
- a QR code (the `segno` library, pure Python, MIT) of the short link,
  bottom corner, with the short URL text beside it;
- H.264 `yuv420p`, AAC 128 kbps, 1080 × 1080 and 1080 × 1920, ≤ 30 s,
  `faststart`.

A 15 s clip renders in 5–15 s on the EC2 instance and up to a minute on
the RPi5; the job runs with `nice` under a process-wide concurrency of 1.
Output goes to `PathManager.quote_clips_dir()/<id>/<variant>.v<n>.mp4`
through `FileStorage`, served by `GET /q/{id}/{variant}.mp4` with the same
cache and revocation rules as the images.

**4.3 Clips are narrower than cards.** §1's clip column: RSS enclosure
only, never YouTube-derived audio (the existing "Hear it on YouTube"
timestamp link covers those), named speaker only, show on the clips allowlist,
30 s cap. The rendered video carries the show's name and the short link
on every frame; there is no unbranded export.

### 5. Where sharing happens

**Public pages (anonymous and signed in).** `QuoteCard` gains a share
menu replacing the bare share button: *Copy link* (the short link),
*Share…* (Web Share with the short link, files when `canShare({files})`
and the image is ready, spec #91 §7's path), *Download image* (square),
*Copy caption*. On the public episode page the `linked` variant gains the
clip player (§5.3) and the follow hook (§5.4).

**Transcript (signed in).** Each turn's hover or long-press actions (next
to `TimestampLink`) gain *Quote this*. It opens a bottom sheet with the
turn's gated sentence block preselected, ± sentence toggles within the
gate's limits (one speaker turn, ≤ 320 chars, ≤ 30 s), a live card
preview, the caption, and the same actions plus *Create clip* when the
quote is clip-eligible. The sheet refuses with a plain reason when the
turn fails the gate ("Sponsor read", "No named speaker", "Too long").
Phases 1–3 allow only the gate's own sentence block; Phase 4 allows the
trim.

**Caption.** Pre-written, editable, copied with the link:

> "{quote}" — {speaker} on {show}
> {@handle if the speaker's enrichment has an X handle and the card is for X}
> {short link}

The handle comes from the spec #45 enrichment facts (`X (Twitter)`); it
is a suggestion in the caption, never on the image, and the sheet shows
it as a removable chip (Decision D5: tagging is the viral mechanic, but
the user decides). Operators can blank a wrong handle with the existing
entity curation commands.

**5.3 The anonymous clip player.** On the public episode page, the linked
quote card (and each quote in the stream when its clip audio exists)
gets a *Hear it · 0:14* button that plays the `quote_audio` MP3 inline.
This is the only anonymous playback and it plays only the quote. The
public route serves the MP3 with `Range` support and the public cache
helper; eligibility is re-checked at request time.

**5.4 The follow hook.** The public episode page already has it:
spec #117 (v1.47.0) put a Follow button beside the show name for every reader
who does not follow the show. Signed out, it goes through sign-in
(`intent=follow`, the spec #102 pending follow) and comes back to the
episode with the follow done and a toast that offers Undo. This spec adds
no second button. The linked quote card ends with one line, "Thestill
summarises every episode of *Show*.", and a Follow button that is the
same component (`EpisodeFollow`), so the pending follow, the toast and
the `follow` event's source are shared; the source value for this
placement is `quote_card`. **No auto-follow** on landing: the pending
follow is stored only by the visitor's click on Follow, never by the
`/q` link (a URL is not consent, spec #80's rule; a forced follow also
games spec #111's activation metric). Readers who already follow the
show see no button on the card.

### 6. Measurement

Product tables first, PostHog second. Facts the product needs anyway
(links, opens of a link, clips rendered) count every reader. The one
table kept only to measure, `quote_share_actions`, honours the
usage-analytics opt-out like every measurement-only fact (spec #111 §1).

| Fact | Where | Why |
|---|---|---|
| Links minted | `quote_shares` (`source`, `created_by`, `created_at`) | catalog size only: gated rows appear when a quote is served or a candidate is written, **not** when someone shares, so this is never labelled "shares" |
| Share actions (signed in) | new `quote_share_actions(share_id, user_id, kind image\|clip\|link, method link\|files\|download\|caption, created_at)`, written by `POST /api/quote-shares/{id}/actions` from the signed-in share menu and by the clip and download routes themselves | the share volume the product tables can know. This log exists only to measure, so it is written only when `analytics_request.may_measure` allows (usage analytics on, no GPC), as spec #111 §1 requires of measurement-only facts; readers who opted out are not in it and the panel says so. Anonymous share actions never write (G6) and exist only as the PostHog `share` event |
| Short link opens | `quote_share_hits` (browser UAs only, `referrer_host`, day) | the backlink loop, per platform; the primary signal, because it counts anonymous sharers' audiences too |
| Clips rendered | `usage_events` kind `clip` (spec #104 ledger) | cost and quota tuning |
| Sign-ins that followed a card | `ts_ref` cookie read by the OAuth callback → `login_completed.next_kind = quote` and a `users.acquired_via = 'quote:<id>'` column set once on first sign-in | the quote-referred cohort for the Scorecard |

Client events (spec #95 names): `share` with `kind` = `quote` or `clip`
and `method` = `link | files | download | caption`; `quote_sheet_open`;
`clip_requested`. Server: `quote_link_open` with `referrer_host`, no id.

`/analytics` gains a *Quote cards* panel: links minted, links opened at
least once, opens per opened link, signed-in share actions by method,
sign-ins, and the quote-referred cohort's spec #111 activation and
week-4 retention beside the all-readers numbers. No "shares" number is
derived from `quote_shares` row counts. The feature is read
cohort by cohort per `docs/experiments.md`; the hypothesis to log before
launch: *readers who arrive from a quote link activate at least as often
as readers who arrive from search*. If they activate markedly less, the
landing hook, not the cards, is what to change next.

### 7. Misattribution, takedown and revocation

- **The speaker on the card is the segment's current speaker**, not the
  one cached when the link was minted. A spec #110 `rename-speaker` or
  label link changes the *segment's* speaker entity
  ([transcript_edits.py:439](../thestill/core/transcript_edits.py#L439)
  `_rename_label` writes `speaker_entity_id` into segment metadata), not
  person A's name, and the editor's finish step queues the spec #106
  `--from entities` refresh that rewrites the `entity_mentions` speaking
  rows. So `QuoteShareService.invalidate(episode_id)` runs **when that
  refresh completes** (a hook at the end of the entity branch for a
  reprocessed episode; an entity relink via `thestill entity relink`
  triggers the same), and it does two things for every share of the
  episode: re-resolves `speaker_entity_id` **by the quoted words, not by segment
  id**. A split or merge in the editor renumbers the effective
  transcript's segments (`transcript_edits.py`), so the stored
  `segment_id` can point at another turn after a refresh. The share's
  anchor is its **`text` snapshot inside its source span**
  (`start_ms`–`end_ms`, the turn's time range when the share was
  minted; times survive splits, merges and renumbering). The rule: take
  the speaking mentions whose time range overlaps the source span and
  whose segment's effective text contains the snapshot. Exactly one:
  the speaker is that mention's entity and `segment_id` is rewritten to
  its segment, so a split inside the turn follows the words to the half
  that holds them. None (the words were edited, or the snapshot now
  straddles a split) or several: the speaker becomes `NULL` and the
  card renders "a guest on *Show*" rather than guess. A match outside
  the source span never counts, however exact: another speaker
  repeating the passage minutes later is not evidence of who said it
  here. A now-hidden
  speaker makes the share 410. The id
  does not change: it was derived once and is stored. The invalidation
  also deletes **every rendered asset that carries the name**: the
  `quote_cards/<id>/` directory and the `quote_clips/<id>/` directory
  (§4.2's videos embed the card PNG, so a stale video would keep showing
  the wrong speaker after the card was fixed). Images re-render on the
  next request. Videos are not rendered on `GET`, so for each share that
  had a rendered clip the invalidation **re-queues the render itself**,
  as a system job that reserves no user quota and runs under the same
  concurrency limit; until it finishes `GET /q/<id>/<variant>.mp4`
  returns 404 with `Retry-After`, never the old file. The `quote_audio`
  MP3 carries no name and is kept; it is found through `audio_key`, which
  the rewrite of `segment_id` does not touch. Image and video cache keys include a
  hash of the speaker name beside `template_version`, so a stale file
  can never match a fresh request even if a deletion is missed. The id
  and link survive. Between the edit and the refresh the old assets can
  still serve; the editor's existing "entities refreshing" state covers
  that window.
- **Hidden speaker** (`entities.public_hidden`): every route 404s or 410s,
  assets deleted by the same invalidation.
- **Show opt-out**: `set-podcast-visibility --public false` already removes
  the show from `_gate`; this spec makes it also revoke the show's
  `quote_shares` and delete assets. New `--audio-clips on|off` puts a show
  on the clips allowlist or takes it off; off keeps cards and deletes
  `quote_audio` and clip renders.
- **`thestill quote-share revoke <id> --reason "…"`**: 410, assets gone,
  audited through `AuditService.record`. **`thestill quote-share list
  --episode <slug>`** shows what exists.
- **Report link** on the `/q` page and under every card: "Wrong speaker?
  Report" → the spec #86 report path with the share id as subject.
- Custom (Phase 4) selections get a clip only when they lie inside audio
  that was retained for a gated quote of the same turn; any other custom
  selection is image-only, and the sheet says so. The original is gone
  by then (D6) and is never re-fetched.
- Custom (Phase 4) cards never carry a name the gate would not put there,
  and never span two speaker turns.

### 8. Abuse, cost and switches (spec #104)

| Control | Default | Note |
|---|---|---|
| `quote_cards_enabled` switch | on | Off: no new renders; cached images still serve; `/q` still redirects |
| `clips_enabled` switch | on | Off: `POST …/clip` 503, queued jobs drain |
| `read_only` | — | pauses both, as every write |
| `quote_shares_per_day` quota | 50 | custom shares and clip requests reserve it; gated rows do not (the server mints them when it serves a quote, bounded by the gated set and the public rate limit) |
| `clips_per_day` quota | 10 | reserve → render → commit; failed render releases |
| Public rate limit | existing | `/q/*` sits on the public router |
| Render concurrency | 2 images, 1 video | process-wide semaphores |
| Per-show `audio_clips` | false | the allowlist: `set-podcast-visibility --audio-clips on` per show (D3) |

Anonymous visitors never trigger a render they did not get from the cache
or a gated public quote (G6). Image renders are cheap enough that the
rate limit suffices; video is never anonymous.

### 9. Ideas kept for later (not in scope)

- **Auto-cards on every public episode page**: the top three gated quotes
  rendered as image cards in the page and in the spec #105 fallback.
  Cheap once §3 exists; waits for #100's score so "top" means something.
- **Creator mode**: a show's owner downloads that episode's cards and
  clips. Podcasters pay for this elsewhere and have the audience.
- **Briefing quote of the day**: one card in the spec #51 email.
- **"Most quoted this week" thread** from #100's leaderboard, posted by
  the operator.
- **MCP tool `share_quote`** returning the short link, so connector users
  share from chat; counts as a value day.
- **Genre card styles** keyed off the spec #98 profile.
- **Video as the Instagram format, in place of the `story` image.** Raised
  2026-11-23, not decided. On Instagram a Reel with the quote's audio and
  karaoke captions reaches people a static image does not, and hearing
  the speaker is proof where a card is a claim. If Phase 0 shows that
  quotes get shared, Phase 1 could drop the `story` image and leave
  Instagram to the Phase 3 video; the `square` image stays for LinkedIn
  and downloads. Against moving video earlier: links in Instagram
  captions are not clickable, so a Reel can get views and send almost
  nobody back, and video carries the audio, allowlist and render-queue
  weight (Q6). Rendering in the browser was considered and set aside:
  video export is unreliable on phones, where Instagram sharing happens.

## Phases

| Phase | Scope | Depends on |
|---|---|---|
| 0 | `quote_shares` + `quote_share_hits` tables; deterministic ids; `GET /q/{id}` and `/q/{id}.png` with the `og` card; `GET /api/public/quote-shares/{id}` and the `s` fragment on the episode page; `QuoteCardRenderer`; share menu on public `QuoteCard`; `ts_ref` attribution; `/analytics` panel; revoke CLI and show-opt-out revocation | — (works on the #94 stand-in quotes) |
| 1 | `square` and `story` variants; download and Web Share with files; caption with the X handle chip; *Quote this* in the transcript (gate's block only); follow hook on the linked card | Phase 0; spec #91 §7's files path (small, can be built here) |
| 2 | `quote_audio` cutting at the entity stage + backfill; original-audio retention until the cut when `DELETE_AUDIO_AFTER_PROCESSING` is on, and of Dalston's job audio on the direct-URL path (`audio_retained_for`, orphan sweep); the anonymous clip player; `audio_clips` column and CLI flag | spec #100 Phase 1 (candidates table); Phase 0 |
| 3 | Video clips: queued render, `clips_per_day`, `clips_enabled`, QR, `GET /q/{id}/{variant}.mp4` | Phase 2; libass probe |
| 4 | Custom trim within one speaker turn; free highlight that snaps to the gate's rules (image-only outside retained clip audio, §7) | Phase 1; the use of *Quote this* in Phases 0–1 (D11) |

Phase 0 is the launchable unit and the cheapest test of the whole thesis:
if shared quote links do not get opened, nothing after it is worth
building.

### Phase 0 on today's code (checked 2026-11-23)

A read of the code before building found places where the sections above
describe spec #100's future gate, not the stand-in Phase 0 runs on. For
Phase 0 these hold; the sections above describe the end state.

- **What a quote is today.** The stand-in's quote is an entity mention's
  `quote_excerpt`: up to about 200 characters around a name, snapped to
  sentence ends where there are any (`_excerpt_around`). `_gate` checks
  60 characters, the intro skip, sponsor terms and visibility. It has no
  320-character cap, no ad-adjacency check and no whole-sentence rule.
  Phase 0 therefore adds its own card test on top of `_gate`: the text
  stands alone (`_stands_alone`: opens and ends a sentence) and is at
  most 320 characters, and the card's fonts can draw it. The quote is
  set in Newsreader, or in Inter when it has letters Newsreader lacks
  (Cyrillic, Greek). Text neither font covers (Arabic, Chinese and
  others) gets no card: a card of empty boxes is worse than none, and
  bundling more scripts is a later choice. A quote that fails the test
  keeps today's `#t=` link and gets no `share_id`.
- **The speaker is always a named person when a share is minted** on
  this source (`_FROM` requires it). "A guest on *Show*" appears in
  Phase 0 only when a later transcript edit leaves the speaker
  unresolvable (§7).
- **Times are the turn's, not the excerpt's.** A row's `start_ms` is the
  start of the turn the excerpt came from. Phase 0 stores that with the
  turn's end (the speaking row's `end_ms`, selected for this purpose) as
  the share's source span. The excerpt's own times arrive with Phase 2,
  from word timestamps, in the clip cut; the source span does not change.
- **Minting happens when a quote payload is built, not per response.**
  Payloads are cached for 10 minutes and the prerender calls the same
  builders, so the upsert runs once per cache build. This is the public
  module's second write after the page-view counter; its "never writes"
  note is corrected. Under `read_only` a fresh build mints nothing; a
  payload cached before the switch keeps its ids, which is harmless (the
  rows exist).
- **The `/q` page writes its own head.** `render_head` always sets
  `og:url` to the canonical, and D1 needs them to differ.
- **A merged speaker is followed, not dropped.** `speaker_entity_id` has
  no `ON DELETE SET NULL` shortcut to rely on: an entity merge deletes
  the losing row, so the share re-resolves the speaker from the segment
  (§7) and, failing that, through `entity_redirects`.
- **Fonts live in the Python package** (`thestill/assets/fonts/`), not
  under `thestill/web/static/`, which is the Vite build output and is
  emptied on every build. Pillow becomes a direct dependency.
- **The takedown command is `set-podcast-visibility --private`** (the
  flag is `--public/--private`).
- **Attribution.** `login_completed.next_kind` is a closed list and the
  browser's event allowlist takes `share` with `kind` only; Phase 0 adds
  `quote` to the first and `method` to the second. The `/analytics`
  panel is Postgres-only, like the rest of `/analytics`.

### Acceptance criteria

- `GET /q/<id>` for a gated quote returns 200 HTML whose `og:url` is the
  short link, `og:image` the `/q/<id>.png` URL, `robots` `noindex`,
  canonical the episode URL; the same bytes for a browser, Twitterbot and
  curl.
- `GET /q/<id>.png` renders once and serves from cache after; ETag changes
  when the speaker name changes.
- `GET /q/<id>` for an id with no `quote_shares` row is 404 without
  touching the quote tables; every quote a public payload returns has a
  row before the response is sent.
- With `DELETE_AUDIO_AFTER_PROCESSING=true`, a clip-eligible episode
  processed end to end has its `quote_audio` files and no original; an
  ineligible episode has no original from the downsample stage on.
- An unresolved speaker renders "a guest on *Show*"; a `public_hidden`
  speaker's share is 404/410 with no assets on disk.
- After a segment's speaker is corrected from A to B in the transcript
  editor and the entity refresh completes, `/q/<id>.png` for that quote
  shows B, with the same id and link; a previously rendered
  `/q/<id>/<variant>.mp4` is never served with A again, and comes back
  showing B once the re-queued render finishes.
- `/analytics` labels `quote_shares` counts as links minted; share
  volume comes from `quote_share_actions` and opens from
  `quote_share_hits`.
- Pasting the link into X's and LinkedIn's post composers shows the card
  (manual, recorded in the PR).
- Following the link lands on the episode page with the quote highlighted
  and, for an anonymous visitor, a Follow CTA; no auto-follow occurs.
- `set-podcast-visibility --public false` revokes the show's shares; the
  next `/q` request is 410 and the assets are gone.
- A quote on a YouTube-derived episode has a card and no clip button;
  `POST …/clip` for it is 409 `clip_ineligible`.
- Anonymous `POST` to any share or clip route is 401.
- `/analytics` shows shares, opens and the quote-referred cohort's
  activation.

## Testing

- **Renderer golden files**: one card per variant for a short, a long and
  a right-to-left quote, compared with a perceptual threshold (fonts
  render slightly differently across platforms; exact bytes would flake).
- **Eligibility predicate**: table-driven pytest over every row in §1,
  SQLite and Postgres.
- **Routes**: head tags, cache headers, rate limit, 404/410 matrix, UA
  blindness (same body for three user agents), `ts_ref` cookie set and
  read by the OAuth callback.
- **Invalidation after a split**: a turn before a shared quote is split,
  so every later segment id shifts by one; after the refresh the share
  still names its own speaker and its `segment_id` is the new one. A
  shared turn split after its start, with the second half (which holds
  the snapshot) reassigned, takes the second half's speaker. A share
  whose words were edited in its own turn loses its name, even when
  another speaker says the same words elsewhere in the episode.
- **Invalidation**: a `rename-speaker` op that moves a segment from
  person A to person B, followed by the entity refresh, rewrites the
  share's `speaker_entity_id` to B, deletes the card and clip
  directories, re-queues the clip render without a quota reservation,
  and the next card render shows B's name while the clip URL is 404 with
  `Retry-After` until the new video exists; relinking to no entity
  renders "a guest on *Show*"; the on-read upsert updates a stale
  `speaker_entity_id`; a cache key with the old speaker hash never serves.
- **Measurement**: serving a quotes payload twice adds rows to
  `quote_shares` only once and never to `quote_share_actions`; a
  signed-in download adds one action row, and none for a reader who
  opted out of usage analytics or sends GPC; an anonymous
  `POST …/actions` is 401.
- **Identity**: two different excerpts of one turn (the same
  `start_ms`) get two ids and two rows, each with its own text; the
  same excerpt reached from an entity page and from the episode page
  gets one.
- **Minting**: a quotes payload inserts a `quote_shares` row per returned
  quote and is idempotent on the second call; the same payload under
  `read_only` writes nothing and carries no `share_id`; `/q/<id>` for an
  id with no row is 404 and runs no quote query (asserted with a query
  counter).
- **Retention**: with `DELETE_AUDIO_AFTER_PROCESSING` on, the downsample
  handler keeps the original for a clip-eligible episode and deletes it
  for an ineligible one; the candidates step deletes it after the cut; the
  sweep deletes an 8-day-old retained original and clears the marker.
- **Retention, Dalston path**: a clip-eligible episode transcribed through
  the direct-URL path has the job audio retained and marked by the
  transcribe handler; an ineligible one writes nothing.
- **Clip cutting**: a synthetic 60 s tone with known word timestamps cuts
  to the expected length ± 50 ms; the ASS track has one event per word;
  the ffmpeg filtergraph is a pure function with a unit test, and one
  integration test runs real ffmpeg when it is present.
- **Landing**: two shares minted from different excerpts of one turn
  each land on the episode page with their own text in the linked card;
  a revoked share's `s` falls back to the match by `t`.
- **Playwright**: share menu on a public quote; `/q` → episode `#t=` →
  highlighted quote → Follow CTA; signed-in *Quote this* sheet refuses a
  sponsor-read turn with the reason.
- **Vitest**: no `POST` from the anonymous page; `canShare({files})`
  branch and fallback.

## Risks and negative consequences

| Risk | Mitigation |
|---|---|
| A wrong name on a widely shared card | Gate-only sources; render-time name; revoke CLI; report link; hidden-speaker switch (§7). The residual risk is a diarization error the gate cannot see; the "Hear it" link makes the claim checkable |
| Publisher objection to audio clips | RSS-only, 30 s, attribution and link on every frame, clips only for shows on the `audio_clips` allowlist (D3), per-id revoke |
| YouTube Terms of Service | No clips from YouTube-derived audio, ever; cards link to YouTube at the timestamp instead |
| Facebook and LinkedIn replace the card with the episode artwork | `og:url` is the short link (D1); checked in the acceptance criteria |
| Search engines index `/q` pages or treat them as cloaking | `noindex` in the page and in an `X-Robots-Tag` header, plus the canonical; UA-blind bytes. `/q/` is **not** disallowed in `robots.txt`: Twitterbot obeys `robots.txt`, so a disallowed link would preview as nothing, and a crawler that cannot fetch the page cannot see its `noindex` either |
| Render storms on the RPi5 | Cache, two-image semaphore, public rate limit, `quote_cards_enabled` |
| Storage growth from clip audio | ~5 MB per public episode; deleted with the episode or when the show leaves the allowlist; `thestill quote-audio prune` for episodes no longer public |
| Retained originals pile up when the entity branch fails (`DELETE_AUDIO_AFTER_PROCESSING` deployments) | The candidates step deletes after the cut; the 7-day housekeeping sweep deletes the rest and clears the marker; `clips_enabled` off restores today's deletion |
| The on-read upsert of gated `quote_shares` rows becomes a write amplifier on public pages | One statement per response, idempotent, bounded by the gated set (one row per quote ever); skipped under `read_only`; gone once spec #100 mints on write |
| Caption tagging annoys the people quoted | The handle is a removable chip, never automatic posting; operators can blank a handle |
| More anonymous traffic that still does not activate | The follow hook ships in Phase 1; §6 reads the cohort and names the hypothesis before launch |

## Rollback

Both switches off stops new renders and clip jobs within one request;
cached assets keep serving so shared links do not break. Removing the
feature: drop the `/q` routes (links 404), keep the tables (additive, no
other reader), delete `quote_cards/`, `quote_clips/` and `quote_audio/`
with a one-line PathManager-based script. No existing table changes
except the nullable `users.acquired_via` and `podcasts.audio_clips`
columns, both ignorable.

## Decisions

| # | Decision | Why |
|---|---|---|
| D1 | `og:url` on the `/q` page is the short link itself; `<link rel="canonical">` is the episode URL | Facebook and LinkedIn fetch `og:url` and use its tags; pointing it at the episode would show the episode artwork instead of the card. Search engines read the canonical and the `noindex` |
| D2 | Pillow with a bundled font, not headless Chromium or client canvas | Runs on the RPi5 and the small EC2 box; deterministic; previews need a server image anyway (Options B, C) |
| D3 | Clips are an allowlist per show (`audio_clips` default false, switched on with `--audio-clips on`); cards follow `public_pages` and stay opt-out | Putting a publisher's audio into branded video is the one part of this spec that can draw a complaint, and under an opt-out the complaint is the first signal. Text cards match what the public pages already show. Decided 2026-11-23 (was open question Q1) |
| D4 | No QR on image cards; QR on video only | A QR on an X or LinkedIn image is dead weight; on Reels and TikTok it is the only link |
| D5 | The speaker's handle goes in the caption as a removable chip, never on the image, never auto-posted | Tagging drives the reach; the user, not the system, decides to tag a person |
| D6 | Clip audio is cut at the entity stage from the retained original and kept; never re-fetched from the enclosure. With `DELETE_AUDIO_AFTER_PROCESSING` on, clip-eligible episodes keep the original until the cut (§4.1), with a 7-day orphan sweep | Original audio is usually gone by share time, the cut needs resolved speakers so it cannot run earlier, and dynamic ad insertion makes re-fetched audio misaligned by minutes (spec #113) |
| D7 | No auto-follow from a quote link | A URL is not consent (spec #80); a forced follow games spec #111's activation metric |
| D8 | Gated quotes have deterministic ids, and the id-to-quote row is persisted before the link is shown: minted by the quote payloads on read (idempotent upsert) until spec #100's candidates step mints it on write | A truncated hash cannot be reversed, so `/q/<id>` must be a lookup, never a search; deterministic ids let #100 replace the source without changing links. Self-describing ids were rejected: they triple the URL length and episode ids have no short indexed form |
| D9 | The `/q` page is head-only plus the visible link; no server-written quote text in the body | The page is `noindex` and redirects at once; only link previewers read it, and they read the tags. Decided 2026-11-23 (was Q2) |
| D10 | Phase 0 ships on the spec #94 stand-in query and does not wait for spec #100's candidates table | Phase 0 is the test of whether shared quote links get opened; the id scheme is independent of the source, so #100 replaces it without changing links. Decided 2026-11-23 (was Q3) |
| D11 | Phase 4 is decided by use, not by a cohort comparison: it starts only if signed-in readers use *Quote this* (`quote_sheet_open` and `quote_share_actions`) in Phases 0–1 | Phase 4 exists for readers who want their own passage, so their use of the gated version is the direct signal. A quote-referred activation cohort would be a handful of people at current volume (spec #111) and could not be read in four weeks. The cohort is still logged per `docs/experiments.md`. Decided 2026-11-23 (was Q4) |
| D12 | `story` video captions sit in the lower third with a 320 px bottom safe area | Reels and TikTok cover the bottom with their own controls, and centred captions would collide with the waveform. Revisit with the first real render in Phase 3. Decided 2026-11-23 (was Q5) |

## Open questions

The five questions of the first draft are decisions D3 and D9–D12. One
question has opened since:

| # | Question | Default if unanswered |
|---|---|---|
| Q6 | Decision D6 keeps the original audio until the entity stage because a re-fetched enclosure carries other ads and its timestamps drift. Spec #116 has since shipped sync tracks (v1.38.0) that map the transcribed copy onto another copy of the same episode. Could the clip be cut on demand from a re-fetched enclosure aligned on the server with the sync track, with no retention, no `audio_retained_for` column and no orphan sweep? It would also cover episodes whose original is already gone. For it, too: production transcribes through Dalston's direct-URL path, where retention means a second copy of every job's audio (§4.1). Against it: the aligner runs in the browser today, a server port is new work, and a fetch per clip presents to the publisher as a download. | Settle before Phase 2 starts; Phases 0 and 1 use no audio. Until then D6 stands as written |
