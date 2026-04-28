# Corpus Search & Entity Index

**Status**: 📝 Draft
**Created**: 2026-04-28
**Updated**: 2026-04-28
**Priority**: High (unlocks the next product surface; LLM-harness use is the
hero use case)

## Overview

Today the corpus is a pile of per-episode markdown and SQLite rows. There is
no way to ask "everywhere SpaceX was mentioned on Prof G in the last quarter,"
"what has Musk said about AI safety," or "find clips about data centre
buildup across all my feeds" — even though the data to answer all three is
already on disk.

This spec adds three things, in order of differentiation:

1. **An entity index.** Persons, companies, and topics extracted from cleaned
   transcripts, normalised to canonical IDs, with per-mention timestamps and
   speaker attribution. This is the layer nobody ships out of the box for
   podcast corpora — it is the differentiator.
2. **Hybrid retrieval over transcripts and summaries.** Lexical (BM25),
   semantic (vector), and entity-scoped queries, fused with RRF and a
   reranker. Built by **delegating to an embedded [tobi/qmd](https://github.com/tobi/qmd)
   instance** rather than reimplementing — qmd is MIT-licensed and already
   does this well; we own only the entity layer and the tool surface.
3. **An MCP/CLI/REST surface designed for an LLM harness first.** The hero
   use case is "ask Claude/ChatGPT a question against your corpus and get an
   answer composed from playable citations." The web UI is a secondary
   browser over the same tools.

The web UX (command bar, entity pages, augmented reader) is part of this
spec but explicitly secondary. If the MCP surface is right, the web UI is
just another consumer.

## Customer outcomes

The spec is anchored on five outcomes, in priority order. Every roadmap item
exists to serve one of these.

### O1 — "Find what someone said about a thing"

> _"What has Scott Galloway said about data centres in the last six months?"_
> _"All clips where Sequoia is mentioned on All-In."_
> _"Find every time Musk talked about Neuralink."_

This is the single most-requested capability and the one the corpus is
uniquely positioned to answer. Today: impossible without re-listening or
ctrl-F across hundreds of markdown files. After: one MCP tool call or one
URL.

**Concrete win:** ≥ 80% of "person × topic" questions answerable in a single
tool round-trip with at least three cited clips, each playable from the
returned timestamp.

### O2 — "Search the corpus like the web, but with operators"

> _"`"sovereign AI" AND -nvidia` over the last 90 days."_
> _"`speaker:friedberg` AND data centre."_

Users with strong intent want exact-match search with boolean operators and
filters. They do not want a chat interface for this — they want a search
box. This is the layer that should feel as fast as Cmd-F.

**Concrete win:** P50 query latency < 150 ms over the local corpus,
P95 < 500 ms, on a corpus of ~10k episodes.

### O3 — "Surface adjacent things I didn't know to ask for"

> _"Episodes about 'AI infrastructure' even when nobody used those exact
> words" — finds segments about GPU shortages, Stargate, hyperscaler capex._

Semantic retrieval. Mostly invisible glue under O1 and O2 — when the user
types a query, hybrid search blends lexical and semantic so they don't have
to choose. Becomes user-visible as "related episodes" on the reader.

**Concrete win:** on a labelled set of 50 question/episode pairs (built
during phase 1), top-5 semantic recall ≥ 0.8.

### O4 — "Explore the graph"

> _"Who appears most often on Prof G alongside Galloway?"_
> _"Which companies are mentioned together with OpenAI?"_

Entity pages with co-occurrence chips, mention timelines, and "people they
appear with" lists. This is the surface that turns the corpus from a list
of episodes into a knowledge base.

**Concrete win:** every person and company with ≥ 3 mentions has a usable
entity page, reachable from any transcript via the entity highlight.

### O5 — "Ask Claude a question and get a narrative answer with sources"

> _"What has Galloway said about AI capex on Prof G Markets over the past
> year, and how has the framing evolved across episodes?"_
> _"Pull every clip where the All-In hosts disagree on Nvidia, and
> summarise the disagreement."_

The harness use case. Claude composes narrative answers from the structured
tools we expose. Our job is to make the tools citation-shaped enough that
Claude never has to fabricate a quote — every claim is grounded in a tool
result with `episode_id`, `start_ms`, `quote`, and a deeplink.

The example deliberately does **not** ask for sentiment trends. Sentiment
over time is deferred (D.1); v1 acceptance is "narrative answer composed
from cited clips," which Claude can do today over `find_mentions` +
`list_quotes_by` results. If you find yourself wanting to put a sentiment
question in the eval set, defer it to D.1's acceptance — the v1 contract
must not depend on a deferred feature.

**Concrete win:** for ten reference questions (defined in
[Test plan](#test-plan)), Claude produces an answer where 100% of quoted
material is traceable to a tool result it actually received in the same
turn.

### Non-outcomes (explicitly)

- **O5b — sentiment trend visualisations in the web UI.** Defer. The
  sentiment column is captured at extraction time so the data exists; we do
  not ship the chart in this spec. Justification: small-corpus sentiment
  trends are noisy and the visualisation only earns its complexity when
  we have many feeds and many years.
- **Conversation memory / personalised search.** Out of scope. The corpus
  is the same for every user; ranking is not personalised.
- **Multi-language support.** English-only. Non-English transcripts are
  indexed but extraction quality is undefined.
- **Real-time streaming search updates.** Index refreshes happen at the end
  of each pipeline stage; we do not push search index updates over a
  websocket.

## Strategy

### What we do

**1. Two sources of truth, separated by domain.**

- **`AnnotatedTranscript` JSON sidecar (referenced by
  `episodes.clean_transcript_json_path`,
  [annotated_transcript.py:105](../thestill/models/annotated_transcript.py#L105))
  is the source of truth for _transcript text and segment timing_.**
  Per-segment word timestamps, speaker labels, and segment IDs all live
  there; the cleaned Markdown is a render produced from it (spec
  [#18](18-segment-preserving-transcript-cleaning.md)). Entity extraction
  reads the JSON, never the rendered Markdown — that way segment IDs and
  timestamps come from a typed, structured source instead of a
  best-effort parse of human-readable text.

  Episodes that pre-date the JSON sidecar (legacy Markdown-only) are
  **skipped** by `extract-entities` with `entity_extraction_status =
  'skipped_legacy'`. Backfilling them is out of scope for this spec; if
  the legacy fraction matters in practice, that's a separate spec to
  re-clean those episodes through the segment-preserving pipeline.
- **SQLite is the source of truth for _entity data_** — mentions,
  resolution, sentiment, confidence, extractor version, co-occurrences.
  These are extractor outputs that are not naturally expressible in the
  transcript text and would be lossy to round-trip through Markdown.

Everything in `data/corpus/` (per-episode rendered pages, per-entity pages)
is a **projection** — regenerated from `(clean_transcripts/* + SQLite)`.
`thestill reindex` rebuilds the projection; `thestill rebuild-entities`
re-runs extraction/resolution against the cleaned transcripts to rebuild
the entity tables. Neither is rebuildable from `data/corpus/` alone, and
that's fine — `data/corpus/` exists to feed qmd and to be Obsidian-readable,
not to be a backup.

This split keeps qmd happy (it just indexes a directory of Markdown) while
giving the entity layer a typed, queryable home. Power users can still open
`data/corpus/` in Obsidian and get a graph view; the source-of-truth
guarantee is on `clean_transcripts/` and SQLite, not on `corpus/`.

**2. Entity layer is native, retrieval engine is borrowed.**

We build:

- The pipeline stage that extracts entities (GLiNER zero-shot NER).
- The resolution stage that canonicalises them (ReFinED → Wikidata QIDs,
  with local alias fallback).
- The `entities` and `entity_mentions` tables.
- The MCP tools, REST endpoints, and CLI commands that surface them.
- The web entity pages and command bar.

We delegate to qmd, with **two distinct calls for two distinct latency
budgets**:

- **`qmd search`** — raw FTS5 BM25 over the corpus. No reranking, no
  expansion, no LLM in the loop. This is the fast path for `mode=lexical`
  and powers the ⌘K bar (O2's Cmd-F latency budget). Sub-50 ms on the
  fixture corpus.
- **`qmd query`** — full hybrid pipeline (BM25 + vector + RRF +
  cross-encoder rerank + query expansion). Used only for `mode=semantic`
  and `mode=hybrid` where the higher latency is acceptable for richer
  recall. P50 in the hundreds of ms is fine here.

Defaulting `search_corpus(mode)` to `hybrid` is correct for the LLM
harness (Claude is rarely doing Cmd-F semantics), but the ⌘K bar in the
web UI will pin `mode=lexical` so typing feels live. We never silently
upgrade lexical to hybrid — the cost difference is too large.

qmd runs as a sidecar daemon (`qmd mcp --http --port 8181`) pointed at
`data/corpus/`. Our MCP server's `search_corpus` tool delegates over HTTP;
the user only sees one MCP surface.

This split is reversible: if qmd's pace, runtime, or constraints become a
problem, we replace it with the same recipe in pure Python (sqlite-vec +
FTS5 + RRF). The DB schema and tool surface do not change.

**3. MCP-first; CLI parity; REST as a backstop.**

Every capability is reachable as:

- An **MCP tool** with a clean, intent-named signature (Claude is the
  primary client).
- A **`thestill` CLI subcommand** with the same arguments (scripts and
  power users).
- A **REST endpoint** (`/api/search/...`) consumed by the web UI.

All three call the same service layer. There is exactly one
implementation; the three surfaces are thin shells.

**4. Citation-shaped results.**

Every search/list tool returns rows that look like:

```jsonc
{
  "episode_id": "ep_01HXY…",
  "podcast_id": "pod_propg_markets",
  "podcast_title": "Prof G Markets",
  "episode_title": "The AI Capex Cliff",
  "published_at": "2026-03-14",
  "start_ms": 2347000,
  "end_ms": 2389000,
  "speaker": "Scott Galloway",
  "quote": "The hyperscalers are spending like…",
  "score": 0.873,
  "match_type": "lexical|semantic|entity",
  "deeplink": "thestill://episode/ep_01HXY…?t=2347",
  "web_url": "/episodes/ep_01HXY…?t=2347"
}
```

No tool returns a "summarised" field without the source attached. Claude
can compose, but cannot fabricate.

**Mapping qmd hits to exact timestamps.** Pinned by the Phase 0.1
spike against qmd 2.1.0 (CLI + MCP server now share a version, where
1.1.5 had a separately-tagged `qmd@0.9.9` MCP server). Re-verified
under 2.1.0 — response shape is identical.

- qmd's MCP server exposes **one search tool** named `query` (no
  separate lexical/hybrid tools). Mode is selected by the
  `searches[].type` field — `lex` (BM25, no LLM), `vec` (vector), or
  `hyde` (hypothetical-answer expansion). All three return identical
  response shapes.
- Each hit in `result.structuredContent.results[]` has these fields:

  ```jsonc
  {
    "docid":   "#cba654",                                            // content-hash, opaque
    "file":    "thestill-qmd-spike/episodes/ep03-…-compute.md",       // collection-relative path
    "title":   "ep03-dylan-patel-compute",                            // filename stem
    "score":   0.93,                                                  // float 0–1
    "context": null,                                                  // future hook; ignore
    "snippet": "3: @@ -2,2 @@ (1 before, 0 after)\n4: \n5: [01:41] **Dylan Patel:** …"
  }
  ```

- **`snippet` is the load-bearing field for line resolution.** It
  starts with a `<line>: @@ -<start>,<count> @@ (N before, M after)`
  unified-diff hunk header; following lines are `<line>: <content>`
  pairs (1-indexed, source-file lines). The first non-header
  `<line>:` token is the line number `qmd_client.py` keys off when
  binary-searching the segmap sidecar.
- **No byte offsets are exposed by qmd**, so the segmap sidecar's
  `byte_start`/`byte_end` fields are kept as belt-and-braces only —
  every lookup goes through line numbers in production.
- The `qmd search` CLI does emit byte/line markers in plain-text form
  (`qmd://collection/path.md:LINE` URIs + diff hunks), but the CLI
  output is for humans; `qmd_client.py` always uses the MCP transport.

**Implications for the search/service layer:**

- `mode=lexical` and `mode=hybrid` both call MCP `query`, just with
  different `searches` payloads (`[{type:"lex", …}]` vs
  `[{type:"lex", …}, {type:"vec", …}]`). One transport, one tool.
- The `qmd update`/`qmd embed` shell-out remains the right reindex
  path — the MCP server has no write tools.

The line-number-keyed contract:

- Each rendered episode page in `data/corpus/episodes/.../<id>.md` is
  written as one Markdown block per cleaned-transcript segment, prefixed
  by an HTML anchor:

  ```markdown
  <!-- seg id=42 t=2347000-2389000 spk="Scott Galloway" -->
  [[person:scott-galloway]]: The hyperscalers are spending like...

  <!-- seg id=43 t=2389001-2412500 spk="Ed Elson" -->
  [[person:ed-elson]]: Right, but the question is...
  ```

  Anchors are HTML comments — invisible in any Markdown renderer
  (including Obsidian) — and are emitted as the very first line of each
  segment block so byte-offset → segment-id is a single backward scan.

- `corpus_writer.py` writes a sidecar `<id>.segmap.json` next to each
  rendered page, keyed by line range (and byte offsets as belt-and-braces
  for the byte-offset variant):

  ```jsonc
  [
    {"seg_id": 42, "line_start": 7,  "line_end": 9,  "byte_start": 0,   "byte_end": 312, "start_ms": 2347000, "end_ms": 2389000},
    {"seg_id": 43, "line_start": 11, "line_end": 13, "byte_start": 313, "byte_end": 671, "start_ms": 2389001, "end_ms": 2412500}
  ]
  ```

  qmd never reads the sidecar; only `qmd_client.py` does, in-process,
  loading it on the path returned by qmd and binary-searching by whichever
  key qmd actually exposed (line preferred, byte fallback). The sidecar is
  regenerated alongside the Markdown — same idempotent rebuild — so it
  never drifts.

- For entity-scoped tools (`find_mentions`, `list_quotes_by`), timestamps
  come straight from `entity_mentions.start_ms/end_ms`; no qmd round-trip
  involved.

A qmd hit with no resolvable segment (e.g., front-matter region,
between-segment whitespace) is dropped from results rather than returned
with a fudged timestamp. Logged at `info` for diagnostics.

**5. Plain wiki-link syntax for entity references.**

Inside generated markdown, entities are linked as `[[person:elon-musk]]`,
`[[company:spacex]]`, `[[topic:data-centres]]`. This is Obsidian-compatible
(power users could literally open the corpus in Obsidian and get a graph
view for free) and trivially renderable in our web reader.

**6. Phased rollout that produces user-visible value at each phase.**

Each phase below ships something usable. Phase 1 ends with `thestill list-mentions`
in the CLI even though the web UI is still untouched. Phase 4 ends with
`search_corpus` over MCP even though entity pages don't exist yet. We do
not stack a wall of plumbing before the first user-facing capability.

### What we explicitly do not do

| Decision | Why |
|---|---|
| **No fine-tuned NER model.** GLiNER zero-shot only. | Custom training is a six-month project that buys ~5% accuracy on a 10k-episode corpus. Not worth it for a v1. Revisit when (a) zero-shot recall is measurably hurting users or (b) we want non-English support |
| **No graph database.** `entity_mentions` is a row-store table; relations are SQL queries. | A 10k-episode corpus produces ~1M mentions and ~10k entities. SQL with the right indexes serves co-occurrence queries in <100 ms. A graph DB adds a runtime, a query language, and zero capability over what we need |
| **No personalised ranking, no per-user "library".** | The corpus is the same for every user; relevance is not user-specific in v1. Defer until we have multi-user data telling us it matters |
| **No conversational memory in the search layer.** | The harness (Claude/ChatGPT) already has memory. Adding it on the server side duplicates state and creates a sync problem |
| **No real-time index updates over websockets.** | Index updates happen at pipeline-stage boundaries (after `extract-entities`, after `resolve-entities`). The web UI polls or refreshes. Real-time invalidation is overhead with no user-visible win |
| **No custom embeddings model.** Use whatever qmd ships. | Model selection is qmd's problem; we benefit from their upgrades. If we self-host the recipe later, default to `bge-small-en-v1.5` or whatever is current at that time |
| **No sentiment trend chart in the web UI.** | Sentiment over time is the lowest-confidence outcome. Capture the data; defer the UI |
| **No "edit entity" / merge UI in v1.** | Resolution mistakes are corrected by a CLI tool (`thestill entity merge X Y`). A web editor is a 10x scope add. Revisit if mistakes become common |
| **No Postgres migration in this spec.** | SQLite + FTS5 + sqlite-vec serves a 10k-episode corpus. The repository layer keeps the swap mechanical for later |

### Why this strategy and not alternatives

**Alternative A — Build the whole stack in Python from day 1.**
Rejected. qmd is MIT-licensed, current (v2.1.0, April 2026), and gives us
hybrid retrieval + reranking + query expansion as a battle-tested unit.
Reimplementing it before we know we need to is a month of plumbing for
zero differentiation.

**Alternative B — Use a hosted vector DB (Pinecone, Weaviate Cloud).**
Rejected. The product's identity is local-first and the corpus is small
(~10s of GB at the high end). A hosted DB adds latency, cost, an outbound
network dependency, and a privacy story we don't want to tell.

**Alternative C — Ship search without entities first, add entities later.**
Rejected. Entities are the differentiator. Without them, we are competing
with every "search your notes" tool on earth. With them, we are the only
tool that answers "what has X said about Y." Ship the differentiator with
the foundational capability, not after.

**Alternative D — LLM-only NER, no GLiNER.**
Rejected for the extraction stage. Per-chunk LLM calls cost real money at
corpus scale (10k episodes × ~50 chunks/episode × $0.01 ≈ $5k just to
re-extract). GLiNER runs on CPU, costs cents to operate, and is good
enough for the typed entities we care about (PERSON, COMPANY, PRODUCT,
TOPIC). LLMs are still used for canonicalisation hints and for sentiment
tagging where their judgement is the value.

## Architecture

### Data model

**New tables.** Migrations live in `thestill/repositories/migrations/`.

```sql
CREATE TABLE entities (
    id              TEXT PRIMARY KEY,        -- "person:elon-musk", "company:spacex"
    type            TEXT NOT NULL CHECK (type IN ('person','company','product','topic')),
    canonical_name  TEXT NOT NULL,
    wikidata_qid    TEXT,                    -- "Q317521" — nullable
    aliases         TEXT NOT NULL DEFAULT '[]',  -- JSON array
    description     TEXT,
    created_at      TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP,
    updated_at      TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP
);
CREATE INDEX idx_entities_type ON entities(type);
CREATE INDEX idx_entities_wikidata ON entities(wikidata_qid);

CREATE TABLE entity_mentions (
    id                  INTEGER PRIMARY KEY AUTOINCREMENT,
    entity_id           TEXT REFERENCES entities(id) ON DELETE CASCADE,
                        -- NULL until resolution completes; see resolution_status
    resolution_status   TEXT NOT NULL DEFAULT 'pending'
                        CHECK (resolution_status IN ('pending','resolved','unresolvable')),
    episode_id          TEXT NOT NULL REFERENCES episodes(id) ON DELETE CASCADE,
    segment_id          INTEGER NOT NULL,    -- FK to transcript segment; required, see Citation contract
    start_ms            INTEGER NOT NULL,
    end_ms              INTEGER NOT NULL,
    speaker             TEXT,                -- best-effort attribution
    role                TEXT CHECK (role IN ('host','guest','mentioned','self')),
    surface_form        TEXT NOT NULL,       -- exact span text
    quote_excerpt       TEXT NOT NULL,       -- ±1 sentence around the mention
    sentiment           REAL,                -- [-1, 1], nullable
    confidence          REAL NOT NULL,       -- extractor confidence
    extractor           TEXT NOT NULL,       -- "gliner-v2.5", "refined-v1.0"
    created_at          TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP,
    resolved_at         TEXT
);
CREATE INDEX idx_mentions_entity ON entity_mentions(entity_id, episode_id) WHERE entity_id IS NOT NULL;
CREATE INDEX idx_mentions_episode ON entity_mentions(episode_id);
CREATE INDEX idx_mentions_role ON entity_mentions(entity_id, role) WHERE entity_id IS NOT NULL;
CREATE INDEX idx_mentions_pending ON entity_mentions(resolution_status) WHERE resolution_status = 'pending';

-- Materialised co-occurrence (rebuilt nightly)
CREATE TABLE entity_cooccurrences (
    entity_a_id     TEXT NOT NULL REFERENCES entities(id) ON DELETE CASCADE,
    entity_b_id     TEXT NOT NULL REFERENCES entities(id) ON DELETE CASCADE,
    episode_count   INTEGER NOT NULL,
    last_seen_at    TEXT NOT NULL,
    PRIMARY KEY (entity_a_id, entity_b_id),
    CHECK (entity_a_id < entity_b_id)        -- canonical ordering
);
```

**Existing tables touched.** `episodes` gets a single new column:
`entity_extraction_status TEXT` with values `pending|complete|failed`,
plumbed through the existing pipeline-stage status pattern.

### Markdown corpus layout

```
data/corpus/
├── episodes/
│   └── <podcast-slug>/
│       └── <episode-id>.md         # frontmatter + cleaned transcript + entity wikilinks
├── persons/
│   └── elon-musk.md                # frontmatter + auto-summary + mentions list
├── companies/
│   └── spacex.md
└── topics/
    └── data-centres.md
```

**Episode frontmatter:**

```yaml
---
type: episode
episode_id: ep_01HXY...
podcast_id: pod_propg_markets
podcast: Prof G Markets
title: The AI Capex Cliff
published_at: 2026-03-14
duration_ms: 3640000
participants:
  - person:scott-galloway
  - person:ed-elson
mentions_top:
  - company:nvidia
  - company:openai
  - topic:data-centres
language: en
---

# The AI Capex Cliff

[[person:scott-galloway]]: The hyperscalers are spending like...
```

**Person/company/topic frontmatter:**

```yaml
---
type: person
id: person:elon-musk
canonical_name: Elon Musk
wikidata_qid: Q317521
aliases: [Musk, "@elonmusk"]
mention_count: 247
first_seen: 2024-01-12
last_seen: 2026-04-22
---
```

These pages are fully regenerable from the database. They exist on disk so
qmd indexes them and so power users can `obsidian://open` them.

### Pipeline integration

The new work runs as an **asynchronous branch** off `clean-transcript`,
not as a blocking link in the existing chain. `summarize` — the existing
shipping product — must not wait on a GLiNER bug or a qmd outage.

```
download → downsample → transcribe → clean-transcript ┬─→ summarize          (existing critical path)
                                                       │
                                                       └─→ extract-entities → resolve-entities → write-corpus → reindex
                                                          (entity branch, independent failure domain)
```

When `clean-transcript` completes, the dispatcher enqueues **both**
`summarize` and `extract-entities` for the episode. They progress in
parallel under the existing per-stage worker pools (spec
[#20](20-parallel-task-queues.md)). A failure in the entity branch flags
`episodes.entity_extraction_status = 'failed'` and routes to the DLQ
without affecting the summary product. A failure in `summarize` does not
block the entity branch either.

Re-running entity extraction on already-summarised episodes is supported
and idempotent; the two branches share `clean-transcript` as their only
upstream dependency.

- **`extract-entities`**: runs GLiNER over each cleaned transcript segment,
  produces `entity_mentions` rows with `entity_id = NULL`,
  `resolution_status = 'pending'`, and `surface_form`/`segment_id`
  populated. Sentiment scored by a cheap LLM call (Haiku 4.5 or local)
  per mention; this is the only LLM cost in the new path.
- **`resolve-entities`**: groups mentions by `surface_form`, runs ReFinED
  to assign Wikidata QIDs, creates/updates `entities` rows, fills in
  `entity_id`, flips `resolution_status` to `resolved` or `unresolvable`.
  Falls back to local alias matching (Levenshtein) when ReFinED returns
  nothing.
- **`write-corpus`**: regenerates the `data/corpus/episodes/.../*.md`
  file (with `<!-- seg ... -->` anchors per Strategy §4) and the sidecar
  `<id>.segmap.json`, and updates affected
  `data/corpus/persons|companies|topics/*.md` pages.
- **`reindex`**: invokes qmd's CLI to re-embed the changed paths. qmd's
  HTTP server exposes only `/mcp` and `/health` — there is no remote
  reindex endpoint. We shell out:

  ```bash
  qmd update --collection thestill-corpus --paths <changed-files>
  qmd embed --collection thestill-corpus
  ```

  Collection bootstrap (one-time) is `qmd collection add thestill-corpus
  data/corpus --glob "**/*.md"`, run by `make qmd-up` in dev and by the
  installer in deployed setups. Subsequent `update`/`embed` runs are
  incremental — qmd's docid hashes detect unchanged files. The qmd HTTP
  daemon at `:8181/mcp` is left running for reads; only the embed worker
  is invoked for writes.

Each stage is an existing-style atomic processor, plugged into the same
retry + DLQ + queue infrastructure as the other stages. No new pipeline
framework.

**Queue contract changes.** `tasks.stage` is currently a CHECK-constrained
enum (`download|downsample|transcribe|clean|summarize`). This spec adds:

```sql
ALTER TABLE tasks DROP CONSTRAINT tasks_stage_check;  -- (handled via SQLite table-rebuild migration)
-- New CHECK includes: extract-entities, resolve-entities, write-corpus, reindex
```

The dispatcher's "what runs next" logic moves from a single linear
`get_next_stage(current)` to a small **dependency graph** — same primitive
as today's chain, just expressed as `prerequisites_complete(stage,
episode_id)`. After `clean`, the dispatcher fans out to both `summarize`
and `extract-entities` (independent branches). Within the entity branch,
ordering is linear: `extract-entities → resolve-entities → write-corpus
→ reindex`. The `summarize` branch keeps its existing single-stage
shape.

Each new stage gets a handler in the queue dispatcher and a row in the
processing-status grid in the web UI (spec [#09](09-single-user-web-ui.md))
and the queue viewer (spec [#10](10-queue-viewer.md)). The episode-card
processing badge (spec [#21](21-episode-processing-indicator.md)) reports
the new stages too, with display labels: "Extracting entities",
"Resolving", "Writing corpus", "Reindexing".

**Failure isolation rule.** A failure on any entity-branch stage must not
mark the episode as failed in the user-facing sense. The episode is still
"processed" (downloaded, transcribed, summarised); only its entity index
is incomplete. The web UI shows entity-branch failures as a separate
status pill, not as a red episode card.

### MCP tool surface

Hosted by an extended `thestill/mcp/` server. Each tool's input schema is
JSON Schema; outputs are arrays of citation-shaped rows (see Strategy §4)
unless noted.

```
search_corpus(query, mode?: "lexical"|"semantic"|"hybrid"=hybrid,
              filters?: {podcast_id?, date_range?, has_entity?[]},
              limit?: int = 20) → CitationRow[]

find_mentions(entity: string, entity_type?: "person"|"company"|"product"|"topic",
              podcast_id?, date_range?, role?: "host"|"guest"|"mentioned",
              limit?: int = 50) → CitationRow[]

list_quotes_by(speaker: string, topic?: string,
               podcast_id?, date_range?,
               limit?: int = 50) → CitationRow[]

get_entity(id_or_name: string) → {
    entity: EntityRecord,
    mention_count: int,
    cooccurring: EntityRef[],          // top 20
    recent_mentions: CitationRow[]     // top 10
}

list_episodes(podcast_id?, date_range?, has_entity?: string[],
              limit?: int = 50) → EpisodeSummary[]

get_episode(episode_id: string) → {
    episode: EpisodeRecord,
    summary_md: string,
    transcript_url: string,
    participants: EntityRef[],
    top_mentions: EntityRef[]
}

trends(entity: string, metric: "mentions"|"sentiment",
       granularity: "week"|"month" = "month",
       date_range?) → TrendBucket[]    // [{period, count, mean_sentiment}]
```

**Tool-naming rule.** Each tool name describes intent, not schema. `find_mentions`
and `list_quotes_by` are deliberately separate even though they overlap, because
the LLM picks better when names match phrasing.

### CLI surface

Every MCP tool has a CLI peer:

```
thestill search "sovereign AI" --since 90d --podcast prof-g-markets
thestill find-mentions spacex --since 6m
thestill quotes-by "Scott Galloway" --topic data-centres
thestill entity get elon-musk
thestill entity merge person:musk person:elon-musk      # admin
thestill reindex                                        # rebuilds index from disk
```

### REST surface

Mirrors MCP under `/api/search/...`. Documented in
[02-api-reference.md](02-api-reference.md) when shipped.

### Component layout

```
thestill/
├── core/
│   ├── entity_extractor.py        # GLiNER processor (new)
│   ├── entity_resolver.py         # ReFinED + alias fallback (new)
│   ├── corpus_writer.py           # markdown + segmap.json regeneration (new)
│   └── reindex.py                 # shells out to `qmd update`/`qmd embed` (new)
├── search/                         # new package
│   ├── service.py                 # one impl, three surfaces
│   ├── qmd_client.py              # thin HTTP client to qmd daemon
│   ├── ranking.py                 # blend qmd results with entity-scoped SQL
│   └── citation.py                # shape rows into CitationRow
├── repositories/
│   └── sqlite_entity_repository.py  # new
├── mcp/
│   └── tools/                     # split each tool into its own module
└── cli.py                         # add subcommands
```

## Tactical: roadmap

Numbered tasks. **Sequencing principle:** validate the differentiator
(outcomes O1 and O5 over the entity layer) as early as possible; defer
the qmd integration and the web UI to phases that depend on a validated
entity foundation. Each phase produces a user-visible artefact and is
shippable as its own PR.

### Phase 0 — Spike, evals, and foundations (3–4 days)

The point of Phase 0 is to **lock down the unknowns and the measurement
sticks before writing any production code.** Three deliverables, all
small, all upfront.

- **0.1 — qmd metadata spike.** ✅ Done 2026-04-28 against qmd 2.1.0
  (re-run after upgrading from 1.1.5; confirmed identical response
  shape across the major bump). Findings pinned in Strategy §4:
  the MCP `query` tool exposes line numbers via `snippet` text
  (1-indexed `<line>: …` prefix and a `@@ -<start>,<count> @@` diff
  header), no byte offsets; `mode=lexical` and `mode=hybrid` both call
  the same `query` tool with different `searches[].type` payloads.
  Sidecar stays line-keyed; byte offsets are belt-and-braces only.
- **0.2 — Speaker/segment coverage audit.** Run a one-shot script over
  the existing `clean_transcript_json_path` corpus to count: episodes
  with `AnnotatedTranscript` JSON sidecars present, episodes without (=
  legacy, will be skipped), and per-mention speaker-attribution
  coverage. If `speaker IS NULL` for >30% of mentions on a representative
  sample, `list_quotes_by` is unusable and we redesign O1 before
  building it. Output a one-page report; gate Phase 1 on it.
- **0.3 — Eval set construction.** Hand-build:
  - **50 question/episode pairs** for semantic-recall measurement (O3).
    Real questions, real episodes, multiple correct episodes per
    question where applicable.
  - **10 harness reference questions** for the O5 acceptance gate.
    Each annotated with the entities and quote-excerpt fragments a
    correct answer should cite. Pulled from real conversations.
  These exist as `tests/fixtures/eval/` JSON files and are the
  measurement stick from Phase 1 onward — not retrofitted at the end.
- **0.4** Add migration for `entities`, `entity_mentions` (with nullable
  `entity_id` and `resolution_status`), `entity_cooccurrences` tables;
  new `entity_extraction_status` column on `episodes` (with
  `skipped_legacy` allowed).
- **0.5 — Queue migration.** Rebuild `tasks` table with extended `stage`
  CHECK. Replace linear `get_next_stage()` with
  `prerequisites_complete(stage, episode_id)` so `clean=complete` can
  fan out to **both** `summarize` and `extract-entities`. Register
  placeholder handlers; failures isolated to the entity branch
  (Strategy §6).
- **0.6** Add `data/corpus/` directory with `.gitkeep`s; document layout
  in [01-architecture.md](01-architecture.md).
- **0.7** Define `EntityRecord`, `EntityMention`, `CitationRow`,
  `SegmentAnchor` Pydantic models in `thestill/models/entities.py`.
- **0.8** Stub `SqliteEntityRepository` with empty methods + type
  signatures the rest of the work will fill in.

### Phase 1 — Entity extraction, resolution, and MCP alpha (5–7 days)

The hero phase. By the end, **Claude Desktop pointed at our MCP server
can answer "what has X said about Y" with cited clips, on real corpus
data.** No qmd, no web UI, no semantic search — just the entity layer
and the SQL-only tools that ride on it. This is the earliest possible
validation of the product claim.

- **1.1** Add `gliner` to dependencies; pin to a tested version. Vendor
  a small test fixture (one episode's `AnnotatedTranscript` JSON) for
  repeatable extraction.
- **1.2** Implement `core/entity_extractor.py`. **Input: the
  `AnnotatedTranscript` JSON sidecar pointed at by
  `episodes.clean_transcript_json_path`** (not the rendered Markdown).
  Output: `entity_mentions` rows with `entity_id = NULL`,
  `resolution_status = 'pending'`, and `segment_id`/`start_ms`/`end_ms`
  taken directly from the JSON segment that produced the mention.
  Episodes with no JSON sidecar → mark `entity_extraction_status =
  'skipped_legacy'` and emit zero rows. Default entity types:
  `["person","company","product","topic"]`.
- **1.3** Wire into pipeline as the `extract-entities` stage handler
  (registered in 0.5), running as part of the **entity branch**, not
  blocking `summarize`. CLI: `thestill extract-entities --podcast-id
  ... --max-episodes ...`. Update
  [09-single-user-web-ui.md](09-single-user-web-ui.md)'s status grid
  and [10-queue-viewer.md](10-queue-viewer.md)'s stage labels.
- **1.4** Add `refined` to dependencies. Document install footprint in
  [configuration.md](../docs/configuration.md). If memory pressure on
  the API server is a problem, run resolution in a separate worker
  process (decided during 0.2 audit).
- **1.5** Implement `core/entity_resolver.py`. Batch-resolve
  `surface_form` → `wikidata_qid` per episode. Merge into `entities`
  table by QID where available; create local `entity_id` for
  unresolved entities (slugified surface form). Flip mention's
  `resolution_status` to `resolved` or `unresolvable`. Pipeline stage
  `resolve-entities` registered.
- **1.6** Local alias merging: nightly job that collapses entities
  sharing a QID, or whose `canonical_name` matches via Levenshtein <
  0.1 of length.
- **1.7** Co-occurrence materialisation: `thestill
  rebuild-cooccurrences` command, also called automatically at end of
  `resolve-entities` for affected episodes.
- **1.8** **MCP alpha.** Implement and register the SQL-only tools —
  no qmd dependency:
  - `find_mentions(entity, entity_type?, podcast?, date_range?,
    role?, limit?) → CitationRow[]`
  - `list_quotes_by(speaker, topic?, podcast?, date_range?, limit?)
    → CitationRow[]`
  - `get_episode_clip(episode_id, start_ms, end_ms?, ±sec?) →
    CitationRow`
  - `get_entity(id_or_name) → {entity, mention_count, cooccurring,
    recent_mentions}`
  - `list_episodes(podcast?, date_range?, has_entity?[]) →
    EpisodeSummary[]`
  Each returns citation-shaped rows (Strategy §4) with
  `match_type='entity'`. Document the surface in
  [docs/corpus-search-mcp.md](../docs/corpus-search-mcp.md).
- **1.9** CLI peers: `thestill list-mentions`, `thestill find-mentions`,
  `thestill quotes-by`, `thestill entity get|merge|split`. Admin-only
  CLI for merge/split; no UI yet.
- **1.10** Sentiment annotation (off by default behind
  `THESTILL_ENTITY_SENTIMENT=1`). Stored in `entity_mentions.sentiment`
  but **not** consumed by any tool in v1; data is captured for D.1.
- **1.11** Wire into Claude Desktop manifest: add a
  `claude_desktop_config.json` snippet to the docs.
- **1.12** **Run the 10 harness reference questions (from 0.3) against
  the MCP alpha and the entity layer over a real podcast (Prof G
  Markets, ~150 eps). Treat each failure as either a bug, an
  extraction-quality issue, or a tool-shape issue, and fix before
  moving to Phase 2.** **First user-visible artefact, and the O1+O5
  validation gate.**

### Phase 2 — Hybrid corpus search via qmd (4–5 days)

Now that the entity layer is validated, add the lexical/semantic search
layer for queries that aren't entity-scoped (O2, O3).

- **2.1** Add qmd to runtime deps. Document Node.js ≥22 requirement.
  Provide `make qmd-up` / `make qmd-down`.
- **2.2** Collection bootstrap: `thestill corpus bootstrap` runs `qmd
  collection add thestill-corpus data/corpus --glob "**/*.md"`,
  invoked by `make qmd-up` and the installer.
- **2.3** Implement `core/corpus_writer.py`. Regenerates per-episode
  Markdown into `data/corpus/episodes/.../*.md` with `<!-- seg ... -->`
  anchors and writes the `<id>.segmap.json` sidecar (key shape per
  0.1's spike outcome). Per-entity pages regenerated from SQLite.
  Idempotent.
- **2.4** Implement `core/reindex.py` shelling out to `qmd
  update --paths <...>` then `qmd embed`. Hooked as the `reindex`
  stage handler in the entity branch.
- **2.5** Implement `search/qmd_client.py`. Two methods:
  `search_lexical()` calling `qmd search` (raw BM25, fast),
  `search_hybrid()` calling `qmd query` (full pipeline). Each maps
  hits to `CitationRow` via the segmap sidecar. Hits with no
  resolvable segment dropped (logged).
- **2.6** Add `search_corpus(query, mode, filters?, limit?)` MCP tool.
  Mode `lexical` → `search_lexical`; mode `semantic`|`hybrid` →
  `search_hybrid`. Default mode is `hybrid`.
- **2.7** REST mirror under `/api/search/...`.
- **2.8** Re-run the 10 harness reference questions: now Claude can
  answer broader questions ("episodes about X" without naming an
  entity) on top of the entity layer. **Second user-visible artefact.**
- **2.9** Run the 50 question/episode pairs (from 0.3) against
  `search_corpus(mode=hybrid)`; record top-5 recall. Gate this phase on
  ≥ 0.8.

### Phase 3 — Productionisation & failure handling (2–3 days)

The entity branch has been running async since Phase 1, but a few rough
edges need polish before it's hands-off.

- **3.1** Backfill command: `thestill rebuild-entities --podcast-id
  --since` re-runs extraction/resolution over a slice of the corpus,
  for when GLiNER is upgraded or entity types are extended.
- **3.2** DLQ surfacing for entity-branch failures: separate filter in
  the queue viewer ([10-queue-viewer.md](10-queue-viewer.md)) so they
  don't drown the existing critical path.
- **3.3** Latency budget enforcement in CI: per-tool P50/P95 thresholds
  on the fixture corpus (Test plan). Fail PRs that regress.
- **3.4** Skipped-legacy episode count visible in `thestill status`.

### Phase 4 — Command bar and search UI (4–5 days)

The web UI lands only after the MCP surface is real and the entity layer
is validated.

- **4.1** Add `⌘K` global command bar to `Layout.tsx`. Typeahead
  grouped by `Episodes / Persons / Companies / Topics / Quotes`. Hits
  `/api/search/quick` which calls `search_corpus(mode=lexical)` —
  pinned to lexical for typing-latency, never silently upgraded
  (Strategy §2). Operators (`person:`, `company:`, `after:`) parsed
  client-side.
- **4.2** Search results page (the ⌘K "see all results" escape hatch).
  Three tabs: All / Quotes / Entities. Each row plays inline.
- **4.3** Empty/error states across the new surface.

### Phase 5 — Entity pages and graph exploration (4–6 days)

- **5.1** Entity page route: `/entities/:type/:id`. Components:
  `<EntityHeader/>`, `<MentionTimeline/>` (sparkline by month),
  `<NotableQuotes/>`, `<CooccurrenceChips/>`, `<MentionFeed/>`
  (paginated, inline audio scrub via existing `<FloatingPlayer/>`).
- **5.2** Augmented reader: existing transcript view gets entity
  wiki-link rendering with hover cards. Right rail: "People in this
  episode", "Companies mentioned", "Related episodes" (vector
  similarity from qmd `query`).
- **5.3** Empty states (entities with 0–2 mentions, etc.) reviewed
  by Sasa.
- **5.4** Update [README.md](README.md) (this index), bump status to
  `✅ Complete`.

### Deferred (post-spec)

- **D.1** `trends` tool implementation + sentiment chart on entity pages.
- **D.2** Postgres migration path (only if SQLite hits a wall).
- **D.3** Web "merge entity" / "split entity" admin UI.
- **D.4** Personalised ranking / user-specific corpora.
- **D.5** Non-English extraction quality.

## Test plan

Three layers, each gating the next.

### Unit tests

Target: ≥ 90% line coverage on new packages (`thestill/search/`, new
`core/*.py`, new repository).

| Module | Key tests |
|---|---|
| `core/entity_extractor.py` | Fixture `AnnotatedTranscript` JSON sidecar in / fixed `entity_mentions` out (golden file). Rows include `segment_id`/`start_ms`/`end_ms` taken directly from the JSON segment they came from (not parsed from rendered Markdown). `entity_id IS NULL` and `resolution_status='pending'` on every emitted row. Episode with `clean_transcript_json_path = NULL` → zero rows + `entity_extraction_status='skipped_legacy'`. Empty transcript → empty list. Confidence threshold honoured. Unicode-heavy transcript handled. |
| `core/entity_resolver.py` | Mocked ReFinED returns QID → entity row created/merged, mention's `entity_id` filled and `resolution_status='resolved'`. ReFinED returns nothing → local alias slug used or `resolution_status='unresolvable'`. Two surface forms with same QID → one entity. Re-running resolution on already-resolved mentions is a no-op. |
| `core/corpus_writer.py` | Rendered episode Markdown contains `<!-- seg id=N t=A-B spk=... -->` anchor as the first line of every segment block. `<id>.segmap.json` sidecar byte offsets match the rendered file exactly. Re-running on the same inputs is byte-identical (file diff = ∅, sidecar diff = ∅). Wiki-links present for every resolved mention. |
| `core/reindex.py` | qmd CLI invoked with the right args (`update --paths`, then `embed`). Subprocess non-zero exit → typed error and queue retry. Stderr streamed to structlog. |
| `search/qmd_client.py` | `mode=lexical` calls `qmd search` (no rerank, no expansion); `mode=hybrid` and `mode=semantic` call `qmd query`. Mock qmd HTTP responses → mapped to `CitationRow` correctly using sidecar binary search. Hit with no resolvable segment dropped (logged, not returned). Sidecar mtime change invalidates cache. qmd 5xx → typed error. qmd offline → typed error with retry hint. |
| `search/service.py` | `search_corpus("foo", mode="hybrid")` calls qmd `query`; `search_corpus("foo", mode="lexical")` calls qmd `search`; `find_mentions("musk")` runs SQL only, never calls qmd. Filters compose correctly. Pagination boundary cases. Lexical mode is never silently upgraded to hybrid. |
| `repositories/sqlite_entity_repository.py` | All CRUD on `entities`, `entity_mentions`. Co-occurrence rebuild produces canonical (a < b) ordering. Cascade deletes work. |
| `mcp/tools/*` | Each tool: input schema rejects malformed input; output schema validates against returned rows; PII never logged. |

Test framework conventions follow [04-testing.md](04-testing.md).

### Integration tests

Target: every pipeline transition + every cross-layer call exercised once
end-to-end.

| Test | Scope |
|---|---|
| `test_pipeline_extract_to_index.py` | Take a fixture cleaned transcript, run `clean → extract-entities → resolve-entities → write-corpus → reindex` end-to-end through the queue dispatcher (not by calling handlers directly). Assert `entity_mentions` rows exist with `resolution_status='resolved'`, the rendered Markdown + sidecar are present, and `qmd query` over HTTP returns the new content. |
| `test_queue_branch_fanout.py` | An episode reaching `clean=complete` enqueues **both** `summarize` and `extract-entities` (parallel branches). A forced failure in `extract-entities` does **not** mark `summarize` as failed and vice versa. `episodes.entity_extraction_status` reflects the entity-branch state independently of the summary stage. |
| `test_queue_stage_progression.py` | Within the entity branch, completion of `extract-entities` advances to `resolve-entities`, then `write-corpus`, `reindex`. Failure in any entity-branch stage routes to DLQ with a typed reason and does not contaminate the summarize branch. Stage labels appear in `/api/tasks` payloads. |
| `test_legacy_episode_skipped.py` | Episode with `clean_transcript_json_path = NULL` reaches `extract-entities`; handler emits zero mentions and sets `entity_extraction_status = 'skipped_legacy'`. Subsequent stages (`resolve-entities`, `write-corpus`, `reindex`) are no-ops for that episode. |
| `test_search_service_hybrid.py` | Spin up qmd against a 10-episode fixture corpus, run `search_corpus` in lexical/semantic/hybrid modes, assert each returns sensible top-3 with playable timestamps. |
| `test_qmd_hit_to_segment.py` | Given a fixed `<id>.md` + `<id>.segmap.json` and a qmd hit at byte offset X, `qmd_client` resolves to the correct `segment_id`/`start_ms`/`end_ms`. Hit at offset 0 (frontmatter) → dropped. |
| `test_entity_resolution_dedupe.py` | Two episodes mention "Musk" and "Elon Musk"; after resolution, exactly one `entities` row exists with both as aliases and both mentions point to it. |
| `test_unresolved_mentions_listable.py` | Insert a `pending` mention (no `entity_id`); `thestill list-mentions` and the `idx_mentions_pending` index find it. Resolution flips it to `resolved`. |
| `test_cooccurrence_rebuild.py` | Episode mentioning A and B once → `entity_cooccurrences(A,B).episode_count == 1`. Adding a second episode with both → 2. Removing an episode → decrements. |

### MCP / API contract tests

Target: every tool's input/output schema is locked behind a golden file.

- **Golden-file tests** for each MCP tool: a fixed query against the
  10-episode fixture corpus produces a fixed JSON response (modulo
  timestamps). Drift fails CI. Same pattern for REST endpoints.
- **Schema tests**: round-trip every tool's output through its declared
  schema; reject on extra/missing keys.
- **Latency budget tests** (CI gate): on the 10-episode fixture, separated
  by mode because they have different budgets:
  - `search_corpus(mode=lexical)` P50 < 30 ms, P95 < 100 ms — this is the
    ⌘K typing path; if it slows, typing feels broken.
  - `search_corpus(mode=hybrid)` P50 < 200 ms, P95 < 600 ms — full qmd
    pipeline is allowed to be slower.
  - `find_mentions` / `list_quotes_by` P50 < 20 ms, P95 < 80 ms (entity
    SQL is fast and cheap).
  Production budget on the full corpus is 3× these numbers (O2 win
  condition); enforced by a separate nightly perf job, not per-PR CI.

### UI tests

Playwright suite under `thestill/web/frontend/tests/`. Same conventions as
[09-single-user-web-ui.md](09-single-user-web-ui.md).

| Test | Flow |
|---|---|
| `cmdk-typeahead.spec.ts` | Open ⌘K, type "musk", see grouped results, arrow-down to a person hit, Enter, land on entity page. |
| `entity-page.spec.ts` | Visit `/entities/person/elon-musk`, verify timeline sparkline renders, "Notable quotes" populated, clicking a quote opens the floating player at the right timestamp. |
| `cooccurrence-chip.spec.ts` | On the SpaceX entity page, click the "Elon Musk" co-occurrence chip → land on Musk page. |
| `reader-wikilink-hover.spec.ts` | Open an episode, hover an entity link → hover card appears with summary + "Go to entity page" link. |
| `search-results-fallback.spec.ts` | ⌘K → "see all results" → results page renders three tabs, each populated. |
| `empty-states.spec.ts` | Entity with no mentions, search with no hits, episode with no extracted entities — each shows the designed empty state, not a blank screen. |

### End-to-end harness eval

The 10 reference questions built in **Phase 0.3** are the **acceptance
gate for O1 + O5**. They run twice in the lifecycle:

1. **At the end of Phase 1** against the SQL-only MCP alpha. Pass = O1
   validated (Claude can answer "what has X said about Y" with cited
   clips before qmd or any UI exists). If the alpha fails the gate,
   stop and fix the entity layer rather than building qmd on a shaky
   foundation.
2. **At the end of Phase 2** against the full surface (entity + qmd
   hybrid). Pass = O5 validated for narrative questions that span
   beyond a single entity.
3. **Nightly thereafter** against fixtures; regressions block release.

Each question is run via Claude Desktop pointed at our MCP server in a
clean session. Pass criteria for every run:

1. Claude calls at least one of our tools (no fabrication).
2. Every quoted phrase in the answer matches a `quote` field returned by a
   tool call in the same turn (verified by string match).
3. The answer cites at least 3 distinct episodes when the question is
   broad ("what has X said about Y over time").
4. No tool returns a 5xx; all errors are typed and explained in the
   response.

The 50 question/episode pairs (also from Phase 0.3) are run against
`search_corpus(mode=hybrid)` at the end of Phase 2 and gate top-5 recall
≥ 0.8 (O3).

### Manual verification before ship

- Build the corpus from one full real podcast (`Prof G Markets`, ~150
  episodes). Spot-check 20 random mention rows for correctness.
- Open the corpus in Obsidian; verify wiki-links navigate correctly and
  graph view shows sensible clusters. (Sanity check, not an enforcement
  gate.)
- Run the 10 reference questions interactively and read the answers.
  Subjective judgement but documented in the PR.

## Open questions

- **GLiNER licence + model card review.** Confirm the specific model we ship
  has a permissive licence compatible with our distribution.
- **Speaker attribution accuracy.** We rely on existing diarisation in
  cleaned transcripts. If `speaker` is `null` for >30% of mentions,
  `list_quotes_by` is unusable. Need a phase-1 measurement before phase 5.
- **qmd model auto-download UX.** First-run downloads ~2 GB of GGUFs.
  Acceptable for self-hosted; revisit when we ship hosted.
- **ReFinED memory footprint.** Loads several GB of Wikidata index. May
  need to run as a separate worker process, not in the same Python process
  as the API server. Decide during phase 2.

## Success metrics (post-ship, 30-day window)

- ≥ 80% of "person × topic" questions in the eval set produce an answer
  with ≥ 3 cited clips (O1).
- P50 query latency < 150 ms on the user's full corpus, P95 < 500 ms (O2).
- Top-5 semantic recall ≥ 0.8 on the labelled set (O3).
- Every person/company with ≥ 3 mentions has a populated entity page (O4).
- 10/10 harness reference questions pass the no-fabrication gate (O5).
