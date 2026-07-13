# EEA Top-Podcast Regions

> **Status:** 📝 Draft
> **Created:** 2026-07-13
> **Author:** Product & Engineering
> **Related:** [#28 corpus-search-and-entities](28-corpus-search-and-entities.md) (owns the Top Podcasts surface), [#42 robustness](42-robustness-and-failure-mode-hardening.md) (FM-6 parallel-path drift — the frontend region lists must not drift from the seeded data)

---

## Executive Summary

Top-podcast charts ship for two regions today — `us` and `gb`. This spec
expands coverage to the **entire EEA** (EU 27 + Iceland, Liechtenstein,
Norway), so a user anywhere in the single market sees a chart for their own
country rather than falling back to the US or UK.

The feature is region-generic: supported regions are **derived at runtime by
globbing `data/top_podcasts_<code>.json`** and seeding the `top_podcasts_meta`
table. There is no hardcoded `["us", "gb"]` allowlist. Adding a region is
**(1) drop a data file, (2) give it a frontend label + flag**.

One caveat surfaced during implementation: the glob-seeder existed **only in
the SQLite repository** ([sqlite_podcast_repository.py](../thestill/repositories/sqlite_podcast_repository.py)
`_seed_top_podcasts`). The Postgres repo — which backs the hosted server — had
no counterpart and only ever received chart rows via the one-shot
`db_promotion` migration, so on Postgres "drop a JSON file" did nothing. This
spec closes that FM-6 parallel-path gap by adding the same glob-import to the
Postgres repo (`PostgresPodcastRepository._seed_top_podcasts`, run once at
construction). No schema, API, or query-layer change beyond that seeder.

Liechtenstein (`li`) has no Apple storefront chart (its iTunes RSS returns zero
entries), so it is **excluded** — a region that can only ever be empty is worse
than absent. Net: **29 new region files**, GB retained (no longer EEA
post-Brexit, but a first-class region regardless).

---

## Motivation

- The resolved region drives the Top Podcasts page, the "Top in 🇩🇪 DE" badge in
  the Add Podcast modal, and the default chart a new user sees after IP-based
  region inference ([geoip.py](../thestill/utils/geoip.py)).
- IP inference already returns any ISO 3166-1 alpha-2 code, so an EEA user is
  *detected* correctly but then served a chart that doesn't exist for their
  country — silently degrading to the first seeded region (`us`). Expanding the
  data closes that gap.

---

## Scope — Region Set

EEA = EU 27 + Iceland, Liechtenstein, Norway. Codes are Apple storefront codes,
which are ISO 3166-1 alpha-2 lowercase and match the app's internal convention.

| Group | Codes |
|---|---|
| EU 27 | `at be bg hr cy cz dk ee fi fr de gr hu ie it lv lt lu mt nl pl pt ro sk si es se` |
| EEA non-EU | `is li no` |
| **Excluded** | `li` — no Apple chart (RSS returns 0 entries) |
| **Already present** | none of the EEA set; `us`/`gb` remain |

**29 new data files** generated: the EU 27 minus none, plus `is` and `no`.
(`gb` predates this spec and is not in the EEA set.)

Verified 2026-07-13 against `itunes.apple.com/<code>/rss/toppodcasts`: all 29
return full charts; `li` returns zero.

---

## Transcription-Language Coverage

Chart data is language-agnostic RSS metadata, so region coverage is independent
of the ASR backend. But a user who *follows* a show from a new region will have
it transcribed, so the region set is worth checking against the ASR's supported
languages.

The default provider is **Whisper** ([config.py](../thestill/utils/config.py)
`transcription_provider = "whisper"`), whose large-v3 model — and the ElevenLabs
`scribe_v1` alternative — cover all EEA languages. No gap there.

The **`parakeet`** provider (`nvidia/parakeet-tdt-0.6b-v3`) supports 25 European
languages: `bg hr cs da nl en et fi fr de el hu it lv lt mt pl pt ro sk sl es
sv ru uk` (verified against the HuggingFace model card, 2026-07-13). Against the
EEA regions this adds, the primary-language gaps are:

| Region | Primary chart language | Parakeet v3 |
|---|---|---|
| Iceland (`is`) | Icelandic | ❌ unsupported |
| Norway (`no`) | Norwegian | ❌ unsupported |
| Ireland (`ie`) | English (Irish `ga` also official) | ✅ EN; Irish-language shows ❌ |
| Luxembourg (`lu`) | Luxembourgish; FR/DE dominant | ✅ via FR/DE; Lëtzebuergesch ❌ |
| Cyprus (`cy`) | Greek | ✅ (Turkish-Cypriot content ❌) |
| all other 24 EEA | Maltese, Croatian, Greek, Baltics, … | ✅ |

Only **Iceland and Norway** have an unsupported *primary* language. This does
not block adding the regions — it is a note for any deployment that runs
`transcription_provider=parakeet`: Icelandic/Norwegian shows followed from those
charts should fall back to Whisper/ElevenLabs. (Unrelated cleanup surfaced while
checking: [parakeet_transcriber.py](../thestill/core/parakeet_transcriber.py)
line 42 still says "English-only", stale since the v3 multilingual bump.)

---

## Approach

### 1. Data generation

Run the existing builder once per region:

```bash
./venv/bin/python scripts/build_top_podcasts.py --region <code>
```

Full enrichment (Apple charts + iTunes Lookup for RSS/artwork/category +
YouTube channel search + RSS cadence/duration). Each file targets ~500 unique
podcasts, written to `data/top_podcasts_<code>.{json,csv}`. Regions are run
**sequentially** to stay under iTunes/YouTube rate limits.

Both repositories auto-discover each new JSON on next server init (mtime-gated
smart-refresh), insert its rows into `top_podcasts` / `top_podcast_rankings`,
and record the region in `top_podcasts_meta`. `available_regions` in the
`GET /api/top-podcasts` response — read live per request — then includes the
new codes.

**Deploy note:** the Postgres seeder needs the current schema (the
`top_podcasts.image_url` column from alembic `0005`). A DB behind on migrations
must be brought to head (`alembic upgrade head`) before the seeder can load
rows; it fails open and logs otherwise.

### 2. Frontend labels + flags

Two presentation-only lists, decoupled from the data layer, get the full EEA
set so the region shows a country name and flag rather than a bare code:

- [Settings.tsx](../thestill/web/frontend/src/pages/Settings.tsx) `REGIONS` —
  the manual region picker (`{ code, label }`).
- [regions.ts](../thestill/web/frontend/src/utils/regions.ts) `FLAG` — code →
  emoji flag, used by the Top Podcasts dropdown and the Add Podcast badge.

Both are kept in **the same EEA order** and cover identical codes (FM-6: the
two lists must not drift). `li` is omitted from both — it has no chart.

---

## Non-Goals

- No schema, migration, or API change — the region dimension already exists
  end to end. (The one repository change is the Postgres glob-seeder above,
  bringing it to parity with SQLite; no new tables or columns.)
- No hardcoded region allowlist introduced anywhere; discovery stays
  glob/`top_podcasts_meta`-driven so the next region is still just a dropped
  file.
- No automated/scheduled rebuild of the chart data — regeneration cadence is an
  ops concern, unchanged by this spec.
- No `li` support until Apple exposes a Liechtenstein storefront chart.
- No re-inference or migration of existing users' stored regions.

---

## Testing

- **Seeding:** after generation, boot against a scratch DB and assert
  `get_top_podcast_regions()` returns all 29 new codes plus `us`/`gb`, and that
  each region has a non-empty ranking set.
- **List parity:** a frontend unit check (or lint) that `REGIONS` codes and
  `FLAG` keys cover the same set, guarding FM-6 drift.
- **Empty-region guard:** confirm no `data/top_podcasts_li.json` is written and
  `li` appears in neither frontend list.

---

## Decision Log

| Date | Decision |
|------|----------|
| 2026-07-13 | Spec created. EEA coverage chosen over "EU only" per request. Full YouTube enrichment kept (vs. skipping it for speed). `li` excluded — no Apple chart. GB retained as a non-EEA first-class region. No new allowlist: discovery stays glob-driven. |
| 2026-07-13 | Verified Parakeet v3 language coverage: Icelandic and Norwegian unsupported (all other EEA primary languages covered). Regions kept — default Whisper/ElevenLabs cover the gap; note added for `parakeet` deployments. |
| 2026-07-13 | Implementation found the glob-seeder was SQLite-only; Postgres (hosted backend) never seeded from `data/*.json`. Added `PostgresPodcastRepository._seed_top_podcasts` for parity (FM-6). Also surfaced that the local Postgres was 4 migrations behind (`0001`→`0005`, missing `image_url`); documented `alembic upgrade head` as the deploy prerequisite. |
