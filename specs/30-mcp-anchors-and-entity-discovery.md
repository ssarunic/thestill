# MCP — Anchor Queries and Entity Discovery

**Status**: 📝 Draft
**Created**: 2026-05-06
**Updated**: 2026-05-06
**Priority**: Medium (closes the gap between the web entity surface and the MCP/LLM-harness surface introduced by spec #28)
**Builds on**: [28-corpus-search-and-entities.md](28-corpus-search-and-entities.md)

## Overview

Spec #28 framed the MCP surface as the hero use case: "if the MCP surface
is right, the web UI is just another consumer." Phases 1–5 of #28 shipped
the entity index, the corpus search, the command bar, and the entity page
— but the MCP surface stopped at the Phase 1 shape (`find_mentions`,
`list_quotes_by`, `get_entity`, `list_episodes_by_entity`, `get_episode_clip`,
`search_corpus`). The new entity-anchor data added in PRs #60/#61 (hosts /
recurring / guest stored on `podcasts.host_entity_ids` /
`podcasts.recurring_entity_ids` / `episodes.guest_entity_ids`) and the
role-boosted prefix lookup (`search_entities_by_prefix`) are reachable
from the web but not from MCP.

Concretely: today an LLM agent cannot answer "who hosts Prof G Markets?",
"what shows is Sarah a guest on?", or "find all entities matching 'mrk'"
through the MCP surface. The data exists; the tools don't.

This spec adds **seven thin read-only MCP tools** that wrap repository
methods that already exist or are already proven by the web layer. No
new query logic, no new tables. The work is mostly mechanical wiring:
each tool is a 5–10 line wrapper over an existing repo method, with a
small JSON-Schema input descriptor and a citation/ref-shaped output.

## Customer outcomes

Anchored on the same five outcomes from spec #28. This spec deepens
**O1** ("find what someone said about a thing") and **O5** ("ask Claude
a question and get a narrative answer with sources") by giving the
harness new starting points it currently lacks.

### O1 deepening — anchor queries

Today the harness can answer _"what has Galloway said about data centres?"_
once it already knows Galloway is `person:scott-galloway`. It cannot
answer _"who hosts Prof G Markets?"_ at all, and the natural follow-up
_"what other shows is he a guest on?"_ requires the harness to scan
mentions across the entire corpus and infer a role from speaker labels
— inferring data that is already explicit in the database.

**Concrete win:** for any "who hosts X" / "what shows feature Y as a
guest" question, one MCP tool call returns the answer with `episode_count`
attached. No mention-scanning, no inference. Verified by 10 hand-built
question/answer pairs added to the eval set.

### O5 deepening — entity discovery as a starting point

Today an agent that hasn't been given an entity id has no way to find
one through MCP. It can call `search_corpus` and get citations, then
hope an entity id appears in the citation envelope, but there's no
"give me the entity rows that match this string" tool. The web has it
(`⌘K` typeahead, role-boosted), powered by `search_entities_by_prefix`.
MCP doesn't.

This is the single biggest blocker to "Claude composes a narrative
answer" working without the user pre-pasting entity ids. The harness
needs a search-by-name primitive as much as the human does.

**Concrete win:** an agent given a free-form name (e.g., _"Mrk"_,
_"Galloway"_, _"Prof G"_) can resolve it to a canonical entity id in
one MCP call, with the role-boost ranking entities the user is most
likely to mean to the top.

## Scope

### In scope (this spec)

Seven new MCP tools, one updated tool, all read-only:

| Tool | Wraps | Purpose |
|------|-------|---------|
| `search_entities` | `SqliteEntityRepository.search_entities_by_prefix` | Prefix lookup with role-boost. The harness's primary discovery primitive. |
| `list_entities_by_type` | `SqliteEntityRepository.list_entities_by_type` | Type-filtered enumeration; used to populate "all companies in the corpus" views. |
| `get_podcast_anchors` | `SqliteEntityRepository.get_podcast_anchors` | Hosts + recurring entities for one podcast. |
| `get_episode_anchors` | `SqliteEntityRepository.get_episode_anchors` | Guest entities for one episode. |
| `list_podcasts_for_entity` | New repo helper (5 lines, JSON-each over `host_entity_ids` / `recurring_entity_ids`) | Reverse: "which podcasts is this entity a host of (or recurring on)?" Optional `role` filter. |
| `list_episodes_for_entity` | New repo helper (5 lines, JSON-each over `episodes.guest_entity_ids`) | Reverse: "which episodes is this entity a guest on?" |
| `get_entity` (extend) | `SqliteEntityRepository.get_entity_summary` | Add the three role fields that PR #60 already returns from the repo but the MCP tool currently strips. |

The two new repo helpers are subsets of `get_entity_roles` (PR #60),
just split apart so callers asking only the podcast-side or only the
episode-side don't pay for the other.

### Out of scope (deferred to follow-on specs)

- **`get_episode_entities`** — the per-episode entity rail data (top-N
  entities + per-entity mention list, grouped). Equivalent of the
  `GET /api/episodes/{id}/entities` REST endpoint. Useful for an
  agent reading an episode, but the existing `find_mentions(episode_id=...)`
  covers the same data with a less-friendly shape. Add when an actual
  agent use case demands it.
- **Entity write tools** (set anchors, merge entities, override mentions,
  annotate). Footgun risk without HITL gating; spec #28 §"MCP-first"
  explicitly says "the MCP server has no write tools." Hold the line.
- **Co-occurrence graph queries** — the `entity_cooccurrences` table is
  rich but agents rarely ask graph-shape questions. `get_entity` already
  surfaces `cooccurring`. Skip until evidence demands it.
- **`entity://` resources** — read-only resource URIs are MCP-idiomatic
  but redundant with the tool surface. Tools are good enough.
- **CLI / REST mirrors** — every MCP tool in spec #28 has a CLI peer.
  Maintain that pattern: this spec adds CLI peers (`thestill entity
  search`, `thestill entity list-by-type`, `thestill entity anchors-of`,
  etc.) so the surfaces stay symmetric. REST is not added because the
  web frontend uses `search_entities_by_prefix` directly via the
  existing `/api/search/quick` endpoint and uses the existing
  `/api/entities/{type}/{slug}` endpoint for the page render — no new
  REST surface is needed.

## Current state

### What's already on disk

- `SqliteEntityRepository.search_entities_by_prefix` —
  [sqlite_entity_repository.py:684](../thestill/repositories/sqlite_entity_repository.py#L684) — role-boosted, used by `/api/search/quick`. Returns
  `EntityHit` rows with `id`, `type`, `canonical_name`, `matched_alias`,
  `mention_count`, `role`, `role_episode_count`.
- `SqliteEntityRepository.list_entities_by_type` —
  [sqlite_entity_repository.py:174](../thestill/repositories/sqlite_entity_repository.py#L174) — used by `entity_page_writer`. Returns full
  `EntityRecord` rows (description + timestamps included).
- `SqliteEntityRepository.get_podcast_anchors` —
  [sqlite_entity_repository.py:877](../thestill/repositories/sqlite_entity_repository.py#L877) — used by `/api/episodes/{id}/entities` to
  derive `speaker_kind`.
- `SqliteEntityRepository.get_episode_anchors` —
  [sqlite_entity_repository.py:891](../thestill/repositories/sqlite_entity_repository.py#L891) — used by the same route.
- `SqliteEntityRepository.get_entity_roles` —
  [sqlite_entity_repository.py:583](../thestill/repositories/sqlite_entity_repository.py#L583) — already returns the per-entity
  `hosts_podcasts` / `recurring_podcasts` / `guest_episodes`. The
  forward-lookup tools (`list_podcasts_for_entity`, `list_episodes_for_entity`)
  can either decompose this method or get their own thin helpers.
- `get_entity_summary` —
  [sqlite_entity_repository.py:520](../thestill/repositories/sqlite_entity_repository.py#L520) — already returns all three role
  fields; `_handle_get_entity` in MCP just doesn't pluck them.

### What MCP exposes today

All in [thestill/mcp/entity_tools.py](../thestill/mcp/entity_tools.py):

- `find_mentions` — citation rows for an entity / type / podcast / role.
- `list_quotes_by` — citation rows by speaker substring.
- `get_episode_clip` — single mention at a timestamp (used by the audio
  player).
- `get_entity` — summary (entity / mention_count / cooccurring /
  recent_mentions). **Drops the three role fields.**
- `list_episodes_by_entity` — episodes containing all of a list of
  entity names.

Plus `search_corpus` in [search_tools.py](../thestill/mcp/search_tools.py).

The surface is mention-shaped: every tool returns citation rows or
episode rows. None of the new tools in this spec return citations —
they return entity refs and podcast/episode refs. That's the central
shape change.

## Proposed MCP surface

JSON Schema described informally; implementation follows the existing
`entity_tools.py` patterns (Tool descriptor + `_handle_*` function +
dispatch in `call_tool`).

### `search_entities`

```text
search_entities(prefix: string,
                types?: ("person"|"company"|"product"|"topic")[],
                limit_per_type?: int = 5)
  → EntityHit[]

EntityHit = {
  id: string,                          // "person:elon-musk"
  type: "person"|"company"|"product"|"topic",
  canonical_name: string,
  matched_alias: string | null,        // when prefix matched an alias
  mention_count: int,
  role: "host"|"guest"|"recurring" | null,
  role_episode_count: int,
}
```

Order: role-boost desc, then `mention_count` desc, then name length
asc. Same ranking the web typeahead uses; do not re-tune for MCP.

### `list_entities_by_type`

```text
list_entities_by_type(type: "person"|"company"|"product"|"topic",
                      limit?: int = 100)
  → EntityRef[]
```

Bare `EntityRef` (id + type + canonical_name + wikidata_qid). Strips
description and timestamps to keep the payload small for the harness.
A `get_entity` follow-up call surfaces the rest.

### `get_podcast_anchors`

```text
get_podcast_anchors(podcast_id: string)
  → { hosts: EntityRef[], recurring: EntityRef[] }
```

Resolves entity ids to `EntityRef` shape (so the harness gets names
back, not raw ids). Stable order: hosts in `host_entity_ids` order,
recurring alphabetised.

### `get_episode_anchors`

```text
get_episode_anchors(episode_id: string)
  → { guests: EntityRef[] }
```

Same resolution behaviour. Note: the repo's `get_episode_anchors`
currently returns the union of host + recurring + guest ids — used by
the extractor's anchor-injection pass. The MCP tool intentionally
narrows to **guests only** because that's the question an agent asks
("who's the guest?"). Hosts are answered by `get_podcast_anchors` on
the parent podcast.

### `list_podcasts_for_entity`

```text
list_podcasts_for_entity(entity_id: string,
                         role?: "host"|"recurring")
  → { podcast_id, podcast_slug, podcast_title,
      role: "host"|"recurring",
      episode_count: int }[]
```

Without `role`, returns all matches (an entity can be both a host of
one show and recurring on another). Backed by a JSON-each scan over
`host_entity_ids` and `recurring_entity_ids`. The web entity page
already renders these two lists; this just re-shapes the same data
for tool callers.

### `list_episodes_for_entity`

```text
list_episodes_for_entity(entity_id: string,
                         podcast_id?: string,
                         date_range?: { from?: ISO, to?: ISO },
                         limit?: int = 50)
  → { episode_id, episode_slug, episode_title,
      podcast_id, podcast_slug, podcast_title,
      published_at: ISO | null }[]
```

Episodes where the entity is a guest. Sorted newest-first. Filterable
by podcast or date range — common harness narrowings ("Sarah's guest
appearances on Lex Fridman last quarter").

### `get_entity` (updated)

Add three fields to the existing tool's response:

```text
{
  entity, mention_count, cooccurring, recent_mentions,    // existing
  hosts_podcasts: HostedPodcastRef[],                     // new
  recurring_podcasts: HostedPodcastRef[],                 // new
  guest_episodes: GuestEpisodeRef[],                      // new
}
```

`HostedPodcastRef` and `GuestEpisodeRef` are the same shapes the web
uses (defined in PR #61's `api_entities.py`). The repo already returns
them via `get_entity_summary`; this is a one-line `_handle_get_entity`
fix to stop dropping them.

### Tool-naming rule recap

Spec #28 §"Tool-naming rule" — names describe **intent**, not schema.
The naming choices above follow that: an agent reading the tool list
should pick the right tool from the name alone.

- `search_entities` (not `query_entities` / `lookup_entity`) — matches
  the harness's mental model ("I'm searching for an entity by name").
- `get_podcast_anchors` (not `list_hosts`) — anchors is the spec
  vocabulary; lists what the podcast _has_, not what the entity _is_.
- `list_podcasts_for_entity` (not `find_hosted_podcasts`) — flips the
  question from podcast to entity, and `_for_` keeps it parallel with
  `list_episodes_for_entity`.

## Implementation phases

Two phases. Phase 1 lands in one PR; Phase 2 is the docs/cleanup pass.

### Phase 1 — Tools, tests, CLI peers (1–2 days)

**1.1 Repo helpers.** Add two thin methods to `SqliteEntityRepository`:

- `list_podcasts_for_entity(entity_id, role=None)` — `json_each` scan
  over `host_entity_ids` and (optionally) `recurring_entity_ids`.
- `list_episodes_for_entity(entity_id, podcast_id=None, date_range=None, limit=50)` —
  `json_each` scan over `episodes.guest_entity_ids`, with the usual
  podcast / date filters.

These can decompose from `get_entity_roles`'s SQL — extract the host
and guest queries into individual methods with the new optional
filters, then have `get_entity_roles` delegate to them. Net: same
shape `get_entity_roles` returns today, plus two leaner siblings.

Tests in
[tests/unit/repositories/test_sqlite_entity_repository_queries.py](../tests/unit/repositories/test_sqlite_entity_repository_queries.py)
mirror the `TestGetEntityRoles` class style — fixture seeds an entity
in different anchor positions, asserts the new methods return what
they should and respect the role / podcast / date filters.

**1.2 MCP tools.** Add the seven tool descriptors + handlers in
[thestill/mcp/entity_tools.py](../thestill/mcp/entity_tools.py).
Order matters for the LLM tool list: anchor reads first, discovery
second. Update `_handle_get_entity` to pluck the three role fields
from `get_entity_summary`.

**1.3 CLI peers** in [thestill/cli.py](../thestill/cli.py) under the
existing `thestill entity` command group:

```text
thestill entity search "mrk" --type person --limit-per-type 5
thestill entity list-by-type person --limit 50
thestill entity anchors-of-podcast <podcast-slug>
thestill entity anchors-of-episode <podcast-slug>/<episode-slug>
thestill entity podcasts-for <entity-id> [--role host|recurring]
thestill entity episodes-for <entity-id> [--podcast <slug>] [--since 90d]
```

Each is a 10–20 line click command, output is a tabulated text format
(reuse the patterns from `thestill entity get`).

**1.4 Integration tests.** Add a `tests/integration/mcp/test_entity_mcp_tools.py`
file that exercises each new tool through the MCP server's
`call_tool` dispatch — same pattern the existing entity tools use.
Eight tools (seven new + the updated `get_entity`), eight tests
minimum.

**Acceptance**: `pytest tests/unit/ tests/integration/` clean, all
seven tools callable from `claude mcp call`, CLI peers smoke-pass
on the local corpus.

### Phase 2 — Eval pass + docs (~half a day)

**2.1 Eval add.** Add 10 question/answer pairs to the spec #28 eval
suite that are unanswerable today and answerable after Phase 1. Five
target O1 deepening (anchor queries), five target O5 deepening
(discovery). Run before/after to confirm `find_mentions` recall
isn't regressed (it shouldn't be — the new tools are additive, not
replacements).

**2.2 Docs.**

- Update [docs/mcp-usage.md](../docs/mcp-usage.md) with the seven new
  tools and their typical usage patterns.
- Cross-link from spec #28 §"MCP surface" to this spec.
- Update this spec's status header to ✅ Complete with the merge
  date.

**Acceptance**: docs merged, eval pass shows ≥ 8/10 of the new
question pairs answered correctly with the new tools.

## Test plan

### Unit (per repo helper)

- `list_podcasts_for_entity(entity_id)` returns hosts + recurring
  entries; with `role="host"` returns only hosts; with `role="recurring"`
  returns only recurring; for an entity that isn't anchored anywhere,
  returns `[]`.
- `list_episodes_for_entity(entity_id)` returns guest episodes
  newest-first; respects `podcast_id` and `date_range` filters; for
  an entity with no guest appearances returns `[]`.
- `get_entity_roles` continues to return the same shape as before
  (backwards-compatibility check; the refactor extracts methods but
  doesn't change the aggregate shape).

### Integration (per MCP tool)

For each of the seven tools:

1. Happy path — seed corpus, call tool, assert shape and content.
2. Empty result — call against an entity / podcast / episode with no
   matching data, assert `[]` not error.
3. Error path — bogus entity id / podcast id, assert structured
   `error` envelope, not exception.
4. (`search_entities` only) — alias match, role-boost ordering.
5. (`get_entity` only) — assert the three new fields are present and
   non-null when anchors exist.

### Manual smoke

- `claude mcp call search_entities '{"prefix":"mrk"}'` → finds Nikola
  Mrkšić, role=host.
- `claude mcp call get_podcast_anchors '{"podcast_id":"<deep-learning-with-polyai>"}'`
  → returns him.
- `claude mcp call list_podcasts_for_entity '{"entity_id":"person:nikola-mrksic"}'`
  → returns the same podcast with episode_count=10.

## Open questions

1. **Should `list_entities_by_type` paginate?** Today the repo method
   returns every row of the type. For `person` in a 10k-episode corpus
   this could be 5k+ entities. Two options: (a) cap in the tool with
   a `limit` default of 100 (matches `find_mentions`); (b) introduce
   `offset` for pagination. Recommendation: (a) — limit of 100, plus
   ranked by `mention_count` desc so the truncation lops off
   long-tail entities the harness wouldn't have used anyway. Add
   `offset` only if a real use case appears.

2. **`list_podcasts_for_entity` without a `role` filter — return both
   roles in one list, or split?** Two shapes: (a) flat list with
   `role` discriminator on each row; (b) `{ host: [...], recurring: [...] }`
   shape (matches `get_podcast_anchors`). Recommendation: (a) — flat
   with discriminator, because the agent often wants "any podcast
   this entity is associated with" and bucketing forces them to merge
   the lists themselves.

3. **Should `get_entity` _always_ return the three role fields, or
   only when non-empty?** Either is fine for the wire format; the JSON
   schema can declare them required. Recommendation: always present,
   empty arrays when absent. Removes a "key may be missing" branch
   from the harness.

## Risks and notes

- **Risk: payload size on hot entities.** A super-host (e.g., Joe
  Rogan-style) could have hundreds of guest episodes. The
  `guest_episodes_limit` in `get_entity_roles` is 50 today; keep it
  for the MCP `get_entity` and let `list_episodes_for_entity` carry
  the unbounded form (with its own `limit`). Don't promise a full
  list from `get_entity`.
- **Note: the seven tools push the `entity_tools.py` file past 600
  lines.** Worth splitting into `entity_tools.py` (existing) and
  `entity_tools_anchors.py` (new). Decide during implementation; it's
  a layout change, not a contract change.
- **Note: forward-compat with future role types.** Today the role
  enum is `host` / `guest` / `recurring`. If a fourth type lands
  (e.g., `co-host`, `producer`), every tool that surfaces the role
  needs to be checked. The string enum is fine; the `EntityHit.role`
  field shape doesn't constrain it. Future-roles are additive.
