#!/usr/bin/env python3
"""
Transcript quality evaluation suite.

Compares Dalston (self-hosted) transcription against ElevenLabs reference,
and evaluates cleaning approaches (current pipeline vs corrections-list).

Usage:
    ./venv/bin/python evaluation/run.py                    # Full run
    ./venv/bin/python evaluation/run.py --resume           # Resume after crash
    ./venv/bin/python evaluation/run.py --reset            # Start fresh
    ./venv/bin/python evaluation/run.py --episodes 0,1,4   # Specific episodes
    ./venv/bin/python evaluation/run.py --skip-elevenlabs  # Skip expensive step
    ./venv/bin/python evaluation/run.py --skip-dalston     # Skip Dalston transcription
    ./venv/bin/python evaluation/run.py --metrics-only     # Recompute metrics from outputs
    ./venv/bin/python evaluation/run.py --report-only      # Regenerate report only
"""

import argparse
import json
import shutil
import sys
import time
from datetime import datetime, timezone
from pathlib import Path
from typing import Optional

# Add project root and scripts to path
PROJECT_ROOT = Path(__file__).parent.parent
sys.path.insert(0, str(PROJECT_ROOT))
sys.path.insert(0, str(PROJECT_ROOT / "scripts"))

from structlog import get_logger

from evaluation.episodes import (
    TEST_EPISODES,
    EpisodeSpec,
    EpisodeState,
    StepState,
    find_audio_file,
    find_existing_transcript,
)
from evaluation.metrics import aggregate_episode_metrics

logger = get_logger(__name__)

DATA_ROOT = PROJECT_ROOT / "data"
OUTPUT_DIR = Path(__file__).parent / "outputs"

STEPS = ["dalston_transcribe", "elevenlabs_transcribe", "clean_d", "clean_b", "metrics"]


class EvalContext:
    """Shared dependencies, created once and passed to all step runners."""

    def __init__(self):
        from thestill.core.facts_manager import FactsManager
        from thestill.core.llm_provider import create_llm_provider_from_config
        from thestill.core.transcript_formatter import TranscriptFormatter
        from thestill.utils.config import load_config
        from thestill.utils.path_manager import PathManager

        self.config = load_config()
        self.path_manager = PathManager(str(DATA_ROOT))
        self.facts_manager = FactsManager(self.path_manager)
        self.formatter = TranscriptFormatter()
        self.provider = create_llm_provider_from_config(self.config)


# ---------------------------------------------------------------------------
# State management
# ---------------------------------------------------------------------------
def load_state(state_path: Path) -> dict[int, EpisodeState]:
    if not state_path.exists():
        return {}
    with open(state_path) as f:
        raw = json.load(f)
    return {int(k): EpisodeState.model_validate(v) for k, v in raw.get("episodes", {}).items()}


def save_state(state: dict[int, EpisodeState], state_path: Path):
    state_path.parent.mkdir(parents=True, exist_ok=True)
    data = {"episodes": {str(k): v.model_dump() for k, v in state.items()}}
    with open(state_path, "w") as f:
        json.dump(data, f, indent=2)


# ---------------------------------------------------------------------------
# Step runners
# ---------------------------------------------------------------------------
def step_dalston_transcribe(ep, ctx: EvalContext, output_dir: Path) -> StepState:
    """Transcribe with Dalston (or reuse existing transcript)."""
    output_path = output_dir / f"ep_{ep.index:02d}_dalston.json"

    existing = find_existing_transcript(ep, DATA_ROOT, "dalston")
    if existing:
        logger.info("Reusing existing Dalston transcript", episode=ep.label, source=str(existing))
        start = time.time()
        with open(existing) as f:
            data = json.load(f)
        with open(output_path, "w") as f:
            json.dump(data, f, indent=2)
        return StepState(status="done", output_path=str(output_path), timing_s=time.time() - start)

    audio_path = find_audio_file(ep, DATA_ROOT)
    if not audio_path:
        return StepState(status="skipped", error="No audio file found")

    start = time.time()
    try:
        from thestill.core.dalston_transcriber import DalstonTranscriber
        from thestill.models.transcription import TranscribeOptions

        transcriber = DalstonTranscriber(
            base_url=ctx.config.dalston_base_url,
            api_key=getattr(ctx.config, "dalston_api_key", ""),
            enable_diarization=True,
        )
        transcript = transcriber.transcribe_audio(
            audio_path=str(audio_path),
            output_path=str(output_path),
            options=TranscribeOptions(language="en"),
        )
        if transcript is None:
            return StepState(status="failed", error="Transcriber returned None", timing_s=time.time() - start)
        with open(output_path, "w") as f:
            json.dump(transcript.model_dump(), f, indent=2, default=str)
        return StepState(status="done", output_path=str(output_path), timing_s=time.time() - start)
    except Exception as e:
        logger.error("Dalston transcription failed", episode=ep.label, error=str(e), exc_info=True)
        return StepState(status="failed", error=str(e), timing_s=time.time() - start)


def step_elevenlabs_transcribe(ep, ctx: EvalContext, output_dir: Path) -> StepState:
    """Transcribe with ElevenLabs (reference)."""
    output_path = output_dir / f"ep_{ep.index:02d}_elevenlabs.json"

    existing = find_existing_transcript(ep, DATA_ROOT, "scribe_v")
    if existing:
        logger.info("Reusing existing ElevenLabs transcript", episode=ep.label, source=str(existing))
        start = time.time()
        with open(existing) as f:
            data = json.load(f)
        with open(output_path, "w") as f:
            json.dump(data, f, indent=2)
        return StepState(status="done", output_path=str(output_path), timing_s=time.time() - start)

    audio_path = find_audio_file(ep, DATA_ROOT)
    if not audio_path:
        return StepState(status="skipped", error="No audio file found")

    start = time.time()
    try:
        from thestill.core.elevenlabs_transcriber import ElevenLabsTranscriber
        from thestill.models.transcription import TranscribeOptions

        transcriber = ElevenLabsTranscriber(
            api_key=ctx.config.elevenlabs_api_key,
            model="scribe_v1",
            enable_diarization=True,
            use_async=False,
            path_manager=ctx.path_manager,
        )
        transcript = transcriber.transcribe_audio(
            audio_path=str(audio_path),
            output_path=str(output_path),
            options=TranscribeOptions(language="en"),
        )
        if transcript is None:
            return StepState(status="failed", error="Transcriber returned None", timing_s=time.time() - start)
        with open(output_path, "w") as f:
            json.dump(transcript.model_dump(), f, indent=2, default=str)
        return StepState(status="done", output_path=str(output_path), timing_s=time.time() - start)
    except Exception as e:
        logger.error("ElevenLabs transcription failed", episode=ep.label, error=str(e), exc_info=True)
        return StepState(status="failed", error=str(e), timing_s=time.time() - start)


def step_clean_d(ep, ctx: EvalContext, dalston_output: str, output_dir: Path) -> StepState:
    """Clean with current pipeline (full LLM rewrite)."""
    from thestill.core.transcript_cleaner import TranscriptCleaner

    output_path = output_dir / f"ep_{ep.index:02d}_clean_d.md"
    start = time.time()

    try:
        with open(dalston_output) as f:
            raw_data = json.load(f)

        podcast_facts = ctx.facts_manager.load_podcast_facts(ep.podcast_slug)
        episode_facts = ctx.facts_manager.load_episode_facts(ep.podcast_slug, ep.episode_slug)
        if not episode_facts:
            return StepState(status="skipped", error="No episode facts", timing_s=time.time() - start)

        formatted = ctx.formatter.format_transcript(raw_data)
        language = raw_data.get("language", "en")
        if language and len(language) > 2:
            language = language[:2]

        cleaner = TranscriptCleaner(ctx.provider)
        cleaned = cleaner.clean_transcript(
            formatted_markdown=formatted,
            podcast_facts=podcast_facts,
            episode_facts=episode_facts,
            episode_title=episode_facts.episode_title,
            language=language,
        )

        output_path.write_text(cleaned, encoding="utf-8")
        return StepState(status="done", output_path=str(output_path), timing_s=time.time() - start)
    except Exception as e:
        logger.error("Clean D failed", episode=ep.label, error=str(e), exc_info=True)
        return StepState(status="failed", error=str(e), timing_s=time.time() - start)


def step_clean_b(ep, ctx: EvalContext, dalston_output: str, output_dir: Path) -> StepState:
    """Clean with corrections-list approach (Approach B)."""
    from cleaning_prototype import approach_b_corrections

    output_path = output_dir / f"ep_{ep.index:02d}_clean_b.md"
    start = time.time()

    try:
        with open(dalston_output) as f:
            raw_data = json.load(f)

        podcast_facts = ctx.facts_manager.load_podcast_facts(ep.podcast_slug)
        episode_facts = ctx.facts_manager.load_episode_facts(ep.podcast_slug, ep.episode_slug)
        if not episode_facts:
            return StepState(status="skipped", error="No episode facts", timing_s=time.time() - start)

        data = {
            "raw_data": raw_data,
            "podcast_facts": podcast_facts,
            "episode_facts": episode_facts,
        }
        episode_dict = {
            "podcast_slug": ep.podcast_slug,
            "episode_slug": ep.episode_slug,
            "label": ep.label,
        }

        result = approach_b_corrections(data, episode_dict, ctx.provider, ctx.formatter)
        output_path.write_text(result.output_text, encoding="utf-8")
        return StepState(status="done", output_path=str(output_path), timing_s=time.time() - start)
    except Exception as e:
        logger.error("Clean B failed", episode=ep.label, error=str(e), exc_info=True)
        return StepState(status="failed", error=str(e), timing_s=time.time() - start)


def step_metrics(ep, ctx: EvalContext, ep_state: EpisodeState, output_dir: Path) -> StepState:
    """Compute all metrics for an episode from existing outputs."""
    output_path = output_dir / f"ep_{ep.index:02d}_metrics.json"
    start = time.time()

    try:
        raw_dalston = _load_json_if_done(ep_state.dalston_transcribe)
        raw_elevenlabs = _load_json_if_done(ep_state.elevenlabs_transcribe)
        clean_d_text = _load_text_if_done(ep_state.clean_d)
        clean_b_text = _load_text_if_done(ep_state.clean_b)
        existing_cleaned_text = _load_existing_cleaned(ep)

        podcast_facts = ctx.facts_manager.load_podcast_facts(ep.podcast_slug)
        episode_facts = ctx.facts_manager.load_episode_facts(ep.podcast_slug, ep.episode_slug)

        # Collect timings
        timings = {}
        for step_name in STEPS[:-1]:  # exclude metrics itself
            ss = getattr(ep_state, step_name)
            if ss.timing_s is not None:
                timings[step_name] = ss.timing_s

        metrics = aggregate_episode_metrics(
            raw_dalston=raw_dalston,
            raw_elevenlabs=raw_elevenlabs,
            clean_d_text=clean_d_text,
            clean_b_text=clean_b_text,
            existing_cleaned_text=existing_cleaned_text,
            podcast_facts=podcast_facts,
            episode_facts=episode_facts,
            timings=timings,
        )
        metrics["episode_index"] = ep.index
        metrics["episode_label"] = ep.label

        with open(output_path, "w") as f:
            json.dump(metrics, f, indent=2)
        return StepState(status="done", output_path=str(output_path), timing_s=time.time() - start)
    except Exception as e:
        logger.error("Metrics computation failed", episode=ep.label, error=str(e), exc_info=True)
        return StepState(status="failed", error=str(e), timing_s=time.time() - start)


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------
def _load_json_if_done(ss: StepState) -> Optional[dict]:
    if ss.status != "done" or not ss.output_path:
        return None
    try:
        with open(ss.output_path) as f:
            return json.load(f)
    except (OSError, json.JSONDecodeError):
        return None


def _load_text_if_done(ss: StepState) -> Optional[str]:
    if ss.status != "done" or not ss.output_path:
        return None
    try:
        return Path(ss.output_path).read_text(encoding="utf-8")
    except OSError:
        return None


def _load_existing_cleaned(ep: EpisodeSpec) -> Optional[str]:
    clean_dir = DATA_ROOT / "clean_transcripts" / ep.podcast_slug
    matches = list(clean_dir.glob(f"{ep.episode_slug}*_cleaned.md"))
    if matches:
        return matches[0].read_text(encoding="utf-8")
    return None


# ---------------------------------------------------------------------------
# Report generation
# ---------------------------------------------------------------------------
def generate_report(state: dict[int, EpisodeState], output_dir: Path) -> dict:
    """Aggregate all per-episode metrics into a summary report."""
    episodes_data = []
    for ep in TEST_EPISODES:
        metrics_path = output_dir / f"ep_{ep.index:02d}_metrics.json"
        if metrics_path.exists():
            with open(metrics_path) as f:
                episodes_data.append(json.load(f))

    # Compute aggregates
    agg = {}
    # WER aggregates
    for key in ["wer_dalston_vs_elevenlabs"]:
        values = [e[key] for e in episodes_data if e.get(key) is not None]
        if values:
            agg[key] = {"mean": sum(values) / len(values), "min": min(values), "max": max(values), "n": len(values)}

    # Per-variant aggregates
    for variant in ["clean_d", "clean_b", "existing"]:
        for metric in ["wer_vs_elevenlabs", "content_retention"]:
            values = []
            for e in episodes_data:
                v = (e.get("variants") or {}).get(variant)
                if v and v.get(metric) is not None:
                    values.append(v[metric])
            if values:
                key = f"{variant}_{metric}"
                agg[key] = {"mean": sum(values) / len(values), "min": min(values), "max": max(values), "n": len(values)}

        # First timestamp pass rate
        ts_checks = []
        for e in episodes_data:
            v = (e.get("variants") or {}).get(variant)
            if v and v.get("first_timestamp"):
                ts_checks.append(v["first_timestamp"].get("ok", False))
        if ts_checks:
            agg[f"{variant}_first_ts_pass_rate"] = sum(ts_checks) / len(ts_checks)

        # Entity accuracy
        entity_ratios = []
        for e in episodes_data:
            v = (e.get("variants") or {}).get(variant)
            if v and v.get("entity_accuracy"):
                entity_ratios.append(v["entity_accuracy"]["ratio"])
        if entity_ratios:
            agg[f"{variant}_entity_accuracy"] = {
                "mean": sum(entity_ratios) / len(entity_ratios),
                "n": len(entity_ratios),
            }

    # Timestamp alignment
    alignments = [e["timestamp_alignment"] for e in episodes_data if e.get("timestamp_alignment")]
    if alignments:
        agg["timestamp_alignment"] = {
            "mean_delta_s": sum(a["mean_delta_s"] for a in alignments) / len(alignments),
            "mean_p90_delta_s": sum(a["p90_delta_s"] for a in alignments) / len(alignments),
            "n": len(alignments),
        }

    # Timing aggregates
    for step in STEPS[:-1]:
        timings = []
        for e in episodes_data:
            t = (e.get("timings") or {}).get(step)
            if t is not None:
                timings.append(t)
        if timings:
            agg[f"timing_{step}"] = {"mean": sum(timings) / len(timings), "total": sum(timings), "n": len(timings)}

    report = {
        "generated_at": datetime.now(timezone.utc).isoformat(),
        "episode_count": len(TEST_EPISODES),
        "completed_count": len(episodes_data),
        "episodes": episodes_data,
        "aggregates": agg,
    }

    report_path = output_dir / "report.json"
    with open(report_path, "w") as f:
        json.dump(report, f, indent=2)
    logger.info("Report saved", path=str(report_path))
    return report


def print_summary_table(report: dict):
    """Print a human-readable summary table to terminal."""
    agg = report.get("aggregates", {})
    episodes = report.get("episodes", [])

    print(f"\n{'=' * 130}")
    print(f"  TRANSCRIPT QUALITY EVALUATION REPORT")
    print(f"  Generated: {report.get('generated_at', '?')}")
    print(f"  Episodes: {report.get('completed_count', 0)}/{report.get('episode_count', 0)}")
    print(f"{'=' * 130}")

    # Per-episode table
    header = f"{'#':>3} {'Episode':<35} {'WER Dal':>8} {'WER ClD':>8} {'WER ClB':>8} {'WER Ext':>8} {'Ret D':>7} {'Ret B':>7} {'TS D':>5} {'TS B':>5} {'Ent D':>6} {'Ent B':>6}"
    print(f"\n{header}")
    print("-" * 130)

    for e in episodes:
        idx = e.get("episode_index", "?")
        label = e.get("episode_label", "?")[:35]

        wer_dal = e.get("wer_dalston_vs_elevenlabs")
        wer_dal_s = f"{wer_dal:.3f}" if wer_dal is not None else "N/A"

        variants = e.get("variants", {})
        vals = {}
        for vname in ["clean_d", "clean_b", "existing"]:
            v = variants.get(vname) or {}
            wer = v.get("wer_vs_elevenlabs")
            vals[f"wer_{vname}"] = f"{wer:.3f}" if wer is not None else "N/A"
            ret = v.get("content_retention")
            vals[f"ret_{vname}"] = f"{ret:.1%}" if ret is not None else "N/A"
            ts = v.get("first_timestamp", {})
            vals[f"ts_{vname}"] = "OK" if ts.get("ok") else f"{ts.get('delta_s', '?')}s" if ts else "N/A"
            ent = v.get("entity_accuracy", {})
            vals[f"ent_{vname}"] = f"{ent['ratio']:.0%}" if ent.get("ratio") is not None else "N/A"

        print(
            f"{idx:>3} {label:<35} "
            f"{wer_dal_s:>8} {vals['wer_clean_d']:>8} {vals['wer_clean_b']:>8} {vals['wer_existing']:>8} "
            f"{vals['ret_clean_d']:>7} {vals['ret_clean_b']:>7} "
            f"{vals['ts_clean_d']:>5} {vals['ts_clean_b']:>5} "
            f"{vals['ent_clean_d']:>6} {vals['ent_clean_b']:>6}"
        )

    # Aggregates
    print(f"\n{'=' * 130}")
    print("  AGGREGATES")
    print(f"{'=' * 130}")

    for key, val in sorted(agg.items()):
        if isinstance(val, dict):
            parts = [f"{k}={v:.4f}" if isinstance(v, float) else f"{k}={v}" for k, v in val.items()]
            print(f"  {key:<45} {', '.join(parts)}")
        else:
            print(f"  {key:<45} {val:.4f}" if isinstance(val, float) else f"  {key:<45} {val}")

    # Timings summary
    print(f"\n  TIMINGS")
    print(f"  {'-' * 60}")
    for step in STEPS[:-1]:
        tkey = f"timing_{step}"
        t = agg.get(tkey)
        if t:
            print(f"  {step:<30} mean={t['mean']:.1f}s  total={t['total']:.1f}s  n={t['n']}")

    print()


# ---------------------------------------------------------------------------
# Main runner
# ---------------------------------------------------------------------------
def run_evaluation(args):
    """Main evaluation loop."""
    state_path = OUTPUT_DIR / "state.json"

    # Handle --reset
    if args.reset and OUTPUT_DIR.exists():
        shutil.rmtree(OUTPUT_DIR)
        print("Reset: cleared all outputs.")

    OUTPUT_DIR.mkdir(parents=True, exist_ok=True)

    # Load or init state
    if args.resume or args.metrics_only or args.report_only:
        state = load_state(state_path)
        if state:
            print(f"Loaded state for {len(state)} episodes.")
        elif args.metrics_only or args.report_only:
            print("Warning: no state file found — metrics/report may be empty.")
            state = {}
    elif state_path.exists() and not args.reset:
        print("State file exists. Use --resume to continue, --reset to start fresh.")
        sys.exit(1)
    else:
        state = {}

    # Filter episodes
    if args.episodes:
        indices = [int(x.strip()) for x in args.episodes.split(",")]
        episodes = [ep for ep in TEST_EPISODES if ep.index in indices]
    else:
        episodes = list(TEST_EPISODES)

    if args.report_only:
        report = generate_report(state, OUTPUT_DIR)
        print_summary_table(report)
        return

    ctx = EvalContext()
    print(f"Using LLM provider: {ctx.provider.get_model_display_name()}")

    total_episodes = len(episodes)
    for i, ep in enumerate(episodes):
        print(f"\n{'='*80}")
        print(f"  [{i+1}/{total_episodes}] Episode {ep.index}: {ep.label}")
        print(f"{'='*80}")

        ep_state = state.get(ep.index, EpisodeState())

        # Step 1: Dalston transcription
        if not args.metrics_only:
            if args.skip_dalston:
                if ep_state.dalston_transcribe.status == "pending":
                    ep_state.dalston_transcribe = StepState(status="skipped", error="--skip-dalston")
            elif ep_state.dalston_transcribe.status != "done":
                print(f"  [1/5] Dalston transcription...")
                ep_state.dalston_transcribe = step_dalston_transcribe(ep, ctx, OUTPUT_DIR)
                print(
                    f"        → {ep_state.dalston_transcribe.status} ({ep_state.dalston_transcribe.timing_s:.1f}s)"
                    if ep_state.dalston_transcribe.timing_s
                    else f"        → {ep_state.dalston_transcribe.status}"
                )
                state[ep.index] = ep_state
                save_state(state, state_path)
            else:
                print(f"  [1/5] Dalston transcription... already done")

        # Step 2: ElevenLabs transcription
        if not args.metrics_only:
            if args.skip_elevenlabs:
                if ep_state.elevenlabs_transcribe.status == "pending":
                    ep_state.elevenlabs_transcribe = StepState(status="skipped", error="--skip-elevenlabs")
            elif ep_state.elevenlabs_transcribe.status != "done":
                print(f"  [2/5] ElevenLabs transcription...")
                ep_state.elevenlabs_transcribe = step_elevenlabs_transcribe(ep, ctx, OUTPUT_DIR)
                print(
                    f"        → {ep_state.elevenlabs_transcribe.status} ({ep_state.elevenlabs_transcribe.timing_s:.1f}s)"
                    if ep_state.elevenlabs_transcribe.timing_s
                    else f"        → {ep_state.elevenlabs_transcribe.status}"
                )
                state[ep.index] = ep_state
                save_state(state, state_path)
            else:
                print(f"  [2/5] ElevenLabs transcription... already done")

        # Step 3: Clean D (requires Dalston transcript)
        if not args.metrics_only:
            if ep_state.dalston_transcribe.status != "done":
                if ep_state.clean_d.status == "pending":
                    ep_state.clean_d = StepState(status="skipped", error="No Dalston transcript")
            elif ep_state.clean_d.status != "done":
                print(f"  [3/5] Cleaning (current pipeline D)...")
                ep_state.clean_d = step_clean_d(ep, ctx, ep_state.dalston_transcribe.output_path, OUTPUT_DIR)
                print(
                    f"        → {ep_state.clean_d.status} ({ep_state.clean_d.timing_s:.1f}s)"
                    if ep_state.clean_d.timing_s
                    else f"        → {ep_state.clean_d.status}"
                )
                state[ep.index] = ep_state
                save_state(state, state_path)
            else:
                print(f"  [3/5] Cleaning (current pipeline D)... already done")

        # Step 4: Clean B (requires Dalston transcript)
        if not args.metrics_only:
            if ep_state.dalston_transcribe.status != "done":
                if ep_state.clean_b.status == "pending":
                    ep_state.clean_b = StepState(status="skipped", error="No Dalston transcript")
            elif ep_state.clean_b.status != "done":
                print(f"  [4/5] Cleaning (corrections-list B)...")
                ep_state.clean_b = step_clean_b(ep, ctx, ep_state.dalston_transcribe.output_path, OUTPUT_DIR)
                print(
                    f"        → {ep_state.clean_b.status} ({ep_state.clean_b.timing_s:.1f}s)"
                    if ep_state.clean_b.timing_s
                    else f"        → {ep_state.clean_b.status}"
                )
                state[ep.index] = ep_state
                save_state(state, state_path)
            else:
                print(f"  [4/5] Cleaning (corrections-list B)... already done")

        # Step 5: Metrics (always recompute if --metrics-only, otherwise only if not done)
        should_compute_metrics = args.metrics_only or ep_state.metrics.status != "done"
        if should_compute_metrics:
            print(f"  [5/5] Computing metrics...")
            ep_state.metrics = step_metrics(ep, ctx, ep_state, OUTPUT_DIR)
            print(f"        → {ep_state.metrics.status}")
            state[ep.index] = ep_state
            save_state(state, state_path)
        else:
            print(f"  [5/5] Computing metrics... already done")

    # Generate report
    print(f"\n{'='*80}")
    print("  Generating report...")
    report = generate_report(state, OUTPUT_DIR)
    print_summary_table(report)


def main():
    parser = argparse.ArgumentParser(description="Transcript quality evaluation suite")
    parser.add_argument("--resume", action="store_true", help="Resume from existing state")
    parser.add_argument("--reset", action="store_true", help="Delete all outputs and start fresh")
    parser.add_argument("--episodes", type=str, help="Comma-separated episode indices (e.g. 0,1,4)")
    parser.add_argument("--skip-elevenlabs", action="store_true", help="Skip ElevenLabs transcription")
    parser.add_argument("--skip-dalston", action="store_true", help="Skip Dalston transcription")
    parser.add_argument("--metrics-only", action="store_true", help="Only (re)compute metrics from existing outputs")
    parser.add_argument("--report-only", action="store_true", help="Only regenerate report from existing metrics")

    args = parser.parse_args()
    run_evaluation(args)


if __name__ == "__main__":
    main()
