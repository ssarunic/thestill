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

from collections import defaultdict
from typing import List, Literal, Optional

from fastapi import APIRouter, Depends, HTTPException, Query
from pydantic import BaseModel
from structlog import get_logger

from ..dependencies import AppState, get_app_state

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
    mentions: List[MentionLite]


class EpisodeEntitiesResponse(BaseModel):
    episode_id: str
    podcast_id: str
    entities: List[EpisodeEntity]


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
                mentions=mentions,
            )
        )

    # Spec §5.2 right-rail sort order: hosts/guests/recurring first
    # (participants), then mentioned-only, each bucket descending by
    # mention count. The frontend can re-sort but the canonical order
    # is set here so consumers other than the rail see the same shape.
    kind_priority = {"host": 0, "guest": 1, "recurring": 2, "unknown": 3}
    items.sort(key=lambda i: (kind_priority[i.speaker_kind], -i.mention_count, i.entity.canonical_name))

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
        episode_slug = episode_lookup[1].slug if episode_lookup else None
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
    )
