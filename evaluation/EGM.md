# evaluation: transcript-quality evaluation suite

## Overview

A standalone, resumable script that compares transcription and cleaning quality on 20 fixed
episodes. For each episode it does the following:

1. Transcribes with Dalston.
2. Transcribes with ElevenLabs `scribe_v1`, which serves as the reference.
3. Cleans the Dalston transcript in two ways:
   - "clean_d", the full-rewrite `TranscriptCleaner`;
   - "clean_b", the corrections-list approach from `scripts/cleaning_prototype.py`.
4. Computes WER, content retention, the first-timestamp check, entity accuracy and word
   timestamp alignment.

It is a developer tool. The pipeline, the web server and the tests do not import it.
`evaluation/outputs*/` are gitignored run data and are not part of the contract.

## Interface

- `evaluation/run.py:main` is the argparse CLI. Run it as `./venv/bin/python evaluation/run.py`.
  Its flags:
  - `--resume`: continue from `outputs/state.json`.
  - `--reset`: delete `outputs/` and start again.
  - `--episodes 0,1,4`: run only these episode indices.
  - `--skip-elevenlabs` and `--skip-dalston`: skip one transcription provider.
  - `--metrics-only`: recompute the metrics (step 5) only.
  - `--report-only`: rebuild `report.json` from the existing metrics files.
- `evaluation/run.py:STEPS` lists the five steps in order: `dalston_transcribe`,
  `elevenlabs_transcribe`, `clean_d`, `clean_b` and `metrics`.
- `evaluation/run.py:OUTPUT_DIR` is `evaluation/outputs`.
- `evaluation/run.py:EvalContext` builds the shared context:
  - `thestill.utils.config.load_config`;
  - `PathManager` on `data/`;
  - `FactsManager`;
  - `TranscriptFormatter`;
  - `thestill.core.llm_provider.create_llm_provider_from_config`.
- The steps run per episode and each returns a `StepState`:
  - `evaluation/run.py:step_dalston_transcribe` and `evaluation/run.py:step_elevenlabs_transcribe`;
  - `evaluation/run.py:step_clean_d` and `evaluation/run.py:step_clean_b`;
  - `evaluation/run.py:step_metrics`.

  `evaluation/run.py:generate_report` and `evaluation/run.py:print_summary_table` turn the
  results into the report and the printed table.
- The data models live in `evaluation/episodes.py`:
  - `evaluation/episodes.py:EpisodeSpec` describes one test episode.
  - `evaluation/episodes.py:StepState` records one step's status: `pending`, `done`, `failed`
    or `skipped`.
  - `evaluation/episodes.py:EpisodeState` holds the state of all five steps.
- `evaluation/episodes.py:TEST_EPISODES` is the fixed list of 20 episodes, labelled PROB-1..5,
  NORM-1..11, SHORT-1 and MULTI-1..3.
- Helpers in `evaluation/episodes.py`:
  - `evaluation/episodes.py:find_audio_file` matches
    `data/downsampled_audio/<podcast>/<episode>*.wav`.
  - `evaluation/episodes.py:find_existing_transcript` matches
    `data/raw_transcripts/<podcast>/<episode>*_transcript.json`, filtered on the
    `model_used` prefix.
- Metric functions in `evaluation/metrics.py`:
  - `evaluation/metrics.py:normalize_text`;
  - `evaluation/metrics.py:compute_wer` (uses `jiwer`, with an edit-distance fallback in
    `_word_error_rate`);
  - `evaluation/metrics.py:compute_content_retention`;
  - `evaluation/metrics.py:check_first_timestamp` (5 s tolerance);
  - `evaluation/metrics.py:extract_entity_vocab` and `evaluation/metrics.py:compute_entity_accuracy`;
  - `evaluation/metrics.py:compute_timestamp_alignment`;
  - `evaluation/metrics.py:extract_words_from_transcript`;
  - `evaluation/metrics.py:aggregate_episode_metrics`.

## Invariants

- Resuming and state:
  - `evaluation/run.py:run_evaluation` exits 1 if `outputs/state.json` exists and none of
    `--resume`, `--reset`, `--metrics-only` or `--report-only` is given.
  - A step runs again only if its status is not `done`.
  - The script saves the state after every step.
- Dependencies between steps:
  - In `evaluation/run.py:run_evaluation`, `clean_d` and `clean_b` are marked `skipped` when
    `dalston_transcribe` is not `done`.
  - `--skip-dalston` and `--skip-elevenlabs` change a step only while it is `pending`
    (`evaluation/run.py:530-532`, `548-550`). On `--resume` with `dalston_transcribe`
    already `done`, `--skip-dalston` changes nothing and `clean_d` / `clean_b` still run.
- Reusing transcripts:
  - `evaluation/run.py:step_dalston_transcribe` reuses an existing raw transcript whose
    `model_used` starts with `dalston`.
  - `evaluation/run.py:step_elevenlabs_transcribe` reuses one whose `model_used` starts with
    `scribe_v`.
  - The script calls a provider only when there is no transcript to reuse.
- Failure handling:
  - Errors inside each step's `try` are caught and recorded as `failed`.
  - Not every step error is caught. In `evaluation/run.py:step_dalston_transcribe` and
    `evaluation/run.py:step_elevenlabs_transcribe`, the transcript-reuse path (read and
    write of the JSON) and `find_audio_file` run before the `try` (`run.py:95-107`,
    `137-149`), so an I/O or JSON error there aborts the run.
  - `evaluation/run.py:step_clean_d` and `evaluation/run.py:step_clean_b` import their cleaner
    before the `try` (`run.py:179`, `216`), so an import error is not caught (see Known
    Drift).
- `evaluation/run.py:--report-only` ignores `--episodes` and reports on all of
  `TEST_EPISODES`.
- `evaluation/metrics.py` is pure. It does no I/O and imports nothing from `thestill`.

## Side Effects

- `evaluation/run.py:--reset` deletes `evaluation/outputs` with `shutil.rmtree`.
- These functions write files under `evaluation/outputs/`:
  - `evaluation/run.py:save_state` writes `state.json`.
  - The step functions write `ep_NN_dalston.json`, `ep_NN_elevenlabs.json`, `ep_NN_clean_d.md`,
    `ep_NN_clean_b.md` and `ep_NN_metrics.json`.
  - `evaluation/run.py:generate_report` writes `report.json`.
- The steps make these network calls:
  - `evaluation/run.py:step_dalston_transcribe` calls `thestill.core.dalston_transcriber.DalstonTranscriber`
    with `config.dalston_base_url`.
  - `evaluation/run.py:step_elevenlabs_transcribe` calls `thestill.core.elevenlabs_transcriber.ElevenLabsTranscriber`
    with model `scribe_v1`.
  - `evaluation/run.py:step_clean_d` and `evaluation/run.py:step_clean_b` call the configured
    LLM provider.
- The script reads the following, all under `data/`:
  - downsampled audio;
  - raw transcripts;
  - existing cleaned Markdown;
  - podcast and episode facts, through `FactsManager`.

  It does not read from or write to any database.

## Known Drift

- `evaluation/run.py:step_clean_d` imports `thestill.core.transcript_cleaner.TranscriptCleaner`.
  `evaluation/run.py:step_clean_b` imports `scripts/cleaning_prototype.py`, which imports the
  same module. That module was deleted in commit `8f398689` (legacy pipelines dropped).
  `run_evaluation` makes neither call inside a `try`, so a full run that reaches step 3
  stops with `ModuleNotFoundError`. `--metrics-only` and `--report-only` still work.
  `--skip-dalston` avoids the error only while `dalston_transcribe` is `pending`; on
  `--resume` with it `done`, `clean_d` runs (`run.py:569-571`) and raises
  `ModuleNotFoundError`.

## Verification

- No automated tests cover this folder. `make test` tests the `thestill` modules it depends
  on, but never imports `evaluation/`. To check the pure metric functions, import
  `evaluation/metrics.py` and call them directly.
- Do not run `evaluation/run.py` as a test: it calls paid transcription and LLM APIs.
