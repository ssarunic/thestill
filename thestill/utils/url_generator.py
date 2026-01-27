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
URL generator for web UI links.

Provides consistent URL generation for podcasts, episodes, and other resources.
"""


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

    def episode_transcript(self, podcast_slug: str, episode_slug: str) -> str:
        """
        Generate URL for an episode transcript.

        Args:
            podcast_slug: URL-safe podcast identifier
            episode_slug: URL-safe episode identifier

        Returns:
            Relative URL path
        """
        return f"/podcasts/{podcast_slug}/episodes/{episode_slug}/transcript"

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
