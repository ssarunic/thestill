# Spec #28 evaluation fixtures

These JSON files are the **measurement sticks** for spec #28 (corpus
search & entities). They drive Phase 0.3, gate Phase 1 and Phase 2, and
run nightly thereafter.

## Files

| File | What it gates | When it runs |
|---|---|---|
| `harness_reference_questions.json` | O1 + O5 acceptance — "ask Claude a question, get a narrative answer with cited clips" | End of Phase 1 (against SQL-only MCP alpha), end of Phase 2 (against full hybrid surface), nightly |
| `semantic_recall_pairs.json` | O3 — top-5 semantic recall ≥ 0.8 over `search_corpus(mode=hybrid)` | End of Phase 2, nightly |

Each file's schema is documented in the file header. Counts target the
spec: 10 harness questions, 50 semantic-recall pairs.

## What "build" means

Phase 0.3 is **a manual exercise**. There is no script that generates
these — the value comes from real questions a real human (you) actually
wants the corpus to answer. Drafting them once and treating them as
golden inputs is the only way the eval gate has teeth.

When picking questions:

- **Anchor on real episodes you've heard.** A question whose ground-truth
  episode you can name from memory is much more useful than one
  generated from a list of episode titles.
- **Mix easy and hard.** A spread that hits "obvious lexical" (Q4 in
  the harness file) and "semantic-only, no shared keywords" (Q9) keeps
  the eval honest.
- **Include questions deferred features cannot answer.** Sentiment
  trends are deferred (D.1) — do not include a sentiment question in
  the v1 acceptance set, even if it would be interesting. The spec is
  explicit: v1 cannot depend on a deferred feature.

## How to run them

Phase 1 task 1.12: run the 10 harness questions against the MCP alpha
in Claude Desktop, score by the no-fabrication gate (every quoted
phrase matches a `quote` field returned in the same turn). Phase 2
task 2.9: run the 50 semantic pairs through `search_corpus(mode=hybrid)`
and assert top-5 recall ≥ 0.8.

The runner code lives under `tests/integration/` and lands in Phase 1.
