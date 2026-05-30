# Copyright 2025-2026 Thestill
#
# Licensed under the Apache License, Version 2.0 (the "License").

"""Throwaway prototype of ``scan_entities_for_review()`` (see review-queue sketch).

Read-only. Scans the live ``data/podcasts.db`` and prints a ranked
"needs review" queue using cheap field comparisons — no model load.
This exists to *see what the heuristics surface today* before deciding
whether to build the real feature; it is not wired into the app.

Run:  ./venv/bin/python scripts/prototype_entity_review.py
"""

from __future__ import annotations

import json
import re
import sqlite3
from collections import defaultdict
from dataclasses import dataclass, field
from typing import Optional

from thestill.core.entity_type_rules import ALLOWED_P31_BY_TYPE, classify_entity_type
from thestill.models.entities import EntityType

DB = "data/podcasts.db"
TOP_N = 30

# Extra canonical tokens that don't make a resolution "wrong" — corporate
# suffixes / disambiguators. If the canonical's only extra tokens are
# these, "surface ⊊ canonical" is benign ("Tesla" → "Tesla, Inc.").
_BENIGN_EXTRA = {
    "inc",
    "incorporated",
    "corp",
    "corporation",
    "ltd",
    "limited",
    "llc",
    "co",
    "company",
    "gmbh",
    "group",
    "holdings",
    "plc",
    "sa",
    "ag",
    "the",
}


def _tokens(s: str) -> set[str]:
    return set(re.findall(r"[a-z0-9]+", (s or "").lower()))


# Connectors that aren't "content" words when judging an extension.
_CONNECTORS = {"of", "the", "and", "for", "to", "in", "on", "de", "von", "van", "der", "a"}


def _concept_extension(surface: str, canonical: str) -> bool:
    """True when ``canonical`` extends ``surface`` with a *lowercase common
    noun* — the wrong-resolution tell.

    Distinguishes "Anthropic" -> "Anthropic principle" (lowercase 'principle'
    => a different concept, SUSPECT) from "Donald" -> "Donald Trump" or
    "Palantir" -> "Palantir Technologies" (capitalized proper continuation)
    and "Slack" -> "Slack (software)" (parenthetical disambiguator) — both
    benign. We strip parentheticals and keep original case to decide.
    """
    s_tok = _tokens(surface)
    base = re.sub(r"\(.*?\)", " ", canonical)  # drop "(software)" etc.
    words = re.findall(r"[A-Za-z0-9&]+", base)
    extra = [w for w in words if w.lower() not in s_tok]
    return any(w.islower() and len(w) > 2 and w.lower() not in _CONNECTORS for w in extra)


@dataclass
class Flag:
    entity_id: str
    type: str
    canonical: str
    qid: Optional[str]
    score: float = 0.0
    kinds: list[str] = field(default_factory=list)
    evidence: dict = field(default_factory=dict)

    def add(self, kind: str, weight: float, **ev) -> None:
        self.kinds.append(kind)
        self.score = min(1.0, self.score + weight)
        self.evidence.update(ev)


def main() -> None:
    con = sqlite3.connect(DB)
    con.row_factory = sqlite3.Row

    # --- load resolved entities that carry a QID + P31 ---
    entities = {
        r["id"]: r
        for r in con.execute(
            "SELECT id, type, canonical_name, wikidata_qid, wikidata_instance_of, aliases " "FROM entities"
        )
    }
    flags: dict[str, Flag] = {}

    def flag_for(eid: str) -> Flag:
        e = entities[eid]
        return flags.setdefault(eid, Flag(eid, e["type"], e["canonical_name"], e["wikidata_qid"]))

    # --- signal 1: P31 not in the entity's own allowed set ---------------
    for eid, e in entities.items():
        if not e["wikidata_qid"]:
            continue
        p31 = [q for q in json.loads(e["wikidata_instance_of"] or "[]") if q]
        if not p31:
            continue
        etype = EntityType(e["type"])
        allowed = ALLOWED_P31_BY_TYPE[etype]
        if not (set(p31) & allowed):
            # P31 carries signal but none supports the stored bucket.
            suggested = classify_entity_type(p31, fallback=etype)
            disagree = suggested is not None and suggested != etype
            flag_for(eid).add(
                "p31_says_other_type" if disagree else "p31_unsupported_type",
                0.6 if disagree else 0.45,
                p31=p31,
                p31_suggests=suggested.value if suggested else None,
            )

    # --- signal 2: a mention's surface ⊊ the resolved canonical ----------
    # "Anthropic" (mention) resolved to entity "Anthropic principle".
    pairs = con.execute(
        "SELECT entity_id, surface_form, COUNT(*) n FROM entity_mentions "
        "WHERE resolution_status='resolved' AND entity_id IS NOT NULL "
        "GROUP BY entity_id, surface_form"
    )
    extends: dict[str, list[tuple[str, int]]] = defaultdict(list)
    for r in pairs:
        e = entities.get(r["entity_id"])
        if not e:
            continue
        s_tok, c_tok = _tokens(r["surface_form"]), _tokens(e["canonical_name"])
        if not s_tok or not (s_tok < c_tok):  # proper subset only
            continue
        if _concept_extension(r["surface_form"], e["canonical_name"]):
            extends[r["entity_id"]].append((r["surface_form"], r["n"]))
    for eid, occ in extends.items():
        total = sum(n for _, n in occ)
        flag_for(eid).add(
            "surface_extends_canonical",
            0.5,
            surfaces=[s for s, _ in occ][:5],
            mention_count=total,
        )

    # --- report A: WRONG-RESOLUTION queue, ranked by impact --------------
    # The override-worthy queue: a mention's surface resolved to a
    # canonical that extends it with meaningful words ("Anthropic" ->
    # "Anthropic principle"). Ranked by mentions poisoned, because that's
    # the blast radius of the fix. ``p31_says_other_type`` co-firing is a
    # strong corroborator, so it bumps the row.
    wrong = [f for f in flags.values() if "surface_extends_canonical" in f.kinds]
    for f in wrong:
        f.score = f.evidence.get("mention_count", 0) + (500 if "p31_says_other_type" in f.kinds else 0)
    wrong.sort(key=lambda f: f.score, reverse=True)
    print(f"\n=== A. WRONG-RESOLUTION QUEUE (top {TOP_N} of {len(wrong)}) ===")
    print("    ranked by # mentions poisoned; (+) = P31 also disagrees\n")
    for f in wrong[:TOP_N]:
        corrob = " (+P31 disagrees)" if "p31_says_other_type" in f.kinds else ""
        print(
            f"  {f.evidence['mention_count']:>5}x  {f.evidence['surfaces']} -> "
            f"{f.canonical!r} [{f.type}/{f.qid}]{corrob}"
        )

    # --- report A2: pure type-hygiene (allow-set gaps, NOT overrides) -----
    gaps = [f for f in flags.values() if f.kinds == ["p31_unsupported_type"]]
    print(f"\n=== A2. TYPE/ALLOW-SET GAPS — {len(gaps)} entities ===")
    print("    P31 unsupported by stored type but classify can't retype it.")
    print("    Fix = expand the P31 frozensets, not per-entity overrides.\n")
    missing_p31: dict[str, int] = defaultdict(int)
    for f in gaps:
        for q in f.evidence.get("p31", []):
            missing_p31[q] += 1
    for q, n in sorted(missing_p31.items(), key=lambda kv: kv[1], reverse=True)[:12]:
        print(f"  {n:>4}  entities have unmapped P31 {q}")

    # --- report B: fragmentation clusters (QID-less locals) --------------
    stems: dict[str, list[str]] = defaultdict(list)
    for eid, e in entities.items():
        if e["wikidata_qid"]:
            continue
        toks = re.findall(r"[a-z0-9]+", e["canonical_name"].lower())
        if toks:
            stems[toks[0]].append(e["canonical_name"])
    big = sorted(
        ((stem, names) for stem, names in stems.items() if len(names) >= 6),
        key=lambda kv: len(kv[1]),
        reverse=True,
    )
    print(f"\n=== B. FRAGMENTATION CLUSTERS (QID-less, >=6 variants) — top 15 ===\n")
    for stem, names in big[:15]:
        resolved_sibling = any(
            entities[eid]["wikidata_qid"]
            for eid, e in entities.items()
            if re.findall(r"[a-z0-9]+", e["canonical_name"].lower())[:1] == [stem]
        )
        tag = "  <-- has a resolved sibling w/ QID" if resolved_sibling else ""
        print(f"  '{stem}': {len(names)} local variants{tag}")
        print(f"        e.g. {names[:6]}")

    # --- report C: ambiguous backlog -------------------------------------
    amb = con.execute(
        "SELECT surface_form, COUNT(*) n FROM entity_mentions "
        "WHERE resolution_status='ambiguous' GROUP BY surface_form "
        "ORDER BY n DESC LIMIT 15"
    ).fetchall()
    print(f"\n=== C. AMBIGUOUS BACKLOG (surface_form) — top 15 ===\n")
    if not amb:
        print("  (none)")
    for r in amb:
        print(f"  {r['n']:>4}x  {r['surface_form']!r}")


if __name__ == "__main__":
    main()
