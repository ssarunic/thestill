# Metadata Speaker Priors — hosts & guests before transcription

> **Status:** 📝 Draft (2026-07-29)
> **Created:** 2026-07-29
> **Author:** Engineering
> **Related:** [#42 robustness](42-robustness-and-failure-mode-hardening.md) (FM-1 errors-as-empty-results, FM-5 consistent-mock tests, FM-7 unsanitized-LLM-output all apply here), [#48 refresh-feed-stage](48-refresh-feed-stage.md) (refresh path where the deterministic tier hooks in), [#45 entity-page-enrichment](45-entity-page-enrichment.md) (existing Wikidata/Wikipedia clients reused in Phase 3)

---

## Executive Summary

Today hosts and guests are discovered **after transcription**:
`FactsExtractor.extract_episode_facts`
([facts_extractor.py:146](../thestill/core/facts_extractor.py#L146)) requires the
raw transcript, and even the metadata-only
`extract_initial_podcast_facts` is only invoked from the cleaning stage
([transcript_cleaning_processor.py:267](../thestill/core/transcript_cleaning_processor.py#L267)).
Until an episode reaches `CLEAN_TRANSCRIPT`, we know nothing about who is in it —
even though the feed usually tells us.

This spec adds a **speaker-priors pass** that extracts candidate hosts and guests
from metadata alone, before any audio is processed. Priors are exactly that —
*priors*, never ground truth:

- The inbox/web UI can show "with Jane Doe" while an episode is still queued.
- The post-transcription facts pass starts from candidates ("expected
  participants: …") instead of guessing from scratch, improving phase-2 speaker
  identification, which already consumes `podcast_facts.hosts` /
  `episode_facts.guests`
  ([segmented_transcript_cleaner.py:437-456](../thestill/core/segmented_transcript_cleaner.py#L437-L456)).
- (Later) diarization gets an expected-speaker-count hint where the provider
  supports it.

## Sources, tiered by reliability

| Tier | Source | Level | Reliability | Cost |
|------|--------|-------|-------------|------|
| A | Podcasting 2.0 `<podcast:person role="host\|guest">` | feed + item | Near-authoritative when present; low adoption (indie hosts) | Free, deterministic |
| B | `itunes:author` (already stored as [`Podcast.author`](../thestill/models/podcast.py#L361)) | feed | ~50/50 host name vs. network name — never seeds on its own; context input to Tier C only (see §2) | Free, already ingested |
| C | LLM pass over episode `title` + `description` (+ podcast title/description) | item | High precision for interview shows; recall format-dependent (panel/news shows list nobody); metadata can lie (cancelled guest, clip-only "featuring") | One cheap LLM call per new episode |
| D | Wikidata/Wikipedia (existing [wikidata_client.py](../thestill/core/wikidata_client.py) / [wikipedia_client.py](../thestill/core/wikipedia_client.py)) | feed | Excellent for hosts of notable shows; useless for per-episode guests | Free API calls, cacheable |

Hard limit, stated up front: metadata yields **names, not speaker labels**.
Mapping to diarized `SPEAKER_XX` still requires the transcript. The
post-transcription facts pass remains authoritative; this spec only changes its
starting point from "blank" to "confirm/reject these candidates."

## Design

### 1. Deterministic tier at refresh (free)

Parse `<podcast:person>` during feed refresh, alongside the existing metadata
extraction ([feed_manager.py:233](../thestill/core/feed_manager.py#L233)).

- feedparser 6.x does not map the `podcast:` namespace reliably — supplement
  with a targeted parse of the raw response body the refresh path already holds.
  (Verify against feedparser's namespace table during implementation; if it does
  expose `podcast_person`, drop the supplementary parse.)
- Feed-level persons with `role="host"` → podcast-level host priors.
  Item-level persons → episode-level guest/host priors.
- No schema change to `Podcast`/`Episode`: results flow into the facts layer
  (below), not new DB columns.

### 2. LLM tier at pipeline entry (paid, best-effort)

A new `MetadataPriorsExtractor` (sibling of `FactsExtractor`,
same `LLMProvider` plumbing) runs a single structured-output call over podcast
title/description + episode title/description + `Podcast.author`. Output:
`hosts`, `guests`, each `"Name - Role/Description"` to match the existing facts
string format.

`itunes:author` is **context, not a seed**: there is no deterministic way to
tell a person name from a company name ("Malcolm Gladwell" vs. "Pushkin
Industries" have the same shape), so the LLM call — which already holds the
podcast metadata — judges whether `author` looks like a person and whether it
corroborates a host candidate, at zero extra cost and inside a
provenance-marked pass. In degraded mode (no LLM key configured), `author`
seeds nothing: a blank host field beats a plausible-looking wrong one
("Host: Wondery"), and degradation-to-absence-never-misinformation is the #42
posture this spec holds throughout.

**Placement:** invoked from the `DOWNLOAD` task handler after the audio is
persisted — not at refresh. Rationale:

- Refresh stays LLM-free (no new latency/dependency in the discovery path).
- `DOWNLOAD` precedes `TRANSCRIBE`, so priors exist before any diarization hint
  could be consumed, and long before cleaning.
- The queue's retry machinery covers transient LLM failures without a new stage
  (a dedicated `EXTRACT_PRIORS` stage is overkill for one cheap call — revisit
  only if cost/latency ever argues for independent scheduling).

**Failure posture (#42):** priors extraction is strictly best-effort. An LLM
error must not fail the `DOWNLOAD` task. Per FM-1, a failed extraction is logged
as its own event (`priors_extraction_failed`) and produces *no* facts file — it
is never conflated with "extraction succeeded, found nobody" (which writes an
empty-guests skeleton). Per FM-7, LLM output is sanitized (control bytes,
length caps) before persistence.

### 3. Storage: facts skeletons with provenance

Reuse `FactsManager` files — no new store.

- **Episode:** the priors pass writes an `EpisodeFacts` *skeleton*: `guests`
  populated, `speaker_mapping` empty, plus a new field
  `provenance: "metadata" | "transcript" | "user"` (default `"transcript"` for
  existing files so nothing changes on upgrade).
- **Podcast:** promote `extract_initial_podcast_facts` (already metadata-only)
  to run at podcast-add time; merge in Tier-A feed-level hosts.
- **Merge rules** (highest wins): user edit > transcript-confirmed >
  `podcast:person` > LLM-metadata. (`itunes:author` has no tier of its own —
  it only enters via the LLM pass, per §2.)
  - The priors pass never overwrites an existing facts file (user-editable
    files stay sacred — unchanged from today's contract).
  - The post-transcription pass loads the skeleton, feeds priors into its
    prompt as "expected participants (unconfirmed, from metadata — confirm or
    reject against the transcript)", and its output replaces the skeleton with
    `provenance: "transcript"`.

### 4. Consumption

- **Cleaner (phase 2):** already renders hosts/guests into its prompt; when
  `provenance == "metadata"` the rendering appends "(unconfirmed, from episode
  metadata)" so the model treats them as candidates, not facts.
- **Web UI:** episode cards/inbox display metadata-provenance guests with one
  **global** subtle "expected" treatment (e.g. "with Jane Doe · expected") —
  no per-user visibility rules. Priors derive from feed metadata, which is
  podcast-scoped and identical for every user; per-user gating would add
  settings surface and query complexity for no informational difference. The
  badge silently upgrades to plain text when the transcript pass confirms a
  name, and a rejected name (cancelled guest) disappears rather than lingers —
  both fall out of the merge rules for free, since the transcript-provenance
  file replaces the skeleton. Optional; not a blocker for the pipeline work.
- **Diarization hints (Phase 4):** pass expected speaker count
  (`len(hosts) + len(guests)`) to providers that accept it (Google diarization
  min/max speaker count; ElevenLabs `num_speakers` — verify per provider).
  Count hints only; never pass names as truth.

## Phasing

| Phase | Scope | Depends on |
|-------|-------|------------|
| P1 | Deterministic tier: `podcast:person` parse at refresh, podcast-add-time initial facts, `provenance` field + merge rules | — |
| P2 | `MetadataPriorsExtractor` LLM pass in `DOWNLOAD` handler; cleaner "unconfirmed" annotation | P1 |
| P3 | Wikidata/Wikipedia host enrichment for notable shows (cached, podcast-level only) | P1 |
| P4 | Diarization speaker-count hints per provider | P2 |

P1+P2 deliver the core value; P3/P4 are independent add-ons.

## Testing

- Feed fixtures with and without `<podcast:person>` (feed-level, item-level,
  malformed roles).
- Per FM-5, LLM mocks must vary: guests found / none found / provider error /
  garbage output (control bytes, oversized strings) — asserting the distinct
  outcomes (skeleton with guests, empty skeleton, *no file* + failure event,
  sanitized persistence).
- Merge-rule matrix: user-edited file survives both passes; transcript pass
  replaces metadata skeleton; priors pass declines to write over any existing
  file.
- Title-pattern corpus for the LLM prompt eval (interview / panel / news
  formats) — candidate for a `thestill eval` rubric rather than unit tests.

## Resolved decisions

- **`itunes:author` never seeds a host prior on its own.** It is context input
  to the Tier-C LLM pass, which classifies person-vs-org and corroboration in
  the same call (§2). Without an LLM key it seeds nothing — degrade to absence,
  not misinformation.
- **Unconfirmed-guest visibility is global**, one "expected" badge for all
  users; no per-user rules (§4). Revisit only if user feedback demands it.
