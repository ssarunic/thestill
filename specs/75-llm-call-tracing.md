# LLM Call Tracing

> **Status:** 📝 Draft v2 (2026-07-23) — v1 wrapper design replaced by base-class observer hook after review (a wrapper cannot see raw responses/usage); PII posture, concurrency, and replay contract hardened
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
seam**. A base-class observer hook — one `self._emit_trace(...)` call in
each concrete provider's generation methods, at the single point where
the raw SDK response is in hand — captures every call from every call
site (segmented cleaner batches, facts extraction, summarizer, briefings,
eval judges) into append-only JSONL under `data/llm_traces/`, one line
per call: full messages (system prompts deduplicated by hash), raw
response text, effective request configuration, token usage, latency,
finish reason, an invocation id linking the line to the artifact it
produced, and the structlog correlation ids bound at call time.

Traces follow the house artifact pattern (#53, #54): plain files,
`jq`-able, append-only, no database, no external service. They are
**content-bearing artifacts in the same protection class as raw
transcripts — not logs**; constitution §6's "never log full file
contents or PII" continues to govern the structlog stream unchanged.
Off by default (`LLM_TRACE=false`); a trace-write failure never fails
the traced call.

Explicitly out of scope: always-on production tracing, DB storage, a web
UI, OpenTelemetry/Langfuse/PostHog integration, and an automatic replay
harness (replay is Phase 3; Phase 1 only pins the record contract that
keeps it possible).

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

### 1. Capture seam: base-class observer hook

**A pure wrapper cannot do this job** — the v1 design was wrong.
`generate_structured` / `generate_structured_cached` return only the
validated Pydantic object; every concrete implementation discards the
SDK response (raw text, usage, finish reason) internally before
returning (e.g. the OpenAI and Gemini `generate_structured` bodies). A
`TracingLLMProvider` wrapping the public interface would see none of the
metadata the record schema promises.

Instead, the seam is an **observer hook on the `LLMProvider` base
class**:

- `LLMProvider` gains an optional `trace_sink: Optional[TraceSink]`
  (set by the provider factory when `LLM_TRACE=true`; `None` otherwise)
  and one protected helper, `_emit_trace(...)`, that builds and writes
  the record. All bookkeeping — enabled check, hashing, dedup, record
  assembly, locking, the FM-1 catch-all — lives in this one helper and
  the `TraceSink` it delegates to. When `trace_sink is None` the helper
  returns immediately.
- Each concrete provider's generation methods (`chat_completion`,
  `generate_structured`, `generate_structured_cached` where overridden)
  add a single `self._emit_trace(...)` call at the point where the raw
  SDK response exists, passing: the messages as sent, raw response text,
  usage, finish reason, the effective request parameters (see §2), and
  the exception on the error path.

This is per-provider instrumentation — the thing v1 wanted to avoid —
but reduced to its irreducible minimum: one data-handoff line per
method, with zero logic in the providers. The #42 FM-6 drift risk
("provider added later forgets to emit") is mitigated structurally, not
by review vigilance: a **shared contract-test suite** parametrized over
all concrete providers (SDK clients mocked) asserts that success, error,
and refusal paths each produce exactly one trace record with the
required fields. A provider that forgets the emit call fails CI.

The factory keeps tracing invisible to callers: `get_provider_name()`,
`get_model_name()`, and chunk-size auto-detection are unaffected by
whether a sink is attached.

### 2. Record shape

One JSON object per line, schema-versioned:

```json
{
  "schema_version": 1,
  "ts": "2026-07-23T09:14:03.221+00:00",
  "invocation_id": "inv-9f2c41d8",
  "provider": "gemini",
  "model": "gemini-3-flash-preview",
  "method": "generate_structured_cached",
  "context": {"episode_id": "1e876a61", "task_id": "t-4821", "command_id": null},
  "system_prompt_sha256": "ac8c8363…",
  "messages": [{"role": "user", "content": "{\"previous_cleaned\": …}"}],
  "request": {
    "temperature": 0.0,
    "max_tokens": 8192,
    "response_format": {"type": "json_object"},
    "cache_system_message": true,
    "provider_options": {}
  },
  "response": "{\"patches\": […]}",
  "response_model": "CleanupPatchBatch",
  "response_schema_sha256": "77b1a02f…",
  "usage": {"input_tokens": 5210, "output_tokens": 1804, "cached_input_tokens": 3900},
  "latency_ms": 2140,
  "finish_reason": "stop",
  "error": null
}
```

Field notes:

- **`invocation_id`** is a short unique id minted once per pipeline
  invocation — one `clean_transcript` run, one summarize call, one eval
  run — and carried on every line that invocation produces. It is the
  disambiguator between two same-day re-runs of the same episode with
  the same model and prompt, and the join key to artifacts (§5).
- **`context`** is read from `structlog.contextvars` at emit time. The
  task worker already binds `task_id`/`episode_id`; the web layer binds
  `request_id`; #53 binds `run_id`. The CLI, however, binds only
  `command_id`/`command_name` ([cli_logging.py:87](../thestill/utils/cli_logging.py#L87))
  and its pipeline loops carry no per-episode context — so **Phase 1
  includes binding `episode_id` (via
  `structlog.contextvars.bound_contextvars`) around each episode
  iteration of the CLI pipeline commands** (`clean-transcript`,
  `transcribe`, `summarize`, `downsample`). Without that, `trace show
  --episode-id` would silently return nothing for CLI runs — the
  primary debugging workflow broken on the primary invocation path.
  This also improves ordinary CLI logs, which currently cannot be
  filtered per episode either. Absent ids are recorded as `null`, never
  omitted keys.
- **`request`** captures the *effective* request configuration — after
  provider defaults are applied, not the caller's arguments: resolved
  temperature (or its absence for non-supporting models), effective
  max tokens, `response_format`, `cache_system_message`, and a
  `provider_options` bag for provider-specific settings
  (reasoning/thinking budgets, safety settings) as they are added.
  This is what makes a trace line a sufficient replay contract (§7);
  a record that omits it can only replay calls whose defaults happen
  not to have changed since capture.
- **System prompts are deduplicated by content hash.** The segmented
  cleaner repeats an identical multi-KB system prefix across every batch
  of an episode (that is what makes prompt caching work — #18). Storing
  it per-line would multiply trace size ~2×. Instead the `system` message
  is replaced by its SHA-256, and the full text is written once to
  `data/llm_traces/prompts/<sha256>.txt` on first sight (atomically —
  §3). This mirrors #53's `prompt_sha256` convention, and the hash
  doubles as the prompt-identity key for grouping traces across runs.
  User messages are stored inline and verbatim — they are the per-call
  payload.
- **Structured-output schemas are snapshotted the same way.**
  `response_model` (the class name) is a human label only; the durable
  contract is `response_schema_sha256`, whose full JSON Schema is
  written once to `data/llm_traces/schemas/<sha256>.json`. A class that
  is later renamed, moved, or has fields changed does not orphan old
  traces — replay validates against the *captured* schema.
- **`response` is the raw provider text** (before sanitization/parsing),
  because malformed output is precisely what a debugging session needs to
  see. Control characters are escaped by JSON encoding, not stripped —
  the trace must show what the provider actually sent (#42 FM-7
  forensics).
- **`usage`** comes from the provider response where available
  (all five providers return token counts); `null` where not.
- **On failure**, `response` is `null` and `error` carries the exception
  class, message, and — for #41-style refusals — the `finish_reason` and
  provider context. A refused batch that falls back to raw text is
  currently a single log line; with tracing on it becomes a permanent,
  queryable record.

### 3. Storage layout, concurrency, and lifecycle

```
data/llm_traces/            # directory mode 0700
├── prompts/
│   └── <sha256>.txt        # deduplicated system prompts
├── schemas/
│   └── <sha256>.json       # deduplicated response-model JSON Schemas
└── 2026-07-23/
    ├── worker-83214.jsonl  # one file per process per day
    └── cli-84102.jsonl
```

- **Concurrency: one process-wide writer, internally locked.** The v1
  assumption that in-process calls are sequential is false — the task
  worker dispatches concurrent tasks via `asyncio.create_task` and runs
  handlers in threads ([task_worker.py:543](../thestill/core/task_worker.py#L543)),
  so multiple provider instances emit from multiple threads of one PID.
  The `TraceSink` is therefore a per-process singleton: appends
  (serialize + write + flush) happen under a `threading.Lock`, so lines
  never interleave. Cross-process safety still comes from the
  per-PID filename — two processes never share a file.
- **Atomic dedup-file creation.** `prompts/` and `schemas/` files are
  written to a temp name and `os.replace`d into place; two threads (or
  processes) discovering the same hash concurrently both succeed, and a
  crash mid-write never leaves a torn file behind. Content-addressing
  makes the race benign — both writers produce identical bytes.
- **Daily rollover** is checked under the writer lock: when the UTC date
  of a write differs from the open file's date, the sink closes it and
  opens `<new-date>/<label>-<pid>.jsonl`. A process spanning midnight
  splits its lines across two dated files; `invocation_id` is the
  cross-file join key, so no invocation is lost to the boundary.
- **Append-only, flushed per line** — a crashed run's traces survive up
  to the last completed call (FM-2: the line is written *after* the
  response or error is known, never before).
- **Size**: a 110-minute episode cleaned in ~25–30 batches produces
  roughly 300–500 KB of JSONL (with prompt dedup). At current
  single-operator volume this is a few MB/day when enabled.

### 4. Sensitivity: traces are protected artifacts, not logs

Constitution §6 forbids logging "secrets, tokens, full file contents, or
PII" — and traces contain full transcript text, which can include
personal names, medical details, or listener-letter content. The v1
claim that this was "satisfied by construction" was wrong. The resolved
posture:

- **Traces are content-bearing pipeline artifacts, in the same
  protection class as `raw_transcripts/` and `clean_transcripts/`** —
  which already hold the identical content durably. Constitution §6
  governs the *log stream* (structlog output, which may be shipped to
  console, files, or cloud collectors) and is **unchanged** by this
  spec: no message content, transcript text, or trace payload ever
  passes through structlog. The trace layer logs only metadata events
  (`llm_trace_write_failed`, paths, counts).
- **Explicit controls**, because traces *duplicate* protected content
  into a new location: `data/llm_traces/` is created `0700`; traces are
  covered by the same backup/exclusion decisions as transcript
  artifacts; and the default retention is **auto-pruning after
  `CLEANUP_DAYS`** (same knob as audio cleanup) so payload copies don't
  accumulate unbounded. A `--keep` marker file in a dated directory
  exempts it from the sweep (for traces pinned to an open incident or a
  committed eval baseline).
- **Hosted mode (#43) must not enable full-payload capture.** On a
  multi-tenant deployment, `LLM_TRACE=true` is refused at startup
  (config validation error, not a silent ignore — FM-4); the hosted
  observability need is the usage-only sampled telemetry sketched in
  Open Question 3. Full-payload tracing is a single-operator,
  local-disk debugging tool by definition of this policy, not merely by
  default.
- The constitution gains one clarifying sentence under §6 (shipped with
  this spec): the no-content rule applies to the log stream; durable
  content-bearing artifacts (transcripts, traces) are governed by their
  own specs' protection requirements.

### 5. Gating and configuration

```bash
LLM_TRACE=false            # default: off — zero overhead, zero disk
LLM_TRACE_DIR=             # optional override; default {data}/llm_traces
```

- When off, the factory attaches no sink — `_emit_trace` short-circuits
  on `None`; no trace directory is created.
- When on, it applies process-wide: all stages, all providers. Per-stage
  selectivity is deliberately rejected — the interesting incidents (a
  cleaner batch failing because facts extraction produced a garbage
  keyword list) span stages, and a filter knob invites the "tracing was
  on but not for that stage" hole.
- Refused (startup error) when the deployment is multi-tenant (§4).

### 6. Relationship to provenance and evals

The three layers are complementary, not overlapping:

| Layer | Granularity | Question it answers |
|---|---|---|
| `CleaningProvenance` (sidecar) | artifact | which model/prompt produced this file? |
| #53 eval runs | artifact × judge | did quality move between two points in time? |
| **This spec** | call | what exactly was asked/answered, and what did it cost? |

`invocation_id` makes the artifact↔trace join exact rather than
heuristic: `CleaningProvenance` gains an optional
`trace_invocation_id: Optional[str]` (populated only when tracing was
on for that run; `None` otherwise and on all pre-existing sidecars),
and a #53 run manifest can record the same id per item (Open
Question 2). `episode_id` + date + prompt hash remain useful for
ad-hoc grouping, but two same-day re-runs of the same episode under the
same model are only distinguishable by invocation id — v1's
hash-and-date linkage was ambiguous exactly where traces matter most
(before/after re-runs while iterating on a prompt).

### 7. CLI (Phase 2)

Files are `jq`-able by design, so the CLI stays minimal:

```bash
thestill trace stats [--since DATE] [--episode-id ID]
    # calls, tokens in/out/cached, est. cost, latency p50/p95 — grouped
    # by (provider, model, method)
thestill trace show --episode-id ID [--invocation-id INV] [--errors-only]
    # human-readable dump of one episode's calls, prompts resolved
```

`stats` is the payoff for motivation #4; `show` for motivation #1. No
`list`/`compare` — that is what `jq` and #53 are for.

### 8. Replay (Phase 3 — future, gated on demand)

A captured trace line contains everything needed to re-issue the call
*byte-for-byte*: resolve `system_prompt_sha256` and
`response_schema_sha256` from the content stores, apply the recorded
`request` block (not today's defaults), re-send the identical user
payload, validate against the captured schema, diff the patches. A
`thestill trace replay` command would turn any captured run into an
offline prompt/model A/B harness feeding #53's rubrics. Deliberately not
designed further here — Phase 1's record contract (verbatim user
messages, hash-resolvable prompts *and schemas*, effective request
configuration) is scoped precisely so that replay needs no information
that wasn't captured.

### Rejected alternatives

- **Pure `TracingLLMProvider` wrapper (v1).** Cannot observe raw
  responses, usage, or finish reasons — the public `LLMProvider`
  interface returns validated objects only, and implementations discard
  the SDK response internally. Kept here as a warning: any future
  "just wrap it" refactor re-imports the same blindness.
- **Response envelope (change generation methods to return
  `(result, metadata)`).** Would capture the same data without
  per-provider emit calls, but changes the signature of every generation
  method and touches every call site in the codebase — far more churn
  than one handoff line per provider method, for the same information.
- **OpenTelemetry / Langfuse / PostHog LLM analytics.** External
  infrastructure (a collector, a SaaS account, a network dependency in
  the pipeline's hot path) for a single-operator tool whose every other
  artifact is a local file — and shipping full transcript payloads to a
  third party would create a new confidentiality exposure §4 exists to
  prevent. The JSONL schema loses nothing — if a hosted backend (#43)
  later wants OTel, the sink is one exporter away, carrying the
  usage-only subset.
- **Store traces in the database.** Same reasoning as #53's no-DB
  non-goal: traces are developer tooling; files are `jq`-able, diffable,
  and require no migration across the dual SQLite/Postgres backends.
- **Always-on tracing.** Doubles the disk footprint of every pipeline
  run to serve a debugging need that is episodic, and turns the §4
  content-duplication concern from a bounded opt-in into a standing
  fact. Off by default; flip it on for incident reproduction, prompt
  work, and eval baselining.
- **Resurrect the `debug/prompts/` callback.** Per-call-site capture is
  exactly what drifted out of existence once already (#42 FM-6); it also
  never captured responses, which are half the story.

---

## Testing

- **Contract suite over all providers (the FM-6 backstop)**: one
  parametrized test module, SDK clients mocked, asserting every concrete
  provider emits exactly one record per generation call on the success
  path, the error path, and the refusal path — with `usage`,
  `finish_reason`, `request`, and raw `response` populated. A new
  provider that forgets `_emit_trace` fails CI.
- **Unit — record shape**: schema fields present; system prompt replaced
  by hash and written once; schema snapshot written once; second sight
  of the same hash does not rewrite; user messages verbatim; absent
  context ids are `null`.
- **Unit — concurrency**: N threads emitting through one sink produce N
  valid, non-interleaved JSONL lines; concurrent first-sight of the same
  prompt hash leaves one intact file; a write racing the midnight
  rollover lands in exactly one dated file.
- **Unit — failure isolation (FM-1)**: sink raising `OSError` (disk
  full, permission denied) logs a warning and the traced call still
  returns its result; provider raising propagates *and* writes an
  `error` trace line first.
- **Unit — CLI context binding**: the pipeline commands bind/unbind
  `episode_id` per iteration; a two-episode `clean-transcript` run
  yields traces attributable to each episode, and log lines between
  iterations carry no stale id.
- **Integration**: `clean-transcript` on a fixture episode with
  `LLM_TRACE=true` and a mock provider produces a JSONL whose per-batch
  lines join back to the cleaned sidecar via `trace_invocation_id`;
  with `LLM_TRACE=false`, `data/llm_traces/` is not created.
- **No live LLM calls in CI** (#53 precedent).

---

## Failure-mode checklist (spec #42)

| FM | Where it bites here | Mitigation |
|---|---|---|
| FM-1 errors-as-empty-results | trace-write failure breaking the pipeline call | sink is fire-and-forget: catch-all around the write, `llm_trace_write_failed` warning, call result unaffected |
| FM-2 checkpoint-before-durability | trace line written before the call completes | line is written *after* the response/error is known; a crash mid-call loses only that line; dedup files written temp-then-rename |
| FM-3 mixed-tz | `ts` comparisons and rollover boundaries across files | all timestamps ISO-8601 UTC with offset; rollover keyed on UTC date |
| FM-4 silent degradation | tracing "on" but sink dead → operator thinks traces exist; or `LLM_TRACE` silently ignored in hosted mode | first write failure logs at WARNING with the path; `trace stats` reports file/line counts so absence is visible; hosted mode *refuses* the flag at startup instead of ignoring it |
| FM-5 consistent-mock tests | wrapper tested only against mocks that never error | contract suite covers error and refusal paths; failure-isolation tests use a sink stubbed to raise; concurrency tests use the real filesystem |
| FM-6 parallel-path drift | a provider added later forgets its `_emit_trace` calls | bookkeeping centralized in the base-class helper; parametrized contract suite over all concrete providers fails CI on a missing emit |
| FM-7 unsanitized-LLM-output | control chars / hostile content in responses corrupting the JSONL | JSON encoding escapes everything; raw response stored deliberately (forensics), never re-interpreted by the trace layer |

---

## Open questions

1. **Retention pinning UX.** Default retention is the `CLEANUP_DAYS`
   sweep (§4); is a `--keep` marker file the right pinning mechanism,
   or should `thestill trace` grow `pin`/`unpin` subcommands in
   Phase 2?
2. **Eval coupling.** Should `thestill eval run` force tracing for judge
   calls and record `trace_invocation_id` per manifest item? Cheap and
   symmetrical, but it makes eval runs grow a dependency on this spec —
   decide when Phase 2 lands.
3. **Sampling.** A future `LLM_TRACE_SAMPLE=0.1` usage-only mode (no
   payloads) as the hosted-scale (#43) cost-telemetry shape — the only
   trace mode §4 permits on multi-tenant deployments. Not needed at
   current volume.
4. **Streaming calls.** No current call site streams (the segmented
   path dropped the callback), so Phase 1 records complete
   request/response pairs only. If streaming returns, the sink records
   the assembled final text plus a `streamed: true` flag.

---

## Revision history

- **2026-07-23 (v2)** — Review pass, six findings resolved: wrapper
  design replaced with base-class `_emit_trace` observer hook +
  provider contract suite (a wrapper cannot see raw responses/usage);
  Phase 1 now includes per-episode `episode_id` context binding in CLI
  pipeline loops (CLI binds only `command_id` today, breaking
  `--episode-id` joins); traces reclassified as protected artifacts
  with explicit controls (0700, `CLEANUP_DAYS` retention, hosted-mode
  refusal, constitution §6 clarification) instead of the incorrect
  "satisfied by construction" claim; sink made a locked per-process
  singleton with atomic dedup writes and defined midnight rollover
  (task worker runs concurrent threaded handlers); `invocation_id`
  added to every line and `CleaningProvenance` to disambiguate
  same-day re-runs; `request` block + response-schema snapshots added
  so replay needs no post-capture information.
- **2026-07-23 (v1)** — Initial draft, following the 2026-07-23
  Croatian transcript-cleaning audit (silent 25% chunk fallback in
  `mjesto-zlocina` #192 undiagnosable without call records).
