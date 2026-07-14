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

"""Focused coverage for spec #58 original-language summaries."""

from pathlib import Path
from unittest.mock import Mock

from thestill.core.post_processor import EpisodeMetadata, TranscriptSummarizer
from thestill.core.summary_artifacts import load_valid_summary_manifest, summary_manifest_key, translation_metadata_key
from thestill.core.summary_translation import SummaryTranslator
from thestill.models.podcast import Episode
from thestill.repositories.podcast_repository import PodcastRepository
from thestill.services.briefing_script_generator import extract_gist
from thestill.services.podcast_service import PodcastService, extract_summary_preview
from thestill.utils.file_storage import LocalFileStorage
from thestill.utils.path_manager import PathManager


class _RecordingProvider:
    def __init__(self, response: str = "translated") -> None:
        self.response = response
        self.calls: list[dict] = []

    def get_model_name(self) -> str:
        return "gpt-4o-mini"

    def chat_completion(self, **kwargs) -> str:
        self.calls.append(kwargs)
        return self.response


def test_summarizer_keeps_existing_british_english_prompt() -> None:
    summarizer = TranscriptSummarizer(_RecordingProvider())  # type: ignore[arg-type]

    prompt = summarizer._get_formatted_system_prompt(EpisodeMetadata(title="Episode", language="en"))

    assert "Use British English." in prompt
    assert "Language requirement" not in prompt


def test_summarizer_localises_all_scaffolding_for_non_english() -> None:
    summarizer = TranscriptSummarizer(_RecordingProvider())  # type: ignore[arg-type]

    prompt = summarizer._get_formatted_system_prompt(EpisodeMetadata(title="Epizoda", language="hr-HR"))

    assert "Write the entire summary in Croatian." in prompt
    assert "including every section header" in prompt
    assert "English wording\nin the template" in prompt
    assert "Use British English." not in prompt


def test_translation_prompt_preserves_markdown_links_and_citation_syntax() -> None:
    provider = _RecordingProvider("## Sažetak\n* Tvrdnja [00:10](?t=10&cite=c0)")

    result = SummaryTranslator(provider).translate(
        "## The Gist\n* Claim [00:10](?t=10&cite=c0)",
        source_language="en",
        target_language="hr",
    )

    assert result.startswith("## Sažetak")
    system_prompt = provider.calls[0]["messages"][0]["content"]
    assert "from English into Croatian" in system_prompt
    assert "link destination byte-for-byte" in system_prompt
    assert "`[[...]]` syntax byte-for-byte" in system_prompt


def test_language_detection_accepts_a_verbose_provider_response() -> None:
    provider = _RecordingProvider("The language is hr.")

    detected = SummaryTranslator(provider).detect_language(
        "## 1. 🎙️ Ukratko\nOvo je hrvatski sažetak.",
        candidates=("en", "hr"),
    )

    assert detected == "hr"


def test_translation_is_cached_as_a_language_suffixed_sibling(tmp_path: Path) -> None:
    path_manager = PathManager(str(tmp_path))
    file_storage = LocalFileStorage(str(tmp_path))
    service = PodcastService(
        tmp_path,
        Mock(spec=PodcastRepository),
        path_manager,
        file_storage=file_storage,
    )
    episode = Episode(
        title="Episode",
        description="Description",
        audio_url="https://example.com/episode.mp3",
        external_id="episode-1",
        summary_path="podcast/episode_summary.md",
    )
    original_path = path_manager.summary_file(episode.summary_path)
    file_storage.write_text(path_manager.to_relative(original_path), "## The Gist\n* Claim [00:10]")
    provider = _RecordingProvider("## Sažetak\n* Tvrdnja [00:10]")

    first = service.get_or_create_summary_translation(
        episode,
        source_language="en",
        target_language="hr",
        provider=provider,  # type: ignore[arg-type]
    )
    second = service.get_or_create_summary_translation(
        episode,
        source_language="en",
        target_language="hr",
        provider=provider,  # type: ignore[arg-type]
    )

    translated_path = original_path.with_suffix(".hr.md")
    assert first == second == "## Sažetak\n* Tvrdnja [00:10]"
    assert file_storage.read_text(path_manager.to_relative(translated_path)) == first
    assert len(provider.calls) == 1
    assert service.get_available_summary_languages(episode, canonical_language="en") == ["en", "hr"]


def test_unmarked_legacy_and_intermediate_summaries_are_classified_once(tmp_path: Path) -> None:
    path_manager = PathManager(str(tmp_path))
    file_storage = LocalFileStorage(str(tmp_path))
    service = PodcastService(tmp_path, Mock(spec=PodcastRepository), path_manager, file_storage=file_storage)
    episode = Episode(
        title="Episode",
        description="Description",
        audio_url="https://example.com/episode.mp3",
        external_id="episode-1",
        summary_path="podcast/episode_summary.md",
    )
    summary_path = path_manager.summary_file(episode.summary_path)
    summary_key = path_manager.to_relative(summary_path)
    content = "## 1. 🎙️ The Gist\nAn English legacy summary."
    file_storage.write_text(summary_key, content)
    provider = _RecordingProvider("en")

    detected = service.detect_and_record_summary_language(
        episode,
        podcast_language="hr",
        provider=provider,  # type: ignore[arg-type]
    )

    assert detected == "en"
    manifest = load_valid_summary_manifest(file_storage, summary_key=summary_key, summary_content=content)
    assert manifest is not None
    assert manifest.canonical_language == "en"
    assert file_storage.exists(summary_manifest_key(summary_key))
    assert len(provider.calls) == 1


def test_untrusted_old_translation_is_ignored_and_replaced(tmp_path: Path) -> None:
    path_manager = PathManager(str(tmp_path))
    file_storage = LocalFileStorage(str(tmp_path))
    service = PodcastService(tmp_path, Mock(spec=PodcastRepository), path_manager, file_storage=file_storage)
    episode = Episode(
        title="Episode",
        description="Description",
        audio_url="https://example.com/episode.mp3",
        external_id="episode-1",
        summary_path="podcast/episode_summary.md",
    )
    original_path = path_manager.summary_file(episode.summary_path)
    translated_path = original_path.with_suffix(".hr.md")
    file_storage.write_text(path_manager.to_relative(original_path), "## The Gist\nEnglish source")
    file_storage.write_text(path_manager.to_relative(translated_path), "mislabeled stale content")

    assert service.get_summary_for_episode(episode, language="hr", canonical_language="en") is None
    assert service.get_available_summary_languages(episode, canonical_language="en") == ["en"]

    provider = _RecordingProvider("## Ukratko\nHrvatski prijevod")
    regenerated = service.get_or_create_summary_translation(
        episode,
        source_language="en",
        target_language="hr",
        provider=provider,  # type: ignore[arg-type]
    )

    translated_key = path_manager.to_relative(translated_path)
    assert regenerated == "## Ukratko\nHrvatski prijevod"
    assert file_storage.read_text(translated_key) == regenerated
    assert file_storage.exists(translation_metadata_key(translated_key))
    assert service.get_available_summary_languages(episode, canonical_language="en") == ["en", "hr"]


def test_localised_first_section_still_feeds_previews_and_briefings() -> None:
    summary = """## 1. 🎙️ Ukratko
Ana razgovara s Markom.

Ovo je detaljan pregled nove tehnologije. Objašnjava zašto je važna običnim ljudima.

## 2. ⏱️ Tijek razgovora
* [00:00] Uvod
"""

    preview = extract_summary_preview(summary)
    gist = extract_gist(summary)

    assert preview == "Ovo je detaljan pregled nove tehnologije. Objašnjava zašto je važna običnim ljudima."
    assert gist is not None
    assert "detaljan pregled nove tehnologije" in gist
