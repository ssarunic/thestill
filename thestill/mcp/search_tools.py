"""Spec #28 §2.10 — ``search_corpus`` MCP tool.

One tool, three modes:

- ``mode='lexical'`` — FTS5 BM25 over chunks. No embedding required.
- ``mode='semantic'`` — k-NN over chunks_vec via cosine distance.
- ``mode='hybrid'`` (default) — RRF over both. Best recall.

All three accept an optional ``filters`` object pushed down into the
SQL WHERE clause: podcast_id, date_range (from/to), has_entity[].
"""

from __future__ import annotations

import json
from typing import Any, List, Optional

from mcp.types import TextContent, Tool

from ..search.base import SearchBackend, SearchFilters, SearchMode

SEARCH_TOOL_NAMES = frozenset({"search_corpus"})


def search_tool_definitions() -> List[Tool]:
    return [
        Tool(
            name="search_corpus",
            description=(
                "Lexical, semantic, or hybrid search over the podcast corpus. "
                "Returns citation-shaped rows: episode title, speaker, quote, "
                "timestamp, deeplink. Use this when the user's question isn't "
                'entity-scoped (e.g. "episodes about agentic engineering" '
                'rather than "what has Karpathy said"). For entity-scoped '
                "queries prefer ``find_mentions``.\n\n"
                "Modes:\n"
                "- lexical: BM25 keyword match. Fast, exact terms.\n"
                "- semantic: vector similarity. Concept-level recall.\n"
                "- hybrid (default): RRF over both. Best recall on novel phrasings."
            ),
            inputSchema={
                "type": "object",
                "properties": {
                    "query": {
                        "type": "string",
                        "description": (
                            "Free-form search text. Lexical mode supports BM25 " 'syntax: "quoted phrase", -negation.'
                        ),
                    },
                    "mode": {
                        "type": "string",
                        "enum": ["lexical", "semantic", "hybrid"],
                        "default": "hybrid",
                        "description": "Search strategy.",
                    },
                    "limit": {
                        "type": "integer",
                        "default": 10,
                        "description": "Max rows to return.",
                    },
                    "filters": {
                        "type": "object",
                        "description": "Optional WHERE-clause filters pushed to SQL.",
                        "properties": {
                            "podcast_id": {"type": "string"},
                            "date_range": {
                                "type": "object",
                                "properties": {
                                    "from": {"type": "string", "description": "ISO-8601 date."},
                                    "to": {"type": "string", "description": "ISO-8601 date."},
                                },
                            },
                            "has_entity": {
                                "type": "array",
                                "items": {"type": "string"},
                                "description": "Entity ids that must appear in the episode.",
                            },
                        },
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
    search_backend: SearchBackend,
) -> Optional[List[TextContent]]:
    if name not in SEARCH_TOOL_NAMES:
        return None
    args = arguments or {}
    return _handle_search_corpus(args, search_backend=search_backend)


def _handle_search_corpus(
    args: dict,
    *,
    search_backend: SearchBackend,
) -> List[TextContent]:
    query = (args.get("query") or "").strip()
    if not query:
        return [TextContent(type="text", text=json.dumps({"results": [], "error": "empty query"}))]
    mode = SearchMode((args.get("mode") or "hybrid").lower())
    limit = int(args.get("limit") or 10)
    filters = _parse_filters(args.get("filters"))

    hits = search_backend.search(query, mode=mode, limit=limit, filters=filters)
    payload = {
        "query": query,
        "mode": mode.value,
        "results": [h.as_citation() for h in hits],
        "total": len(hits),
    }
    return [TextContent(type="text", text=json.dumps(payload))]


def _parse_filters(raw) -> Optional[SearchFilters]:
    if not raw:
        return None
    date_range = raw.get("date_range") or {}
    has_entity = raw.get("has_entity") or []
    return SearchFilters(
        podcast_id=raw.get("podcast_id"),
        date_from=date_range.get("from"),
        date_to=date_range.get("to"),
        has_entity=tuple(has_entity),
    )
