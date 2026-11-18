# narration

## Overview

Narrated digest generation (specs #33 and #77). The package turns the
episodes in one briefing into a spoken-style script. It runs as a pipeline:

1. Pick verbatim quotes from cleaned transcript sidecars (`QuoteSelector`).
2. Cluster episodes into themed segments with one LLM call
   (`ThemeClusterer`).
3. Write and validate the script with an LLM, retrying once
   (`ScriptWriter`).
4. Write a JSON script and a Markdown read-through under
   `PathManager.narrations_dir()`.

With no LLM, the generator emits the phase-1 skeleton (an opener, then
each episode's selected quotes, still `mode="narrated"`). When the script writer returns no valid
blocks (a hard validation failure on every attempt), it falls back to the
plain link-index briefing (`mode="fallback"`).

`NarrationRunner` is the entry point that the `thestill narrate --briefing`
CLI and the web layer use. It applies the account, feature-switch and quota
gates from [`../abuse/EGM.md`](../abuse/EGM.md) before any LLM call. Anchor
voices come from `../narration_prompts/`, whose contract is the
[Anchor prompts](#anchor-prompts) section below.

## Interface

- `narration_runner.py:NarrationRunner`: `run(*, briefing_id, target_duration_seconds=300, slug="morning", wpm=150.0, max_quote_share=0.40) -> NarrationRun` and `artifact_exists(*, briefing_id, slug) -> bool`. Both take keyword arguments only.
- `narration_runner.py:NarrationRun`: frozen result. Exposes `narration_id`
  (`"{briefing_id}-{slug}"`), `json_path` and `markdown_path`.
- `narration_runner.py:NarrationRunnerError`: raised for an unknown
  briefing, a briefing with no resolvable episodes, or an invalid
  slug/config.
- `narration_generator.py:NarrationGenerator`:
  `generate(episodes, config) -> NarrationContent`,
  `write_json_script(content, config) -> Path` and
  `write_markdown(content, config) -> Optional[Path]`.
- `narration_generator.py:NarrationConfig`: run configuration (target
  duration, wpm, shares, `slug`, `basename`, `briefing_id`) with
  `file_basename(generated_at)`.
- `narration_generator.py` default constants:
  - `DEFAULT_WPM` = 150.0
  - `DEFAULT_MAX_QUOTE_SHARE` = 0.40
  - `DEFAULT_TARGET_DURATION_SECONDS` = 300
  - `DEFAULT_BOUNDARY_TRIM_FRACTION`
  - `DEFAULT_TAIL_SHARE`
  - `DEFAULT_OPENER_SHARE`
  - `DEFAULT_SIGNOFF_SHARE`
  - `DEFAULT_MATERIAL_MAX_WORDS` = 400
  - `DEFAULT_STATED_TARGET_RATIO` = 0.8
- `quote_selector.py:QuoteSelector`:
  `select(turns, config, starting_id=1) -> List[QuoteCandidate]`, with ids
  `q{n}`. `quote_selector.py:QuoteSelectorConfig` configures it, and
  `quote_selector.py:truncate_to_sentence_prefix` trims long turns.
- `theme_clusterer.py:ThemeClusterer`:
  `cluster(briefs, target_duration_seconds) -> ThemePlan`.
  `theme_clusterer.py:SEGMENT_RELATIONSHIPS` lists the allowed
  relationships: `none`, `consensus`, `debate`, `contradiction` and
  `extension`.
- `script_writer.py:ScriptWriter`:
  `write(plan, briefs_by_id, quotes, narration_word_budget) -> ScriptResult`.
  `script_writer.py:ScriptResult` holds the output.
- `script_writer.py` constants:
  - `WORD_BUDGET_TOLERANCE_HIGH` = 0.15
  - `WORD_BUDGET_TOLERANCE_LOW` = 0.50
  - `VERBATIM_LEAK_NGRAM` = 8
  - `MAX_REGENERATIONS` = 1
  - `DEFAULT_STATED_TARGET_RATIO` = 0.8
- `transcript_loader.py:TranscriptTurnLoader`: `load(podcast, episode)`,
  `load_episode_facts(podcast, episode)`, `sidecar_exists(podcast, episode)`
  and `AD_ADJACENT_WINDOW_S`.
- `transcript_loader.py:ResolvedTurn`: one transcript turn after speaker
  resolution.
- `claim_selector.py:select_claim(brief, angle, *, max_words=400)`: picks
  one claim and one colour per episode and returns
  `claim_selector.py:EpisodeClaim`.
- `claim_selector.py:quote_fits_claim(quote_text, claim_text)`: true when
  the quote shares enough content tokens with the claim.
- `register.py:measure_register(...) -> RegisterMetrics` measures the
  script's register. Helpers:
  - `split_sentences`
  - `has_first_person`
  - `addresses_listener`
  - `unquote_scare_quotes`
  - `classify_transition`
- `markdown_renderer.py:NarrationMarkdownRenderer`:
  `render(blocks, quotes, plan, episodes, stats, generated_at) -> str`.
- `artefacts.py:read_narration_header(path)`: reads the JSON header of a
  narration artefact, or returns `None`.
- `models.py` types:
  - `EpisodeBrief`
  - `NarrationContent`
  - `NarrationStats`
  - `QuoteCandidate`
  - `ScriptBlock`
  - `Segment`
  - `ThemePlan`
  - `ValidationFailure`
- `models.py` literals:
  - `ScriptBlockKind` (`narration`, `quote` or `reaction`)
  - `SpeakerRole` (`host`, `guest` or `unknown`)
  - `NarrationMode` (`narrated` or `fallback`)
- `models.py` helpers: `REACTION_MAX_WORDS` (30) and `word_count`.

## Invariants

- `narration_runner.py:NarrationRunner.run` runs its steps in this order:
  1. Resolve the briefing.
  2. `access.ensure_user_active(owner)`.
  3. `access.require_feature("narration")`.
  4. Resolve the episodes.
  5. Reserve the `narrations_per_day` quota.
  6. Make the LLM calls.

  The account and feature gates therefore run before any LLM spend. The
  quota reservation is committed only after `generate` returns, and the
  artefacts are written after the commit.
- `narration_runner.py:NarrationRunner._resolve_episodes` uses only inbox
  rows in `INBOX_STATES_ELIGIBLE_FOR_BRIEFING` within the briefing's
  `cursor_from` to `cursor_to` window, and keeps inbox order.
- `narration_generator.py:NarrationConfig.__post_init__` validates the slug
  with `_validate_slug` so a file name cannot escape `data/narrations`.
  `write_json_script` and `write_markdown` check the target path with
  `path_manager._assert_inside_root`.
- `narration_generator.py:NarrationGenerator.generate` handles missing LLM
  pieces and bad scripts this way:
  - With no LLM provider (and no injected clusterer and writer), it
    returns the phase-1 skeleton from `_build_skeleton_narration` with
    `mode="narrated"`, not the link-index fallback
    (`narration_generator.py:221-227, 442-460`).
  - When the script writer returns no blocks, it produces the link-index
    fallback with `mode="fallback"` and a `fallback_reason` summarising
    the validation failures (`narration_generator.py:380-383`).
  - Known issue: the `llm_provider_missing` fallback
    (`narration_generator.py:357, 370`) is unreachable: `_llm_available()` (`narration_generator.py:498-499`)
    is true only when a provider exists (from which both a clusterer and a
    writer are built) or both are injected.
- `narration_generator.py:NarrationGenerator._enforce_quote_share_cap` keeps
  quotes greedily by `(-score, quote_id)` while their total duration stays
  at or below `target * max_quote_share`.
- `narration_generator.py:NarrationGenerator._covered_and_tail`: every
  input episode lands in a segment or in the tail. Episodes the model
  dropped are appended to the tail.
- `quote_selector.py:QuoteSelector._score_turns` excludes these turns:
  - turns with no resolved speaker
  - ad-adjacent turns
  - turns under 12 words
  - turns that mention a sponsor inside the boundary-trim window

  It truncates long turns (over 60 s or 90 words) to a sentence prefix.
- `quote_selector.py:QuoteSelector.select` sorts deterministically by
  `(-score, start_seconds, segment_id)`. It allows `per_episode_max` quotes
  per episode, at most one per speaker per episode, and no two picks within
  60 s of each other.
- `theme_clusterer.py:ThemeClusterer._reconcile`:
  - Every input id appears exactly once. Invented and duplicate ids are
    dropped, and omitted ids go to the tail.
  - There are at most 4 segments.
  - A relationship outside `SEGMENT_RELATIONSHIPS`, or on a segment with
    fewer than 2 episodes, becomes `none`.

  When the LLM call raises, `ThemeClusterer.cluster` sends every episode to
  the tail.
- `script_writer.py:ScriptWriter._validate` treats these as hard failures:
  - `empty_blocks`
  - `unknown_quote_id`
  - `verbatim_leak` (an 8-gram copied from a quote)
  - `word_budget_low` (below 50% of the budget)
  - `word_budget_high` (above 115% of the budget)

  `_validate_reactions` and `_validate_register` produce soft failures,
  which never trigger the fallback on their own.
- `script_writer.py:ScriptWriter.write` makes at most
  `MAX_REGENERATIONS + 1` attempts (temperature 0.4, then 0.2). A budget of
  0 or less returns `empty_blocks` without an LLM call.
- `transcript_loader.py:TranscriptTurnLoader.load` builds turns only from
  `kind == "content"` segments. It returns `[]` when the sidecar is unset,
  missing, outside the storage root, or fails to load.
- `transcript_loader.py:_resolve_speaker`: an unresolved raw `SPEAKER_NN`
  label gives `speaker_name=None`, which makes the turn ineligible as a
  quote.
- `claim_selector.py:select_claim` falls back to the first gist sentence
  when there are no takeaways, and returns `None` when there is neither a
  takeaway nor a gist.
- `artefacts.py:read_narration_header` never raises for a corrupt file.
  On an OS or JSON decode error it logs `narration.read_failed` and returns
  `None`. For valid JSON that is not a dict it returns `None` without
  logging (`artefacts.py:47-48`).

## Side Effects

- LLM calls:
  - `theme_clusterer.py:ThemeClusterer.cluster` calls
    `provider.generate_structured` (temperature 0.2).
  - `script_writer.py:ScriptWriter.write` calls
    `provider.generate_structured`, up to 2 times.
  - `narration_runner.py:NarrationRunner.run` binds
    `stage="narration"` and `briefing_id` as log context variables.
- Database reads:
  - `narration_runner.py:NarrationRunner.run` calls `BriefingRepository.get`
    and `InboxRepository.list_episode_ids_in_window`.
  - `narration_runner.py:NarrationRunner._resolve_episodes` calls
    `PodcastRepository.get_episodes_by_ids`.
- Quota ledger: `narration_runner.py:NarrationRunner.run` calls
  `reserve_quota(..., "narrations_per_day", subject_id=briefing.id)` and
  commits it.
- Storage writes (local or S3):
  - `narration_generator.py:NarrationGenerator.write_json_script` writes
    `<basename>.json` under `path_manager.narrations_dir()` through
    `file_storage.write_text`.
  - `narration_generator.py:NarrationGenerator.write_markdown` writes
    `<basename>.md` in the same directory.
- Storage reads:
  - `narration_generator.py:NarrationGenerator._read_summary` reads the
    summary with `file_storage.read_text`.
  - `transcript_loader.py:TranscriptTurnLoader._load_sidecar` reads the
    cleaned-transcript sidecar and replays the transcript-edit op log with
    `core.transcript_edits.load_effective_transcript`.
  - `transcript_loader.py:TranscriptTurnLoader.load_episode_facts` reads
    episode facts with `FactsManager` and caches them per instance.
- Fallback rendering:
  `narration_generator.py:NarrationGenerator._build_fallback_narration`
  calls `BriefingScriptGenerator.generate` and logs `narration.fallback`.
- Existence check: `narration_runner.py:NarrationRunner.artifact_exists`
  stats `narrations_dir()/{briefing_id}-{slug}.json`.
- No I/O: `quote_selector.py`, `register.py`, `claim_selector.py` and
  `markdown_renderer.py` only compute.

## Anchor prompts

This section is the contract for the sibling package
`thestill/services/narration_prompts`. It lives here, not in an `EGM.md`
inside that folder, because `available_anchor_prompts()` treats every `*.md`
file in the folder as an anchor voice.

The package holds the anchor system prompts for the narrated briefing as
Markdown voice files (`conversational_v2.md`, `conversational_anchor.md`,
`newsroom_anchor.md`), plus `noise_phrases.py`, the list of phrases the
anchor must not use. The prompt loader and the script writer's register lint
both read that one list.

Interface:

- `__init__.py:PROMPTS_DIR`: the directory holding the voice files.
- `__init__.py:DEFAULT_ANCHOR_PROMPT_NAME`: `"conversational_v2"`.
- `__init__.py:NOISE_PHRASES_PLACEHOLDER`: the `{{noise_phrases}}` token a
  voice file may contain.
- `__init__.py:available_anchor_prompts()`: sorted stems of every `*.md` file
  in `PROMPTS_DIR`.
- `__init__.py:load_anchor_prompt(name)`: the voice text with
  `{{noise_phrases}}` filled in. `__init__.py:load_default_anchor_prompt()`
  loads the default voice.
- `noise_phrases.py:NOISE_PHRASES`: tuple of banned filler phrases.
  `render_noise_phrases()` renders it for a prompt;
  `find_noise_phrases(text)` and `count_noise_phrase_hits(text)` search text
  ignoring case and punctuation.

Invariants:

- Every `*.md` file in `PROMPTS_DIR` is listed as a voice, so the folder
  must hold voice files only (`__init__.py:available_anchor_prompts`).
- `load_anchor_prompt` raises `ValueError` when the name does not match
  `^[a-z0-9_]+$` (`_PROMPT_NAME_RE`), resolves outside `PROMPTS_DIR`, or has
  no file. It never falls back to another voice.
- `load_anchor_prompt` fills `NOISE_PHRASES_PLACEHOLDER` from
  `render_noise_phrases()`, so the model's instruction and the lint in
  `script_writer.py` use the same `NOISE_PHRASES` tuple.

Side effects:

- `load_anchor_prompt` reads `<name>.md` from disk on every call, with no
  cache, so an edited voice takes effect on the next `thestill narrate` run.
- `noise_phrases.py` does no I/O.

Verification: `./venv/bin/pytest tests/unit/services/test_narration_prompts.py`

## Verification

- Run: `./venv/bin/pytest tests/unit/services/narration`
- Full subsystem: `./venv/bin/pytest tests/unit/services`
