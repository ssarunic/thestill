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

"""Inbox tools for the MCP server (spec #112).

- ``list_inbox(state?, q?, limit?, before?)`` — the twin of ``GET /api/inbox``:
  the caller's inbox, newest delivery first, with the unread count. Read
  only (``read`` scope); listing never flips a row to read.
- ``import_episode(url)`` — the twin of ``POST /api/imports``: an Apple
  Podcasts, YouTube, Spotify or audio-file URL becomes an episode in the
  caller's inbox. Idempotent; a repeat says ``already_in_inbox``.
- ``send_to_inbox(episode_id | episode_url)`` — the twin of the web "Send to
  my inbox" button for an episode Thestill already has.

All three are thin: every rule (imports switch, ``imports_per_day``, immutable
delivery, the pipeline guard) lives in ``ImportService`` / ``InboxService``.
Scope, read-only mode and the mutation rate limit (writes only) are applied
by the dispatcher in ``tools.py`` before this module is reached.
"""

from __future__ import annotations

import json
import re
from datetime import datetime
from typing import Any, List, Optional
from urllib.parse import unquote, urlparse

from mcp.types import TextContent, Tool

from ..models.inbox import INBOX_STATES
from ..services.import_service import ImportError as ImportServiceError
from ..services.inbox_service import InvalidInboxQueryError, InvalidInboxStateError
from ..utils.datetime_utils import ensure_utc
from ..utils.url_generator import episode_links

#: The inbox tools that write (and may start the pipeline): rate-limited and
#: refused in read-only mode, like ``add_podcast``.
INBOX_WRITE_TOOL_NAMES = frozenset({"import_episode", "send_to_inbox"})
INBOX_TOOL_NAMES = INBOX_WRITE_TOOL_NAMES | {"list_inbox"}

LIST_DEFAULT_LIMIT = 10
LIST_MAX_LIMIT = 50

# An episode page on this instance: /podcasts/<podcast-slug>/episodes/<episode-slug>[/…]
_EPISODE_PATH_RE = re.compile(r"^/podcasts/([^/]+)/episodes/([^/]+)")


def inbox_tool_definitions() -> List[Tool]:
    return [
        Tool(
            name="list_inbox",
            description=(
                "List your Thestill inbox, newest delivery first: the episodes delivered to you from "
                "podcasts you follow, plus ones you imported or sent there. Use it for \"what's new in my "
                'inbox" or "my latest episodes". Each row has the inbox state (unread, read, saved), '
                "when it arrived, whether its summary is ready, and the episode id for get_summary or "
                "get_transcript. Dismissed episodes are left out unless state=dismissed. Also returns "
                "your unread count. Pass next_before back as before for older rows."
            ),
            input_schema={
                "type": "object",
                "properties": {
                    "state": {
                        "type": "string",
                        "enum": list(INBOX_STATES),
                        "description": "Only rows in this state (default: all except dismissed)",
                    },
                    "q": {
                        "type": "string",
                        "description": "Words that must all appear in the episode title, show title or description",
                    },
                    "limit": {
                        "type": "integer",
                        "minimum": 1,
                        "maximum": LIST_MAX_LIMIT,
                        "description": f"Rows to return (default {LIST_DEFAULT_LIMIT}, max {LIST_MAX_LIMIT})",
                    },
                    "before": {
                        "type": "string",
                        "description": "next_before from a previous call, for the next (older) page",
                    },
                },
            },
        ),
        Tool(
            name="import_episode",
            description=(
                "Import one podcast episode into your Thestill inbox from its link: an Apple Podcasts "
                "episode link (with ?i=…), a YouTube video, a Spotify episode link, or a direct audio "
                "file URL. Thestill transcribes and summarises it. Safe to repeat: an episode already "
                "in your inbox is reported as already_in_inbox. One URL per call."
            ),
            input_schema={
                "type": "object",
                "properties": {
                    "url": {
                        "type": "string",
                        "description": "Episode link (Apple Podcasts, YouTube, Spotify or audio file)",
                    }
                },
                "required": ["url"],
            },
        ),
        Tool(
            name="send_to_inbox",
            description=(
                "Send an episode Thestill already has to your inbox, e.g. one found with search_corpus, "
                "find_mentions or list_episodes. Pass exactly one of episode_id (from citation rows) or "
                "episode_url (the Thestill episode page link). Safe to repeat."
            ),
            input_schema={
                "type": "object",
                "properties": {
                    "episode_id": {"type": "string", "description": "Episode UUID"},
                    "episode_url": {
                        "type": "string",
                        "description": "Thestill episode page URL (…/podcasts/<show>/episodes/<episode>)",
                    },
                },
            },
        ),
    ]


def dispatch_inbox_tool(
    name: str,
    arguments: Any,
    *,
    user_id: str,
    import_service,
    inbox_service,
    inbox_repository,
    podcast_repository,
    base_url: str,
) -> Optional[List[TextContent]]:
    """Run an inbox tool for ``user_id``; ``None`` when ``name`` isn't one.

    Episodes are described from the caller's inbox item, never a full
    ``Podcast``: the synthetic ``audio-imports`` parent of a bare-audio
    import doesn't validate as one (its feed URL is ``synthetic://``).
    """
    if name == "list_inbox":
        return _list_inbox(arguments, user_id, inbox_service, base_url)
    if name == "import_episode":
        return _import_episode(arguments, user_id, import_service, inbox_repository, base_url)
    if name == "send_to_inbox":
        return _send_to_inbox(arguments, user_id, inbox_service, inbox_repository, podcast_repository, base_url)
    return None


def _list_inbox(arguments, user_id, inbox_service, base_url) -> List[TextContent]:
    arguments = arguments or {}
    state = str(arguments.get("state") or "").strip() or None
    q = str(arguments.get("q") or "").strip() or None

    raw_limit = arguments.get("limit")
    try:
        limit = LIST_DEFAULT_LIMIT if raw_limit in (None, "") else int(raw_limit)
    except (TypeError, ValueError):
        return _reply({"success": False, "error": f"limit must be a whole number from 1 to {LIST_MAX_LIMIT}."})
    if not 1 <= limit <= LIST_MAX_LIMIT:
        return _reply({"success": False, "error": f"limit must be a whole number from 1 to {LIST_MAX_LIMIT}."})

    before = None
    raw_before = str(arguments.get("before") or "").strip()
    if raw_before:
        try:
            before = ensure_utc(datetime.fromisoformat(raw_before.replace("Z", "+00:00")))
        except ValueError:
            return _reply({"success": False, "error": "before must be the next_before value from a previous call."})

    try:
        items = inbox_service.list(user_id, state=state, limit=limit, before=before, q=q)
    except (InvalidInboxStateError, InvalidInboxQueryError) as exc:
        # Authored by InboxService for the web's 400s: safe to relay.
        return _reply({"success": False, "error": str(exc)})

    rows = []
    for item in items:
        row = _describe(item, base_url)
        episode = item.episode
        row.update(
            {
                "inbox_state": item.entry.state,
                "source": item.entry.source,
                "delivered_at": item.entry.delivered_at.isoformat(),
                "published_at": episode.pub_date.isoformat() if episode.pub_date else None,
                "duration_seconds": episode.duration,
                "summary_ready": bool(episode.summary_path),
            }
        )
        rows.append(row)
    return _reply(
        {
            "success": True,
            "unread_count": inbox_service.unread_count(user_id),
            "count": len(rows),
            "episodes": rows,
            "next_before": items[-1].entry.delivered_at.isoformat() if len(items) == limit else None,
        }
    )


def _import_episode(arguments, user_id, import_service, inbox_repository, base_url) -> List[TextContent]:
    url = str(arguments.get("url") or "").strip()
    if not url:
        return _reply({"success": False, "error": "Missing required parameter: url"})
    try:
        result = import_service.import_url(user_id=user_id, url=url)
    except ImportServiceError as exc:
        # User-facing by construction: import errors never embed exception
        # text (spec #112), so the web route and this tool both relay them.
        return _reply({"success": False, "error": str(exc)})

    episode = _describe(inbox_repository.get_item(user_id, result.episode_id), base_url)
    episode.setdefault("id", result.episode_id)
    episode.setdefault("title", result.title)
    episode["failed"] = result.episode_failed
    title = episode["title"]
    message = {
        "new_episode": f"Imported “{title}” into your inbox. Processing has started.",
        "added_existing": f"Added “{title}” to your inbox.",
        "already_in_inbox": f"“{title}” is already in your inbox.",
    }[result.outcome]
    if result.episode_failed:
        message += " Processing failed for this episode earlier; it won't be retried automatically."
    return _reply({"success": True, "outcome": result.outcome, "message": message, "episode": episode})


def _send_to_inbox(
    arguments, user_id, inbox_service, inbox_repository, podcast_repository, base_url
) -> List[TextContent]:
    episode_id = str(arguments.get("episode_id") or "").strip()
    episode_url = str(arguments.get("episode_url") or "").strip()
    if bool(episode_id) == bool(episode_url):
        return _reply({"success": False, "error": "Pass exactly one of episode_id or episode_url."})

    if episode_url:
        match = _EPISODE_PATH_RE.match(urlparse(episode_url).path)
        if not match:
            return _reply(
                {
                    "success": False,
                    "error": "episode_url must be a Thestill episode page (…/podcasts/<show>/episodes/<episode>).",
                }
            )
        found = podcast_repository.get_episode_by_slug(unquote(match[1]), unquote(match[2]))
        episode_id = found[1].id if found else ""
    # ``episode_exists`` is also the malformed-id guard: Postgres would reject
    # a non-UUID at the driver, so a typo stays "not found", not an error.
    if not episode_id or not inbox_service.episode_exists(episode_id):
        return _reply({"success": False, "error": f"Episode not found: {episode_url or episode_id}"})

    _entry, created = inbox_service.deliver_to_user(user_id, episode_id, source="ad_hoc")
    episode = _describe(inbox_repository.get_item(user_id, episode_id), base_url)
    episode.setdefault("id", episode_id)
    title = episode.get("title") or episode_id
    message = f"Sent “{title}” to your inbox." if created else f"“{title}” is already in your inbox."
    return _reply({"success": True, "created": created, "message": message, "episode": episode})


def _describe(item, base_url: str) -> dict:
    """The episode block every inbox tool returns; ``{}`` when the item is missing."""
    if item is None:
        return {}
    episode, podcast = item.episode, item.podcast
    state = episode.state.value if hasattr(episode.state, "value") else str(episode.state)
    links = episode_links(
        base_url=base_url,
        podcast_slug=podcast.slug,
        episode_slug=episode.slug,
        audio_url=str(episode.audio_url) if episode.audio_url else None,
    )
    return {
        "id": episode.id,
        "title": episode.title,
        "podcast_title": podcast.title,
        "state": state,
        "failed": bool(episode.is_failed),
        "episode_url": links["episode_url"],
        "audio_url": links["audio_url"],
    }


def _reply(payload: dict) -> List[TextContent]:
    return [TextContent(type="text", text=json.dumps(payload, indent=2))]
