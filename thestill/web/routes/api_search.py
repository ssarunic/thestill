"""Spec #28 §2.10 + §4.1 — REST mirrors for the search surface.

Two endpoints, both backed by the same SqliteVecBackend on the
AppState:

- ``GET /api/search/corpus`` — full citation-shaped search, modes
  ``lexical|semantic|hybrid``. Defaults to ``hybrid`` and is what
  the search results page (Phase 4.2) hits.
- ``GET /api/search/quick`` — typeahead for the ⌘K command bar.
  **Pinned to lexical** (Strategy §2): no ``mode`` parameter is
  exposed, the endpoint never silently upgrades. Returns five
  groups (Episodes / Persons / Companies / Topics / Quotes) capped
  at ``limit_per_group`` rows each.
"""

from __future__ import annotations

import sqlite3
import time
from typing import List, Literal, Optional

from fastapi import APIRouter, Depends, HTTPException, Query
from pydantic import BaseModel
from structlog import get_logger

from ...search.base import SearchFilters, SearchMode
from ..dependencies import AppState, get_app_state

logger = get_logger(__name__)


def _query(state, sql: str, params: list) -> list[dict]:
    """Run a read-only metadata query on the active backend (spec #44).

    These route-local lookups predate the repository seam and used raw
    sqlite3 directly — the "hidden SQLite coupling" spec #44 warned about.
    The SQL here is dialect-portable (LOWER/LIKE/IN/JOIN), so one string
    serves both engines: ``?`` placeholders are rewritten to ``%s`` for
    psycopg, and uuid cells are stringified so callers keep dict-of-str
    semantics identical to the SQLite rows.
    """
    dsn = getattr(state.repository, "dsn", None)
    if dsn:
        from ...utils.postgres_ext import connect as pg_connect

        with pg_connect(dsn) as conn:
            rows = conn.execute(sql.replace("?", "%s"), params).fetchall()
        import uuid as _uuid
        from datetime import datetime as _dt
        from datetime import timezone as _tz

        def _cell(v):
            # Match the SQLite row shapes exactly: uuids and timestamps come
            # back as strings there, and the response models expect strings.
            # Timestamps normalise to UTC — psycopg renders timestamptz in
            # the session timezone, which would leak +01:00-style offsets.
            if isinstance(v, _uuid.UUID):
                return str(v)
            if isinstance(v, _dt):
                return (v.astimezone(_tz.utc) if v.tzinfo else v).isoformat()
            return v

        return [{k: _cell(v) for k, v in r.items()} for r in rows]

    conn = sqlite3.connect(str(state.repository.db_path))
    conn.row_factory = sqlite3.Row
    try:
        return [dict(r) for r in conn.execute(sql, params).fetchall()]
    finally:
        conn.close()

router = APIRouter()


class SearchResult(BaseModel):
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
    score: float
    match_type: str
    deeplink: str
    web_url: str
    # Spec #28 §4.2 — search results page plays each quote inline via
    # the FloatingPlayer. Without the audio URL the click has to fall
    # back to a full navigation, which loses the user's place in the
    # results list.
    audio_url: Optional[str] = None
    image_url: Optional[str] = None
    duration: Optional[float] = None


class SearchResponse(BaseModel):
    query: str
    mode: str
    total: int
    results: List[SearchResult]


class RelatedEpisode(BaseModel):
    """One episode-card row for the episode-page "Related episodes" rail."""

    episode_id: str
    podcast_id: str
    podcast_slug: str
    episode_slug: str
    podcast_title: str
    episode_title: str
    published_at: Optional[str] = None
    image_url: Optional[str] = None
    # Blended relevance score (higher = closer), min-max normalised per
    # source episode by the builder — so it ranks within one rail but
    # isn't comparable across episodes. Surfaced for transparency; the
    # rail doesn't render it.
    score: float


class RelatedEpisodesResponse(BaseModel):
    episode_id: str
    episodes: List[RelatedEpisode]


# ---------------------------------------------------------------------------
# Quick-search response models — discriminated by ``kind`` so the React
# client can render heterogeneous group items uniformly.
# ---------------------------------------------------------------------------


class QuickEpisodeItem(BaseModel):
    kind: Literal["episode"] = "episode"
    episode_id: str
    podcast_id: str
    podcast_slug: str
    episode_slug: str
    title: str
    podcast_title: str
    pub_date: Optional[str] = None
    image_url: Optional[str] = None


class QuickEntityItem(BaseModel):
    kind: Literal["entity"] = "entity"
    entity_type: str  # person | company | product | topic
    id: str
    name: str
    matched_alias: Optional[str] = None
    mention_count: int
    # Role this entity holds in the corpus, derived from
    # podcasts.host_entity_ids / podcasts.recurring_entity_ids /
    # episodes.guest_entity_ids. ``None`` for entities that only
    # appear as transcript mentions.
    role: Optional[str] = None  # guest | host | recurring | None
    role_episode_count: int = 0


class QuickQuoteItem(BaseModel):
    kind: Literal["quote"] = "quote"
    episode_id: str
    podcast_id: str
    podcast_slug: str
    episode_slug: str
    podcast_title: str
    episode_title: str
    speaker: Optional[str] = None
    quote: str
    start_ms: int
    end_ms: int
    score: float
    # Spec #28 §4.1 — typeahead quote rows seek the FloatingPlayer
    # inline when selected from ⌘K, so we ship the audio URL alongside
    # the slug so the CommandBar can play without a second fetch.
    audio_url: Optional[str] = None
    image_url: Optional[str] = None
    duration: Optional[float] = None


class QuickGroup(BaseModel):
    type: str  # episode | person | company | topic | quote
    label: str
    items: list


class QuickSearchResponse(BaseModel):
    query: str
    took_ms: int
    groups: List[QuickGroup]
    see_all_url: str


@router.get("/corpus", response_model=SearchResponse)
def search_corpus(
    q: str = Query(..., min_length=1, description="Search text."),
    mode: str = Query("hybrid", pattern="^(lexical|semantic|hybrid)$"),
    limit: int = Query(10, ge=1, le=50),
    podcast_id: Optional[str] = Query(None),
    date_from: Optional[str] = Query(None, description="ISO-8601 date."),
    date_to: Optional[str] = Query(None, description="ISO-8601 date."),
    has_entity: Optional[List[str]] = Query(None),
    state: AppState = Depends(get_app_state),
):
    backend = state.search_backend
    if backend is None:
        raise HTTPException(status_code=503, detail="search backend not initialised")

    filters = SearchFilters(
        podcast_id=podcast_id,
        date_from=date_from,
        date_to=date_to,
        has_entity=tuple(has_entity or ()),
    )
    requested_mode = SearchMode(mode)
    effective_mode = requested_mode
    try:
        hits = backend.search(q, mode=requested_mode, limit=limit, filters=filters)
    except ModuleNotFoundError as exc:
        # ``sentence-transformers`` is an optional dep — semantic and
        # hybrid both require it. Fall back to lexical so the page
        # returns BM25 results instead of going offline; log so the
        # operator knows recall is degraded.
        if requested_mode == SearchMode.LEXICAL or "sentence_transformers" not in str(exc):
            raise
        logger.warning(
            "search_corpus_embedding_unavailable_fallback_lexical",
            requested_mode=requested_mode.value,
            error=str(exc),
        )
        effective_mode = SearchMode.LEXICAL
        hits = backend.search(q, mode=effective_mode, limit=limit, filters=filters)
    # Resolve slugs so the React client can build /podcasts/<p>/episodes/<e>
    # routes directly. The citation's web_url is /episodes/<id> — kept on
    # the wire for MCP/desktop callers but the web doesn't have that route.
    payload_map = _resolve_episode_payloads(
        state=state,
        episode_ids=[h.episode_id for h in hits],
    )
    results: list[SearchResult] = []
    for h in hits:
        row = SearchResult(**h.as_citation())
        payload = payload_map.get(h.episode_id)
        if payload is not None:
            row.podcast_slug = payload.podcast_slug
            row.episode_slug = payload.episode_slug
            row.audio_url = payload.audio_url
            row.image_url = payload.image_url
            row.duration = payload.duration
        results.append(row)
    return SearchResponse(
        query=q,
        mode=effective_mode.value,
        total=len(results),
        results=results,
    )


@router.get("/related", response_model=RelatedEpisodesResponse)
def search_related(
    episode_id: str = Query(..., min_length=1, description="Source episode id."),
    limit: int = Query(5, ge=1, le=20),
    state: AppState = Depends(get_app_state),
):
    """Spec #28 §5.2 — "Related episodes" for the episode-page right rail.

    Reads the precomputed ``episode_related`` table (built by
    ``thestill related build`` from a TF-IDF + dense-vector + entity
    blend; see ``search.related_builder``). A plain indexed read — no
    embedding model, no vector scan on the request path. Episodes with
    no precomputed neighbours (corpus too small, not yet built, or no
    topically-related episodes) return ``[]``.
    """
    rows = _read_related(
        state=state,
        episode_id=episode_id,
        limit=limit,
    )
    return RelatedEpisodesResponse(episode_id=episode_id, episodes=rows)


def _read_related(*, state, episode_id: str, limit: int) -> List[RelatedEpisode]:
    """Join ``episode_related`` to episodes/podcasts for deep-linkable cards.

    Rows whose related episode lacks slugs are skipped (not linkable),
    mirroring ``_resolve_episode_payloads``. Ordered by precomputed rank.
    """
    sql = """
        SELECT r.related_episode_id AS episode_id,
               r.score             AS score,
               e.title             AS episode_title,
               e.slug              AS episode_slug,
               e.pub_date          AS published_at,
               e.image_url         AS image_url,
               p.id                AS podcast_id,
               p.title             AS podcast_title,
               p.slug              AS podcast_slug,
               p.image_url         AS podcast_image_url
        FROM episode_related r
        JOIN episodes e ON e.id = r.related_episode_id
        JOIN podcasts p ON p.id = e.podcast_id
        WHERE r.episode_id = ?
        ORDER BY r.rank
        LIMIT ?
    """
    rows = _query(state, sql, [episode_id, limit])
    out: List[RelatedEpisode] = []
    for row in rows:
        if not row["podcast_slug"] or not row["episode_slug"]:
            continue  # legacy row without slugs — not deep-linkable
        out.append(
            RelatedEpisode(
                episode_id=row["episode_id"],
                podcast_id=row["podcast_id"],
                podcast_slug=row["podcast_slug"],
                episode_slug=row["episode_slug"],
                podcast_title=row["podcast_title"],
                episode_title=row["episode_title"],
                published_at=row["published_at"],
                image_url=row["image_url"] or row["podcast_image_url"] or None,
                score=row["score"],
            )
        )
    return out


# Order is the contract — the frontend renders groups in this order
# regardless of which buckets are empty (groups stay in the response
# with ``items: []`` so the list is stable across keystrokes).
_GROUP_ORDER: tuple[tuple[str, str, Optional[str]], ...] = (
    ("episode", "Episodes", None),
    ("person", "People", "person"),
    ("company", "Companies", "company"),
    ("topic", "Topics", "topic"),
    ("quote", "Quotes", None),
)


@router.get("/quick", response_model=QuickSearchResponse)
def search_quick(
    q: str = Query(..., min_length=1, description="Free-form search text."),
    limit_per_group: int = Query(5, ge=1, le=10),
    podcast_id: Optional[str] = Query(None),
    podcast_slug: Optional[str] = Query(
        None,
        description="Podcast slug; resolved to podcast_id server-side. Wins over podcast_id if both set.",
    ),
    date_from: Optional[str] = Query(None, description="ISO-8601 lower bound on pub_date."),
    date_to: Optional[str] = Query(None, description="ISO-8601 upper bound on pub_date."),
    has_entity: Optional[List[str]] = Query(None),
    state: AppState = Depends(get_app_state),
):
    """Spec #28 §4.1 — typeahead for the ⌘K command bar.

    Always ``mode=lexical`` for the quote group (Strategy §2 pins
    the typing path; never silently upgrade). Episodes and entities
    come from direct SQL lookups on indexed columns — no embedding
    model is touched on this path.
    """
    backend = state.search_backend
    if backend is None:
        raise HTTPException(status_code=503, detail="search backend not initialised")
    entity_repository = state.entity_repository

    started = time.perf_counter()
    query = q.strip()
    # The CommandBar parses ``podcast:<slug>`` client-side and forwards
    # the slug. Resolve to id here so the rest of the pipeline (which
    # only knows about podcast_id) doesn't need a slug-aware code path.
    resolved_podcast_id = podcast_id
    if podcast_slug:
        podcast = state.repository.get_by_slug(podcast_slug)
        if podcast is None:
            # An unknown slug shouldn't 500 the typeahead — just drop
            # the filter and let the broader query stand. The client
            # already shows the slug in its idle hint, so the user
            # knows what they typed.
            resolved_podcast_id = None
        else:
            resolved_podcast_id = podcast.id
    filters = SearchFilters(
        podcast_id=resolved_podcast_id,
        date_from=date_from,
        date_to=date_to,
        has_entity=tuple(has_entity or ()),
    )

    # 1) Quotes — lexical search_corpus. Pinned mode; never upgraded.
    quote_hits = backend.search(
        query,
        mode=SearchMode.LEXICAL,
        limit=limit_per_group,
        filters=filters,
    )

    # 2) Episodes — direct title prefix on the episodes table. The
    #    Backend's ``db_path`` is the same file the entity repo uses,
    #    so we open one short-lived connection rather than threading
    #    yet another service through.
    episode_items = _episode_typeahead(
        state=state,
        prefix=query,
        limit=limit_per_group,
        podcast_id=resolved_podcast_id,
        date_from=date_from,
        date_to=date_to,
    )

    # 3) Entities — single repo call returns hits across all four
    #    types; we partition into person/company/topic groups below.
    entity_hits = entity_repository.search_entities_by_prefix(
        query,
        types=("person", "company", "topic"),
        limit_per_type=limit_per_group,
    )
    entities_by_type: dict[str, list] = {"person": [], "company": [], "topic": []}
    for hit in entity_hits:
        if hit.type in entities_by_type:
            entities_by_type[hit.type].append(
                QuickEntityItem(
                    entity_type=hit.type,
                    id=hit.id,
                    name=hit.canonical_name,
                    matched_alias=hit.matched_alias,
                    mention_count=hit.mention_count,
                    role=hit.role,
                    role_episode_count=hit.role_episode_count,
                )
            )

    # Resolve podcast_slug + episode_slug for quote rows so the client
    # can build the actual /podcasts/:slug/episodes/:slug deep link
    # without a follow-up fetch. ResolvedHit.as_citation() returns
    # /episodes/<id>?t=… which doesn't match the live frontend route.
    quote_items: list[QuickQuoteItem] = []
    if quote_hits:
        payload_map = _resolve_episode_payloads(
            state=state,
            episode_ids=[h.episode_id for h in quote_hits],
        )
        for h in quote_hits:
            payload = payload_map.get(h.episode_id)
            if payload is None:
                continue  # episode metadata missing — skip rather than render a broken row
            quote_items.append(
                QuickQuoteItem(
                    episode_id=h.episode_id,
                    podcast_id=h.podcast_id,
                    podcast_slug=payload.podcast_slug,
                    episode_slug=payload.episode_slug,
                    podcast_title=h.podcast_title,
                    episode_title=h.episode_title,
                    speaker=h.speaker,
                    quote=h.text[:600],
                    start_ms=h.start_ms,
                    end_ms=h.end_ms,
                    score=h.score,
                    audio_url=payload.audio_url,
                    image_url=payload.image_url,
                    duration=payload.duration,
                )
            )

    items_by_group = {
        "episode": episode_items,
        "person": entities_by_type["person"],
        "company": entities_by_type["company"],
        "topic": entities_by_type["topic"],
        "quote": quote_items,
    }
    groups = [
        QuickGroup(type=group_type, label=label, items=items_by_group[group_type])
        for group_type, label, _ in _GROUP_ORDER
    ]

    elapsed_ms = int((time.perf_counter() - started) * 1000)
    logger.info(
        "search_quick",
        query=query,
        took_ms=elapsed_ms,
        episode_hits=len(episode_items),
        person_hits=len(entities_by_type["person"]),
        company_hits=len(entities_by_type["company"]),
        topic_hits=len(entities_by_type["topic"]),
        quote_hits=len(quote_items),
    )
    return QuickSearchResponse(
        query=query,
        took_ms=elapsed_ms,
        groups=groups,
        see_all_url=f"/search?q={query}",
    )


def _episode_typeahead(
    *,
    state,
    prefix: str,
    limit: int,
    podcast_id: Optional[str],
    date_from: Optional[str],
    date_to: Optional[str] = None,
) -> List[QuickEpisodeItem]:
    """LIKE-match episode titles, joined to podcasts for slug.

    Indexed via ``idx_episodes_pub_date`` for the ORDER BY; the LIKE
    is a sequential scan but ``episodes`` is small enough at v1 scale
    that it's well under the latency budget.
    """
    if not prefix:
        return []
    where_parts = ["LOWER(e.title) LIKE ?"]
    params: list = [f"%{prefix.lower()}%"]
    if podcast_id:
        where_parts.append("e.podcast_id = ?")
        params.append(podcast_id)
    if date_from:
        where_parts.append("e.pub_date >= ?")
        params.append(date_from)
    if date_to:
        where_parts.append("e.pub_date <= ?")
        params.append(date_to)
    where_clause = " AND ".join(where_parts)
    sql = f"""
        SELECT e.id            AS episode_id,
               e.title          AS title,
               e.slug           AS episode_slug,
               e.pub_date       AS pub_date,
               e.image_url      AS image_url,
               p.id             AS podcast_id,
               p.title          AS podcast_title,
               p.slug           AS podcast_slug,
               p.image_url      AS podcast_image_url
        FROM episodes e
        JOIN podcasts p ON p.id = e.podcast_id
        WHERE {where_clause}
        ORDER BY e.pub_date DESC NULLS LAST
        LIMIT ?
    """
    params.append(limit)
    rows = _query(state, sql, params)
    items: list[QuickEpisodeItem] = []
    for row in rows:
        episode_slug = row["episode_slug"] or ""
        podcast_slug = row["podcast_slug"] or ""
        if not episode_slug or not podcast_slug:
            continue  # legacy rows without slugs aren't deep-linkable yet
        items.append(
            QuickEpisodeItem(
                episode_id=row["episode_id"],
                podcast_id=row["podcast_id"],
                podcast_slug=podcast_slug,
                episode_slug=episode_slug,
                title=row["title"],
                podcast_title=row["podcast_title"],
                pub_date=row["pub_date"],
                image_url=row["image_url"] or row["podcast_image_url"],
            )
        )
    return items


class _EpisodePayload(BaseModel):
    """Per-episode playback metadata used by both search endpoints.

    The slug pair was the original payload shape (``_resolve_episode_slugs``);
    we expanded it to carry the data the FloatingPlayer needs (audio
    URL, artwork, duration) so neither the search-results page nor the
    ⌘K command bar has to do a follow-up ``/episodes/<id>`` fetch on
    click. Rows with no audio URL still come back so the caller can
    fall back to a deep-link navigation.
    """

    podcast_slug: str
    episode_slug: str
    audio_url: Optional[str] = None
    image_url: Optional[str] = None
    duration: Optional[float] = None


def _resolve_episode_payloads(
    *,
    state,
    episode_ids: List[str],
) -> dict[str, _EpisodePayload]:
    """Return playback metadata for each episode id (podcast/episode
    slugs, audio URL, artwork, duration). One round-trip with an IN
    clause; rows missing slugs are dropped because they aren't
    deep-linkable.
    """
    if not episode_ids:
        return {}
    placeholders = ",".join("?" for _ in episode_ids)
    sql = f"""
        SELECT e.id              AS episode_id,
               e.slug            AS episode_slug,
               e.audio_url       AS audio_url,
               e.image_url       AS image_url,
               e.duration        AS duration,
               p.slug            AS podcast_slug,
               p.image_url       AS podcast_image_url
        FROM episodes e
        JOIN podcasts p ON p.id = e.podcast_id
        WHERE e.id IN ({placeholders})
    """
    rows = _query(state, sql, list(episode_ids))
    out: dict[str, _EpisodePayload] = {}
    for row in rows:
        if not row["podcast_slug"] or not row["episode_slug"]:
            continue
        out[row["episode_id"]] = _EpisodePayload(
            podcast_slug=row["podcast_slug"],
            episode_slug=row["episode_slug"],
            audio_url=row["audio_url"] or None,
            image_url=row["image_url"] or row["podcast_image_url"] or None,
            duration=row["duration"],
        )
    return out


def _resolve_episode_slugs(
    *,
    state,
    episode_ids: List[str],
) -> dict[str, tuple[str, str]]:
    """Backwards-compatible slug-only shape.

    Kept so existing callers (and tests that import the symbol directly)
    don't break while the FloatingPlayer wiring rolls out. New code
    should prefer ``_resolve_episode_payloads``.
    """
    payloads = _resolve_episode_payloads(state=state, episode_ids=episode_ids)
    return {ep_id: (p.podcast_slug, p.episode_slug) for ep_id, p in payloads.items()}
