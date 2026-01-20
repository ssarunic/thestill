# Copyright 2025 thestill.me
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
Includes pipeline operations: refresh, download, downsample, transcribe, clean.
"""

import json
import logging
from pathlib import Path
from typing import Any

from mcp.server import Server
from mcp.types import TextContent, Tool

from ..core.audio_downloader import AudioDownloader
from ..core.audio_preprocessor import AudioPreprocessor
from ..core.feed_manager import PodcastFeedManager
from ..models.transcription import TranscribeOptions
from ..repositories.sqlite_podcast_repository import SqlitePodcastRepository
from ..services import PodcastService, RefreshService, StatsService
from ..utils.config import load_config
from ..utils.path_manager import PathManager

logger = logging.getLogger(__name__)


def setup_tools(server: Server, storage_path: str):
    """
    Set up all MCP tools for the server.

    Args:
        server: MCP server instance
        storage_path: Path to data storage
    """
    # Load full config for database path and other settings
    config = load_config()

    # Initialize shared components
    path_manager = PathManager(storage_path)
    repository = SqlitePodcastRepository(db_path=config.database_path)
    podcast_service = PodcastService(storage_path, repository, path_manager)
    stats_service = StatsService(storage_path, repository, path_manager)
    feed_manager = PodcastFeedManager(repository, path_manager)
    refresh_service = RefreshService(feed_manager, podcast_service)
    audio_downloader = AudioDownloader(str(path_manager.original_audio_dir()))
    audio_preprocessor = AudioPreprocessor()

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
                description="Get the cleaned Markdown transcript for a specific episode. Returns the cleaned transcript from clean_transcripts/ directory. Episode must be in CLEANED or SUMMARIZED state.",
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
            # Pipeline tools - Processing operations
            Tool(
                name="refresh_feeds",
                description="Refresh podcast feeds to discover new episodes. This is step 1 of the pipeline. Does not download audio - just discovers what's new.",
                inputSchema={
                    "type": "object",
                    "properties": {
                        "podcast_id": {
                            "type": "string",
                            "description": "Optional: Podcast index (1, 2, 3...) or RSS URL to refresh only that podcast",
                        },
                        "max_episodes": {
                            "type": "integer",
                            "description": "Maximum episodes to discover per podcast (default: no limit)",
                        },
                    },
                    "required": [],
                },
            ),
            Tool(
                name="download_episodes",
                description="Download audio files for discovered episodes. This is step 2 of the pipeline. Episodes must be discovered first via refresh_feeds.",
                inputSchema={
                    "type": "object",
                    "properties": {
                        "podcast_id": {
                            "type": "string",
                            "description": "Optional: Podcast index or RSS URL to download only from that podcast",
                        },
                        "max_episodes": {
                            "type": "integer",
                            "description": "Maximum episodes to download (default: 5)",
                            "default": 5,
                        },
                    },
                    "required": [],
                },
            ),
            Tool(
                name="downsample_audio",
                description="Downsample downloaded audio to 16kHz WAV format for transcription. This is step 3 of the pipeline.",
                inputSchema={
                    "type": "object",
                    "properties": {
                        "podcast_id": {
                            "type": "string",
                            "description": "Optional: Podcast index or RSS URL to downsample only from that podcast",
                        },
                        "max_episodes": {
                            "type": "integer",
                            "description": "Maximum episodes to downsample (default: 5)",
                            "default": 5,
                        },
                    },
                    "required": [],
                },
            ),
            Tool(
                name="transcribe_episodes",
                description="Transcribe downsampled audio to JSON transcripts. This is step 4 of the pipeline. Requires audio to be downsampled first.",
                inputSchema={
                    "type": "object",
                    "properties": {
                        "podcast_id": {
                            "type": "string",
                            "description": "Optional: Podcast index or RSS URL to transcribe only from that podcast",
                        },
                        "max_episodes": {
                            "type": "integer",
                            "description": "Maximum episodes to transcribe (default: 1 due to processing time)",
                            "default": 1,
                        },
                    },
                    "required": [],
                },
            ),
            Tool(
                name="clean_transcripts",
                description="Clean raw transcripts with LLM processing for better readability. This is step 5 (final) of the pipeline.",
                inputSchema={
                    "type": "object",
                    "properties": {
                        "podcast_id": {
                            "type": "string",
                            "description": "Optional: Podcast index or RSS URL to clean only from that podcast",
                        },
                        "max_episodes": {
                            "type": "integer",
                            "description": "Maximum episodes to clean (default: 1 due to LLM costs)",
                            "default": 1,
                        },
                    },
                    "required": [],
                },
            ),
            Tool(
                name="process_episode",
                description="Run the full processing pipeline (download → downsample → transcribe → clean) for a specific episode. Convenient for processing a single episode end-to-end.",
                inputSchema={
                    "type": "object",
                    "properties": {
                        "podcast_id": {
                            "type": "string",
                            "description": "Podcast index (1, 2, 3...) or RSS URL",
                        },
                        "episode_id": {
                            "type": "string",
                            "description": "Episode index (1=latest), 'latest', date (YYYY-MM-DD), or GUID",
                        },
                    },
                    "required": ["podcast_id", "episode_id"],
                },
            ),
            Tool(
                name="summarize_episodes",
                description="Summarize cleaned transcripts with comprehensive analysis. This is step 6 of the pipeline. Produces executive summary, notable quotes, content angles, social snippets, and critical analysis.",
                inputSchema={
                    "type": "object",
                    "properties": {
                        "podcast_id": {
                            "type": "string",
                            "description": "Optional: Podcast index or RSS URL to summarize only from that podcast",
                        },
                        "max_episodes": {
                            "type": "integer",
                            "description": "Maximum episodes to summarize (default: 1 due to LLM costs)",
                            "default": 1,
                        },
                    },
                    "required": [],
                },
            ),
            Tool(
                name="get_summary",
                description="Get the summary for a specific episode. Returns the comprehensive analysis including executive summary, quotes, and content angles.",
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
                    podcasts = podcast_service.get_podcasts()
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
                podcasts = podcast_service.get_podcasts()

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

                episodes = podcast_service.get_episodes(podcast_id, limit=limit, since_hours=since_hours)
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

                transcript_result = podcast_service.get_transcript(podcast_id, episode_id)

                if transcript_result is None:
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

                # If transcript_type is None, it means transcript is not available
                if transcript_result.transcript_type is None:
                    return [
                        TextContent(
                            type="text", text=json.dumps({"success": False, "error": transcript_result.content})
                        )
                    ]

                # Return the transcript content directly (not JSON-encoded)
                return [TextContent(type="text", text=transcript_result.content)]

            # Pipeline tools implementation
            elif name == "refresh_feeds":
                podcast_id = arguments.get("podcast_id")
                max_episodes = arguments.get("max_episodes")

                # Convert to int if it's a numeric string
                if podcast_id and isinstance(podcast_id, str) and podcast_id.isdigit():
                    podcast_id = int(podcast_id)

                try:
                    result_obj = refresh_service.refresh(
                        podcast_id=podcast_id,
                        max_episodes=max_episodes,
                        max_episodes_per_podcast=max_episodes,
                        dry_run=False,
                    )

                    # Build episode list for response
                    episodes_discovered = []
                    for podcast, episodes in result_obj.episodes_by_podcast:
                        for ep in episodes:
                            episodes_discovered.append(
                                {
                                    "podcast_title": podcast.title,
                                    "episode_title": ep.title,
                                    "pub_date": ep.pub_date.isoformat() if ep.pub_date else None,
                                }
                            )

                    result = {
                        "success": True,
                        "message": f"Discovered {result_obj.total_episodes} new episode(s)",
                        "total_episodes": result_obj.total_episodes,
                        "episodes": episodes_discovered[:20],  # Limit response size
                        "next_step": "Run download_episodes to download the audio files",
                    }
                except ValueError as e:
                    result = {"success": False, "error": str(e)}

                return [TextContent(type="text", text=json.dumps(result, indent=2))]

            elif name == "download_episodes":
                podcast_id = arguments.get("podcast_id")
                max_episodes = arguments.get("max_episodes", 5)

                # Convert to int if it's a numeric string
                if podcast_id and isinstance(podcast_id, str) and podcast_id.isdigit():
                    podcast_id = int(podcast_id)

                # Get episodes that need downloading
                episodes_to_download = feed_manager.get_episodes_to_download(storage_path)

                if not episodes_to_download:
                    return [
                        TextContent(
                            type="text",
                            text=json.dumps(
                                {
                                    "success": True,
                                    "message": "No episodes found that need downloading",
                                    "hint": "Run refresh_feeds first to discover new episodes",
                                }
                            ),
                        )
                    ]

                # Filter by podcast_id if specified
                if podcast_id:
                    podcast = podcast_service.get_podcast(podcast_id)
                    if not podcast:
                        return [
                            TextContent(
                                type="text",
                                text=json.dumps({"success": False, "error": f"Podcast not found: {podcast_id}"}),
                            )
                        ]
                    episodes_to_download = [
                        (p, eps) for p, eps in episodes_to_download if str(p.rss_url) == str(podcast.rss_url)
                    ]

                # Apply max_episodes limit
                all_episodes = []
                for podcast, episodes in episodes_to_download:
                    for ep in episodes:
                        all_episodes.append((podcast, ep))
                        if len(all_episodes) >= max_episodes:
                            break
                    if len(all_episodes) >= max_episodes:
                        break

                # Download episodes
                downloaded = []
                failed = []
                for podcast, episode in all_episodes:
                    try:
                        audio_path = audio_downloader.download_episode(episode, podcast)
                        if audio_path:
                            # Store the relative path (includes podcast subdirectory)
                            feed_manager.mark_episode_downloaded(str(podcast.rss_url), episode.external_id, audio_path)
                            downloaded.append({"podcast": podcast.title, "episode": episode.title})
                        else:
                            failed.append(
                                {"podcast": podcast.title, "episode": episode.title, "error": "Download failed"}
                            )
                    except Exception as e:
                        failed.append({"podcast": podcast.title, "episode": episode.title, "error": str(e)})

                result = {
                    "success": True,
                    "message": f"Downloaded {len(downloaded)} episode(s)",
                    "downloaded": downloaded,
                    "failed": failed if failed else None,
                    "next_step": "Run downsample_audio to prepare for transcription",
                }
                return [TextContent(type="text", text=json.dumps(result, indent=2))]

            elif name == "downsample_audio":
                podcast_id = arguments.get("podcast_id")
                max_episodes = arguments.get("max_episodes", 5)

                # Convert to int if it's a numeric string
                if podcast_id and isinstance(podcast_id, str) and podcast_id.isdigit():
                    podcast_id = int(podcast_id)

                # Get episodes that need downsampling
                episodes_to_downsample = feed_manager.get_episodes_to_downsample(storage_path)

                if not episodes_to_downsample:
                    return [
                        TextContent(
                            type="text",
                            text=json.dumps(
                                {
                                    "success": True,
                                    "message": "No episodes found that need downsampling",
                                    "hint": "Run download_episodes first",
                                }
                            ),
                        )
                    ]

                # Filter by podcast_id if specified
                if podcast_id:
                    podcast = podcast_service.get_podcast(podcast_id)
                    if not podcast:
                        return [
                            TextContent(
                                type="text",
                                text=json.dumps({"success": False, "error": f"Podcast not found: {podcast_id}"}),
                            )
                        ]
                    episodes_to_downsample = [
                        (p, eps) for p, eps in episodes_to_downsample if str(p.rss_url) == str(podcast.rss_url)
                    ]

                # Apply max_episodes limit
                all_episodes = []
                for podcast, episodes in episodes_to_downsample:
                    for ep in episodes:
                        all_episodes.append((podcast, ep))
                        if len(all_episodes) >= max_episodes:
                            break
                    if len(all_episodes) >= max_episodes:
                        break

                # Downsample episodes
                downsampled = []
                failed = []
                for podcast, episode in all_episodes:
                    try:
                        original_audio_file = path_manager.original_audio_file(episode.audio_path)
                        if not original_audio_file.exists():
                            failed.append(
                                {"podcast": podcast.title, "episode": episode.title, "error": "Audio file not found"}
                            )
                            continue

                        downsampled_path = audio_preprocessor.downsample_audio(
                            str(original_audio_file), str(path_manager.downsampled_audio_dir())
                        )

                        if downsampled_path:
                            downsampled_filename = Path(downsampled_path).name
                            feed_manager.mark_episode_downsampled(
                                str(podcast.rss_url), episode.external_id, downsampled_filename
                            )
                            downsampled.append({"podcast": podcast.title, "episode": episode.title})
                        else:
                            failed.append(
                                {"podcast": podcast.title, "episode": episode.title, "error": "Downsampling failed"}
                            )
                    except Exception as e:
                        failed.append({"podcast": podcast.title, "episode": episode.title, "error": str(e)})

                result = {
                    "success": True,
                    "message": f"Downsampled {len(downsampled)} episode(s)",
                    "downsampled": downsampled,
                    "failed": failed if failed else None,
                    "next_step": "Run transcribe_episodes to create transcripts",
                }
                return [TextContent(type="text", text=json.dumps(result, indent=2))]

            elif name == "transcribe_episodes":
                podcast_id = arguments.get("podcast_id")
                max_episodes = arguments.get("max_episodes", 1)

                # Convert to int if it's a numeric string
                if podcast_id and isinstance(podcast_id, str) and podcast_id.isdigit():
                    podcast_id = int(podcast_id)

                # Get episodes that need transcription (downloaded/downsampled but not transcribed)
                episodes_to_transcribe = feed_manager.get_downloaded_episodes(storage_path)

                if not episodes_to_transcribe:
                    return [
                        TextContent(
                            type="text",
                            text=json.dumps(
                                {
                                    "success": True,
                                    "message": "No episodes found that need transcription",
                                    "hint": "Run downsample_audio first",
                                }
                            ),
                        )
                    ]

                # Filter by podcast_id if specified
                if podcast_id:
                    podcast = podcast_service.get_podcast(podcast_id)
                    if not podcast:
                        return [
                            TextContent(
                                type="text",
                                text=json.dumps({"success": False, "error": f"Podcast not found: {podcast_id}"}),
                            )
                        ]
                    episodes_to_transcribe = [
                        (p, ep) for p, ep in episodes_to_transcribe if str(p.rss_url) == str(podcast.rss_url)
                    ]

                # Apply max_episodes limit
                episodes_to_transcribe = episodes_to_transcribe[:max_episodes]

                # Initialize transcriber based on config
                transcriber = _get_transcriber(config)
                if transcriber is None:
                    return [
                        TextContent(
                            type="text",
                            text=json.dumps(
                                {"success": False, "error": "Transcription not configured. Check your .env settings."}
                            ),
                        )
                    ]

                # Transcribe episodes
                transcribed = []
                failed = []
                for podcast, episode in episodes_to_transcribe:
                    try:
                        # Only use downsampled audio
                        if not episode.downsampled_audio_path:
                            failed.append(
                                {
                                    "podcast": podcast.title,
                                    "episode": episode.title,
                                    "error": "No downsampled audio available",
                                }
                            )
                            continue

                        audio_file = path_manager.downsampled_audio_file(episode.downsampled_audio_path)
                        if not audio_file.exists():
                            failed.append(
                                {"podcast": podcast.title, "episode": episode.title, "error": "Audio file not found"}
                            )
                            continue

                        # Determine output path using podcast subdirectory structure
                        # Downsampled audio path is in format: pod-slug/episode-slug_hash.wav
                        path_parts = Path(episode.downsampled_audio_path).parts
                        if len(path_parts) >= 2:
                            podcast_subdir = path_parts[0]
                        else:
                            podcast_subdir = podcast.slug

                        # Create podcast subdirectory for raw transcripts
                        transcript_dir = path_manager.raw_transcripts_dir() / podcast_subdir
                        transcript_dir.mkdir(parents=True, exist_ok=True)

                        transcript_filename = f"{audio_file.stem}_transcript.json"
                        output_path = str(transcript_dir / transcript_filename)
                        output_db_path = f"{podcast_subdir}/{transcript_filename}"

                        # Convert language for provider (Google needs BCP-47 format)
                        episode_language = podcast.language
                        if config.transcription_provider.lower() == "google":
                            locale_map = {
                                "en": "en-US",
                                "hr": "hr-HR",
                                "de": "de-DE",
                                "es": "es-ES",
                                "fr": "fr-FR",
                                "it": "it-IT",
                            }
                            episode_language = locale_map.get(
                                podcast.language, f"{podcast.language}-{podcast.language.upper()}"
                            )

                        # Transcribe
                        transcript_data = transcriber.transcribe_audio(
                            str(audio_file),
                            output_path,
                            options=TranscribeOptions(language=episode_language),
                        )

                        if transcript_data:
                            feed_manager.mark_episode_processed(
                                str(podcast.rss_url), episode.external_id, raw_transcript_path=output_db_path
                            )
                            transcribed.append({"podcast": podcast.title, "episode": episode.title})
                        else:
                            failed.append(
                                {"podcast": podcast.title, "episode": episode.title, "error": "Transcription failed"}
                            )
                    except Exception as e:
                        failed.append({"podcast": podcast.title, "episode": episode.title, "error": str(e)})

                result = {
                    "success": True,
                    "message": f"Transcribed {len(transcribed)} episode(s)",
                    "transcribed": transcribed,
                    "failed": failed if failed else None,
                    "next_step": "Run clean_transcripts for better readability",
                }
                return [TextContent(type="text", text=json.dumps(result, indent=2))]

            elif name == "clean_transcripts":
                podcast_id = arguments.get("podcast_id")
                max_episodes = arguments.get("max_episodes", 1)

                # Convert to int if it's a numeric string
                if podcast_id and isinstance(podcast_id, str) and podcast_id.isdigit():
                    podcast_id = int(podcast_id)

                # Find transcripts that need cleaning
                podcasts = feed_manager.list_podcasts()
                transcripts_to_clean = []

                for podcast in podcasts:
                    # Filter by podcast_id if specified
                    if podcast_id:
                        target_podcast = podcast_service.get_podcast(podcast_id)
                        if not target_podcast or str(podcast.rss_url) != str(target_podcast.rss_url):
                            continue

                    for episode in podcast.episodes:
                        if episode.raw_transcript_path:
                            transcript_path = path_manager.raw_transcript_file(episode.raw_transcript_path)
                            if not transcript_path.exists():
                                continue

                            # Check if clean transcript already exists
                            clean_exists = False
                            if episode.clean_transcript_path:
                                clean_path = path_manager.clean_transcript_file(episode.clean_transcript_path)
                                clean_exists = clean_path.exists()

                            if not clean_exists:
                                transcripts_to_clean.append((podcast, episode, transcript_path))

                if not transcripts_to_clean:
                    return [
                        TextContent(
                            type="text",
                            text=json.dumps(
                                {
                                    "success": True,
                                    "message": "No transcripts found that need cleaning",
                                    "hint": "Run transcribe_episodes first",
                                }
                            ),
                        )
                    ]

                # Apply max_episodes limit
                transcripts_to_clean = transcripts_to_clean[:max_episodes]

                # Initialize cleaning processor
                cleaning_processor = _get_cleaning_processor(config)
                if cleaning_processor is None:
                    return [
                        TextContent(
                            type="text",
                            text=json.dumps(
                                {"success": False, "error": "LLM not configured. Check your .env settings."}
                            ),
                        )
                    ]

                # Clean transcripts
                cleaned = []
                failed = []
                for podcast, episode, transcript_path in transcripts_to_clean:
                    try:
                        with open(transcript_path, "r", encoding="utf-8") as f:
                            transcript_data = json.load(f)

                        # Generate output path using podcast subdirectory structure
                        base_name = transcript_path.stem
                        if base_name.endswith("_transcript"):
                            base_name = base_name[: -len("_transcript")]

                        # Extract episode_slug_hash from base_name (format: podcast-slug_episode-slug_hash)
                        # We want just episode-slug_hash for the filename
                        parts = base_name.split("_")
                        if len(parts) >= 3:
                            episode_slug_hash = "_".join(parts[1:])  # episode-slug_hash
                        else:
                            episode_slug_hash = base_name

                        # Create podcast subdirectory
                        podcast_subdir = path_manager.clean_transcripts_dir() / podcast.slug
                        podcast_subdir.mkdir(parents=True, exist_ok=True)

                        cleaned_filename = f"{episode_slug_hash}_cleaned.md"
                        cleaned_path = podcast_subdir / cleaned_filename

                        # Database stores relative path: {podcast_slug}/{filename}
                        clean_transcript_db_path = f"{podcast.slug}/{cleaned_filename}"

                        result_data = cleaning_processor.clean_transcript(
                            transcript_data=transcript_data,
                            podcast_title=podcast.title,
                            podcast_description=podcast.description,
                            episode_title=episode.title,
                            episode_description=episode.description,
                            podcast_slug=podcast.slug,
                            episode_slug=episode.slug,
                            output_path=str(cleaned_path),
                            path_manager=path_manager,
                            language=podcast.language,
                        )

                        if result_data:
                            feed_manager.mark_episode_processed(
                                str(podcast.rss_url),
                                episode.external_id,
                                raw_transcript_path=transcript_path.name,
                                clean_transcript_path=clean_transcript_db_path,
                            )
                            cleaned.append(
                                {
                                    "podcast": podcast.title,
                                    "episode": episode.title,
                                    "corrections": len(result_data.get("corrections", [])),
                                    "speakers": len(result_data.get("speaker_mapping", {})),
                                }
                            )
                        else:
                            failed.append(
                                {"podcast": podcast.title, "episode": episode.title, "error": "Cleaning failed"}
                            )
                    except Exception as e:
                        failed.append({"podcast": podcast.title, "episode": episode.title, "error": str(e)})

                result = {
                    "success": True,
                    "message": f"Cleaned {len(cleaned)} transcript(s)",
                    "cleaned": cleaned,
                    "failed": failed if failed else None,
                    "complete": "Transcripts are now ready! Use get_transcript to read them.",
                }
                return [TextContent(type="text", text=json.dumps(result, indent=2))]

            elif name == "process_episode":
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

                # Convert to int if numeric strings
                if isinstance(podcast_id, str) and podcast_id.isdigit():
                    podcast_id = int(podcast_id)
                if isinstance(episode_id, str) and episode_id.isdigit():
                    episode_id = int(episode_id)

                # Get the episode
                podcast = podcast_service.get_podcast(podcast_id)
                if not podcast:
                    return [
                        TextContent(
                            type="text",
                            text=json.dumps({"success": False, "error": f"Podcast not found: {podcast_id}"}),
                        )
                    ]

                episode = podcast_service.get_episode(podcast_id, episode_id)
                if not episode:
                    return [
                        TextContent(
                            type="text",
                            text=json.dumps({"success": False, "error": f"Episode not found: {episode_id}"}),
                        )
                    ]

                steps_completed = []
                current_step = None

                try:
                    # Step 1: Download if needed
                    if not episode.audio_path:
                        current_step = "download"
                        audio_path = audio_downloader.download_episode(episode, podcast)
                        if audio_path:
                            # Store the relative path (includes podcast subdirectory)
                            feed_manager.mark_episode_downloaded(str(podcast.rss_url), episode.external_id, audio_path)
                            episode.audio_path = audio_path
                            steps_completed.append("download")
                        else:
                            return [
                                TextContent(
                                    type="text",
                                    text=json.dumps(
                                        {
                                            "success": False,
                                            "error": "Download failed",
                                            "steps_completed": steps_completed,
                                        }
                                    ),
                                )
                            ]
                    else:
                        steps_completed.append("download (already done)")

                    # Step 2: Downsample if needed
                    if not episode.downsampled_audio_path:
                        current_step = "downsample"
                        original_audio_file = path_manager.original_audio_file(episode.audio_path)
                        downsampled_path = audio_preprocessor.downsample_audio(
                            str(original_audio_file), str(path_manager.downsampled_audio_dir())
                        )
                        if downsampled_path:
                            downsampled_filename = Path(downsampled_path).name
                            feed_manager.mark_episode_downsampled(
                                str(podcast.rss_url), episode.external_id, downsampled_filename
                            )
                            episode.downsampled_audio_path = downsampled_filename
                            steps_completed.append("downsample")
                        else:
                            return [
                                TextContent(
                                    type="text",
                                    text=json.dumps(
                                        {
                                            "success": False,
                                            "error": "Downsampling failed",
                                            "steps_completed": steps_completed,
                                        }
                                    ),
                                )
                            ]
                    else:
                        steps_completed.append("downsample (already done)")

                    # Step 3: Transcribe if needed
                    if not episode.raw_transcript_path:
                        current_step = "transcribe"
                        transcriber = _get_transcriber(config)
                        if transcriber is None:
                            return [
                                TextContent(
                                    type="text",
                                    text=json.dumps(
                                        {
                                            "success": False,
                                            "error": "Transcriber not configured",
                                            "steps_completed": steps_completed,
                                        }
                                    ),
                                )
                            ]

                        audio_file = path_manager.downsampled_audio_file(episode.downsampled_audio_path)

                        # Determine output path using podcast subdirectory structure
                        path_parts = Path(episode.downsampled_audio_path).parts
                        if len(path_parts) >= 2:
                            podcast_subdir = path_parts[0]
                        else:
                            podcast_subdir = podcast.slug

                        transcript_dir = path_manager.raw_transcripts_dir() / podcast_subdir
                        transcript_dir.mkdir(parents=True, exist_ok=True)

                        transcript_filename = f"{audio_file.stem}_transcript.json"
                        output_path = str(transcript_dir / transcript_filename)
                        output_db_path = f"{podcast_subdir}/{transcript_filename}"

                        # Convert language for provider (Google needs BCP-47 format)
                        episode_language = podcast.language
                        if config.transcription_provider.lower() == "google":
                            locale_map = {
                                "en": "en-US",
                                "hr": "hr-HR",
                                "de": "de-DE",
                                "es": "es-ES",
                                "fr": "fr-FR",
                                "it": "it-IT",
                            }
                            episode_language = locale_map.get(
                                podcast.language, f"{podcast.language}-{podcast.language.upper()}"
                            )

                        transcript_data = transcriber.transcribe_audio(
                            str(audio_file),
                            output_path,
                            options=TranscribeOptions(language=episode_language),
                        )
                        if transcript_data:
                            feed_manager.mark_episode_processed(
                                str(podcast.rss_url), episode.external_id, raw_transcript_path=output_db_path
                            )
                            episode.raw_transcript_path = output_db_path
                            steps_completed.append("transcribe")
                        else:
                            return [
                                TextContent(
                                    type="text",
                                    text=json.dumps(
                                        {
                                            "success": False,
                                            "error": "Transcription failed",
                                            "steps_completed": steps_completed,
                                        }
                                    ),
                                )
                            ]
                    else:
                        steps_completed.append("transcribe (already done)")

                    # Step 4: Clean if needed
                    if not episode.clean_transcript_path:
                        current_step = "clean"
                        cleaning_processor = _get_cleaning_processor(config)
                        if cleaning_processor is None:
                            return [
                                TextContent(
                                    type="text",
                                    text=json.dumps(
                                        {
                                            "success": False,
                                            "error": "LLM not configured",
                                            "steps_completed": steps_completed,
                                        }
                                    ),
                                )
                            ]

                        transcript_path = path_manager.raw_transcript_file(episode.raw_transcript_path)

                        with open(transcript_path, "r", encoding="utf-8") as f:
                            transcript_data = json.load(f)

                        base_name = transcript_path.stem
                        if base_name.endswith("_transcript"):
                            base_name = base_name[: -len("_transcript")]
                        cleaned_filename = f"{base_name}_cleaned.md"

                        # Create podcast subdirectory for clean transcripts
                        podcast_subdir = path_manager.clean_transcripts_dir() / podcast.slug
                        podcast_subdir.mkdir(parents=True, exist_ok=True)
                        cleaned_path = podcast_subdir / cleaned_filename

                        # Database stores relative path: {podcast_slug}/{filename}
                        clean_transcript_db_path = f"{podcast.slug}/{cleaned_filename}"

                        result_data = cleaning_processor.clean_transcript(
                            transcript_data=transcript_data,
                            podcast_title=podcast.title,
                            podcast_description=podcast.description,
                            episode_title=episode.title,
                            episode_description=episode.description,
                            podcast_slug=podcast.slug,
                            episode_slug=episode.slug,
                            output_path=str(cleaned_path),
                            path_manager=path_manager,
                            language=podcast.language,
                        )

                        if result_data:
                            feed_manager.mark_episode_processed(
                                str(podcast.rss_url),
                                episode.external_id,
                                raw_transcript_path=episode.raw_transcript_path,
                                clean_transcript_path=clean_transcript_db_path,
                            )
                            steps_completed.append("clean")
                        else:
                            return [
                                TextContent(
                                    type="text",
                                    text=json.dumps(
                                        {
                                            "success": False,
                                            "error": "Cleaning failed",
                                            "steps_completed": steps_completed,
                                        }
                                    ),
                                )
                            ]
                    else:
                        steps_completed.append("clean (already done)")

                    result = {
                        "success": True,
                        "message": f"Episode processed: {episode.title}",
                        "podcast": podcast.title,
                        "episode": episode.title,
                        "steps_completed": steps_completed,
                        "transcript_ready": True,
                    }
                    return [TextContent(type="text", text=json.dumps(result, indent=2))]

                except Exception as e:
                    return [
                        TextContent(
                            type="text",
                            text=json.dumps(
                                {
                                    "success": False,
                                    "error": f"Failed at step '{current_step}': {str(e)}",
                                    "steps_completed": steps_completed,
                                }
                            ),
                        )
                    ]

            elif name == "summarize_episodes":
                podcast_id = arguments.get("podcast_id")
                max_episodes = arguments.get("max_episodes", 1)

                # Convert to int if it's a numeric string
                if podcast_id and isinstance(podcast_id, str) and podcast_id.isdigit():
                    podcast_id = int(podcast_id)

                # Find cleaned transcripts that need summarizing
                podcasts = feed_manager.list_podcasts()
                transcripts_to_summarize = []

                for podcast in podcasts:
                    # Filter by podcast_id if specified
                    if podcast_id:
                        target_podcast = podcast_service.get_podcast(podcast_id)
                        if not target_podcast or str(podcast.rss_url) != str(target_podcast.rss_url):
                            continue

                    for episode in podcast.episodes:
                        if episode.clean_transcript_path:
                            clean_path = path_manager.clean_transcript_file(episode.clean_transcript_path)
                            if not clean_path.exists():
                                continue

                            # Check if summary already exists
                            summary_exists = False
                            if episode.summary_path:
                                summary_path = path_manager.summary_file(episode.summary_path)
                                summary_exists = summary_path.exists()

                            if not summary_exists:
                                transcripts_to_summarize.append((podcast, episode, clean_path))

                if not transcripts_to_summarize:
                    return [
                        TextContent(
                            type="text",
                            text=json.dumps(
                                {
                                    "success": True,
                                    "message": "No transcripts found that need summarizing",
                                    "hint": "Run clean_transcripts first",
                                }
                            ),
                        )
                    ]

                # Apply max_episodes limit
                transcripts_to_summarize = transcripts_to_summarize[:max_episodes]

                # Initialize summarizer
                summarizer = _get_summarizer(config)
                if summarizer is None:
                    return [
                        TextContent(
                            type="text",
                            text=json.dumps(
                                {"success": False, "error": "LLM not configured. Check your .env settings."}
                            ),
                        )
                    ]

                # Summarize transcripts
                summarized = []
                failed = []
                for podcast, episode, clean_path in transcripts_to_summarize:
                    try:
                        with open(clean_path, "r", encoding="utf-8") as f:
                            transcript_text = f.read()

                        # Generate output filename
                        base_name = clean_path.stem
                        if base_name.endswith("_cleaned"):
                            base_name = base_name[: -len("_cleaned")]
                        summary_filename = f"{base_name}_summary.md"
                        summary_path = path_manager.summaries_dir() / summary_filename

                        summarizer.summarize(transcript_text, summary_path)

                        # Update feed manager
                        feed_manager.mark_episode_processed(
                            str(podcast.rss_url),
                            episode.external_id,
                            summary_path=summary_filename,
                        )
                        summarized.append({"podcast": podcast.title, "episode": episode.title})
                    except Exception as e:
                        failed.append({"podcast": podcast.title, "episode": episode.title, "error": str(e)})

                result = {
                    "success": True,
                    "message": f"Summarized {len(summarized)} episode(s)",
                    "summarized": summarized,
                    "failed": failed if failed else None,
                    "complete": "Summaries are now ready! Use get_summary to read them.",
                }
                return [TextContent(type="text", text=json.dumps(result, indent=2))]

            elif name == "get_summary":
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

                summary = podcast_service.get_summary(podcast_id, episode_id)

                if summary is None:
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

                # If summary starts with "N/A", it means it's not available
                if summary.startswith("N/A"):
                    return [TextContent(type="text", text=json.dumps({"success": False, "error": summary}))]

                # Return the summary content directly (not JSON-encoded)
                return [TextContent(type="text", text=summary)]

            else:
                logger.error(f"Unknown tool: {name}")
                return [TextContent(type="text", text=json.dumps({"success": False, "error": f"Unknown tool: {name}"}))]

        except Exception as e:
            logger.error(f"Error calling tool {name}: {e}", exc_info=True)
            return [TextContent(type="text", text=json.dumps({"success": False, "error": str(e)}))]


def _get_transcriber(config):
    """
    Initialize and return a transcriber based on config settings.

    Args:
        config: Configuration object

    Returns:
        Transcriber instance or None if not configured
    """
    try:
        if config.transcription_provider.lower() == "google":
            from ..core.google_transcriber import GoogleCloudTranscriber

            if not config.google_app_credentials and not config.google_cloud_project_id:
                logger.warning("Google Cloud credentials not configured")
                return None

            return GoogleCloudTranscriber(
                credentials_path=config.google_app_credentials or None,
                project_id=config.google_cloud_project_id or None,
                storage_bucket=config.google_storage_bucket or None,
                enable_diarization=config.enable_diarization,
                min_speakers=config.min_speakers,
                max_speakers=config.max_speakers,
                parallel_chunks=config.max_workers,
            )
        elif config.enable_diarization:
            from ..core.whisper_transcriber import WhisperXTranscriber

            return WhisperXTranscriber(
                model_name=config.whisper_model,
                device=config.whisper_device,
                enable_diarization=True,
                hf_token=config.huggingface_token,
                min_speakers=config.min_speakers,
                max_speakers=config.max_speakers,
                diarization_model=config.diarization_model,
            )
        else:
            from ..core.whisper_transcriber import WhisperTranscriber

            return WhisperTranscriber(config.whisper_model, config.whisper_device)
    except Exception as e:
        logger.error(f"Failed to initialize transcriber: {e}")
        return None


def _get_cleaning_processor(config):
    """
    Initialize and return a transcript cleaning processor based on config settings.

    Args:
        config: Configuration object

    Returns:
        TranscriptCleaningProcessor instance or None if not configured
    """
    try:
        from ..core.llm_provider import create_llm_provider
        from ..core.transcript_cleaning_processor import TranscriptCleaningProcessor

        llm_provider = create_llm_provider(
            provider_type=config.llm_provider,
            openai_api_key=config.openai_api_key,
            openai_model=config.openai_model,
            openai_reasoning_effort=config.openai_reasoning_effort,
            ollama_base_url=config.ollama_base_url,
            ollama_model=config.ollama_model,
            gemini_api_key=config.gemini_api_key,
            gemini_model=config.gemini_model,
            gemini_thinking_level=config.gemini_thinking_level,
            anthropic_api_key=config.anthropic_api_key,
            anthropic_model=config.anthropic_model,
            mistral_api_key=config.mistral_api_key,
            mistral_model=config.mistral_model,
        )
        return TranscriptCleaningProcessor(llm_provider)
    except Exception as e:
        logger.error(f"Failed to initialize cleaning processor: {e}")
        return None


def _get_summarizer(config):
    """
    Initialize and return a transcript summarizer based on config settings.

    Args:
        config: Configuration object

    Returns:
        TranscriptSummarizer instance or None if not configured
    """
    try:
        from ..core.llm_provider import create_llm_provider
        from ..core.post_processor import TranscriptSummarizer

        llm_provider = create_llm_provider(
            provider_type=config.llm_provider,
            openai_api_key=config.openai_api_key,
            openai_model=config.openai_model,
            openai_reasoning_effort=config.openai_reasoning_effort,
            ollama_base_url=config.ollama_base_url,
            ollama_model=config.ollama_model,
            gemini_api_key=config.gemini_api_key,
            gemini_model=config.gemini_model,
            gemini_thinking_level=config.gemini_thinking_level,
            anthropic_api_key=config.anthropic_api_key,
            anthropic_model=config.anthropic_model,
            mistral_api_key=config.mistral_api_key,
            mistral_model=config.mistral_model,
        )
        return TranscriptSummarizer(llm_provider)
    except Exception as e:
        logger.error(f"Failed to initialize summarizer: {e}")
        return None
