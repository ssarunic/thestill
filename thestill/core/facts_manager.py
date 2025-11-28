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
Facts manager for loading and saving podcast/episode facts as Markdown files.

Facts are stored as human-editable Markdown files that can be easily embedded
into LLM prompts and version-controlled.
"""

import logging
import re
from pathlib import Path
from typing import Optional

from thestill.models.facts import EpisodeFacts, PodcastFacts
from thestill.utils.path_manager import PathManager

logger = logging.getLogger(__name__)


def slugify(text: str) -> str:
    """
    Convert text to a URL/filesystem-safe slug.

    Args:
        text: Text to slugify (e.g., podcast title)

    Returns:
        Lowercase string with spaces replaced by hyphens, special chars removed
    """
    # Convert to lowercase
    slug = text.lower()
    # Replace spaces and underscores with hyphens
    slug = re.sub(r"[\s_]+", "-", slug)
    # Remove special characters except hyphens
    slug = re.sub(r"[^a-z0-9\-]", "", slug)
    # Collapse multiple hyphens
    slug = re.sub(r"-+", "-", slug)
    # Strip leading/trailing hyphens
    slug = slug.strip("-")
    return slug or "unnamed"


class FactsManager:
    """
    Manages loading and saving facts as Markdown files.

    Facts are stored in two directories:
    - data/podcast_facts/{slug}.facts.md - Podcast-level facts
    - data/episode_facts/{episode_id}.facts.md - Episode-specific facts
    """

    def __init__(self, path_manager: PathManager):
        """
        Initialize FactsManager.

        Args:
            path_manager: PathManager instance for constructing paths
        """
        self.path_manager = path_manager
        self._podcast_facts_dir = "podcast_facts"
        self._episode_facts_dir = "episode_facts"

    def podcast_facts_dir(self) -> Path:
        """Get path to podcast facts directory."""
        return self.path_manager.storage_path / self._podcast_facts_dir

    def episode_facts_dir(self) -> Path:
        """Get path to episode facts directory."""
        return self.path_manager.storage_path / self._episode_facts_dir

    def ensure_facts_directories(self) -> None:
        """Create facts directories if they don't exist."""
        self.podcast_facts_dir().mkdir(parents=True, exist_ok=True)
        self.episode_facts_dir().mkdir(parents=True, exist_ok=True)

    def get_podcast_facts_path(self, podcast_slug: str) -> Path:
        """
        Get path to podcast facts file.

        Args:
            podcast_slug: Slugified podcast title

        Returns:
            Path to {slug}.facts.md file
        """
        return self.podcast_facts_dir() / f"{podcast_slug}.facts.md"

    def get_episode_facts_path(self, episode_id: str) -> Path:
        """
        Get path to episode facts file.

        Args:
            episode_id: Episode UUID

        Returns:
            Path to {episode_id}.facts.md file
        """
        return self.episode_facts_dir() / f"{episode_id}.facts.md"

    def load_podcast_facts(self, podcast_slug: str) -> Optional[PodcastFacts]:
        """
        Load podcast facts from Markdown file.

        Args:
            podcast_slug: Slugified podcast title

        Returns:
            PodcastFacts if file exists, None otherwise
        """
        path = self.get_podcast_facts_path(podcast_slug)
        if not path.exists():
            logger.debug(f"No podcast facts found at {path}")
            return None

        content = path.read_text(encoding="utf-8")
        return self._parse_podcast_facts(content)

    def save_podcast_facts(self, podcast_slug: str, facts: PodcastFacts) -> Path:
        """
        Save podcast facts to Markdown file.

        Args:
            podcast_slug: Slugified podcast title
            facts: PodcastFacts to save

        Returns:
            Path to saved file
        """
        self.ensure_facts_directories()
        path = self.get_podcast_facts_path(podcast_slug)
        content = self._render_podcast_facts(facts)
        path.write_text(content, encoding="utf-8")
        logger.info(f"Saved podcast facts to {path}")
        return path

    def load_episode_facts(self, episode_id: str) -> Optional[EpisodeFacts]:
        """
        Load episode facts from Markdown file.

        Args:
            episode_id: Episode UUID

        Returns:
            EpisodeFacts if file exists, None otherwise
        """
        path = self.get_episode_facts_path(episode_id)
        if not path.exists():
            logger.debug(f"No episode facts found at {path}")
            return None

        content = path.read_text(encoding="utf-8")
        return self._parse_episode_facts(content)

    def save_episode_facts(self, episode_id: str, facts: EpisodeFacts) -> Path:
        """
        Save episode facts to Markdown file.

        Args:
            episode_id: Episode UUID
            facts: EpisodeFacts to save

        Returns:
            Path to saved file
        """
        self.ensure_facts_directories()
        path = self.get_episode_facts_path(episode_id)
        content = self._render_episode_facts(facts)
        path.write_text(content, encoding="utf-8")
        logger.info(f"Saved episode facts to {path}")
        return path

    def _render_podcast_facts(self, facts: PodcastFacts) -> str:
        """Render PodcastFacts to Markdown format."""
        lines = [f"# {facts.podcast_title}", ""]

        if facts.hosts:
            lines.append("## Hosts")
            for host in facts.hosts:
                lines.append(f"- {host}")
            lines.append("")

        if facts.recurring_roles:
            lines.append("## Recurring Roles")
            for role in facts.recurring_roles:
                lines.append(f"- {role}")
            lines.append("")

        if facts.production_team:
            lines.append("## Production Team")
            for member in facts.production_team:
                lines.append(f"- {member}")
            lines.append("")

        if facts.known_guests:
            lines.append("## Known Guests")
            for guest in facts.known_guests:
                lines.append(f"- {guest}")
            lines.append("")

        if facts.sponsors:
            lines.append("## Sponsors/Advertisers")
            for sponsor in facts.sponsors:
                lines.append(f"- {sponsor}")
            lines.append("")

        if facts.keywords:
            lines.append("## Keywords & Proper Nouns")
            for keyword in facts.keywords:
                lines.append(f"- {keyword}")
            lines.append("")

        if facts.style_notes:
            lines.append("## Style Notes")
            for note in facts.style_notes:
                lines.append(f"- {note}")
            lines.append("")

        return "\n".join(lines)

    def _render_episode_facts(self, facts: EpisodeFacts) -> str:
        """Render EpisodeFacts to Markdown format."""
        lines = [f"# Episode: {facts.episode_title}", ""]

        if facts.speaker_mapping:
            lines.append("## Speaker Mapping")
            for speaker_id, name in sorted(facts.speaker_mapping.items()):
                lines.append(f"- {speaker_id}: {name}")
            lines.append("")

        if facts.guests:
            lines.append("## Guest(s)")
            for guest in facts.guests:
                lines.append(f"- {guest}")
            lines.append("")

        if facts.topics_keywords:
            lines.append("## Topics/Keywords")
            for topic in facts.topics_keywords:
                lines.append(f"- {topic}")
            lines.append("")

        if facts.ad_sponsors:
            lines.append("## Ad Sponsors This Episode")
            for sponsor in facts.ad_sponsors:
                lines.append(f"- {sponsor}")
            lines.append("")

        return "\n".join(lines)

    def _parse_podcast_facts(self, content: str) -> PodcastFacts:
        """Parse Markdown content into PodcastFacts."""
        sections = self._parse_markdown_sections(content)

        # Extract title from first H1
        title_match = re.search(r"^#\s+(.+)$", content, re.MULTILINE)
        title = title_match.group(1).strip() if title_match else "Unknown Podcast"

        return PodcastFacts(
            podcast_title=title,
            hosts=sections.get("hosts", []),
            recurring_roles=sections.get("recurring roles", []),
            production_team=sections.get("production team", []),
            known_guests=sections.get("known guests", []),
            sponsors=sections.get("sponsors/advertisers", []),
            keywords=sections.get("keywords & proper nouns", []),
            style_notes=sections.get("style notes", []),
        )

    def _parse_episode_facts(self, content: str) -> EpisodeFacts:
        """Parse Markdown content into EpisodeFacts."""
        sections = self._parse_markdown_sections(content)

        # Extract title from first H1 (format: "# Episode: Title")
        title_match = re.search(r"^#\s+Episode:\s*(.+)$", content, re.MULTILINE)
        title = title_match.group(1).strip() if title_match else "Unknown Episode"

        # Parse speaker mapping (format: "- SPEAKER_01: Name (Role)")
        speaker_mapping = {}
        for item in sections.get("speaker mapping", []):
            match = re.match(r"(SPEAKER_\d+):\s*(.+)", item)
            if match:
                speaker_mapping[match.group(1)] = match.group(2).strip()

        return EpisodeFacts(
            episode_title=title,
            speaker_mapping=speaker_mapping,
            guests=sections.get("guest(s)", []),
            topics_keywords=sections.get("topics/keywords", []),
            ad_sponsors=sections.get("ad sponsors this episode", []),
        )

    def _parse_markdown_sections(self, content: str) -> dict:
        """
        Parse Markdown content into sections.

        Returns a dict mapping section headers (lowercase) to list of items.
        """
        sections = {}
        current_section = None
        current_items = []

        for line in content.split("\n"):
            line = line.strip()

            # Check for H2 section header
            h2_match = re.match(r"^##\s+(.+)$", line)
            if h2_match:
                # Save previous section
                if current_section:
                    sections[current_section] = current_items
                current_section = h2_match.group(1).strip().lower()
                current_items = []
                continue

            # Check for list item
            if line.startswith("- ") and current_section:
                current_items.append(line[2:].strip())

        # Save last section
        if current_section:
            sections[current_section] = current_items

        return sections

    def get_podcast_facts_markdown(self, podcast_slug: str) -> Optional[str]:
        """
        Get raw Markdown content of podcast facts for embedding in prompts.

        Args:
            podcast_slug: Slugified podcast title

        Returns:
            Markdown content or None if file doesn't exist
        """
        path = self.get_podcast_facts_path(podcast_slug)
        if not path.exists():
            return None
        return path.read_text(encoding="utf-8")

    def get_episode_facts_markdown(self, episode_id: str) -> Optional[str]:
        """
        Get raw Markdown content of episode facts for embedding in prompts.

        Args:
            episode_id: Episode UUID

        Returns:
            Markdown content or None if file doesn't exist
        """
        path = self.get_episode_facts_path(episode_id)
        if not path.exists():
            return None
        return path.read_text(encoding="utf-8")
