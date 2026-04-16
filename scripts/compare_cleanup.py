#!/usr/bin/env python3
# Copyright 2025-2026 Thestill
#
# Licensed under the Apache License, Version 2.0 (the "License");
# you may not use this file except in compliance with the License.
# You may obtain a copy of the License at
#
#     http://www.apache.org/licenses/LICENSE-2.0

"""Compare legacy vs segmented cleanup across recent episodes.

Snapshots each episode's existing cleaned Markdown as ``*.baseline.md``,
re-runs cleanup with both pipelines (segmented primary + legacy shadow
via the ``THESTILL_CLEANUP_PIPELINE`` / ``THESTILL_LEGACY_CLEANUP_SHADOW``
env vars), and computes six metrics per episode. The output is a per-
episode table plus a median summary.

Run after setting your provider API key in ``.env`` like you do for a
normal ``thestill clean-transcript`` call — this script reuses the same
config.

Usage:
    # Smoke test on the 10 most-recent cleaned+summarised episodes
    ./venv/bin/python scripts/compare_cleanup.py --last 10

    # Print targets without re-running the LLM
    ./venv/bin/python scripts/compare_cleanup.py --last 10 --dry-run

    # Analyse-only: compute metrics on whatever is already on disk
    # (useful for iterating on the metric code without burning tokens)
    ./venv/bin/python scripts/compare_cleanup.py --last 10 --analyse-only

Three files are compared per episode:

- ``{file}_cleaned.baseline.md`` — the pre-existing cleaned output (the
  historical legacy result, from whenever it was first cleaned).
- ``debug/{file}.shadow_legacy.md`` — a fresh legacy re-run, captured
  during this script's cleanup pass. Serves as a control to measure the
  LLM's run-to-run non-determinism.
- ``{file}_cleaned.md`` — the new segmented output (overwrites the
  historical one; the baseline snapshot preserves it).
"""

from __future__ import annotations

import argparse
import json
import os
import re
import shutil
import sqlite3
import statistics
import sys
import time
from dataclasses import dataclass, field
from pathlib import Path
from typing import List, Optional, Tuple

PROJECT_ROOT = Path(__file__).parent.parent
sys.path.insert(0, str(PROJECT_ROOT))

from thestill.core.facts_manager import FactsManager  # noqa: E402
from thestill.core.llm_provider import create_llm_provider_from_config  # noqa: E402
from thestill.core.transcript_cleaning_processor import TranscriptCleaningProcessor  # noqa: E402
from thestill.core.transcript_formatter import TranscriptFormatter  # noqa: E402
from thestill.models.facts import EpisodeFacts, PodcastFacts  # noqa: E402
from thestill.models.transcript import Transcript  # noqa: E402
from thestill.utils.config import load_config  # noqa: E402
from thestill.utils.path_manager import PathManager  # noqa: E402


@dataclass
class EpisodeTarget:
    """One episode selected for the compare run."""

    podcast_id: str
    podcast_slug: str
    podcast_title: str
    podcast_language: str
    podcast_description: str
    episode_id: str
    episode_slug: str
    episode_title: str
    episode_description: str
    raw_transcript_path: str
    clean_transcript_path: str


@dataclass
class MetricsRow:
    """Metrics for one cleaned-output variant of one episode."""

    first_timestamp_seconds: Optional[float]
    word_count: int
    raw_word_coverage_pct: float
    entity_recall_pct: float
    ad_marker_count: int
    char_ratio: float


@dataclass
class EpisodeReport:
    """Full compare report for one episode."""

    target: EpisodeTarget
    raw_word_total: int
    baseline: Optional[MetricsRow] = None
    shadow_legacy: Optional[MetricsRow] = None
    segmented: Optional[MetricsRow] = None
    errors: List[str] = field(default_factory=list)


_SELECT_COLUMNS = """
    p.id, p.slug, p.title, p.language, p.description,
    e.id, e.slug, e.title, e.description,
    e.raw_transcript_path, e.clean_transcript_path
"""


def _row_to_target(row: Tuple) -> EpisodeTarget:
    return EpisodeTarget(
        podcast_id=row[0],
        podcast_slug=row[1],
        podcast_title=row[2],
        podcast_language=row[3] or "en",
        podcast_description=row[4] or "",
        episode_id=row[5],
        episode_slug=row[6],
        episode_title=row[7],
        episode_description=row[8] or "",
        raw_transcript_path=row[9],
        clean_transcript_path=row[10],
    )


def discover_recent_episodes(db_path: Path, limit: int) -> List[EpisodeTarget]:
    """Return the N most-recent episodes with both clean and summary paths set."""
    query = f"""
        SELECT {_SELECT_COLUMNS}
        FROM episodes e
        JOIN podcasts p ON p.id = e.podcast_id
        WHERE e.clean_transcript_path IS NOT NULL
          AND e.summary_path IS NOT NULL
          AND e.raw_transcript_path IS NOT NULL
        ORDER BY e.pub_date DESC
        LIMIT ?
    """
    conn = sqlite3.connect(str(db_path))
    try:
        rows = conn.execute(query, (limit,)).fetchall()
    finally:
        conn.close()
    return [_row_to_target(row) for row in rows]


def discover_episodes_by_id(db_path: Path, episode_ids: List[str]) -> List[EpisodeTarget]:
    """Return episode targets for the given DB ids, preserving argument order.

    Missing ids are silently dropped — the caller reports which ids
    didn't resolve against the overall selection count.
    """
    if not episode_ids:
        return []
    placeholders = ", ".join("?" for _ in episode_ids)
    query = f"""
        SELECT {_SELECT_COLUMNS}
        FROM episodes e
        JOIN podcasts p ON p.id = e.podcast_id
        WHERE e.id IN ({placeholders})
          AND e.raw_transcript_path IS NOT NULL
          AND e.clean_transcript_path IS NOT NULL
    """
    conn = sqlite3.connect(str(db_path))
    try:
        rows = conn.execute(query, episode_ids).fetchall()
    finally:
        conn.close()

    # Preserve the order the caller gave us.
    by_id = {row[5]: _row_to_target(row) for row in rows}
    return [by_id[eid] for eid in episode_ids if eid in by_id]


def discover_gap_candidates(
    db_path: Path,
    path_manager: PathManager,
    *,
    max_scan: int,
    first_ts_threshold_seconds: float = 60.0,
    coverage_threshold_pct: float = 70.0,
) -> List[EpisodeTarget]:
    """Auto-discover episodes whose existing cleaned MD shows the gap-bug signature.

    Pre-filter logic (each check cheap on its own, executed in order so
    the expensive one — parsing the raw JSON for word count — only runs
    when the fast check hasn't already flagged the episode):

    1. Fast path: peek the first ~200 bytes of the cleaned MD and extract
       the earliest ``[HH:MM:SS]`` / ``[MM:SS]`` marker. When the marker
       is beyond ``first_ts_threshold_seconds`` the episode is an
       obvious gap-bug suspect and is reported without computing
       coverage.
    2. Slow path: parse the raw JSON to compute the word-coverage
       ratio. When the cleaned MD contains less than
       ``coverage_threshold_pct`` of the raw transcript's words, the
       episode is reported.

    ``max_scan`` caps the total number of episodes checked — the DB
    query returns the most-recent episodes first, so a cap acts as a
    "check the last N" window. Progress output is the caller's
    responsibility; this function is silent so it can be imported and
    reused without side-effects.
    """
    candidates = discover_recent_episodes(db_path, max_scan)
    suspects: List[EpisodeTarget] = []
    for target in candidates:
        clean_path = path_manager.clean_transcript_file(target.clean_transcript_path)
        if not clean_path.exists():
            continue

        head = clean_path.read_text(encoding="utf-8", errors="ignore")[:4096]
        first_ts = _first_timestamp_seconds(head)
        if first_ts is not None and first_ts > first_ts_threshold_seconds:
            suspects.append(target)
            continue

        # Slow path: coverage check. Needs the raw JSON and the cleaned
        # MD's full text. Skipped on any I/O error — a broken episode
        # can't be a gap-bug candidate we can fix.
        try:
            raw_path = path_manager.raw_transcript_file(target.raw_transcript_path)
            with raw_path.open("r", encoding="utf-8") as fh:
                raw_data = json.load(fh)
            transcript = Transcript.model_validate(raw_data)
            raw_word_total = _raw_word_total(transcript)
            cleaned_word_count = len(clean_path.read_text(encoding="utf-8").split())
        except Exception:  # pylint: disable=broad-except
            continue

        if raw_word_total == 0:
            continue
        coverage = 100.0 * cleaned_word_count / raw_word_total
        if coverage < coverage_threshold_pct:
            suspects.append(target)

    return suspects


def snapshot_baseline(path_manager: PathManager, target: EpisodeTarget) -> Optional[Path]:
    """Copy the current cleaned MD to ``*.baseline.md``, idempotent.

    Returns the baseline path, or ``None`` if the source cleaned file is
    missing from disk (record-out-of-sync — reported as an error).
    """
    clean_path = path_manager.clean_transcript_file(target.clean_transcript_path)
    if not clean_path.exists():
        return None
    baseline_path = clean_path.with_name(clean_path.stem + ".baseline.md")
    if not baseline_path.exists():
        shutil.copy(clean_path, baseline_path)
    return baseline_path


def shadow_legacy_path(path_manager: PathManager, target: EpisodeTarget) -> Path:
    """Expected debug file for the legacy shadow produced during re-run."""
    return path_manager.clean_transcript_shadow_file(
        target.podcast_slug,
        Path(target.clean_transcript_path).name,
        "legacy",
    )


def run_dual_cleanup(
    processor: TranscriptCleaningProcessor,
    path_manager: PathManager,
    target: EpisodeTarget,
) -> None:
    """Execute the cleaning processor with segmented primary + legacy shadow.

    Env vars are set for the duration of this call. The processor reads
    them inside ``clean_transcript`` and both pipelines run side by side.
    """
    os.environ["THESTILL_CLEANUP_PIPELINE"] = "segmented"
    os.environ["THESTILL_LEGACY_CLEANUP_SHADOW"] = "1"

    raw_path = path_manager.raw_transcript_file(target.raw_transcript_path)
    with raw_path.open("r", encoding="utf-8") as fh:
        transcript_data = json.load(fh)

    clean_path = path_manager.clean_transcript_file(target.clean_transcript_path)
    clean_path.parent.mkdir(parents=True, exist_ok=True)

    processor.clean_transcript(
        transcript_data=transcript_data,
        podcast_title=target.podcast_title,
        podcast_description=target.podcast_description,
        episode_title=target.episode_title,
        episode_description=target.episode_description,
        podcast_slug=target.podcast_slug,
        episode_slug=target.episode_slug,
        output_path=str(clean_path),
        path_manager=path_manager,
        save_prompts=False,
        language=target.podcast_language,
    )


# ---------------------------------------------------------------------------
# Metrics
# ---------------------------------------------------------------------------

_TIMESTAMP_RE = re.compile(r"\[(\d{1,2}):(\d{2})(?::(\d{2}))?\]")
_AD_BREAK_RE = re.compile(r"\[AD\s*BREAK\]", re.IGNORECASE)


def _first_timestamp_seconds(text: str) -> Optional[float]:
    """Return the earliest ``[HH:MM:SS]`` / ``[MM:SS]`` marker in seconds."""
    match = _TIMESTAMP_RE.search(text)
    if not match:
        return None
    a, b, c = match.groups()
    if c is not None:
        return int(a) * 3600 + int(b) * 60 + int(c)
    return int(a) * 60 + int(b)


def _entity_strings(podcast_facts: Optional[PodcastFacts], episode_facts: EpisodeFacts) -> List[str]:
    """Flatten facts into a list of entity strings for recall scoring.

    Strips role annotations like ``"(Host)"`` and ``" - Description"`` so
    the substring check matches the name alone.
    """
    raw: List[str] = []
    if podcast_facts is not None:
        raw += podcast_facts.hosts
        raw += podcast_facts.production_team
        raw += podcast_facts.sponsors
        raw += podcast_facts.keywords
    raw += episode_facts.guests
    raw += episode_facts.ad_sponsors
    raw += episode_facts.topics_keywords

    cleaned: List[str] = []
    for item in raw:
        if not item:
            continue
        # Strip " - <description>" and " (<role>)" annotations.
        core = item.split(" - ", 1)[0]
        core = core.split(" (", 1)[0].strip()
        if len(core) >= 2:
            cleaned.append(core)
    return cleaned


def _entity_recall_pct(text: str, entities: List[str]) -> float:
    """Fraction of entities whose name appears (case-insensitive) in ``text``."""
    if not entities:
        return 100.0
    lower = text.lower()
    hits = sum(1 for entity in entities if entity.lower() in lower)
    return 100.0 * hits / len(entities)


def _raw_word_total(transcript: Transcript) -> int:
    """Total word count in the raw transcript (prefers word-level data)."""
    total_words = sum(len(seg.words) for seg in transcript.segments)
    if total_words > 0:
        return total_words
    return sum(len(seg.text.split()) for seg in transcript.segments)


def compute_metrics(
    cleaned_text: str,
    transcript: Transcript,
    entities: List[str],
    formatted_raw: str,
    raw_word_total: int,
) -> MetricsRow:
    word_count = len(cleaned_text.split())
    coverage = 100.0 * word_count / raw_word_total if raw_word_total else 0.0
    ratio = len(cleaned_text) / len(formatted_raw) if formatted_raw else 0.0

    return MetricsRow(
        first_timestamp_seconds=_first_timestamp_seconds(cleaned_text),
        word_count=word_count,
        raw_word_coverage_pct=coverage,
        entity_recall_pct=_entity_recall_pct(cleaned_text, entities),
        ad_marker_count=len(_AD_BREAK_RE.findall(cleaned_text)),
        char_ratio=ratio,
    )


# ---------------------------------------------------------------------------
# Analysis
# ---------------------------------------------------------------------------


def analyse_episode(
    target: EpisodeTarget,
    path_manager: PathManager,
    facts_manager: FactsManager,
) -> EpisodeReport:
    """Load the three cleaned variants from disk and compute metrics for each.

    Variants missing from disk are reported as ``None`` in the row —
    typically the baseline is missing on episodes cleaned before this
    script first ran, and the shadow is missing if the re-run hasn't
    happened yet (``--analyse-only`` mode on a fresh episode).
    """
    raw_path = path_manager.raw_transcript_file(target.raw_transcript_path)
    with raw_path.open("r", encoding="utf-8") as fh:
        transcript_data = json.load(fh)
    transcript = Transcript.model_validate(transcript_data)

    formatted_raw = TranscriptFormatter().format_transcript(transcript_data)
    raw_word_total = _raw_word_total(transcript)

    podcast_facts = facts_manager.load_podcast_facts(target.podcast_slug)
    episode_facts = facts_manager.load_episode_facts(target.podcast_slug, target.episode_slug)
    if episode_facts is None:
        episode_facts = EpisodeFacts(episode_title=target.episode_title)
    entities = _entity_strings(podcast_facts, episode_facts)

    clean_path = path_manager.clean_transcript_file(target.clean_transcript_path)
    baseline_path = clean_path.with_name(clean_path.stem + ".baseline.md")
    shadow_path = shadow_legacy_path(path_manager, target)

    report = EpisodeReport(target=target, raw_word_total=raw_word_total)

    def _metrics_or_none(path: Path, label: str) -> Optional[MetricsRow]:
        if not path.exists():
            report.errors.append(f"{label} missing: {path}")
            return None
        text = path.read_text(encoding="utf-8")
        return compute_metrics(text, transcript, entities, formatted_raw, raw_word_total)

    report.baseline = _metrics_or_none(baseline_path, "baseline")
    report.shadow_legacy = _metrics_or_none(shadow_path, "shadow_legacy")
    report.segmented = _metrics_or_none(clean_path, "segmented")

    return report


# ---------------------------------------------------------------------------
# Rendering
# ---------------------------------------------------------------------------


def _fmt_ts(seconds: Optional[float]) -> str:
    if seconds is None:
        return "  —"
    mins = int(seconds // 60)
    secs = int(seconds % 60)
    return f"{mins:>3d}:{secs:02d}"


def _fmt_pct(value: Optional[float]) -> str:
    if value is None:
        return "   —"
    return f"{value:5.1f}%"


def _fmt_int(value: Optional[int]) -> str:
    if value is None:
        return " —"
    return f"{value:>2d}"


def _fmt_ratio(value: Optional[float]) -> str:
    if value is None:
        return "  —"
    return f"{value:.2f}"


def _render_row(method: str, row: Optional[MetricsRow]) -> str:
    if row is None:
        return f"    {method:<10} | (missing)"
    return (
        f"    {method:<10} |  first_ts {_fmt_ts(row.first_timestamp_seconds)}"
        f"  wc {row.word_count:>6}  cov {_fmt_pct(row.raw_word_coverage_pct)}"
        f"  ent {_fmt_pct(row.entity_recall_pct)}"
        f"  ads {_fmt_int(row.ad_marker_count)}"
        f"  ratio {_fmt_ratio(row.char_ratio)}"
    )


def _median_of(
    reports: List[EpisodeReport],
    method: str,
    attr: str,
) -> Optional[float]:
    values: List[float] = []
    for report in reports:
        row = getattr(report, method)
        if row is None:
            continue
        value = getattr(row, attr)
        if value is None:
            continue
        values.append(float(value))
    if not values:
        return None
    return statistics.median(values)


def render_report(reports: List[EpisodeReport]) -> str:
    lines: List[str] = []
    lines.append("=" * 80)
    lines.append("Cleanup comparison — baseline vs fresh-legacy-shadow vs new-segmented")
    lines.append("=" * 80)
    lines.append("")

    for idx, report in enumerate(reports, start=1):
        target = report.target
        title = f"{target.episode_title}"
        if len(title) > 70:
            title = title[:67] + "..."
        lines.append(f"[{idx:>2}] {target.podcast_title[:30]:<30}  •  {title}")
        lines.append(f"     raw words: {report.raw_word_total}")
        lines.append(_render_row("baseline", report.baseline))
        lines.append(_render_row("shadow-lgc", report.shadow_legacy))
        lines.append(_render_row("segmented", report.segmented))
        if report.errors:
            for err in report.errors:
                lines.append(f"    ⚠️  {err}")
        lines.append("")

    # Median summary across episodes
    lines.append("-" * 80)
    lines.append("Medians across all episodes:")
    lines.append("-" * 80)
    for method in ("baseline", "shadow_legacy", "segmented"):
        ts = _median_of(reports, method, "first_timestamp_seconds")
        cov = _median_of(reports, method, "raw_word_coverage_pct")
        ent = _median_of(reports, method, "entity_recall_pct")
        ads = _median_of(reports, method, "ad_marker_count")
        ratio = _median_of(reports, method, "char_ratio")
        label = {"baseline": "baseline", "shadow_legacy": "shadow-lgc", "segmented": "segmented"}[method]
        lines.append(
            f"  {label:<10}"
            f"  first_ts {_fmt_ts(ts)}"
            f"  cov {_fmt_pct(cov)}"
            f"  ent {_fmt_pct(ent)}"
            f"  ads {_fmt_ratio(ads) if ads is not None else '  —'}"
            f"  ratio {_fmt_ratio(ratio)}"
        )
    lines.append("")
    lines.append("Interpretation:")
    lines.append("  first_ts — where the cleaned output first references the audio.")
    lines.append("    Baseline >> 0 and segmented ~0 means the gap bug is fixed.")
    lines.append("  cov     — cleaned-word-count / raw-word-count.")
    lines.append("    Baseline < 0.7 means the old run skipped content.")
    lines.append("  ent     — fraction of known entities (hosts, sponsors, guests,")
    lines.append("            keywords) appearing anywhere in the cleaned text.")
    lines.append("  ads     — count of [AD BREAK] markers.")
    lines.append("  ratio   — cleaned_chars / formatted_raw_chars.")
    lines.append("")
    lines.append("Shadow-legacy vs baseline tells you the LLM noise floor;")
    lines.append("segmented vs shadow-legacy is the real signal.")

    return "\n".join(lines)


# ---------------------------------------------------------------------------
# Entry point
# ---------------------------------------------------------------------------


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    selector = parser.add_argument_group("target selection (at most one)")
    selector.add_argument(
        "--last",
        type=int,
        default=10,
        help="Number of most-recent cleaned+summarised episodes to include (default 10)",
    )
    selector.add_argument(
        "--episode-id",
        action="append",
        dest="episode_ids",
        default=None,
        help="Target specific episode row(s) by DB id. Repeatable. Overrides --last.",
    )
    selector.add_argument(
        "--gap-candidates",
        action="store_true",
        help=(
            "Auto-discover episodes whose existing cleaned MD exhibits the "
            "gap bug (first_ts > 60s OR coverage < 70%%). Scans the most-"
            "recent --max-scan episodes. Overrides --last."
        ),
    )
    selector.add_argument(
        "--max-scan",
        type=int,
        default=200,
        help="Window size for --gap-candidates discovery (default 200)",
    )

    parser.add_argument(
        "--dry-run",
        action="store_true",
        help="Print the selected targets and exit",
    )
    parser.add_argument(
        "--analyse-only",
        action="store_true",
        help="Skip the cleanup re-run; compute metrics on existing on-disk files",
    )
    parser.add_argument(
        "--storage-path",
        default="./data",
        help="Storage root (default ./data)",
    )
    parser.add_argument(
        "--save-json",
        type=Path,
        default=None,
        help="Optional path to save the metrics as JSON (in addition to stdout)",
    )
    args = parser.parse_args()

    config = load_config()
    path_manager = PathManager(storage_path=args.storage_path)
    db_path = Path(args.storage_path) / "podcasts.db"
    if not db_path.exists():
        print(f"❌ Database not found at {db_path}", file=sys.stderr)
        return 1

    # Resolve target selection. Priority: explicit ids > gap-candidates > last-N.
    if args.episode_ids:
        targets = discover_episodes_by_id(db_path, args.episode_ids)
        missing = set(args.episode_ids) - {t.episode_id for t in targets}
        if missing:
            print(f"  ⚠️  {len(missing)} episode-id(s) did not resolve: " f"{', '.join(sorted(missing))}")
    elif args.gap_candidates:
        print(f"Scanning up to {args.max_scan} recent cleaned+summarised episodes for " f"gap-bug signatures...")
        targets = discover_gap_candidates(db_path, path_manager, max_scan=args.max_scan)
        print(f"Found {len(targets)} gap-bug candidate(s).")
    else:
        targets = discover_recent_episodes(db_path, args.last)

    if not targets:
        print("❌ No episodes selected.", file=sys.stderr)
        return 1

    print(f"Selected {len(targets)} episode(s):")
    for idx, target in enumerate(targets, start=1):
        print(f"  [{idx:>2}] {target.podcast_slug}  •  {target.episode_title[:70]}")

    if args.dry_run:
        return 0

    # Snapshot baselines before any destructive work.
    snapshot_errors: List[str] = []
    for target in targets:
        baseline = snapshot_baseline(path_manager, target)
        if baseline is None:
            snapshot_errors.append(f"{target.episode_slug}: cleaned file missing — skipping baseline snapshot")
    if snapshot_errors:
        for err in snapshot_errors:
            print(f"  ⚠️  {err}")

    if not args.analyse_only:
        llm_provider = create_llm_provider_from_config(config)
        print(f"\nLLM: {config.llm_provider.upper()} / {llm_provider.get_model_name()}")
        processor = TranscriptCleaningProcessor(llm_provider)

        os.environ["THESTILL_CLEANUP_PIPELINE"] = "segmented"
        os.environ["THESTILL_LEGACY_CLEANUP_SHADOW"] = "1"
        print("Env: THESTILL_CLEANUP_PIPELINE=segmented THESTILL_LEGACY_CLEANUP_SHADOW=1")

        for idx, target in enumerate(targets, start=1):
            print(f"\n[{idx}/{len(targets)}] Re-running cleanup: " f"{target.podcast_slug} / {target.episode_slug}")
            start = time.time()
            try:
                run_dual_cleanup(processor, path_manager, target)
                print(f"    done in {time.time() - start:.1f}s")
            except Exception as exc:  # pylint: disable=broad-except
                print(f"    ❌ failed: {exc}")

    facts_manager = FactsManager(path_manager)
    reports: List[EpisodeReport] = []
    for target in targets:
        try:
            reports.append(analyse_episode(target, path_manager, facts_manager))
        except Exception as exc:  # pylint: disable=broad-except
            # Keep the loop alive so one bad episode doesn't kill the rest
            # of the report. The exception message goes into the report as
            # a visible error so the user can see which episode failed.
            stub = EpisodeReport(target=target, raw_word_total=0)
            stub.errors.append(f"analysis failed: {exc}")
            reports.append(stub)

    print("")
    print(render_report(reports))

    if args.save_json:
        args.save_json.parent.mkdir(parents=True, exist_ok=True)
        payload = {
            "generated_at": time.strftime("%Y-%m-%dT%H:%M:%S"),
            "episodes": [
                {
                    "podcast_slug": r.target.podcast_slug,
                    "episode_slug": r.target.episode_slug,
                    "episode_title": r.target.episode_title,
                    "raw_word_total": r.raw_word_total,
                    "baseline": r.baseline.__dict__ if r.baseline else None,
                    "shadow_legacy": r.shadow_legacy.__dict__ if r.shadow_legacy else None,
                    "segmented": r.segmented.__dict__ if r.segmented else None,
                    "errors": r.errors,
                }
                for r in reports
            ],
        }
        args.save_json.write_text(json.dumps(payload, indent=2), encoding="utf-8")
        print(f"\nSaved metrics JSON: {args.save_json}")

    return 0


if __name__ == "__main__":
    sys.exit(main())
