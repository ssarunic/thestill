# entity_linking

## Overview

Turns a spoken entity name into a Wikidata QID (spec #81). `factory.build_linker` returns one of two linkers behind the `EntityLinker` protocol:

- `LiveWikidataLinker`: fetches Wikidata/Wikipedia candidates, has an LLM pick one, validates the pick against the offered set and the reviewer blacklist, and caches the decision per podcast. Selected when `config.entity_linker == "live"`.
- `thestill.core.entity_resolver.EntityResolver`: the legacy ReFinED linker. It is imported lazily and used for any other setting.

The pipeline caller is `thestill/core/task_handlers.py:resolve_pending_mentions` / `handle_resolve_entities`. This package has no direct SQL. It persists only through the injected link-decision repository.

## Interface

- `protocol.py:EntityLinker`: protocol with `uses_context: bool` and `resolve(mentions, *, is_blacklisted=None, context=None) -> List[ResolutionResult]`. When `uses_context` is False, callers skip building a `LinkContext`.
- `protocol.py:IsBlacklisted`: `Callable[[str, str], bool]` over `(surface_form, qid)`.
- `factory.py:build_linker`: builds a `LiveWikidataLinker` (`WikidataCandidateSource` + `WikipediaClient`, `LLMCandidateChooser`, `LinkDecisionCache`, shared rate limiter) when `config.entity_linker == "live"`. Otherwise it builds `EntityResolver`.
- `factory.py:create_linking_provider`: picks the chooser LLM from `ENTITY_LINKING_PROVIDER` / `_MODEL`. Without those settings it falls back to the cleaning provider and model.
- `factory.py:linker_is_available`: always True for `live`. Otherwise returns `EntityResolver.is_available()`.
- `types.py:surface_key`: collapses whitespace and casefolds in Python, never in SQL. It is the cache and grouping key.
- `types.py:LinkContext`: frozen record of episode/podcast ids, language (default `en`), titles, descriptions, `anchor_names` and `resource_hints`.
- `types.py:NameGroup`, `Candidate`, `ChoiceDecision`, `LinkDecision`: frozen per-stage values. `LinkDecision` also carries `from_cache`, `cache_scope` (`podcast` | `corpus`) and `hinted`.
- `types.py:CONFIDENCE_LEVELS`: `("low", "medium", "high")`.
- `candidates.py:WikidataCandidateSource`: `fetch(groups, language, episode_id) -> CandidateFetch` and `lookup_name(name)`. Wikipedia hits are merged first, then Wikidata hits, with duplicates removed.
- `candidates.py:CandidateFetch`: `candidates[key] == []` is an answer. Keys in `failed` have no answer yet.
- `candidates.py:GENERIC_NOUNS` / `is_generic_noun`: closed set of common nouns that are skipped without a lookup. `is_generic_noun` also matches a generic noun behind determiners ("this person", "our team") and with a possessive suffix ("that guy's"), but not when the spoken last word is capitalised ("This Country" is a title). "the" is not a determiner here.
- `chooser.py:LLMCandidateChooser`: `choose(groups, candidates, context, strict=False) -> ChoiceOutcome`. Its `version` is `f"{PROMPT_VERSION}:{model}"`.
- `chooser.py:Choice` / `ChooserResponse`: Pydantic structured-output schema with `id`, `qid`, `confidence` (`high` | `medium` | `low`), `reason` and `proposed_name`.
- `chooser.py:ChoiceOutcome`: `decisions`, `unanswered`, `llm_calls`, `call_errors`.
- `chooser.py:build_user_message`: builds the chooser prompt and wraps it as untrusted input.
- `live_linker.py:LiveWikidataLinker`: `link()` is the pure core and writes nothing. `resolve()` is the pipeline entry point: it records decisions to the cache and returns results.
- `live_linker.py:LinkOutcome`: run counters plus the `broken` property.
- `live_linker.py:LinkerUnavailableError`: carries partial `results` (safe to record) and `unanswered_names`.
- `live_linker.py:group_mentions`: one `NameGroup` per `surface_key`, with up to 3 excerpts (first, middle, last).
- `cache.py:LinkDecisionCache`: `lookup` (podcast scope first, then corpus), `record_hit` and `record` (upsert, then try promotion).
- `cache.py:invalidate_link_decisions`: deletes the rows for a surface form in every scope. Called on human corrections. Returns 0 when the repo is `None`.
- `rate_limiter.py:WikidataRateLimiter`: `acquire()` blocks until the next evenly spaced slot. `hold_off(seconds)` applies a Retry-After to all callers.
- `rate_limiter.py:get_shared_rate_limiter` / `reset_shared_rate_limiter`: process singleton. `reset_shared_rate_limiter` is a test hook.
- `shared.py:ResolutionResult`: frozen record of `mention_id`, `entity: EntityRecord`, `status` (`resolved` | `unresolvable`) and `method`.
- `shared.py:EntityLinkerBrokenError`, `ResolutionResult`, `linked_result`, `unresolvable_result`, `SURFACE_LABEL_TO_ENTITY_TYPE`: shared with the ReFinED resolver. `thestill/core/entity_resolver.py` re-exports them (plus private helpers such as `_build_entity_id`).
- `shared.py:infer_entity_type_from_label`: maps the extractor label to an `EntityType` (default `topic`). Used by `shared.py:unresolvable_result` and `live_linker.py`. It is not re-exported by `entity_resolver.py`.
- `validator.py:DecisionValidator`, `Validation`, `meets_confidence`, `NOT_OFFERED`, `BLACKLISTED`: check a `ChoiceDecision` against the offered candidates and the blacklist.

## Invariants

- `validator.py:DecisionValidator.validate`: if the LLM picks a QID that was not offered, the result is `NOT_OFFERED` and no decision is made. It is never turned into a "none". A blacklisted pick becomes a high-confidence "none".
- `live_linker.py:LiveWikidataLinker._accepted`: a link needs a QID, confidence at or above `min_confidence` (default `"medium"`), and a final blacklist check.
- `live_linker.py:LinkOutcome.broken`: the linker is broken when it is reachable, `unusable_answers >= _BROKEN_MIN_FAILURES` (3), and unusable answers are at least half of those asked. `resolve` then raises `EntityLinkerBrokenError` and records nothing.
- `live_linker.py:LiveWikidataLinker.resolve`: if any name is unanswered, it raises `LinkerUnavailableError` after recording the cached and fresh decisions it has. Unanswered names stay pending and are never stored as null.
- `live_linker.py:LiveWikidataLinker.link`: a cached link that has since been blacklisted is decided again. So is a cached link for a name that `is_generic_noun` now matches; the candidate source answers it with no candidates, so the fresh "none" replaces the remembered link. Blacklisted candidates are never offered. A name with no candidates becomes a high-confidence "none" without an LLM call. Unusable answers get one strict second pass.
- `cache.py:LinkDecisionCache._usable`: rows from another linker version are ignored. Unlinked or low-confidence rows expire after `none_ttl_days` (default 30). Links never expire.
- `cache.py:LinkDecisionCache.record`: nothing is written when `podcast_id` is None. Corpus rows come only from promotion. Reasons are truncated to `REASON_MAX_CHARS` (200).
- `cache.py:LinkDecisionCache._maybe_promote`: runs only for a recorded decision with a QID and non-low confidence. It considers only podcast rows with the current `linker_version`, a non-null QID and non-low confidence. The decision is promoted to corpus scope when `PROMOTION_PODCASTS` (3) or more of those rows exist and all name the same QID. A disagreeing QID among them blocks promotion; a "none", a low-confidence row or a row from another linker version does not.
- `chooser.py:PROMPT_VERSION`: `"p5"`. Bumping it invalidates cached decisions, because the cache version includes it.
- `chooser.py:LLMCandidateChooser.choose`: batches of `BATCH_SIZE` (40), plus one re-ask of omitted names in batches of `REASK_BATCH_SIZE` (10). Unknown and duplicate ids are ignored.
- `candidates.py:WikidataCandidateSource`: every attempt takes one rate-limiter slot, with at most 2 attempts. Disambiguation pages are dropped. At most `WIKIPEDIA_LIMIT` (5) Wikipedia hits are added. Searches use the episode language, then fall back to `en`.
- `types.py:surface_key`: case folding is done in Python so SQLite and Postgres can never disagree.
- `rate_limiter.py:get_shared_rate_limiter`: there is one limiter per process, and the first caller's `max_rps` wins.

## Side Effects

- Network: `candidates.py:WikidataCandidateSource` calls `thestill/core/wikidata_client.py:WikidataClient.search_entities` and `thestill/core/wikipedia_client.py:WikipediaClient.search_entities`. `shared.py:linked_result` calls `WikidataClient.fetch_p31` / `fetch_superclasses`.
- LLM: `chooser.py:LLMCandidateChooser._ask` calls `thestill/core/llm_provider.py:LLMProvider.generate_structured`. Exceptions are counted as `call_errors`.
- Persistence: `cache.py:LinkDecisionCache` and `invalidate_link_decisions` read and write link decisions through the injected `repositories.link_decision_repository.LinkDecisionRepository` (`get`, `upsert`, `record_hit`, `podcast_decisions`, `delete`).
- Blocking: `rate_limiter.py:WikidataRateLimiter.acquire` calls `time.sleep` outside a `threading.Lock`. The limiter is shared across worker threads.
- Config: `factory.py:build_linker` and `create_linking_provider` read `entity_linker`, `entity_linking_provider`, `entity_linking_model`, `cleaning_provider`, `cleaning_model`, `wikidata_max_rps`, `entity_linking_none_ttl_days` and `entity_linking_min_confidence`.
- Logging (structlog):
  - `live_linker.py` emits `entity_linking_*` events.
  - `candidates.py` emits `entity_linking_candidate_search_failed` (`WikidataCandidateSource.fetch`; the error text only, never the spoken name) and `wikidata_control_chars_stripped` (`_clean`).
  - `chooser.py` emits `entity_linking_chooser_call_failed` (`LLMCandidateChooser.choose`) and `llm_control_chars_stripped` (`Choice._strip_control_chars`).
  - `cache.py` emits `entity_link_decisions_invalidated` and `entity_link_decision_promoted`.
  - `shared.py:linked_result` emits `entity_person_link_dropped` and `entity_type_reclassified`.

## Verification

- Run: `./venv/bin/pytest tests/unit/core/entity_linking`
