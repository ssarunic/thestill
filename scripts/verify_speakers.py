#!/usr/bin/env python3
# Copyright 2025-2026 Thestill
#
# Licensed under the Apache License, Version 2.0 (the "License");
# you may not use this file except in compliance with the License.
# You may obtain a copy of the License at
#
#     http://www.apache.org/licenses/LICENSE-2.0

"""Verify speaker-label coverage on segmented-cleanup output.

For each of the N most-recent cleaned+summarised episodes, checks:

1. **No generic labels survive.** ``SPEAKER_XX`` in the segmented JSON
   means facts extraction missed that speaker and the mapping didn't
   apply. Flagged as a coverage gap.

2. **Segmented speakers align with the blended render.** Both tabs in
   the web UI draw speaker names from the same per-episode facts
   mapping, so the two speaker sets must match. A mismatch indicates a
   drift between the structured path and the blended render — which
   would be a bug in the rendering/storage layer.

Usage:
    ./venv/bin/python scripts/verify_speakers.py --last 20
    ./venv/bin/python scripts/verify_speakers.py --last 20 --storage-path ./data
"""

from __future__ import annotations

import argparse
import json
import re
import sys
from dataclasses import dataclass, field
from pathlib import Path
from typing import List, Set

PROJECT_ROOT = Path(__file__).parent.parent
sys.path.insert(0, str(PROJECT_ROOT))

from scripts.compare_cleanup import EpisodeTarget, discover_recent_episodes  # noqa: E402
from thestill.models.annotated_transcript import AnnotatedTranscript  # noqa: E402
from thestill.utils.path_manager import PathManager  # noqa: E402

_GENERIC_LABEL_RE = re.compile(r"^SPEAKER_\d+$")
# Matches either ``[MM:SS] **Name:**`` or ``**Name:**`` (no timestamp)
# lines in the legacy blended-Markdown render. Captures the speaker
# name. We intentionally skip ``[AD BREAK]`` lines.
_BLENDED_SPEAKER_RE = re.compile(r"^(?:\[\d{2}:\d{2}(?::\d{2})?\]\s+)?\*\*([^*\[]+?):\*\*")


@dataclass
class EpisodeReport:
    target: EpisodeTarget
    segmented_speakers: Set[str] = field(default_factory=set)
    blended_speakers: Set[str] = field(default_factory=set)
    generic_in_segmented: Set[str] = field(default_factory=set)
    extra_in_segmented: Set[str] = field(default_factory=set)
    missing_from_segmented: Set[str] = field(default_factory=set)
    errors: List[str] = field(default_factory=list)

    @property
    def has_generic_labels(self) -> bool:
        return bool(self.generic_in_segmented)

    @property
    def is_aligned(self) -> bool:
        """Blended speakers must be a subset of segmented, and no SPEAKER_NN.

        Equality is *not* required: the segmented pipeline preserves
        speaker labels on ``kind="ad_break"`` segments (e.g. "Ad
        Narrator"), but those segments render as ``[AD BREAK]`` in the
        blended Markdown and their speaker drops out of the blended
        regex. So extra speakers in segmented is expected and fine;
        what matters is that nothing the blended render shows is
        missing from the segmented structure.
        """
        return not self.missing_from_segmented and not self.has_generic_labels

    @property
    def has_segmented_output(self) -> bool:
        return bool(self.segmented_speakers) and not self.errors


def _extract_segmented_speakers(path: Path) -> Set[str]:
    """Return the distinct speaker labels in an AnnotatedTranscript sidecar."""
    payload = path.read_text(encoding="utf-8")
    annotated = AnnotatedTranscript.model_validate_json(payload)
    return {s.speaker for s in annotated.segments if s.speaker}


def _extract_blended_speakers(path: Path) -> Set[str]:
    """Return the distinct speaker labels in a blended-Markdown file.

    Skips ``[AD BREAK]`` markers — those use the same ``**...**``
    format but name a sponsor, not a speaker. Also skips the literal
    string ``"None"``, which is how segments with ``speaker=None``
    render (a legacy byte-identical quirk in ``to_blended_markdown``);
    the segmented JSON filters those same segments out of its speaker
    set, so the two views are actually consistent.
    """
    speakers: Set[str] = set()
    for line in path.read_text(encoding="utf-8").splitlines():
        stripped = line.strip()
        if "[AD BREAK]" in stripped:
            continue
        match = _BLENDED_SPEAKER_RE.match(stripped)
        if match:
            name = match.group(1).strip()
            if name and name != "None":
                speakers.add(name)
    return speakers


def analyse_episode(target: EpisodeTarget, path_manager: PathManager) -> EpisodeReport:
    report = EpisodeReport(target=target)

    # The segmented JSON sidecar: primary source of structured speakers.
    json_path = path_manager.clean_transcripts_dir() / target.clean_transcript_path
    json_path = json_path.with_suffix(".json")
    if not json_path.exists():
        report.errors.append(f"segmented JSON not found: {json_path}")
        return report

    try:
        report.segmented_speakers = _extract_segmented_speakers(json_path)
    except Exception as error:  # pylint: disable=broad-except
        report.errors.append(f"segmented JSON parse failed: {error}")
        return report

    # The blended-Markdown primary — whichever pipeline wrote it last.
    blended_path = path_manager.clean_transcript_file(target.clean_transcript_path)
    if blended_path.exists():
        report.blended_speakers = _extract_blended_speakers(blended_path)
    else:
        report.errors.append(f"blended MD not found: {blended_path}")

    # Coverage: any SPEAKER_NN that survived is a facts-extraction gap.
    report.generic_in_segmented = {s for s in report.segmented_speakers if _GENERIC_LABEL_RE.match(s)}

    # Alignment: blended's speakers must all appear in segmented. The
    # opposite direction is fine — segmented keeps speaker labels on
    # ad_break segments (e.g. "Ad Narrator") that the blended render
    # collapses into ``[AD BREAK]`` markers.
    report.extra_in_segmented = report.segmented_speakers - report.blended_speakers
    report.missing_from_segmented = report.blended_speakers - report.segmented_speakers

    return report


def render_report(reports: List[EpisodeReport]) -> str:
    lines: List[str] = []
    lines.append("=" * 80)
    lines.append("Speaker coverage + alignment check")
    lines.append("=" * 80)
    lines.append("")

    aligned = [r for r in reports if r.is_aligned]
    generic_hits = [r for r in reports if r.has_generic_labels]
    misaligned = [r for r in reports if r.has_segmented_output and not r.is_aligned]
    missing_segmented = [r for r in reports if not r.has_segmented_output]

    for idx, r in enumerate(reports, start=1):
        title = r.target.episode_title
        if len(title) > 60:
            title = title[:57] + "..."
        tag = "✓" if r.is_aligned else ("✗" if r.has_segmented_output else "·")
        lines.append(f"[{idx:>2}] {tag} {r.target.podcast_slug[:28]:<28}  •  {title}")
        if r.errors:
            for err in r.errors:
                lines.append(f"       ⚠️  {err}")
            continue
        lines.append(f"       segmented speakers ({len(r.segmented_speakers):>2}): {sorted(r.segmented_speakers)}")
        lines.append(f"       blended   speakers ({len(r.blended_speakers):>2}): {sorted(r.blended_speakers)}")
        if r.generic_in_segmented:
            lines.append(f"       ⚠️  GENERIC LABELS IN SEGMENTED: {sorted(r.generic_in_segmented)}")
        if r.missing_from_segmented:
            lines.append(f"       ⚠️  missing from segmented: {sorted(r.missing_from_segmented)}")
        if r.extra_in_segmented:
            # Informational — usually ad_break labels. Not a drift.
            lines.append(f"       ℹ️  extra in segmented (ad labels): {sorted(r.extra_in_segmented)}")

    lines.append("")
    lines.append("-" * 80)
    lines.append("Summary")
    lines.append("-" * 80)
    lines.append(f"  aligned (no issues):     {len(aligned):>2}/{len(reports)}")
    lines.append(f"  generic labels remain:   {len(generic_hits):>2}/{len(reports)}")
    lines.append(f"  misaligned (drift):      {len(misaligned):>2}/{len(reports)}")
    lines.append(f"  missing segmented JSON:  {len(missing_segmented):>2}/{len(reports)}")
    return "\n".join(lines)


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument(
        "--last",
        type=int,
        default=20,
        help="Number of most-recent cleaned+summarised episodes to check",
    )
    parser.add_argument(
        "--storage-path",
        default="./data",
        help="Storage root (default ./data)",
    )
    args = parser.parse_args()

    path_manager = PathManager(storage_path=args.storage_path)
    db_path = Path(args.storage_path) / "podcasts.db"
    if not db_path.exists():
        print(f"❌ Database not found at {db_path}", file=sys.stderr)
        return 1

    targets = discover_recent_episodes(db_path, args.last)
    if not targets:
        print("❌ No episodes found", file=sys.stderr)
        return 1

    print(f"Checking {len(targets)} episode(s)...")
    reports = [analyse_episode(t, path_manager) for t in targets]
    print(render_report(reports))

    # Exit non-zero when any episode had generic labels or the blended
    # render references a speaker the segmented output dropped. "Extra
    # in segmented" is informational (ad_break labels) and doesn't fail.
    has_issues = any(r.has_generic_labels or (r.has_segmented_output and r.missing_from_segmented) for r in reports)
    return 1 if has_issues else 0


if __name__ == "__main__":
    sys.exit(main())
