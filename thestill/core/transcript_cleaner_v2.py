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
Transcript cleaner v2 - Pass 2 of the two-pass transcript cleaning pipeline.

Takes pre-formatted markdown transcript with speaker mapping from Pass 1 (facts extraction) and:
1. Stage 2a: Deterministically substitutes speaker names (no LLM)
2. Stage 2b: Uses LLM to clean spelling, grammar, detect ads, format output

Input: Pre-formatted markdown (from TranscriptFormatter), NOT raw JSON.
This significantly reduces token usage since markdown is much smaller than JSON.
"""

import logging
import re
from pathlib import Path
from typing import Any, Callable, Dict, List, Optional

from thestill.core.llm_provider import LLMProvider
from thestill.models.facts import EpisodeFacts, PodcastFacts

logger = logging.getLogger(__name__)

# Import model configs for dynamic max_tokens lookup
from thestill.core.post_processor import MODEL_CONFIGS

# Type alias for prompt save callback
PromptSaveCallback = Callable[[Dict[str, Any]], None]

# Type alias for streaming chunk callback
StreamingCallback = Callable[[str], None]

# Default max output tokens for unknown models (conservative)
DEFAULT_MAX_OUTPUT_TOKENS = 8192


def get_max_output_tokens(model_name: str) -> int:
    """
    Get the maximum output tokens for a model from MODEL_CONFIGS.

    Args:
        model_name: The model name (e.g., "claude-sonnet-4-5-20250929")

    Returns:
        The max_output_tokens for the model, or DEFAULT_MAX_OUTPUT_TOKENS if unknown
    """
    # Check for exact match first
    if model_name in MODEL_CONFIGS:
        return MODEL_CONFIGS[model_name].max_output_tokens

    # Check for partial match (model names often have date suffixes)
    for config_name, limits in MODEL_CONFIGS.items():
        # Match by prefix (e.g., "claude-sonnet-4-5" matches "claude-sonnet-4-5-20250929")
        if model_name.startswith(config_name.rsplit("-", 1)[0]):
            return limits.max_output_tokens
        if config_name.startswith(model_name.rsplit("-", 1)[0]):
            return limits.max_output_tokens

    # Fallback for common provider patterns
    model_lower = model_name.lower()
    if "gemini" in model_lower:
        return 65536  # Gemini 2.x default
    elif "gpt-4" in model_lower:
        return 16384  # GPT-4o default
    elif "claude" in model_lower:
        return 64000  # Claude 4.x default

    return DEFAULT_MAX_OUTPUT_TOKENS


class TranscriptCleanerV2:
    """
    Two-stage transcript cleaner for Pass 2.

    Stage 2a: Deterministic speaker substitution (on pre-formatted markdown)
    Stage 2b: LLM-based cleanup (spelling, grammar, ads, formatting)

    Input: Pre-formatted markdown from TranscriptFormatter, NOT raw JSON.
    """

    def __init__(
        self,
        provider: LLMProvider,
        chunk_size: int = 100000,
        on_stream_chunk: Optional[StreamingCallback] = None,
    ):
        """
        Initialize transcript cleaner.

        Args:
            provider: LLM provider for cleanup
            chunk_size: Maximum characters per chunk for LLM processing
            on_stream_chunk: Optional callback for streaming output chunks
        """
        self.provider = provider
        self.chunk_size = chunk_size
        self.on_stream_chunk = on_stream_chunk

    def clean_transcript(
        self,
        formatted_markdown: str,
        podcast_facts: Optional[PodcastFacts],
        episode_facts: EpisodeFacts,
        episode_title: str = "",
        on_prompt_ready: Optional[PromptSaveCallback] = None,
    ) -> str:
        """
        Clean transcript using facts from Pass 1.

        Args:
            formatted_markdown: Pre-formatted markdown from TranscriptFormatter
            podcast_facts: Podcast-level facts (hosts, keywords, etc.)
            episode_facts: Episode-specific facts (speaker mapping, guests, etc.)
            episode_title: Title for the output header
            on_prompt_ready: Optional callback invoked BEFORE each LLM call with prompt data

        Returns:
            Cleaned Markdown transcript
        """
        # Stage 2a: Apply speaker substitution (deterministic)
        logger.info("Stage 2a: Applying speaker mapping...")
        markdown_with_speakers = self._apply_speaker_mapping(formatted_markdown, episode_facts)

        # Stage 2b: LLM cleanup
        logger.info("Stage 2b: Cleaning transcript with LLM...")
        cleaned_transcript = self._llm_cleanup(
            formatted_transcript=markdown_with_speakers,
            podcast_facts=podcast_facts,
            episode_facts=episode_facts,
            episode_title=episode_title,
            on_prompt_ready=on_prompt_ready,
        )

        return cleaned_transcript

    def _apply_speaker_mapping(
        self,
        formatted_markdown: str,
        episode_facts: EpisodeFacts,
    ) -> str:
        """
        Stage 2a: Apply speaker mapping to pre-formatted markdown.

        Replaces SPEAKER_XX placeholders with actual names from episode_facts.

        Args:
            formatted_markdown: Pre-formatted markdown with SPEAKER_XX placeholders
            episode_facts: Episode facts with speaker_mapping

        Returns:
            Markdown with speaker names substituted
        """
        if not formatted_markdown:
            return ""

        result = formatted_markdown
        mapping = episode_facts.speaker_mapping

        for speaker_id, speaker_name in mapping.items():
            if not speaker_name:
                continue

            # Remove role suffix for cleaner output (e.g., "Scott Galloway (Host)" -> "Scott Galloway")
            # Keep role in facts file, but not in transcript
            clean_name = speaker_name
            if " (" in clean_name and clean_name.endswith(")"):
                clean_name = clean_name.rsplit(" (", 1)[0]

            # Replace **SPEAKER_XX:** with **Name:**
            # Pattern matches the format from TranscriptFormatter: `[HH:MM:SS]` **SPEAKER_XX:** text
            pattern = rf"\*\*{re.escape(speaker_id)}:\*\*"
            replacement = f"**{clean_name}:**"
            result = re.sub(pattern, replacement, result)

        return result

    def _llm_cleanup(
        self,
        formatted_transcript: str,
        podcast_facts: Optional[PodcastFacts],
        episode_facts: EpisodeFacts,
        episode_title: str,
        on_prompt_ready: Optional[PromptSaveCallback] = None,
    ) -> str:
        """
        Stage 2b: Use LLM to clean the transcript.

        Handles:
        - Spelling and grammar correction (British English)
        - Proper noun fixing using keywords from facts
        - Filler word removal
        - Ad break detection and marking
        - Final formatting
        """
        # Build prompts
        system_prompt = self._build_cleanup_system_prompt()
        user_prompt = self._build_cleanup_user_prompt(
            formatted_transcript=formatted_transcript,
            podcast_facts=podcast_facts,
            episode_facts=episode_facts,
            episode_title=episode_title,
        )

        # Handle chunking for large transcripts
        if len(formatted_transcript) > self.chunk_size:
            return self._process_chunks(
                formatted_transcript=formatted_transcript,
                system_prompt=system_prompt,
                podcast_facts=podcast_facts,
                episode_facts=episode_facts,
                episode_title=episode_title,
                on_prompt_ready=on_prompt_ready,
            )

        # Get max tokens for this model
        max_tokens = get_max_output_tokens(self.provider.get_model_name())
        logger.debug(f"Using max_tokens={max_tokens} for model {self.provider.get_model_name()}")

        # Prepare messages
        messages = [
            {"role": "system", "content": system_prompt},
            {"role": "user", "content": user_prompt},
        ]

        # Save prompt BEFORE LLM call
        if on_prompt_ready is not None:
            on_prompt_ready(
                {
                    "phase": "v2_cleanup",
                    "messages": messages,
                    "temperature": 0.1,
                    "max_tokens": max_tokens,
                    "input_chars": len(formatted_transcript),
                }
            )

        # Single-chunk processing - use streaming if callback provided and supported
        if self.on_stream_chunk and hasattr(self.provider, "chat_completion_streaming"):
            response = self.provider.chat_completion_streaming(
                messages=messages,
                temperature=0.1,
                max_tokens=max_tokens,
                on_chunk=self.on_stream_chunk,
            )
        else:
            response = self.provider.chat_completion(
                messages=messages,
                temperature=0.1,
                max_tokens=max_tokens,
            )

        return response.strip()

    def _process_chunks(
        self,
        formatted_transcript: str,
        system_prompt: str,
        podcast_facts: Optional[PodcastFacts],
        episode_facts: EpisodeFacts,
        episode_title: str,
        on_prompt_ready: Optional[PromptSaveCallback] = None,
    ) -> str:
        """Process large transcripts in chunks."""
        chunks = self._split_into_chunks(formatted_transcript)
        cleaned_chunks = []

        # Get max tokens for this model
        max_tokens = get_max_output_tokens(self.provider.get_model_name())

        for i, chunk in enumerate(chunks):
            logger.info(f"Processing chunk {i + 1}/{len(chunks)}...")

            user_prompt = self._build_cleanup_user_prompt(
                formatted_transcript=chunk,
                podcast_facts=podcast_facts,
                episode_facts=episode_facts,
                episode_title=f"{episode_title} (Part {i + 1}/{len(chunks)})" if episode_title else "",
            )

            # Prepare messages
            messages = [
                {"role": "system", "content": system_prompt},
                {"role": "user", "content": user_prompt},
            ]

            # Save prompt BEFORE LLM call
            if on_prompt_ready is not None:
                on_prompt_ready(
                    {
                        "phase": "v2_cleanup",
                        "chunk": i + 1,
                        "total_chunks": len(chunks),
                        "messages": messages,
                        "temperature": 0.1,
                        "max_tokens": max_tokens,
                        "input_chars": len(chunk),
                    }
                )

            # Use streaming if callback provided and supported
            if self.on_stream_chunk and hasattr(self.provider, "chat_completion_streaming"):
                response = self.provider.chat_completion_streaming(
                    messages=messages,
                    temperature=0.1,
                    max_tokens=max_tokens,
                    on_chunk=self.on_stream_chunk,
                )
            else:
                response = self.provider.chat_completion(
                    messages=messages,
                    temperature=0.1,
                    max_tokens=max_tokens,
                )

            cleaned_chunks.append(response.strip())

        # Combine chunks
        return "\n\n".join(cleaned_chunks)

    def _split_into_chunks(self, text: str) -> List[str]:
        """Split transcript into chunks at paragraph boundaries."""
        paragraphs = text.split("\n\n")
        chunks = []
        current_chunk = []
        current_size = 0

        for para in paragraphs:
            para_size = len(para) + 2  # +2 for \n\n

            if current_size + para_size > self.chunk_size and current_chunk:
                chunks.append("\n\n".join(current_chunk))
                current_chunk = [para]
                current_size = para_size
            else:
                current_chunk.append(para)
                current_size += para_size

        if current_chunk:
            chunks.append("\n\n".join(current_chunk))

        return chunks

    def _build_cleanup_system_prompt(self) -> str:
        """Build system prompt for transcript cleanup."""
        return """You are an expert podcast editor and proofreader. Your goal is to polish a transcript into a readable Markdown document.

The transcript has already been formatted with speaker names and timestamps. Your tasks:

1. AD DETECTION:
   - Identify commercial segments (sponsor reads, product promotions)
   - Replace ad content with: > **[TIMESTAMP] [AD BREAK]** - Sponsor Name
   - Keep the timestamp from the start of the ad segment

2. EDITING & CORRECTION:
   - Fix spelling and grammar while PRESERVING the speakers' original style
   - Do NOT change between American/British English - keep the original variant
   - Fix proper nouns using the Keywords list provided (includes common mishearings)
   - Remove filler words (um, uh, like, you know) if they disrupt readability
   - Keep the banter and personality natural - don't over-edit

3. FORMATTING:
   - Output strictly in Markdown
   - Preserve the format: **[MM:SS] Speaker Name** Text of the segment...
   - Add a blank line between each speaker turn
   - PRESERVE timestamps exactly as given (do not modify them)
   - You may merge very short consecutive segments from the same speaker

IMPORTANT:
- Do NOT add any preamble or explanation
- Do NOT wrap output in code blocks
- Output ONLY the cleaned transcript
- Maintain the exact speaker names provided (already substituted)"""

    def _build_cleanup_user_prompt(
        self,
        formatted_transcript: str,
        podcast_facts: Optional[PodcastFacts],
        episode_facts: EpisodeFacts,
        episode_title: str,
    ) -> str:
        """Build user prompt for transcript cleanup."""
        lines = []

        # Add podcast facts context
        if podcast_facts:
            lines.append("PODCAST FACTS:")
            if podcast_facts.hosts:
                lines.append(f"Hosts: {', '.join(podcast_facts.hosts)}")
            if podcast_facts.sponsors:
                lines.append(f"Known Sponsors: {', '.join(podcast_facts.sponsors)}")
            if podcast_facts.keywords:
                lines.append(f"Keywords & Mishearings: {', '.join(podcast_facts.keywords)}")
            if podcast_facts.style_notes:
                lines.append(f"Style: {', '.join(podcast_facts.style_notes)}")
            lines.append("")

        # Add episode facts context
        lines.append("EPISODE FACTS:")
        if episode_facts.guests:
            lines.append(f"Guests: {', '.join(episode_facts.guests)}")
        if episode_facts.topics_keywords:
            lines.append(f"Topics: {', '.join(episode_facts.topics_keywords)}")
        if episode_facts.ad_sponsors:
            lines.append(f"Ad Sponsors: {', '.join(episode_facts.ad_sponsors)}")
        lines.append("")

        # Add transcript
        lines.append("TRANSCRIPT TO CLEAN:")
        lines.append(formatted_transcript)

        return "\n".join(lines)

    def clean_transcript_deterministic_only(
        self,
        formatted_markdown: str,
        episode_facts: EpisodeFacts,
    ) -> str:
        """
        Clean transcript using only deterministic steps (no LLM).

        Useful for testing or when LLM is unavailable.

        Args:
            formatted_markdown: Pre-formatted markdown from TranscriptFormatter
            episode_facts: Episode facts with speaker mapping

        Returns:
            Markdown with speaker names substituted (no LLM cleanup)
        """
        return self._apply_speaker_mapping(formatted_markdown, episode_facts)
