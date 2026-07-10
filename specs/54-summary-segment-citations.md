# Summary → Transcript Segment Citations

> **Status:** ✅ Implemented
> **Created:** 2026-07-09
> **Updated:** 2026-07-09 (implemented)
> **Author:** Product & Engineering
> **Related:** [#18 segment-preserving-transcript-cleaning](18-segment-preserving-transcript-cleaning.md) (the `AnnotatedSegment` anchor + `to_blended_markdown` offset), [#23 transcript-playback-sync](23-transcript-playback-sync.md) (click-to-seek / scroll / highlight + `?t=` deep links), [#52 inbox-reader-overlay](52-inbox-reader-overlay.md) (`EpisodeReader` — where tab + seek state now live), [#22 floating-media-player](22-floating-media-player.md) (`PlayerContext.seek`), [#42 robustness-and-failure-mode-hardening](42-robustness-and-failure-mode-hardening.md) (FM-1/6/7 + silent-stale-artefact), [#35 pluggable-file-storage](35-pluggable-file-storage.md) (sidecar persistence)

---

## Executive Summary

Episode summaries are packed with citations like `**Source:** [49:30, 50:45,
01:24:00]`, but they are **inert plain text**. A reader who wants to hear the
moment behind a claim has to eyeball the timestamp, switch to the transcript
tab, scroll by hand, and scrub the player. Meanwhile the app already knows how
to do all of that — [#23](23-transcript-playback-sync.md) shipped
click-to-seek, auto-scroll, segment highlighting, and `?t=<seconds>` deep
links; [`handleSegmentSeek`](../thestill/web/frontend/src/components/EpisodeReader.tsx#L63)
already turns "seconds" into "seek the live player, or start playback at that
offset."

This spec makes summary citations **first-class pointers into the transcript**
rather than timestamp strings. At summary-generation time a deterministic
resolver maps each `[MM:SS]` the LLM emits to the `AnnotatedSegment` that
contains it, and stores a **durable anchor** (`source_segment_ids` + the cited
playback time) — with the positional `segment_id` kept only as a re-resolvable
UI hint — in a citations sidecar. The stored markdown keeps a readable body;
each citation becomes an app deep link. At render time the frontend draws each
link as a clickable chip; clicking seeks the player to the **cited** time and
scrolls/highlights the containing segment.

Inverting the model — pointer is truth, timestamp is a label — removes three
failure surfaces: no free-text regex in the browser, no `MM:SS`→seconds
arithmetic at click time, and no drift between the segment shown and the segment
sought. The **displayed timestamp is preserved verbatim** (`[49:30]` stays
`[49:30]`); we do not snap it to a segment boundary.

Per the user's direction, this ships with a deterministic **backfill command**
so existing summaries can be brought onto the same citation model without a new
LLM call. The command reuses the same resolver as future summary writes; it is
not a second implementation.

### Review revisions (2026-07-09)

This draft was revised after a code review. Changes from the first draft:

1. **Durable anchor is `source_segment_ids` + `cited_playback_s`**, not the
   positional `AnnotatedSegment.id` (which the model says is "NOT stable across
   algorithm changes" — [annotated_transcript.py:80](../thestill/models/annotated_transcript.py#L80)).
   `segment_id` survives only as a cached, re-resolvable hint.
2. **Offset is explicit.** The LLM sees playback-time timestamps
   (`to_blended_markdown` adds `playback_time_offset_seconds` —
   [annotated_transcript.py:259](../thestill/models/annotated_transcript.py#L259)),
   so the resolver maps back to raw segment time with `playback_s − offset`.
3. **Deep links, not a custom `cite:` scheme.** `ReactMarkdown` strips unknown
   protocols via `urlTransform`, so `cite:c3` would be blanked before our
   renderer sees it. Use `?t=2970&cite=c3` (reuses the existing `?t=` path);
   custom scheme + `urlTransform` allow-list kept only as a fallback.
4. **Displayed timestamp is preserved**, never snapped to segment start.
5. **Frontend target is `EpisodeReader`** (tab + seek moved there in #52), and
   the scroll-to-segment contract is made explicit (a prop, not the viewer's
   internal `scrollToKey`).
6. **Staleness guard.** Sidecar carries `summary_sha256`, `episode_id`,
   `transcript_algorithm_version`, `playback_time_offset_seconds`; the API
   drops or re-resolves citations when they no longer match the served
   summary/transcript.
7. **Markdown-aware rewrite, not whole-document regex** — the implementation
   walks fenced/non-fenced lines and bracket groups, skipping inline code,
   fenced code, existing links/images/reference links, and `[a-b]` ranges.
   A full AST pass can replace it later if a Python Markdown parser becomes a
   direct runtime dependency.
8. **Backfill is in scope.** Existing summaries get a no-LLM resolver command
   (`thestill resolve-summary-citations`) with dry-run/force filters.
9. **Prompt changes stay small.** No section-format rewrite; a narrow citation
   hygiene instruction is allowed if it improves parseability without changing
   the summary contract.
10. **Segment id does not solve dynamic-ad drift.** It is the right UI anchor
    for transcript scroll/highlight, but live-audio drift remains an audio-source
    mismatch handled by the existing drift warning/fail-closed behavior.

---

## Motivation

1. **The link already exists conceptually; only the wiring is missing.**
   Summary timestamps and transcript segments describe the same audio moments;
   today the correspondence is re-derived by the human every time.
2. **Timestamp strings are a fragile join key.** Parsing `[MM:SS]` in the
   browser means a regex over untrusted LLM prose, and every click re-guesses
   which segment a bare timestamp falls in — recomputing a mapping the backend
   can compute once, deterministically, with the transcript in hand.
3. **Pointers are portable; strings are not.** A stored `source_segment_ids`
   anchor is usable by the web reader, by MCP anchor tools
   ([#30](30-mcp-anchors-and-entity-discovery.md)), and by future exports; a
   `[MM:SS]` string is only usable by whoever re-implements the parser.

---

## Goals

1. Every citation the summarizer emits resolves, at generation time, to a
   concrete transcript segment, persisted as a **durable `source_segment_ids` +
   cited-playback-time anchor** in a sidecar alongside the summary markdown.
2. The summary markdown body stays human-readable; citations are app deep links
   that survive any markdown renderer.
3. The web reader renders each citation as a clickable chip that **preserves the
   cited timestamp label**; clicking seeks/plays the episode at the cited time.
4. Clicking a citation (phase 3) jumps to the transcript, scrolls the containing
   segment into view, and highlights it — reusing [#23](23-transcript-playback-sync.md)
   machinery through an explicit scroll-to-segment contract.
5. Resolution is deterministic, validated, per-citation isolated (one bad
   timestamp degrades to plain text), and stale-safe (mismatched sidecars are
   ignored, never silently wrong).
6. Both summary write paths (CLI and queued stage) share one resolve-and-persist
   helper — no parallel implementations.
7. Existing summaries can be backfilled by the same pure-Python resolver,
   without calling the LLM or changing transcript data.

## Non-goals

- **Major summary prompt or section-format rewrite.** The LLM keeps emitting
  `[MM:SS]` / `[HH:MM:SS]` timestamps (Option B), and the existing sections stay
  intact. A small citation-hygiene prompt tweak is allowed, but not a structural
  prompt redesign and not "LLM emits segment ids".
- **Word-level citation UI.** Citations anchor to a segment for v1. If the
  resolved `AnnotatedSegment` already carries `source_word_span`, copy it into
  the sidecar for future precision; do not create or edit word anchors here.
- **Editing transcript data.** Citations are summary-specific derived metadata,
  so they live in a sidecar. Resolution is pure Python over the existing
  annotated transcript and never requires a new LLM call.

---

## Background — current shape

### Summary generation (two write paths)

`TranscriptSummarizer.summarize(transcript_text, metadata)`
([post_processor.py:280](../thestill/core/post_processor.py#L280)) takes the
cleaned-transcript **markdown** and returns summary markdown; it does **not**
persist — the caller does, via `FileStorage` ([#35](35-pluggable-file-storage.md)).
Two callers must not drift ([#42](42-robustness-and-failure-mode-hardening.md)
FM-6):

- CLI: [cli.py:2034–2200](../thestill/cli.py#L2034) (`summarize` command).
- Queue: [task_handlers.py:624 `handle_summarize`](../thestill/core/task_handlers.py#L624)
  (the `SUMMARIZE` stage).

Both write `{clean_stem}_summary.md` under `path_manager.summaries_dir()`
(optionally namespaced by `<podcast_slug>/`).

### The transcript anchor and the offset

The segmented sidecar (`AnnotatedTranscript`,
[annotated_transcript.py](../thestill/models/annotated_transcript.py)) is
reachable via `episode.clean_transcript_json_path` →
`path_manager.clean_transcript_file(...)`. Each
[`AnnotatedSegment`](../thestill/models/annotated_transcript.py#L75) has:

- `id: int` — positional, "deterministic per `(input, algorithm_version)` but
  **NOT stable across algorithm changes**" ([:80](../thestill/models/annotated_transcript.py#L80)).
- `source_segment_ids: List[int]` — the **durable anchor** (raw `Segment.id`s
  that survive re-cleans); "anything that must persist across re-cleans keys off
  this field."
- `start: float`, `end: float` — seconds in **raw transcript time**, inclusive.
- `source_word_span` — reserved for future word-level anchoring.

`AnnotatedTranscript.playback_time_offset_seconds` is the offset between raw
transcript time and player/playback time. **The markdown the summarizer sees is
already in playback time**: `to_blended_markdown` emits
`segment.start + playback_time_offset_seconds`
([:259](../thestill/models/annotated_transcript.py#L259)). So the LLM's `[MM:SS]`
are playback-time labels, and the player's `currentTime` (and the existing `?t=`
deep link) is also playback time.

### The seek/scroll/highlight path (built by #23, relocated by #52)

- [`EpisodeReader`](../thestill/web/frontend/src/components/EpisodeReader.tsx#L63)
  now owns `activeTab` and the seek handler (shared by the standalone page and
  the inbox overlay). `SegmentedTranscriptViewer` accepts `onSeekRequest`,
  highlights the active segment, and auto-scrolls via `useAutoScrollFollow`;
  `follow.scrollToKey(...)` is **internal** to the viewer.
- `?t=<seconds>` deep links are consumed by
  [`useDeepLinkSeek`](../thestill/web/frontend/src/hooks/useDeepLinkSeek.ts),
  which fires `onSeek(seconds)` once per key change.
- [SummaryViewer.tsx](../thestill/web/frontend/src/components/SummaryViewer.tsx)
  renders summary markdown with `ReactMarkdown` + `remarkGfm` and **no custom
  renderers** — citations pass through as text today.
- Summary and transcript are **tabs** in `EpisodeReader`.

---

## Design

### The three decisions (locked)

1. **Option B — resolve, don't ask.** The LLM keeps emitting timestamps; a
   deterministic post-processor converts them to pointers. (Rejected: LLM emits
   segment ids — hallucinated opaque ints, needs validation anyway, loses the
   human-eyeballable cross-check, costs a prompt rewrite.)
2. **Anchor on the durable fields.** Primary anchor = `source_segment_ids` +
   `cited_playback_s`. `segment_id` is stored as `segment_id_hint` — a cached
   UI value for the transcript currently served by the API. If the sidecar was
   resolved against an older `algorithm_version`, the API re-resolves from
   `source_segment_ids`; the client does not repair stale anchors.
3. **Keep the markdown body; citations are app deep links.** A resolved citation
   is rewritten to `[49:30](?t=2970&cite=c3)` — reusing the existing `?t=`
   mechanism, portable to any markdown renderer, and not tripping
   `ReactMarkdown`'s protocol filter. (Fallback if a same-page relative link is
   awkward: a `cite:` scheme plus a narrow `urlTransform` allow-list.)

### Offset handling (resolution math)

Given an emitted playback-time timestamp `p` (seconds) and the transcript's
`offset = playback_time_offset_seconds`:

- `raw_t = p − offset` is the raw transcript time.
- Resolve to the `content`-kind segment whose `[start, end]` contains `raw_t`.
  If `raw_t` lands in a trimmed gap, snap to the nearest segment start within a
  tolerance (default 5 s); otherwise mark **unresolved**.
- `cited_playback_s = p` (what we seek to and label with — **unchanged**).
- `target_playback_s` defaults to `cited_playback_s`; kept as a distinct field
  only so a future policy could seek to a segment boundary without touching the
  label.

Range check uses `[0, transcript_source_duration_s + offset]`; out-of-range →
unresolved (never clamped) — [#42](42-robustness-and-failure-mode-hardening.md)
FM-7.

### Dynamic-ad drift

Segment anchors solve **transcript navigation** drift, not live-audio drift.
When a host later serves a different MP3 because dynamic ads changed, the same
playback second may no longer contain the same words. In that case:

- `segment_id_hint` / `source_segment_ids` still scroll to the right transcript
  row.
- `cited_playback_s` still seeks to the timestamp the summary displays.
- The existing transcript/audio drift warning remains the user-facing signal
  that audio may not match the transcript.

Do not pretend segment ids make the live audio immutable. They make the
summary-to-transcript join robust; the audio-source mismatch is handled by the
existing drift detection and fail-closed citation validation.

### Data model — citations sidecar

Stored next to the summary markdown, path derived by convention:
`…_summary.md` → `…_summary.citations.json`, written through `FileStorage`.

```jsonc
{
  "schema_version": 1,
  "episode_id": "…",
  "summary_sha256": "…",                 // hash of the summary .md this describes
  "clean_transcript_json_path": "prof-g-markets/…_transcript.json",
  "transcript_algorithm_version": "v1",  // AnnotatedTranscript.algorithm_version
  "playback_time_offset_seconds": 0.0,   // offset used at resolution time
  "citations": [
    {
      "id": "c3",
      "raw_label": "49:30",              // preserved verbatim for display
      "cited_playback_s": 2970.0,        // seek target (playback time)
      "target_playback_s": 2970.0,       // == cited by default
      "segment_id_hint": 89,             // AnnotatedSegment.id — cached UI hint
      "source_segment_ids": [142],       // durable anchor
      "source_word_span": null,          // copy from segment when present; else null
      "resolved": true
    }
    // unresolved: { "id":"c8", "raw_label":"1:59:59", "resolved": false }
  ]
}
```

### Resolver (implemented in `thestill/core/summary_citations.py`)

Pure function — takes summary markdown + a loaded `AnnotatedTranscript`, returns
`(rewritten_markdown, CitationsSidecar)`:

1. Walk the markdown line-by-line, preserving all text outside the rewritten
   timestamp tokens byte-for-byte. Fenced code blocks are skipped wholesale.
2. In non-fenced lines, scan bracket groups while skipping inline code spans,
   existing non-citation inline/reference links, images, and non-timestamp
   `[a-b]` ranges. Existing `?t=&cite=` links emitted by this resolver are
   treated idempotently so sidecars can be rebuilt without nesting links.
3. Inside eligible bracket groups, find timestamp tokens
   `\b\d{1,2}:\d{2}(?::\d{2})?\b` (including comma lists — each timestamp
   becomes its own citation).
4. Parse `MM:SS` / `HH:MM:SS` → playback seconds `p`; resolve via the offset
   math above.
5. Rewrite each **resolved** token as a markdown link
   `[raw_label](?t=<cited_playback_s>&cite=cN)`; leave unresolved tokens as
   plain text.
6. Emit the sidecar (including `summary_sha256` of the rewritten markdown).

Guardrails (FM-1/FM-7): never raises on bad input — a bad timestamp degrades
that one citation; a per-summary cap (default 500) bounds pathological input.

Implementation note: this deliberately avoids adding a backend Markdown AST
dependency in v1. It is still Markdown-aware rather than a whole-document regex:
the scanner only rewrites bracketed timestamp citation text and leaves markdown
syntax it does not own untouched.

### Wiring (shared helper + backfill — no path drift)

One helper both write paths call, e.g.
`summary_citations.resolve_and_persist(...)` or a `SummaryService` method:
`summarize()` → load annotated transcript (if `clean_transcript_json_path`
present) → resolve → persist `_summary.md` **and**
`_summary.citations.json` via `FileStorage`. If the annotated sidecar is absent
(legacy/raw-only), skip resolution and write the summary unchanged — citations
are additive, never required.

The same helper powers `thestill resolve-summary-citations`, which backfills
existing summaries without a new LLM call:

- default `--dry-run` reports candidate episodes, resolved/unresolved counts,
  and whether the summary markdown would change;
- `--write` persists the rewritten summary and sidecar;
- `--force` replaces an existing sidecar/rewrite even when the hash matches;
- filters: `--podcast`, `--episode`, `--limit`, `--only-missing`;
- every write uses `FileStorage`, so local/S3 behavior matches the normal
  summarize stage.

### API

Extend the summary endpoint
([api_podcasts.py:381](../thestill/web/routes/api_podcasts.py#L381)):

```jsonc
{
  "episode_id": "…", "episode_title": "…",
  "content": "…markdown with [49:30](?t=2970&cite=c3)…",
  "available": true,
  "citations": [ { "id":"c3", "raw_label":"49:30", "cited_playback_s":2970.0,
                   "segment_id_hint":89, "source_segment_ids":[142],
                   "resolved":true }, … ]   // null when no valid sidecar
}
```

**Staleness guard:** before returning `citations`, the API compares the
sidecar's `summary_sha256` against the served summary, checks `episode_id`, and
checks `playback_time_offset_seconds` against the current annotated transcript.
Hash/episode/offset mismatches return `citations: null` (and log), so the
reader falls back to plain timestamp labels.

If only `transcript_algorithm_version` differs, the API attempts server-side
re-resolution from `source_segment_ids` into the current annotated transcript
and returns refreshed `segment_id_hint` values when resolved citations can be
mapped. Any citation that cannot be re-resolved is omitted from the returned
`citations` list; `SummaryViewer` treats unknown citation links as plain label
text. The client never owns stale-anchor repair.

### Frontend

- **Types** ([api/types.ts](../thestill/web/frontend/src/api/types.ts)): add
  `SummaryCitation` + `citations: SummaryCitation[] | null`.
- **`SummaryViewer`**: accept `citations` + `onCite(citation)`. Override
  `ReactMarkdown`'s `a` renderer: if the `href` is our citation deep link
  (`?t=…&cite=cN`), render a `<CitationChip>` labelled with the link's own text
  (`raw_label`, preserved) whose click calls `onCite`; other links render
  normally. No custom URL scheme, so `urlTransform` leaves it intact.
- **`EpisodeReader`** ([EpisodeReader.tsx](../thestill/web/frontend/src/components/EpisodeReader.tsx#L63)):
  owns `onCite`: seek/play with `target_playback_s ?? cited_playback_s` (already
  playback time — no offset re-application), clear entity filters that could
  hide the target, switch to the transcript tab, then drive a **new
  `scrollToSegmentId` contract** on `SegmentedTranscriptViewer` (a
  `{ segmentId, nonce }` prop that maps to the viewer's internal
  `follow.scrollToKey`). The API already returned a current `segment_id_hint`;
  the client does not re-resolve durable anchors. Highlight follows the
  existing active-segment mechanism.

### Prior art / standards (considered, not adopted as the store)

- **W3C Media Fragments** (`#t=`) covers media time only — no transcript
  scroll/highlight; our `?t=` deep link already occupies this role.
- **WebVTT** is the closest timed-cue model and a plausible **export** format,
  but `AnnotatedTranscript` is already richer, so it's inspiration, not storage.
- **URL text fragments** are text-brittle and can't start playback — poor fit.

The app-level sidecar stays authoritative; these inform an optional future
export, not the citation store.

---

## Failure modes ([#42](42-robustness-and-failure-mode-hardening.md))

- **FM-1 (per-item isolation):** one unresolvable/out-of-range timestamp
  degrades to plain text; never fails the summarize task or drops siblings.
- **FM-6 (parallel-path drift):** CLI and queue call the same resolve-and-persist
  helper; unit coverage exercises the helper/backfill path, with a broader
  CLI-vs-queue parity test left as useful follow-up.
- **FM-7 (unsanitized LLM output):** untrusted markdown — rewrite only
  Markdown-aware bracketed timestamp text, range-validate (no clamp), cap
  counts, no arbitrary URL schemes rendered clickable.
- **Silent-stale artefact (FM-2 family):** the sidecar is a derived artefact of
  a specific summary + transcript; `summary_sha256` + episode/offset checks
  fail closed, while algorithm-version-only drift is repaired server-side from
  durable raw anchors or dropped per citation.
- **FM-3 (mixed units):** everything is seconds (float); resolution keeps raw vs
  playback time explicit via `offset`.

---

## Phases

1. **Implemented — backend resolution + persistence + backfill.**
   `summary_citations.py` (Markdown-aware rewrite + offset math), sidecar model,
   shared write helper in both paths,
   API surfaces `citations` with the staleness/re-resolution guard, and
   `thestill resolve-summary-citations` backfills existing summaries. Prompt
   format remains unchanged.
2. **Implemented — clickable chips.** `SummaryViewer` chip + `onCite` renders
   resolved citation links and degrades unknown/stale ids to plain text.
3. **Implemented — cross-pane jump.** `EpisodeReader` seeks/plays, switches to
   the transcript tab, clears entity filters, and passes `scrollToSegmentId` to
   `SegmentedTranscriptViewer`.

---

## Testing

Implemented coverage:

- `tests/unit/core/test_summary_citations.py`: resolver rewrite/offset/range
  behavior, idempotent rebuild from existing citation links, API re-resolution
  across algorithm changes, and backfill write.
- `thestill/web/frontend/src/components/SummaryViewer.test.tsx`: citation chips,
  stale/unknown citation fallback, and normal markdown links.
- `thestill/web/frontend/src/components/SegmentedTranscriptViewer.test.tsx`:
  explicit segment scroll target.

Remaining useful follow-up coverage:

- **Resolver units:** inside a segment; trimmed-gap snap within tolerance;
  out-of-range → unresolved; comma list → N citations; `HH:MM:SS` vs `MM:SS`;
  **offset applied** (playback→raw); Markdown safety (existing links / code
  spans / `[a-b]` ranges untouched); no annotated sidecar → markdown unchanged,
  no sidecar written.
- **Determinism:** same inputs → byte-identical sidecar + rewritten markdown
  (positional ids; no `Date.now`/random).
- **Both-paths parity:** CLI and `handle_summarize` produce equivalent sidecars.
- **Backfill:** dry-run reports counts without writes; write mode persists the
  same sidecar/rewrite shape as the normal summarize path; `--force` replaces
  existing sidecars deterministically.
- **Staleness:** edited summary (hash mismatch) or offset mismatch → API
  returns `citations: null`; `algorithm_version` mismatch with compatible
  `source_segment_ids` → refreshed segment hints; incompatible anchors →
  per-citation plain text.
- **Frontend:** resolved deep link → chip with `raw_label` preserved; click →
  `onCite` with the right citation; unresolved → plain text; phase-3 scroll
  targets the containing segment returned by the API.

---

## Open questions

1. **Sidecar path vs DB column.** Convention-derived path (chosen) vs a nullable
   `summary_citations_path` column mirroring `clean_transcript_json_path`
   (explicit presence, costs an alembic migration).
2. **Grouped `Source:` lists.** One chip per timestamp (chosen) vs a single
   grouped citation with multiple targets.
3. **Deep link vs custom scheme.** `?t=&cite=` (chosen) reuses `?t=` and dodges
   `urlTransform`; is a relative `?…` href inside markdown ergonomic in all
   render contexts, or do we prefer the `cite:` scheme + allow-list after all?
