# Entity Page Enrichment

> **Status:** 🚧 Active development (2026-07-01 — corrected: Tier-0 shipped via #47 — `models/enrichment.py`, entity repo reads `enrichment`)
> **Created:** 2026-05-22
> **Updated:** 2026-05-22
> **Author:** Engineering
> **Related:** [#28 corpus-search-and-entities](28-corpus-search-and-entities.md) (owns the entity page; this spec enriches it), [#30 mcp-anchors-and-entity-discovery](30-mcp-anchors-and-entity-discovery.md), [#15 mistral-llm-provider](15-mistral-llm-provider.md) (provider-add pattern), [#42 robustness](42-robustness-and-failure-mode-hardening.md) (FM-1/FM-2/FM-4 inform the failure model)

---

## Executive Summary

Today an entity page ([Entities.tsx](../thestill/web/frontend/src/pages/Entities.tsx),
served by [api_entities.py](../thestill/web/routes/api_entities.py)) is a
*database view*: canonical name, aliases, a one-line ReFinED description, mention
counts, podcast roles, co-occurring entities, and a mentions feed. It answers
"where was this name said," but not "who/what is this" or "what's interesting
about them." It's accurate and dull.

We already hold the key to fixing the first half cheaply: resolved entities
carry a **Wikidata QID** ([entities.py:112](../thestill/models/entities.py#L112)),
and we already make one Wikidata round-trip per entity for P31 gating
([wikidata_client.py](../thestill/core/wikidata_client.py)). The same endpoint
serves images, birth dates, founders, websites, logos, and more — all free. On
top of that, we have five LLM providers wired up
([llm_provider.py](../thestill/core/llm_provider.py)) that currently never touch
entities, and a clean structured-output pattern to copy
([facts_extractor.py:122](../thestill/core/facts_extractor.py#L122)).

This spec adds an **enrichment layer** in three tiers of cost/effort:

- **Tier 0 — Structured facts & visuals (free, "straightforward").** Wikidata
  claims (photo/logo, vital stats, affiliations) + the Wikipedia REST summary
  (lead paragraph + thumbnail), plus internal wins from data we already have
  (founder↔company cross-links, the co-occurrence relationship graph, "most
  discussed on" by mention count). No LLM, no paid APIs.
- **Tier 1 — LLM narrative & delight (cached).** "Why they matter," quirky
  facts, founding story, and a neutral "what the shows are saying" synthesis of
  notable quotes — grounded and cited, generated via the existing provider.
- **Tier 2 — External / ambitious.** Company financials & funding, news-event
  correlation for mention spikes, a predictions tracker, and an audio supercut
  of quote clips.

**Key principle:** enrichment is **additive display data, gated on a Wikidata
QID** — it never blocks the pipeline, never changes extraction/resolution, and
is only fetched for Wikipedia-notable entities (a QID *is* the notability gate,
which keeps us from fetching biographical data about a private individual merely
named on a show). The page must render fully and look intentional when
enrichment is absent (the long-tail majority of entities have no QID).

**Explicitly cut (decided):** per-mention **sentiment** scoring — sentiment
arcs, stance/"warmest-coolest," and bull/bear splits. The `sentiment` column on
[EntityMention](../thestill/models/entities.py#L154) stays unused for now. The
discourse features kept below (relationship graph, "most discussed," notable
quotes, audio supercut) are all count- or content-based, not sentiment-based.

---

## Table of Contents

1. [Goals & Non-Goals](#goals--non-goals)
2. [Current State](#current-state)
3. [Design Overview](#design-overview)
4. [Data Sources & Field Map](#data-sources--field-map)
5. [Data Model & Persistence](#data-model--persistence)
6. [Enrichment Pipeline](#enrichment-pipeline)
7. [Failure Model](#failure-model)
8. [API Surface](#api-surface)
9. [Frontend](#frontend)
10. [Tiers / Implementation Phases](#tiers--implementation-phases)
11. [Testing](#testing)
12. [Risks & Mitigations](#risks--mitigations)
13. [Open Questions](#open-questions)
14. [Cross-References](#cross-references)

---

## Goals & Non-Goals

### Goals

- Make person and company pages genuinely informative and fun to read, not just
  a mention ledger.
- Reuse what we already have: the QID, the Wikidata client, the co-occurrence
  aggregate, the LLM provider abstraction.
- Degrade gracefully: a QID-less or fetch-failed entity still renders a clean,
  intentional page.
- Keep enrichment **out of the critical path** — it's a side-channel that the
  page reads, never a step that can stall ingest/refresh.

### Non-Goals

- Not changing entity extraction or resolution — [#28](28-corpus-search-and-entities.md)
  owns GLiNER/ReFinED/coref/anchor. This spec consumes their output.
- Not per-mention sentiment (see Executive Summary).
- Not a real-time lookup on page load — enrichment is fetched/generated in batch
  and cached; the page reads the cache.
- Not a general human-editable entity CMS in v1 (a manual-override path that
  survives reindex, mirroring `mention_overrides`, is noted as future work).
- Not enriching non-notable (QID-less) entities with external data — by design.

## Current State

- **Page:** [Entities.tsx](../thestill/web/frontend/src/pages/Entities.tsx) renders
  the [EntitySummaryResponse](../thestill/web/routes/api_entities.py#L208) from
  `GET /api/entities/{type}/{id_slug}`.
- **Response today:** `entity` (id/type/canonical_name/wikidata_qid), `aliases`,
  `description` (ReFinED-derived), `mention_count`, `cooccurring`,
  `recent_mentions`, `hosts_podcasts`, `recurring_podcasts`, `guest_episodes`.
- **Entity record:** [EntityRecord](../thestill/models/entities.py#L97) — adds
  `wikidata_instance_of` (cached P31) and timestamps. No image, no structured
  facts, no narrative.
- **Wikidata access:** [WikidataClient.fetch_p31](../thestill/core/wikidata_client.py#L71)
  hits `Special:EntityData/{QID}.json` and extracts only P31. Per-client LRU;
  failures collapse to `[]`.
- **Repo:** the `entities` table is upserted in
  [sqlite_entity_repository.py:119](../thestill/repositories/sqlite_entity_repository.py#L119);
  `find_entity_by_qid` ([:177](../thestill/repositories/sqlite_entity_repository.py#L177))
  already exists — useful for QID→local-entity cross-linking.
- **LLM:** providers built via
  [create_llm_provider_from_config](../thestill/core/llm_provider.py#L2975);
  structured output via [generate_structured](../thestill/core/llm_provider.py#L683)
  / `generate_structured_cached`. [FactsExtractor](../thestill/core/facts_extractor.py#L122)
  is the canonical "build provider → system+user prompt → typed response" shape
  to mirror.

## Design Overview

```
                        ┌──────────────────────────────────────┐
 resolved EntityRecord  │            EntityEnricher             │
 (has wikidata_qid) ───▶│  Tier 0  WikidataClient.fetch_facts   │──┐
                        │          WikipediaClient.fetch_summary │  │
                        │  Tier 1  LLM (generate_structured)     │  │ writes
                        │  Tier 2  finance / news / audio        │  │
                        └──────────────────────────────────────┘  ▼
                                                        entity_enrichment table
                                                        (1 row / entity, JSON +
                                                         per-source status/ts)
                                                                   │ read
        GET /api/entities/{type}/{slug}  ──▶  EntitySummaryResponse.enrichment  ──▶  Entity page
```

- A new `core/entity_enricher.py` owns the orchestration; `core/wikidata_client.py`
  gains a `fetch_facts(qid)` method (DRY — one Wikidata client, per [#42](42-robustness-and-failure-mode-hardening.md)
  FM-6) and a thin `core/wikipedia_client.py` wraps the REST summary endpoint.
- Enrichment is persisted in a **separate `entity_enrichment` table** keyed by
  `entity_id`, so (a) the hot `entities` table stays lean, and (b) enrichment
  survives an entity reindex the way `mention_overrides` does — a reindex must
  not wipe a fetched photo or a generated bio.
- The API gains **one additive, nullable field** (`enrichment`) on
  `EntitySummaryResponse`; the frontend renders richer sections when present and
  the existing layout when absent.

## Data Sources & Field Map

All Tier-0 sources are free and require only attribution. Wikidata properties
are read from the same `Special:EntityData/{QID}.json` payload we already fetch.

**Person (Wikidata):** `P18` image, `P569` date of birth (→ compute age),
`P570` date of death, `P19` place of birth, `P27` citizenship, `P106`
occupation, `P69` educated at, `P39` positions held, `P102` party,
`P856` official website, `P2002` X/Twitter, `P2013` Facebook, `P2003`
Instagram, `P112` (reverse: companies *founded by* them), `P1830`/employer.

**Company (Wikidata):** `P154` logo, `P571` inception, `P112` founders (→
cross-link to their person pages), `P159` headquarters, `P452` industry,
`P169` CEO/chief exec, `P1128` employees, `P749` parent, `P355` subsidiaries,
`P414` stock exchange / ticker, `P1056` products, `P856` website.

**Wikipedia REST** (`/api/rest_v1/page/summary/{title}`): lead `extract`,
`thumbnail`/`originalimage`, `content_urls`. Cheap, gives a clean one-paragraph
"what is this" plus a fallback image when Wikidata `P18`/`P154` is empty. The
Wikipedia page title comes from the QID's sitelinks (also in the EntityData
payload) — no separate title-search needed.

**Internal (no fetch):** founder↔company links via
[find_entity_by_qid](../thestill/repositories/sqlite_entity_repository.py#L177)
on the `P112`/`P1830` QIDs; the co-occurrence list already in the summary
payload powers the relationship graph; `mention_count` per podcast powers "most
discussed on."

**Tier 2 (external, paid/ambitious):** financials & funding (a finance or
Crunchbase-class API — out of the free tier), news-event correlation for mention
spikes (news/search API), predictions tracker (LLM over transcript),
audio supercut (stitched from existing `audio_url` + `start_ms`/`end_ms`).

## Data Model & Persistence

New table (additive migration, alongside the `wikidata_instance_of` migration
pattern at [sqlite_entity_repository.py:1300](../thestill/repositories/sqlite_entity_repository.py#L1300)):

```
entity_enrichment
  entity_id        TEXT PRIMARY KEY  REFERENCES entities(id) ON DELETE CASCADE
  image_url        TEXT              -- resolved P18/P154 or Wikipedia thumb (Commons)
  image_attribution TEXT             -- author + license string (required for display)
  image_license    TEXT
  wikipedia_extract TEXT             -- lead paragraph
  wikipedia_url    TEXT
  facts_json       TEXT              -- Tier 0 structured facts (typed via Pydantic on read)
  narrative_json   TEXT              -- Tier 1 LLM output (why_they_matter, fun_facts[], founding_story, ...)
  external_json    TEXT              -- Tier 2 (financials, news, predictions)
  -- per-source status so a transient failure is never cached as "no data":
  wikidata_status  TEXT   CHECK(... 'pending'|'ok'|'empty'|'failed')   DEFAULT 'pending'
  wikidata_fetched_at  TEXT
  wikipedia_status TEXT   DEFAULT 'pending'
  wikipedia_fetched_at TEXT
  llm_status       TEXT   DEFAULT 'pending'
  llm_fetched_at   TEXT
  retry_after      TEXT              -- earliest next attempt for a 'failed' source (backoff)
  schema_version   INTEGER DEFAULT 1 -- bump to invalidate LLM output when the prompt changes
  created_at       TEXT
  updated_at       TEXT
```

`facts_json` / `narrative_json` are validated against Pydantic models on read
(constitution #5 — boundary validation), e.g. `EntityFacts` (person/company
variants) and `EntityNarrative`. Timestamps follow the repo ISO-8601 `+00:00`
convention (never raw `CURRENT_TIMESTAMP`).

## Enrichment Pipeline

- **New CLI command** `thestill enrich-entities` (mirrors the other pipeline
  verbs): `--podcast-id`, `--entity-id`, `--max-entities`, `--tier {0,1,2}`,
  `--force` (ignore `retry_after`/`schema_version`), `--dry-run`. Default selects
  QID-bearing entities whose enrichment is `pending` or stale.
- **Optional pipeline hook:** run Tier 0 as a post-resolution step after
  `entity_anchor`, so newly-resolved famous entities get a photo without a manual
  command. Tier 1/2 stay batch-only (cost).
- **Selection:** only entities with a non-null `wikidata_qid` are eligible for
  Tier 0/1 external enrichment. QID-less entities skip straight to the
  internal-only sections (relationship graph, mentions).
- **Caching/cadence:** Wikidata/Wikipedia rechecked infrequently (e.g. ≥30 days);
  LLM regenerated only on `schema_version` bump or `--force`. Reuse the client
  LRU within a batch; persist across runs in the table.
- **Etiquette:** keep the existing descriptive `User-Agent`
  ([wikidata_client.py:41](../thestill/core/wikidata_client.py#L41)); serialize
  Wikimedia requests with a small delay to respect rate limits.

## Failure Model

Enrichment is best-effort, but [#42](42-robustness-and-failure-mode-hardening.md)
applies directly — the failure paths are exactly the traps that spec named:

- **FM-1 (errors as empty results):** a Wikidata 503 must **not** be stored as
  `status='empty'` (= "this person has no photo"). Distinguish `failed`
  (transient, retry after backoff) from `empty` (fetched OK, genuinely no
  `P18`). The page treats `pending`/`failed` as "not yet known," not "none."
- **FM-2 (checkpoint before durability):** mark a source `ok`/`empty` and set
  `*_fetched_at` only **after** the row is committed — never optimistically.
- **FM-4 (silent degradation):** `thestill enrich-entities` reports counts of
  `failed`/`empty`/`ok` per source and exits non-zero on hard errors, so a
  Wikimedia outage that quietly zeroes enrichment is visible.
- Narrow the excepts: network/HTTP/parse errors → `failed` + log; programming
  errors propagate. (The current `fetch_p31` swallows everything to `[]`; the new
  `fetch_facts` must separate "no claim" from "request failed.")

## API Surface

Extend [EntitySummaryResponse](../thestill/web/routes/api_entities.py#L208) with
one nullable field — purely additive, no breaking change:

```python
class EntityEnrichment(BaseModel):
    image_url: Optional[str] = None
    image_attribution: Optional[str] = None      # rendered as required credit
    headline: Optional[str] = None               # Wikidata description / first line
    wikipedia_extract: Optional[str] = None
    wikipedia_url: Optional[str] = None
    facts: list[EntityFact] = []                  # label/value(/url) pairs for the sidebar
    affiliations: list[EntityRef] = []            # founders↔companies, cross-linked to our pages
    narrative: Optional[EntityNarrative] = None   # Tier 1: why_they_matter, fun_facts[], founding_story
    external: Optional[EntityExternal] = None     # Tier 2
    sources: list[str] = []                       # attribution/provenance footer

class EntitySummaryResponse(BaseModel):
    ...
    enrichment: Optional[EntityEnrichment] = None
```

`affiliations` entries reuse the existing `EntityRef` shape so the frontend
links straight to another entity page. `cooccurring` (already present) is what
the relationship graph renders — no new field needed for that.

## Frontend

Rework [Entities.tsx](../thestill/web/frontend/src/pages/Entities.tsx) into a
typed layout that branches on `entity.type` and on enrichment presence:

- **Hero:** photo/logo + canonical name + `headline` one-liner. Falls back to the
  current text header when no image/headline.
- **Vital-stats sidebar:** `facts[]` (born/age, birthplace, occupation, website,
  socials for people; founded, founders, HQ, CEO, employees, industry, ticker for
  companies). Rendered only for present facts.
- **About:** `wikipedia_extract` with a "via Wikipedia" link; **Why they matter /
  fun facts / founding story** from `narrative` (Tier 1), each clearly marked as
  AI-generated and source-linked.
- **Affiliations & products:** cross-linked chips into other entity pages.
- **Relationship graph:** small force-directed view of `cooccurring` (people ↔
  companies ↔ products) — a Tier-0 win on data we already return.
- **In the podcasts you follow:** keep `recent_mentions`; add "most discussed on"
  (by count) and, later, the audio supercut button (Tier 2).
- **Image credit / sources footer:** mandatory attribution for Commons images and
  CC BY-SA Wikipedia text.

Wire types in `src/api/types.ts`; the `useEntitySummary` hook is unchanged (same
endpoint, richer payload).

## Tiers / Implementation Phases

| Tier | Scope | Data source | Effort / risk |
|---|---|---|---|
| **0** | Photo/logo, vital-stats, Wikipedia lead, founder↔company cross-links, relationship graph, "most discussed on" | Wikidata + Wikipedia REST + internal data we already hold | **Low** — extend `WikidataClient`, add `WikipediaClient`, one table, one additive API field, frontend. The "straightforward" tier. |
| **1** | "Why they matter," quirky facts, founding story, "what the shows are saying" quote synthesis | Existing LLM provider (`generate_structured`), grounded in Wikipedia + transcript quotes | **Medium** — prompt design, caching, hallucination guardrails, cost. |
| **2** | Company financials/funding, news↔mention-spike correlation, predictions tracker, audio supercut | Paid finance/news APIs; existing audio + `start_ms`/`end_ms` | **High** — external/paid deps, accuracy & licensing; each item is independently shippable. |

Tier 0 is the spine and ships first; 1 and 2 are independently sequenced.

## Testing

- **Wikidata/Wikipedia parsing:** fixture-based tests over real `EntityData` and
  REST summary payloads (incl. redirects, missing `P18`, dead QIDs) — assert
  `failed` vs `empty` are distinguished (FM-1).
- **Repo round-trip:** write enrichment → reindex the entity → assert enrichment
  survives (mirrors `mention_overrides` survival); ISO-8601 `+00:00` timestamps.
- **Failure model:** a mocked 503 leaves `status='failed'` + `retry_after`, never
  `'empty'`; a committed fetch sets `*_fetched_at` only after the row persists.
- **API:** `EntitySummaryResponse.enrichment` is `None` for a QID-less entity and
  populated for an enriched one; payload validates.
- **LLM (Tier 1):** structured-output schema validation; a stubbed provider so
  tests don't hit the network (per [#04 testing](04-testing.md)).
- **Frontend:** entity page renders with and without `enrichment` (empty-state
  must look intentional); person vs company layouts.

## Risks & Mitigations

- **Hallucination (Tier 1):** ground every generated claim in the Wikipedia
  extract or a transcript quote; show sources; never assert unverified bio facts.
- **Defamation / controversies:** **omit "controversies" in v1.** If added later,
  strictly Wikipedia-sourced, neutral, and behind a toggle — a wrong "controversy"
  about a real person is a real liability.
- **Image & text licensing:** Wikimedia Commons images and CC BY-SA Wikipedia text
  require attribution; store and display credit (`image_attribution`, sources
  footer). Don't hotlink originals where caching is cheaper.
- **Privacy / long tail:** external enrichment is gated on a QID (= notability), so
  we never fetch biographical data about a private individual merely named on a
  show. QID-less entities get internal-only sections.
- **Staleness:** cached with explicit `*_fetched_at` and a re-check cadence;
  `schema_version` invalidates stale LLM output on prompt change.
- **Rate limits / cost:** batch + cache aggressively; serialize Wikimedia calls;
  Tier 1 regenerated only on version bump.

## Open Questions

- [ ] Run Tier 0 automatically as a post-resolution pipeline step, or keep it a
      manual/scheduled `enrich-entities` run only?
- [ ] One `entity_enrichment` row with JSON columns (proposed) vs per-source rows?
      JSON keeps it 1:1 and simple; per-source rows ease independent refresh.
- [ ] Manual-override path for enrichment (human-curated photo/blurb) that
      survives reindex — defer to a follow-up, or design the table for it now?
- [ ] Tier 2 financial data source & licensing — which provider, and is it in
      scope for self-hosted users at all?
- [ ] Does Postgres migration ([#44](44-postgres-migration.md)) need the new table
      in its port list? (Yes — add `entity_enrichment` to the repo interface there.)

## Cross-References

- [28-corpus-search-and-entities.md](28-corpus-search-and-entities.md) — owns
  entity extraction/resolution and the entity page (Phase 5); this spec enriches
  its output.
- [42-robustness-and-failure-mode-hardening.md](42-robustness-and-failure-mode-hardening.md)
  — FM-1/FM-2/FM-4 drive the failure model above.
- [44-postgres-migration.md](44-postgres-migration.md) — new table must join the
  repo port list.
- Code: [wikidata_client.py](../thestill/core/wikidata_client.py),
  [llm_provider.py](../thestill/core/llm_provider.py),
  [facts_extractor.py](../thestill/core/facts_extractor.py),
  [api_entities.py](../thestill/web/routes/api_entities.py),
  [sqlite_entity_repository.py](../thestill/repositories/sqlite_entity_repository.py),
  [Entities.tsx](../thestill/web/frontend/src/pages/Entities.tsx).
