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

"""
Transcript summarizer for podcast transcripts using LLM.
Produces comprehensive analysis with executive summary, quotes, content angles, and social snippets.
"""

from dataclasses import dataclass
from datetime import datetime
from pathlib import Path
from typing import List, Optional

from structlog import get_logger

from thestill.utils.console import ConsoleOutput
from thestill.utils.language_config import normalize_language_code, resolve_language_spec
from thestill.utils.prompt_safety import UNTRUSTED_CONTENT_PREAMBLE, wrap_untrusted

# Import ModelLimits and MODEL_CONFIGS from llm_provider (canonical location)
# Re-export for backward compatibility with existing imports
from .llm_provider import MODEL_CONFIGS, LLMProvider, ModelLimits
from .summary_profiles import GENERAL, SummaryProfile, compose_system_prompt

__all__ = ["MODEL_CONFIGS", "ModelLimits", "TranscriptSummarizer", "EpisodeMetadata"]

logger = get_logger(__name__)


@dataclass
class EpisodeMetadata:
    """Metadata about an episode to include in the summary prompt."""

    title: str
    pub_date: Optional[datetime] = None
    duration_seconds: Optional[int] = None
    podcast_title: Optional[str] = None
    language: str = "en"
    episode_id: Optional[str] = None  # logging only

    @property
    def formatted_date(self) -> str:
        """Format publication date for display."""
        if not self.pub_date:
            return "Unknown date"
        return self.pub_date.strftime("%d %b %Y")

    @property
    def formatted_duration(self) -> str:
        """Format duration for display."""
        if not self.duration_seconds:
            return "Unknown duration"
        hours, remainder = divmod(self.duration_seconds, 3600)
        minutes, seconds = divmod(remainder, 60)
        if hours > 0:
            return f"{hours}h {minutes}min"
        return f"{minutes} min"


class TranscriptSummarizer:
    """LLM-based summarizer for podcast transcripts"""

    # The ``general`` profile's prompt (spec #98); kept as a class attribute
    # for callers and tests that read the original single prompt.
    SYSTEM_PROMPT = compose_system_prompt(GENERAL)

    def __init__(
        self, provider: LLMProvider, max_tokens: Optional[int] = None, console: Optional[ConsoleOutput] = None
    ):
        """
        Initialize transcript summarizer with an LLM provider.

        Args:
            provider: LLMProvider instance (OpenAI, Anthropic, Gemini, or Ollama)
            max_tokens: Maximum tokens per chunk (optional, auto-calculated if not provided)
            console: ConsoleOutput instance for user-facing messages (optional)
        """
        self.provider = provider
        self.console = console or ConsoleOutput()
        model = provider.get_model_name()

        # Get model limits and calculate optimal chunk size
        self.model_limits = MODEL_CONFIGS.get(model)
        if self.model_limits is None:
            self.console.warning(f"Model '{model}' not in config table. Using conservative defaults.")
            self.model_limits = ModelLimits(tpm=30000, rpm=500, tpd=90000, context_window=128000)

        # Calculate max tokens per chunk if not provided
        if max_tokens is None:
            # Use 70% of TPM limit to leave headroom for output tokens
            tpm_based_limit = int(self.model_limits.tpm * 0.7)
            # Don't exceed context window (leave 20K for output)
            context_based_limit = self.model_limits.context_window - 20000
            # Use the smaller of the two
            self.max_tokens = min(tpm_based_limit, context_based_limit)
        else:
            self.max_tokens = max_tokens

        self.console.info(f"Model: {model}")
        self.console.info(f"   TPM Limit: {self.model_limits.tpm:,} | Max chunk size: {self.max_tokens:,} tokens")

    def _estimate_tokens(self, text: str) -> int:
        """Rough token estimation: ~4 chars per token"""
        return len(text) // 4

    def _chunk_transcript(self, transcript_text: str, base_prompt: str) -> List[str]:
        """Split large transcripts into processable chunks.

        ``base_prompt`` is the profile's composed prompt before the language
        and untrusted-content additions; overhead has always been measured
        on it, so chunk boundaries for ``general`` stay where they were.
        """
        overhead_tokens = self._estimate_tokens(base_prompt)
        available_tokens = self.max_tokens - overhead_tokens
        max_chars = available_tokens * 4  # Rough estimation

        if len(transcript_text) <= max_chars:
            return [transcript_text]

        # Split by paragraphs/sentences
        chunks = []
        sentences = transcript_text.split(". ")
        current_chunk_text = ""

        for sentence in sentences:
            if len(current_chunk_text) + len(sentence) > max_chars and current_chunk_text:
                chunks.append(current_chunk_text.strip())
                current_chunk_text = sentence + ". "
            else:
                current_chunk_text += sentence + ". "

        if current_chunk_text:
            chunks.append(current_chunk_text.strip())

        return chunks

    def _get_formatted_system_prompt(
        self, metadata: Optional[EpisodeMetadata] = None, profile: SummaryProfile = GENERAL
    ) -> str:
        """Return the system prompt for ``profile`` (metadata no longer embedded in prompt).

        Appends the untrusted-content preamble defined in
        :mod:`thestill.utils.prompt_safety` so the model knows to treat
        anything inside the sentinels as data rather than instructions.
        """
        language = normalize_language_code(metadata.language if metadata else "en")
        prompt = compose_system_prompt(profile)
        if language != "en":
            language_name = resolve_language_spec(language)["name"]
            prompt = prompt.replace(
                "Use British English.",
                f"Write the entire summary in {language_name}.",
                1,
            )
            prompt += f"""

**Language requirement:** Localise the complete output into {language_name},
including every section header, template label (such as \"What happened\"),
and all prose. Keep the section numbering, emojis, Markdown structure,
timestamps, proper names, and citation syntax unchanged. The English wording
in the template describes the required structure; it is not output language.
"""
        return prompt + UNTRUSTED_CONTENT_PREAMBLE

    def _process_single_chunk(
        self,
        chunk_text: str,
        chunk_num: int,
        total_chunks: int,
        system_prompt: str,
    ) -> str:
        """Process a single transcript chunk"""
        # Wrap transcript content in untrusted sentinels
        # so prompt-injection attempts inside the audio cannot hijack the
        # model into ignoring the operator's instructions.
        header = "TRANSCRIPT:\n\n"
        if total_chunks > 1:
            header += f"[CHUNK {chunk_num}/{total_chunks}]\n\n"
        user_message = header + wrap_untrusted(chunk_text, label="TRANSCRIPT")

        try:
            messages = [
                {"role": "system", "content": system_prompt},
                {"role": "user", "content": user_message},
            ]

            # Use temperature if model supports it
            temperature = 0.3 if self.model_limits.supports_temperature else None

            response = self.provider.chat_completion(messages=messages, temperature=temperature)
            return response

        except Exception as e:
            self.console.error(f"Error processing chunk {chunk_num}/{total_chunks}: {e}")
            raise

    def summarize(
        self,
        transcript_text: str,
        metadata: Optional[EpisodeMetadata] = None,
        profile: SummaryProfile = GENERAL,
    ) -> str:
        """
        Summarize a transcript with comprehensive analysis.

        Returns the markdown text — persisting the result is the caller's
        responsibility, routed through ``FileStorage`` (spec #35) so the
        summary lands on the configured backend (local or S3). Internal
        file I/O was removed to avoid the half-migrated state where
        summaries were written to local disk while ``DigestGenerator``
        read them through ``FileStorage``.

        Args:
            transcript_text: The transcript text (from cleaned transcript markdown)
            metadata: Optional episode metadata (title, pub_date, duration) for accurate summary
            profile: Genre profile that fills the summary's slots (spec #98)

        Returns:
            The summary as markdown text
        """
        estimated_tokens = self._estimate_tokens(transcript_text)

        self.console.info(f"Summarizing transcript with {self.provider.get_model_name()}...")
        self.console.info(f"Estimated tokens: ~{estimated_tokens:,}")
        logger.info(
            "summarizing_episode",
            episode_id=metadata.episode_id if metadata else None,
            summary_profile=profile.key,
            summary_profile_version=profile.version,
        )

        # Format system prompt with metadata
        system_prompt = self._get_formatted_system_prompt(metadata, profile)

        chunks = self._chunk_transcript(transcript_text, compose_system_prompt(profile))

        if len(chunks) > 1:
            self.console.info(f"Large transcript detected. Splitting into {len(chunks)} chunks...")

        try:
            chunk_outputs = []

            for i, chunk in enumerate(chunks, 1):
                if len(chunks) > 1:
                    self.console.progress(f"Processing chunk {i}/{len(chunks)}...")

                output_text = self._process_single_chunk(chunk, i, len(chunks), system_prompt)
                chunk_outputs.append(output_text)

            # Combine results if multiple chunks
            if len(chunk_outputs) == 1:
                final_output = chunk_outputs[0]
            else:
                final_output = "\n\n---\n\n".join(chunk_outputs)

            self.console.success("Summarization completed successfully")
            return final_output

        except Exception as e:
            self.console.error(f"Error during summarization: {e}")
            raise
