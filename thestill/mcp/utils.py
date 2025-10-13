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
MCP Utilities

Helper functions for URI parsing and ID resolution.
RESTful URI format: thestill://podcasts/{id}/episodes/{id}/...
"""

from typing import Dict, Union
from urllib.parse import unquote


def parse_thestill_uri(uri: str) -> Dict[str, Union[str, int]]:
    """
    Parse a thestill:// URI with RESTful path structure.

    URI Format:
        thestill://podcasts/{podcast_id}
        thestill://podcasts/{podcast_id}/episodes/{episode_id}
        thestill://podcasts/{podcast_id}/episodes/{episode_id}/transcript
        thestill://podcasts/{podcast_id}/episodes/{episode_id}/audio

    Args:
        uri: URI string starting with thestill://

    Returns:
        Dictionary with parsed components:
        - resource: Resource type (podcast, episode, transcript, audio)
        - podcast_id: Podcast identifier (int or str)
        - episode_id: Episode identifier (int or str, optional)

    Examples:
        >>> parse_thestill_uri("thestill://podcasts/1")
        {"resource": "podcast", "podcast_id": 1}

        >>> parse_thestill_uri("thestill://podcasts/1/episodes/latest")
        {"resource": "episode", "podcast_id": 1, "episode_id": "latest"}

        >>> parse_thestill_uri("thestill://podcasts/1/episodes/2/transcript")
        {"resource": "transcript", "podcast_id": 1, "episode_id": 2}

        >>> parse_thestill_uri("thestill://podcasts/1/episodes/2/audio")
        {"resource": "audio", "podcast_id": 1, "episode_id": 2}
    """
    if not uri.startswith("thestill://"):
        raise ValueError(f"Invalid URI scheme: {uri}. Expected thestill://")

    # Extract and parse path
    path = uri[len("thestill://") :]
    parts = [unquote(p) for p in path.split("/") if p]

    if len(parts) < 2:
        raise ValueError(f"Invalid URI format: {uri}. Expected thestill://podcasts/{{id}}/...")

    # Validate podcasts namespace
    if parts[0] != "podcasts":
        raise ValueError(f"Invalid URI: {uri}. Expected 'podcasts' as first path segment")

    # Parse podcast ID
    podcast_id = _parse_id(parts[1])

    # Case 1: thestill://podcasts/{podcast_id}
    if len(parts) == 2:
        return {"resource": "podcast", "podcast_id": podcast_id}

    # Validate episodes namespace
    if len(parts) >= 3 and parts[2] != "episodes":
        raise ValueError(f"Invalid URI: {uri}. Expected 'episodes' as third path segment")

    if len(parts) < 4:
        raise ValueError(f"Invalid URI format: {uri}. Expected thestill://podcasts/{{id}}/episodes/{{id}}")

    # Parse episode ID
    episode_id = _parse_id(parts[3])

    # Case 2: thestill://podcasts/{podcast_id}/episodes/{episode_id}
    if len(parts) == 4:
        return {"resource": "episode", "podcast_id": podcast_id, "episode_id": episode_id}

    # Case 3: thestill://podcasts/{podcast_id}/episodes/{episode_id}/{sub-resource}
    if len(parts) == 5:
        sub_resource = parts[4].lower()

        if sub_resource not in ["transcript", "audio"]:
            raise ValueError(f"Invalid sub-resource: {sub_resource}. " f"Expected 'transcript' or 'audio'")

        return {"resource": sub_resource, "podcast_id": podcast_id, "episode_id": episode_id}

    # Too many path segments
    raise ValueError(
        f"Invalid URI format: {uri}. " f"Expected thestill://podcasts/{{id}}/episodes/{{id}}/[transcript|audio]"
    )


def _parse_id(id_str: str) -> Union[str, int]:
    """
    Parse an ID string to int if numeric, otherwise return as string.

    Args:
        id_str: ID string to parse

    Returns:
        Integer if numeric, string otherwise

    Examples:
        >>> _parse_id("1")
        1
        >>> _parse_id("latest")
        "latest"
        >>> _parse_id("2025-01-15")
        "2025-01-15"
    """
    if id_str.isdigit():
        return int(id_str)
    return id_str


def build_podcast_uri(podcast_id: Union[str, int]) -> str:
    """
    Build a podcast URI from podcast ID.

    Args:
        podcast_id: Podcast identifier

    Returns:
        URI string

    Example:
        >>> build_podcast_uri(1)
        "thestill://podcasts/1"
    """
    return f"thestill://podcasts/{podcast_id}"


def build_episode_uri(podcast_id: Union[str, int], episode_id: Union[str, int]) -> str:
    """
    Build an episode URI from podcast and episode IDs.

    Args:
        podcast_id: Podcast identifier
        episode_id: Episode identifier

    Returns:
        URI string

    Example:
        >>> build_episode_uri(1, "latest")
        "thestill://podcasts/1/episodes/latest"
    """
    return f"thestill://podcasts/{podcast_id}/episodes/{episode_id}"


def build_transcript_uri(podcast_id: Union[str, int], episode_id: Union[str, int]) -> str:
    """
    Build a transcript URI from podcast and episode IDs.

    Args:
        podcast_id: Podcast identifier
        episode_id: Episode identifier

    Returns:
        URI string

    Example:
        >>> build_transcript_uri(1, 2)
        "thestill://podcasts/1/episodes/2/transcript"
    """
    return f"thestill://podcasts/{podcast_id}/episodes/{episode_id}/transcript"


def build_audio_uri(podcast_id: Union[str, int], episode_id: Union[str, int]) -> str:
    """
    Build an audio URI from podcast and episode IDs.

    Args:
        podcast_id: Podcast identifier
        episode_id: Episode identifier

    Returns:
        URI string

    Example:
        >>> build_audio_uri(1, 2)
        "thestill://podcasts/1/episodes/2/audio"
    """
    return f"thestill://podcasts/{podcast_id}/episodes/{episode_id}/audio"
