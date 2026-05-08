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

"""ReFinED-driven entity disambiguation.

Spec #28 §1.5. Takes pending ``EntityMention`` rows, runs ReFinED
against the surrounding excerpt to map the surface form to a
Wikidata QID, returns a ``ResolutionResult`` per mention. The
``EntityRecord`` it produces is what the handler upserts into the
``entities`` table; the ``mention_id`` and resolution status drive
the per-row ``resolve_mention`` UPDATE.

Why ReFinED rather than a vector lookup or LLM:

- Per spec §"Strategy" — purpose-built for entity disambiguation
  against Wikidata, vs an LLM where the resolution is a side-effect
  of generation. Cheaper at corpus scale and more deterministic.
- Returns Wikidata QIDs natively — exactly the ``wikidata_qid``
  field on ``EntityRecord``.
- CPU works (~30s/1000 mentions); GPU is 4-5x faster.

Same lazy-load + threading.Lock + ``preloaded_model`` test seam
pattern as ``EntityExtractor`` — the resolver is held at
process scope on ``AppState.entity_resolver``.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import TYPE_CHECKING, Iterable, List, Optional, Protocol

from structlog import get_logger

from ..models.entities import EntityMention, EntityRecord, EntityType, ResolutionMethod
from ..utils.slug import generate_slug
from .entity_type_rules import classify_entity_type

if TYPE_CHECKING:
    # ``refined`` is an optional dep — imported lazily inside
    # ``_load_model`` so this module imports cleanly without it.
    from refined.inference.processor import Refined  # noqa: F401


class _P31Lookup(Protocol):
    """Structural type matching :class:`thestill.core.wikidata_client.WikidataClient`.

    Lets the resolver accept either the real client or the
    ``NullWikidataClient`` test stub without an import-time dependency.
    """

    def fetch_p31(self, qid: str) -> List[str]: ...


logger = get_logger(__name__)


# Default ReFinED model. ``wikipedia_model_with_numbers`` is the
# package's recommended starting point; ``entity_set="wikipedia"``
# restricts disambiguation to ~6M wikipedia-notable entities (sufficient
# for podcast guests and companies). Switch to ``"wikidata"`` (~33M
# entities) if recall on niche names becomes a problem — ~3x larger
# disk + memory.
DEFAULT_REFINED_MODEL = "wikipedia_model_with_numbers"
DEFAULT_ENTITY_SET = "wikipedia"

# Spec #28 §1.13.3 — minimum ReFinED confidence to accept a QID.
# Below this threshold the prediction is downgraded to ``unresolvable``.
# Tuned to 0.5 from the post-1.12 audit: lower values let through
# things like ``"Vercel" → "Vercel-Villedieu-le-Camp"`` and ``"frontier
# labs" → "Reinforcement learning"`` — both products of ReFinED grasping
# at the highest-scoring candidate when no entity in its index matched
# the surface well. 0.5 trims the long tail of bad guesses while
# preserving named-entity hits, which typically score 0.7+.
DEFAULT_MIN_QID_CONFIDENCE = 0.5


# Map ReFinED's ``coarse_type`` (used as a fallback when the GLiNER
# ``surface_label`` was not persisted on the mention) to our typed
# ``EntityType``. ReFinED's coarse types are loosely OntoNotes-style.
COARSE_TYPE_TO_ENTITY_TYPE = {
    "PER": EntityType.PERSON,
    "PERSON": EntityType.PERSON,
    "ORG": EntityType.COMPANY,
    "ORGANIZATION": EntityType.COMPANY,
    "PRODUCT": EntityType.PRODUCT,
    "GPE": EntityType.TOPIC,  # geo-political entities → topic
    "LOC": EntityType.TOPIC,
    "EVENT": EntityType.TOPIC,
    "MISC": EntityType.TOPIC,
}


# Map GLiNER's surface_label (the Phase 1.2 extractor label) to
# ``EntityType``. Direct one-to-one — the four types are the same.
SURFACE_LABEL_TO_ENTITY_TYPE = {
    "person": EntityType.PERSON,
    "company": EntityType.COMPANY,
    "product": EntityType.PRODUCT,
    "topic": EntityType.TOPIC,
}


@dataclass(frozen=True)
class ResolutionResult:
    """One mention's resolution output.

    ``entity`` is the canonical ``EntityRecord`` to upsert into the
    ``entities`` table. ``mention_id`` and ``status`` drive the
    per-row ``resolve_mention`` UPDATE. When ``status='unresolvable'``
    the ``entity`` is still populated — the handler creates a local
    slug-only entity (no QID) so future occurrences of the same
    surface form can be merged into it.

    ``method`` records *how* the resolver landed on this entity (spec
    §1.13.6) — ``direct`` for ReFinED hits, ``override`` when a
    persisted ``mention_overrides`` row forced the answer,
    ``unresolvable`` when the threshold rejected the QID, etc.
    """

    mention_id: int
    entity: EntityRecord
    status: str  # "resolved" | "unresolvable"
    method: ResolutionMethod = ResolutionMethod.DIRECT


class EntityResolver:
    """ReFinED-backed entity disambiguator.

    The model is loaded lazily on first ``resolve`` call. Subsequent
    calls reuse the in-memory model; loading takes ~30-60s and ~4-6GB
    of RAM, so callers should hold the resolver at process scope.
    """

    def __init__(
        self,
        *,
        model: str = DEFAULT_REFINED_MODEL,
        entity_set: str = DEFAULT_ENTITY_SET,
        min_qid_confidence: float = DEFAULT_MIN_QID_CONFIDENCE,
        preloaded_model: Optional["Refined"] = None,
        wikidata_client: Optional[_P31Lookup] = None,
    ):
        """``preloaded_model`` is a test seam — pass a stub or
        pre-warmed real model and ``_load_model`` becomes a no-op.

        ``wikidata_client`` enables spec #28 §5.2 P31-based bucket
        gating: when set, every resolved QID has its ``instance of``
        fetched and the type is reclassified via
        ``entity_type_rules.classify_entity_type``. ``None`` disables
        the check (current behavior — kept as default to keep the test
        fixtures network-free).
        """
        self.model_name = model
        self.entity_set = entity_set
        self.min_qid_confidence = min_qid_confidence
        self._model: Optional["Refined"] = preloaded_model
        self._wikidata_client = wikidata_client

    # ------------------------------------------------------------------
    # Public API
    # ------------------------------------------------------------------

    def resolve(
        self,
        mentions: List[EntityMention],
        *,
        is_blacklisted=None,
    ) -> List[ResolutionResult]:
        """Resolve a list of pending mentions in one pass.

        Each mention is resolved independently against its
        ``quote_excerpt``. Mentions sharing a ``surface_form`` are NOT
        deduplicated here — context matters (e.g. "Apple" the company
        vs "apple" the fruit) and the per-mention `excerpt` gives
        ReFinED the disambiguation hint it needs.

        ``is_blacklisted`` (spec §1.13.7) is an optional callable
        ``(surface_form, qid) -> bool`` consulted for every QID candidate
        before we accept it. The resolver itself doesn't read SQLite —
        the handler injects the lookup so the resolver stays a pure
        model wrapper.
        """
        if not mentions:
            return []
        self._load_model()
        results: List[ResolutionResult] = []
        for mention in mentions:
            try:
                result = self._resolve_one(mention, is_blacklisted=is_blacklisted)
            except Exception:
                logger.exception(
                    "refined_resolve_failed",
                    mention_id=mention.id,
                    surface_form=mention.surface_form,
                )
                result = self._unresolvable_result(mention)
            results.append(result)
        logger.info(
            "entity_resolution_complete",
            mentions=len(mentions),
            resolved=sum(1 for r in results if r.status == "resolved"),
            unresolvable=sum(1 for r in results if r.status == "unresolvable"),
        )
        return results

    # ------------------------------------------------------------------
    # Internals
    # ------------------------------------------------------------------

    def _load_model(self) -> None:
        if self._model is not None:
            return
        try:
            from refined.inference.processor import Refined
        except ImportError as exc:  # pragma: no cover — env-specific
            raise RuntimeError(
                "refined is not installed — install the entities extra: " 'pip install -e ".[entities]"'
            ) from exc
        logger.info("refined_model_loading", model=self.model_name, entity_set=self.entity_set)
        self._model = Refined.from_pretrained(
            model_name=self.model_name,
            entity_set=self.entity_set,
        )
        logger.info("refined_model_loaded")

    def _resolve_one(self, mention: EntityMention, *, is_blacklisted=None) -> ResolutionResult:
        """Run ReFinED over the mention's excerpt and pick the
        prediction whose surface span best matches ``surface_form``.

        The excerpt is small (≤2× ``QUOTE_EXCERPT_WINDOW`` chars =
        ~400 chars) so the per-mention call is cheap. We don't pass
        ReFinED the full segment text — the excerpt already contains
        ±200 chars of disambiguation context.
        """
        spans = self._model.process_text(mention.quote_excerpt)
        match = _pick_best_span(spans, mention.surface_form)
        if match is None or match.predicted_entity is None:
            return self._unresolvable_result(mention)

        wikidata_qid = getattr(match.predicted_entity, "wikidata_entity_id", None)
        if not wikidata_qid:
            return self._unresolvable_result(mention)

        # Spec §1.13.3 — confidence floor. ReFinED's Span objects
        # expose ``.entity_linking_model_confidence_score`` for
        # numeric-aware models; older builds expose ``.confidence``.
        score = _extract_confidence(match)
        if score is not None and score < self.min_qid_confidence:
            logger.info(
                "refined_low_confidence_rejected",
                surface_form=mention.surface_form,
                qid=wikidata_qid,
                score=round(score, 3),
                threshold=self.min_qid_confidence,
            )
            return self._unresolvable_result(mention)

        # Spec §1.13.7 — blacklist negative cache. If a human has
        # already said "no, this surface should never resolve to this
        # QID", honor it.
        if is_blacklisted is not None and is_blacklisted(mention.surface_form, wikidata_qid):
            logger.info(
                "refined_blacklisted_rejected",
                surface_form=mention.surface_form,
                qid=wikidata_qid,
            )
            return self._unresolvable_result(mention)

        canonical_name = (
            getattr(match.predicted_entity, "wikipedia_entity_title", None)
            or getattr(match.predicted_entity, "human_readable_name", None)
            or mention.surface_form
        )
        fallback_type = self._infer_entity_type(mention, getattr(match, "coarse_type", None))
        # Spec #28 §5.2 — Wikidata P31 gating. Re-bucket entities whose
        # ``instance of`` contradicts the GLiNER/coarse-type guess
        # (e.g. countries that GLiNER labelled "company"). When the
        # client isn't injected we fall through with the unchecked
        # type — same behavior as before this commit.
        p31_qids: List[str] = []
        if self._wikidata_client is not None and wikidata_qid:
            p31_qids = self._wikidata_client.fetch_p31(wikidata_qid)
            classified = classify_entity_type(p31_qids, fallback_type)
            if classified is not None and classified != fallback_type:
                logger.info(
                    "entity_type_reclassified",
                    surface_form=mention.surface_form,
                    qid=wikidata_qid,
                    from_type=fallback_type.value,
                    to_type=classified.value,
                    p31=p31_qids,
                )
            entity_type = classified or fallback_type
        else:
            entity_type = fallback_type
        entity_id = _build_entity_id(entity_type, canonical_name, wikidata_qid)
        return ResolutionResult(
            mention_id=mention.id,  # type: ignore[arg-type]  # always set when read from DB
            entity=EntityRecord(
                id=entity_id,
                type=entity_type,
                canonical_name=canonical_name,
                wikidata_qid=wikidata_qid,
                aliases=[mention.surface_form] if _is_plausible_alias(mention.surface_form, canonical_name) else [],
                description=getattr(match.predicted_entity, "description", None),
                wikidata_instance_of=p31_qids,
            ),
            status="resolved",
            method=ResolutionMethod.DIRECT,
        )

    def _unresolvable_result(self, mention: EntityMention) -> ResolutionResult:
        """Build the local-slug fallback entity.

        Spec §1.5: "create local ``entity_id`` for unresolved entities
        (slugified surface form)." The mention's ``resolution_status``
        flips to ``unresolvable`` but we still produce an
        ``EntityRecord`` so future occurrences of the same surface
        form land in the same local entity. Phase 1.6's alias-merge
        nightly job collapses these into resolved entities once a QID
        becomes available.
        """
        entity_type = self._infer_entity_type(mention, coarse_type=None)
        entity_id = _build_entity_id(entity_type, mention.surface_form, qid=None)
        return ResolutionResult(
            mention_id=mention.id,  # type: ignore[arg-type]
            entity=EntityRecord(
                id=entity_id,
                type=entity_type,
                canonical_name=mention.surface_form,
                wikidata_qid=None,
                aliases=[],
            ),
            status="unresolvable",
            method=ResolutionMethod.UNRESOLVABLE,
        )

    def _infer_entity_type(self, mention: EntityMention, coarse_type: Optional[str]) -> EntityType:
        """Prefer the GLiNER label persisted with the mention; fall
        back to ReFinED's coarse type; default to ``topic`` when both
        signals are absent or unmappable.
        """
        if mention.surface_label:
            mapped = SURFACE_LABEL_TO_ENTITY_TYPE.get(mention.surface_label.lower())
            if mapped is not None:
                return mapped
        if coarse_type:
            mapped = COARSE_TYPE_TO_ENTITY_TYPE.get(coarse_type.upper())
            if mapped is not None:
                return mapped
        return EntityType.TOPIC


def _extract_confidence(span) -> Optional[float]:
    """Pull a single confidence number out of a ReFinED Span.

    ReFinED's API surface drifts between releases — try several
    attribute names in priority order and stop on the first numeric
    hit. Returns ``None`` when nothing is exposed (older builds);
    callers treat that as "skip the floor check" rather than reject.
    """
    for attr in (
        "entity_linking_model_confidence_score",
        "el_confidence",
        "confidence",
        "score",
    ):
        value = getattr(span, attr, None)
        if value is None:
            continue
        try:
            return float(value)
        except (TypeError, ValueError):
            continue
    return None


def _pick_best_span(spans, surface_form: str):
    """Pick the ReFinED span whose ``.text`` best matches the
    mention's ``surface_form``. Exact match wins; otherwise the span
    with the highest character overlap *covering at least half of
    the surface form* wins. Returns ``None`` when no span passes the
    coverage floor — better an unresolvable mention than a wildly
    wrong one (e.g. ``"consumer preferences"`` resolving to
    ``Henry Ford`` because the two phrases happen to share the
    2-character substring ``"en"``).
    """
    if not spans:
        return None
    target = surface_form.lower().strip()
    if not target:
        return None
    exact = [s for s in spans if (s.text or "").lower().strip() == target]
    if exact:
        return exact[0]
    overlaps = [(s, _char_overlap(s.text or "", target)) for s in spans]
    # Require the longest common substring to cover at least half of
    # the target. ``o * 2 >= len(target)`` is integer-safe and rejects
    # incidental coincidences like ``"en"`` shared between two
    # otherwise-unrelated phrases.
    overlaps = [(s, o) for s, o in overlaps if o > 0 and o * 2 >= len(target)]
    if not overlaps:
        return None
    overlaps.sort(key=lambda pair: pair[1], reverse=True)
    return overlaps[0][0]


def _is_plausible_alias(surface_form: str, canonical_name: str) -> bool:
    """Defense-in-depth: only persist ``surface_form`` as an alias of
    ``canonical_name`` when the two share lexical content. Stops the
    resolver from quietly recording wildly unrelated phrases as
    aliases when ReFinED returns a low-confidence match — historical
    contamination case: ``"consumer preferences"`` was stored as an
    alias of ``Henry Ford`` because both phrases co-occurred in one
    excerpt. After the ``_pick_best_span`` fix this should not happen
    in the first place; this guard is the second line of defense.

    Returns ``True`` when the alias is worth keeping:
    - one is a substring of the other (case-insensitive), OR
    - they share at least one whitespace token

    Returns ``False`` for identical strings (no alias needed) and
    for empty strings.
    """
    s = surface_form.lower().strip()
    c = canonical_name.lower().strip()
    if not s or not c or s == c:
        return False
    if s in c or c in s:
        return True
    return bool(set(s.split()) & set(c.split()))


def _char_overlap(a: str, b: str) -> int:
    """Length of the longest common substring (used for span-pick
    fallback only). Cheap O(n*m) DP — fine for the ≤30-char strings
    we're comparing.
    """
    a_lower = a.lower()
    b_lower = b.lower()
    if a_lower == b_lower:
        return len(a_lower)
    n, m = len(a_lower), len(b_lower)
    if n == 0 or m == 0:
        return 0
    prev = [0] * (m + 1)
    best = 0
    for i in range(1, n + 1):
        curr = [0] * (m + 1)
        for j in range(1, m + 1):
            if a_lower[i - 1] == b_lower[j - 1]:
                curr[j] = prev[j - 1] + 1
                if curr[j] > best:
                    best = curr[j]
        prev = curr
    return best


def _build_entity_id(entity_type: EntityType, canonical_name: str, qid: Optional[str]) -> str:
    """Produce ``"{type}:{slug}"``.

    Slug source preference:
    1. Slug of canonical_name when it produces something useful
    2. ``q{qid}`` when slug degrades to ``unnamed`` (unicode-only
       surface forms transliterate to empty)
    3. ``q{qid}`` directly (lowercase) when ``canonical_name`` is
       empty or whitespace-only

    The QID-derived id keeps disambiguation stable across surface-form
    changes — re-resolving the same canonical entity later doesn't
    create a duplicate row even if its ``canonical_name`` shifted.
    """
    base_slug = generate_slug(canonical_name)
    if base_slug == "unnamed" and qid:
        base_slug = qid.lower()
    return f"{entity_type.value}:{base_slug}"
