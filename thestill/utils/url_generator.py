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
URL generator for web UI links.

Provides consistent URL generation for podcasts, episodes, and other resources.
"""

from typing import Dict, Optional


class UrlGenerator:
    """
    Generates web UI URLs for resources.

    URLs are relative paths that work with the frontend router.

    Usage:
        url_gen = UrlGenerator()
        episode_url = url_gen.episode("my-podcast", "my-episode")
        # Returns: "/podcasts/my-podcast/episodes/my-episode"
    """

    def podcast(self, podcast_slug: str) -> str:
        """
        Generate URL for a podcast page.

        Args:
            podcast_slug: URL-safe podcast identifier

        Returns:
            Relative URL path
        """
        return f"/podcasts/{podcast_slug}"

    def episode(self, podcast_slug: str, episode_slug: str) -> str:
        """
        Generate URL for an episode page.

        Args:
            podcast_slug: URL-safe podcast identifier
            episode_slug: URL-safe episode identifier

        Returns:
            Relative URL path
        """
        return f"/podcasts/{podcast_slug}/episodes/{episode_slug}"

    def episode_at(self, podcast_slug: str, episode_slug: str, start_seconds: float) -> str:
        """Episode page URL with a ``?t={seconds}`` deep-link cue.

        Used by the narration markdown renderer (spec #33) and the
        search backend (api_search.py) to land the player at a specific
        timestamp. Negative offsets are clamped to zero — the frontend
        treats a negative ``t`` as "skip the cue".
        """
        seconds = max(0, int(start_seconds))
        return f"{self.episode(podcast_slug, episode_slug)}?t={seconds}"

    def episode_summary(self, podcast_slug: str, episode_slug: str) -> str:
        """
        Generate URL for an episode summary.

        Args:
            podcast_slug: URL-safe podcast identifier
            episode_slug: URL-safe episode identifier

        Returns:
            Relative URL path
        """
        return f"/podcasts/{podcast_slug}/episodes/{episode_slug}/summary"

    def digest(self, digest_id: str) -> str:
        """
        Generate URL for a digest page.

        Args:
            digest_id: Digest identifier

        Returns:
            Relative URL path
        """
        return f"/digests/{digest_id}"


def episode_links(
    *,
    base_url: str,
    podcast_slug: Optional[str],
    episode_slug: Optional[str],
    audio_url: Optional[str],
    start_ms: Optional[int] = None,
) -> Dict[str, Optional[str]]:
    """Where to read and where to listen, for MCP and API callers.

    - ``web_url``: the episode page path, ``#t=`` when ``start_ms`` is set
      (the fragment form public pages use, so it seeks for signed-out
      visitors too). ``None`` without both slugs: there's no id route.
    - ``episode_url``: ``web_url`` made absolute with ``PUBLIC_BASE_URL``;
      ``None`` when that's unset (a local stdio server has no public host).
    - ``audio_url``: the publisher's enclosure from the feed, never our
      own downloaded or downsampled copy.
    """
    web_url = None
    if podcast_slug and episode_slug:
        web_url = UrlGenerator().episode(podcast_slug, episode_slug)
        if start_ms is not None:
            web_url += f"#t={max(0, start_ms // 1000)}"
    return {
        "web_url": web_url,
        "episode_url": f"{base_url.rstrip('/')}{web_url}" if base_url and web_url else None,
        "audio_url": audio_url or None,
    }
