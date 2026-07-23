# LLM Call Tracing

> **Status:** 📝 Draft (2026-07-23)
> **Created:** 2026-07-23
> **Updated:** 2026-07-23
> **Author:** Engineering (pipeline observability)
> **Related:** [#18 segment-preserving-transcript-cleaning](18-segment-preserving-transcript-cleaning.md) (the per-batch cleaner whose calls are the primary trace subject; its legacy `debug/prompts/` mechanism was never carried forward — see [transcript_cleaning_processor.py](../thestill/core/transcript_cleaning_processor.py) "saving prompts there is a follow-up"), [#41 llm-prohibited-content-fallback](41-llm-prohibited-content-fallback.md) (refusals are exactly the calls worth capturing), [#42 robustness-and-failure-mode-hardening](42-robustness-and-failure-mode-hardening.md), [#53 eval-runs-and-summary-rubric](53-eval-runs-and-summary-rubric.md) (artifact-level provenance; this spec is its call-level complement), `feat/cleaning-provenance` (the `CleaningProvenance` sidecar stamp records *which* model produced an artifact; traces record *how*)

---

## Executive summary

Every pipeline stage's inputs and outputs are durably stored, but the LLM
exchanges that transform one into the other vanish the moment they
complete. When a cleaning run silently emits a raw chunk (observed
2026-07-23: 25% of a Mjesto Zločina episode passed through verbatim while
an earlier run had cleaned the same span correctly), there is nothing to
inspect — no record of what the model was asked, what it answered, whether
it errored, or what it cost.

This spec adds an **opt-in, file-based trace sink at the `LLMProvider`
seam**. One wrapper class captures every call from every call site
(segmented cleaner batches, facts extraction, summarizer, briefings, eval
judges) into append-only JSONL under `data/llm_traces/`, one line per
call: full messages (system prompts deduplicated by hash), response,
model, token usage, latency, finish reason, and the structlog correlation
ids (`episode_id`, `task_id`, `request_id`, …) already bound at call time.

Traces follow the house artifact pattern (#53, #54): plain files,
`jq`-able, append-only, no database, no external service. Off by default
(`LLM_TRACE=false`); a trace-write failure never fails the traced call.

Explicitly out of scope: always-on production tracing, DB storage, a web
UI, OpenTelemetry/Langfuse/PostHog integration, and automatic replay
harnesses (replay is Phase 3, gated on demand).

---

## Motivation

1. **Silent failures are currently undiagnosable.** The 2026-07-23
   Croatian-transcript audit found a ~13-minute span (39 paragraphs, 25%
   of the episode) of `mjesto-zlocina` #192 emitted as verbatim raw garble
   in the "clean" output, while the May 13 `.baseline.md` proves the same
   span was cleanable. Was it a provider error swallowed somewhere, an
   empty patch batch, a `PROHIBITED_CONTENT` pass-through (#41), or a
   parsing fallback? Unknowable — the batch requests and responses are
   gone. With traces, the answer is one `jq` query.

2. **Evals can compare artifacts but not explain them.** #53 records
   *which bytes* were judged and *by whom*, and `CleaningProvenance` now
   records which model produced an artifact — but neither can show *why*
   segment 412 came back unchanged. Call-level traces close the loop:
   artifact → producing calls → exact prompt and response.

3. **Prompt iteration is blind without before/after inputs.** The
   remedies planned for the cleaner (verbatim-guard, anti-hallucination
   rules, uncertainty markers) all change the system prompt. Captured
   traces from a real run are the raw material for offline A/B: re-send
   the identical batch payload under a candidate prompt or model and diff
   the patches — without re-running the pipeline or re-transcribing.

4. **Cost and latency are invisible.** Token usage is returned by every
   provider and dropped on the floor. There is no way to answer "what
   does cleaning a 110-minute episode cost?" or "which stage dominates
   spend?" today.

5. **The half-built predecessor was silently dropped.** The legacy
   cleaner saved prompts to `debug/prompts/`; the #18 segmented rewrite
   deleted the callback with a "follow-up" comment
   ([transcript_cleaning_processor.py:288-293](../thestill/core/transcript_cleaning_processor.py#L288-L293)).
   The empty `debug/prompts/` directories on disk are the fossil record.
   This spec is that follow-up, generalized: at the provider seam instead
   of one call site, capturing responses (which the old mechanism never
   did), and covering all stages instead of only cleaning.

---

## Design

### 1. Capture seam: a wrapping provider

One new class, `TracingLLMProvider(LLMProvider)`, wraps any concrete
provider and delegates every abstract method. The three generation
methods (`chat_completion`, `generate_structured`,
`generate_structured_cached`) record a trace line around the delegated
call; everything else passes through untouched.

Why a wrapper and not edits to the five concrete providers or the call
sites:

- **One seam, total coverage.** Every LLM call in the codebase flows
  through an `LLMProvider` instance. Wrapping at construction (the
  provider factory in `utils/config.py` / service wiring) captures the
  cleaner's batches, facts extraction, the summarizer, briefing
  generation, and #53 eval judges without touching any of them.
- **Zero drift risk.** Per-provider edits are five copies of the same
  bookkeeping (#42 FM-6, parallel-path drift). Call-site edits are a
  dozen.
- **Composability.** The wrapper stacks cleanly with whatever #41
  Option A (per-batch model fallback) eventually builds, and tests can
  wrap `MockLLMProvider` to assert trace behaviour.

The wrapper reports the *inner* provider's `get_provider_name()` /
`get_model_name()` so provenance stamps (`CleaningProvenance`) and
chunk-size auto-detection are unaffected by tracing being on or off.

### 2. Record shape

One JSON object per line, schema-versioned:

```json
{
  "schema_version": 1,
  "ts": "2026-07-23T09:14:03.221+00:00",
  "provider": "gemini",
  "model": "gemini-3-flash-preview",
  "method": "generate_structured_cached",
  "context": {"episode_id": "1e876a61", "task_id": "t-4821", "command_id": null},
  "system_prompt_sha256": "ac8c8363…",
  "messages": [{"role": "user", "content": "{\"previous_cleaned\": …}"}],
  "response": "{\"patches\": […]}",
  "response_model": "CleanupPatchBatch",
  "temperature": 0.0,
  "max_tokens": null,
  "usage": {"input_tokens": 5210, "output_tokens": 1804, "cached_input_tokens": 3900},
  "latency_ms": 2140,
  "finish_reason": "stop",
  "error": null
}
```

Field notes:

- **`context`** is read from `structlog.contextvars` at call time — the
  correlation ids the constitution already mandates (`request_id`,
  `command_id`, `task_id`, `episode_id`, `run_id`) are bound by the
  worker/CLI/web layers before any LLM call happens, so the trace layer
  gets attribution for free and adds no new plumbing.
- **System prompts are deduplicated by content hash.** The segmented
  cleaner repeats an identical multi-KB system prefix across every batch
  of an episode (that is what makes prompt caching work — #18). Storing
  it per-line would multiply trace size ~2×. Instead the `system` message
  is replaced by its SHA-256, and the full text is written once to
  `data/llm_traces/prompts/<sha256>.txt` on first sight. This mirrors
  #53's `prompt_sha256` convention, and the hash doubles as the
  prompt-identity key for grouping traces across runs. User messages are
  stored inline and verbatim — they are the per-call payload.
- **`response` is the raw provider text** (before sanitization/parsing),
  because malformed output is precisely what a debugging session needs to
  see. For structured calls, `response_model` names the schema that
  validated it. Control characters are escaped by JSON encoding, not
  stripped — the trace must show what the provider actually sent
  (#42 FM-7 forensics).
- **`usage`** comes from the provider response where available
  (all five providers return token counts); `null` where not.
- **On failure**, `response` is `null` and `error` carries the exception
  class, message, and — for #41-style refusals — the `finish_reason` and
  provider context. A refused batch that falls back to raw text is
  currently a single log line; with tracing on it becomes a permanent,
  queryable record.

### 3. Storage layout and lifecycle

```
data/llm_traces/
├── prompts/
│   └── <sha256>.txt            # deduplicated system prompts
└── 2026-07-23/
    ├── worker-83214.jsonl      # one file per process per day
    └── cli-84102.jsonl
```

- **One file per process per day** (`<proc-label>-<pid>.jsonl`). Appends
  within a process are sequential, so lines never interleave; separate
  processes (server worker vs a parallel CLI run) never share a file.
  No locking needed.
- **Append-only, no rewrite.** The sink holds the file open in append
  mode and flushes per line, so a crashed run's traces survive up to the
  last completed call.
- **Size**: a 110-minute episode cleaned in ~25–30 batches produces
  roughly 300–500 KB of JSONL (with system-prompt dedup). At current
  single-operator volume this is a few MB/day when enabled — no rotation
  machinery required. Retention is manual or via the existing cleanup
  path (`CLEANUP_DAYS` applies if wired in; see Open Questions).
- **Sensitivity**: traces contain transcript text — the same content
  already stored in `raw_transcripts/` and `clean_transcripts/` under the
  same `data/` root, so no new confidentiality class is created. API keys
  never appear in messages. The constitution's "never log secrets or PII"
  rule is satisfied by construction; the sink writes message content, not
  environment or headers.

### 4. Gating and configuration

```bash
LLM_TRACE=false            # default: off — zero overhead, zero disk
LLM_TRACE_DIR=             # optional override; default {data}/llm_traces
```

- When off, the factory returns the bare provider — the wrapper is not
  in the stack at all (not a no-op wrapper: *absent*).
- When on, it applies process-wide: all stages, all providers. Per-stage
  selectivity is deliberately rejected — the interesting incidents (a
  cleaner batch failing because facts extraction produced a garbage
  keyword list) span stages, and a filter knob invites the "tracing was
  on but not for that stage" hole.
- `thestill eval run` may later force-enable tracing for judge calls so
  every eval run carries its own call record (Open Question 3).

### 5. Relationship to provenance and evals

The three layers are complementary, not overlapping:

| Layer | Granularity | Question it answers |
|---|---|---|
| `CleaningProvenance` (sidecar) | artifact | which model/prompt produced this file? |
| #53 eval runs | artifact × judge | did quality move between two points in time? |
| **This spec** | call | what exactly was asked/answered, and what did it cost? |

Traces link to the other layers without new fields: `episode_id` joins a
trace line to its artifacts, `system_prompt_sha256` joins to the prompt
revision (`CLEANUP_PROMPT_VERSION` bumps change the hash), and the trace
date brackets an eval run's `created_at`.

### 6. CLI (Phase 2)

Files are `jq`-able by design, so the CLI stays minimal:

```bash
thestill trace stats [--since DATE] [--episode-id ID]
    # calls, tokens in/out/cached, est. cost, latency p50/p95 — grouped
    # by (provider, model, method)
thestill trace show --episode-id ID [--errors-only]
    # human-readable dump of one episode's calls, prompts resolved
```

`stats` is the payoff for motivation #4; `show` for motivation #1. No
`list`/`compare` — that is what `jq` and #53 are for.

### 7. Replay (Phase 3 — future, gated on demand)

A captured trace line contains everything needed to re-issue the call:
resolve `system_prompt_sha256`, swap the model or edit the system prompt,
re-send the identical user payload, diff the patches. A
`thestill trace replay` command would turn any captured run into an
offline prompt/model A/B harness feeding #53's rubrics. Deliberately not
designed further here — Phase 1's record shape (verbatim user messages,
hash-resolvable system prompts, named response models) is the contract
that keeps this possible.

### Rejected alternatives

- **OpenTelemetry / Langfuse / PostHog LLM analytics.** External
  infrastructure (a collector, a SaaS account, a network dependency in
  the pipeline's hot path) for a single-operator tool whose every other
  artifact is a local file. The JSONL schema loses nothing — if a hosted
  backend (#43) later wants OTel, the sink is one exporter away.
- **Store traces in the database.** Same reasoning as #53's no-DB
  non-goal: traces are developer tooling; files are `jq`-able, diffable,
  and require no migration across the dual SQLite/Postgres backends.
- **Always-on tracing.** Doubles the disk footprint of every pipeline
  run to serve a debugging need that is episodic. Off by default; flip it
  on for incident reproduction, prompt work, and eval baselining.
- **Resurrect the `debug/prompts/` callback.** Per-call-site capture is
  exactly what drifted out of existence once already (#42 FM-6); it also
  never captured responses, which are half the story.

---

## Testing

- **Unit — wrapper transparency**: every `LLMProvider` method delegates;
  `get_model_name`/`get_provider_name` report the inner provider; a
  wrapped `MockLLMProvider` behaves identically to a bare one from the
  caller's perspective.
- **Unit — record shape**: one line per call; schema fields present;
  system prompt replaced by hash and written once to `prompts/`; second
  call with the same system prompt does not rewrite the file; user
  messages verbatim; structured responses recorded raw.
- **Unit — failure isolation (FM-1)**: sink raising `OSError` (disk
  full, permission denied) logs a warning and the traced call still
  returns its result; provider raising propagates *and* writes an
  `error` trace line first.
- **Unit — correlation**: bound `structlog.contextvars` appear in
  `context`; absent ids are `null`, never missing keys.
- **Integration**: `clean-transcript` on a fixture episode with
  `LLM_TRACE=true` and a mock provider produces a JSONL whose per-batch
  lines join back to the cleaned sidecar's segments; with
  `LLM_TRACE=false`, `data/llm_traces/` is not created.
- **No live LLM calls in CI** (#53 precedent).

---

## Failure-mode checklist (spec #42)

| FM | Where it bites here | Mitigation |
|---|---|---|
| FM-1 errors-as-empty-results | trace-write failure breaking the pipeline call | sink is fire-and-forget: catch-all around the write, `llm_trace_write_failed` warning, call result unaffected |
| FM-2 checkpoint-before-durability | trace line written before the call completes | line is written *after* the response/error is known; a crash mid-call loses only that line |
| FM-3 mixed-tz | `ts` comparisons across files | all timestamps ISO-8601 UTC with offset, per house rule |
| FM-4 silent degradation | tracing "on" but sink dead → operator thinks traces exist | first write failure logs at WARNING with the path; `trace stats` reports file/line counts so absence is visible, not inferred |
| FM-5 consistent-mock tests | wrapper tested only against mocks that never error | failure-isolation tests use a sink stubbed to raise; integration test uses the real filesystem |
| FM-6 parallel-path drift | per-provider or per-call-site capture copies | single wrapper at the factory seam; concrete providers untouched |
| FM-7 unsanitized-LLM-output | control chars / hostile content in responses corrupting the JSONL | JSON encoding escapes everything; raw response stored deliberately (forensics), never re-interpreted by the trace layer |

---

## Open questions

1. **Retention.** Wire `data/llm_traces/` into the existing
   `CLEANUP_DAYS` sweep, or leave pruning manual? Files are small and
   valuable while iterating on prompts; auto-pruning a trace that
   explains a still-open incident would be a self-inflicted FM-4.
2. **Streaming calls.** No current call site streams (the segmented
   path dropped the callback), so Phase 1 records complete
   request/response pairs only. If streaming returns, the sink records
   the assembled final text plus a `streamed: true` flag.
3. **Eval coupling.** Should `thestill eval run` force tracing for judge
   calls and record the trace file path in the run manifest? Cheap and
   symmetrical, but it makes eval runs grow a dependency on this spec —
   decide when Phase 2 lands.
4. **Sampling.** A future `LLM_TRACE_SAMPLE=0.1` for hosted-scale (#43)
   cost telemetry without full payload capture. Not needed at current
   volume; the schema's `usage`-only subset would be the shape.

---

## Revision history

- **2026-07-23** — Initial draft, following the 2026-07-23 Croatian
  transcript-cleaning audit (silent 25% chunk fallback in
  `mjesto-zlocina` #192 undiagnosable without call records).
