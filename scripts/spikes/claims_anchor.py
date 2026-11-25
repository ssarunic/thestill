"""Spec #119 Phase 0 spike: re-anchor model quotes onto transcript words.

The model's quote is treated as a pointer, not as text to show. This aligns it
token-by-token against the claimed segment plus its same-speaker neighbours,
and keeps the transcript's own words for the matched span. A claim survives
when >= 80% of the quote's tokens land in one contiguous transcript window.

    ./venv/bin/python scripts/spikes/claims_anchor.py <run-dir>
"""

from __future__ import annotations

import collections
import difflib
import json
import re
import sys
from pathlib import Path

TOKEN = re.compile(r"[A-Za-z0-9']+")


def tokens(text):
    return [(m.group(0).lower().replace("'", ""), m.start(), m.end()) for m in TOKEN.finditer(text)]


def anchor(quote, segs, idx):
    """Return (first_seg_id, last_seg_id, text, coverage) or None."""
    speaker = segs[idx]["speaker"]
    lo = idx
    while lo > 0 and idx - lo < 2 and segs[lo - 1]["speaker"] == speaker:
        lo -= 1
    hi = idx
    while hi < len(segs) - 1 and hi - idx < 2 and segs[hi + 1]["speaker"] == speaker:
        hi += 1
    window = []  # (token, seg_index, start, end)
    for i in range(lo, hi + 1):
        window += [(t, i, a, b) for t, a, b in tokens(segs[i]["text"])]
    q = [t for t, _, _ in tokens(quote)]
    if not q or not window:
        return None
    sm = difflib.SequenceMatcher(None, q, [w[0] for w in window], autojunk=False)
    blocks = [b for b in sm.get_matching_blocks() if b.size]
    if not blocks:
        return None
    matched = sum(b.size for b in blocks)
    first, last = window[blocks[0].b], window[blocks[-1].b + blocks[-1].size - 1]
    span_tokens = (blocks[-1].b + blocks[-1].size) - blocks[0].b
    coverage = matched / len(q)
    # the transcript span may hold extra words (disfluencies, an elided
    # middle) but not be mostly words the model never quoted
    if coverage < 0.8 or span_tokens > 2.5 * len(q) + 10:
        return None
    parts = []
    for i in range(first[1], last[1] + 1):
        text = segs[i]["text"]
        a = first[2] if i == first[1] else 0
        b = last[3] if i == last[1] else len(text)
        parts.append(text[a:b])
    return segs[first[1]]["id"], segs[last[1]]["id"], " ".join(parts), round(coverage, 2)


def main(run_dir):
    stats = collections.Counter()
    for f in sorted(Path(run_dir).glob("*__*.json")):
        r = json.loads(f.read_text())
        segs = [
            s for s in json.loads(Path(r["file"]).read_text())["segments"] if s["kind"] in ("content", "intro", "outro")
        ]
        pos = {s["id"]: i for i, s in enumerate(segs)}
        for c in r["claims"]:
            c["anchor"] = None
            for sid in (
                c["segment_id"],
                c["segment_id"] - 1,
                c["segment_id"] + 1,
                c["segment_id"] - 2,
                c["segment_id"] + 2,
            ):
                if sid in pos and (hit := anchor(c["quote"], segs, pos[sid])):
                    c["anchor"] = dict(zip(("first_segment_id", "last_segment_id", "text", "coverage"), hit))
                    first = segs[pos[hit[0]]]
                    c["speaker"], c["start"], c["end"] = first["speaker"], first["start"], segs[pos[hit[1]]]["end"]
                    break
            stats["anchored" if c["anchor"] else "dropped"] += 1
            if c["anchor"]:
                stats["exact" if c["status"] == "ok" else "repaired"] += 1
                stats["multi_segment"] += c["anchor"]["first_segment_id"] != c["anchor"]["last_segment_id"]
        f.write_text(json.dumps(r, indent=1))
    print(dict(stats))  # noqa: T201


if __name__ == "__main__":
    main(sys.argv[1])
