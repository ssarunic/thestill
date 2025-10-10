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
MCP Resources - Read-only data access

Provides MCP resources for podcasts, episodes, and transcripts.
"""

import json
import logging
from typing import Any
from urllib.parse import unquote

from mcp.server import Server
from mcp.types import Resource, TextContent

from ..services import PodcastService
from .utils import parse_thestill_uri, build_podcast_uri, build_episode_uri, build_transcript_uri, build_audio_uri

logger = logging.getLogger(__name__)


def setup_resources(server: Server, storage_path: str):
    """
    Set up all MCP resources for the server.

    Args:
        server: MCP server instance
        storage_path: Path to data storage
    """
    podcast_service = PodcastService(storage_path)

    @server.list_resources()
    async def list_resources() -> list[Resource]:
        """
        List available resources.

        Returns base resource templates that clients can use.
        """
        logger.debug("Listing available resources")
        return [
            Resource(
                uri="thestill://podcasts/{podcast_id}",
                name="Podcast metadata",
                description="Get podcast information by index (1, 2, 3...) or RSS URL",
                mimeType="application/json"
            ),
            Resource(
                uri="thestill://podcasts/{podcast_id}/episodes/{episode_id}",
                name="Episode metadata",
                description="Get episode information by podcast and episode ID",
                mimeType="application/json"
            ),
            Resource(
                uri="thestill://podcasts/{podcast_id}/episodes/{episode_id}/transcript",
                name="Episode transcript",
                description="Get cleaned transcript in Markdown format",
                mimeType="text/markdown"
            ),
            Resource(
                uri="thestill://podcasts/{podcast_id}/episodes/{episode_id}/audio",
                name="Episode audio reference",
                description="Get audio file URL and metadata",
                mimeType="application/json"
            )
        ]

    @server.read_resource()
    async def read_resource(uri: str) -> str:
        """
        Read a resource by URI.

        Args:
            uri: Resource URI (thestill://podcasts/...)

        Returns:
            Resource content as string
        """
        logger.info(f"Reading resource: {uri}")

        # Parse the thestill:// URI
        try:
            parsed = parse_thestill_uri(uri)
            resource_type = parsed["resource"]
            podcast_id = parsed["podcast_id"]
            episode_id = parsed.get("episode_id")

            logger.debug(f"Parsed URI: resource={resource_type}, podcast={podcast_id}, episode={episode_id}")

        except ValueError as e:
            logger.error(f"Invalid URI: {uri} - {e}")
            raise ValueError(f"Invalid URI: {uri}. {str(e)}")

        # Handle podcast resource
        if resource_type == "podcast":
            podcast = podcast_service.get_podcast(podcast_id)
            if not podcast:
                logger.warning(f"Podcast not found: {podcast_id}")
                raise ValueError(f"Podcast not found: {podcast_id}")

            # Get podcast index
            podcasts = podcast_service.list_podcasts()
            podcast_index = next(
                (p.index for p in podcasts if str(p.rss_url) == str(podcast.rss_url)),
                0
            )

            # Build response
            result = {
                "index": podcast_index,
                "title": podcast.title,
                "description": podcast.description,
                "rss_url": str(podcast.rss_url),
                "last_processed": podcast.last_processed.isoformat() if podcast.last_processed else None,
                "episodes_count": len(podcast.episodes),
                "episodes_processed": sum(1 for ep in podcast.episodes if ep.processed)
            }

            return json.dumps(result, indent=2)

        # Handle episode resource
        elif resource_type == "episode":
            episode = podcast_service.get_episode(podcast_id, episode_id)
            if not episode:
                logger.warning(f"Episode not found: {podcast_id}/{episode_id}")
                raise ValueError(f"Episode not found: {podcast_id}/{episode_id}")

            # Get indices
            podcasts = podcast_service.list_podcasts()
            podcast = podcast_service.get_podcast(podcast_id)
            if not podcast:
                raise ValueError(f"Podcast not found: {podcast_id}")

            podcast_index = next(
                (p.index for p in podcasts if str(p.rss_url) == str(podcast.rss_url)),
                0
            )

            # Get episode index (latest = 1, second latest = 2, etc.)
            sorted_episodes = sorted(
                podcast.episodes,
                key=lambda ep: ep.pub_date or "",
                reverse=True
            )
            episode_index = next(
                (idx for idx, ep in enumerate(sorted_episodes, start=1) if ep.guid == episode.guid),
                0
            )

            # Build response
            from pathlib import Path
            storage_path = Path(podcast_service.storage_path)
            result = {
                "podcast_index": podcast_index,
                "episode_index": episode_index,
                "title": episode.title,
                "description": episode.description,
                "pub_date": episode.pub_date.isoformat() if episode.pub_date else None,
                "duration": episode.duration,
                "guid": episode.guid,
                "processed": episode.processed,
                "audio_url": str(episode.audio_url),
                "transcript_available": bool(episode.raw_transcript_path and (storage_path / "raw_transcripts" / episode.raw_transcript_path).exists()),
                "clean_transcript_available": bool(episode.clean_transcript_path and (storage_path / "clean_transcripts" / episode.clean_transcript_path).exists()),
                "summary_available": bool(episode.summary_path and (storage_path / "summaries" / episode.summary_path).exists())
            }

            return json.dumps(result, indent=2)

        # Handle transcript resource
        elif resource_type == "transcript":
            transcript = podcast_service.get_transcript(podcast_id, episode_id)
            if transcript is None:
                logger.warning(f"Episode not found for transcript: {podcast_id}/{episode_id}")
                raise ValueError(f"Episode not found: {podcast_id}/{episode_id}")

            return transcript

        # Handle audio resource
        elif resource_type == "audio":
            episode = podcast_service.get_episode(podcast_id, episode_id)
            if not episode:
                logger.warning(f"Episode not found for audio: {podcast_id}/{episode_id}")
                raise ValueError(f"Episode not found: {podcast_id}/{episode_id}")

            # Build audio reference response
            from pathlib import Path
            result = {
                "audio_url": str(episode.audio_url),
                "duration": episode.duration,
                "title": episode.title,
                "local_file": episode.audio_path is not None,  # Indicates if downloaded
                "format": "audio/mpeg"  # Default, could be enhanced with actual format detection
            }

            return json.dumps(result, indent=2)

        else:
            logger.error(f"Unknown resource type: {resource_type}")
            raise ValueError(f"Unknown resource type: {resource_type}")
