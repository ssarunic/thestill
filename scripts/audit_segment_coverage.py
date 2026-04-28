#!/usr/bin/env python3
# Copyright 2025-2026 Thestill
#
# Licensed under the Apache License, Version 2.0 (the "License");
# you may not use this file except in compliance with the License.
# You may obtain a copy of the License at
#
#     http://www.apache.org/licenses/LICENSE-2.0

"""Spec #28 Phase 0.2 — speaker / segment coverage audit.

Counts, across the cleaned-transcript corpus:

* How many cleaned episodes have an ``AnnotatedTranscript`` JSON sidecar
  (=> ``extract-entities`` will run normally on them).
* How many are legacy Markdown-only (``clean_transcript_json_path IS
  NULL``) and will be skipped with ``entity_extraction_status =
  'skipped_legacy'``.
* Of the segments in the JSON sidecars, what fraction have a populated
  ``speaker`` field. Spec acceptance gate: if more than 30% of
  ``content`` segments lack a speaker, ``list_quotes_by`` is unusable
  and the spec wants O1 redesigned before Phase 1 builds it.

Output is plain text on stdout, JSON when ``--json`` is passed. The
exit code is non-zero if the speaker-coverage gate fails so this can
be wired into CI later.

Usage:
    ./venv/bin/python scripts/audit_segment_coverage.py
    ./venv/bin/python scripts/audit_segment_coverage.py --json > audit.json
    ./venv/bin/python scripts/audit_segment_coverage.py --storage-path ./data
"""

from __future__ import annotations

import argparse
import json
import sys
from dataclasses import asdict, dataclass, field
from pathlib import Path
from typing import List, Optional

PROJECT_ROOT = Path(__file__).parent.parent
sys.path.insert(0, str(PROJECT_ROOT))

from thestill.models.annotated_transcript import AnnotatedTranscript  # noqa: E402
from thestill.repositories.sqlite_podcast_repository import SqlitePodcastRepository  # noqa: E402
from thestill.utils.path_manager import PathManager  # noqa: E402

# Spec acceptance gate. If a higher fraction of content segments has no
# speaker, ``list_quotes_by`` is unusable and Phase 1 must redesign O1
# first.
SPEAKER_COVERAGE_FAIL_THRESHOLD = 0.30


@dataclass
class CoverageReport:
    episodes_total: int = 0
    episodes_cleaned: int = 0
    episodes_with_json_sidecar: int = 0
    episodes_legacy_only: int = 0
    json_sidecar_load_failures: List[str] = field(default_factory=list)

    content_segments: int = 0
    content_segments_with_speaker: int = 0

    @property
    def speaker_coverage_pct(self) -> float:
        if self.content_segments == 0:
            return 0.0
        return self.content_segments_with_speaker / self.content_segments

    @property
    def speaker_gap_pct(self) -> float:
        return 1.0 - self.speaker_coverage_pct

    @property
    def gate_passes(self) -> bool:
        # No data → treat as ungated rather than as a failure. The
        # caller (or the human running this) decides whether to proceed.
        if self.content_segments == 0:
            return True
        return self.speaker_gap_pct <= SPEAKER_COVERAGE_FAIL_THRESHOLD


def audit(storage_path: str, db_path: Optional[str] = None) -> CoverageReport:
    pm = PathManager(storage_path)
    db = db_path or str(Path(storage_path) / "podcasts.db")
    repo = SqlitePodcastRepository(db_path=db)

    report = CoverageReport()

    # Paginate through every episode in the corpus. ``get_all_episodes``
    # is the most permissive listing on the repo (no state filter); we
    # ask for a generous page so a typical single-user corpus fits in
    # one round-trip.
    page_size = 1000
    offset = 0
    while True:
        rows, total = repo.get_all_episodes(limit=page_size, offset=offset)
        for podcast, episode in rows:
            report.episodes_total += 1
            if not episode.clean_transcript_path:
                continue
            report.episodes_cleaned += 1
            if not episode.clean_transcript_json_path:
                report.episodes_legacy_only += 1
                continue
            report.episodes_with_json_sidecar += 1

            sidecar_path = pm.clean_transcripts_dir() / episode.clean_transcript_json_path
            try:
                payload = json.loads(sidecar_path.read_text(encoding="utf-8"))
                annotated = AnnotatedTranscript.model_validate(payload)
            except Exception as exc:
                report.json_sidecar_load_failures.append(f"{episode.id}: {exc}")
                continue

            for segment in annotated.segments:
                if segment.kind != "content":
                    continue
                report.content_segments += 1
                if segment.speaker:
                    report.content_segments_with_speaker += 1

        offset += len(rows)
        if not rows or offset >= total:
            break

    return report


def _format_pct(value: float) -> str:
    return f"{value * 100:.1f}%"


def _print_text(report: CoverageReport) -> None:
    lines = [
        "Spec #28 Phase 0.2 — segment-coverage audit",
        "",
        f"Episodes (any state)............ {report.episodes_total}",
        f"  cleaned (have clean_transcript_path)  {report.episodes_cleaned}",
        f"    with JSON sidecar (extract OK)      {report.episodes_with_json_sidecar}",
        f"    legacy Markdown-only (skip)         {report.episodes_legacy_only}",
        "",
        f"Content segments inspected...... {report.content_segments}",
        f"  with speaker.................. {report.content_segments_with_speaker} "
        f"({_format_pct(report.speaker_coverage_pct)})",
        f"  no speaker.................... "
        f"{report.content_segments - report.content_segments_with_speaker} "
        f"({_format_pct(report.speaker_gap_pct)})",
        "",
        f"Gate (≤ {_format_pct(SPEAKER_COVERAGE_FAIL_THRESHOLD)} no-speaker): "
        f"{'PASS' if report.gate_passes else 'FAIL'}",
    ]
    if report.json_sidecar_load_failures:
        lines.append("")
        lines.append(f"WARN — {len(report.json_sidecar_load_failures)} sidecar load failures:")
        for msg in report.json_sidecar_load_failures[:10]:
            lines.append(f"  - {msg}")
        if len(report.json_sidecar_load_failures) > 10:
            lines.append(f"  ... ({len(report.json_sidecar_load_failures) - 10} more)")
    print("\n".join(lines))


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--storage-path", default="./data", help="Path to data dir (default: ./data)")
    parser.add_argument("--database-path", default=None, help="Path to podcasts.db (default: <storage>/podcasts.db)")
    parser.add_argument("--json", action="store_true", help="Emit JSON instead of plain text")
    args = parser.parse_args()

    report = audit(storage_path=args.storage_path, db_path=args.database_path)

    if args.json:
        json.dump(asdict(report), sys.stdout, indent=2, default=str)
        sys.stdout.write("\n")
    else:
        _print_text(report)

    return 0 if report.gate_passes else 2


if __name__ == "__main__":
    raise SystemExit(main())
