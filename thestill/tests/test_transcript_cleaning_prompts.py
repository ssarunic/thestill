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

from thestill.core.transcript_cleaning_processor import TranscriptCleaningProcessor


def test_prompts_are_saved_with_debug_files(tmp_path, mock_llm_provider_with_defaults):
    """Ensure LLM prompts are persisted as separate markdown files for easier debugging."""
    processor = TranscriptCleaningProcessor(mock_llm_provider_with_defaults)

    transcript_data = {
        "metadata": {"audio_file": "sample.mp3", "language": "en", "duration": 15},
        "segments": [
            {"speaker": "SPEAKER_00", "text": "Um welcome to the show", "start": 0, "end": 5},
            {"speaker": "SPEAKER_01", "text": "Thanks for inviting me", "start": 6, "end": 10},
        ],
    }

    output_path = tmp_path / "clean_transcripts" / "sample_cleaned.md"

    processor.clean_transcript(
        transcript_data=transcript_data,
        podcast_title="Sample Podcast",
        podcast_description="A test show about testing.",
        episode_title="Episode 1",
        episode_description="The pilot episode.",
        output_path=str(output_path),
        save_corrections=True,
        save_metrics=False,
    )

    # Prompts are now saved as separate .md files in debug/prompts/
    prompts_dir = output_path.parent / "debug" / "prompts"
    assert prompts_dir.exists(), f"Prompts directory should exist at {prompts_dir}"

    prompt_files = list(prompts_dir.glob("*.md"))
    assert len(prompt_files) >= 2, f"Expected at least 2 prompt files, got {len(prompt_files)}"

    # Check that we have analysis and speaker_identification prompts
    filenames = [f.name for f in prompt_files]
    has_analysis = any("analysis" in name for name in filenames)
    has_speaker = any("speaker_identification" in name for name in filenames)

    assert has_analysis, f"Expected analysis prompt file, found: {filenames}"
    assert has_speaker, f"Expected speaker_identification prompt file, found: {filenames}"

    # Verify content format of one of the prompt files
    analysis_file = next(f for f in prompt_files if "analysis" in f.name)
    content = analysis_file.read_text(encoding="utf-8")

    # Check that markdown structure is present
    assert "# Prompt" in content, "Prompt file should have markdown header"
    assert "## SYSTEM" in content, "Prompt file should have SYSTEM section"
    assert "## USER" in content, "Prompt file should have USER section"
    assert "**Temperature:**" in content, "Prompt file should show temperature"
    assert "**Max Tokens:**" in content, "Prompt file should show max tokens"
