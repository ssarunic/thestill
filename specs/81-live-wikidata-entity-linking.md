# Live Wikidata Entity Linking Specification

> **Status:** 🚧 Phase 1 in progress — linker, cache, config switch and deferral implemented on `feat/81-live-wikidata-entity-linking` (2026-09-21, default still `refined`); the `entity-linking` eval is the second PR
> **Created:** 2026-09-21
> **Updated:** 2026-09-21
> **Priority:** High — the 494-episode entity backfill on prod waits on this
> **Author:** Product & Engineering
> **Related:** [#28 corpus-search-and-entities](28-corpus-search-and-entities.md), [#42 robustness-and-failure-mode-hardening](42-robustness-and-failure-mode-hardening.md), [#47 auto-entity-enrichment-stage](47-auto-entity-enrichment-stage.md), [#53 eval-runs-and-summary-rubric](53-eval-runs-and-summary-rubric.md), [#66 aws-single-ec2-hosting](66-aws-single-ec2-hosting.md), [#75 llm-call-tracing](75-llm-call-tracing.md)

---

## Executive Summary

Entity linking — turning the mention "Dario Amodei" into Wikidata `Q…` —
runs on ReFinED, a research library whose last code change was November 2022
and whose knowledge is a 2022 Wikipedia dump. It cannot know anyone or
anything that became notable since, which for an AI and technology podcast
corpus is most of what matters. 40% of all mentions are unresolvable today.

This spec replaces ReFinED with a linker that is **current by
construction**: look each name up in live Wikidata, then let the pipeline
LLM choose among the candidates using the surrounding sentence.

1. **Candidates** — for each distinct name in an episode, ask Wikidata's
   search API for the top matches, with labels, descriptions and types.
2. **Choice** — one structured LLM call per episode receives every name,
   its context sentences and its candidates, and returns a QID or "none"
   for each.
3. **Validation** — a returned QID is accepted only if it was one of the
   candidates offered for that name. The LLM chooses; it never invents.
4. **Cache** — decisions are stored per name and podcast, so a recurring
   host or company costs nothing after its first episode.

**Key principle:** the linker is a drop-in behind the existing
`EntityResolver.resolve()` contract. Overrides, the blacklist, anchors,
coreference, alias merging, type reclassification and enrichment are
untouched. Wikidata QIDs are stable identifiers, so the 468,000 mentions
already linked stay valid — nothing is re-labelled.

GLiNER (mention finding) stays. It is actively maintained and is not the
problem.

---

## Problem

Measured on prod, 2026-09-21 (793,021 mentions):

| Status | Method | Mentions |
|---|---|---:|
| resolved | anchor (host/guest/recurring match) | 321,316 |
| **unresolvable** | — | **318,207** |
| resolved | direct (ReFinED) | 134,185 |
| resolved | coref | 12,876 |
| ambiguous | — | 6,203 |

- **ReFinED links only 17% of mentions.** Anchors do most of the work;
  ReFinED's share is 134k of 793k.
- **The knowledge is frozen in 2022.** Both published entity sets
  (`wikipedia`, ~6M entities; `wikidata`, ~33M) were built from the same
  2022 dumps. No newer set exists. On the first prod episode processed
  after the 2026-09-21 resize, "Dario Amodei" and "Ed Zitron" came back
  unresolvable.
- **Missing entities become wrong links, not just gaps.** "Anthropic" links
  to Q240581 (the anthropic principle, ~3,500 mentions) because the company
  is not in the set. On that same first episode, "Truman" linked to Harry
  S. Truman 21 times in a discussion of *The Truman Show*.
- **The software is unmaintained and already breaking.** It installs from a
  git commit ([pyproject.toml:167](../pyproject.toml#L167)) because it was
  never published to PyPI. It needs two monkey-patches to load at all
  ([entity_resolver.py:541](../thestill/core/entity_resolver.py#L541),
  [:573](../thestill/core/entity_resolver.py#L573)). On 2026-09-19
  transformers 5 removed an API it calls and 641 mentions were silently
  written off as unresolvable before the failure was noticed.
- **It is expensive to host.** 8.6 GB of downloaded index, several GB of
  RAM, a ~90 s model load. It is the reason prod ran without entity
  extraction from 2026-08-06 to 2026-09-21 and the reason the instance is a
  t4g.large ([#66](66-aws-single-ec2-hosting.md)).

The unresolvable tail is concentrated: 318k unresolvable mentions are only
44,574 distinct names, and **374 names account for half of them**. An
episode has 92 distinct names on average. Both facts make a cached,
per-name design cheap.

---

## Goals

- People, companies and products that became notable after 2022 resolve to
  their Wikidata QID.
- No frozen knowledge snapshot anywhere in the linking path; nothing to
  rebuild on a schedule.
- ReFinED, its git dependency, its monkey-patches and its 8.6 GB index are
  removed.
- Link quality is **measured** against ReFinED on a fixed episode set
  before cutover, and does not regress on the entities ReFinED gets right.
- A linker outage never records mentions as unresolvable
  ([#42](42-robustness-and-failure-mode-hardening.md):
  errors-as-empty-results).
- The existing `unresolvable` backlog can be swept through the new linker
  with one command.

## Non-goals

- **Replacing GLiNER.** Mention detection is out of scope.
- **Re-linking mentions that are already resolved.** Correcting existing
  wrong links (Anthropic → anthropic principle) is a follow-up sweep, scoped
  under Open questions; this spec only guarantees new links are made by the
  new linker.
- **Creating Wikidata entries** for people who have none. "None of these"
  remains a valid, final answer; the local slug-only entity path is kept.
- **A local Wikidata mirror.** Considered and deferred — see Alternatives.
- **Schema changes to `entities` or `entity_mentions`.** One new cache table
  only.

---

## Design

### Where it plugs in

`handle_resolve_entities`
([task_handlers.py:1169](../thestill/core/task_handlers.py#L1169)) already
isolates the model behind one call:

```python
forced_results, remaining = _apply_overrides(repo, pending)      # :1225
resolver_results = resolver.resolve(remaining, is_blacklisted=repo.is_blacklisted)  # :1229
```

`resolve()` takes pending `EntityMention`s and returns one
`ResolutionResult` per mention (`mention_id`, `entity`, `status`, `method`).
Everything after it — `upsert_entity`, `resolve_mention`, the coreference
pass, alias merging — consumes that list and does not care who produced it.

The new linker implements the same contract. `_get_or_create_entity_resolver`
([task_handlers.py:1408](../thestill/core/task_handlers.py#L1408)) picks the
implementation from config.

### Module layout

```text
thestill/core/entity_linking/
├── __init__.py
├── protocol.py          # EntityLinker Protocol: resolve(mentions, *, is_blacklisted) -> List[ResolutionResult]
├── candidates.py        # WikidataCandidateSource: search + describe, rate-limited
├── chooser.py           # LLMCandidateChooser: one structured call per episode batch
├── live_linker.py       # LiveWikidataLinker: cache -> candidates -> chooser -> validate
└── cache.py             # LinkDecisionCache (repository-backed)
```

`ResolutionResult`, `_build_entity_id`, `_is_plausible_alias` and the type
maps move out of `entity_resolver.py` into a shared module so both
implementations import them during the transition, and so they survive
ReFinED's removal.

### Stage 1 — Candidates

`WikidataClient` ([wikidata_client.py](../thestill/core/wikidata_client.py))
gains `search_entities(name, *, language, limit)` on the existing
`WIKIDATA_API_URL`, using `wbsearchentities`. It returns, per hit: QID,
label and description. The description ("1998 film", "33rd president of the
United States") is what the chooser disambiguates on. P31 is **not** fetched
per candidate — at 8 candidates × 92 names that would be ~700 extra requests
an episode — only for the QID finally accepted, where it drives the same type
re-bucketing as today.

- **One search per distinct name per episode**, case-folded. An episode
  averages 92 distinct names before the cache and far fewer after it.
- **Limit 8 candidates.** `wbsearchentities` ranks by label/alias match,
  which is a prior, not a judgement — the chooser does the judging.
- **Generic nouns are dropped before search.** The alias cleanup found that
  most junk mentions are common nouns ("agent", "people", "founder", "ceo").
  Names that are a single lowercase dictionary word with a GLiNER label of
  `topic` skip the lookup and stay unresolved. This is a cost control, not
  a quality rule; the chooser would reject them anyway.
- **Politeness.** Descriptive `User-Agent` (already set), a process-wide
  rate limit (default 5 requests/s, `WIKIDATA_MAX_RPS`), honour
  `Retry-After` and `maxlag`. Wikimedia's API etiquette asks for serial or
  lightly parallel requests from a single client.
- **No candidates** is a real answer: the name resolves to `none` without an
  LLM call.
- **A failure is never an empty result.** `search_entities` raises
  `WikidataUnavailable` for a timeout, a non-200, an unparseable body or a
  `maxlag` error body; only a well-formed empty `search` list means "nothing
  by that name". One name failing does not stop the others.
- **Blacklisted candidates are removed before the chooser sees them**, so it
  picks among the rest instead of re-proposing what a reviewer ruled out.

### Stage 2 — Choice

One `generate_structured` call
([llm_provider.py:714](../thestill/core/llm_provider.py#L714)) per episode,
split into batches of at most 40 names to bound the prompt.

Input per name:

- the name as spoken, and its GLiNER label;
- up to three context excerpts (the mention's `quote_excerpt`, ±200
  characters — the same context ReFinED gets today), chosen from different
  parts of the episode;
- the candidates: QID, label, description, P31 labels.

Episode-level input, once: podcast title, episode title, and the episode's
anchor entities (hosts and guests already known). This is context ReFinED
never had, and it is what should separate *The Truman Show* from the
president.

Names are addressed by a short id (`n1`, `n2`, …) so the answer does not
depend on the model echoing a transcribed name exactly. Everything spoken,
scraped or fetched is fenced with `wrap_untrusted` and the system prompt
carries `UNTRUSTED_CONTENT_PREAMBLE`; only the ids and the layout are ours.

Output (Pydantic response model): for each id, `qid: Optional[str]`,
`confidence: Literal["high", "medium", "low"]`, and a one-line `reason`.
The reason is stored, sanitised and cut to 200 characters, in the decision
row — it is the review queue's source. It is never logged and never shown to
users. ([#75](75-llm-call-tracing.md) tracing is not built yet, so there is
no trace to send it to.)

The prompt instructs: choose only from the listed candidates; answer `none`
when no candidate fits, when the name is a generic noun, or when the context
does not settle it. **Names with the same spelling are judged per name, not
per mention** — the first version assumes one referent per name per episode.
ReFinED resolves per mention, but in practice an episode that uses "Apple"
for both the company and the fruit is rare, and the `ambiguous` status
exists for the coref pass to flag it. See Open questions.

Model: `ENTITY_LINKING_PROVIDER` / `ENTITY_LINKING_MODEL`, defaulting to
`CLEANING_PROVIDER` / `CLEANING_MODEL` (Gemini Flash). Note that these two
settings are *not* what the `clean-transcript` stage runs on — that uses the
global `LLM_PROVIDER`; they are read here literally, as the project's
"cheap, fast model" pair. Provider and model are resolved as a coupled pair,
the way the eval judge's are. The call goes through the normal provider seam,
so [#75](75-llm-call-tracing.md) tracing will capture it once it exists.

### Stage 3 — Validation

Applied to every returned item before anything is written:

1. **The QID must be in the candidate list offered for that name.** Anything
   else is discarded and the name treated as unanswered — not as `none`.
   This makes invented identifiers structurally impossible.
2. **Blacklist.** `is_blacklisted(surface_form, qid)` is consulted three
   times: candidates are filtered before the chooser, the chooser's answer
   is checked, and a *remembered* decision is checked again when it is
   reused — a link that has since been ruled out is decided afresh, so the
   cache heals itself even if nobody invalidated it.
3. **Confidence.** `low` is recorded as `unresolvable`, with the guessed QID
   kept in the decision row for the review queue. `medium` and `high` are
   accepted. The threshold (`ENTITY_LINKING_MIN_CONFIDENCE`, default
   `medium`) is applied when a decision is *used*, not when it is stored, so
   changing it takes effect without re-asking.
4. **Sanitise.** Labels and reasons pass through `sanitize_text`
   ([text_sanitizer.py:41](../thestill/utils/text_sanitizer.py#L41)) before
   storage or logging (failure-mode catalogue: unsanitised LLM output).
5. **Type.** The existing P31 reclassification
   (`entity_type_rules.classify_entity_type`) runs unchanged.
6. **Alias.** The spoken name is added as an alias only if
   `_is_plausible_alias` accepts it — the same guard that stopped the
   pre-2026-05-08 alias pollution.

Accepted links are recorded with a new `ResolutionMethod.LLM_LINKED`
([entities.py:85](../thestill/models/entities.py#L85)) so they can be
counted, audited and, if ever needed, reverted as a set. `DIRECT` keeps
meaning "ReFinED linked it".

### Stage 4 — Cache

New table `entity_link_decisions`:

| Column | Notes |
|---|---|
| `surface_key` | case-folded, whitespace-normalised name |
| `podcast_id` | nullable; `NULL` = corpus-wide decision |
| `qid` | nullable; `NULL` = decided "none" |
| `label`, `description` | the chosen entity's Wikidata label and description, so a cache hit builds the entity with no network call |
| `reason` | the chooser's one-line reason, sanitised, ≤200 chars; review-queue source, never logged |
| `confidence` | high / medium / low |
| `decided_at` | ISO-8601 UTC |
| `linker_version` | prompt + model fingerprint |
| `hits` | times reused |

Lookup order: podcast-scoped, then corpus-wide. A decision is written
podcast-scoped first; it is promoted to corpus-wide once the same name has
resolved to the same QID in three different podcasts with no disagreement.
Names with disagreement across podcasts stay podcast-scoped — "Mercury" on a
science show and on a music show are different things.

- **Unlinked names expire** (`ENTITY_LINKING_NONE_TTL_DAYS`, default 30):
  a `none`, and a `low`-confidence guess. This is the freshness mechanism:
  someone without a Wikidata entry today gets re-checked next month. Links
  do not expire.
- **A correction invalidates the cache.** All four paths that record one —
  `POST /entities/corrections`, `mention drop`, `mention repoint`,
  `resolution-blacklist add` — call `invalidate_link_decisions` *before*
  re-resolution is queued. A source-scanning test fails if a module writes an
  override or a blacklist entry without calling it.
- **Uniqueness per scope** is two partial unique indexes (podcast-scoped,
  and corpus-wide where `podcast_id IS NULL`), because NULLs never collide in
  a plain UNIQUE. They are also the upsert's conflict target, so two episodes
  deciding the same name at once both succeed without a lock.
- **Keys are folded in Python** (`casefold`, whitespace collapsed), never in
  SQL: SQLite's `LOWER` is ASCII-only and Postgres's follows the collation.
- **`linker_version` change** makes older decisions eligible for re-decision
  lazily, not in bulk.

Lands in the SQLite bootstrap, Postgres `SCHEMA_SQL` and an Alembic revision,
the same three places as every table since #44.

### Failure handling

| Situation | Behaviour |
|---|---|
| Wikidata unreachable, 429, or 5xx for a name | `TransientError`; task retries with backoff; mentions stay `pending` |
| LLM call fails or returns unparseable output | same |
| Still failing on the last retry | **defer, do not fail** — see below |
| Part of an episode decided before an outage | those decisions are recorded and remembered; only the unanswered names stay `pending`, so the retry is cheap |
| LLM omits some names from its answer | those names are re-asked once in a smaller batch; still missing → stay `pending`, task succeeds for the rest |
| More than half of an episode's names unanswered | raise `EntityLinkerBrokenError` — the successor of `EntityResolverBrokenError` ([entity_resolver.py:534](../thestill/core/entity_resolver.py#L534)); nothing is recorded |
| No candidates / chooser says `none` | `unresolvable`, final (until the `none` TTL) |

`unresolvable` is a terminal status that nothing revisits, so it is only
ever written for a *decision*, never for a *failure*.

**Deferral on the last retry.** `resolve-entities` sits ahead of `reindex`
in a linear chain, so a task that dead-letters leaves its episode
unsearchable — the lesson of the #66 cutover. A network dependency makes
that far more likely than a local model did. So on the final attempt
(`task.retry_count >= task.max_retries - 1` — `retry_count` is what earlier
attempts consumed; the queue counts this attempt only after it fails) a
linker failure does not raise: the handler leaves the mentions `pending`,
marks the episode, logs at error level, and returns so `reindex` runs.

- **Unreachable** (Wikidata or the LLM): `entity_extraction_status =
  'linking_deferred'`, event `entity_linking_deferred`.
- **Broken** (`EntityLinkerBrokenError`): status `failed`, event
  `entity_linking_broken_final`. Decided 2026-09-21: search never depends on
  the linker, so even a broken one does not block `reindex`.

Both are counted in `thestill status`, `/api/status`, the dashboard API and
the MCP `get_status` tool, next to the existing "skipped (extractor
unavailable)" line. `thestill resolve-entities` drains them, and a later
successful pass settles `linking_deferred` back to `complete` with a
conditional update. A visible, queryable backlog rather than a silent skip
(failure-mode catalogue: silent degradation).

Before the last attempt the failure is raised as an **`item`-class**
`TransientError`, deliberately. The worker reschedules `infra`-class errors
without spending retry budget, so during a real outage the retry count would
never advance and the last attempt would never come.

The rule lives in the handler, so it protects the ReFinED path too — which
is what prod runs until Phase 3.

### Configuration

| Variable | Default | Meaning |
|---|---|---|
| `ENTITY_LINKER` | `refined` → `live` at Phase 3 | which implementation `resolve-entities` uses |
| `ENTITY_LINKING_PROVIDER` / `_MODEL` | cleaning provider / model | the chooser's LLM |
| `ENTITY_LINKING_MIN_CONFIDENCE` | `medium` | lowest accepted confidence |
| `ENTITY_LINKING_NONE_TTL_DAYS` | `30` | re-check interval for "none" |
| `WIKIDATA_MAX_RPS` | `5` | process-wide request ceiling |

### Logging

Structured events, entity IDs and counts only — excerpts and reasons go to
the #75 trace, not the log:

- `entity_linking_started` — `episode_id`, `names`, `cache_hits`
- `entity_linking_candidates_fetched` — `names`, `with_candidates`, `wikidata_ms`
- `entity_linking_chosen` — `linked`, `none`, `low_confidence`, `rejected_not_offered`, `rejected_blacklisted`
- `entity_linking_completed` — totals plus `llm_calls`, `duration_ms`

`rejected_not_offered > 0` is the signal that the model is inventing QIDs
and the prompt needs attention.

---

## Cost and capacity

Estimates, to be replaced by Phase 1 measurements:

- **Per episode, cold cache:** ~92 Wikidata searches (~20 s at 5 rps) and
  2–3 LLM calls of roughly 10–15k input tokens in total. On Gemini Flash
  that is on the order of a cent.
- **Per episode, warm cache:** hosts, recurring guests and the common
  companies are cache hits. Expect the searched set to fall to the names
  that are new to that podcast.
- **Backlog sweep:** 44,574 distinct unresolvable names. At 5 rps the
  Wikidata side is ~2.5 hours; the generic-noun filter and the 374-names-
  cover-half concentration mean the LLM side is a few thousand calls.
- **Host:** once ReFinED is removed the app no longer needs 8.6 GB of disk
  or the extra RAM. Whether to return to a t4g.medium is a cost decision for
  after Phase 4, not part of this spec — GLiNER still needs ~1 GB.

---

## Evaluation (the cutover gate)

No cutover on impressions. Built on the #53 eval runner, as a new rubric
`entity-linking`:

1. **Fixed set:** 20 episodes across at least 8 podcasts, chosen to include
   2025–2026 AI/tech episodes (where ReFinED is weakest), two non-English
   episodes, and one episode with a known ambiguity trap.
2. **Both linkers run on the same GLiNER mentions.** The live linker writes
   to a scratch table, not to `entity_mentions`.
3. **Agreement is free:** where both return the same QID, count it and move
   on.
4. **Disagreements are judged** by the pinned judge model, given the name,
   context, and both candidates' Wikidata descriptions: which is right,
   both wrong, or genuinely ambiguous. A 50-item sample of judge verdicts is
   spot-checked by a human to confirm the judge is trustworthy here.
5. **Deterministic checks:** every accepted QID exists; none was outside its
   candidate list; no blacklisted pair was accepted.

**Pass criteria:**

- on names ReFinED links, the live linker is judged wrong where ReFinED was
  right in **under 2%** of cases;
- the live linker links **at least 25%** of the names ReFinED left
  unresolvable, with judged precision **≥ 90%**;
- zero deterministic-check failures.

Thresholds are a starting position; Phase 1 sets them from the first run.

---

## Phases

| Phase | Scope | Gate |
|---|---|---|
| **1 — Linker + eval** | `entity_linking/` package, `search_entities`, cache table, `ENTITY_LINKER` switch (default `refined`), `entity-linking` rubric. No behaviour change in prod. | eval pass criteria met on the fixed set |
| **2 — Shadow** | `ENTITY_LINKER=refined` still writes; the live linker runs alongside on new episodes and writes to the scratch table. One week on prod. | cost, latency and Wikidata error rate within estimates; no `rejected_not_offered` trend |
| **3 — Cutover** | `ENTITY_LINKER=live` on prod. Run the held 494-episode backfill through it. | first 50 backfilled episodes spot-checked |
| **4 — Sweep + removal** | `thestill relink-unresolvable` sweeps the 318k backlog through the linker, most-frequent names first. Remove ReFinED: the dependency, the patches, `REFINED_DATA_DIR`, the smoke test, the 8.6 GB cache on the box. | `entities` extra installs without git; image shrinks |

Phase 3 is the reason the backfill is on hold: running it through ReFinED
now would link 494 episodes with the tool being retired and leave their
post-2022 names to be swept again.

---

## Testing

- **Unit — candidates:** `wbsearchentities` response parsing; rate limiter;
  `Retry-After`; empty result; generic-noun filter.
- **Unit — chooser:** prompt assembly (excerpts, anchors, candidates);
  batching at 40; response-model validation.
- **Unit — validation:** QID not offered → discarded and counted;
  blacklisted pair → none; `low` → unresolvable + review queue; implausible
  alias not stored.
- **Unit — cache:** scope order; promotion at three agreeing podcasts;
  disagreement blocks promotion; `none` TTL; invalidation on correction.
- **Unit — failure:** Wikidata 5xx → mentions stay pending; partial LLM
  answer → re-ask once; majority unanswered → `EntityLinkerBrokenError`,
  nothing written. Mocks must be able to fail independently per name
  (failure-mode catalogue: consistent-mock tests).
- **Contract:** one parametrised suite runs against both `EntityResolver`
  and `LiveWikidataLinker` to pin the `resolve()` contract the handler
  depends on.
- **Integration (opt-in, network):** a live smoke test resolving a handful
  of fixed names, the successor of `test_refined_smoke.py`.

---

## Alternatives considered

| Option | Why not |
|---|---|
| **LLM fallback only, keep ReFinED for the first pass** | Keeps the unmaintained dependency, its patches and its hosting cost. Rejected on the premise of this spec. |
| **Rebuild ReFinED's data from current dumps** | Same 2022 code; a multi-hundred-GB, day-long job on unmaintained scripts, repeated yearly. |
| **ReLiK (Sapienza, 2024)** | Newest credible local linker, but its index is also a frozen snapshot, its last release was September 2024, and the repo declares no licence. Re-labelling cost for roughly a year of freshness. |
| **OpenTapioca** | Actively maintained and live-synced with Wikidata, but a lightweight statistical linker with markedly lower accuracy, and it needs a Solr service plus a self-hosted Wikidata index. |
| **Hosted NLU (Google Natural Language, TextRazor)** | Current knowledge, but ~$60–80/month at this volume, a second vendor, and it replaces mention detection as well, changing boundaries against 793k existing rows. |
| **Local Wikidata mirror for candidates** | Removes the API dependency but reintroduces a snapshot to refresh and tens of GB to host. Revisit only if `wbsearchentities` rate limits bite. |
| **LLM links from its own knowledge, no candidates** | Invented QIDs, and the model's knowledge has its own cutoff. Candidate-constrained choice avoids both. |

---

## Implementation notes (Phase 1, first PR)

- **One resolve core.** `resolve_pending_mentions` in
  [task_handlers.py](../thestill/core/task_handlers.py) is the single
  implementation of overrides → linking → recording → coreference →
  alias-merge. `thestill resolve-entities` used to hard-code ReFinED and skip
  overrides, the blacklist, coreference and the recorded method; it now calls
  the same function and follows `ENTITY_LINKER`.
- **`link()` writes nothing.** `LiveWikidataLinker.resolve` remembers
  decisions and builds results; `link` is the same core with no writes, which
  is what the eval runs. Eval output goes to the run directory as files, as
  [#53](53-eval-runs-and-summary-rubric.md) requires — there is no scratch
  table, so "one new table" holds.
- **Entity ids can differ between linkers for the same QID.** ReFinED names
  an entity by its Wikipedia title ("Amazon (company)"), the live linker by
  its Wikidata label ("Amazon"), and the id is a slug of the name. The
  existing inline QID-duplicate merge in the resolve core folds the two rows
  together. Worth watching in the shadow phase.
- **Not in this PR:** the frontend `EntityBacklogNotice` does not yet show the
  deferred count (the API carries it), and the Postgres half of the
  repository contract suite has only run in CI.

## Open questions / follow-ups

- **Per-name vs per-mention.** Version 1 assumes one referent per name per
  episode. If the eval shows real same-name collisions, the chooser can be
  asked to flag a name as `split` and fall back to per-mention for that
  name only.
- **Re-linking existing wrong links.** Anthropic → anthropic principle and
  similar are `resolved`, so the unresolvable sweep does not touch them. A
  targeted pass — re-ask the linker for every `direct` link whose ReFinED
  confidence was under a threshold — is the likely shape. Needs its own
  eval.
- **Non-English episodes.** `wbsearchentities` is language-scoped. Version 1
  searches the episode's language and falls back to English. Croatian
  coverage should be checked explicitly in the eval set.
- **Wikidata search recall.** `wbsearchentities` matches labels and aliases
  only. Names transcribed with ASR spelling errors ("Dario Amaday") will
  miss. Option: let the chooser propose a corrected spelling for a second
  search, capped at one retry per name.
- **Instance size after ReFinED is gone.** Decide after Phase 4 with a week
  of `docker stats`.
- **`list_episodes_by_entity` is broken on Postgres** (SQLite-only
  connection). Unrelated to linking, but it is the MCP tool most likely to
  be used to inspect the results of this work; fix alongside Phase 1.
