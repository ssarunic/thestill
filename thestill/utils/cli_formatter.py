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
CLI output formatting utilities.

Centralizes formatting logic for CLI commands to improve consistency and testability.
"""

from typing import List

from ..services.podcast_service import EpisodeWithIndex, PodcastWithIndex


class CLIFormatter:
    """Formats output for CLI commands with consistent styling."""

    @staticmethod
    def format_podcast_list(podcasts: List[PodcastWithIndex]) -> str:
        """
        Format list of podcasts for display.

        Args:
            podcasts: List of podcasts with index information

        Returns:
            Formatted string ready for display
        """
        if not podcasts:
            return "No podcasts tracked yet. Use 'thestill add <rss_url>' to add some!"

        lines = [f"\n📻 Tracked Podcasts ({len(podcasts)}):", "─" * 50, ""]

        for podcast in podcasts:
            lines.append(f"{podcast.index}. {podcast.title}")
            lines.append(f"   RSS: {podcast.rss_url}")
            if podcast.last_processed:
                lines.append(f"   Last processed: {podcast.last_processed.strftime('%Y-%m-%d %H:%M')}")
            lines.append(f"   Episodes: {podcast.episodes_processed}/{podcast.episodes_count} processed")
            lines.append("")

        return "\n".join(lines)

    @staticmethod
    def format_episode_list(episodes: List[EpisodeWithIndex], podcast_title: str = None) -> str:
        """
        Format list of episodes for display.

        Args:
            episodes: List of episodes with index information
            podcast_title: Optional podcast title for header

        Returns:
            Formatted string ready for display
        """
        if not episodes:
            return "No episodes found."

        lines = []

        if podcast_title:
            lines.append(f"\n📻 {podcast_title}")
            lines.append("")

        for episode in episodes:
            # Map episode state to status icon
            state_icons = {
                "discovered": "○",  # Not downloaded
                "downloaded": "↓",  # Downloaded
                "downsampled": "♪",  # Audio ready
                "transcribed": "✎",  # Transcribed (pencil writing)
                "cleaned": "✓",  # Fully processed
            }
            status_icon = state_icons.get(episode.state, "?")

            lines.append(f"  {status_icon} {episode.episode_index}. {episode.title}")
            if episode.pub_date:
                lines.append(f"     Published: {episode.pub_date.strftime('%Y-%m-%d')}")
            if episode.duration:
                lines.append(f"     Duration: {episode.duration}")
            if episode.state == "cleaned":
                if episode.transcript_available:
                    lines.append("     📝 Transcript available")
                if episode.summary_available:
                    lines.append("     📄 Summary available")
            lines.append("")

        return "\n".join(lines)

    @staticmethod
    def format_success(message: str) -> str:
        """Format success message with checkmark."""
        return f"✅ {message}"

    @staticmethod
    def format_error(message: str) -> str:
        """Format error message with X mark."""
        return f"❌ {message}"

    @staticmethod
    def format_info(message: str) -> str:
        """Format informational message with icon."""
        return f"ℹ️  {message}"

    @staticmethod
    def format_header(title: str, icon: str = "📊") -> str:
        """
        Format section header with icon and separator.

        Args:
            title: Header title
            icon: Icon to display (default: 📊)

        Returns:
            Formatted header with separator line
        """
        return f"{icon} {title}\n{'═' * 30}"

    @staticmethod
    def format_progress(message: str) -> str:
        """Format progress message with icon."""
        return f"🔄 {message}"

    @staticmethod
    def format_completion(message: str) -> str:
        """Format completion message with checkmark."""
        return f"✅ {message}"
