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
CLI output formatting utilities.

Centralizes formatting logic for CLI commands to improve consistency and testability.
"""

from datetime import datetime, timezone
from typing import TYPE_CHECKING, List

from ..services.podcast_service import EpisodeWithIndex, PodcastWithIndex

if TYPE_CHECKING:
    from ..services.stats_service import ActivityItem


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
            # Show language code if not English
            lang_suffix = f" ({podcast.language})" if podcast.language and podcast.language != "en" else ""
            lines.append(f"{podcast.index}. {podcast.title}{lang_suffix}")
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
                "cleaned": "✓",  # Cleaned
                "summarized": "★",  # Fully processed
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

    @staticmethod
    def format_activity_log(items: List["ActivityItem"]) -> str:
        """
        Format activity log for display.

        Args:
            items: List of ActivityItem objects sorted by timestamp descending

        Returns:
            Formatted string ready for display
        """
        if not items:
            return "No recent activity."

        # State icons (same as format_episode_list)
        state_icons = {
            "discovered": "○",  # Not downloaded
            "downloaded": "↓",  # Downloaded
            "downsampled": "♪",  # Audio ready
            "transcribed": "✎",  # Transcribed
            "cleaned": "✓",  # Cleaned
            "summarized": "★",  # Fully processed
        }

        lines = [
            f"\nRecent Activity ({len(items)} episodes)",
            "─" * 80,
            "",
        ]

        for item in items:
            icon = state_icons.get(item.state, "?")
            state_display = item.state.upper()

            # Calculate relative time
            time_ago = CLIFormatter._format_relative_time(item.timestamp)

            # Truncate long episode titles
            title = item.episode_title[:50]
            if len(item.episode_title) > 50:
                title = title + "..."

            lines.append(f"  {icon} {state_display:12} | {item.podcast_title} > {title}")
            lines.append(f"     {time_ago}")
            if item.pub_date:
                lines.append(f"     Published: {item.pub_date.strftime('%Y-%m-%d')}")
            lines.append("")

        return "\n".join(lines)

    @staticmethod
    def _format_relative_time(timestamp: datetime) -> str:
        """
        Format timestamp as relative time (e.g., '2 hours ago').

        Args:
            timestamp: Datetime to format

        Returns:
            Relative time string
        """
        # Ensure timestamp is timezone-aware
        if timestamp.tzinfo is None:
            timestamp = timestamp.replace(tzinfo=timezone.utc)

        now = datetime.now(timezone.utc)
        delta = now - timestamp

        seconds = int(delta.total_seconds())

        if seconds < 0:
            return "just now"
        elif seconds < 60:
            return "just now"
        elif seconds < 3600:
            minutes = seconds // 60
            return f"{minutes} minute{'s' if minutes != 1 else ''} ago"
        elif seconds < 86400:
            hours = seconds // 3600
            return f"{hours} hour{'s' if hours != 1 else ''} ago"
        elif seconds < 604800:
            days = seconds // 86400
            return f"{days} day{'s' if days != 1 else ''} ago"
        else:
            weeks = seconds // 604800
            return f"{weeks} week{'s' if weeks != 1 else ''} ago"
