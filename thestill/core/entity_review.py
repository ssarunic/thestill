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

"""Entity-resolution detection queue + admin correction flow.

Two halves, both deliberately dependency-injected (repo / queue / Wikidata
client are passed in) so they unit-test without a network or an
``AppState``:

* :func:`scan_entities_for_review` — ranks *likely-wrong* resolutions so an
  admin doesn't have to stumble on them. The resolver mislinks popular
  short names to older/narrower Wikidata entities ("Anthropic" → the
  cosmology *Anthropic principle*; "Opus" → *Opus number*). Two heuristics
  catch the bulk:

  - ``surface_extends_canonical`` — a mention's ``surface_form`` is a strict
    token-subset of the resolved canonical, and the canonical adds a
    *lowercase common noun*. That lowercase-noun test is what separates
    "Anthropic" → "Anthropic **principle**" (wrong) from "Donald" → "Donald
    **Trump**" or "Palantir" → "Palantir **Technologies**" (correct
    coref/disambiguation, capitalised continuation).
  - ``p31_says_other_type`` — :func:`classify_entity_type` actively
    disagrees with the stored type.

  Ranked by mentions poisoned (blast radius). The separate "P31 unsupported
  by the stored type but classify can't retype it" class is deliberately
  *excluded*: that's an allow-set gap fixed by extending the frozensets in
  ``entity_type_rules``, not a per-entity override.

* :func:`apply_correction` — applies a blacklist / override (reusing the
  existing repo methods the CLI uses) and then triggers re-resolution of the
  poisoned mentions so the fix lands on existing data, not just future runs.
"""

from __future__ import annotations

import json
import re
from dataclasses import dataclass, field
from typing import TYPE_CHECKING, Dict, List, Optional, Tuple

from structlog import get_logger

from ..models.entities import EntityRecord, EntityType
from .entity_resolver import _build_entity_id
from .entity_type_rules import ALLOWED_P31_BY_TYPE, classify_entity_type
from .queue_manager import TaskStage

if TYPE_CHECKING:  # avoid import cost / cycles at runtime
    from ..repositories.sqlite_entity_repository import SqliteEntityRepository
    from .queue_manager import QueueManager
    from .wikidata_client import WikidataClient

logger = get_logger(__name__)

# Resolution status values worth re-resolving when a correction lands. We
# leave ``pending`` (already queued) and ``dropped`` (intentional human
# action) untouched — see ``find_mention_ids_by_surface``.
_REVISABLE_STATUSES = ("resolved", "unresolvable", "ambiguous")

# A P31 type-disagreement is strong corroboration that a resolution is
# wrong, so it boosts an entity's rank — but impact (mentions poisoned)
# stays the dominant term, so a 1500-mention bug still outranks a
# 6-mention one. A multiplier keeps that ordering; an additive bonus
# would let tiny corroborated rows leapfrog big uncorroborated ones.
_P31_DISAGREE_BOOST = 1.5

# Connector words that aren't "content" when judging a canonical extension.
_CONNECTORS = {"of", "the", "and", "for", "to", "in", "on", "de", "von", "van", "der", "a"}

_VALID_ACTIONS = ("blacklist", "force_entity", "drop", "force_unresolvable")


# ----------------------------------------------------------------------
# Detection — scan
# ----------------------------------------------------------------------


@dataclass
class ReviewFlag:
    """One likely-wrong resolution, surfaced for admin review."""

    entity_id: str
    type: str
    canonical_name: str
    qid: Optional[str]
    score: float
    kinds: List[str] = field(default_factory=list)
    evidence: Dict = field(default_factory=dict)
    # The one-click fix this flag suggests: "blacklist" (the surface should
    # not ground to ``suggested_qid``) or "review" (a type mismatch with no
    # one-click correction here — fix via retype/rules).
    suggested_action: str = "review"
    suggested_qid: Optional[str] = None


def _tokens(s: str) -> set[str]:
    return set(re.findall(r"[a-z0-9]+", (s or "").lower()))


def _concept_extension(surface: str, canonical: str) -> bool:
    """True when ``canonical`` extends ``surface`` with a *lowercase common
    noun* — the wrong-resolution tell.

    Parentheticals are stripped first ("Slack (software)" is a benign
    Wikipedia disambiguator, not a concept change) and original case is kept
    so "Anthropic principle" (lowercase ``principle`` ⇒ different concept,
    SUSPECT) is distinguished from "Donald Trump" / "Palantir Technologies"
    (capitalised proper continuation, benign).
    """
    s_tok = _tokens(surface)
    base = re.sub(r"\(.*?\)", " ", canonical)  # drop "(software)" etc.
    words = re.findall(r"[A-Za-z0-9&]+", base)
    extra = [w for w in words if w.lower() not in s_tok]
    return any(w.islower() and len(w) > 2 and w.lower() not in _CONNECTORS for w in extra)


def scan_entities_for_review(
    repo: "SqliteEntityRepository",
    *,
    limit: int = 50,
) -> List[ReviewFlag]:
    """Return the top ``limit`` likely-wrong resolutions, ranked by impact.

    Read-only and network-free: it consumes ``fetch_resolution_review_rows``
    (resolved, QID-bearing entities joined to their mention surfaces) and
    applies the two heuristics described in the module docstring.
    """
    # Group the per-(entity, surface) rows into one record per entity. The
    # entity-level columns repeat across a group's rows; the per-surface
    # counts accumulate under ``surfaces``.
    by_entity: Dict[str, dict] = {}
    for row in repo.fetch_resolution_review_rows():
        rec = by_entity.setdefault(row["entity_id"], {**row, "surfaces": []})
        rec["surfaces"].append((row["surface_form"], int(row["mention_count"])))

    flags: List[ReviewFlag] = []
    for eid, row in by_entity.items():
        canonical = row["canonical_name"]
        try:
            etype = EntityType(row["type"])
        except ValueError:  # defensive — unknown stored type
            continue
        try:
            p31 = [q for q in json.loads(row["wikidata_instance_of"] or "[]") if q]
        except (ValueError, TypeError):
            p31 = []

        surfs = row["surfaces"]
        total_mentions = sum(n for _, n in surfs)
        kinds: List[str] = []
        evidence: Dict = {}
        suggested_action = "review"
        suggested_qid: Optional[str] = None

        # Signal 1 — a mention's surface ⊊ the resolved canonical (lowercase
        # extension). The blast radius is the mentions of the extending
        # surfaces, since those are the ones grounded to the wrong entity.
        extending = [
            (s, n)
            for s, n in surfs
            if _tokens(s) and _tokens(s) < _tokens(canonical) and _concept_extension(s, canonical)
        ]
        if extending:
            affected = sum(n for _, n in extending)
            kinds.append("surface_extends_canonical")
            evidence["surfaces"] = [s for s, _ in sorted(extending, key=lambda x: -x[1])[:5]]
            evidence["affected_mentions"] = affected
            suggested_action = "blacklist"
            suggested_qid = row["wikidata_qid"]

        # Signal 2 — Wikidata P31 actively says this is a different type.
        # (The "P31 present but unsupported, classify keeps the same type"
        # case is intentionally NOT flagged — that's an allow-set gap.)
        # Distinct from ``repo.find_mistyped_entities``, which keys off the
        # GLiNER surface_label majority rather than Wikidata P31.
        p31_disagrees = False
        if p31:
            classified = classify_entity_type(p31, fallback=etype)
            if classified is not None and classified != etype:
                p31_disagrees = True
                kinds.append("p31_says_other_type")
                evidence["p31"] = p31
                evidence["p31_suggests"] = classified.value

        if not kinds:
            continue

        impact = evidence.get("affected_mentions", total_mentions)
        score = impact * (_P31_DISAGREE_BOOST if p31_disagrees else 1.0)
        flags.append(
            ReviewFlag(
                entity_id=eid,
                type=row["type"],
                canonical_name=canonical,
                qid=row["wikidata_qid"],
                score=round(score, 2),
                kinds=kinds,
                evidence=evidence,
                suggested_action=suggested_action,
                suggested_qid=suggested_qid,
            )
        )

    flags.sort(key=lambda f: f.score, reverse=True)
    return flags[:limit]


# ----------------------------------------------------------------------
# Correction — apply + re-resolve
# ----------------------------------------------------------------------


@dataclass
class CorrectionResult:
    """Outcome of :func:`apply_correction`."""

    action: str
    surface_form: str
    affected_mentions: int
    episodes_enqueued: int
    created_entity_id: Optional[str] = None
    target_entity_id: Optional[str] = None
    override_id: Optional[int] = None
    blacklist_id: Optional[int] = None
    golden_snippet: str = ""


class CorrectionError(ValueError):
    """Raised for an invalid correction request (maps to HTTP 400)."""


def apply_correction(
    *,
    repo: "SqliteEntityRepository",
    queue_manager: "QueueManager",
    wikidata_client: "Optional[WikidataClient]" = None,
    action: str,
    surface_form: str,
    episode_id: Optional[str] = None,
    wrong_qid: Optional[str] = None,
    target_qid: Optional[str] = None,
    target_entity_id: Optional[str] = None,
    reason: Optional[str] = None,
    created_by: str = "admin",
    language: str = "en",
) -> CorrectionResult:
    """Apply an admin correction, then re-resolve the mentions it poisons.

    ``action``:

    - ``blacklist`` — refuse ``surface_form → wrong_qid``. Re-resolution then
      drops the bad grounding (the surface becomes unresolvable unless a
      better candidate exists). Pair with ``force_entity`` for a clean fix.
    - ``force_entity`` — pin the surface to a correct entity. Provide either
      ``target_entity_id`` (must exist) or ``target_qid`` (minted from
      Wikidata if absent). A ``wrong_qid`` may be supplied to *also*
      blacklist the bad QID in the same call (the "redirect" combo).
    - ``drop`` / ``force_unresolvable`` — store the matching override.

    Re-resolution (all actions): the affected mentions are reset to
    ``pending`` and a ``resolve-entities`` task is enqueued per distinct
    episode (guarded against duplicates). The resolve handler re-applies the
    override + blacklist at the front of the pass, so the correction lands on
    existing data and auto-chains reindex + cooccurrence rebuild.
    """
    if action not in _VALID_ACTIONS:
        raise CorrectionError(f"invalid action {action!r}; expected one of {_VALID_ACTIONS}")
    surface_form = (surface_form or "").strip()
    if not surface_form:
        raise CorrectionError("surface_form is required")

    override_id: Optional[int] = None
    blacklist_id: Optional[int] = None
    created_entity_id: Optional[str] = None
    resolved_target_id: Optional[str] = target_entity_id

    if action == "blacklist":
        if not wrong_qid:
            raise CorrectionError("blacklist requires wrong_qid")
        blacklist_id = repo.add_blacklist_entry(surface_form=surface_form, wrong_qid=wrong_qid, reason=reason)

    elif action == "force_entity":
        if target_qid and not target_entity_id:
            resolved_target_id, created_entity_id = _ensure_entity_for_qid(
                repo=repo,
                wikidata_client=wikidata_client,
                qid=target_qid,
                fallback_name=surface_form,
                language=language,
            )
        if not resolved_target_id:
            raise CorrectionError("force_entity requires target_entity_id or target_qid")
        if repo.get_entity(resolved_target_id) is None:
            raise CorrectionError(f"unknown target entity {resolved_target_id!r}")
        # Optional redirect combo: blacklist the bad QID too.
        if wrong_qid:
            blacklist_id = repo.add_blacklist_entry(surface_form=surface_form, wrong_qid=wrong_qid, reason=reason)
        override_id = repo.add_override(
            surface_form=surface_form,
            episode_id=episode_id,
            kind="force_entity",
            entity_id=resolved_target_id,
            reason=reason,
            created_by=created_by,
        )

    else:  # drop | force_unresolvable
        override_id = repo.add_override(
            surface_form=surface_form,
            episode_id=episode_id,
            kind=action,
            reason=reason,
            created_by=created_by,
        )

    affected, episodes_enqueued = _reresolve_surface(
        repo=repo, queue_manager=queue_manager, surface_form=surface_form, episode_id=episode_id
    )

    result = CorrectionResult(
        action=action,
        surface_form=surface_form,
        affected_mentions=affected,
        episodes_enqueued=episodes_enqueued,
        created_entity_id=created_entity_id,
        target_entity_id=resolved_target_id,
        override_id=override_id,
        blacklist_id=blacklist_id,
        golden_snippet=_golden_snippet(
            action=action, surface_form=surface_form, wrong_qid=wrong_qid, target_qid=target_qid
        ),
    )
    logger.info(
        "entity_correction_applied",
        action=action,
        surface_form=surface_form,
        episode_id=episode_id,
        affected_mentions=affected,
        episodes_enqueued=episodes_enqueued,
        created_entity_id=created_entity_id,
        target_entity_id=resolved_target_id,
    )
    return result


def _ensure_entity_for_qid(
    *,
    repo: "SqliteEntityRepository",
    wikidata_client: "Optional[WikidataClient]",
    qid: str,
    fallback_name: str,
    language: str,
) -> Tuple[str, Optional[str]]:
    """Return ``(entity_id, created_entity_id_or_None)`` for a target QID.

    Reuses an entity that already carries the QID (under whatever id it was
    created with) before minting — otherwise the resolve task's duplicate-QID
    merge would later collapse the mint away, and because
    ``mention_overrides.entity_id`` is ``ON DELETE SET NULL`` the override's
    target would blank out (``override_target_missing`` on re-resolve). Only
    when no entity carries the QID do we mint one from Wikidata; the minted
    row's enrichment is then dropped so it re-enriches cleanly.
    """
    # Authoritative reuse check: any entity already grounded to this QID.
    existing_by_qid = repo.find_entity_by_qid(qid)
    if existing_by_qid is not None:
        return existing_by_qid.id, None

    if wikidata_client is None:
        raise CorrectionError("minting a target entity from a QID requires a Wikidata client")
    facts = wikidata_client.fetch_facts(qid, language=language)
    if facts is None:
        raise CorrectionError(f"Wikidata returned no entity for {qid!r}")
    # P31 comes from the payload we just fetched — avoids a second network
    # round-trip and the silent-degradation trap of ``fetch_p31`` (which
    # swallows transient errors and returns ``[]``, mis-typing the entity).
    p31 = facts.entity_refs("P31")
    entity_type = _mint_type_from_p31(p31)
    canonical = facts.label or fallback_name
    entity_id = _build_entity_id(entity_type, canonical, qid)

    # Defensive: a same-slug entity without the QID (unresolved local) — reuse.
    existing = repo.get_entity(entity_id)
    if existing is not None:
        return entity_id, None

    repo.upsert_entity(
        EntityRecord(
            id=entity_id,
            type=entity_type,
            canonical_name=canonical,
            wikidata_qid=qid,
            wikidata_instance_of=p31,
            description=facts.description,
        )
    )
    repo.delete_enrichment(entity_id)  # force fresh enrichment for the new QID
    return entity_id, entity_id


def _mint_type_from_p31(p31: List[str]) -> EntityType:
    """Pick a bucket for a *minted* entity straight from its P31 set.

    Unlike :func:`classify_entity_type` (which gates COMPANY/PRODUCT behind
    a matching GLiNER fallback to avoid trusting a mislabel), a minted
    target comes from an admin-supplied QID that is authoritative — so we
    map P31 → type directly. Precedence mirrors ``classify_entity_type``
    (PERSON, then TOPIC before COMPANY so countries/NGOs don't land in
    "companies"), defaulting to TOPIC when nothing matches.
    """
    s = {q for q in p31 if q}
    for etype in (EntityType.PERSON, EntityType.TOPIC, EntityType.COMPANY, EntityType.PRODUCT):
        if s & ALLOWED_P31_BY_TYPE[etype]:
            return etype
    return EntityType.TOPIC


def _reresolve_surface(
    *,
    repo: "SqliteEntityRepository",
    queue_manager: "QueueManager",
    surface_form: str,
    episode_id: Optional[str],
) -> Tuple[int, int]:
    """Reset poisoned mentions to pending and enqueue per-episode resolves.

    Returns ``(affected_mentions, episodes_enqueued)``.
    """
    affected = repo.find_mention_ids_by_surface(surface_form, episode_id=episode_id, statuses=_REVISABLE_STATUSES)
    if not affected:
        return 0, 0
    repo.reset_mentions_to_pending([mid for mid, _ in affected])
    episodes = sorted({ep for _, ep in affected})
    # Always enqueue, even if a resolve task is already active for the
    # episode: ``has_pending_task`` would also match a ``processing`` task
    # that has *already* snapshotted its pending mentions and so would never
    # see the rows we just reset — leaving them stuck pending. The worker's
    # per-episode mutex serialises resolve tasks and the handler is
    # idempotent, so a fresh task safely runs after the active one. Repeated
    # corrections don't pile up: once reset to pending, mentions drop out of
    # ``find_mention_ids_by_surface`` (which only sees revisable terminals).
    for ep in episodes:
        queue_manager.add_task(ep, TaskStage.RESOLVE_ENTITIES)
    logger.info(
        "entity_correction_reresolve_enqueued",
        surface_form=surface_form,
        affected_mentions=len(affected),
        episodes_enqueued=len(episodes),
    )
    return len(affected), len(episodes)


def _golden_snippet(*, action: str, surface_form: str, wrong_qid: Optional[str], target_qid: Optional[str]) -> str:
    """Build a paste-ready ``GoldenCase(...)`` for the regression eval.

    Best-effort: the admin fills the excerpt + frozen ReFinED snapshot
    (which require a real transcript / model output). This pre-fills the
    bits the correction already knows so adding a permanent regression
    guard after a fix is a copy-paste, not a from-scratch authoring task.
    See ``tests/unit/core/test_entity_resolution_golden.py``.
    """
    case_id = re.sub(r"[^a-z0-9]+", "_", surface_form.lower()).strip("_") or "case"
    if action == "blacklist" or (action == "force_entity" and wrong_qid):
        expect = 'expect_status="unresolvable",\n    expect_qid=None,'
        bl = f'\n    blacklist=(("{surface_form}", "{wrong_qid}"),),' if wrong_qid else ""
    elif action == "force_entity":
        expect = f'expect_status="resolved",\n    expect_qid="{target_qid}",'
        bl = ""
    else:  # drop | force_unresolvable
        expect = 'expect_status="unresolvable",\n    expect_qid=None,'
        bl = ""
    qid_for_refined = wrong_qid or target_qid or "Q_FILL_ME"
    return (
        "GoldenCase(\n"
        f'    id="{case_id}_{action}",\n'
        f'    surface="{surface_form}",\n'
        '    excerpt="<paste a real excerpt that mentions it>",\n'
        '    label="<person|company|product|topic>",\n'
        f'    refined={{"{surface_form}": ("{qid_for_refined}", "<title>", "ORG", 0.95)}},\n'
        f'    p31={{"{qid_for_refined}": ["<P31>"]}},{bl}\n'
        f"    {expect}\n"
        "    expect_type=EntityType.TOPIC,\n"
        f'    guards="{action} correction for {surface_form!r}",\n'
        "),"
    )
