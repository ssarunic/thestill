"""Spec #28 §2.6 — ``search_corpus`` MCP tool.

One tool, three modes:

- ``mode='lexical'`` builds a single-element ``[{type:'lex', query:Q}]``
  payload — fast BM25 over the rendered Markdown.
- ``mode='semantic'`` builds ``[{type:'vec', query:Q}]`` — embedding
  similarity, slower but understands meaning.
- ``mode='hybrid'`` (default) builds ``[{type:'lex'}, {type:'vec'}]`` —
  the strongest recall configuration per the Phase 0.1 spike. Pass
  intent for additional context when ambiguous (e.g. ``query='Apple'``
  + ``intent='the company, not the fruit'``).

Hits land in two flavours:

1. **Episode-page hits** map back to a transcript segment via
   ``segmap.json`` and become ``CitationRow`` rows directly.
2. **Entity-page hits** (``persons/<slug>.md`` etc.) don't have a
   segmap. We instead hand back a synthesized row pointing at the
   entity itself — the caller (Claude / the web command-bar) can
   follow up with ``find_mentions`` to enumerate the actual
   citations. This keeps the contract uniform: every result is one
   navigable row.
"""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any, List, Optional

from mcp.types import TextContent, Tool

from ..models.entities import CitationRow, MatchType
from ..repositories.sqlite_entity_repository import SqliteEntityRepository
from ..search.qmd_client import QmdClient, ResolvedHit

SEARCH_TOOL_NAMES = frozenset({"search_corpus"})


def search_tool_definitions() -> List[Tool]:
    """Return the ``Tool`` definition for ``search_corpus``."""
    return [
        Tool(
            name="search_corpus",
            description=(
                "Lexical, semantic, or hybrid search over the entire podcast "
                "corpus. Returns citation-shaped rows: episode title, speaker, "
                "quote, timestamp, deeplink. Use this when the user's question "
                "isn't entity-scoped (e.g. \"episodes that talk about agentic "
                'engineering" rather than "what has Karpathy said"). For '
                "entity-scoped queries prefer ``find_mentions``.\n\n"
                "Modes:\n"
                "- lexical: BM25 keyword match. Fast, exact terms.\n"
                "- semantic: vector similarity. Concept-level recall.\n"
                "- hybrid (default): both. Best recall on novel phrasings."
            ),
            inputSchema={
                "type": "object",
                "properties": {
                    "query": {
                        "type": "string",
                        "description": 'Free-form search text. For lexical mode, supports BM25 syntax: "quoted phrase", -negation.',
                    },
                    "mode": {
                        "type": "string",
                        "enum": ["lexical", "semantic", "hybrid"],
                        "default": "hybrid",
                        "description": "Search strategy.",
                    },
                    "intent": {
                        "type": "string",
                        "description": "Optional disambiguating context (e.g. 'the company, not the fruit').",
                    },
                    "limit": {"type": "integer", "default": 10, "description": "Max rows."},
                    "min_score": {
                        "type": "number",
                        "default": 0.0,
                        "description": "Minimum qmd relevance score (0-1).",
                    },
                },
                "required": ["query"],
            },
        ),
    ]


def dispatch_search_tool(
    name: str,
    arguments: Any,
    *,
    repository: SqliteEntityRepository,
    qmd_client: QmdClient,
) -> Optional[List[TextContent]]:
    """Dispatch ``search_corpus`` calls — returns ``None`` for other names."""
    if name not in SEARCH_TOOL_NAMES:
        return None
    args = arguments or {}
    return _handle_search_corpus(args, repository=repository, qmd_client=qmd_client)


# ----------------------------------------------------------------------
# Handler
# ----------------------------------------------------------------------


def _handle_search_corpus(
    args: dict,
    *,
    repository: SqliteEntityRepository,
    qmd_client: QmdClient,
) -> List[TextContent]:
    query = (args.get("query") or "").strip()
    if not query:
        return [TextContent(type="text", text=json.dumps({"results": [], "error": "empty query"}))]
    mode = (args.get("mode") or "hybrid").lower()
    limit = int(args.get("limit") or 10)
    min_score = float(args.get("min_score") or 0.0)
    intent = args.get("intent")

    searches = _build_searches(query, mode)
    raw_hits = qmd_client.search(
        searches,
        limit=limit,
        intent=intent,
        min_score=min_score,
    )

    # Episode-page hits → CitationRow via segmap
    episode_hits = [h for h in raw_hits if _is_episode_hit(h)]
    citation_rows = qmd_client.to_citation_rows(episode_hits, repository=repository)
    # Tag the match_type accurately. ``to_citation_rows`` defaults to
    # SEMANTIC; lex-only mode should report LEXICAL.
    if mode == "lexical":
        citation_rows = [_swap_match_type(r, MatchType.LEXICAL) for r in citation_rows]

    # Entity-page hits → synthesized "entity card" row pointing at the entity.
    entity_rows = _entity_card_rows(raw_hits, repository=repository)

    payload = {
        "query": query,
        "mode": mode,
        "results": [
            *(_row_to_dict(r) for r in citation_rows),
            *entity_rows,
        ],
        "total": len(citation_rows) + len(entity_rows),
    }
    return [TextContent(type="text", text=json.dumps(payload))]


# ----------------------------------------------------------------------
# Helpers
# ----------------------------------------------------------------------


def _build_searches(query: str, mode: str) -> List[dict]:
    """Translate ``mode`` into qmd's typed sub-query payload."""
    if mode == "lexical":
        return [{"type": "lex", "query": query}]
    if mode == "semantic":
        return [{"type": "vec", "query": query}]
    # hybrid (default)
    return [
        {"type": "lex", "query": query},
        {"type": "vec", "query": query},
    ]


def _is_episode_hit(hit: ResolvedHit) -> bool:
    """``ResolvedHit`` is only built for episode-page hits today —
    entity-page hits short-circuit in ``_resolve_hit``. So if we got
    one back, it's an episode hit.
    """
    return True


def _entity_card_rows(
    hits: List[ResolvedHit],
    *,
    repository: SqliteEntityRepository,  # noqa: ARG001 — reserved for future enrichment
) -> List[dict]:
    """Synthesize entity-card rows from raw qmd hits that landed on
    entity pages. Today ``QmdClient.search`` filters those out
    upstream — this hook is the seam where Phase 5 (entity pages on
    the web) plugs in. For now it always returns ``[]``.
    """
    return []


def _swap_match_type(row: CitationRow, new_type: MatchType) -> CitationRow:
    """Pydantic models are immutable in v2 by default — copy + override."""
    return row.model_copy(update={"match_type": new_type})


def _row_to_dict(row: CitationRow) -> dict:
    """Serialize a ``CitationRow`` to JSON-safe primitives."""
    return {
        "episode_id": row.episode_id,
        "podcast_id": row.podcast_id,
        "podcast_title": row.podcast_title,
        "episode_title": row.episode_title,
        "published_at": row.published_at.isoformat() if row.published_at else None,
        "start_ms": row.start_ms,
        "end_ms": row.end_ms,
        "speaker": row.speaker,
        "quote": row.quote,
        "score": row.score,
        "match_type": row.match_type.value,
        "deeplink": row.deeplink,
        "web_url": row.web_url,
    }
