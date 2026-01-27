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
Digest generator for creating morning briefing documents.

Implements THES-27: Generates a markdown digest from processed episodes,
grouping them by podcast with brief descriptions extracted from summaries.
"""

import re
from dataclasses import dataclass, field
from datetime import datetime, timezone
from pathlib import Path
from typing import Dict, List, Optional, Tuple

from structlog import get_logger

from ..models.podcast import Episode, Podcast
from ..utils.path_manager import PathManager
from ..utils.url_generator import UrlGenerator

logger = get_logger(__name__)


@dataclass
class DigestEpisodeInfo:
    """Information about an episode for digest generation."""

    podcast: Podcast
    episode: Episode
    brief_description: Optional[str] = None
    summary_link: Optional[str] = None
    failed: bool = False
    failure_reason: Optional[str] = None


@dataclass
class DigestStats:
    """Statistics for the digest."""

    total_episodes: int = 0
    successful_episodes: int = 0
    failed_episodes: int = 0
    podcasts_count: int = 0
    processing_time_seconds: Optional[float] = None

    @property
    def success_rate(self) -> float:
        """Calculate success rate as percentage."""
        if self.total_episodes == 0:
            return 0.0
        return (self.successful_episodes / self.total_episodes) * 100


@dataclass
class DigestContent:
    """Generated digest content."""

    markdown: str
    stats: DigestStats
    generated_at: datetime = field(default_factory=lambda: datetime.now(timezone.utc))
    output_path: Optional[Path] = None


class DigestGenerator:
    """
    Generates markdown digest documents from processed episodes.

    The digest provides a "morning briefing" view of recently processed
    podcast episodes, with:
    - Header with timestamp and summary statistics
    - Episodes grouped by podcast
    - Brief descriptions extracted from summaries
    - Links to full summary files
    - Failure section if any episodes failed

    Usage:
        generator = DigestGenerator(path_manager)
        content = generator.generate(episodes, stats)
        generator.write(content, output_path)
    """

    def __init__(self, path_manager: PathManager, url_generator: UrlGenerator | None = None):
        """
        Initialize digest generator.

        Args:
            path_manager: PathManager for resolving file paths
            url_generator: UrlGenerator for creating web URLs (optional, creates default if not provided)
        """
        self.path_manager = path_manager
        self.url_generator = url_generator or UrlGenerator()

    def generate(
        self,
        episodes: List[Tuple[Podcast, Episode]],
        processing_time_seconds: Optional[float] = None,
        failures: Optional[List[Tuple[Podcast, Episode, str]]] = None,
    ) -> DigestContent:
        """
        Generate a digest from processed episodes.

        Args:
            episodes: List of (Podcast, Episode) tuples that were processed
            processing_time_seconds: Optional total processing time
            failures: Optional list of (Podcast, Episode, reason) for failed episodes

        Returns:
            DigestContent with markdown and statistics
        """
        failures = failures or []

        # Build episode info with descriptions
        episode_infos = []
        for podcast, episode in episodes:
            info = self._build_episode_info(podcast, episode)
            episode_infos.append(info)

        # Add failures
        for podcast, episode, reason in failures:
            info = DigestEpisodeInfo(
                podcast=podcast,
                episode=episode,
                failed=True,
                failure_reason=reason,
            )
            episode_infos.append(info)

        # Calculate stats
        stats = DigestStats(
            total_episodes=len(episode_infos),
            successful_episodes=sum(1 for e in episode_infos if not e.failed),
            failed_episodes=sum(1 for e in episode_infos if e.failed),
            podcasts_count=len({e.podcast.id for e in episode_infos}),
            processing_time_seconds=processing_time_seconds,
        )

        # Generate markdown
        markdown = self._generate_markdown(episode_infos, stats)

        logger.info(
            "Digest generated",
            total_episodes=stats.total_episodes,
            successful=stats.successful_episodes,
            failed=stats.failed_episodes,
            podcasts=stats.podcasts_count,
        )

        return DigestContent(markdown=markdown, stats=stats)

    def write(self, content: DigestContent, output_path: Path) -> Path:
        """
        Write digest content to a file.

        Args:
            content: DigestContent to write
            output_path: Path to write to

        Returns:
            The output path
        """
        output_path.parent.mkdir(parents=True, exist_ok=True)

        with open(output_path, "w", encoding="utf-8") as f:
            f.write(content.markdown)

        content.output_path = output_path

        logger.info("Digest written", path=str(output_path))

        return output_path

    def _build_episode_info(self, podcast: Podcast, episode: Episode) -> DigestEpisodeInfo:
        """Build episode info with description extracted from summary."""
        info = DigestEpisodeInfo(podcast=podcast, episode=episode)

        # Try to read and extract description from summary
        if episode.summary_path:
            summary_file = self.path_manager.summary_file(episode.summary_path)
            if summary_file.exists():
                try:
                    with open(summary_file, "r", encoding="utf-8") as f:
                        summary_text = f.read()
                    info.brief_description = self._extract_executive_summary(summary_text)
                    info.summary_link = episode.summary_path
                except Exception as e:
                    logger.warning(
                        "Failed to read summary",
                        episode_id=episode.external_id,
                        error=str(e),
                    )

        return info

    def _extract_executive_summary(self, summary_text: str) -> Optional[str]:
        """
        Extract 2-3 sentences from 'The Gist' section.

        The Gist section has this structure:
        ## 1. 🎙️ The Gist
        [Optional host/guest intro]
        [2 sentence summary]
        **The Big 3-5 Takeaways:**
        ...

        We want the 2 sentences before "The Big 3-5 Takeaways".
        """
        # Find "The Gist" section
        gist_pattern = r"##\s*1\.?\s*(?:🎙️\s*)?The Gist\s*\n(.*?)(?=\*\*The Big|##\s*2\.|$)"
        match = re.search(gist_pattern, summary_text, re.DOTALL | re.IGNORECASE)

        if not match:
            # Fallback: try to find any "Gist" section
            gist_pattern = r"(?:The Gist|Executive Summary|Overview)\s*\n+(.*?)(?=\*\*|##|$)"
            match = re.search(gist_pattern, summary_text, re.DOTALL | re.IGNORECASE)

        if not match:
            return None

        gist_text = match.group(1).strip()

        # Remove markdown formatting artifacts
        gist_text = re.sub(r"\[[\d:]+\]", "", gist_text)  # Remove timestamps
        gist_text = re.sub(r"\*\*([^*]+)\*\*", r"\1", gist_text)  # Remove bold
        gist_text = re.sub(r"\*([^*]+)\*", r"\1", gist_text)  # Remove italic
        gist_text = re.sub(r"\s+", " ", gist_text).strip()  # Normalize whitespace

        # Extract sentences (up to 3)
        sentences = self._split_sentences(gist_text)
        if not sentences:
            return None

        # Take first 2-3 sentences, preferring 2
        description = " ".join(sentences[:2])

        # If the description is very short, add a third sentence if available
        if len(description) < 100 and len(sentences) > 2:
            description = " ".join(sentences[:3])

        return description if description else None

    def _split_sentences(self, text: str) -> List[str]:
        """Split text into sentences."""
        # Simple sentence splitting on common end punctuation
        sentences = re.split(r"(?<=[.!?])\s+", text)
        # Filter out empty strings and very short fragments
        return [s.strip() for s in sentences if s.strip() and len(s.strip()) > 10]

    def _generate_markdown(self, episode_infos: List[DigestEpisodeInfo], stats: DigestStats) -> str:
        """Generate the markdown content for the digest."""
        lines = []

        # Header
        now = datetime.now(timezone.utc)
        lines.append("# Podcast Digest")
        lines.append(f"Generated: {now.strftime('%Y-%m-%d %H:%M')} UTC")
        lines.append("")

        # Summary section
        lines.append("## Summary")
        lines.append("")

        success_text = f"{stats.successful_episodes} of {stats.total_episodes}"
        if stats.failed_episodes > 0:
            success_text += f" ({stats.failed_episodes} failed)"
        lines.append(f"- **Episodes processed:** {success_text}")
        lines.append(f"- **Podcasts updated:** {stats.podcasts_count}")

        if stats.processing_time_seconds:
            time_str = self._format_duration(stats.processing_time_seconds)
            lines.append(f"- **Processing time:** {time_str}")

        lines.append("")
        lines.append("---")
        lines.append("")

        # Group episodes by podcast
        successful_by_podcast: Dict[str, List[DigestEpisodeInfo]] = {}
        failed_episodes: List[DigestEpisodeInfo] = []

        for info in episode_infos:
            if info.failed:
                failed_episodes.append(info)
            else:
                podcast_id = info.podcast.id
                if podcast_id not in successful_by_podcast:
                    successful_by_podcast[podcast_id] = []
                successful_by_podcast[podcast_id].append(info)

        # Episodes by podcast
        for podcast_id, infos in successful_by_podcast.items():
            podcast = infos[0].podcast
            episode_count = len(infos)
            episode_word = "episode" if episode_count == 1 else "episodes"

            lines.append(f"## 🎙️ {podcast.title} ({episode_count} {episode_word})")
            lines.append("")

            for info in infos:
                lines.extend(self._format_episode(info))
                lines.append("")

        # Failures section
        if failed_episodes:
            lines.append("---")
            lines.append("")
            lines.append("## ⚠️ Failed Episodes")
            lines.append("")

            for info in failed_episodes:
                lines.append(f"- **{info.episode.title}** ({info.podcast.title})")
                if info.failure_reason:
                    lines.append(f"  - Reason: {info.failure_reason}")
            lines.append("")

        return "\n".join(lines)

    def _format_episode(self, info: DigestEpisodeInfo) -> List[str]:
        """Format a single episode entry."""
        lines = []
        episode = info.episode

        # Title with link to episode page
        episode_url = self.url_generator.episode(info.podcast.slug, episode.slug)
        lines.append(f"### [{episode.title}]({episode_url})")

        # Metadata line
        meta_parts = []
        if episode.pub_date:
            meta_parts.append(f"**Published:** {episode.pub_date.strftime('%B %d, %Y')}")
        if episode.duration:
            duration_str = self._format_duration(episode.duration)
            meta_parts.append(f"**Duration:** {duration_str}")

        if meta_parts:
            lines.append(" | ".join(meta_parts))
            lines.append("")

        # Brief description
        if info.brief_description:
            lines.append(info.brief_description)
        else:
            # Fallback to episode description if no summary available
            if episode.description:
                # Truncate to ~200 chars
                desc = episode.description[:200]
                if len(episode.description) > 200:
                    desc = desc.rsplit(" ", 1)[0] + "..."
                lines.append(desc)

        return lines

    def _format_duration(self, seconds: float) -> str:
        """Format duration in human-readable format."""
        total_seconds = int(seconds)
        hours = total_seconds // 3600
        minutes = (total_seconds % 3600) // 60
        secs = total_seconds % 60

        if hours > 0:
            return f"{hours}h {minutes}m"
        elif minutes > 0:
            return f"{minutes}m {secs}s"
        else:
            return f"{secs}s"
