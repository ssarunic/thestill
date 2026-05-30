# Copyright 2025-2026 Thestill
#
# Licensed under the Apache License, Version 2.0 (the "License");
# you may not use this file except in compliance with the License.
# You may obtain a copy of the License at
#
#     http://www.apache.org/licenses/LICENSE-2.0

"""Spec #28 §5.2 — REST surface for the episode-page entity UX.

Two endpoints:

- ``GET /api/episodes/{episode_id}/entities`` — every resolved
  ``entity_mention`` for the episode joined with its canonical
  ``EntityRecord``, plus the per-entity aggregate the right rail and
  key-entities strip render. Used by ``EntityRail``,
  ``KeyEntitiesStrip``, ``EntityHighlight`` and ``EntityFilterBar``.
- ``GET /api/entities/{type}/{id_slug}`` — entity summary
  (``EntityRecord`` + mention_count + cooccurring + recent_mentions).
  Used by the entity page (Phase 5.1) and the hover card.

Both wrap ``SqliteEntityRepository`` methods that already exist; this
module is the FastAPI shell that gives the React frontend a reachable
URL.
"""

from __future__ import annotations

import math
from collections import defaultdict
from typing import List, Literal, Optional

from fastapi import APIRouter, Depends, HTTPException, Query
from pydantic import BaseModel
from structlog import get_logger

from ...core.entity_review import CorrectionError, apply_correction, scan_entities_for_review
from ...core.wikidata_client import WikidataClient
from ...models.enrichment import EnrichmentUnavailable, EntityAffiliation, EntityFact
from ...models.user import User
from ..dependencies import AppState, get_app_state, require_auth
from ..responses import api_response

logger = get_logger(__name__)
router = APIRouter()


# ---------------------------------------------------------------------------
# Wire types
# ---------------------------------------------------------------------------


class EntityRef(BaseModel):
    """Slim reference shape — name + id is enough to render a chip."""

    id: str
    type: str
    canonical_name: str
    wikidata_qid: Optional[str] = None


class MentionLite(BaseModel):
    """One ``entity_mention`` row, scoped to an episode.

    Trimmed to what the reader needs: timestamp + segment_id (so the
    transcript viewer can scroll/highlight the segment) + speaker +
    confidence. The full ``EntityMention`` carries fields irrelevant
    to the reader (extractor version, resolution_method, etc.).
    """

    id: int
    entity_id: str
    segment_id: int
    start_ms: int
    end_ms: int
    speaker: Optional[str] = None
    role: Optional[str] = None  # host | guest | mentioned | self | speaking
    surface_form: str
    quote_excerpt: str
    confidence: float
    sentiment: Optional[float] = None


class EpisodeEntity(BaseModel):
    """One entity that appears in this episode, with all its mentions."""

    entity: EntityRef
    mention_count: int
    first_mention_ms: int  # earliest start_ms across this entity's mentions
    # ``host`` / ``guest`` / ``recurring`` / ``unknown`` — derived from
    # podcast-level + episode-level anchor lists, NOT from the mention's
    # ``role`` field. Spec §1.13.1: host/guest is a property of the
    # entity↔podcast relationship, not the mention.
    speaker_kind: Literal["host", "guest", "recurring", "unknown"]
    # Spec #28 §5.2 — composite relevance score used to sort the rail.
    # Combines mention_count, speaker_kind weight, log(time_spread), and
    # average mention confidence so a 2× mention by a host across the
    # whole episode outranks a 5× mention buried in one tangent. Exposed
    # on the wire so the frontend can render diagnostics or filter
    # client-side without re-deriving the formula.
    salience: float = 0.0
    mentions: List[MentionLite]


class EpisodeEntitiesResponse(BaseModel):
    episode_id: str
    podcast_id: str
    entities: List[EpisodeEntity]


# Spec #28 §5.2 — relative weights used in the salience composite.
# Tuned so the final score keeps mention_count as the dominant term
# (the rail is still "the things mentioned most") while letting hosts
# and broadly-referenced entities float above raw-count peers.
_SPEAKER_KIND_WEIGHT = {
    "host": 1.5,
    "guest": 1.5,
    "recurring": 1.2,
    "unknown": 1.0,
}
# Cap on log(time-spread-seconds) used to normalize the spread term to
# roughly [0, 1]. ln(3600) ≈ 8.2 — covers a 1h episode; longer episodes
# saturate without runaway influence.
_TIME_SPREAD_LOG_CAP = math.log(3600.0)


def _compute_salience(
    *,
    speaker_kind: str,
    mentions: list,
) -> float:
    """Spec #28 §5.2 salience composite.

    ``salience = mention_count × role_weight × (1 + spread_factor) × avg_confidence``

    - ``mention_count``: dominant term — the rail still sorts roughly
      by frequency.
    - ``role_weight``: hosts/guests/recurring get a multiplicative boost
      so participant entities don't lose to high-frequency one-off names.
    - ``spread_factor``: ``ln(1 + spread_seconds) / ln(3600)``, capped at
      1.0 — rewards entities mentioned across the episode over those
      clustered in one segment.
    - ``avg_confidence``: down-weights entities whose mentions are
      borderline (NER often emits 0.5–0.65 false positives).

    Returns 0.0 when ``mentions`` is empty (defensive — shouldn't
    happen because grouped entries always carry at least one mention).
    """
    if not mentions:
        return 0.0
    role_weight = _SPEAKER_KIND_WEIGHT.get(speaker_kind, 1.0)
    if len(mentions) >= 2:
        spread_seconds = (mentions[-1].start_ms - mentions[0].start_ms) / 1000.0
        spread_factor = math.log1p(max(spread_seconds, 0.0)) / _TIME_SPREAD_LOG_CAP
        spread_factor = min(spread_factor, 1.0)
    else:
        spread_factor = 0.0
    avg_confidence = sum(m.confidence for m in mentions) / len(mentions)
    return len(mentions) * role_weight * (1.0 + spread_factor) * avg_confidence


class EntityCooccurrenceRef(BaseModel):
    entity: EntityRef
    episode_count: int
    last_seen_at: Optional[str] = None


class CitationRow(BaseModel):
    """Citation-shaped row for ``recent_mentions`` (Strategy §4)."""

    episode_id: str
    podcast_id: str
    podcast_slug: Optional[str] = None
    episode_slug: Optional[str] = None
    podcast_title: str
    episode_title: str
    published_at: Optional[str] = None
    start_ms: int
    end_ms: int
    speaker: Optional[str] = None
    quote: str
    surface_form: str
    # Spec #28 §5.1 — entity page must seek the FloatingPlayer inline
    # when a quote timestamp is clicked. Without the audio URL the page
    # has to fall back to a full navigation to /podcasts/.../episodes/...
    # which loses the user's place on the entity page.
    audio_url: Optional[str] = None
    image_url: Optional[str] = None
    duration: Optional[float] = None


class HostedPodcastRef(BaseModel):
    """Podcast where this entity is anchored as host or recurring."""

    podcast_id: str
    podcast_slug: Optional[str] = None
    podcast_title: str
    episode_count: int


class GuestEpisodeRef(BaseModel):
    """Episode where this entity appears as a guest anchor."""

    episode_id: str
    episode_slug: Optional[str] = None
    episode_title: str
    podcast_id: str
    podcast_slug: Optional[str] = None
    podcast_title: str
    published_at: Optional[str] = None


class EntityEnrichmentWire(BaseModel):
    """Spec #45 Tier 0 — Wikidata/Wikipedia display data for the entity page.

    A projection of the domain :class:`EntityEnrichment` that drops the
    provenance fields (status/timestamps/retry_after) the frontend doesn't
    need. ``facts`` / ``affiliations`` reuse the domain models verbatim —
    they carry no server-only fields, so a separate wire type would just be
    a maintenance hazard. Omitted (``enrichment: null``) when the entity has
    no QID or hasn't been enriched yet; the frontend degrades to the base
    layout.
    """

    image_url: Optional[str] = None
    image_attribution: Optional[str] = None
    image_license: Optional[str] = None
    headline: Optional[str] = None
    wikipedia_extract: Optional[str] = None
    wikipedia_url: Optional[str] = None
    facts: List[EntityFact] = []
    affiliations: List[EntityAffiliation] = []


class MostDiscussedRef(BaseModel):
    """Spec #45 — a podcast ranked by how often it mentions this entity."""

    podcast_id: str
    podcast_slug: Optional[str] = None
    podcast_title: str
    mention_count: int


class EntitySummaryResponse(BaseModel):
    entity: EntityRef
    aliases: List[str]
    description: Optional[str] = None
    mention_count: int
    cooccurring: List[EntityCooccurrenceRef]
    recent_mentions: List[CitationRow]
    # Spec #28 §1.13.1: host/guest is an entity↔podcast/episode anchor,
    # not a mention. Surface anchors separately so a host who never says
    # their own name still renders their affiliation.
    hosts_podcasts: List[HostedPodcastRef] = []
    recurring_podcasts: List[HostedPodcastRef] = []
    guest_episodes: List[GuestEpisodeRef] = []
    # Spec #45 — Tier-0 enrichment + "most discussed on". Both optional so
    # the page renders unchanged for un-enriched entities.
    most_discussed_on: List[MostDiscussedRef] = []
    enrichment: Optional[EntityEnrichmentWire] = None


# ---------------------------------------------------------------------------
# Episode-scoped: GET /api/episodes/{episode_id}/entities
# ---------------------------------------------------------------------------


@router.get(
    "/episodes/{episode_id}/entities",
    response_model=EpisodeEntitiesResponse,
)
def get_episode_entities(
    episode_id: str,
    min_confidence: float = Query(
        0.0,
        ge=0.0,
        le=1.0,
        description="Drop mentions with confidence below this floor.",
    ),
    state: AppState = Depends(get_app_state),
) -> EpisodeEntitiesResponse:
    """Resolved mentions for one episode, grouped by entity.

    Returns one ``EpisodeEntity`` per distinct entity that appears in
    the episode, with every mention attached. The frontend slices the
    list (top-N for the strip; full set for the rail and inline
    highlights). ``min_confidence`` is the gate behind the reader's
    "highlight vs plain text" rule (spec §5.2 visual rules).
    """
    episode_lookup = state.repository.get_episode(episode_id)
    if episode_lookup is None:
        raise HTTPException(status_code=404, detail=f"Episode not found: {episode_id}")
    podcast, _episode = episode_lookup

    entity_repo = state.entity_repository
    # find_mentions(episode_id=...) returns every resolved mention for
    # the episode joined with podcast/episode/entity. limit is a hard
    # cap inside the repo — bump it well above any plausible per-episode
    # mention count so we never silently truncate the rail.
    contexts = entity_repo.find_mentions(episode_id=episode_id, limit=5000)

    anchors = entity_repo.get_podcast_anchors(podcast.id)
    host_ids = set(anchors.get("hosts", []))
    recurring_ids = set(anchors.get("recurring", []))
    guest_ids = set(entity_repo.get_episode_anchors(episode_id)) - host_ids - recurring_ids

    grouped: dict[str, List[MentionLite]] = defaultdict(list)
    entity_records: dict[str, EntityRef] = {}

    for ctx in contexts:
        m = ctx.mention
        if m.confidence < min_confidence:
            continue
        if not m.entity_id:
            # Defensive: find_mentions filters to resolution_status='resolved'
            # which implies entity_id is set, but the column is nullable so
            # static type-checking can't see that. Skip rather than panic.
            continue
        grouped[m.entity_id].append(
            MentionLite(
                id=m.id or 0,
                entity_id=m.entity_id,
                segment_id=m.segment_id,
                start_ms=m.start_ms,
                end_ms=m.end_ms,
                speaker=m.speaker,
                role=m.role.value if m.role else None,
                surface_form=m.surface_form,
                quote_excerpt=m.quote_excerpt,
                confidence=m.confidence,
                sentiment=m.sentiment,
            )
        )
        if m.entity_id not in entity_records and ctx.entity_type and ctx.entity_canonical_name:
            entity_records[m.entity_id] = EntityRef(
                id=m.entity_id,
                type=ctx.entity_type,
                canonical_name=ctx.entity_canonical_name,
            )

    # ``ctx`` doesn't carry wikidata_qid; backfill from the entities
    # table for any entity we'll surface. One round-trip per distinct
    # entity, but the per-episode entity count is small (10–50 typical).
    for entity_id, ref in entity_records.items():
        full = entity_repo.get_entity(entity_id)
        if full and full.wikidata_qid:
            entity_records[entity_id] = ref.model_copy(update={"wikidata_qid": full.wikidata_qid})

    items: list[EpisodeEntity] = []
    for entity_id, mentions in grouped.items():
        ref = entity_records.get(entity_id)
        if ref is None:
            continue
        mentions.sort(key=lambda m: m.start_ms)
        kind: Literal["host", "guest", "recurring", "unknown"]
        if entity_id in host_ids:
            kind = "host"
        elif entity_id in guest_ids:
            kind = "guest"
        elif entity_id in recurring_ids:
            kind = "recurring"
        else:
            kind = "unknown"
        items.append(
            EpisodeEntity(
                entity=ref,
                mention_count=len(mentions),
                first_mention_ms=mentions[0].start_ms,
                speaker_kind=kind,
                salience=_compute_salience(speaker_kind=kind, mentions=mentions),
                mentions=mentions,
            )
        )

    # Spec §5.2 right-rail sort: hosts/guests/recurring first
    # (participants), mentioned-only after. Within each bucket sort by
    # salience descending — see ``_compute_salience`` for the formula.
    # Tiebreak by canonical_name for deterministic output. Replaces the
    # raw ``mention_count`` sort that let one-shot tangents outrank
    # broadly-referenced entities.
    kind_priority = {"host": 0, "guest": 1, "recurring": 2, "unknown": 3}
    items.sort(key=lambda i: (kind_priority[i.speaker_kind], -i.salience, i.entity.canonical_name))

    return EpisodeEntitiesResponse(
        episode_id=episode_id,
        podcast_id=podcast.id,
        entities=items,
    )


# ---------------------------------------------------------------------------
# Entity-scoped: GET /api/entities/{type}/{id_slug}
# ---------------------------------------------------------------------------


_VALID_TYPES = {"person", "company", "product", "topic"}


@router.get(
    "/entities/{entity_type}/{id_slug}",
    response_model=EntitySummaryResponse,
)
def get_entity_summary(
    entity_type: str,
    id_slug: str,
    state: AppState = Depends(get_app_state),
) -> EntitySummaryResponse:
    """Entity page payload — record, aggregates, recent mentions.

    The URL accepts the bare slug (``elon-musk``) — the entity id is
    reconstructed as ``"{type}:{slug}"``. Callers that already have the
    full id can use either ``person/elon-musk`` or
    ``person/person:elon-musk`` (we strip a leading ``"{type}:"`` prefix).
    """
    if entity_type not in _VALID_TYPES:
        raise HTTPException(status_code=400, detail=f"Invalid entity type: {entity_type}")

    # Accept both bare slug and full id forms so deeplinks survive
    # whichever shape the caller has on hand.
    bare_slug = id_slug
    prefix = f"{entity_type}:"
    if bare_slug.startswith(prefix):
        bare_slug = bare_slug[len(prefix) :]
    entity_id = f"{entity_type}:{bare_slug}"

    summary = state.entity_repository.get_entity_summary(entity_id)
    if summary is None:
        raise HTTPException(status_code=404, detail=f"Entity not found: {entity_id}")

    entity_record = summary["entity"]
    entity_ref = EntityRef(
        id=entity_record.id,
        type=entity_record.type.value,
        canonical_name=entity_record.canonical_name,
        wikidata_qid=entity_record.wikidata_qid,
    )

    cooccurring: list[EntityCooccurrenceRef] = []
    for row in summary["cooccurring"]:
        other = row["entity"]
        cooccurring.append(
            EntityCooccurrenceRef(
                entity=EntityRef(
                    id=other.id,
                    type=other.type.value,
                    canonical_name=other.canonical_name,
                    wikidata_qid=other.wikidata_qid,
                ),
                episode_count=row["episode_count"],
                last_seen_at=row["last_seen_at"],
            )
        )

    recent_mentions: list[CitationRow] = []
    for ctx in summary["recent_mentions"]:
        m = ctx.mention
        # The web doesn't have an `/episodes/<id>` route; resolve slugs
        # so the row is deeplinkable from the entity page.
        episode_lookup = state.repository.get_episode(ctx.episode_id)
        episode_slug = None
        episode_audio_url = None
        episode_image_url = None
        episode_duration = None
        if episode_lookup is not None:
            _ep_podcast, ep_record = episode_lookup
            episode_slug = ep_record.slug
            # ``audio_url`` is a HttpUrl on the model; coerce to str so
            # the response stays JSON-serializable.
            episode_audio_url = str(ep_record.audio_url) if ep_record.audio_url else None
            episode_image_url = ep_record.image_url
            episode_duration = ep_record.duration
        recent_mentions.append(
            CitationRow(
                episode_id=ctx.episode_id,
                podcast_id=ctx.podcast_id,
                podcast_slug=ctx.podcast_slug,
                episode_slug=episode_slug,
                podcast_title=ctx.podcast_title,
                episode_title=ctx.episode_title,
                published_at=(ctx.episode_pub_date.isoformat() if ctx.episode_pub_date else None),
                start_ms=m.start_ms,
                end_ms=m.end_ms,
                speaker=m.speaker,
                quote=m.quote_excerpt,
                surface_form=m.surface_form,
                audio_url=episode_audio_url,
                image_url=episode_image_url,
                duration=episode_duration,
            )
        )

    hosts_podcasts = [
        HostedPodcastRef(
            podcast_id=row["podcast_id"],
            podcast_slug=row.get("podcast_slug"),
            podcast_title=row["podcast_title"],
            episode_count=row["episode_count"],
        )
        for row in summary.get("hosts_podcasts", [])
    ]
    recurring_podcasts = [
        HostedPodcastRef(
            podcast_id=row["podcast_id"],
            podcast_slug=row.get("podcast_slug"),
            podcast_title=row["podcast_title"],
            episode_count=row["episode_count"],
        )
        for row in summary.get("recurring_podcasts", [])
    ]
    guest_episodes = [
        GuestEpisodeRef(
            episode_id=row["episode_id"],
            episode_slug=row.get("episode_slug"),
            episode_title=row["episode_title"],
            podcast_id=row["podcast_id"],
            podcast_slug=row.get("podcast_slug"),
            podcast_title=row["podcast_title"],
            published_at=row.get("published_at"),
        )
        for row in summary.get("guest_episodes", [])
    ]

    most_discussed_on = [
        MostDiscussedRef(
            podcast_id=row["podcast_id"],
            podcast_slug=row.get("podcast_slug"),
            podcast_title=row["podcast_title"],
            mention_count=row["mention_count"],
        )
        for row in summary.get("most_discussed_on", [])
    ]

    # Spec #45 — surface Tier-0 enrichment only when it has something to
    # show. ``has_content`` keeps a row that exists purely as a "failed"/
    # "empty" provenance marker from rendering an empty hero.
    enrichment_obj = summary.get("enrichment")
    enrichment_wire: Optional[EntityEnrichmentWire] = None
    if enrichment_obj is not None and enrichment_obj.has_content():
        enrichment_wire = EntityEnrichmentWire(
            image_url=enrichment_obj.image_url,
            image_attribution=enrichment_obj.image_attribution,
            image_license=enrichment_obj.image_license,
            headline=enrichment_obj.headline,
            wikipedia_extract=enrichment_obj.wikipedia_extract,
            wikipedia_url=enrichment_obj.wikipedia_url,
            facts=enrichment_obj.facts,
            affiliations=enrichment_obj.affiliations,
        )

    return EntitySummaryResponse(
        entity=entity_ref,
        aliases=entity_record.aliases,
        description=entity_record.description,
        mention_count=summary["mention_count"],
        cooccurring=cooccurring,
        recent_mentions=recent_mentions,
        hosts_podcasts=hosts_podcasts,
        recurring_podcasts=recurring_podcasts,
        guest_episodes=guest_episodes,
        most_discussed_on=most_discussed_on,
        enrichment=enrichment_wire,
    )


# ---------------------------------------------------------------------------
# Admin detection queue — find + correct likely-wrong resolutions
# ---------------------------------------------------------------------------


class ReviewFlagWire(BaseModel):
    """One likely-wrong resolution surfaced for admin review."""

    entity_id: str
    type: str
    canonical_name: str
    wikidata_qid: Optional[str] = None
    score: float
    kinds: List[str]
    evidence: dict
    suggested_action: str  # "blacklist" | "review"
    suggested_qid: Optional[str] = None


class ReviewQueueResponse(BaseModel):
    count: int
    flags: List[ReviewFlagWire]


class CorrectionRequest(BaseModel):
    """Apply a correction to a wrong resolution and re-resolve its mentions.

    ``force_entity`` takes either ``target_entity_id`` (an existing entity)
    or ``target_qid`` (minted from Wikidata if absent). A ``wrong_qid`` may
    accompany ``force_entity`` to also blacklist the bad QID (redirect).
    """

    action: Literal["blacklist", "force_entity", "drop", "force_unresolvable"]
    surface_form: str
    episode_id: Optional[str] = None
    wrong_qid: Optional[str] = None
    target_qid: Optional[str] = None
    target_entity_id: Optional[str] = None
    reason: Optional[str] = None


@router.get("/entities/review-queue", response_model=ReviewQueueResponse)
def get_review_queue(
    limit: int = Query(50, ge=1, le=200),
    state: AppState = Depends(get_app_state),
    _: User = Depends(require_auth),
) -> ReviewQueueResponse:
    """Ranked list of likely-wrong entity resolutions (admin review queue).

    Read-only and network-free — see ``core.entity_review`` for the
    heuristics and ranking. Highest score = most mentions poisoned.
    """
    flags = scan_entities_for_review(state.entity_repository, limit=limit)
    return ReviewQueueResponse(
        count=len(flags),
        flags=[
            ReviewFlagWire(
                entity_id=f.entity_id,
                type=f.type,
                canonical_name=f.canonical_name,
                wikidata_qid=f.qid,
                score=f.score,
                kinds=f.kinds,
                evidence=f.evidence,
                suggested_action=f.suggested_action,
                suggested_qid=f.suggested_qid,
            )
            for f in flags
        ],
    )


@router.post("/entities/corrections")
def post_correction(
    request: CorrectionRequest,
    state: AppState = Depends(get_app_state),
    _: User = Depends(require_auth),
) -> dict:
    """Apply an admin correction (blacklist / override) and re-resolve.

    Reuses the same repo primitives as the ``mention`` / ``resolution-blacklist``
    CLI commands, then resets the poisoned mentions to ``pending`` and
    enqueues a ``resolve-entities`` task per affected episode so the fix
    lands on existing data. Returns a paste-ready golden-eval snippet so the
    correction can be locked in as a permanent regression guard.
    """
    try:
        result = apply_correction(
            repo=state.entity_repository,
            queue_manager=state.queue_manager,
            wikidata_client=WikidataClient(),
            action=request.action,
            surface_form=request.surface_form,
            episode_id=request.episode_id,
            wrong_qid=request.wrong_qid,
            target_qid=request.target_qid,
            target_entity_id=request.target_entity_id,
            reason=request.reason,
        )
    except CorrectionError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc
    except EnrichmentUnavailable as exc:
        raise HTTPException(status_code=502, detail=f"Wikidata unavailable: {exc}") from exc

    return api_response(
        {
            "action": result.action,
            "surface_form": result.surface_form,
            "affected_mentions": result.affected_mentions,
            "episodes_enqueued": result.episodes_enqueued,
            "created_entity_id": result.created_entity_id,
            "target_entity_id": result.target_entity_id,
            "override_id": result.override_id,
            "blacklist_id": result.blacklist_id,
            "golden_snippet": result.golden_snippet,
        }
    )
