# Copyright 2025 thestill.ai
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

"""
MCP Tools - Action handlers

Provides MCP tools for managing podcasts and retrieving information.
"""

import json
import logging
from typing import Any

from mcp.server import Server
from mcp.types import TextContent, Tool

from ..services import PodcastService, StatsService

logger = logging.getLogger(__name__)


def setup_tools(server: Server, storage_path: str):
    """
    Set up all MCP tools for the server.

    Args:
        server: MCP server instance
        storage_path: Path to data storage
    """
    podcast_service = PodcastService(storage_path)
    stats_service = StatsService(storage_path)

    @server.list_tools()
    async def list_tools() -> list[Tool]:
        """List available tools."""
        logger.debug("Listing available tools")
        return [
            Tool(
                name="add_podcast",
                description="Add a new podcast to tracking. Supports RSS URLs, Apple Podcast URLs, and YouTube channels/playlists.",
                inputSchema={
                    "type": "object",
                    "properties": {
                        "url": {
                            "type": "string",
                            "description": "RSS feed URL, Apple Podcast URL, or YouTube channel/playlist URL",
                        }
                    },
                    "required": ["url"],
                },
            ),
            Tool(
                name="remove_podcast",
                description="Remove a podcast from tracking by index number or RSS URL.",
                inputSchema={
                    "type": "object",
                    "properties": {
                        "podcast_id": {"type": "string", "description": "Podcast index (1, 2, 3...) or RSS URL"}
                    },
                    "required": ["podcast_id"],
                },
            ),
            Tool(
                name="list_podcasts",
                description="List all tracked podcasts with their indices and statistics.",
                inputSchema={"type": "object", "properties": {}},
            ),
            Tool(
                name="list_episodes",
                description="List episodes for a specific podcast with optional filtering.",
                inputSchema={
                    "type": "object",
                    "properties": {
                        "podcast_id": {"type": "string", "description": "Podcast index (1, 2, 3...) or RSS URL"},
                        "limit": {
                            "type": "integer",
                            "description": "Maximum number of episodes to return (default: 10)",
                            "default": 10,
                        },
                        "since_hours": {
                            "type": "integer",
                            "description": "Only include episodes published in the last N hours (optional)",
                        },
                    },
                    "required": ["podcast_id"],
                },
            ),
            Tool(
                name="get_status",
                description="Get system-wide statistics including podcast count, episode counts, and processing status.",
                inputSchema={"type": "object", "properties": {}},
            ),
            Tool(
                name="get_transcript",
                description="Get the cleaned Markdown transcript for a specific episode. Returns the processed transcript from the processed/ directory.",
                inputSchema={
                    "type": "object",
                    "properties": {
                        "podcast_id": {"type": "string", "description": "Podcast index (1, 2, 3...) or RSS URL"},
                        "episode_id": {
                            "type": "string",
                            "description": "Episode index (1=latest, 2=second latest, etc.), 'latest', date (YYYY-MM-DD), or GUID",
                        },
                    },
                    "required": ["podcast_id", "episode_id"],
                },
            ),
        ]

    @server.call_tool()
    async def call_tool(name: str, arguments: Any) -> list[TextContent]:
        """
        Call a tool with given arguments.

        Args:
            name: Tool name
            arguments: Tool arguments

        Returns:
            List of text content results
        """
        logger.info(f"Calling tool: {name} with args: {arguments}")

        try:
            if name == "add_podcast":
                url = arguments.get("url")
                if not url:
                    return [
                        TextContent(
                            type="text", text=json.dumps({"success": False, "error": "Missing required parameter: url"})
                        )
                    ]

                podcast = podcast_service.add_podcast(url)
                if podcast:
                    # Get the podcast index
                    podcasts = podcast_service.list_podcasts()
                    podcast_index = next((p.index for p in podcasts if str(p.rss_url) == str(podcast.rss_url)), 0)

                    result = {
                        "success": True,
                        "message": f"Podcast added: {podcast.title}",
                        "podcast_index": podcast_index,
                        "podcast": {
                            "title": podcast.title,
                            "description": podcast.description,
                            "rss_url": str(podcast.rss_url),
                        },
                    }
                else:
                    result = {"success": False, "error": "Failed to add podcast or podcast already exists"}

                return [TextContent(type="text", text=json.dumps(result, indent=2))]

            elif name == "remove_podcast":
                podcast_id = arguments.get("podcast_id")
                if not podcast_id:
                    return [
                        TextContent(
                            type="text",
                            text=json.dumps({"success": False, "error": "Missing required parameter: podcast_id"}),
                        )
                    ]

                # Convert to int if it's a numeric string
                if isinstance(podcast_id, str) and podcast_id.isdigit():
                    podcast_id = int(podcast_id)

                success = podcast_service.remove_podcast(podcast_id)
                if success:
                    result = {"success": True, "message": "Podcast removed successfully"}
                else:
                    result = {"success": False, "error": f"Podcast not found: {podcast_id}"}

                return [TextContent(type="text", text=json.dumps(result, indent=2))]

            elif name == "list_podcasts":
                podcasts = podcast_service.list_podcasts()

                result = {
                    "podcasts": [
                        {
                            "index": p.index,
                            "title": p.title,
                            "rss_url": p.rss_url,
                            "episodes_count": p.episodes_count,
                            "episodes_processed": p.episodes_processed,
                            "last_processed": p.last_processed.isoformat() if p.last_processed else None,
                        }
                        for p in podcasts
                    ]
                }

                return [TextContent(type="text", text=json.dumps(result, indent=2))]

            elif name == "list_episodes":
                podcast_id = arguments.get("podcast_id")
                if not podcast_id:
                    return [
                        TextContent(
                            type="text",
                            text=json.dumps({"success": False, "error": "Missing required parameter: podcast_id"}),
                        )
                    ]

                # Convert to int if it's a numeric string
                if isinstance(podcast_id, str) and podcast_id.isdigit():
                    podcast_id = int(podcast_id)

                limit = arguments.get("limit", 10)
                since_hours = arguments.get("since_hours")

                episodes = podcast_service.list_episodes(podcast_id, limit, since_hours)
                if episodes is None:
                    return [
                        TextContent(
                            type="text",
                            text=json.dumps({"success": False, "error": f"Podcast not found: {podcast_id}"}),
                        )
                    ]

                # Get podcast info
                podcast = podcast_service.get_podcast(podcast_id)

                result = {
                    "podcast_title": podcast.title if podcast else "Unknown",
                    "podcast_index": episodes[0].podcast_index if episodes else 0,
                    "episodes": [
                        {
                            "index": ep.episode_index,
                            "title": ep.title,
                            "pub_date": ep.pub_date.isoformat() if ep.pub_date else None,
                            "duration": ep.duration,
                            "state": ep.state,
                            "transcript_available": ep.transcript_available,
                            "summary_available": ep.summary_available,
                        }
                        for ep in episodes
                    ],
                }

                return [TextContent(type="text", text=json.dumps(result, indent=2))]

            elif name == "get_status":
                stats = stats_service.get_stats()

                result = {
                    "podcasts_tracked": stats.podcasts_tracked,
                    "episodes_total": stats.episodes_total,
                    "episodes_processed": stats.episodes_processed,
                    "episodes_unprocessed": stats.episodes_unprocessed,
                    "transcripts_available": stats.transcripts_available,
                    "audio_files_count": stats.audio_files_count,
                    "storage_path": stats.storage_path,
                    "last_updated": stats.last_updated.isoformat(),
                }

                return [TextContent(type="text", text=json.dumps(result, indent=2))]

            elif name == "get_transcript":
                podcast_id = arguments.get("podcast_id")
                episode_id = arguments.get("episode_id")

                if not podcast_id or not episode_id:
                    return [
                        TextContent(
                            type="text",
                            text=json.dumps(
                                {"success": False, "error": "Missing required parameters: podcast_id and episode_id"}
                            ),
                        )
                    ]

                # Convert to int if it's a numeric string
                if isinstance(podcast_id, str) and podcast_id.isdigit():
                    podcast_id = int(podcast_id)
                if isinstance(episode_id, str) and episode_id.isdigit():
                    episode_id = int(episode_id)

                transcript = podcast_service.get_transcript(podcast_id, episode_id)

                if transcript is None:
                    return [
                        TextContent(
                            type="text",
                            text=json.dumps(
                                {
                                    "success": False,
                                    "error": f"Episode not found: podcast={podcast_id}, episode={episode_id}",
                                }
                            ),
                        )
                    ]

                # If transcript starts with "N/A", it means it's not available
                if transcript.startswith("N/A"):
                    return [TextContent(type="text", text=json.dumps({"success": False, "error": transcript}))]

                # Return the transcript content directly (not JSON-encoded)
                return [TextContent(type="text", text=transcript)]

            else:
                logger.error(f"Unknown tool: {name}")
                return [TextContent(type="text", text=json.dumps({"success": False, "error": f"Unknown tool: {name}"}))]

        except Exception as e:
            logger.error(f"Error calling tool {name}: {e}", exc_info=True)
            return [TextContent(type="text", text=json.dumps({"success": False, "error": str(e)}))]
