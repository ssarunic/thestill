# Transcript and Summary Export

> **Status:** 📝 Draft
> **Created:** 2026-09-30
> **Updated:** 2026-09-30
> **Priority:** Medium
> **Author:** Product & Engineering
> **Related:** [#18 segment-preserving-transcript-cleaning](18-segment-preserving-transcript-cleaning.md), [#42 robustness-and-failure-mode-hardening](42-robustness-and-failure-mode-hardening.md), [#54 summary-segment-citations](54-summary-segment-citations.md), [#76 episode-detail-page-hierarchy](76-episode-detail-page-hierarchy.md), [#78 remote-mcp-access](78-remote-mcp-access.md), [#82 summary-entity-links](82-summary-entity-links.md), [#87 episode-platform-links](87-episode-platform-links.md)

---

## Executive Summary

A reader can see an episode's summary and transcript but cannot take them
anywhere. There is no copy button, no download, and selecting the page by
hand picks up buttons, entity chips and player chrome.

This spec adds one **Export** control to the reader's Summary | Transcript
tab bar. It opens a small menu:

| Action | Summary tab | Transcript tab |
|---|---|---|
| Copy as Markdown | ✓ | ✓ |
| Copy as plain text | ✓ | ✓ |
| Download Markdown (`.md`) | ✓ | ✓ |
| Download subtitles (`.srt`, `.vtt`) | — | ✓ |

Every action is served by one server-side renderer per artifact, exposed as
`GET …/summary/export` and `GET …/transcript/export`. Copy and download
fetch the same bytes, so what you paste is what you save.

The export is a **document, not a dump of the stored file**. The stored
summary is a fragment: no title, no source, and every citation is a
relative link (`?t=36.578&cite=c0`) that is dead outside the app. The
export wraps it with a header (episode, podcast, date, duration, links),
the episode description, the body with citations rewritten to absolute
deep links, and a footer with the show-notes links and a provenance line.
The transcript gets the same header and footer around the cleaned text.

## Why people want this

1. **Ask an LLM about it.** The most common case. People paste a
   transcript into Claude or ChatGPT to ask their own questions, or a
   summary to draft something from it. They want copy, not download, and
   dense text: speakers kept, markup and timestamps optional. The MCP
   tools from spec #78 serve power users; everyone else pastes.
2. **Quote or cite it.** A newsletter, a post, a research note. They want
   the speaker, the timestamp and a link that opens the episode at that
   moment.
3. **Keep it.** Obsidian, Readwise, a notes folder, a shared drive. They
   want a Markdown file with front matter so it files itself and survives
   thestill.
4. **Send it to someone who does not use thestill.** Mostly the summary.
   It has to make sense on its own: what episode, which show, where to
   listen, and who wrote it (a model, not the host).
5. **Reuse the audio.** Clip makers and accessibility users want
   subtitles aligned to the episode audio. Segments already carry start
   and end times, so SRT and VTT cost little.

## Current state

1. **Transcript payload.** `GET /api/podcasts/{p}/episodes/{e}/transcript`
   returns JSON. When annotated segments exist it ships them and blanks
   `content` to halve the payload
   ([api_podcasts.py:425](../thestill/web/routes/api_podcasts.py#L425)).
   The reader renders from segments, so there is no ready-made text for
   the client to copy.
2. **Transcript text rendering already exists.**
   `AnnotatedTranscript.to_blended_markdown()` renders segments to the
   `[MM:SS] **Speaker:** text` form, drops filler, marks ad breaks, and
   accepts `exclude_kinds`
   ([annotated_transcript.py:197](../thestill/models/annotated_transcript.py#L197)).
   `get_transcript_for_episode` falls back to it for raw-only episodes
   ([podcast_service.py:701](../thestill/services/podcast_service.py#L701)).
3. **Summary is a fragment with relative citation links.** The stored
   Markdown starts at `## 1. 🎙️ The Gist`. Citations are
   `[03:25](?t=205&cite=c6)`; the reader turns them into play buttons via
   the citations sidecar
   ([SummaryViewer.tsx:133](../thestill/web/frontend/src/components/SummaryViewer.tsx#L133)).
   Entity links (spec #82) are added client-side and exist only in the
   reader.
4. **Metadata is all available.** Episode `title`, `pub_date`,
   `duration`, `website_url`, plain-text `description` and
   `description_links`
   ([podcast.py:381](../thestill/models/podcast.py#L381)); platform links
   from spec #87; summary language variants and the canonical language
   from the summary endpoint.
5. **Absolute URLs.** `PUBLIC_BASE_URL` is configured in prod for OAuth
   redirects ([config.py:551](../thestill/utils/config.py#L551)). The
   reader's deep-link contract is `?t=<whole seconds>`
   ([useDeepLinkSeek.ts:33](../thestill/web/frontend/src/hooks/useDeepLinkSeek.ts#L33)).
6. **No slot in the header.** The episode ActionRow already holds Play,
   Watch video, Share and Send to my inbox
   ([EpisodeHeader.tsx:110](../thestill/web/frontend/src/components/episode-header/EpisodeHeader.tsx#L110)).
   The tab bar has room at its right end, next to the summary language
   toggle ([EpisodeReader.tsx:598](../thestill/web/frontend/src/components/EpisodeReader.tsx#L598)).
7. **Clipboard patterns exist.** `ShareButton` does Web Share with a
   copy-link fallback and toasts
   ([ShareButton.tsx](../thestill/web/frontend/src/components/ShareButton.tsx)).

## Design

### 1. The export document

Both artifacts share one envelope. Markdown form, summary shown:

```markdown
---
title: "Coding agents in Obsidian ft. Artem Zhutov"
podcast: "Tool Use - AI Conversations"
published: 2026-01-12
duration: "45:53"
language: en
kind: summary
url: https://thestill.me/podcasts/tool-use-ai-conversations/episodes/coding-agents-in-obsidian-ft-artem-zhutov
episode_website: https://example.com/ep/76
exported: 2026-09-30
---

# Coding agents in Obsidian ft. Artem Zhutov

Tool Use - AI Conversations · 12 January 2026 · 46 min
[Open in thestill](https://thestill.me/podcasts/…) · [Episode website](https://example.com/ep/76) · [Apple Podcasts](https://podcasts.apple.com/…)

> Mike Bird talks with Artem Zhutov about turning Obsidian into a
> personal operating system…

## 1. 🎙️ The Gist

…

* Stop trying to build the "perfect" system… [03:25](https://thestill.me/podcasts/…/episodes/…?t=205)

---

**Links from the show notes**

- [Artem on YouTube](https://youtube.com/@…)
- [WhisperFlow](https://…)

Summary generated by thestill from the episode transcript on 2026-01-13.
It is a model's reading of the conversation, not the hosts' own words.
Timestamps open the episode at that moment.
```

Rules:

- **Front matter only in downloads.** Pasted into a chat or a message,
  YAML front matter is noise. `Download Markdown` includes it; `Copy as
  Markdown` starts at the `#` title. The renderer takes
  `frontmatter=true|false`; the client sets it.
- **Header.** Title, then one line of podcast, date and rounded duration,
  then a links line. The links line carries the thestill URL, the episode
  website when present, and spec #87 platform links that resolved to a
  URL. Missing values are omitted, never printed as "None" or "N/A".
- **Description.** The first paragraph of the plain-text description,
  capped at 600 characters on a word boundary, as a blockquote. The full
  description stays in thestill; the export needs orientation, not the
  show notes' sponsor copy.
- **Body.** Summary: the stored Markdown, with citation links rewritten
  (§3). Transcript: rendered from segments (§2).
- **Footer.** A rule, the show-notes links from `description_links`
  (deduplicated by URL, capped at 25, skipped when empty), then the
  provenance line. The summary line says it is model-generated and names
  the summary date. The transcript line says "Transcript generated by
  thestill from the episode audio" and names the transcript type. A raw
  transcript adds "not yet cleaned; expect recognition errors."
- **Summary language.** The summary export takes `lang` like the summary
  endpoint and exports the variant the reader is showing. It never
  triggers a translation: a missing variant returns 404, and the client
  only offers export for the language already on screen. Header labels
  stay English in v1.

### 2. Transcript rendering

The transcript body comes from the annotated transcript when present, the
same source the reader renders. It falls back to the cleaned Markdown
file, then to the raw-transcript path the service already uses.

| Format | Content |
|---|---|
| Markdown | `to_blended_markdown(exclude_kinds={"ad_break", "music"})`: `[MM:SS] **Speaker:** text` per speaker block. Timecodes stay plain text in the body; one deep link every ~5 minutes would be noise. The header link opens the episode. |
| Plain text | One paragraph per speaker turn, `Speaker: text`, no timecodes, no Markdown. Ads, music and filler excluded. This is the LLM-paste format: fewer tokens than Markdown and nothing a model mistakes for structure. |
| SRT / VTT | One cue per segment of every kind except `filler`, ad reads included, so cues stay aligned with the audio. Speaker as a prefix (`Artem Zhutov: …`), and as a `<v Artem Zhutov>` voice tag in VTT. Segments longer than 7 s or 84 characters split on word boundaries using `source_word_span` when available, else by proportional time. |

Details:

- **Timeline.** All times are segment start plus
  `playback_time_offset_seconds`, the same timeline as the reader's
  timecodes and `?t=`. The subtitle footer note and the download
  filename carry no promise beyond that: a listener's copy of the audio
  can differ where the feed uses dynamic ad insertion.
- **Speaker labels** are exported as the reader shows them. A null
  speaker continues the previous block. Generic labels (`SPEAKER_01`,
  `Unknown`) pass through unchanged; about half the local cleaned
  transcripts still contain one. Fixing them belongs to the speaker data
  quality work, and the export improves when the reader does.
- **No transcript** returns 404 with the same "not available" message the
  reader shows, and the menu hides transcript actions.

### 3. Summary citation rewriting

Each citation link `[label](?t=…&cite=cN)` becomes
`[label](<episode url>?t=<seconds>)`:

1. When the citations sidecar validates for the served content, the
   seconds come from the resolved citation, the moment the reader's
   play button would start at.
2. Otherwise the renderer floors the `t` in the href.
3. A link with neither becomes plain `[label]` text.

`cite=` is dropped; it means nothing outside the reader. Plain text keeps
the `[03:25]` label and drops the URL. Other links in the summary pass
through untouched. Entity links from spec #82 are client-side and do not
appear; the export needs none.

The episode URL is `PUBLIC_BASE_URL` plus the reader path
`/podcasts/{podcast_slug}/episodes/{episode_slug}`. When
`PUBLIC_BASE_URL` is empty, as in local development, the renderer uses
the request's base URL.

### 4. API

```text
GET /api/podcasts/{podcast_slug}/episodes/{episode_slug}/summary/export
    ?format=md|txt            default md
    &lang=<code>              default canonical
    &frontmatter=true|false   default false; md only

GET /api/podcasts/{podcast_slug}/episodes/{episode_slug}/transcript/export
    ?format=md|txt|srt|vtt    default md
    &frontmatter=true|false   default false; md only
```

- **Response.** The raw text, not JSON. Content types are
  `text/markdown`, `text/plain`, `application/x-subrip` and `text/vtt`,
  all with `charset=utf-8`.
- **Filename.** Always sent as `Content-Disposition: attachment` with
  `{podcast-slug}--{episode-slug}--{summary|transcript}.{ext}`, each slug
  cut to 60 characters, plus the RFC 5987 `filename*` form. Copy ignores
  the header; `fetch` still reads the body.
- **Caching.** The content-hash ETag used by the JSON endpoints,
  through a text variant of `etag_json_response`. Both artifacts are
  write-once per language, so repeat copies revalidate cheaply.
- **Auth.** The router-level session requirement already covers these
  paths. A same-origin `<a href download>` sends the cookie, so download
  needs no blob handling.
- **Code layout.** One `ExportService` in `thestill/services/` holding
  the envelope and both renderers, built on
  `get_segmented_transcript_for_episode`, `get_summary_for_episode` and
  `get_summary_citations_for_episode`. Routes stay thin. MCP gets a
  `format` argument on `get_summary` and `get_transcript` in Phase 3 and
  calls the same service, so no second formatter appears.

### 5. Reader control

- **Placement.** One 44 px icon button with an export glyph at the right
  end of the tab bar, before the summary language toggle when both show.
  The menu offers the actions for the active tab only, so a label like
  "Copy as Markdown" never has to say which artifact.
- **Menu items**, in order: Copy as plain text, Copy as Markdown,
  Download Markdown, and on the transcript tab Download subtitles (SRT)
  and Download subtitles (VTT). Plain text leads because pasting into an
  assistant is the main case.
- **Availability.** The button is hidden while the tab has nothing to
  export: summary "not yet summarized", no transcript, a translation in
  progress. It never renders disabled with no explanation.
- **Feedback.** Success toasts "Summary copied" or "Transcript copied".
  A failure toasts the reason ("Couldn't copy. Try Download instead.").
  Downloads need no toast; the browser shows them.
- **Keyboard.** The menu is a standard menu button: Enter or Space opens
  it, arrows move, Escape closes and returns focus. The item labels are
  the accessible names.

### 6. Clipboard mechanics

Safari only grants clipboard writes inside the user gesture. An
`await fetch(…)` before `navigator.clipboard.writeText` loses the gesture
and fails. The copy handler therefore:

1. Calls `navigator.clipboard.write([new ClipboardItem({ 'text/plain': promise })])`
   synchronously in the click, where `promise` resolves to a `Blob` of
   the fetched export. Current Safari and Chromium accept a promise
   value; check the support floor when implementing.
2. Falls back to `writeText(await fetch…)` where `ClipboardItem` or
   promise values are missing. That works where transient activation
   outlasts the fetch, which a cached ETag response makes likely.
3. Toasts the failure and points at Download if both fail.

Copy as Markdown writes `text/plain` only. Writing `text/html` as well
would paste formatted text into Google Docs but a surprise into editors
that prefer HTML; v1 keeps one representation.

Large transcripts are fine: a three-hour episode is about 200 KB of
text, far below clipboard limits.

### 7. Mobile

On a phone, a downloaded `.md` file lands somewhere few people look. When
`navigator.canShare({ files })` is true, the download items become
**Share Markdown** and **Share subtitles**. They build a `File` from the
same export response and call `navigator.share({ files, title })`, so
the user can send it to Notes, Files, Obsidian or Messages. Where file
sharing is unavailable, the items stay downloads. Copy is unchanged on
mobile.

## Edge cases

- **Failed or partially processed episode.** Only what exists exports. A
  transcribed but unsummarized episode shows the export button on the
  transcript tab only.
- **Raw transcript.** Exports with the provenance warning in §1. SRT and
  VTT are still offered because raw segments carry times.
- **Video episodes.** Unchanged. Times follow the same logical timeline.
- **Imported and ad-hoc episodes** with no podcast website or
  description export without those parts.
- **Untrusted text.** Titles, descriptions and summary text are LLM or
  feed content. The renderer strips control characters (spec #42's
  unsanitized-output rule), escapes `"` and newlines in YAML values, and
  drops `javascript:` and `data:` URLs from show-notes links.
- **Very long description links lists** stop at 25 with "…and N more on
  the episode page."

## Non-goals

- PDF, DOCX or HTML downloads.
- Bulk export of several episodes, a podcast or the inbox.
- Exporting with the reader's entity filter or a selection applied.
  Copying a single passage stays the browser's native selection.
- Per-segment "copy quote with link". It is a good follow-up and would
  reuse §3's link builder.
- Public, unauthenticated export links.
- Translated header labels.

## Phases

1. **Markdown and plain text.** `ExportService`, both endpoints with
   `md` and `txt`, the tab-bar menu with Copy and Download, clipboard
   handling from §6, and tests.
2. **Subtitles and mobile share.** `srt` and `vtt` with cue splitting,
   and the Web Share file path from §7.
3. **MCP parity.** A `format` argument on the `get_summary` and
   `get_transcript` MCP tools, backed by the same service.

## Testing

- **Renderer unit tests** on fixtures in `tests/fixtures/`: a summary
  with a valid sidecar, one with a stale sidecar, and one with no
  citations. A cleaned, a raw and a segment-less transcript. Assert
  absolute links, `cite=` removal, front-matter escaping, footer caps and
  omitted empty fields.
- **Golden files** for each format of one short real episode, so format
  drift shows up in review as a diff.
- **SRT/VTT validation**: monotonic cue times, no cue over 7 s, and a
  parse round-trip through a subtitle parser in tests.
- **Route tests**: content types, `Content-Disposition` with a
  non-ASCII title, 404s, ETag revalidation, `lang` without translation
  side effects, and auth required.
- **Frontend tests**: menu items per tab, hidden states, the
  `ClipboardItem` path and its fallback, and the share path behind
  `canShare`.
- **Manual**: paste plain text into Claude and ChatGPT, paste Markdown
  into Obsidian and Slack, open an `.srt` in VLC against the episode
  audio, and share a file from iOS Safari.

## Open questions

1. Should Copy as Markdown include the description and footer, or only
   the title line and body? The spec says yes to both, since pasted
   summaries otherwise lose their provenance. It is worth checking
   against real pastes in Phase 1.
2. Should the transcript Markdown carry a deep link on each timecode? It
   is useful for citing and heavy for reading. The spec says no and
   leaves it for the per-segment quote follow-up.
