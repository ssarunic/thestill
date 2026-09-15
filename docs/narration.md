# Briefing Narration

Spec [#33](../specs/33-narrated-digest.md). The narration is a
single-anchor, news-style readout of the day's processed episodes.
The link-index briefing script still ships synchronously without an LLM; the
narration is a progressive enhancement that arrives behind it on the
same `briefing_id`.

## How it works

```text
briefing generated (inbox open / scheduler / spec #50)
  ├─► link-index script written immediately (no LLM)        data/briefings/<user_id>/<briefing_id>/script.md
  └─► narration on demand (POST /api/briefings/{id}/narrate
      or `thestill narrate --briefing <id>`)                data/narrations/<briefing_id>-<slug>.{json,md}
       ├─► quote selection (deterministic)
       ├─► theme clustering   (LLM call #1)
       └─► script generation  (LLM call #2)
            └─► validation contract → regen once → fall back to link-index
```

When narration succeeds, the JSON script carries `mode: "narrated"` and a
markdown read-through is written alongside it. When validation fails
twice (or theme clustering errors out), the JSON carries `mode:
"fallback"` and the markdown becomes the link-index script with a
"narration unavailable" banner — the user always gets a usable briefing.

## Configuration

| Variable | Description | Default |
|----------|-------------|---------|
| `NARRATION_ENABLED` | Master rollout switch. Off by default while fallback rates are measured. | `false` |
| `NARRATION_DEFAULT_DURATION_SECONDS` | Target spoken runtime when the caller doesn't pass one (presets: 180/300/600). | `300` |
| `NARRATION_ANCHOR_PROMPT` | Anchor voice: `conversational_anchor` (one narrator talking to a friend) or `newsroom_anchor` (measured news-anchor). Spec #77. | `conversational_anchor` |
| `NARRATION_STATED_TARGET_RATIO` | Share of the narration word budget the writer is told to aim for; validation keeps the full budget. `0.5`–`1.0`. Spec #77. | `0.8` |
| `NARRATION_MATERIAL_MAX_WORDS` | Per-episode cap on the summary material (Gist + Key Takeaways + The Drama) the writer sees. Spec #77. | `400` |
| `LLM_PROVIDER` | Same provider as the rest of the pipeline (`anthropic` / `openai` / `gemini` / `mistral` / `ollama`). | (per `.env.example`) |

The `thestill narrate` standalone command requires a configured
briefing record and accepts:

| Flag | Description |
|------|-------------|
| `--briefing <id>` | Required. Briefing id to narrate (see the inbox briefing card or `GET /api/briefings`). |
| `--target-duration` | Target spoken duration (preset `short`/`medium`/`long`, or `5m` / `120s` / `0:05:00`). Defaults to `NARRATION_DEFAULT_DURATION_SECONDS`. |
| `--slug` | Output filename slug (`data/narrations/<briefing_id>-<slug>.{json,md}`). Default: `morning`. |
| `--dry-run` / `-d` | Run quote selection + theme clustering only; the LLM is skipped entirely (no provider is initialised). |

An LLM provider is only required for full runs — `--dry-run` works
without one (e.g., offline testing).

## Time-budget model

Narration time is budgeted in seconds, not words or segment count, so
a user choosing "the 5-minute briefing" gets a real promise of
runtime. The math:

```text
target_duration_seconds  =  user choice (presets: 180 / 300 / 600)
quote_seconds            =  Σ duration_seconds(picked quotes), capped at
                            target * max_quote_share
narration_seconds        =  target - quote_seconds
narration_word_budget    =  narration_seconds × wpm / 60
```

Validation tolerates ±15% on the narration word budget. Quotes that
push the run over `max_quote_share` are dropped lowest-scoring-first
in `NarrationGenerator._enforce_quote_share_cap`.

| Knob | Default | Notes |
|------|---------|-------|
| `wpm` | `150` | News-anchor pace. TTS-voice tuning happens here in a follow-up. |
| `max_quote_share` | `0.40` | Spec §"Open Question O1" recommends instrumenting and tuning between 25% and 50%. |
| `boundary_trim_fraction` | `0.05` | First/last 5% of an episode is treated as ad / sponsor real estate; quotes there are filtered when they mention an episode-level sponsor. |

These default constants live in
[`thestill/services/narration/narration_generator.py`](../thestill/services/narration/narration_generator.py)
as `DEFAULT_*` module-level values.

## Anchor prompt

Each anchor voice ships as its own Markdown file under
[`thestill/services/narration_prompts/`](../thestill/services/narration_prompts/)
and is picked by basename with `NARRATION_ANCHOR_PROMPT` (spec #77):

| Voice | File | Character |
|---|---|---|
| `conversational_anchor` (default) | [`conversational_anchor.md`](../thestill/services/narration_prompts/conversational_anchor.md) | One narrator talking to a friend: spoken English, concrete beat first, reacts to clips, story-shaped segments |
| `conversational_v2` | [`conversational_v2.md`](../thestill/services/narration_prompts/conversational_v2.md) | Same narrator with a point of view: one claim per show, a `reaction` block after every clip, a why-you'd-care line per segment, honest transitions (spec #77 Phase 2b) |
| `newsroom_anchor` | [`newsroom_anchor.md`](../thestill/services/narration_prompts/newsroom_anchor.md) | The original spec #33 voice: informed, slightly wry, news-anchor pacing |

The prompt is **read on every run** (no in-process caching) so an
operator can edit the file and re-run `thestill narrate` to A/B a
voice without restarting any long-running process. The files are small
(under 8 KB) so the unconditional read is cheap.

A voice file may contain the placeholder `{{noise_phrases}}`. The loader
replaces it with the list in
[`noise_phrases.py`](../thestill/services/narration_prompts/noise_phrases.py),
the same list the script writer counts against to produce the
`noise_phrase_hits` stat, so the instruction the model sees and the lint
can never drift. Edit that file, not the prompt, to tune the ban list.

To ship a new voice, drop a sibling `<name>.md` in the directory and set
`NARRATION_ANCHOR_PROMPT=<name>`. Names are plain basenames (`[a-z0-9_]`);
anything else, or a missing file, fails the server boot and the
`narrate` command with the list of available voices. Programmatic callers
can still pass `NarrationGenerator(..., anchor_prompt=...)` directly.

### What the writer is given

Besides the quote pool and the theme plan, each lead-segment episode
carries one `claim` and, when the summary has a Drama section, one piece
of `colour`: the Key Takeaway with the most content-token overlap with
the segment angle (first on ties, gist sentence as fallback) and the
Drama round closest to that claim, capped to whole sentences under
`NARRATION_MATERIAL_MAX_WORDS`. Citation links, bold and scare-quoted
terms are stripped first. Every quote in the pool is marked
`fits_claim=yes|no` by overlap with that claim so the writer cues only
clips that illustrate the point, or skips the clip for that show. Each
segment also carries a `transition:` line: a hard cut or a permitted
phrase for unrelated shows, or the clusterer's typed `relationship`
(consensus / debate / contradiction / extension) to name plainly.

The writer is told to aim for `NARRATION_STATED_TARGET_RATIO` × the
narration word budget; validation still enforces the full budget's
−50 % / +15 % window, which is what keeps the richer material from
tipping runs into the link-index fallback.

### Reaction blocks and soft validation

A script block may be `narration`, `quote`, or `reaction`: one spoken
sentence (≤ 30 words) directly after a quote cue, in the same section,
rendered in italics under the clip. Reaction words count toward the
budget and the leak check.

Four rules are checked as **soft** failures: every clip is followed by a
reaction, every segment has a first-person sentence, every segment
addresses the listener ("why you'd care"), and no phrase from
[`noise_phrases.py`](../thestill/services/narration_prompts/noise_phrases.py)
appears. A soft miss earns the single retry with the miss named; if the
retry still misses, the script is accepted and the miss recorded
(`reactions_missing`, `noise_phrase_hits`). A retry that trips a hard
rule (budget, leak, unknown quote id) falls back to the earlier
soft-only attempt rather than to the link index. Hard rules keep their
fallback semantics.

### A/B two voices

```bash
python scripts/narration_ab.py --briefing <briefing-id> --runs 3 \
  --voices conversational_anchor conversational_v2 --out data/narration_ab
```

Runs each voice on the same inbox window, computes the register metrics
below plus two judged ones (ideas per episode, stakes lines) with the
`EVAL_JUDGE_*` judge (falling back to the pipeline provider), prints a
table with a merge-gate verdict per voice, and writes every script.

## Validation contract

Four rules enforced after the script-generation LLM call:

1. **Quote-id pool match** — every block of `kind: "quote"` references
   a `quote_id` that came from the verbatim pool we handed to the
   model. Invented ids fail validation.
2. **No-verbatim-leak** — an 8-word slice from any quote that appears
   verbatim inside a narration block (lowercased + collapsed
   punctuation) is treated as a paraphrase-of-the-quote and rejected.
   Quotes are cued, not retyped.
3. **Word-budget tolerance** — total narration words must land within
   `narration_word_budget × (1 ± 0.15)`.
4. **Non-empty output** — the model must return at least one block,
   and the computed narration word budget must be positive; either
   failing is treated as a validation failure rather than a crash.

A failed run regenerates once with a tightened prompt that includes
the failure tokens (`unknown_quote_id`, `verbatim_leak`,
`word_budget_high`, `word_budget_low`, `empty_blocks`). A second failure flips the
run to fallback mode — the link-index script renders behind a banner
and the JSON still serialises with the failure reasons in
`stats.fallback_reason` and a structured `narration.fallback` log
event for ops dashboards.

## JSON script schema

The on-disk JSON sidecar (`data/narrations/<briefing_id>-<slug>.json`)
is the canonical TTS contract. Schema version `phase2`:

```jsonc
{
  "generated_at": "2026-05-08T07:00:00+00:00",
  "target_duration_seconds": 300,
  "actual_duration_seconds": 292.5,
  "wpm": 150.0,
  "schema_version": "phase2",
  "mode": "narrated",            // "narrated" or "fallback"
  "fallback_reason": null,       // e.g. "word_budget_high,verbatim_leak" when mode=fallback
  "latency_ms": 4280,            // wall-clock around generate(), captured by NarrationRunner; null when generate() is called outside the runner (e.g. tests / programmatic callers / older artefacts)
  "briefing_id": "briefing-uuid-…", // null when the artefact wasn't produced via the runner (or predates the digest retirement)
  "slug": "medium",              // matches the second half of the filename basename
  "blocks": [
    {
      "kind": "narration",
      "section": "opener",
      "text": "Today's lead: …",
      "duration_seconds": 12.4
    },
    {
      "kind": "quote",
      "section": "segment-1",
      "quote_id": "q1",
      "episode_id": "ep-uuid-…",
      "podcast_title": "Lenny's Podcast",
      "speaker": "Zevi Arnovitz",
      "speaker_role": "guest",
      "text": "It's the best time to be a junior, contrary…",
      "start_seconds": 59.0,
      "duration_seconds": 12.0,
      "score": 0.7421
    }
  ],
  "episodes_covered": ["ep-uuid-…"],
  "episodes_in_tail":  [],
  "quote_pool_size": 6,          // spec #77: pool handed to the writer after the share cap
  "episodes_with_sidecar": 7,    // episodes that had a transcript to quote from at all
  "narration_words": 508,
  "stated_word_target": 372,     // the number the writer was told (ratio × budget)
  "noise_phrase_hits": 0,        // hits against narration_prompts/noise_phrases.py; a soft rule, never a fallback
  "reaction_count": 4,           // reaction blocks emitted
  "reactions_missing": 0,        // clips still without a reaction after the retry
  "first_person_sentences": 5,   // register metrics (services/narration/register.py)
  "reportage_sentences": 0,
  "scare_quote_count": 0,
  "sentence_len_p50": 15.0,
  "sentence_len_p90": 22.0,
  "bridges_unearned": 0          // segment openings that bridge to an untyped neighbour
}
```

The durable identifier for original-audio splicing is the
`(episode_id, start_seconds, duration_seconds)` triple on each quote
block — TTS can swap a synthesised quote for the real audio without
schema churn.

## Failure modes & observability

The runner emits two structured log events:

- `narration.run` — once per invocation, with `mode`,
  `target_seconds`, `actual_seconds`, `quote_count`, `latency_ms`,
  `fallback_reason`, and the spec #77 pool and register fields
  (`quote_pool_size`, `episodes_with_sidecar`, `narration_words`,
  `stated_word_target`, `noise_phrase_hits`).
- `narration.quote_pool_empty` (warning) — transcripts were present but
  no quote survived selection. This is never silent: it is the shape of
  the loader drift that produced quote-less briefings before spec #77.
- `narration.fallback` — emitted when validation fails twice, with
  the comma-joined failure reasons.

The dashboard tile at `GET /api/dashboard/narration` aggregates
`data/narrations/*.json` headers and surfaces:

- `total_runs`, `fallback_count`, `fallback_rate`
- `avg_actual_duration_seconds`, `avg_target_duration_seconds`
- `avg_latency_ms`
- `latest` (narration_id + generated_at + mode + duration + latency)

A fallback rate above ~15% over a meaningful sample is a signal to
revisit the prompt or the validation tolerances.

## Cost expectations

Two LLM calls per run (theme clustering + script generation). On
Sonnet-tier the per-run estimate is **~$0.02–$0.05 for a 5-minute
briefing**, dominated by the script-generation call (it carries the
quote pool + per-segment summaries). Prompt caching is honoured on
providers that support it via the existing
`generate_structured_cached` path.

For per-user briefings (spec [#36](../specs/36-per-user-digest-from-inbox.md))
the cost multiplies by user count. v1 keeps per-user briefings on the
link-index for that reason; revisit when usage justifies the spend.

## Development

The reference sample committed at
[`examples/narrations/example-digest-medium.{json,md}`](../examples/narrations/)
is a hand-curated artefact suitable as:

- A reading exercise — what does a narration look like?
- A frontend playground — load the markdown into the
  `NarrationView` Storybook to iterate on styling without standing up
  the pipeline.
- A TTS-pipeline target — develop against this file before integrating
  the live API.

Tests in
[`tests/unit/services/narration/`](../tests/unit/services/narration/)
cover the deterministic backbone (quote selection, transcript loader,
JSON renderer) and the LLM stages with `MockLLMProvider`. The
markdown renderer tests in
[`test_markdown_renderer.py`](../tests/unit/services/narration/test_markdown_renderer.py)
double-check the deep-link timestamps against the
`UrlGenerator.episode_at` helper.
