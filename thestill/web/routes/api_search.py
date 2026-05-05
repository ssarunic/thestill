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


class SearchResponse(BaseModel):
    query: str
    mode: str
    total: int
    results: List[SearchResult]


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
    slug_map = _resolve_episode_slugs(
        db_path=str(state.repository.db_path),
        episode_ids=[h.episode_id for h in hits],
    )
    results: list[SearchResult] = []
    for h in hits:
        row = SearchResult(**h.as_citation())
        slugs = slug_map.get(h.episode_id)
        if slugs is not None:
            row.podcast_slug, row.episode_slug = slugs
        results.append(row)
    return SearchResponse(
        query=q,
        mode=effective_mode.value,
        total=len(results),
        results=results,
    )


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
        db_path=str(state.repository.db_path),
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
                )
            )

    # Resolve podcast_slug + episode_slug for quote rows so the client
    # can build the actual /podcasts/:slug/episodes/:slug deep link
    # without a follow-up fetch. ResolvedHit.as_citation() returns
    # /episodes/<id>?t=… which doesn't match the live frontend route.
    quote_items: list[QuickQuoteItem] = []
    if quote_hits:
        slug_map = _resolve_episode_slugs(
            db_path=str(state.repository.db_path),
            episode_ids=[h.episode_id for h in quote_hits],
        )
        for h in quote_hits:
            slugs = slug_map.get(h.episode_id)
            if slugs is None:
                continue  # episode metadata missing — skip rather than render a broken row
            pslug, eslug = slugs
            quote_items.append(
                QuickQuoteItem(
                    episode_id=h.episode_id,
                    podcast_id=h.podcast_id,
                    podcast_slug=pslug,
                    episode_slug=eslug,
                    podcast_title=h.podcast_title,
                    episode_title=h.episode_title,
                    speaker=h.speaker,
                    quote=h.text[:600],
                    start_ms=h.start_ms,
                    end_ms=h.end_ms,
                    score=h.score,
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
    db_path: str,
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
    conn = sqlite3.connect(db_path)
    conn.row_factory = sqlite3.Row
    try:
        rows = conn.execute(sql, params).fetchall()
    finally:
        conn.close()
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


def _resolve_episode_slugs(
    *,
    db_path: str,
    episode_ids: List[str],
) -> dict[str, tuple[str, str]]:
    """Return ``{episode_id: (podcast_slug, episode_slug)}`` for the
    given ids. One round-trip with an IN clause; rows missing a slug
    are dropped (caller skips them).
    """
    if not episode_ids:
        return {}
    placeholders = ",".join("?" for _ in episode_ids)
    sql = f"""
        SELECT e.id        AS episode_id,
               e.slug      AS episode_slug,
               p.slug      AS podcast_slug
        FROM episodes e
        JOIN podcasts p ON p.id = e.podcast_id
        WHERE e.id IN ({placeholders})
    """
    conn = sqlite3.connect(db_path)
    conn.row_factory = sqlite3.Row
    try:
        rows = conn.execute(sql, episode_ids).fetchall()
    finally:
        conn.close()
    return {
        row["episode_id"]: (row["podcast_slug"] or "", row["episode_slug"] or "")
        for row in rows
        if row["podcast_slug"] and row["episode_slug"]
    }
