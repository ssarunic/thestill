#!/usr/bin/env python3
"""Run segmented cleanup on the first N minutes of raw transcripts and
compare against the slice of the existing Google-Flash cleaned output.

Leaves production paths and the DB untouched: all outputs go to
``reports/cleanup_slice/``.
"""

from __future__ import annotations

import argparse
import json
import os
import re
import sqlite3
import sys
import time
from pathlib import Path
from typing import List, Optional, Tuple

PROJECT_ROOT = Path(__file__).parent.parent
sys.path.insert(0, str(PROJECT_ROOT))

from thestill.core.facts_manager import FactsManager  # noqa: E402
from thestill.core.llm_provider import create_llm_provider_from_config  # noqa: E402
from thestill.core.segmented_transcript_cleaner import SegmentedTranscriptCleaner  # noqa: E402
from thestill.core.transcript_segmenter import TranscriptSegmenter  # noqa: E402
from thestill.models.facts import EpisodeFacts  # noqa: E402
from thestill.models.transcript import Transcript  # noqa: E402
from thestill.utils.config import load_config  # noqa: E402
from thestill.utils.path_manager import PathManager  # noqa: E402

TIMESTAMP_RE = re.compile(r"\[(\d{1,2}):(\d{2})(?::(\d{2}))?\]")


def parse_ts_seconds(line: str) -> Optional[float]:
    m = TIMESTAMP_RE.search(line)
    if not m:
        return None
    a, b, c = m.groups()
    if c is not None:
        return int(a) * 3600 + int(b) * 60 + int(c)
    return int(a) * 60 + int(b)


def slice_existing_cleaned_md(md_path: Path, max_seconds: float) -> str:
    """Return lines from a legacy cleaned-MD file up to max_seconds."""
    if not md_path.exists():
        return ""
    lines = md_path.read_text(encoding="utf-8").splitlines()
    out: List[str] = []
    last_ts: Optional[float] = None
    for line in lines:
        ts = parse_ts_seconds(line)
        if ts is not None:
            last_ts = ts
            if ts > max_seconds:
                break
        out.append(line)
    return "\n".join(out).rstrip() + "\n"


def slice_raw_transcript(transcript: Transcript, max_seconds: float) -> Transcript:
    """Return a copy of `transcript` limited to segments ending by max_seconds."""
    kept = [seg for seg in transcript.segments if seg.start < max_seconds]
    # keep segment IDs stable — they refer back to raw JSON by value
    data = transcript.model_dump()
    data["segments"] = [seg.model_dump() for seg in kept]
    return Transcript.model_validate(data)


def episode_targets(db_path: Path, episode_ids: List[str]) -> List[Tuple[str, str, str, str, str, str, str, str, str]]:
    placeholders = ", ".join("?" for _ in episode_ids)
    query = f"""
        SELECT e.id, e.slug, e.title, e.description,
               p.id, p.slug, p.title, p.description, p.language,
               e.raw_transcript_path, e.clean_transcript_path
        FROM episodes e JOIN podcasts p ON p.id = e.podcast_id
        WHERE e.id IN ({placeholders})
    """
    conn = sqlite3.connect(str(db_path))
    try:
        rows = conn.execute(query, episode_ids).fetchall()
    finally:
        conn.close()
    by_id = {row[0]: row for row in rows}
    return [by_id[eid] for eid in episode_ids if eid in by_id]


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("--episode-id", action="append", dest="episode_ids", required=True)
    parser.add_argument("--minutes", type=float, default=15.0)
    parser.add_argument("--storage-path", default="./data")
    parser.add_argument("--report-dir", default="./reports/cleanup_slice")
    args = parser.parse_args()

    max_seconds = args.minutes * 60.0
    storage_path = Path(args.storage_path)
    report_dir = Path(args.report_dir)
    report_dir.mkdir(parents=True, exist_ok=True)

    db_path = storage_path / "podcasts.db"
    path_manager = PathManager(storage_path=str(storage_path))
    facts_manager = FactsManager(path_manager)

    config = load_config()
    llm_provider = create_llm_provider_from_config(config)
    print(f"LLM: {config.llm_provider.upper()} / {llm_provider.get_model_name()}")
    print(f"Slice: first {args.minutes:.1f} min ({max_seconds:.0f} s)")

    segmenter = TranscriptSegmenter()
    cleaner = SegmentedTranscriptCleaner(llm_provider)

    targets = episode_targets(db_path, args.episode_ids)
    print(f"Episodes: {len(targets)}")

    summary_rows: List[dict] = []

    for idx, row in enumerate(targets, 1):
        (
            episode_id,
            episode_slug,
            episode_title,
            episode_description,
            podcast_id,
            podcast_slug,
            podcast_title,
            podcast_description,
            podcast_language,
            raw_transcript_path,
            clean_transcript_path,
        ) = row

        print(f"\n[{idx:>2}/{len(targets)}] {podcast_slug} / {episode_slug}")
        t_start = time.time()
        try:
            raw_path = path_manager.raw_transcript_file(raw_transcript_path)
            with raw_path.open("r", encoding="utf-8") as fh:
                raw_data = json.load(fh)
            transcript = Transcript.model_validate(raw_data)

            sliced = slice_raw_transcript(transcript, max_seconds)
            print(f"    raw segments: {len(transcript.segments)}  sliced: {len(sliced.segments)}")

            annotated_raw = segmenter.repair(sliced, episode_id=episode_id)
            print(f"    annotated segments after repair: {len(annotated_raw.segments)}")

            podcast_facts = facts_manager.load_podcast_facts(podcast_slug)
            episode_facts = facts_manager.load_episode_facts(podcast_slug, episode_slug)
            if episode_facts is None:
                episode_facts = EpisodeFacts(episode_title=episode_title)

            cleaned = cleaner.clean(
                annotated_raw,
                podcast_facts,
                episode_facts,
                language=podcast_language or "en",
            )
            new_md = cleaned.to_blended_markdown()

            baseline_md_full = path_manager.clean_transcript_file(clean_transcript_path)
            # For episode 1, the production _cleaned.md has been overwritten
            # by the smoke test — fall back to the .baseline.md snapshot.
            baseline_candidate = baseline_md_full.with_name(baseline_md_full.stem + ".baseline.md")
            if baseline_candidate.exists():
                old_md_source = baseline_candidate
            else:
                old_md_source = baseline_md_full
            old_md = slice_existing_cleaned_md(old_md_source, max_seconds)

            elapsed = time.time() - t_start
            print(f"    cleanup done in {elapsed:.1f}s")

            # Per-episode report: side by side in one markdown file
            name = f"{podcast_slug}__{episode_slug}"
            out_path = report_dir / f"{name}.md"
            new_only = report_dir / f"{name}.new.md"
            old_only = report_dir / f"{name}.old.md"
            new_only.write_text(new_md, encoding="utf-8")
            old_only.write_text(old_md, encoding="utf-8")

            header = (
                f"# {episode_title}\n\n"
                f"**Podcast:** {podcast_title}\n"
                f"**First {args.minutes:.0f} min of audio** (first {len(sliced.segments)} raw segments, "
                f"{len(annotated_raw.segments)} post-repair segments; cleanup took {elapsed:.1f}s)\n\n"
                f"- Provider: `{config.llm_provider.upper()} / {llm_provider.get_model_name()}`\n"
                f"- Old source file: `{old_md_source.relative_to(storage_path.parent) if old_md_source.is_relative_to(storage_path.parent) else old_md_source}`\n\n"
                "---\n\n"
            )
            body = (
                "## OLD — Google Flash (current baseline)\n\n"
                + (old_md if old_md.strip() else "_(baseline slice empty)_\n")
                + "\n---\n\n"
                "## NEW — Ollama gemma4:e4b, segmented per-segment cleanup\n\n" + new_md + "\n"
            )
            out_path.write_text(header + body, encoding="utf-8")

            summary_rows.append(
                {
                    "episode_id": episode_id,
                    "podcast_slug": podcast_slug,
                    "episode_title": episode_title,
                    "raw_segments": len(transcript.segments),
                    "sliced_segments": len(sliced.segments),
                    "annotated_segments": len(annotated_raw.segments),
                    "cleaned_words_new": len(new_md.split()),
                    "old_words_sliced": len(old_md.split()),
                    "elapsed_seconds": round(elapsed, 1),
                    "report": str(out_path),
                }
            )
        except Exception as exc:  # pylint: disable=broad-except
            print(f"    ✗ {exc!r}")
            summary_rows.append({"episode_id": episode_id, "error": repr(exc)})
            continue

    summary_path = report_dir / "summary.json"
    summary_path.write_text(json.dumps(summary_rows, indent=2), encoding="utf-8")
    print(f"\nSummary: {summary_path}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
