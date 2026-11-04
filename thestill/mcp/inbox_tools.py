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

- ``import_episode(url)`` — the twin of ``POST /api/imports``: an Apple
  Podcasts, YouTube, Spotify or audio-file URL becomes an episode in the
  caller's inbox. Idempotent; a repeat says ``already_in_inbox``.
- ``send_to_inbox(episode_id | episode_url)`` — the twin of the web "Send to
  my inbox" button for an episode Thestill already has.

Both are thin: every rule (imports switch, ``imports_per_day``, immutable
delivery, the pipeline guard) lives in ``ImportService`` / ``InboxService``.
Scope (``follows``), read-only mode and the mutation rate limit are applied
by the dispatcher in ``tools.py`` before this module is reached.
"""

from __future__ import annotations

import json
import re
from typing import Any, List, Optional
from urllib.parse import unquote, urlparse

from mcp.types import TextContent, Tool

from ..services.import_service import ImportError as ImportServiceError
from ..utils.url_generator import episode_links

INBOX_TOOL_NAMES = frozenset({"import_episode", "send_to_inbox"})

# An episode page on this instance: /podcasts/<podcast-slug>/episodes/<episode-slug>[/…]
_EPISODE_PATH_RE = re.compile(r"^/podcasts/([^/]+)/episodes/([^/]+)")


def inbox_tool_definitions() -> List[Tool]:
    return [
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
    if name == "import_episode":
        return _import_episode(arguments, user_id, import_service, inbox_repository, base_url)
    if name == "send_to_inbox":
        return _send_to_inbox(arguments, user_id, inbox_service, inbox_repository, podcast_repository, base_url)
    return None


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
    """The episode block both tools return; ``{}`` when the item is missing."""
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
