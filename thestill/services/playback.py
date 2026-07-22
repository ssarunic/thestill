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

"""
Playback-asset manifest (spec #61 §4).

One episode, multiple renditions, one logical playback session. The web
player consumes an explicit manifest instead of overloading ``audio_url``
with a "is this video?" side-channel. Audio-only episodes emit
``kind: 'audio'`` with the existing URL so no client breaks.

Today an episode carries exactly one enclosure (the ``audio_url`` /
``audio_mime_type`` pair captured at feed ingest). RSS video enclosures —
the first legitimate video population (§6) — surface as a ``video`` asset;
everything else stays an ``audio`` asset. When #39's alternate-enclosure
variants land, this builder is where a second rendition of the same episode
gets added.

Per-asset ``timeline_offset`` generalizes ``playback_time_offset_seconds``:
it maps logical (transcript) time ``t`` to asset time ``t + offset``, the
same convention the karaoke word payload already uses. Renditions of the
"same" content drift (trimmed leading silence, pre-roll), so the offset
lives on the asset, not the episode.
"""

from typing import Any, Dict, List, Optional

from structlog import get_logger

from ..models.podcast import AlternateEnclosure, Episode
from ..utils.url_patterns import extract_youtube_video_id

logger = get_logger(__name__)

# MIME prefix that marks an enclosure as a video rendition.
_VIDEO_MIME_PREFIX = "video/"

# Alternate-enclosure MIME type that marks an episode-level YouTube link
# (spec #62). The manifest's ``youtube`` asset is built from these rows.
_YOUTUBE_MIME_TYPE = "video/youtube"


def is_video_enclosure(mime_type: Optional[str]) -> bool:
    """True when the enclosure MIME type declares video content."""
    return bool(mime_type) and str(mime_type).lower().startswith(_VIDEO_MIME_PREFIX)


def _select_youtube_asset(
    alternate_enclosures: Optional[List[AlternateEnclosure]],
) -> Optional[Dict[str, Any]]:
    """
    Pick the episode's YouTube asset from its alternate enclosures (spec #62).

    ``is_default`` entries win, else first by observation order (stable sort).
    The video id is extracted and validated (11 URL-safe chars) — feed data
    is untrusted input, so an entry whose URI doesn't yield a valid id is
    skipped with a warning rather than emitted into a frontend iframe URL.
    """
    if not alternate_enclosures:
        return None

    candidates = [a for a in alternate_enclosures if a.mime_type.lower() == _YOUTUBE_MIME_TYPE]
    candidates.sort(key=lambda a: not a.is_default)  # stable: default-first, then observation order

    for candidate in candidates:
        video_id = extract_youtube_video_id(candidate.source_uri)
        if video_id is None:
            logger.warning(
                "youtube_asset_invalid_source_uri",
                episode_id=candidate.episode_id,
                source_uri=candidate.source_uri,
            )
            continue
        return {
            "video_id": video_id,
            "watch_url": f"https://www.youtube.com/watch?v={video_id}",
            "title": candidate.title,
        }
    return None


def build_playback_manifest(
    episode: Episode,
    alternate_enclosures: Optional[List[AlternateEnclosure]] = None,
) -> Dict[str, Any]:
    """
    Build the playback-asset manifest for an episode (spec #61 §4, spec #62).

    Returns a dict with:
        kind: 'audio' | 'video'
        audio: asset dict | None
        video: asset dict | None
        youtube: {video_id, watch_url, title} | None (spec #62 — episode-level
            YouTube link from a video/youtube alternate enclosure; an opt-in
            rendition, never the default engine, so ``kind`` ignores it)
        poster_url: episode artwork for the video poster frame | None
        captions_url: WebVTT captions | None (unpopulated — spec #61 open item)

    Each native asset dict carries ``url``, ``mime_type``, ``duration`` and
    ``timeline_offset`` (seconds; logical time t plays at asset time
    t + offset). The youtube asset deliberately has NO timeline_offset — a
    static offset cannot model YouTube's dynamic ad insertion (spec #62 §8).
    """
    asset: Dict[str, Any] = {
        "url": str(episode.audio_url),
        "mime_type": episode.audio_mime_type,
        "duration": episode.duration,
        "timeline_offset": episode.playback_time_offset_seconds,
    }
    youtube = _select_youtube_asset(alternate_enclosures)

    if is_video_enclosure(episode.audio_mime_type):
        return {
            "kind": "video",
            "audio": None,
            "video": asset,
            "youtube": youtube,
            "poster_url": episode.image_url,
            "captions_url": None,
        }

    return {
        "kind": "audio",
        "audio": asset,
        "video": None,
        "youtube": youtube,
        "poster_url": episode.image_url if youtube else None,
        "captions_url": None,
    }
