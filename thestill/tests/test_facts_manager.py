#!/usr/bin/env python3
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

"""Tests for FactsManager - loading and saving facts as Markdown files."""

import pytest

from thestill.core.facts_manager import FactsManager, slugify
from thestill.models.facts import EpisodeFacts, PodcastFacts
from thestill.utils.path_manager import PathManager


class TestSlugify:
    """Tests for the slugify function."""

    def test_basic_slugify(self):
        assert slugify("Prof G Markets") == "prof-g-markets"

    def test_special_characters_removed(self):
        assert slugify("The Daily Show!") == "the-daily-show"

    def test_multiple_spaces(self):
        assert slugify("Too   Many   Spaces") == "too-many-spaces"

    def test_underscores_to_hyphens(self):
        assert slugify("snake_case_name") == "snake-case-name"

    def test_empty_string(self):
        assert slugify("") == "unnamed"

    def test_only_special_chars(self):
        assert slugify("!!!???") == "unnamed"

    def test_unicode_removed(self):
        assert slugify("Café") == "caf"

    def test_leading_trailing_hyphens(self):
        assert slugify("-test-") == "test"


class TestFactsManagerDirectories:
    """Tests for directory management."""

    def test_podcast_facts_dir(self, tmp_path):
        path_manager = PathManager(str(tmp_path))
        facts_manager = FactsManager(path_manager)
        assert facts_manager.podcast_facts_dir() == tmp_path / "podcast_facts"

    def test_episode_facts_dir(self, tmp_path):
        path_manager = PathManager(str(tmp_path))
        facts_manager = FactsManager(path_manager)
        assert facts_manager.episode_facts_dir() == tmp_path / "episode_facts"

    def test_ensure_facts_directories_creates_dirs(self, tmp_path):
        path_manager = PathManager(str(tmp_path))
        facts_manager = FactsManager(path_manager)

        assert not facts_manager.podcast_facts_dir().exists()
        assert not facts_manager.episode_facts_dir().exists()

        facts_manager.ensure_facts_directories()

        assert facts_manager.podcast_facts_dir().exists()
        assert facts_manager.episode_facts_dir().exists()


class TestPodcastFactsPaths:
    """Tests for podcast facts file paths."""

    def test_get_podcast_facts_path(self, tmp_path):
        path_manager = PathManager(str(tmp_path))
        facts_manager = FactsManager(path_manager)

        path = facts_manager.get_podcast_facts_path("prof-g-markets")
        assert path == tmp_path / "podcast_facts" / "prof-g-markets.facts.md"


class TestEpisodeFactsPaths:
    """Tests for episode facts file paths."""

    def test_get_episode_facts_path(self, tmp_path):
        path_manager = PathManager(str(tmp_path))
        facts_manager = FactsManager(path_manager)

        path = facts_manager.get_episode_facts_path("abc-123-def")
        assert path == tmp_path / "episode_facts" / "abc-123-def.facts.md"


class TestSavePodcastFacts:
    """Tests for saving podcast facts."""

    def test_save_podcast_facts_creates_file(self, tmp_path):
        path_manager = PathManager(str(tmp_path))
        facts_manager = FactsManager(path_manager)

        facts = PodcastFacts(
            podcast_title="Prof G Markets",
            hosts=["Scott Galloway - Main host", "Ed Elson - Co-host"],
            sponsors=["Blueair", "Odoo"],
        )

        path = facts_manager.save_podcast_facts("prof-g-markets", facts)

        assert path.exists()
        content = path.read_text()
        assert "# Prof G Markets" in content
        assert "Scott Galloway" in content
        assert "Blueair" in content

    def test_save_podcast_facts_with_all_fields(self, tmp_path):
        path_manager = PathManager(str(tmp_path))
        facts_manager = FactsManager(path_manager)

        facts = PodcastFacts(
            podcast_title="Test Podcast",
            hosts=["Host 1"],
            recurring_roles=["Ad Narrator"],
            known_guests=["Guest 1"],
            sponsors=["Sponsor 1"],
            keywords=["Keyword (note)"],
            style_notes=["British English"],
        )

        path = facts_manager.save_podcast_facts("test-podcast", facts)
        content = path.read_text()

        assert "## Hosts" in content
        assert "## Recurring Roles" in content
        assert "## Known Guests" in content
        assert "## Sponsors/Advertisers" in content
        assert "## Keywords & Proper Nouns" in content
        assert "## Style Notes" in content


class TestLoadPodcastFacts:
    """Tests for loading podcast facts."""

    def test_load_podcast_facts_not_found(self, tmp_path):
        path_manager = PathManager(str(tmp_path))
        facts_manager = FactsManager(path_manager)

        result = facts_manager.load_podcast_facts("nonexistent")
        assert result is None

    def test_load_podcast_facts_roundtrip(self, tmp_path):
        path_manager = PathManager(str(tmp_path))
        facts_manager = FactsManager(path_manager)

        original = PodcastFacts(
            podcast_title="Prof G Markets",
            hosts=["Scott Galloway - Host", "Ed Elson - Co-host"],
            recurring_roles=["Ad Narrator"],
            known_guests=["Michael Cembalest - JPMorgan"],
            sponsors=["Blueair", "Odoo"],
            keywords=["Odoo (misheard as Odio)"],
            style_notes=["British English preferred"],
        )

        facts_manager.save_podcast_facts("prof-g", original)
        loaded = facts_manager.load_podcast_facts("prof-g")

        assert loaded is not None
        assert loaded.podcast_title == original.podcast_title
        assert loaded.hosts == original.hosts
        assert loaded.recurring_roles == original.recurring_roles
        assert loaded.known_guests == original.known_guests
        assert loaded.sponsors == original.sponsors
        assert loaded.keywords == original.keywords
        assert loaded.style_notes == original.style_notes


class TestSaveEpisodeFacts:
    """Tests for saving episode facts."""

    def test_save_episode_facts_creates_file(self, tmp_path):
        path_manager = PathManager(str(tmp_path))
        facts_manager = FactsManager(path_manager)

        facts = EpisodeFacts(
            episode_title="JPMorgan's Playbook",
            speaker_mapping={
                "SPEAKER_01": "Scott Galloway (Host)",
                "SPEAKER_02": "Ed Elson (Co-host)",
            },
            guests=["Michael Cembalest - JPMorgan"],
            ad_sponsors=["Blueair"],
        )

        path = facts_manager.save_episode_facts("ep-123", facts)

        assert path.exists()
        content = path.read_text()
        assert "# Episode: JPMorgan's Playbook" in content
        assert "SPEAKER_01: Scott Galloway" in content


class TestLoadEpisodeFacts:
    """Tests for loading episode facts."""

    def test_load_episode_facts_not_found(self, tmp_path):
        path_manager = PathManager(str(tmp_path))
        facts_manager = FactsManager(path_manager)

        result = facts_manager.load_episode_facts("nonexistent")
        assert result is None

    def test_load_episode_facts_roundtrip(self, tmp_path):
        path_manager = PathManager(str(tmp_path))
        facts_manager = FactsManager(path_manager)

        original = EpisodeFacts(
            episode_title="Test Episode",
            speaker_mapping={
                "SPEAKER_01": "Host Name (Host)",
                "SPEAKER_02": "Guest Name (Guest)",
            },
            guests=["Guest Name - Company"],
            topics_keywords=["Topic 1", "Topic 2"],
            ad_sponsors=["Sponsor 1"],
        )

        facts_manager.save_episode_facts("ep-456", original)
        loaded = facts_manager.load_episode_facts("ep-456")

        assert loaded is not None
        assert loaded.episode_title == original.episode_title
        assert loaded.speaker_mapping == original.speaker_mapping
        assert loaded.guests == original.guests
        assert loaded.topics_keywords == original.topics_keywords
        assert loaded.ad_sponsors == original.ad_sponsors


class TestGetFactsMarkdown:
    """Tests for getting raw markdown content."""

    def test_get_podcast_facts_markdown(self, tmp_path):
        path_manager = PathManager(str(tmp_path))
        facts_manager = FactsManager(path_manager)

        # Not created yet
        assert facts_manager.get_podcast_facts_markdown("test") is None

        # Create it
        facts = PodcastFacts(podcast_title="Test", hosts=["Host"])
        facts_manager.save_podcast_facts("test", facts)

        # Now should return content
        md = facts_manager.get_podcast_facts_markdown("test")
        assert md is not None
        assert "# Test" in md

    def test_get_episode_facts_markdown(self, tmp_path):
        path_manager = PathManager(str(tmp_path))
        facts_manager = FactsManager(path_manager)

        # Not created yet
        assert facts_manager.get_episode_facts_markdown("ep-123") is None

        # Create it
        facts = EpisodeFacts(episode_title="Test Episode")
        facts_manager.save_episode_facts("ep-123", facts)

        # Now should return content
        md = facts_manager.get_episode_facts_markdown("ep-123")
        assert md is not None
        assert "# Episode: Test Episode" in md


class TestMarkdownParsing:
    """Tests for markdown parsing edge cases."""

    def test_parse_podcast_facts_empty_sections(self, tmp_path):
        path_manager = PathManager(str(tmp_path))
        facts_manager = FactsManager(path_manager)

        # Create file with empty sections
        facts_manager.ensure_facts_directories()
        path = facts_manager.get_podcast_facts_path("test")
        path.write_text("# Test Podcast\n\n## Hosts\n\n## Sponsors/Advertisers\n")

        loaded = facts_manager.load_podcast_facts("test")
        assert loaded is not None
        assert loaded.podcast_title == "Test Podcast"
        assert loaded.hosts == []
        assert loaded.sponsors == []

    def test_parse_episode_facts_speaker_mapping_format(self, tmp_path):
        path_manager = PathManager(str(tmp_path))
        facts_manager = FactsManager(path_manager)

        # Create file with speaker mapping
        facts_manager.ensure_facts_directories()
        path = facts_manager.get_episode_facts_path("ep-test")
        path.write_text(
            """# Episode: Test

## Speaker Mapping
- SPEAKER_00: Scott Galloway (Host)
- SPEAKER_01: Ed Elson (Co-host)
- SPEAKER_02: Ad Narrator
"""
        )

        loaded = facts_manager.load_episode_facts("ep-test")
        assert loaded is not None
        assert loaded.speaker_mapping == {
            "SPEAKER_00": "Scott Galloway (Host)",
            "SPEAKER_01": "Ed Elson (Co-host)",
            "SPEAKER_02": "Ad Narrator",
        }
