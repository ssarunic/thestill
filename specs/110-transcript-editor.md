# Transcript Editor (Admin Correction Workbench)

> **Status:** 🚧 Phases 0–3, 3b (dedicated editor screen) and 3c (local audio) implemented (2026-11-02); Phase 4 not started
> **Created:** 2026-10-27
> **Updated:** 2026-11-02 — §5 rewritten around a dedicated editor screen (transport dock, lane timeline, play-a-sample, focus-not-filter, segment inspector, merge handle) after the in-place build proved unusable; Phases 3b/3c added. Earlier the same day: Phases 0–3 built: op log + loader (`thestill/core/transcript_edits.py`), the seven read sites and the summarizer moved, `transcript.edit` permission, routes, `thestill transcript` CLI, editor UI (speaker panel, inline text, split/merge, kind, undo/redo, conflicts, Finish), report deep link. Implementation notes under §Phases
> **Priority:** Medium. Wrong and unnamed speakers are the most visible transcript defect, and today the only fix is editing episode facts and re-running the cleaner, which also re-runs the LLM.
> **Author:** Product & Engineering
> **Related:** [#18 segment-preserving-transcript-cleaning](18-segment-preserving-transcript-cleaning.md) (`source_segment_ids`, `WordSpan`, the reserved `user_segment_id`), [#59 transcript-summary-feedback](59-transcript-summary-feedback.md) (Tier 2 workbench, carved out into this spec), [#86 quality-feedback-loop](86-quality-feedback-loop.md) (reports, the `transcript.overlay` fix op, cases), [#106 episode-reprocess](106-episode-reprocess.md) (downstream refresh), [#104 abuse-prevention-and-user-admin](104-abuse-prevention-and-user-admin.md) (admin role, audit log), [#28 corpus-search-and-entities](28-corpus-search-and-entities.md) (chunks, entity mentions, `speaking` mentions), [#54 summary-segment-citations](54-summary-segment-citations.md) (citation sidecar), [#97 episode-chapters](97-episode-chapters.md) (forced alignment as a follow-up), [#96 prompt-injection-hardening](96-prompt-injection-hardening.md), [#42 robustness-and-failure-mode-hardening](42-robustness-and-failure-mode-hardening.md)

---

## Executive summary

An admin can open any processed episode's transcript in an editor and fix
it: name and merge speakers, move a segment to another speaker, correct
text, retag a segment's kind, and split or merge segments. Later, if the
data justifies it, they can also drag segment boundaries on a waveform.
The model is the editor ElevenLabs ships for its own transcripts, adapted
to one difference that changes the design: a thestill episode is shared
by every follower, and about ten other artifacts are built from its
cleaned transcript.

So edits never rewrite the transcript files. Each edit is a row in an
append-only **operation log**, keyed to the durable anchors #18 put on
every segment. One loader builds the **effective transcript** (cleaned
sidecar plus replayed operations), and every reader uses it: the
transcript tab, the words API, MCP, and the pipeline stages that build
search chunks, entity mentions, citations and narration. A re-clean
replays the log on the new sidecar; an edit that no longer fits becomes
a visible conflict, never a silent drop. Undo, redo and autosave come
from the log.

After an editing session the cheap refreshes (search chunks, entity
mentions) run automatically through #106's reprocess path. The summary
is never regenerated automatically; the admin sees that it is stale and
decides.

Everything is **admin-only** in this spec: endpoints, CLI and UI. The
gate is a named permission (`transcript.edit`) checked through one
helper that today means "is an effective admin", so a future
**moderator** role is a change to that helper and a grants table, not to
any call site (§[Future: moderator role](#future-moderator-role)).

## Problem

1. **Speakers are wrong or unnamed, and fixing them is expensive.**
   Diarization labels (`SPEAKER_02`) become names through the episode
   facts `speaker_mapping`
   ([facts.py:56](../thestill/models/facts.py#L56)), applied by the
   segmented cleaner. When facts extraction misses a speaker, the label
   leaks to readers; the cleaner's own docstring calls a surviving
   `SPEAKER_NN` "a visible canary: facts extraction missed that speaker
   and the facts file needs editing + a re-clean". When diarization
   splits one person across two labels, nothing can merge them. The
   fix today is editing facts and running
   `thestill reprocess --from clean`, which pays for a full LLM clean
   to change a name.
2. **Speakers drive more than the label.** The "People in this episode"
   sidebar counts, roles (host or guest), `list_quotes_by`, featured
   quotes and narration quotes all come from entity mentions, including
   the synthesized `speaking` mention for each segment's speaker
   ([entities.py:86](../thestill/models/entities.py#L86)). A wrong
   speaker attributes quotes to the wrong person in public.
3. **There is nowhere to act on a report.** #86 designs reports with
   structured proposals (`wrong_speaker`, `wrong_words`, `bad_boundary`,
   `wrong_kind`) and names `transcript.overlay` as the fix op, but
   nothing defines that op, stores it, or applies it.
4. **Misheard words propagate.** A misheard name lands in search chunks
   and entity extraction and is fixable only upstream (ASR hints,
   cleaning prompt), which helps future episodes but not this one.

## Goals

1. An admin fixes a mislabelled or unnamed speaker across a whole
   episode in under a minute, without an LLM call.
2. One data model for every transcript correction: the operation log is
   both the editor's storage and the target of #86's
   `transcript.overlay` fix op.
3. Pipeline artifacts stay write-once. The effective transcript is
   computed, and every reader goes through one loader.
4. Edits survive `reprocess --from clean` and `--from transcribe`, or
   surface as conflicts the admin resolves. Never a silent drop.
5. Each edit records who made it, when, and what it replaced. Any edit
   can be reverted, and so can every edit by one actor.
6. The permission check is a named permission, so a moderator role can
   be added later without touching endpoints, CLI or UI entry points.

## Non-goals

- **Editing by non-admins.** Readers report through #86. Moderators are
  future work (§[Future: moderator role](#future-moderator-role)).
- **Automatic summary regeneration.** The summary is marked stale for
  admins; regenerating costs money and is an explicit #106 reprocess.
- **Rewriting the raw or cleaned transcript files.** Folding the log
  into a new sidecar ("promotion") is an open question, never implicit.
- **Word re-alignment after a text edit.** Edited words lose word-level
  timings in v1 (the segment keeps its start and end). Forced alignment
  is the follow-up #97 already names.
- **Editing summaries, briefings or entity links.** Entity fixes stay
  with entity curation and #86's `entity.*` ops.
- **Phone editing.** The editor is desktop-only in v1; phones keep the
  read-only transcript.
- **MCP write tools.** No edit tool is registered on stdio or remote
  MCP.

## Current state

| Piece | Where | What it gives this spec |
|---|---|---|
| Raw transcript | `Transcript` / `Segment` / `Word` ([transcript.py](../thestill/models/transcript.py)) | Write-once. `Segment.id` and `Segment.speaker` (the diarization label) are stable for the episode's life. |
| Cleaned sidecar | `AnnotatedTranscript` ([annotated_transcript.py:131](../thestill/models/annotated_transcript.py#L131)) | Segments carry `source_segment_ids` ([:99](../thestill/models/annotated_transcript.py#L99)), the durable anchor; `WordSpan` ([:60](../thestill/models/annotated_transcript.py#L60)) addresses words positionally; `user_segment_id` ([:101](../thestill/models/annotated_transcript.py#L101)) is reserved for this spec. The `speaker` field already holds mapped names, not labels. |
| Words API | `GET /{podcast}/episodes/{episode}/transcript/words` ([api_transcript_words.py:71](../thestill/web/routes/api_transcript_words.py#L71)) | Word timings for click-to-play. |
| Viewer | [SegmentedTranscriptViewer.tsx](../thestill/web/frontend/src/components/SegmentedTranscriptViewer.tsx) | Segments, kinds, speaker colours, click-to-seek. The read layer of the editor. |
| Reprocess | `queue_reprocess` ([episode_reprocess.py:68](../thestill/services/episode_reprocess.py#L68)), stages `transcribe`, `clean`, `summarize`, `entities` | Downstream refresh, episode-wide busy guard. |
| Admin gate | `require_admin` / `is_effective_admin` ([dependencies.py:315](../thestill/web/dependencies.py#L315), [:357](../thestill/web/dependencies.py#L357)); frontend `isAdmin` ([AuthContext.tsx:184](../thestill/web/frontend/src/contexts/AuthContext.tsx#L184)) | One definition of "operator now". |
| Audit log | `AuditLog.record` with the closed `AUDIT_ACTIONS` list ([audit_service.py:31](../thestill/services/abuse/audit_service.py#L31)) | Same pattern as entity curation. |

The cleaned sidecar is read directly with
`AnnotatedTranscript.model_validate_json` in seven places:

- reindex, `handle_reindex` ([task_handlers.py:879](../thestill/core/task_handlers.py#L879));
- entity extraction, `handle_extract_entities` ([task_handlers.py:1147](../thestill/core/task_handlers.py#L1147));
- citations, `load_annotated_for_episode` ([summary_citations.py:782](../thestill/core/summary_citations.py#L782));
- narration ([transcript_loader.py:228](../thestill/services/narration/transcript_loader.py#L228));
- the transcript API ([podcast_service.py:779](../thestill/services/podcast_service.py#L779)) and the words API ([:842](../thestill/services/podcast_service.py#L842));
- repair tools ([mojibake_repair.py:399](../thestill/core/mojibake_repair.py#L399), [cli.py:5922](../thestill/cli.py#L5922)).

The summarizer does not read the sidecar at all: `handle_summarize`
([task_handlers.py:685](../thestill/core/task_handlers.py#L685)) reads
the rendered `_cleaned.md`.

## Design

### 1. What maps to what

| ElevenLabs | thestill | Phase |
|---|---|---|
| Speakers panel: Speaker 0–3 rows | Episode speaker panel: one row per diarization label (from the raw segments behind each cleaned segment), with its current name, linked person entity, segment count and colour | 1 |
| Rename speaker | `speaker.rename_label`: set a label's display name and optionally link a person entity (search the entity index; free text allowed). Not free text only, because names drive entity mentions and quotes. | 1 |
| Move segments to speaker | `speaker.merge_labels` (all of one label's segments to another; the common "one person, two labels" fix) and `segment.set_speaker` (one segment) | 1 |
| Add to speaker library | "Save as podcast host": append to `PodcastFacts.hosts` ([facts.py:38](../thestill/models/facts.py#L38)) so future cleans name them. The one edit that writes a facts file; audited. | 1 |
| Edit text in a segment | `segment.set_text` with the replaced text as a snapshot | 2 |
| Merge segments (icon between segments) | `segment.merge` of two adjacent segments | 2 |
| Split segment | `segment.split` at a word boundary | 2 |
| Segment kind (not in ElevenLabs) | `segment.set_kind` (content, ad_break, filler, music, intro, outro) | 2 |
| Undo / redo, "Last saved now" | Revert ops on the log; every edit is saved when made | 2 |
| Align words | Not in v1: edited segments lose word timings. Forced alignment is the #97 follow-up. | — |
| Timeline drag, start/end time fields | `segment.adjust_boundary` against word positions, with a waveform strip | 4, only if `bad_boundary` reports justify it |
| Delete segment | `segment.set_kind` to a hidden kind instead of deleting, so the audio and anchors stay accounted for | 2 |

### 2. Operation log

One table on both backends, added in the next Alembic revision:

```sql
CREATE TABLE transcript_edits (
    id              uuid PRIMARY KEY,
    episode_id      uuid NOT NULL REFERENCES episodes(id) ON DELETE CASCADE,
    seq             integer NOT NULL,          -- per-episode, monotonic
    op              text NOT NULL,             -- closed list below
    raw_sha256      text NOT NULL,             -- raw transcript the anchors refer to
    anchor_json     jsonb NOT NULL,            -- durable anchor(s)
    args_json       jsonb NOT NULL,            -- op arguments
    before_json     jsonb NOT NULL,            -- snapshot of what it replaced
    reverts         uuid NULL REFERENCES transcript_edits(id),
    session_id      uuid NOT NULL,             -- one editor session; groups undo and refresh
    report_id       text NULL,                 -- #86 report this applies, if any
    actor_user_id   uuid NULL,                 -- NULL for CLI/system actors
    actor_kind      text NOT NULL,             -- same Actor kinds as the audit log
    created_at      timestamptz NOT NULL,      -- ISO-8601 UTC, +00:00 on SQLite
    UNIQUE (episode_id, seq)
);
```

Rows are never updated or deleted, except by episode cascade. Undo is a
new row with `reverts` set; redo reverts the revert. "Revert everything
by this actor since T" is a loop of revert rows, which is what makes a
later moderator role safe to add.

**Ops** (closed list, each a Pydantic model validated at write time):

| `op` | Anchor | Args |
|---|---|---|
| `speaker.rename_label` | `{label}` | `{name, entity_id?}` |
| `speaker.merge_labels` | `{label}` | `{into_label}` |
| `segment.set_speaker` | segment anchor | `{label}` or `{name, entity_id?}` |
| `segment.set_text` | segment anchor | `{old_text, new_text}` |
| `segment.set_kind` | segment anchor | `{kind}` |
| `segment.split` | segment anchor | `{at: {raw_segment_id, word_index}}` |
| `segment.merge` | two adjacent segment anchors | — |
| `segment.adjust_boundary` | segment anchor | `{edge: start\|end, to: {raw_segment_id, word_index}}` (Phase 4) |
| `revert` | `{edit_id}` | — |

**Segment anchor:** `{source_segment_ids, source_word_span, start_s,
end_s, text_sha256}`, the same shape as #86's `transcript_segment`
anchor plus the word span and a text hash. `source_segment_ids` locate
the segment (#18 §"Future: user segment-editing UI"); the word span
tells apart the children of a split, which share raw segments; the text
hash is computed on the segment as the editor showed it, after the
earlier ops in the log.

**Speaker labels** are diarization labels read from the raw segments
behind each cleaned segment (majority label across its
`source_segment_ids`), because the cleaned sidecar already holds mapped
names. A label is only an identity within one raw transcript:
`reprocess --from transcribe` overwrites the raw JSON, and every
transcriber assigns `SPEAKER_NN` afresh in encounter order
([dalston_transcriber.py](../thestill/core/dalston_transcriber.py)), so
`SPEAKER_02` after a re-transcribe may be someone else. That is why
every op records `raw_sha256`, the hash of the raw transcript its
anchors were written against.

**Concurrency.** Every write carries `base_seq`, the last `seq` the
client saw. A mismatch returns 409 with the new ops; the client
rebases and retries. Editing is refused (409, same message as #106)
while a `transcribe` or `clean` reprocess for the episode is queued or
running, reusing #106's episode-wide busy guard.

### 3. Effective transcript

A new module (`thestill/core/transcript_edits.py`) owns
`load_effective_transcript(episode) -> EffectiveTranscript`:

1. Load the sidecar.
2. Apply the episode's live ops in `seq` order (reverted ops and the
   reverts themselves cancel out).
3. Return the transcript plus `applied`, `conflicts` and `last_seq`.

Applied segments get a `user_segment_id` (UUID, deterministic from the
first edit's id) and `metadata["edited"] = true`. An op applies only
when all of these hold, and is otherwise a **conflict** (not applied,
listed with the reason):

1. **Same raw transcript.** The op's `raw_sha256` equals the current
   raw transcript's. After a re-transcribe every op conflicts with
   reason `retranscribed`; nothing is applied by label or segment id.
   The editor lists the speaker ops with a suggested new label (the one
   covering most of the old label's speaking time) and a segment's
   suggested match (same text hash, overlapping time); the admin
   confirms each, and confirming writes a fresh op against the new raw
   transcript. Nothing is remapped without that confirmation.
2. **Exactly one candidate.** Among segments with the anchor's
   `source_segment_ids`, exactly one has its `source_word_span`; with
   no candidate, or more than one, the reason is `anchor_missing` or
   `anchor_ambiguous`.
3. **Unchanged text.** The candidate, as rebuilt by the earlier ops in
   the log, still hashes to `text_sha256`. A re-clean that rewrote the
   segment gives reason `text_changed`, even though its ids still
   match. The editor shows the old and new text side by side, and the
   admin re-applies or drops the edit.

Speaker-label ops need only rule 1: a label means the same voice
anywhere in the same raw transcript.

All seven sidecar reads listed in §Current state move to this loader. A
unit test fails if `AnnotatedTranscript.model_validate_json` appears
outside the loader (the same pattern as
[test_scopes.py](../tests/unit/mcp/test_scopes.py), which keeps every MCP
tool registered).
The summarizer renders
`effective.to_blended_markdown(exclude_kinds=_ADS_STRIPPED_KINDS)` when
the episode has applied ops, the same exclusion
[transcript_cleaning_processor.py](../thestill/core/transcript_cleaning_processor.py)
uses to write `_cleaned.md` (the renderer includes ads by default), so
an edit never changes which kinds reach the summarizer; only a kind
retag does. With no applied ops it reads `_cleaned.md` unchanged, so
unedited episodes are byte-identical to today. A test renders an
episode with an ad segment and a speaker-only edit and asserts the
sponsor text is absent.

When any structural op (split, merge, boundary) is applied, the
effective transcript's `algorithm_version` becomes
`<sidecar version>+edits.<seq of the last applied structural op>`.
`load_valid_citations_for_api` trusts a citation's `segment_id_hint`
only when that version matches the one the citations were resolved
against ([summary_citations.py](../thestill/core/summary_citations.py)),
so after a split or merge the hint is ignored and every citation
re-resolves from its `source_segment_ids`. Text, speaker and kind ops
leave the version alone because they do not move segment ids.

The result is cached per `(raw sha256, sidecar sha256, last_seq)`. The
raw hash is in the key because a re-transcribe replaces the raw JSON
but keeps the old sidecar until cleaning succeeds and appends no op, so
without it a cached result would keep reporting the old ops as applied
instead of `retranscribed` conflicts. An episode with no ops pays one
indexed count query.

### 4. Downstream propagation

The ops are live as soon as they are saved: the transcript tab, the
words API and MCP `get_transcript` read the effective transcript, and so
will export ([#91](91-transcript-and-summary-export.md)) when it is
built. Public pages ([#105](105-server-rendered-public-pages.md)) never
serve the transcript, but their attributed quotes follow entity
mentions. Derived artifacts refresh per this table:

| Edit | Search chunks | Entity mentions, roles, sidebar counts | Quotes (`list_quotes_by`, featured, narration pool) | Summary and `[MM:SS]` citations | Chapters |
|---|---|---|---|---|---|
| Speaker rename, merge, move | Auto (speaker column, FTS) | Auto (re-extract; `speaking` mentions follow) | Follow entities | Stale badge: prose may name the wrong person | — |
| Text | Auto | Auto | Follow entities | Stale badge | — |
| Kind (content ↔ ad) | Auto | Auto | Follow entities | Stale badge: summarizer input changed | — |
| Split, merge | Auto | Auto | Follow entities | Citations re-resolve by `source_segment_ids` overlap, forced by the `+edits` version (§3); no stale badge | — |
| Boundary (Phase 4) | Auto | Auto | Follow entities | Citations re-resolve, forced the same way | — (publisher chapters, #97) |

**Auto** means: when the admin presses **Finish editing**, or the
session has been idle for 15 minutes, the service queues one #106
reprocess `--from entities` for the episode. That path must also
re-chunk; Phase 0 confirms it does and adds `reindex` to it if not. A
nightly sweep queues the same refresh for any episode whose newest op is
newer than its last refresh, so a closed tab never leaves an episode
half-applied. Entity extraction is GLiNER on CPU, so this costs cents,
not an LLM call.

**Stale badge** means: admins see "Summary predates N transcript edits —
Reprocess from Summarize" on the episode page, which opens #106's
dialog. Readers see nothing. No summary is regenerated automatically.

### 5. Editor UI

> Revised 2026-11-02. The first build put editing affordances on the
> episode page's transcript tab (PR #461). Trying it on a real episode
> showed the design was wrong: the admin's only instruments are their
> ears and the surrounding turns, and the in-place editor gave them
> neither — audio lived in the global mini player, far from the row
> being judged; a speaker row *filtered* the transcript, hiding the very
> context that tells an ad voice from a guest; the hero, People row,
> rails and tabs left a narrow column with no sense of time. The
> ElevenLabs transcript editor gets the anatomy right, and this section
> now follows it. The op log, routes and replay are unchanged; this is
> presentation only.

#### 5.1 A dedicated screen

The entry point stays **Edit transcript** in the episode page's admin ⋯
menu, but it opens a dedicated route,
`/podcasts/{podcast}/episodes/{episode}/edit`, gated by
`can("transcript.edit")` and desktop-only (below `sm` the route sends the
reader back to the transcript). The screen carries no episode chrome: no
hero, People row, entity strip, rails, tabs or kind toggles. Report deep
links (Phase 3) target this route:
`/edit?report=<id>&segment=<segment id>&fix=…[&value=…]`.

Layout, top to bottom:

```text
┌ ← Back · Episode title · Podcast         ↶ ↷   ● Saved · 3 edits   ⚠ 1 conflict   [Finish editing] ┐
├───────────────┬──────────────────────────────────────────────────────────────────────────────────┤
│ SPEAKERS      │  Ada Lovelace   00:12  Welcome back to the show, everyone. Today we are talking …  │
│ ● Ada L.  41  │                 00:31                                                              │
│   SPEAKER_00  │              ⟐ merge                                                               │
│   ▶ ◀ ▶  ⋯    │  SPEAKER_01     00:31  Thanks for having me. I have been looking forward to this.  │
│ ● SPEAKER_01 7│                 00:38                                                              │
│   unnamed     │              ⟐ merge                                                               │
│   ▶ ◀ ▶  ⋯    │  Ada Lovelace   00:38  So let us start with the question everyone asks …           │
│ ● Ad Narr. 3  │                                                                                    │
│               │                                                                                    │
│ SEGMENT       │                                                                                    │
│ Speaker  ▾    │                                                                                    │
│ Kind     ▾    │                                                                                    │
│ 00:31–00:38   │                                                                                    │
│ [Hide]        │                                                                                    │
├───────────────┴──────────────────────────────────────────────────────────────────────────────────┤
│ ▶  −5s  +5s  1.0×   00:31.2 / 1:02:14                                                             │
│ Ada L.    ▬▬▬▬▬▬▬▬▬      ▬▬▬▬▬▬▬     ▬▬▬▬▬▬▬▬▬▬▬▬▬▬▬▬▬▬                                            │
│ SPEAKER_01          ▬▬▬▬▬        ▬▬▬▬           ▬▬                                                 │
│ Ad Narr.  ▬▬▬                                                        ▬▬▬                           │
│           0:00          0:30         │ 1:00          1:30                                          │
└──────────────────────────────────────────────────────────────────────────────────────────────────┘
```

#### 5.2 Top bar

← Back (to the episode page, transcript tab), the episode title with the
podcast name, undo and redo (⌘Z, ⇧⌘Z), the save status ("Saved" /
"Saving…" / an error / "Reloaded" after a 409), the live edit count, the
conflicts chip (opens the list from §3), and **Finish editing** as the
one primary button. Finish queues the §4 refresh and returns to the
episode page; leaving by Back keeps the edits (they are saved) and the
idle sweep queues the refresh.

#### 5.3 Speakers (left column, sticky)

One row per diarization label, in order of first appearance, with the
viewer's colour dot, the current name, and underneath the raw label and
segment count ("SPEAKER_01 · 7 segments"). An unnamed voice reads
"Unnamed" in place of the name; a facts-mapped guess such as
"Ad Narrator 1" reads as the name with its raw label beneath, so the
admin can see it is a mapping, not a fact.

Every row carries the instruments for judging a voice, which the
in-place build lacked:

- **▶ Play a sample** — plays the voice's longest content segment from
  its start, scrolls the transcript to it and selects it.
- **◀ ▶ Previous / next turn** — seeks to the voice's previous or next
  segment and scrolls to it. The transcript is never filtered: other
  voices stay in place, dimmed slightly while a voice is focused, so the
  turn is always read in context. Clicking the row focuses the voice;
  clicking again clears it.
- **⋯ menu** — Rename or link… (the name-and-entity dialog), Merge into…
  (another label), Save as podcast host. Rename is also reachable by
  clicking the name in the row or in the transcript gutter.

Below the speakers, a **Segment** inspector for the selected row:
speaker (picker over the labels, plus "Someone else…"), kind, start and
end (read-only until Phase 4), and Hide (sets kind `filler`, the
non-destructive delete). Selecting a row anywhere (transcript, lane,
sample) populates it. This replaces the inline speaker button and kind
`<select>` of the first build; the transcript row itself stays clean.

#### 5.4 Transcript (centre)

Turns at reading size, full width. Each row: the speaker name (coloured;
click to open the speaker picker) and the start time in a left gutter,
the end time beneath, the text to the right. The active segment follows
playback and the row that holds the playhead is highlighted the way the
reader's viewer does it.

- **Click a word** to play from it. The words API already serves word
  timings for karaoke; the editor renders them for the selected row and
  the active row, so the click target is the word, not the row. A
  segment marked `words_stale` (text edited) plays from its start.
- **Edit text** by clicking the text (the row turns into a text field in
  place): Enter saves; Enter with the caret inside unchanged text splits
  on that word boundary; Escape cancels; leaving the field saves. The
  ElevenLabs Backspace-at-start merge is dropped in favour of the
  explicit handle below — it was too easy to trigger by accident.
- **Merge handle** between adjacent rows of the same kind: a small
  ⟐ icon on the rail between them, visible on hover and when either row
  is selected, tooltip "Merge segments". It writes `segment.merge`.
- **Edited** rows show a small "edited" tag in the gutter; a conflict
  (§3) shows an amber tag that opens the conflicts list.

Edited text renders as text everywhere, never as Markdown or HTML.

#### 5.5 Transport and lanes (bottom dock, sticky)

A transport row — play/pause, −5 s, +5 s, speed, current time over
duration — bound to the app's player (`PlayerContext`), so follow and
karaoke keep working and the mini player is never needed. Under it a
**lane timeline**: one lane per voice (same order and colours as the
speakers column), every segment a block on a shared time axis, the
playhead as a vertical line, the selected segment filled darker. The
axis shows roughly the current minute at the default zoom; scroll to pan,
⌘-wheel to zoom, and the lanes keep the playhead in view while playing.
Clicking a block seeks there, selects the segment and scrolls the
transcript to it; the lanes are what make a stray turn — three
SPEAKER_03 blocks inside Ada's run — visible before any listening.

The dock is the Phase 4 home: the waveform strip and draggable
boundaries, if built, sit on the selected voice's lane.

#### 5.6 Audio source

The dock plays the episode's playback manifest, like the reader. The
transcript was made from our downloaded copy, and the publisher's URL
can differ by inserted ads (spec #97's drift problem), so when the
episode's `original_audio` file still exists on storage the editor
plays that copy instead through a `transcript.edit`-gated audio route;
timestamps then match the audio exactly. This is the first consumer of
the local copy; the route is in §6.

#### 5.7 Keyboard

Space play/pause, J/K/L as in video editors (−5 s, pause, +5 s), ↑/↓
select the previous/next row, Enter edits the selected row's text, M
merges the selected row with the previous one, ⌘Z / ⇧⌘Z undo/redo,
Escape clears the voice focus. None fire while a text field is focused.

### 6. API and CLI

All routes depend on `require_permission(TRANSCRIPT_EDIT)`:

```text
GET    /api/episodes/{id}/transcript/edits              ops, conflicts, last_seq, speaker panel
POST   /api/episodes/{id}/transcript/edits              {base_seq, session_id, ops: [...]}
POST   /api/episodes/{id}/transcript/edits/{edit_id}/revert
POST   /api/episodes/{id}/transcript/edits/finish       {session_id}  queues the refresh
POST   /api/episodes/{id}/transcript/edits/save-host    {name, entity_id?}  facts hosts (+ host anchor)
GET    /api/episodes/{id}/transcript/edits/people?q=    person entities for the name-or-link picker
GET    /api/episodes/{id}/transcript/edits/audio        (Phase 3c) the local original_audio copy, 404 when gone
```

CLI, for scripting and recovery (operator actor, same as
`thestill entity`):

```text
thestill transcript edits    --podcast-id X --episode <slug> [--json]
thestill transcript conflicts [--podcast-id X]
thestill transcript revert   --edit <id> | --by <email> --since <ISO>
thestill transcript speaker-stats [--json]      # the success metric below
```

### 7. Permissions and audit

A new `thestill/web/permissions.py` defines the named permissions and
the one check:

```python
TRANSCRIPT_EDIT = "transcript.edit"

def has_permission(user, permission, config, *, episode_id=None) -> bool:
    # Spec #110: every permission is admin-only for now.
    return is_effective_admin(user, config)

def require_permission(permission): ...   # FastAPI dependency factory
```

- New routes use `require_permission`, never `require_admin` directly.
  `episode_id` is in the signature now so per-podcast scoping later does
  not change callers.
- `/api/auth/me` returns `permissions: string[]`; `AuthContext` exposes
  `can(permission)`; editor entry points check
  `can("transcript.edit")`, not `isAdmin`.
- The op log is the per-edit history (actor, time, before, after).
  The audit log gets one row per finished session and per bulk revert:
  new `AUDIT_ACTIONS` `transcript.edit_session` (episode, op counts,
  report ids) and `transcript.revert_bulk`, plus `facts.add_host` for
  Save as podcast host. Per-keystroke autosave rows would drown the
  audit log, so they stay in the op log.

## Future: moderator role

Not in scope. The design above is what makes it cheap:

1. **Grants table, not a role column.** Add
   `user_permissions(user_id, permission, scope_kind, scope_id, granted_by, granted_at)`
   with `scope_kind` in `global | podcast`. A role column fits "global
   moderator" but not the likely shape, which is someone trusted on one
   show (a producer, a superfan). `is_admin` stays as it is; admins
   implicitly hold every permission.
2. **`has_permission` becomes:** admin → true; otherwise look up a grant
   for the permission, global or matching the episode's podcast. No call
   site changes.
3. **CLI and audit, mirroring `thestill admin grant`:**
   `thestill moderator grant <email> [--podcast X]` and `revoke`, with
   audit actions `moderator.grant` and `moderator.revoke`.
4. **What moderators get:** `transcript.edit` only. Not reprocess (it
   spends money), not entity curation (corpus-wide), not user admin. The
   stale-summary badge stays admin-only.
5. **What has to exist first:** a per-user edit quota (#104's quota
   service), `thestill transcript revert --by` (built in Phase 0
   above), and an admin view of recent edits by non-admins. Edited text
   reaches LLM prompts via the summarizer, so #96's fencing must cover
   the effective transcript before non-admins can write it.

Moderators could also triage #86 reports for their podcasts; that would
be a second permission (`reports.triage`) under the same scheme.

## Phases

| Phase | Scope | Gate |
|---|---|---|
| 0 — Foundations | `transcript_edits` table and repos (SQLite and Postgres, contract tests), op models, `load_effective_transcript` with replay and conflicts, the seven read sites moved, summarizer branch, permissions module, `/me` permissions, audit actions, `thestill transcript` CLI, confirm or extend the `--from entities` re-chunk | A CLI `speaker.rename_label` on a local episode shows in the transcript API, words API and MCP; survives `reprocess --from clean`; reverts cleanly; unedited episodes produce byte-identical summarizer input |
| 1 — Speaker panel | Speaker panel UI, rename or link, merge labels, move one segment, Save as podcast host, Finish editing and idle refresh, stale badge | On a prod-copy episode with a `SPEAKER_NN` leak, an admin names the speaker in under a minute; after refresh the sidebar, roles and `list_quotes_by` show the right person |
| 2 — Text and segments | Inline text edit, split, merge, kind picker, undo and redo, conflicts list in the header | Every op type round-trips through undo, redo and a re-clean; conflicts appear for an op whose segment text changed |
| 3 — Reports to editor | #86 report deep link, proposal staged, accept writes the op with `report_id` and sets the report's `fix_json` | One `wrong_speaker` and one `wrong_words` report go report → editor → fixed without leaving the editor |
| 3b — Editor screen (§5 revised) | Dedicated `/edit` route with top bar, speakers column (play a sample, previous/next turn, focus-not-filter), segment inspector, clean transcript rows with click-a-word playback and the merge handle, transport dock with lane timeline, keyboard map; the in-place edit mode of Phases 1–2 is removed | On a prod-copy episode an admin, without leaving the screen or touching the mini player, hears a voice, names it, moves a stray turn and finishes; a mis-labelled turn is visible on the lanes before any listening |
| 3c — Local audio for editing | `transcript.edit`-gated route serving the episode's `original_audio` copy when it exists; the dock prefers it | A drifted episode (publisher file longer than transcribed) plays in sync in the editor |
| 4 — Waveform and boundaries | Waveform strip on the selected lane, `segment.adjust_boundary`, drag at word positions | Built only if `bad_boundary` reports or admin requests show the need; a boundary fix survives a re-clean |

Phase 0 changes the read path for every episode, so it ships alone and
soaks before Phase 1's UI. Phase 3b is built in this order: route and
layout with the transport dock (the existing panel, bar and dialogs
moved in), then the speakers column instruments and focus-not-filter,
then the lanes, then the inspector and merge handle replacing the inline
controls, then click-a-word playback.

### Implementation notes (2026-11-02)

Phases 0–3 landed together on one branch, in that order, so the Phase 0
soak is the review window rather than a separate release. Where the build
departs from or narrows the text above:

- **Anchors carry text on the wire.** A segment anchor the editor sends
  holds the segment `text` it showed; `SegmentAnchor` hashes it into
  `text_sha256` on validation, so the row never stores transcript text
  twice and the client needs no hashing.
- **Split addressing.** The client sends `text_word_index` (the
  whitespace word the split lands before); the service fills the raw
  `at` reference by proportional mapping across the segment's word span,
  so a replay lands on the same raw word and audio boundary.
- **Busy guard is stage-aware.** Editing is refused while download,
  downsample, transcribe or clean is in flight; an entity-branch task
  (our own refresh) does not block.
- **Idle refresh and the nightly sweep are one job.** A
  `transcript_edit_refresh` periodic job (every 5 minutes) queues the
  `--from entities` refresh for any episode whose newest op is ≥ 15
  minutes old and newer than its last queued refresh
  (`transcript_edit_refreshes`); Finish queues immediately. A busy
  episode is retried by the same job.
- **Stale badge** counts live ops newer than the last completed
  `summarize` task (`summary_stale_edits` on the editor state); it is
  fetched only for sessions holding `transcript.edit`.
- **Speaker ops touch speaker rows only.** A rename or merge changes
  `content`/`filler` segments of the label; an `ad_break` built from the
  same raw segment keeps its empty speaker.
- **Word timings after a text edit** are dropped: the segment is marked
  `words_stale` and the words API omits it, so the karaoke wipe falls
  back to segment highlighting there (open question 4, v1 answer).
- **Linked speaker entities** ride on `metadata.speaker_entity_id`; the
  extractor's synthesized `speaking` mention uses it ahead of the anchor
  name match, so quotes-by and the People rail follow the admin's choice
  after the refresh.
- **Phase 3 without #86.** The reports table does not exist yet, so the
  editor implements the deep-link contract only:
  `?view=transcript&edit=1&report=<id>&segment=<segment id>&fix=wrong_speaker|wrong_words|wrong_kind[&value=…]`
  opens the editor scrolled to the segment with the proposal staged;
  Accept writes the op with `report_id`. Setting the report's `fix_json`
  is #86's half and waits for its table.
- **Retranscribe remap suggestions** (the "suggested new label" in §3
  rule 1) are not built; conflicts list the op with its `before` snapshot
  and the admin re-applies by hand.
- **CLI** adds `rename-speaker` and `refresh` to the four commands listed
  in §6; `revert --by` takes an email.
- **The in-place editor was a stopgap.** PR #461's first UI (edit mode on
  the transcript tab, speaker panel that filters, inline speaker button
  and kind select) was judged unusable on a real episode — no audio near
  the text, no surrounding turns, no timeline — and §5 was rewritten
  around a dedicated screen, built the same day as Phase 3b
  (`pages/TranscriptEditorPage.tsx`, `components/transcript-editor/`).
  The op log, routes and the `useTranscriptEditor` hook carried over
  unchanged; the reader's viewer lost its editing props again.
- **Phase 3b narrowings.** The lanes are an SVG over a time window
  (default 120 s, ⌘-scroll zoom, scroll pan) rather than the full
  episode; words render only on the selected and active rows (the words
  API payload is per segment, so this keeps the DOM small); a single
  click on a word plays from it and a double-click (or Enter, or the
  inspector's Edit text) opens the text, since the two gestures share
  the same target; Backspace-at-start merge was dropped for the handle
  and the M key. Start and end in the inspector are read-only (Phase 4).
- **Phase 3c.** `GET …/transcript/edits/audio` serves `original_audio`
  from local disk only; the state carries `local_audio` and the dock
  switches the player's source to the route when it is true.
- **Review hardening (2026-11-04, Codex + Antigravity on PR #461).** A
  batch is validated as a whole before any row is written, so a refused
  op leaves nothing behind. A revert's target must be live: a second
  undo of the same op is `edit_rejected`, and the editor drops the redo
  path once a new op follows an undo (a redo then would revive an op the
  new one was anchored without). Speaker ops know every label the
  transcript carries, including one heard only in an ad read, and a row
  re-tagged into a speech kind (`content`, `filler`, `intro`, `outro`)
  takes its label's current name. The stale-summary count compares the
  ops live when the summary ran with the ops live now, ignoring rows in
  conflict. The audio route resolves the stored path and refuses
  anything outside `original_audio` (absolute paths, `..`, symlinks
  out). All transport gestures, keys included, act on the episode being
  edited and leave the YouTube engine before taking the local file. A
  second pass added: a re-tagged row with no name of its own takes its
  label's name; an explicit split cut outside the span is unaddressed;
  the stale count compares the ops *applied* then and now (a structural
  undo that conflicts a text edit the summary saw counts); a pan away
  from the playhead holds until the playhead has been inside the chosen
  window and leaves it.

## Success metrics

- **Unnamed-speaker share:** published episodes whose effective
  transcript has any content segment with speaker `None` or matching
  `SPEAKER_\d+`. `thestill transcript speaker-stats` measures the
  baseline in Phase 0; the goal is that every such episode an admin
  opens leaves the editor at zero.
- **Time to fix a speaker** (session start to Finish editing, from the
  audit rows).
- **Replay conflict rate** after re-cleans. Above a few percent means
  the anchor matching needs work before moderators.
- **Report fix rate** (Phase 3): share of `wrong_speaker` and
  `wrong_words` reports closed through the editor.

## Failure modes ([#42](42-robustness-and-failure-mode-hardening.md))

- **Errors as empty results.** If the op log cannot be read, pipeline
  stages raise a transient error and retry; they never build chunks or
  mentions from the unedited sidecar as if it were current. Reader views
  fall back to the base transcript with
  `transcript_edits.overlay_failed` logged and an admin-only banner.
- **Silent degradation.** Conflicts are counted on the episode, listed
  by `thestill transcript conflicts`, and shown in the editor header.
  They are never dropped.
- **Checkpoint before durability.** The refresh is queued only after the
  ops commit; the nightly sweep catches a refresh lost to a crash
  between the two.
- **Path drift.** One loader, enforced by a test; no reader opens the
  sidecar directly.
- **Mixed time zones.** `created_at` is timezone-aware UTC; SQLite stores
  ISO-8601 with `+00:00`.
- **Consistent-mock tests.** Repository contract tests run on SQLite and
  on Postgres via `TEST_DATABASE_URL`; replay tests use real sidecars
  from fixtures, not hand-built segments.
- **Unsanitized input into prompts.** Edited text reaches the summarizer.
  Admin-only today; #96 fencing is a precondition for moderators.

## Testing

- Op models: each op validates, applies and reverts on a fixture
  sidecar; revert of a revert restores the op.
- Cache: replacing the raw JSON with no new op and the old sidecar
  still in place turns cached applied ops into `retranscribed`
  conflicts.
- Citations: after a split, a citation whose `segment_id_hint` still
  exists but now names the other child resolves from its source anchor
  to the right passage.
- Replay: ops written against one sidecar replay on a re-cleaned
  sidecar of the same raw transcript; a changed segment text yields a
  `text_changed` conflict even when `source_segment_ids` still match;
  an edit to one child of a split applies to that child only; after a
  re-transcribe every op, label ops included, is a `retranscribed`
  conflict and nothing is applied until the admin confirms a remap.
- Read-path parity: for an episode with no ops, every migrated reader
  produces byte-identical output before and after Phase 0.
- Concurrency: stale `base_seq` returns 409; editing during a running
  clean reprocess returns 409.
- Permissions: every new route returns 403 for a signed-in non-admin in
  multi-user mode, and the route list test asserts no new route uses
  `require_admin` or no guard.
- Frontend: speaker panel rename and merge, undo and redo, conflict
  display (vitest); one e2e that edits, finishes and sees the sidebar
  update after the refresh task completes.

## Open questions

1. **Promotion.** Should a heavily edited episode ever fold its log
   into a new sidecar with provenance, so the log can be truncated?
   Evals and citations key off artifact hashes, so not before the eval
   story is clear (#59 left this open too).
2. **Reader-visible marker.** Show "Corrected by editors" on edited
   transcripts? Leaning yes, without naming who.
3. **Speaker rename and episode facts.** Should `speaker.rename_label`
   also write `speaker_mapping`, so the cleaner's LLM sees the right
   names on a re-clean? Recommendation: no. The log replays anyway, and
   #106's `--refresh-episode-facts` would overwrite it.
4. **Word timings after text edits.** Drop them (v1), or carry over
   timings for unchanged words by diffing old and new text?
5. **Bare-audio imports.** Should the importing user edit their own
   import's transcript, the way ElevenLabs works? That needs to know
   whether an import is private to one user (today they hang off the
   shared `audio-imports` parent) and a `transcript.edit` grant scoped
   to the owner.
6. **Idle refresh window.** Is 15 minutes right, given each refresh
   re-embeds the whole episode?
