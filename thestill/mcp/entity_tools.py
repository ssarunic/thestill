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

"""Spec #28 §1.8 — MCP alpha tools for the entity layer.

Five tools, each returning citation-shaped rows (Strategy §4) so
Claude/ChatGPT can compose narrative answers without round-tripping
back to the server for context:

- ``find_mentions(entity, entity_type?, podcast_id?, date_range?,
  role?, limit?) → CitationRow[]``
- ``list_quotes_by(speaker, topic?, podcast_id?, date_range?,
  limit?) → CitationRow[]``
- ``get_episode_clip(episode_id, start_ms, end_ms?, plus_minus_sec?)
  → CitationRow``
- ``get_entity(id_or_name, entity_type?) → {entity, mention_count,
  cooccurring, recent_mentions}``
- ``list_episodes_by_entity(has_entity[], podcast_id?, date_range?,
  limit?) → EpisodeSummary[]``

These are SQL-only against the entity tables. The hybrid
``search_corpus`` tool delegates to ``SqliteVecBackend`` (Phase 2.10).
"""

from __future__ import annotations

import json
from datetime import datetime
from typing import Any, List, Optional

from mcp.types import TextContent, Tool

from ..repositories.sqlite_entity_repository import MentionContext, SqliteEntityRepository
from ..search.citation import build_citation_rows
from ..utils.datetime_utils import now_utc

# Names of the tools this module owns — used by the dispatcher in
# ``tools.py`` to decide whether to delegate.
ENTITY_TOOL_NAMES = frozenset(
    {
        "find_mentions",
        "list_quotes_by",
        "get_episode_clip",
        "get_entity",
        "list_episodes_by_entity",
    }
)


def entity_tool_definitions() -> List[Tool]:
    """Return the ``Tool`` definitions for spec #28 §1.8 tools.

    Called from ``setup_tools.list_tools`` to extend the unified
    tool list. Schemas are JSON-Schema; descriptions are intent-named
    so the LLM picks the right tool for the right phrasing
    (Strategy §3).
    """
    return [
        Tool(
            name="find_mentions",
            description=(
                "Find every resolved mention of an entity (person, company, product, or topic) "
                "across the corpus. Returns citation-shaped rows with episode title, "
                "speaker, quote excerpt, timestamps, and a deeplink. Use this for "
                '"what has X been mentioned in?" or "all clips where Y appears".'
            ),
            inputSchema={
                "type": "object",
                "properties": {
                    "entity": {
                        "type": "string",
                        "description": (
                            "Entity name (canonical, alias, or id like 'person:elon-musk'). "
                            "Resolved against entities table."
                        ),
                    },
                    "entity_type": {
                        "type": "string",
                        "enum": ["person", "company", "product", "topic"],
                        "description": "Disambiguation hint when multiple entities share a name.",
                    },
                    "podcast_id": {
                        "type": "string",
                        "description": "Restrict to one podcast (UUID or slug).",
                    },
                    "date_from": {
                        "type": "string",
                        "description": "ISO-8601 lower bound on episode pub_date (inclusive).",
                    },
                    "date_to": {
                        "type": "string",
                        "description": "ISO-8601 upper bound on episode pub_date (inclusive).",
                    },
                    "role": {
                        "type": "string",
                        "enum": ["host", "guest", "mentioned", "self"],
                        "description": "Filter by how the entity was named.",
                    },
                    "limit": {
                        "type": "integer",
                        "default": 50,
                        "description": "Max rows (default 50).",
                    },
                },
                "required": ["entity"],
            },
        ),
        Tool(
            name="list_quotes_by",
            description=(
                "List resolved mentions where a specific speaker said something — optionally "
                'filtered by topic. Use this for "what has Scott Galloway said about data '
                'centres?" or "every quote from Sam Altman in 2026". Speaker matching is '
                "case-insensitive substring."
            ),
            inputSchema={
                "type": "object",
                "properties": {
                    "speaker": {
                        "type": "string",
                        "description": "Speaker name (matches the diarised label).",
                    },
                    "topic": {
                        "type": "string",
                        "description": (
                            "Optional topic name or entity id — restricts to episodes where "
                            "the topic was also mentioned."
                        ),
                    },
                    "podcast_id": {"type": "string", "description": "Restrict to one podcast."},
                    "date_from": {"type": "string", "description": "ISO-8601 lower bound on pub_date."},
                    "date_to": {"type": "string", "description": "ISO-8601 upper bound on pub_date."},
                    "limit": {"type": "integer", "default": 50, "description": "Max rows."},
                },
                "required": ["speaker"],
            },
        ),
        Tool(
            name="get_episode_clip",
            description=(
                "Return one citation-shaped row for a specific clip in an episode. Used to "
                "turn a (episode_id, start_ms) pointer into a playable, quoted reference. "
                "If ``plus_minus_sec`` is set, the resulting end_ms is widened around the "
                "original mention."
            ),
            inputSchema={
                "type": "object",
                "properties": {
                    "episode_id": {"type": "string", "description": "Episode UUID."},
                    "start_ms": {
                        "type": "integer",
                        "description": "Position to anchor the clip at (milliseconds).",
                    },
                    "end_ms": {
                        "type": "integer",
                        "description": "Optional explicit end (otherwise the segment's end_ms).",
                    },
                    "plus_minus_sec": {
                        "type": "integer",
                        "description": "Symmetric window around start_ms in seconds.",
                    },
                },
                "required": ["episode_id", "start_ms"],
            },
        ),
        Tool(
            name="get_entity",
            description=(
                "Return an entity record plus its mention count, top co-occurring entities, "
                "and recent mentions. Use for entity pages and to disambiguate before "
                "calling ``find_mentions``."
            ),
            inputSchema={
                "type": "object",
                "properties": {
                    "id_or_name": {
                        "type": "string",
                        "description": (
                            "Canonical id ('person:elon-musk'), canonical name ('Elon Musk'), "
                            "or alias ('Musk'). Resolved in that order."
                        ),
                    },
                    "entity_type": {
                        "type": "string",
                        "enum": ["person", "company", "product", "topic"],
                        "description": "Disambiguation hint when multiple entities share a name.",
                    },
                },
                "required": ["id_or_name"],
            },
        ),
        Tool(
            name="list_episodes_by_entity",
            description=(
                "List episodes that contain at least one mention of every entity in "
                "``has_entity`` (set intersection). Returns episode summaries — title, "
                "podcast, pub_date, mention count for each requested entity. Use for "
                '"episodes where both Scott Galloway and Andrew Yang appear".'
            ),
            inputSchema={
                "type": "object",
                "properties": {
                    "has_entity": {
                        "type": "array",
                        "items": {"type": "string"},
                        "description": "Entity ids or names. AND-semantics.",
                    },
                    "podcast_id": {"type": "string", "description": "Restrict to one podcast."},
                    "date_from": {"type": "string", "description": "ISO-8601 lower bound on pub_date."},
                    "date_to": {"type": "string", "description": "ISO-8601 upper bound on pub_date."},
                    "limit": {"type": "integer", "default": 50, "description": "Max rows."},
                },
                "required": ["has_entity"],
            },
        ),
    ]


def dispatch_entity_tool(
    name: str,
    arguments: Any,
    repository: SqliteEntityRepository,
) -> Optional[List[TextContent]]:
    """Handle one of the spec #28 §1.8 tools.

    Returns a list of ``TextContent`` when the tool is recognised,
    or ``None`` when ``name`` isn't an entity tool — letting the
    caller fall through to the existing dispatcher chain.
    """
    if name not in ENTITY_TOOL_NAMES:
        return None
    args = arguments or {}
    if name == "find_mentions":
        return _handle_find_mentions(args, repository)
    if name == "list_quotes_by":
        return _handle_list_quotes_by(args, repository)
    if name == "get_episode_clip":
        return _handle_get_episode_clip(args, repository)
    if name == "get_entity":
        return _handle_get_entity(args, repository)
    if name == "list_episodes_by_entity":
        return _handle_list_episodes_by_entity(args, repository)
    return None


# ---------------------------------------------------------------------------
# Per-tool handlers
# ---------------------------------------------------------------------------


def _handle_find_mentions(args: dict, repo: SqliteEntityRepository) -> List[TextContent]:
    raw = args.get("entity")
    if not raw:
        return _err("'entity' is required")
    entity_type = args.get("entity_type")
    entity = repo.find_entity_by_name(raw, entity_type=entity_type)
    if entity is None:
        return _ok({"results": [], "matched_entity": None, "note": f"no entity matched {raw!r}"})
    date_range = _parse_date_range(args)
    contexts = repo.find_mentions(
        entity_id=entity.id,
        podcast_id=args.get("podcast_id"),
        date_range=date_range,
        role=args.get("role"),
        limit=int(args.get("limit", 50)),
    )
    rows = [r.model_dump(mode="json") for r in build_citation_rows(contexts)]
    return _ok(
        {
            "matched_entity": _entity_to_dict(entity),
            "results": rows,
        }
    )


def _handle_list_quotes_by(args: dict, repo: SqliteEntityRepository) -> List[TextContent]:
    speaker = args.get("speaker")
    if not speaker:
        return _err("'speaker' is required")
    topic_id: Optional[str] = None
    if args.get("topic"):
        topic = repo.find_entity_by_name(args["topic"])
        if topic is not None:
            topic_id = topic.id
    contexts = repo.list_mentions_by_speaker(
        speaker=speaker,
        topic_entity_id=topic_id,
        podcast_id=args.get("podcast_id"),
        date_range=_parse_date_range(args),
        limit=int(args.get("limit", 50)),
    )
    rows = [r.model_dump(mode="json") for r in build_citation_rows(contexts)]
    return _ok({"speaker": speaker, "topic_entity_id": topic_id, "results": rows})


def _handle_get_episode_clip(args: dict, repo: SqliteEntityRepository) -> List[TextContent]:
    episode_id = args.get("episode_id")
    start_ms = args.get("start_ms")
    if episode_id is None or start_ms is None:
        return _err("'episode_id' and 'start_ms' are required")
    ctx = repo.get_mention_for_clip(
        episode_id=episode_id,
        start_ms=int(start_ms),
        end_ms=args.get("end_ms"),
    )
    if ctx is None:
        return _ok({"result": None, "note": "no resolved mention near that timestamp"})
    rows = build_citation_rows([ctx])
    row = rows[0]
    plus_minus = args.get("plus_minus_sec")
    if plus_minus is not None:
        # Widen end_ms symmetrically around the requested start.
        widened_end = int(start_ms) + int(plus_minus) * 1000
        row = row.model_copy(update={"end_ms": max(widened_end, row.end_ms)})
    return _ok({"result": row.model_dump(mode="json")})


def _handle_get_entity(args: dict, repo: SqliteEntityRepository) -> List[TextContent]:
    id_or_name = args.get("id_or_name")
    if not id_or_name:
        return _err("'id_or_name' is required")
    entity = repo.find_entity_by_name(id_or_name, entity_type=args.get("entity_type"))
    if entity is None:
        return _ok({"result": None, "note": f"no entity matched {id_or_name!r}"})
    summary = repo.get_entity_summary(entity.id)
    if summary is None:
        return _ok({"result": None, "note": "entity disappeared mid-query"})
    return _ok(
        {
            "result": {
                "entity": _entity_to_dict(summary["entity"]),
                "mention_count": summary["mention_count"],
                "cooccurring": [
                    {
                        "entity": _entity_to_dict(c["entity"]),
                        "episode_count": c["episode_count"],
                        "last_seen_at": c["last_seen_at"],
                    }
                    for c in summary["cooccurring"]
                ],
                "recent_mentions": [r.model_dump(mode="json") for r in build_citation_rows(summary["recent_mentions"])],
            }
        }
    )


def _handle_list_episodes_by_entity(args: dict, repo: SqliteEntityRepository) -> List[TextContent]:
    names = args.get("has_entity") or []
    if not names:
        return _err("'has_entity' must contain at least one entity")
    # Resolve names → ids; missing ones short-circuit the AND-set.
    resolved_ids: List[str] = []
    unresolved: List[str] = []
    for name in names:
        ent = repo.find_entity_by_name(name)
        if ent is None:
            unresolved.append(name)
        else:
            resolved_ids.append(ent.id)
    if unresolved:
        return _ok({"results": [], "unresolved_names": unresolved})

    # AND-intersection on episode_id: episodes that contain ALL the
    # requested entities (one row per resolved mention; dedupe + count).
    placeholders = ",".join("?" * len(resolved_ids))
    sql = f"""
        SELECT e.id, e.title, e.pub_date, p.id AS podcast_id, p.title AS podcast_title,
               p.slug AS podcast_slug
        FROM episodes e
        JOIN podcasts p ON e.podcast_id = p.id
        WHERE e.id IN (
            SELECT episode_id FROM entity_mentions
            WHERE resolution_status = 'resolved' AND entity_id IN ({placeholders})
            GROUP BY episode_id
            HAVING COUNT(DISTINCT entity_id) = ?
        )
    """
    params: list = list(resolved_ids) + [len(resolved_ids)]
    if args.get("podcast_id"):
        sql += " AND p.id = ?"
        params.append(args["podcast_id"])
    date_range = _parse_date_range(args)
    if date_range is not None:
        sql += " AND e.pub_date BETWEEN ? AND ?"
        params.append(date_range[0].isoformat())
        params.append(date_range[1].isoformat())
    sql += " ORDER BY e.pub_date DESC LIMIT ?"
    params.append(int(args.get("limit", 50)))
    with repo._get_connection() as conn:
        rows = conn.execute(sql, params).fetchall()
    return _ok(
        {
            "matched_entity_ids": resolved_ids,
            "results": [
                {
                    "episode_id": row["id"],
                    "episode_title": row["title"],
                    "published_at": row["pub_date"],
                    "podcast_id": row["podcast_id"],
                    "podcast_title": row["podcast_title"],
                    "podcast_slug": row["podcast_slug"],
                }
                for row in rows
            ],
        }
    )


# ---------------------------------------------------------------------------
# Utilities
# ---------------------------------------------------------------------------


def _parse_date_range(args: dict):
    a = args.get("date_from")
    b = args.get("date_to")
    if not a and not b:
        return None
    if not (a and b):
        # Spec wants a closed interval; if only one bound is given,
        # synthesise the other to "epoch / now".
        a = a or "1970-01-01T00:00:00"
        b = b or now_utc().isoformat()
    return (datetime.fromisoformat(a), datetime.fromisoformat(b))


def _entity_to_dict(entity) -> dict:
    return {
        "id": entity.id,
        "type": entity.type.value,
        "canonical_name": entity.canonical_name,
        "wikidata_qid": entity.wikidata_qid,
        "aliases": entity.aliases,
        "description": entity.description,
    }


def _ok(payload: dict) -> List[TextContent]:
    return [TextContent(type="text", text=json.dumps({"success": True, **payload}, default=str))]


def _err(message: str) -> List[TextContent]:
    return [TextContent(type="text", text=json.dumps({"success": False, "error": message}))]
