"""Spec #28 §2.10 — REST mirror of the ``search_corpus`` MCP tool.

``GET /api/search/corpus`` proxies into the ``SqliteVecBackend`` on
the AppState. The backend owns its embedding model (lazy-loads
sentence-transformers on first semantic/hybrid call); the route just
parses params, builds a filter, and calls ``.search()``.
"""

from __future__ import annotations

from typing import List, Optional

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
    hits = backend.search(q, mode=SearchMode(mode), limit=limit, filters=filters)
    return SearchResponse(
        query=q,
        mode=mode,
        total=len(hits),
        results=[SearchResult(**h.as_citation()) for h in hits],
    )
