"""Spec #28 §2.7 — REST mirror of the ``search_corpus`` MCP tool.

Single endpoint ``GET /api/search/corpus`` that mirrors the MCP tool's
inputs and returns the same citation rows. Intended for the web UI's
command bar (Phase 4) and ad-hoc curl debugging.
"""

from __future__ import annotations

from pathlib import Path
from typing import List, Optional

from fastapi import APIRouter, Depends, HTTPException, Query
from pydantic import BaseModel
from structlog import get_logger

from ...repositories.sqlite_entity_repository import SqliteEntityRepository
from ..dependencies import AppState, get_app_state

logger = get_logger(__name__)
router = APIRouter()


class SearchResult(BaseModel):
    """One row in the search response — citation-shaped."""

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
    mode: str = Query("hybrid", regex="^(lexical|semantic|hybrid)$"),
    intent: Optional[str] = Query(None),
    limit: int = Query(10, ge=1, le=50),
    min_score: float = Query(0.0, ge=0.0, le=1.0),
    state: AppState = Depends(get_app_state),
):
    """Search the rendered corpus via qmd. Mirrors the MCP tool.

    Returns at most ``limit`` rows ordered by qmd relevance. Episode
    hits resolve to a transcript segment via the segmap sidecar; entity
    hits are filtered out (Phase 5 will surface them as a separate
    ``entities`` field once the web UI has entity pages).
    """
    try:
        from ...search.qmd_client import QmdClient
    except FileNotFoundError as exc:  # pragma: no cover — environment-specific
        raise HTTPException(status_code=503, detail=str(exc))

    corpus_dir = state.path_manager.corpus_dir()
    if not corpus_dir.exists():
        raise HTTPException(status_code=503, detail="corpus has not been bootstrapped — run `make qmd-up`")

    try:
        client = QmdClient(corpus_dir=corpus_dir)
    except FileNotFoundError as exc:
        raise HTTPException(status_code=503, detail=f"qmd not installed: {exc}")

    if mode == "lexical":
        searches = [{"type": "lex", "query": q}]
    elif mode == "semantic":
        searches = [{"type": "vec", "query": q}]
    else:  # hybrid
        searches = [{"type": "lex", "query": q}, {"type": "vec", "query": q}]

    hits = client.search(searches, limit=limit, intent=intent, min_score=min_score)
    repo: SqliteEntityRepository = state.entity_repository
    citation_rows = client.to_citation_rows(hits, repository=repo)

    return SearchResponse(
        query=q,
        mode=mode,
        total=len(citation_rows),
        results=[
            SearchResult(
                episode_id=r.episode_id,
                podcast_id=r.podcast_id,
                podcast_title=r.podcast_title,
                episode_title=r.episode_title,
                published_at=r.published_at.isoformat() if r.published_at else None,
                start_ms=r.start_ms,
                end_ms=r.end_ms,
                speaker=r.speaker,
                quote=r.quote,
                score=r.score,
                match_type=r.match_type.value,
                deeplink=r.deeplink,
                web_url=r.web_url,
            )
            for r in citation_rows
        ],
    )
