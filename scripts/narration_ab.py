#!/usr/bin/env python
# Copyright 2025-2026 Thestill
#
# Licensed under the Apache License, Version 2.0 (the "License");
# you may not use this file except in compliance with the License.
# You may obtain a copy of the License at
#
#     http://www.apache.org/licenses/LICENSE-2.0
#
# Unless required by applicable law or agreed to in writing, software
# distributed under the License is distributed on an "AS IS" BASIS,
# WITHOUT WARRANTIES OR CONDITIONS OF ANY KIND, either express or implied.
# See the License for the specific language governing permissions and
# limitations under the License.

"""A/B the anchor voices on one briefing (spec #77 Phase 2b rubric).

Runs each voice N times on the same briefing window, computes the
deterministic register metrics from the script, asks the eval judge for
the two judged metrics (ideas per episode, stakes lines), prints a table
and a merge-gate verdict per voice, and writes every script to --out.

    python scripts/narration_ab.py --briefing <id> [--voices a b] [--runs 3] [--target 300] [--out DIR] [--no-judge]

Standalone for this pass; folding it into ``thestill eval`` (spec #53)
is a follow-up.
"""

from __future__ import annotations

import argparse
import dataclasses
import json
import statistics
import sys
from pathlib import Path
from typing import Dict, List, Optional, Sequence

from pydantic import BaseModel, Field

from thestill.core.llm_provider import LLMProvider, create_llm_provider_from_config
from thestill.evals.runner import resolve_judge
from thestill.models.inbox import INBOX_STATES_ELIGIBLE_FOR_BRIEFING
from thestill.repositories.factory import make_repositories
from thestill.services.narration import NarrationGenerator
from thestill.services.narration.models import NarrationContent
from thestill.services.narration.narration_generator import NarrationConfig
from thestill.services.narration_prompts import load_anchor_prompt
from thestill.utils.config import load_config
from thestill.utils.path_manager import PathManager

# Merge gate from PLAN.md §"Verification". A metric passes when the value
# satisfies the predicate in every run of the voice.
GATE = {
    "first_person_ratio": ("≥ 0.15", lambda v: v >= 0.15),
    "segments_with_first_person": ("all", lambda v: v >= 1.0),
    "reportage_ratio": ("≤ 0.15", lambda v: v <= 0.15),
    "ideas_mean": ("≤ 1.5", lambda v: v <= 1.5),
    "ideas_max": ("≤ 2", lambda v: v <= 2),
    "clips_with_reaction": ("all", lambda v: v >= 1.0),
    "bridges_unearned": ("0", lambda v: v == 0),
    "scare_quotes": ("0", lambda v: v == 0),
    "stakes_lines": ("all", lambda v: v >= 1.0),
    "sentence_len_p50": ("≤ 14", lambda v: v <= 14),
    "sentence_len_p90": ("≤ 24", lambda v: v <= 24),
    "stated_overshoot": ("≤ 1.15", lambda v: v <= 1.15),
    "noise_phrase_hits": ("0", lambda v: v == 0),
    "fallback": ("0", lambda v: v == 0),
}
ROWS = list(GATE) + ["quote_count", "reactions_missing", "actual_seconds"]


class _EpisodeIdeas(BaseModel):
    title: str
    ideas: int = Field(..., ge=0)


class _SegmentJudgement(BaseModel):
    section: str
    has_stakes_line: bool


class _Judgement(BaseModel):
    episodes: List[_EpisodeIdeas] = Field(default_factory=list)
    segments: List[_SegmentJudgement] = Field(default_factory=list)


_JUDGE_SYSTEM = """You grade a spoken podcast-briefing script. Answer only from the script.
- For each listed episode, count the DISTINCT ideas/claims the narrator attributes to it
  (a clip counts toward the idea it illustrates, not as a new idea). 0 if not mentioned.
- For each segment section, say whether it contains a line telling the listener why
  they personally should care (stakes), however glib.
Return JSON matching the schema."""


def _script_text(content: NarrationContent) -> str:
    lines = []
    for b in content.blocks:
        if b.kind == "quote":
            q = next((q for q in content.quotes if q.quote_id == b.quote_id), None)
            lines.append(f"[{b.section}] CLIP {b.quote_id} ({q.speaker if q else '?'}): {q.text if q else ''}")
        else:
            lines.append(f"[{b.section}] {b.kind.upper()}: {b.text}")
    return "\n".join(lines)


def _judge(provider: LLMProvider, content: NarrationContent, titles: Sequence[str]) -> Dict[str, float]:
    user = "Episodes:\n" + "\n".join(f"- {t}" for t in titles) + "\n\nScript:\n" + _script_text(content)
    result = provider.generate_structured(
        messages=[{"role": "system", "content": _JUDGE_SYSTEM}, {"role": "user", "content": user}],
        response_model=_Judgement,
        temperature=0.0,
    )
    counts = [e.ideas for e in result.episodes if e.ideas > 0]
    segs = [s for s in result.segments if s.section.startswith("segment-")]
    return {
        "ideas_mean": statistics.fmean(counts) if counts else 0.0,
        "ideas_max": float(max(counts)) if counts else 0.0,
        "stakes_lines": (sum(1 for s in segs if s.has_stakes_line) / len(segs)) if segs else 0.0,
    }


def _metrics(content: NarrationContent, judged: Optional[Dict[str, float]]) -> Dict[str, float]:
    from thestill.services.narration.register import measure_register

    s = content.stats
    r = measure_register(content.blocks)
    m: Dict[str, float] = {
        "first_person_ratio": r.first_person_ratio,
        "segments_with_first_person": (r.segments_with_first_person / r.segments) if r.segments else 0.0,
        "reportage_ratio": r.reportage_ratio,
        "clips_with_reaction": (r.clips_with_reaction / r.clips) if r.clips else 1.0,
        "bridges_unearned": float(s.bridges_unearned),
        "scare_quotes": float(r.scare_quotes),
        "sentence_len_p50": r.sentence_len_p50,
        "sentence_len_p90": r.sentence_len_p90,
        "stated_overshoot": (s.narration_words / s.stated_word_target) if s.stated_word_target else 0.0,
        "noise_phrase_hits": float(s.noise_phrase_hits),
        "fallback": 0.0 if content.mode == "narrated" else 1.0,
        "quote_count": float(s.quote_count),
        "reactions_missing": float(s.reactions_missing),
        "actual_seconds": round(s.actual_duration_seconds),
    }
    m.update(judged or {"ideas_mean": float("nan"), "ideas_max": float("nan"), "stakes_lines": float("nan")})
    return m


def _say(text: str = "", *, err: bool = False) -> None:
    (sys.stderr if err else sys.stdout).write(text + "\n")


def _fmt(v: float) -> str:
    if v != v:  # nan
        return "n/a"
    return f"{v:.2f}" if isinstance(v, float) and not v.is_integer() else f"{int(v)}"


def main(argv: Optional[Sequence[str]] = None) -> int:
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--briefing", required=True)
    ap.add_argument("--voices", nargs="+", default=["conversational_anchor", "conversational_v2"])
    ap.add_argument("--runs", type=int, default=3)
    ap.add_argument("--target", type=int, default=300, help="target spoken seconds")
    ap.add_argument("--out", type=Path, default=Path("data/narration_ab"))
    ap.add_argument("--no-judge", action="store_true")
    args = ap.parse_args(argv)

    config = load_config(None)
    repos = make_repositories(config)
    pm = PathManager(str(config.storage_path))
    provider = create_llm_provider_from_config(config)
    judge: Optional[LLMProvider] = None
    if not args.no_judge:
        try:
            judge = resolve_judge(config).provider
        except Exception as exc:  # noqa: BLE001 — a missing judge key must not block the A/B
            _say(f"judge unavailable ({exc}); using the pipeline provider as judge", err=True)
            judge = provider

    briefing = repos.briefing.get(args.briefing)
    if briefing is None:
        _say(f"briefing not found: {args.briefing}", err=True)
        return 2
    ids = repos.inbox.list_episode_ids_in_window(
        briefing.user_id,
        since=briefing.cursor_from,
        until=briefing.cursor_to,
        states=INBOX_STATES_ELIGIBLE_FOR_BRIEFING,
        read_since=briefing.created_at,
    )
    pairs = repos.podcast.get_episodes_by_ids(ids)
    episodes = [pairs[i] for i in ids if i in pairs]
    titles = [f"{p.title} — {e.title}" for p, e in episodes]
    args.out.mkdir(parents=True, exist_ok=True)

    results: Dict[str, List[Dict[str, float]]] = {}
    for voice in args.voices:
        gen = NarrationGenerator(
            path_manager=pm,
            file_storage=config.file_storage,
            llm_provider=provider,
            anchor_prompt=load_anchor_prompt(voice),
            material_max_words=config.narration_material_max_words,
            stated_target_ratio=config.narration_stated_target_ratio,
        )
        for run in range(1, args.runs + 1):
            cfg = NarrationConfig(target_duration_seconds=args.target, slug=f"{voice}-{run}", basename=f"{voice}-{run}")
            content = gen.generate(episodes, cfg)
            judged = _judge(judge, content, titles) if judge and content.mode == "narrated" else None
            m = _metrics(content, judged)
            results.setdefault(voice, []).append(m)
            stem = args.out / f"{args.briefing[:8]}-{voice}-{run}"
            stem.with_suffix(".md").write_text(content.markdown or "", encoding="utf-8")
            stem.with_suffix(".json").write_text(
                json.dumps({"stats": dataclasses.asdict(content.stats), "metrics": m}, indent=1), encoding="utf-8"
            )
            _say(
                f"{voice} run {run}: mode={content.mode} words={content.stats.narration_words} "
                f"clips={content.stats.quote_count} reactions={content.stats.reaction_count}",
                file=sys.stderr,
            )

    # Table: metric | gate | per voice mean [min–max]
    head = f"{'metric':28} {'gate':8} " + " ".join(f"{v[:22]:>26}" for v in args.voices)
    _say("\n" + head)
    _say("-" * len(head))
    for row in ROWS:
        cells = []
        for voice in args.voices:
            vals = [r[row] for r in results[voice]]
            cells.append(f"{_fmt(statistics.fmean(vals)):>10} [{_fmt(min(vals))}–{_fmt(max(vals))}]".rjust(26))
        _say(f"{row:28} {GATE.get(row, ('',))[0]:8} " + " ".join(cells))
    _say()
    for voice in args.voices:
        failed = [k for k, (_, ok) in GATE.items() if any(not ok(r[k]) for r in results[voice] if r[k] == r[k])]
        verdict = "PASS" if not failed else "FAIL: " + ", ".join(failed)
        _say(f"gate {voice}: {verdict}")
    _say(f"\nscripts written to {args.out}/")
    return 0


if __name__ == "__main__":
    sys.exit(main())
