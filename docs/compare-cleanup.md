# Comparing Legacy vs Segmented Cleanup

`scripts/compare_cleanup.py` runs the cleanup pipeline on the last N
already-cleaned episodes with both the legacy and the segmented
(spec #18) paths active, then emits a metrics table so you can check
whether the new pipeline tracks the legacy one directionally and
whether the historical "gap at the beginning" bug is fixed.

## Typical workflow

```bash
# 1. Dry-run — just list which episodes would be touched
./venv/bin/python scripts/compare_cleanup.py --last 10 --dry-run

# 2. Analyse-only — compute metrics on whatever is already on disk
#    (useful for iterating on the metric code; no LLM calls)
./venv/bin/python scripts/compare_cleanup.py --last 10 --analyse-only

# 3. Full run — snapshot baselines, re-clean every selected episode
#    with THESTILL_CLEANUP_PIPELINE=segmented THESTILL_LEGACY_CLEANUP_SHADOW=1,
#    then measure. This hits the LLM; budget accordingly.
./venv/bin/python scripts/compare_cleanup.py --last 10

# Save the numeric report for comparison across tuning runs
./venv/bin/python scripts/compare_cleanup.py --last 10 \
    --save-json reports/cleanup_compare_$(date +%Y%m%d).json
```

The script runs against whichever LLM provider your `.env` is configured
for — it reuses `create_llm_provider_from_config`.

## What each metric means

For each episode the report shows three rows — `baseline` (pre-existing
cleaned MD, snapshotted before this script ran), `shadow-lgc` (fresh
legacy re-run produced during this script), and `segmented` (new
pipeline) — across these columns:

| column   | meaning                                                                         |
|----------|---------------------------------------------------------------------------------|
| first_ts | earliest `[HH:MM:SS]` or `[MM:SS]` marker in the cleaned text                   |
| wc       | word count of the cleaned output                                                |
| cov      | `wc / raw_word_total` — how much of the spoken content survives cleaning        |
| ent      | fraction of known entities (hosts, guests, sponsors, keywords) found in output  |
| ads      | count of `[AD BREAK]` markers                                                   |
| ratio    | `cleaned_chars / formatted_raw_chars`                                           |

Keep in mind: **`shadow-lgc` vs `baseline`** tells you the LLM's
run-to-run noise floor (prompt unchanged, re-run on the same input).
**`segmented` vs `shadow-lgc`** is the real signal — differences beyond
the noise floor attributable to the new pipeline.

## Success criteria

The segmented pipeline is directionally healthy when:

- `first_ts` is strictly **better or equal** to `shadow-lgc` on every
  episode. If baseline's `first_ts` is large (say > 300s), segmented's
  dropping near 0 is a direct confirmation of the gap-bug fix.
- `cov` is within ±10% of `shadow-lgc`. Consistently lower means the
  new prompt is over-aggressively dropping content; consistently higher
  means it's padding / hallucinating.
- `ent` is within ±5% of `shadow-lgc`. A large entity-recall drop
  suggests the per-batch context window (`k_prev` / `k_next`) is too
  narrow — entities referenced far from their introduction get lost.
- `ads` is within ±1 of `shadow-lgc` on most episodes. Frequent
  over-marking (segmented >> legacy) points at an over-triggered ad
  detector in the prompt.
- `ratio` lands in `[0.7, 1.0]` for all three methods. Segmented much
  lower than shadow means content drop; much higher means hallucination.

## Files produced per episode

After a full run, each episode's directory holds:

```text
data/clean_transcripts/{podcast_slug}/
├── {file}_cleaned.md                       # new segmented primary
├── {file}_cleaned.json                     # new AnnotatedTranscript sidecar
├── {file}_cleaned.baseline.md              # historical baseline (snapshot)
└── debug/
    └── {file}_cleaned.shadow_legacy.md     # fresh legacy re-run (control)
```

The baseline snapshot is idempotent — re-running the script does not
overwrite it. If you want to reset, delete the `.baseline.md` files
manually.

## Scope and limits

This script is a pre-Phase-D smoke test. It:

- does **not** persist the JSON sidecar into the DB (Phase D does that);
- does **not** expose a UI for side-by-side reading (Phase D does that);
- does **not** iterate over all episodes in bulk (use `thestill
  clean-transcript --force --max-episodes N` for that flow once you are
  happy with the per-episode behaviour).

Related: [spec #18](../specs/18-segment-preserving-transcript-cleaning.md).
