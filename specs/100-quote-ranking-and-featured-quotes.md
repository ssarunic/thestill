# Quote Ranking: Featured Quotes and Most Quoted People

> **Status:** 📝 Draft (2026-10-15)
> **Created:** 2026-10-15
> **Priority:** High. The public person and entity pages (spec #94, UX brief 2026-10-15) lead with "who said what"; without a quality gate and a ranking they would lead with sponsor reads and sentence fragments.
> **Author:** Product & Engineering
> **Related:** [#28 corpus-search-and-entities](28-corpus-search-and-entities.md), [#33 narrated-digest](33-narrated-digest.md) (quote selector reused here), [#81 live-wikidata-entity-linking](81-live-wikidata-entity-linking.md), [#94 public-pages](94-public-pages.md), [#96 prompt-injection-hardening](96-prompt-injection-hardening.md), [#99 top-chart-movement-and-topics](99-top-chart-movement-and-topics.md) (shares the chart's side column)

---

## Executive summary

The public pages UX brief puts a **featured quote** at the top of the person and entity pages, and its aspirational mockups add two more uses:

1. A **featured quote on the show page**, so the one public page without a quote gets one.
2. A **Most quoted people, last 30 days** list on the top chart: named people ranked by how many quality quotes they gave across all shows, each linking to their person page.

All three need the same two things the codebase does not have: a **gate** that says which quotes may appear in public, and a **score** that says which quote is best. This spec builds one table of public quote candidates per episode, scored deterministically after the entity stage, and three read paths over it: featured quote for a person, an entity or a show; quote streams for those pages; and the leaderboard.

It also adds the **hide flag on entities** the brief lists as a blocker for person pages, because a quote attributed to the wrong person is the worst failure these pages can have.

## Current state

**A quote is an `entity_mentions` row** ([postgres_schema.py:417-437](../thestill/repositories/postgres_schema.py#L417-L437)). There is no quotes table.

- **Speaking rows**: one `role = 'speaking'` row per content segment, `extractor = 'speaker:synth'` ([entity_extractor.py:526-595](../thestill/core/entity_extractor.py#L526-L595)). It gets an `entity_id` only when the segment's speaker label matches exactly one host, recurring or guest anchor; otherwise it is `unresolvable`. Its `quote_excerpt` is the segment's **first sentence**, capped at 240 characters (`_speaker_excerpt`, L640-651).
- **NER rows**: one per detected mention, `quote_excerpt` = about 200 characters around the mention, snapped to sentences (L654).
- `speaker` is display-name text, not an id. Unmapped `SPEAKER_NN` labels and the literal string `"None"` leak through ([segmented_transcript_cleaner.py:676-678](../thestill/core/segmented_transcript_cleaner.py#L676-L678), [scripts/verify_speakers.py:98-111](../scripts/verify_speakers.py#L98-L111)).

**`list_mentions_by_speaker`** ([postgres_entity_repository.py:659-697](../thestill/repositories/postgres_entity_repository.py#L659-L697)) matches `speaker ILIKE '%name%'` and returns every resolved mention in segments that speaker voiced, so one segment can return several rows. The production check on 2026-10-15 for Scott Galloway returned the same line three times, a sponsor read ("send us a voice recording at officehours@…") and a three-word fragment ("wisdom was clear.").

**Ads.** Only `kind == "content"` segments produce mentions ([entity_extractor.py:324-328](../thestill/core/entity_extractor.py#L324-L328)), so ad breaks the cleaner tagged never become quotes. Host-read sponsor lines inside content segments still do.

**A quote gate exists for narration.** [quote_selector.py](../thestill/services/narration/quote_selector.py) (spec #33) rejects turns under 12 words, over 60 s, without a speaker, adjacent to ads (`is_ad_adjacent` from [transcript_loader.py](../thestill/services/narration/transcript_loader.py)), sponsor terms near boundaries, leading pronouns, dangling phrases and mid-sentence endings, and scores length fit (30-90 words, 12-35 s). It is deterministic and tested.

**No aggregates.** No per-speaker quote counts exist. `detect_top_speakers` ([postgres_entity_repository.py:1425-1444](../thestill/repositories/postgres_entity_repository.py#L1425-L1444)) is an ad-hoc per-podcast `GROUP BY speaker`.

**No way to hide a person.** Podcasts get `public_pages` in spec #94; entities have no equivalent.

## Design

### 1. `public_quote_candidates`

One row per quotable turn, written by a new pipeline step after `extract-entities` (and by a backfill command):

```sql
CREATE TABLE public_quote_candidates (
    id                bigint GENERATED ALWAYS AS IDENTITY PRIMARY KEY,
    episode_id        uuid     NOT NULL REFERENCES episodes(id) ON DELETE CASCADE,
    podcast_id        uuid     NOT NULL REFERENCES podcasts(id) ON DELETE CASCADE,
    segment_id        bigint   NOT NULL,
    speaker_entity_id text     NOT NULL REFERENCES entities(id) ON DELETE CASCADE,
    start_ms          bigint   NOT NULL,
    end_ms            bigint   NOT NULL,
    text              text     NOT NULL,          -- <= 320 chars, whole sentences
    word_count        smallint NOT NULL,
    about_entity_ids  jsonb    NOT NULL DEFAULT '[]', -- resolved NER entities named in the turn
    score             real     NOT NULL,
    pub_date          timestamptz NOT NULL,       -- episode date, denormalised for windows
    gate_version      smallint NOT NULL,
    UNIQUE (episode_id, segment_id)
);
CREATE INDEX ON public_quote_candidates (speaker_entity_id, pub_date DESC);
CREATE INDEX ON public_quote_candidates (podcast_id, pub_date DESC);
CREATE INDEX ON public_quote_candidates USING gin (about_entity_ids);
```

**Source text** is the cleaned transcript turn (via the narration transcript loader), not `quote_excerpt`, which holds only the first sentence for speaking rows. The step picks the best window of whole sentences inside the turn that fits 320 characters, preferring the window that contains a named entity.

**Speaker** is the speaking row's `entity_id`. A turn whose speaking row is not resolved to a person entity is never a candidate. That restricts public quotes to anchored people (hosts, recurring guests, episode guests), which is the trustworthy set today.

**About** lists entities whose resolved, non-speaking mentions fall inside the chosen window. This powers "Scott Galloway on Nvidia" and entity-page quote streams without a second query.

### 2. The public gate

A candidate is written only if all of these pass. The narration checks are reused, not copied (FM-6): the selector's per-turn predicates move into a shared `quote_gate.py` used by both.

| Check | Source |
| --- | --- |
| Speaker resolved to a `person` entity that is not hidden | new |
| Episode's podcast has `public_pages = true` | spec #94 |
| 12+ words, ends a sentence, no leading pronoun, no dangling phrase | narration selector |
| Not ad-adjacent; no sponsor terms (promo codes, URLs, "brought to you by", email addresses) anywhere in the window | narration selector, widened to the whole window |
| Not in the first 90 s or last 60 s of the episode (intros, outros, housekeeping) | new |
| Not a near-duplicate (normalised text equal, or 90% token overlap) of a candidate already written for the same speaker in the last 30 days | new |
| No `SPEAKER_NN`, `None`, `Unknown` in the text or speaker | new |
| Mojibake repair passed | [mojibake_repair.py](../thestill/core/mojibake_repair.py) |

Each rejection increments a counter by reason (`quote_gate.rejected{reason}`) so the gate can be tuned against real data, as spec #86 asks for quality loops.

### 3. The score

Deterministic, no LLM in Phases 1-2:

```
score = length_fit            # narration selector, 0..1
      × (0.6 + 0.4 × named)   # 1 if the window names a resolved entity
      × self_contained        # 0.7 if it opens with a connective ("and", "but", "so")
      × recency               # 0.5 ^ (age_days / 30), applied at read time, not stored
```

`score` stores everything but recency; recency is applied when reading so a stored row never goes stale.

A Phase 3 option is an LLM rerank of the top 20 candidates per person per week ("which of these would a journalist quote?"), with candidate text wrapped per spec #96. It is only worth doing if the Phase 2 review (below) shows the heuristic picking dull lines.

### 4. Read paths

| Surface | Query | Rules |
| --- | --- | --- |
| Person page featured quote | speaker = person, last 90 days, max score × recency | At most one per episode in any list |
| Person page `?about=X` featured quote | speaker = person, `about_entity_ids ? X` | Falls back to All if none |
| Entity page featured quote | `about_entity_ids ? X`, speaker ≠ X | Speaker must not be the entity itself |
| Entity page "Who's talking" | group by speaker for `about ? X`, last 90 days | Count quotes and distinct shows; best quote per speaker |
| Show page featured quote | podcast = show, last 30 days | Prefer guests over hosts when scores tie within 10% |
| Most quoted people, last 30 days | group by speaker, last 30 days, count ≥ 3, distinct podcasts ≥ 1 | Top 10; cache 1 hour |

All read paths go through one repository module and are on the public routers of spec #94. Every public quote response carries `speaker`, `podcast`, `episode`, `start_ms` and the episode URL with `?q=<seconds>`.

The leaderboard counts **quotes**, not mentions, so a host who speaks for an hour every day does not top it by volume alone: at most 3 candidates per speaker per episode count toward it.

### 5. Hiding an entity

New column `entities.public_hidden boolean NOT NULL DEFAULT false` and `thestill set-entity-visibility --entity person:x --public|--hidden`. When hidden:

- The person or entity page answers 404 to anonymous visitors, with `noindex`, and leaves the sitemap.
- Their candidates stay in the table but are filtered out of every public read, including other people's pages where they are the speaker.
- Signed-in pages are unchanged.

This is the takedown path for a person who asks to be removed, and the emergency switch for a misattribution like the 2026-09 namesake incident.

## Failure modes

| Risk | Guard |
| --- | --- |
| Quote attributed to the wrong person | Only anchor-resolved speakers; `public_hidden` switch; quote always links to its episode so a reader can check |
| Sponsor read as a featured quote | Widened sponsor-term check, intro/outro exclusion |
| Same line repeated across a person's page | Unique per segment; near-duplicate check per speaker |
| Featured quote changes on every page load | Deterministic score; recency computed from the day, not the second |
| Leaderboard dominated by daily-show hosts | 3-per-episode cap |
| Anonymous traffic triggers work | Candidates are built in the pipeline; public reads are indexed SELECTs, cached |

## Non-goals

- Quotes from unanchored speakers. Better speaker identification is its own spec.
- Audio clips of quotes for anonymous visitors.
- Sentiment ("positive about Nvidia"). The column exists but nothing fills it.

## Phases

| Phase | Scope |
| --- | --- |
| 1 | `quote_gate.py` shared with narration; `public_quote_candidates` + pipeline step + `thestill build-quote-candidates [--podcast-id] [--since]` backfill; `entities.public_hidden` + CLI; featured quote and quote streams for person and entity pages |
| 2 | Show-page featured quote; Most quoted people list on `/top`; gate rejection counters; manual review of the top 50 featured quotes before launch |
| 3 | Optional LLM rerank, only if Phase 2 review asks for it |

Phase 1 is a prerequisite for launching public person and entity pages.

## Testing

- Unit: each gate check with fixtures from real rejected lines (the sponsor read, the fragment, the triple duplicate above).
- Unit: window selection keeps whole sentences and stays ≤ 320 characters.
- Unit: score is deterministic for identical input.
- Repository (SQLite and Postgres contract): the six read paths, hidden entities filtered everywhere, `public_pages = false` podcasts filtered everywhere.
- Narration regression: spec #33 selector tests still pass after the predicates move to `quote_gate.py`.
- Review step (manual, Phase 2): top featured quote for 50 random public people and entities, per the "check 10 random episodes" practice for pipeline changes.

## Open questions

1. Is 320 characters per quote, with a few dozen quotes per episode visible across all pages, acceptable as a short excerpt of a publisher's show (spec #94 open question 2)?
2. Should "Most quoted people" exclude hosts by default, so it surfaces guests? Default here: include everyone, rank by quote count with the 3-per-episode cap.
3. Minimum count for the leaderboard: 3 quotes in 30 days is a guess; check against production before Phase 2.
