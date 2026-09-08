# Conversational Briefing Narration

> **Status:** 📝 Draft (2026-09-07)
> **Created:** 2026-09-07
> **Author:** Product & Engineering
> **Related:** [#33 narrated-digest](33-narrated-digest.md) (pipeline this spec amends), [#36 per-user-digest-from-inbox](36-per-user-digest-from-inbox.md) (briefing → inbox window), [#34 briefing-audio-and-feeds](34-briefing-audio-and-feeds.md) (consumes the script; unchanged), [#54 summary-segment-citations](54-summary-segment-citations.md) (citation markup stripped from the new inputs), [#58 original-language-summaries](58-original-language-summaries.md) (section-number anchors), [#42 robustness-and-failure-mode-hardening](42-robustness-and-failure-mode-hardening.md) (FM checklist), [#53 eval-runs-and-summary-rubric](53-eval-runs-and-summary-rubric.md) (no narration rubric yet)

---

## Executive Summary

The #33 narration pipeline is wired end to end but, on live data, produces a briefing that reads like a wire-service round-up: no quote clips at all, and prose built from abstractions ("the conversation is moving from what AI can say to what it can do"). The user's reference point is a NotebookLM audio overview: one voice, spoken language, stories told from the concrete detail outward.

A scratch run on 2026-09-07 (briefing `3572399c`, the 3 August inbox window) showed that four changes close most of the gap, and that no single one of them is sufficient:

1. **Fix two loader bugs** that silently empty the quote pool on every real briefing (sidecar path resolved wrong; speaker map keyed on `SPEAKER_XX` while the sidecar stores resolved names).
2. **Widen the script writer's input** from a two-sentence gist to the summary's Gist + Key Takeaways + The Drama sections. This is where the anecdotes, numbers and disagreements live; the writer currently never sees them and pads the word budget with generalities.
3. **Replace the anchor prompt** with a conversational single-narrator voice, including an explicit ban list of noise phrases, and keep the old newsroom voice as a selectable file.
4. **State a lower word target than the validation ceiling** (80 %) so the richer input does not tip the writer into the `word_budget_high` fallback, which it did twice in a row before this change.

A fifth change, **scoring quote candidates against the segment angle** rather than length alone, is the largest remaining gap (clips that do not match the narration around them) and is specced as Phase 2 because it changes the stage order.

Nothing here touches the JSON script schema, the markdown renderer, the briefing tables, or #34's audio contract. Every change is inside `thestill/services/narration/` plus one prompt file, one helper in `briefing_script_generator.py`, and one config key.

---

## Table of Contents

1. [Motivation: what the test showed](#motivation-what-the-test-showed)
2. [Product Requirements](#product-requirements)
3. [Design](#design)
   1. [Loader fixes](#1-loader-fixes)
   2. [Richer episode material for the writer](#2-richer-episode-material-for-the-writer)
   3. [Conversational anchor prompt](#3-conversational-anchor-prompt)
   4. [Budget: stated target below the ceiling](#4-budget-stated-target-below-the-ceiling)
   5. [Quote relevance against the segment angle (Phase 2)](#5-quote-relevance-against-the-segment-angle-phase-2)
   6. [Observability](#6-observability)
   7. [Configuration](#7-configuration)
4. [Failure modes](#failure-modes-42-checklist)
5. [Testing](#testing)
6. [Phases](#phases)
7. [Accepted limitations](#accepted-limitations)
8. [Open questions](#open-questions)
9. [Decision log](#decision-log)

---

## Motivation: what the test showed

Three runs on the same briefing, same 5-minute target, same model (`gemini-3-flash-preview`). Scratch scripts only; the repo was not modified.

| Run | What changed | Runtime | Clips | Outcome |
|---|---|---:|---:|---|
| A | Shipped code, `thestill narrate` | 4m31s | 0 | `mode=narrated`, quote pool empty, newsroom register |
| B | Loader bugs patched in-process | 5m12s | 6 | Quotes appear; prose still formal; clips loosely related to the narration around them |
| C | B + rich input + conversational prompt + 80 % stated target | 4m53s | 5 | Spoken register, story-first, within budget (499 words against a 466 ceiling) |

Run C with the rich input but **without** the 80 % stated target failed validation twice (`word_budget_high`) and fell back to the link index. That is the interaction this spec has to design around: richer material makes the writer want to say more, and the +15 % ceiling is strict because overruns hurt TTS.

What run A discards. For the 20VC episode the summary's Drama section contains "called Silicon Valley investors 'total bitches' for being too scared of revenue concentration" and the AI-generated fake candidates story; The Rest Is Money's contains the Shanghai dance-floor anecdote and the Enron question. The writer received: "Azeem shares boots-on-the-ground insights from his recent trip to Chinese AI labs and explains why the massive spending on AI might actually be backed by real revenue rather than just hype." Given only that, the model wrote "We begin with a reassessment of the global AI hierarchy."

What run C still gets wrong. The Angelopoulos clip is a list of lab names; the Baumann clip is about the mobile app while the narration around it is about the office. The selector scores candidates on length and sentence containment only ([quote_selector.py:260](../thestill/services/narration/quote_selector.py#L260)); the keyword relevance term is neutral because it runs before theme clustering and has no angle to score against. The writer threads whatever clips it was handed.

---

## Product Requirements

### User stories

| As a... | I want... | So that... |
|---|---|---|
| Listener | The briefing to sound like a person telling me what happened on the shows I follow | I can listen (or read) without translating press-release English in my head |
| Listener | The lead of each story to be the concrete thing (the anecdote, the number, the row) | I remember it, and I know within one sentence whether I care |
| Listener | The clips to be the moment the narration just set up | The clip pays off instead of interrupting |
| Reader | The same script to read well on the page | Reading stays first-class (#33 §"Read or listen") |
| Operator | A quote pool that is never silently empty when transcripts exist | I find out from a log line, not from a flat briefing weeks later |
| Operator | To switch anchor voice with one env var and no deploy | I can A/B the newsroom and conversational voices on the same inbox |

### Behavior rules

1. **Voice is a file.** The anchor prompt stays a Markdown file on disk (#33 O5). This spec adds a second file and a selector; it does not move prompts into the DB.
2. **Quotes are extracted, never generated.** Unchanged from #33. The richer input goes into narration blocks only; the verbatim-leak check still applies.
3. **The script schema does not change.** `blocks[]`, `section`, `quote_id`, `mode`, and the stats block are untouched, so #34, the markdown renderer, the API routes and the tests that assert on them keep working.
4. **Budget semantics do not change for the caller.** `target_duration_seconds`, `wpm`, `max_quote_share` and the ±15 % validation ceiling mean what they meant. Only the number the prompt *states* changes.
5. **Silent degradation is a bug.** An empty quote pool on a briefing whose episodes have sidecars logs a warning and is visible in the stats block (#42 FM-4).
6. **Fallback behaviour is unchanged.** Two failed validations still produce the link-index fallback with the banner (#33 O7). This spec reduces how often that happens; it does not remove the path.

---

## Design

### 1. Loader fixes

Both bugs are in [transcript_loader.py](../thestill/services/narration/transcript_loader.py). Both are path/shape drift between the segmented cleaner that writes the sidecar and the loader that reads it (#42 "path drift").

**1a. Sidecar path.** `Episode.clean_transcript_json_path` is stored storage-relative, e.g. `the-rest-is-money/301-…_cleaned.json`. `_resolve_sidecar_path` passes it to `path_manager.clean_transcript_json_file(podcast.slug, …)`, which prefixes the slug again and swaps the suffix, producing `…/the-rest-is-money/the-rest-is-money/301-…_cleaned.json.json`. The file never exists; the debug log says "sidecar missing on disk" and the episode yields no candidates.

Fix: resolve exactly as [podcast_service.py:748](../thestill/services/podcast_service.py#L748) does:

```python
path = self.path_manager.clean_transcript_file(episode.clean_transcript_json_path)
```

Drop the `podcast.slug` requirement from this branch (it was only needed to feed the wrong helper). Keep the `_assert_inside_root` guard that `clean_transcript_file` already applies.

**1b. Speaker resolution.** The segmented cleaner applies the facts speaker mapping before writing the sidecar ([segmented_transcript_cleaner.py:214](../thestill/core/segmented_transcript_cleaner.py#L214)), so `segments[].speaker` holds `"Azeem Azhar"`, not `"SPEAKER_02"`. The loader looks the label up in `facts.speaker_mapping`, which is keyed `SPEAKER_02 → "Azeem Azhar (Guest)"`, finds nothing, leaves `speaker_name=None`, and the selector drops the turn ("SPEAKER_UNKNOWN turns are not eligible").

Fix: build the lookup both ways once per episode.

```python
by_label = dict(facts.speaker_mapping)                       # SPEAKER_02 -> "Azeem Azhar (Guest)"
by_name  = {strip_role_annotation(v).strip(): v for v in by_label.values()}  # "Azeem Azhar" -> same

annotated = by_label.get(label) or by_name.get(label)
if annotated:
    name, role = strip_role_annotation(annotated).strip(), _classify_role(annotated)
elif label and not _SPEAKER_LABEL_RE.match(label):          # already a human name, no facts row
    name, role = label, "unknown"
else:
    name, role = None, "unknown"                              # raw diarisation label, ineligible
```

A resolved name with no facts row stays eligible with role `unknown`; a raw `SPEAKER_NN` label stays ineligible, preserving the #33 rule. Legacy sidecars that still carry raw labels keep working through `by_label`.

**Guard.** Add a unit test that loads a fixture sidecar whose `speaker` values are names and asserts the turns resolve; today's tests pass because the fixture and the loader share the same wrong assumption (#42 "consistent-mock tests").

### 2. Richer episode material for the writer

Add one optional field to `EpisodeBrief` ([models.py:126](../thestill/services/narration/models.py#L126)):

```python
material: Optional[str] = None   # Gist + Key Takeaways + The Drama, de-marked, capped
```

`gist` stays as is and remains what the theme clusterer sees. The clusterer's job is grouping; two sentences per episode is enough and keeps that call cheap. Only `ScriptWriter._format_segment` emits `material`, in place of `gist`, for the episodes in each segment. Tail episodes get `gist` only (one concrete detail is all the tail needs).

**Extraction.** A new public helper next to `extract_gist` in [briefing_script_generator.py](../thestill/services/briefing_script_generator.py):

```python
def extract_summary_material(summary_text: str, *, max_words: int = 400) -> Optional[str]
```

- Pulls sections `1`, `3`, `4` by section **number** (the stable anchor under #58's localised headings; same regex family as `extract_gist`).
- Strips #54 citation markup: `[MM:SS](?t=…&cite=…)`, `[MM:SS - MM:SS](…)`, bare `[MM:SS]`, and `**bold**`. Timestamps are noise to a narrator.
- Labels each block so the writer knows what it is looking at: `Gist:`, `Key takeaways:`, `Drama (disagreements, anecdotes, tense moments):`.
- Caps at `max_words`, cutting whole bullets from the end, never mid-bullet. Drama is the last section and the first to lose lines; takeaways survive.
- Returns `None` when the summary has no numbered sections (legacy summaries), in which case the writer falls back to `gist`.

Why not the whole summary. Sections 5–9 (Best Quotes, Blog Ideas, Social Snippets, Resource List, BS Test) are either things the pipeline already does better (verbatim quotes from the sidecar) or noise for a narrator. Ten episodes × 400 words is ~4k words of input, which is fine for the writer call; ten full summaries would triple it for no gain.

**Sanitisation.** `material` is LLM output being fed to an LLM. Run it through the same control-byte strip as #41/#42's LLM-output guard before it enters the prompt.

### 3. Conversational anchor prompt

Ship two prompt files under [narration_prompts/](../thestill/services/narration_prompts/):

| File | Voice | Role |
|---|---|---|
| `conversational_anchor.md` | One narrator talking to a friend | **New default** |
| `newsroom_anchor.md` | The current `default_anchor.md`, renamed | Kept for A/B and for anyone who prefers it |

`default_anchor.md` becomes a one-line pointer (or is removed and `load_default_anchor_prompt` resolves the configured name; see §7). Nothing else imports it by name.

The conversational prompt is the one used in run C, verbatim as tested. Its structure, and what each part is for:

- **Frame.** "You host a short daily show for one listener: a smart friend who follows these podcasts but did not get to them today. You are talking, not writing." This replaces "You are the anchor of a daily podcast briefing… news-anchor measured, not radio-DJ excited", which Gemini reads as broadsheet prose.
- **How you talk.** Spoken English, contractions, sentence openers like "So"/"And"/"Okay", address the listener as "you", lead with the concrete beat then the meaning, tell it as a story (what happened, who said what, who won the room), one short reaction after a surprising fact or a clip and never a restatement, set clips up the way people do, refer to shows the way listeners do, transitions from content not template, opinions small and honest.
- **Ban list.** "the conversation is moving", "the landscape", "underscores", "highlights", "delves", "it's worth noting", "in a world where", "this sentiment was echoed", "serves as a cautionary tale", "the intersection of", "a reality check", "the next frontier", "a paradigm", "navigate", "unpack", "at the end of the day", "a deep dive", "notably", "arguably", "a stark reminder", plus the rule "if a sentence has no person, number, place or thing in it, cut it." In the test this list did more work than the persona paragraph.
- **Shape.** Same four-part shape as #33 (opener, segments, tail, signoff) with the opener redefined as a hook ("the single most surprising or funny thing from today"), not a table of contents, and the tail as one sentence per episode with one concrete detail.
- **Output contract and hard constraints.** Byte-for-byte the same JSON `blocks` contract and quote rules as today, plus the budget paragraph from §4.

The prompt also tells the writer that the Drama section "is usually the best material you have. Use it." That single line is what turned the Shanghai anecdote into the opener.

**Ban-list lint.** Add a deterministic stat, not a validation failure: count ban-list hits across narration text and emit it as `stats.noise_phrase_hits` and in the `narration generated` log line. It is a cheap register metric until #53 grows a narration rubric. Making it a hard failure would trade a good-enough script for a link-index fallback, the wrong side of #33 O7.

### 4. Budget: stated target below the ceiling

[script_writer.py](../thestill/services/narration/script_writer.py) states the full budget in the user prompt and validates at +15 %. With richer input the writer lands at 105–125 % of the stated number. Two changes:

1. **Stated target.** `_build_user_prompt` states `int(budget * STATED_TARGET_RATIO)` with `STATED_TARGET_RATIO = 0.8`. `_validate` keeps the real `narration_word_budget` and the existing −50 %/+15 % window. The retry message (`_tighten_prompt`) also states the reduced number; the failure detail keeps reporting the real window so the log is honest.
2. **Prompt language.** The system prompt says the budget is "a hard ceiling, not a target to fill", that the writer has three to four times more material than fits, and gives a per-segment feel ("often 70 to 110 words of your own voice plus one clip"). This is in the conversational prompt already; add the same paragraph to `newsroom_anchor.md` so the A/B is fair.

Observed effect in the test: 499 words against a 466 ceiling (+7 %) with the stated target at 372. Without the ratio: two overruns and a fallback.

`MAX_REGENERATIONS` stays at 1. If Phase 1 telemetry shows `word_budget_high` still dominating fallbacks, lower the ratio before adding retries; each retry is a full writer call.

### 5. Quote relevance against the segment angle (Phase 2)

Today the order is: quote selection per episode → theme clustering → word budget → script. Selection has to precede the budget because quote seconds come off the narration budget. But it also precedes clustering, so the selector has no angle to score against and `relevance` sits at 0.5 for everyone.

Change the order to: **candidate pool** per episode → clustering → **per-segment rerank** → share cap → budget → script.

- **Candidate pool.** `_select_quotes_for_episode` asks the selector for `per_episode_max=4` (today 2) and skips the share cap. The selector's other rules (speaker cap, neighbour suppression, ad adjacency, boundary trim, length fit, containment) all still apply. Cost: none, it is deterministic and local.
- **Per-segment rerank.** New `QuoteReranker.rerank(segment, candidates, material) -> ranked` in a new `quote_reranker.py`. For each candidate in the segment's episodes, relevance = token overlap between the candidate text and `segment.theme + segment.angle + the episode's Key Takeaways lines`, using the same normalisation as `_normalise_for_match`. Final score = `0.6 * relevance + 0.4 * existing score`. Keep the top 2 per episode, top 3 per segment. Deterministic, no LLM call, so #33's reproducibility rule holds.
- **Tail episodes** keep their top-1 by existing score; the test showed a tail clip (Tom Holland on the Golden Hind) works well as texture.
- **Share cap** runs after the rerank, on the survivors, exactly as now.
- **Writer prompt.** `_format_segment` lists each segment's quotes *under that segment* (today the pool is one flat list above the plan), so the writer sees "these clips belong to this story".

The interface is shaped so an embedding-based reranker drops in later (#33 O2): `rerank` takes text in and returns an ordered list; the overlap scorer is the v1 implementation.

Why not let the writer pick from the whole pool. It would need the pool in the prompt anyway, adds a free-text choice that the validator then has to police, and makes clip choice non-deterministic. The rerank keeps editorial choice (which story) with the LLM and mechanical choice (which clip fits that story) deterministic.

### 6. Observability

- `narration.quote_pool_empty` **warning** when `episodes_with_sidecar > 0` and the pool is empty after selection. Today this state logs nothing above debug and produced run A for every briefing since #36 landed.
- Stats block gains `quote_pool_size`, `episodes_with_sidecar`, `noise_phrase_hits`, `stated_word_target`, `narration_words`. All additive; the JSON script's `schema_version` stays `phase2` because consumers ignore unknown keys and nothing existing changes meaning.
- `narration generated` log line carries the same fields.
- `narration.fallback` already logs `reason`; add `narration_words` and `budget` so `word_budget_high` is diagnosable from logs without re-running.

### 7. Configuration

| Key | Default | Purpose |
|---|---|---|
| `NARRATION_ANCHOR_PROMPT` | `conversational_anchor` | Basename (no extension) of the prompt file under `narration_prompts/`. Rejected if it resolves outside that directory. |
| `NARRATION_STATED_TARGET_RATIO` | `0.8` | Fraction of the word budget stated to the writer. Bounds `[0.5, 1.0]`. |
| `NARRATION_MATERIAL_MAX_WORDS` | `400` | Per-episode cap for §2. |

`load_default_anchor_prompt()` becomes `load_anchor_prompt(name)` with the default taken from config; the CLI and web builders pass it through. The existing `anchor_prompt=` constructor override on `NarrationGenerator` stays for tests.

Document the three keys in [docs/configuration.md](../docs/configuration.md).

---

## Failure modes ([#42](42-robustness-and-failure-mode-hardening.md) checklist)

| FM | Where it could bite | Handling |
|---|---|---|
| Errors as empty results | Empty quote pool looked like "no good quotes" | §6 warning + `quote_pool_size` stat; loader test with realistic fixture |
| Path drift | Sidecar path helper vs stored path | §1a resolves through the same helper every other caller uses; regression test asserts the resolved path equals the stored path under the storage root |
| Silent degradation | Fallback to link index on `word_budget_high` | Unchanged fallback, but §4 lowers incidence and §6 logs the numbers |
| Unsanitised LLM output | Summary sections fed back into a prompt | §2 control-byte strip; citation markup stripped |
| Consistent-mock tests | Loader tests used `SPEAKER_XX` fixtures matching the wrong assumption | §1b adds a resolved-name fixture; keep the legacy one |
| Mixed-tz / checkpoint-before-durability | Not touched | n/a |

---

## Testing

Unit, under `tests/unit/services/narration/`:

- `test_transcript_loader.py`: resolved-name sidecar resolves turns with role from facts; raw-label sidecar still resolves; name with no facts row is eligible with role `unknown`; sidecar path equals `clean_transcript_file(stored)`.
- `test_briefing_script_generator.py` (new cases): `extract_summary_material` on a real-shaped summary strips citations and bold, keeps section order, caps by whole bullet, returns `None` on a legacy summary.
- `test_script_writer.py`: prompt states `int(budget*ratio)`; validation still uses the full budget; `noise_phrase_hits` counts; segment quotes listed under their segment (Phase 2).
- `test_quote_reranker.py` (Phase 2): overlap scoring is deterministic; top-N caps; tail untouched.
- `test_narration_generator.py`: `quote_pool_empty` warning fires only when sidecars exist; stats fields present; prompt selection by name and rejection of a path-escaping name.

Manual, before merge: rerun briefing `3572399c` (or the current day's) through `thestill narrate` with both prompt names and attach both markdowns to the PR. The scratch outputs from 2026-09-07 are the baseline.

---

## Phases

| Phase | Scope | Gate |
|---|---|---|
| 1 | §1 loader fixes, §6 warning + stats, loader tests | `thestill narrate` on a live briefing reports `quote_count > 0`; warning fires on a fixture with sidecars and no candidates |
| 2 | §2 material, §3 prompts + lint, §4 ratio, §7 config, docs | Same briefing narrates with the conversational prompt within budget on first attempt in ≥ 4 of 5 runs; `newsroom` still works |
| 3 | §5 reranker and stage reorder | On the test briefing, each lead segment's clips share ≥ 1 content token with the segment angle or takeaways; reproducibility test passes |
| 4 (deferred) | #53 narration rubric using `noise_phrase_hits` plus an LLM judge for "sounds spoken" | After #34 audio, per #53's own note |

Phases 1 and 2 are one branch (`feat/77-conversational-briefing-narration`); Phase 3 can follow separately because it does not change the script schema either.

---

## Accepted limitations

- Clip choice stays deterministic-heuristic until embeddings exist repo-wide (#33 O2). Phase 3's overlap scorer will still occasionally pick a flat line when a better one is nearby.
- The Drama section is only as good as the summary rubric; episodes summarised before the rubric had a Drama section fall back to gist + takeaways.
- English-only prompt; #58's non-English summaries produce English narration as before.
- The ban list is model-tuned by hand against Gemini Flash. Other providers may need different entries; the file is the tuning surface.

---

## Open questions

| # | Question | Current thinking |
|---|---|---|
| O1 | Should the theme clusterer also see `material`? | No for now. It groups fine on gist and the extra tokens cost more than they help. Revisit if segments look wrong rather than prose. |
| O2 | Per-user voice choice (conversational vs newsroom) in the schedule row? | Not in this spec. Env-level switch first; per-user is a #50 schedule field later, and would need the prompt name stored per briefing for reproducibility. |
| O3 | Should `noise_phrase_hits` above a threshold trigger the single retry? | Tempting, but a retry costs a full writer call and the fallback is worse than a few clichés. Measure first. |
| O4 | Does the 80 % ratio hold for the `short` (3 min) preset where the budget is ~250 words? | Unknown. Test all three presets in Phase 2 and make the ratio a config key so it can be tuned without a deploy. |

---

## Decision log

| Date | Decision | Why |
|---|---|---|
| 2026-09-07 | Conversational voice becomes the default; newsroom kept as a file | User judged run C "much better"; keeping the old file makes the change reversible by env var |
| 2026-09-07 | Widen writer input to sections 1+3+4, not the full summary | Those sections hold the concrete material; the rest is redundant with the sidecar quotes or noise |
| 2026-09-07 | Fix budget by stating a lower target, not by loosening validation | The +15 % ceiling protects TTS runtime (#34); the writer's overshoot is a prompt behaviour, so correct it in the prompt |
| 2026-09-07 | Quote relevance as a deterministic rerank after clustering, not LLM choice | Keeps #33's reproducibility rule and the validator simple; embedding upgrade slots into the same interface |
| 2026-09-07 | Ban-list hits are a stat, not a validation failure | A cliché is cheaper than a link-index fallback (#33 O7) |
