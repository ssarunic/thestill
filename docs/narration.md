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
| `LLM_PROVIDER` | Same provider as the rest of the pipeline (`anthropic` / `openai` / `gemini` / `mistral` / `ollama`). | (per `.env.example`) |

The CLI also accepts `--no-narrate` to opt out per-run when you only
want the link-index (e.g., offline testing). The `thestill narrate`
standalone command always requires an LLM provider and a configured
briefing record.

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
[`thestill/services/narration_prompts/`](../thestill/services/narration_prompts/).
v1 ships one default voice
([`default_anchor.md`](../thestill/services/narration_prompts/default_anchor.md)) —
informed, slightly wry, news-anchor pacing.

The prompt is **read on every run** (no in-process caching) so an
operator can edit the file and re-run `thestill narrate` to A/B a new
voice without restarting any long-running process. The file is small
(under 4 KB) so the unconditional read is cheap.

To ship a new voice, drop a sibling Markdown file next to
`default_anchor.md` and pass its contents to `NarrationGenerator(...,
anchor_prompt=...)`. A future spec promotes the voice picker to
config; for now the indirection lives at the constructor.

## Validation contract

Three rules enforced after the script-generation LLM call:

1. **Quote-id pool match** — every block of `kind: "quote"` references
   a `quote_id` that came from the verbatim pool we handed to the
   model. Invented ids fail validation.
2. **No-verbatim-leak** — an 8-word slice from any quote that appears
   verbatim inside a narration block (lowercased + collapsed
   punctuation) is treated as a paraphrase-of-the-quote and rejected.
   Quotes are cued, not retyped.
3. **Word-budget tolerance** — total narration words must land within
   `narration_word_budget × (1 ± 0.15)`.

A failed run regenerates once with a tightened prompt that includes
the failure tokens (`unknown_quote_id`, `verbatim_leak`,
`word_budget_high`, `word_budget_low`). A second failure flips the
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
  "episodes_in_tail":  []
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
  and `fallback_reason`.
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
