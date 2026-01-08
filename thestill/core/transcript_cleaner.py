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
Transcript cleaner - Pass 2 of the two-pass transcript cleaning pipeline.

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

# Type alias for prompt save callback
PromptSaveCallback = Callable[[Dict[str, Any]], None]

# Type alias for streaming chunk callback
StreamingCallback = Callable[[str], None]


class TranscriptCleaner:
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

        # Get max output tokens for this model
        max_output_tokens = self.provider.get_max_output_tokens()

        # Calculate effective chunk size based on OUTPUT token limit
        # For transcript cleaning, output ≈ input size, so we need to ensure
        # each chunk's output fits within the model's output token limit.
        #
        # Key insight: Even if a model supports 65K output tokens, LLMs tend to
        # produce better quality and more reliable outputs with smaller chunks.
        # We cap at ~16K output tokens (~64K chars) for reliability.
        #
        # Use ~4 chars per token estimate, with 80% safety margin for output.
        practical_output_limit = min(max_output_tokens, 16384)  # Cap at 16K tokens
        max_output_chars = int(practical_output_limit * 4 * 0.8)
        effective_chunk_size = min(self.chunk_size, max_output_chars)

        logger.debug(
            f"Chunk sizing: input={len(formatted_transcript)} chars, "
            f"max_output_tokens={max_output_tokens}, "
            f"effective_chunk_size={effective_chunk_size} chars"
        )

        # Handle chunking for large transcripts
        # Chunk if input exceeds effective chunk size (based on output limit)
        if len(formatted_transcript) > effective_chunk_size:
            return self._process_chunks(
                formatted_transcript=formatted_transcript,
                system_prompt=system_prompt,
                podcast_facts=podcast_facts,
                episode_facts=episode_facts,
                episode_title=episode_title,
                on_prompt_ready=on_prompt_ready,
                effective_chunk_size=effective_chunk_size,
            )

        # Get max tokens for this model
        max_tokens = self.provider.get_max_output_tokens()
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
                    "temperature": 0,
                    "max_tokens": max_tokens,
                    "input_chars": len(formatted_transcript),
                }
            )

        # Single-chunk processing - use streaming if callback provided and supported
        # Temperature 0 for deterministic timestamp handling
        if self.on_stream_chunk and hasattr(self.provider, "chat_completion_streaming"):
            response = self.provider.chat_completion_streaming(
                messages=messages,
                temperature=0,
                max_tokens=max_tokens,
                on_chunk=self.on_stream_chunk,
            )
        elif hasattr(self.provider, "chat_completion_with_continuation"):
            # Use continuation to handle truncated responses
            response = self.provider.chat_completion_with_continuation(
                messages=messages,
                temperature=0,
                max_tokens=max_tokens,
            )
        else:
            response = self.provider.chat_completion(
                messages=messages,
                temperature=0,
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
        effective_chunk_size: Optional[int] = None,
    ) -> str:
        """Process large transcripts in chunks."""
        chunk_size = effective_chunk_size or self.chunk_size
        chunks = self._split_into_chunks(formatted_transcript, chunk_size)
        cleaned_chunks = []

        logger.info(
            f"Splitting transcript into {len(chunks)} chunks "
            f"(chunk_size={chunk_size} chars, total={len(formatted_transcript)} chars)"
        )

        # Get max tokens for this model
        max_tokens = self.provider.get_max_output_tokens()

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
                        "temperature": 0,
                        "max_tokens": max_tokens,
                        "input_chars": len(chunk),
                    }
                )

            # Use streaming if callback provided and supported
            # Temperature 0 for deterministic timestamp handling
            try:
                if self.on_stream_chunk and hasattr(self.provider, "chat_completion_streaming"):
                    response = self.provider.chat_completion_streaming(
                        messages=messages,
                        temperature=0,
                        max_tokens=max_tokens,
                        on_chunk=self.on_stream_chunk,
                    )
                elif hasattr(self.provider, "chat_completion_with_continuation"):
                    # Use continuation to handle truncated responses
                    response = self.provider.chat_completion_with_continuation(
                        messages=messages,
                        temperature=0,
                        max_tokens=max_tokens,
                    )
                else:
                    response = self.provider.chat_completion(
                        messages=messages,
                        temperature=0,
                        max_tokens=max_tokens,
                    )
            except (RuntimeError, ValueError) as e:
                # Re-raise with chunk context for easier debugging
                raise RuntimeError(f"Failed to process chunk {i + 1}/{len(chunks)} ({len(chunk)} chars): {e}") from e

            cleaned_chunks.append(response.strip())

        # Combine chunks
        return "\n\n".join(cleaned_chunks)

    def _split_into_chunks(self, text: str, chunk_size: Optional[int] = None) -> List[str]:
        """Split transcript into chunks at paragraph boundaries."""
        target_size = chunk_size or self.chunk_size
        paragraphs = text.split("\n\n")
        chunks = []
        current_chunk = []
        current_size = 0

        for para in paragraphs:
            para_size = len(para) + 2  # +2 for \n\n

            if current_size + para_size > target_size and current_chunk:
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
        return """You are an expert podcast transcript editor. Your job is to LIGHTLY EDIT an existing transcript - NOT rewrite it.

CRITICAL RULE - VERBATIM PRESERVATION:
- You MUST preserve the speaker's ACTUAL WORDS. This is a transcript of what was said.
- DO NOT paraphrase, summarise, or rewrite sentences.
- DO NOT add content that wasn't in the original.
- DO NOT remove content except filler words and ads.
- If the speaker said something awkwardly, KEEP IT AWKWARD - that's how they spoke.
- Your output should be 95%+ identical to the input, with only minor corrections.

The transcript has already been formatted with speaker names and timestamps. Your tasks:

1. AD & CLIP MANAGEMENT:
   - **Ads:** Identify sponsor reads and product promotions. Replace ad content with:
     > **[TIMESTAMP] [AD BREAK]** - Sponsor Name
   - **Aggressive Ad Detection:** If content is clearly a sponsor read (e.g., "Support for the show comes from...", "promo code", "visit [sponsor].com"), mark it as an [AD BREAK] even if the speaker label says it's the Host or Guest. Diarization labels on ads are often wrong.
   - **Clips/Soundbites:** If a voice labelled "Ad Narrator" or similar is playing a news clip, movie quote, cold open, or transition soundbite (NOT a sponsor read), label the speaker as **[Clip]** or **[Soundbite]** instead.

2. STRICT TIMESTAMP BINDING (CRITICAL):
   - You MUST use the EXACT timestamp provided in the source text.
   - DO NOT calculate, estimate, or shift timestamps.
   - DO NOT invent timestamps or adjust for ad duration.
   - If you merge two segments, use the timestamp of the FIRST segment.
   - Copy timestamps character-for-character from the input.
   - The FIRST timestamp in your output MUST match the FIRST timestamp in the input.

3. ENTITY & PHONETIC REPAIR:
   - Fix proper nouns using the Keywords list provided (includes common mishearings)
   - **Credits Check:** At episode end, map "research team" / "production team" names to the provided facts lists. Common phonetic errors:
     - "dashed line" → "Dashiell Lewin"
     - Names read quickly at the end are often mangled - check the facts carefully
   - If a word sounds like a name but doesn't match any known entity, flag it with [?] rather than guessing

4. MINIMAL EDITING (light touch only):
   - Fix ONLY obvious transcription errors (e.g., "their" vs "there", garbled words)
   - Convert spelling to British English (e.g., 'labour', 'programme', 'realise', 'colour')
   - Remove filler words (um, uh, like, you know) ONLY if excessive
   - DO NOT restructure sentences
   - DO NOT improve eloquence or clarity
   - DO NOT add transitions or summaries

5. FORMATTING:
   - Output strictly in Markdown
   - Preserve the format: **[MM:SS] Speaker Name:** Text of the segment...
   - Add a blank line between each speaker turn
   - You may merge very short consecutive segments from the same speaker (using FIRST timestamp)

IMPORTANT:
- Do NOT add any preamble or explanation
- Do NOT wrap output in code blocks
- Output ONLY the cleaned transcript starting from the FIRST timestamp in the input
- Maintain the exact speaker names provided (already substituted)
- Your output MUST start with the same timestamp as the input starts with"""

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
            if podcast_facts.production_team:
                lines.append(f"Production Team (for credits): {', '.join(podcast_facts.production_team)}")
            if podcast_facts.recurring_roles:
                lines.append(f"Recurring Roles: {', '.join(podcast_facts.recurring_roles)}")
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
