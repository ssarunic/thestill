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

from typing import Any, Dict, Optional

from ..models.podcast import Episode

# MIME prefix that marks an enclosure as a video rendition.
_VIDEO_MIME_PREFIX = "video/"


def is_video_enclosure(mime_type: Optional[str]) -> bool:
    """True when the enclosure MIME type declares video content."""
    return bool(mime_type) and str(mime_type).lower().startswith(_VIDEO_MIME_PREFIX)


def build_playback_manifest(episode: Episode) -> Dict[str, Any]:
    """
    Build the playback-asset manifest for an episode (spec #61 §4).

    Returns a dict with:
        kind: 'audio' | 'video'
        audio: asset dict | None
        video: asset dict | None
        poster_url: episode artwork for the video poster frame | None
        captions_url: WebVTT captions | None (unpopulated — spec #61 open item)

    Each asset dict carries ``url``, ``mime_type``, ``duration`` and
    ``timeline_offset`` (seconds; logical time t plays at asset time
    t + offset).
    """
    asset: Dict[str, Any] = {
        "url": str(episode.audio_url),
        "mime_type": episode.audio_mime_type,
        "duration": episode.duration,
        "timeline_offset": episode.playback_time_offset_seconds,
    }

    if is_video_enclosure(episode.audio_mime_type):
        return {
            "kind": "video",
            "audio": None,
            "video": asset,
            "poster_url": episode.image_url,
            "captions_url": None,
        }

    return {
        "kind": "audio",
        "audio": asset,
        "video": None,
        "poster_url": None,
        "captions_url": None,
    }
