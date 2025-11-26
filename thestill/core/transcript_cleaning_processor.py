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
Transcript cleaning processor focused on accuracy and readability.
Acts as a copywriter to fix spelling, grammar, remove filler words, and identify speakers.
"""

import json
import logging
import re
import time
from datetime import datetime
from pathlib import Path
from typing import Dict, List, Optional

from ..models.podcast import TranscriptCleaningMetrics
from ..utils.exceptions import TranscriptCleaningError

logger = logging.getLogger(__name__)

# Threshold for failing the run (if more than this % of chunks fail)
PHASE1_FAILURE_THRESHOLD = 0.5  # 50%

# Threshold for warning about low correction success rate
CORRECTION_SUCCESS_WARNING_THRESHOLD = 0.5  # 50%
CORRECTION_SUCCESS_MIN_CORRECTIONS = 5  # Only warn if at least this many corrections found
from .llm_provider import LLMProvider
from .post_processor import MODEL_CONFIGS
from .transcript_formatter import TranscriptFormatter

# Default max output tokens for unknown models (conservative)
DEFAULT_MAX_OUTPUT_TOKENS = 4096


def get_max_output_tokens(model_name: str) -> int:
    """
    Get the maximum output tokens for a model from MODEL_CONFIGS.

    Args:
        model_name: The model name (e.g., "claude-3-5-sonnet-20241022")

    Returns:
        The max_output_tokens for the model, or DEFAULT_MAX_OUTPUT_TOKENS if unknown
    """
    # Check for exact match first
    if model_name in MODEL_CONFIGS:
        return MODEL_CONFIGS[model_name].max_output_tokens

    # Check for partial match (model names often have date suffixes)
    model_lower = model_name.lower()
    for config_name, limits in MODEL_CONFIGS.items():
        # Match by prefix (e.g., "claude-3-5-sonnet" matches "claude-3-5-sonnet-20241022")
        if model_name.startswith(config_name.rsplit("-", 1)[0]):
            return limits.max_output_tokens
        # Also check if config name starts with model name prefix
        if config_name.startswith(model_name.rsplit("-", 1)[0]):
            return limits.max_output_tokens

    # Fallback for common provider patterns
    if "gemini" in model_lower:
        return 8192  # Gemini default
    elif "gpt-4" in model_lower:
        return 16384  # GPT-4o default
    elif "claude" in model_lower:
        return 8192  # Claude 3.x default (conservative)

    return DEFAULT_MAX_OUTPUT_TOKENS


class TranscriptCleaningProcessor:
    """LLM-based transcript cleaner with copywriting focus"""

    def __init__(self, provider: LLMProvider, chunk_size: Optional[int] = None):
        """
        Initialize transcript cleaning processor with an LLM provider.

        Args:
            provider: LLMProvider instance (OpenAI, Ollama, Gemini, or Anthropic)
            chunk_size: Maximum characters per chunk for processing (optional)
                       If not specified, will be auto-set based on provider:
                       - Gemini 2.0/2.5 Flash: 900K chars (~225K tokens from 1M context)
                       - Claude 3.5 Sonnet: 180K chars (~45K tokens from 200K context)
                       - GPT-4/GPT-4o: 100K chars (~25K tokens from 128K context)
                       - Ollama/Other: 30K chars (conservative default)
        """
        self.provider = provider
        self.formatter = TranscriptFormatter()

        # Auto-set chunk_size based on provider if not specified
        if chunk_size is None:
            model_name = provider.get_model_name().lower()
            if "gemini" in model_name:
                # Gemini 2.0 Flash: 1M input tokens context
                self.chunk_size = 900000
                logger.debug("Auto-set chunk size: 900K chars for Gemini (1M token context)")
            elif "claude" in model_name:
                # Claude 3.5 Sonnet: 200K token context
                self.chunk_size = 180000
                logger.debug("Auto-set chunk size: 180K chars for Claude (200K token context)")
            elif "gpt-4" in model_name or "gpt-5" in model_name:
                # GPT-4/GPT-4o: 128K token context
                self.chunk_size = 100000
                logger.debug("Auto-set chunk size: 100K chars for GPT-4 (128K token context)")
            else:
                # Conservative default for Ollama and other models
                self.chunk_size = 30000
                logger.debug("Auto-set chunk size: 30K chars (conservative default)")
        else:
            self.chunk_size = chunk_size
            logger.debug(f"Using custom chunk size: {chunk_size} chars")

    def clean_transcript(
        self,
        transcript_data: Dict,
        podcast_title: str = "",
        podcast_description: str = "",
        episode_title: str = "",
        episode_description: str = "",
        episode_external_id: str = "",
        episode_id: str = "",
        output_path: Optional[str] = None,
        save_corrections: bool = True,
        save_metrics: bool = True,
    ) -> Dict:
        """
        Clean a transcript with focus on accuracy and readability.

        Args:
            transcript_data: Raw transcript JSON from transcriber
            podcast_title: Title of the podcast
            podcast_description: Description of the podcast
            episode_title: Title of the episode
            episode_description: Description of the episode
            episode_external_id: External identifier (from RSS feed) for the episode
            episode_id: Internal episode ID (UUID) for stable file naming
            output_path: Optional path to save outputs
            save_corrections: Whether to save corrections list for debugging
            save_metrics: Whether to save performance metrics (default: True)

        Returns:
            Dict with keys: corrections, speaker_mapping, cleaned_markdown, processing_time, metrics
        """
        start_time = time.time()

        # Initialize performance tracking
        metrics_data = {
            "episode_external_id": episode_external_id,
            "episode_title": episode_title,
            "podcast_title": podcast_title,
            "llm_provider": self.provider.__class__.__name__,
            "llm_model": self.provider.get_model_name(),
            "chunk_size": self.chunk_size,
            "phase1_llm_calls": 0,
            "phase2_llm_calls": 0,
            "phase3_llm_calls": 0,
        }

        try:
            # Phase 0: Format JSON to clean Markdown (efficient for LLM)
            logger.info("Phase 0: Formatting transcript to clean Markdown...")
            phase0_start = time.time()
            formatted_markdown = self.formatter.format_transcript(transcript_data, episode_title)
            metrics_data["phase0_format_duration_seconds"] = time.time() - phase0_start
            metrics_data["total_transcript_chars"] = len(formatted_markdown)

            # Save formatted markdown to debug folder if requested
            if output_path and save_corrections:
                self._save_phase_output(output_path, "original", formatted_markdown, episode_id)

            # Phase 1: Analyze and create corrections list
            logger.info("Phase 1: Analyzing transcript and identifying corrections...")
            phase1_start = time.time()
            corrections, phase1_chunks_ok, phase1_chunks_failed = self._analyze_and_correct(
                formatted_markdown, podcast_title, podcast_description, episode_title, episode_description
            )
            metrics_data["phase1_analysis_duration_seconds"] = time.time() - phase1_start
            metrics_data["phase1_corrections_found"] = len(corrections)
            metrics_data["phase1_chunks_processed"] = phase1_chunks_ok
            metrics_data["phase1_chunks_failed"] = phase1_chunks_failed
            metrics_data["phase1_llm_calls"] = phase1_chunks_ok + phase1_chunks_failed  # Count all attempts

            # Track degraded status if any chunks failed
            if phase1_chunks_failed > 0:
                metrics_data["run_status"] = "degraded"

            # Save corrections to debug folder if requested
            if output_path and save_corrections:
                self._save_phase_output(output_path, "corrections", corrections, episode_id)

            # Phase 1.5: Apply corrections before speaker identification
            logger.info("Phase 1.5: Applying corrections to improve speaker name accuracy...")
            phase1_5_start = time.time()
            corrected_markdown, applied_count, skipped_corrections = self._apply_corrections(
                formatted_markdown, corrections
            )
            metrics_data["phase1_5_apply_duration_seconds"] = time.time() - phase1_5_start
            metrics_data["phase1_5_corrections_applied"] = applied_count

            # Check correction success rate and warn if low
            if len(corrections) >= CORRECTION_SUCCESS_MIN_CORRECTIONS:
                success_rate = applied_count / len(corrections)
                if success_rate < CORRECTION_SUCCESS_WARNING_THRESHOLD:
                    logger.warning(
                        f"Low correction success rate: {applied_count}/{len(corrections)} "
                        f"({success_rate:.0%}) corrections applied. "
                        f"Some corrections may not have matched the transcript text."
                    )
                    # Mark as degraded if not already failed
                    if metrics_data.get("run_status") == "success":
                        metrics_data["run_status"] = "degraded"

            # Save skipped corrections for debugging if requested
            if output_path and save_corrections and skipped_corrections:
                self._save_phase_output(output_path, "skipped", skipped_corrections, episode_id)

            # Save corrected markdown to debug folder if requested
            if output_path and save_corrections:
                self._save_phase_output(output_path, "corrected", corrected_markdown, episode_id)

            # Phase 2: Identify speakers (using corrected transcript)
            logger.info("Phase 2: Identifying speakers...")
            phase2_start = time.time()
            speaker_mapping = self._identify_speakers(
                corrected_markdown, podcast_title, podcast_description, episode_title, episode_description
            )
            metrics_data["phase2_speaker_duration_seconds"] = time.time() - phase2_start
            metrics_data["phase2_speakers_identified"] = len(speaker_mapping)
            metrics_data["phase2_llm_calls"] = 1  # Speaker identification is always 1 LLM call

            # Save speaker mapping to debug folder if requested
            if output_path and save_corrections:
                self._save_phase_output(output_path, "speakers", speaker_mapping, episode_id)

            # Phase 3: Generate final cleaned transcript (deterministic - no LLM)
            logger.info("Phase 3: Generating final cleaned transcript...")
            phase3_start = time.time()
            cleaned_markdown, phase3_chunks = self._generate_cleaned_transcript(
                corrected_markdown, corrections, speaker_mapping, episode_title
            )
            metrics_data["phase3_generation_duration_seconds"] = time.time() - phase3_start
            metrics_data["phase3_chunks_processed"] = phase3_chunks
            metrics_data["phase3_llm_calls"] = 0  # No LLM calls - deterministic processing

            processing_time = time.time() - start_time
            metrics_data["total_duration_seconds"] = processing_time
            metrics_data["total_chunks_processed"] = phase1_chunks_ok + phase3_chunks
            metrics_data["timestamp"] = datetime.now()

            # Create metrics model
            metrics = TranscriptCleaningMetrics(**metrics_data)

            result = {
                "corrections": corrections,
                "speaker_mapping": speaker_mapping,
                "cleaned_markdown": cleaned_markdown,
                "processing_time": processing_time,
                "episode_title": episode_title,
                "podcast_title": podcast_title,
                "metrics": metrics,
            }

            # Save outputs if path provided
            if output_path:
                self._save_outputs(result, output_path, save_corrections, save_metrics, episode_id)

            # Print performance summary
            logger.info(f"Transcript cleaning completed in {processing_time:.1f} seconds")
            logger.info(
                f"Performance: {metrics.efficiency_metrics['chars_per_second']:.0f} chars/sec, {metrics.total_llm_calls} LLM calls"
            )
            return result

        except Exception as e:
            logger.error(f"Error cleaning transcript: {e}")
            raise

    def _chunk_transcript(self, text: str) -> List[str]:
        """
        Split transcript into chunks that fit within LLM context limits.

        Args:
            text: Full transcript text

        Returns:
            List of text chunks
        """
        if len(text) <= self.chunk_size:
            return [text]

        chunks = []
        lines = text.split("\n")
        current_chunk = []
        current_size = 0

        for line in lines:
            line_size = len(line) + 1  # +1 for newline

            if current_size + line_size > self.chunk_size and current_chunk:
                # Save current chunk and start new one
                chunks.append("\n".join(current_chunk))
                current_chunk = [line]
                current_size = line_size
            else:
                current_chunk.append(line)
                current_size += line_size

        # Add remaining chunk
        if current_chunk:
            chunks.append("\n".join(current_chunk))

        return chunks

    def _analyze_and_correct(
        self,
        formatted_markdown: str,
        podcast_title: str,
        podcast_description: str,
        episode_title: str,
        episode_description: str,
    ) -> tuple[List[Dict], int, int]:
        """
        Phase 1: Analyze transcript and identify all corrections needed.

        Returns:
            Tuple of (corrections list, chunks processed successfully, chunks failed)

        Raises:
            TranscriptCleaningError: If more than PHASE1_FAILURE_THRESHOLD of chunks fail
        """

        # Markdown is already clean and ready for LLM
        transcript_text = formatted_markdown

        system_prompt = """You are an expert copywriter and editor specialising in podcast transcripts.

Your task is to analyze the transcript and identify ALL corrections needed for:
1. Spelling errors (especially technical terms, names, brands)
2. Grammar mistakes
3. Filler words to remove (um, uh, like, you know, etc.) - only when they don't add meaning
4. Punctuation improvements

Context will help you make better corrections:
- Use the podcast/episode titles and descriptions to understand the domain
- Technical podcasts may have jargon that looks wrong but is correct
- Names of people, companies, products should be spelled correctly based on context

CRITICAL: You MUST respond with ONLY valid JSON in the exact format shown below. Do not include any explanatory text before or after the JSON.

JSON Schema:
{
  "type": "object",
  "properties": {
    "corrections": {
      "type": "array",
      "items": {
        "type": "object",
        "properties": {
          "type": {"type": "string", "enum": ["spelling", "grammar", "filler", "punctuation"]},
          "original": {"type": "string"},
          "corrected": {"type": "string"}
        },
        "required": ["type", "original", "corrected"]
      }
    }
  },
  "required": ["corrections"]
}

Example output (respond in exactly this format):
{
  "corrections": [
    {
      "type": "spelling",
      "original": "OpenAi",
      "corrected": "OpenAI"
    },
    {
      "type": "spelling",
      "original": "Alister Campbell",
      "corrected": "Alastair Campbell"
    },
    {
      "type": "filler",
      "original": " um ",
      "corrected": " "
    },
    {
      "type": "grammar",
      "original": "they was going",
      "corrected": "they were going"
    }
  ]
}

If no corrections are needed, return: {"corrections": []}"""

        # Split transcript into chunks if needed
        chunks = self._chunk_transcript(transcript_text)
        all_corrections = []
        chunks_processed = 0
        chunks_failed = 0

        for i, chunk in enumerate(chunks):
            chunk_info = f" (chunk {i+1}/{len(chunks)})" if len(chunks) > 1 else ""
            logger.info(f"Processing{chunk_info}...")

            context_info = f"""PODCAST CONTEXT:
Podcast: {podcast_title}
About: {podcast_description}

Episode: {episode_title}
Description: {episode_description}

TRANSCRIPT TO ANALYZE{chunk_info}:
{chunk}"""

            try:
                messages = [{"role": "system", "content": system_prompt}, {"role": "user", "content": context_info}]

                # Get max_tokens from MODEL_CONFIGS for the specific model
                provider_max_tokens = get_max_output_tokens(self.provider.get_model_name())

                # Use streaming if available (Anthropic, OpenAI)
                if hasattr(self.provider, "chat_completion_streaming"):
                    logger.debug(f"Streaming from LLM ({len(chunk):,} chars input)...")

                    # Track progress with streaming (silent for max speed)
                    chars_received = [0]  # Use list to allow mutation in nested function

                    def on_chunk(chunk_text: str):
                        """Callback for streaming chunks - accumulate silently for max speed"""
                        chars_received[0] += len(chunk_text)
                        # No progress printing - console I/O slows down streaming significantly

                    response = self.provider.chat_completion_streaming(
                        messages=messages,
                        temperature=0.1,
                        max_tokens=provider_max_tokens,
                        response_format={"type": "json_object"},
                        on_chunk=on_chunk,
                    )

                    logger.debug(f"Streaming complete ({chars_received[0]:,} chars received)")

                else:
                    # Fallback to non-streaming for providers that don't support it
                    logger.debug(f"Sending to LLM ({len(chunk):,} chars input)...")
                    response = self.provider.chat_completion(
                        messages=messages,
                        temperature=0.1,
                        max_tokens=provider_max_tokens,
                        response_format={"type": "json_object"},
                    )
                    logger.debug("LLM responded")

                # Parse JSON response
                response = response.strip()
                if "```json" in response:
                    start = response.find("```json") + 7
                    end = response.find("```", start)
                    if end != -1:
                        response = response[start:end].strip()
                elif "```" in response:
                    start = response.find("```") + 3
                    end = response.find("```", start)
                    if end != -1:
                        response = response[start:end].strip()

                result = json.loads(response)
                chunk_corrections = result.get("corrections", [])
                all_corrections.extend(chunk_corrections)
                chunks_processed += 1

            except json.JSONDecodeError as e:
                chunks_failed += 1
                logger.error(f"JSON parsing error in chunk {i+1}/{len(chunks)}: {e}")
                logger.debug(f"Malformed response (first 500 chars): {response[:500] if response else 'empty'}")
            except Exception as e:
                chunks_failed += 1
                logger.error(f"Error analyzing chunk {i+1}/{len(chunks)}: {e}")

        # Check failure rate and raise if too many chunks failed
        total_chunks = len(chunks)
        if total_chunks > 0:
            failure_rate = chunks_failed / total_chunks
            if failure_rate > PHASE1_FAILURE_THRESHOLD:
                raise TranscriptCleaningError(
                    f"Phase 1 failed: {chunks_failed}/{total_chunks} chunks failed to process "
                    f"({failure_rate:.0%} failure rate exceeds {PHASE1_FAILURE_THRESHOLD:.0%} threshold)",
                    chunks_failed=chunks_failed,
                    chunks_total=total_chunks,
                    failure_rate=failure_rate,
                )
            elif chunks_failed > 0:
                logger.warning(
                    f"Phase 1 degraded: {chunks_failed}/{total_chunks} chunks failed "
                    f"({failure_rate:.0%} failure rate)"
                )

        logger.info(f"Found {len(all_corrections)} corrections across {chunks_processed} chunk(s)")
        return all_corrections, chunks_processed, chunks_failed

    def _apply_corrections(self, transcript_text: str, corrections: List[Dict]) -> tuple[str, int, List[Dict]]:
        """
        Apply corrections from Phase 1 to the transcript text.
        This ensures speaker names are properly spelled before speaker identification.

        Args:
            transcript_text: Original transcript markdown
            corrections: List of correction objects from Phase 1

        Returns:
            Tuple of (corrected transcript text, number of corrections applied, skipped corrections)
        """
        corrected_text = transcript_text

        # Sort corrections by type priority - spelling first for proper names
        priority_order = {"spelling": 1, "grammar": 2, "punctuation": 3, "filler": 4}
        sorted_corrections = sorted(corrections, key=lambda c: priority_order.get(c.get("type", ""), 99))

        applied_count = 0
        skipped_corrections: List[Dict] = []

        for correction in sorted_corrections:
            original = correction.get("original", "")
            corrected = correction.get("corrected", "")
            correction_type = correction.get("type", "")

            if not original:
                skipped_corrections.append({**correction, "skip_reason": "empty_original"})
                continue

            # Build regex pattern based on correction type
            escaped = re.escape(original)

            # Determine if we need word boundary checks
            # Only apply lookarounds when the pattern starts/ends with alphanumeric
            # This prevents "La" from matching in "Language" while allowing punctuation-adjacent matches
            starts_with_alpha = original[0].isalpha() if original else False
            ends_with_alpha = original[-1].isalpha() if original else False

            # Build pattern with appropriate boundaries
            # (?<![A-Za-z]) = not preceded by a letter
            # (?![A-Za-z]) = not followed by a letter
            if starts_with_alpha and ends_with_alpha:
                # Word-like pattern: use lookarounds on both sides
                pattern = rf"(?<![A-Za-z]){escaped}(?![A-Za-z])"
            elif starts_with_alpha:
                # Starts with letter but ends with punctuation/space
                pattern = rf"(?<![A-Za-z]){escaped}"
            elif ends_with_alpha:
                # Ends with letter but starts with punctuation/space
                pattern = rf"{escaped}(?![A-Za-z])"
            else:
                # No alphanumeric boundaries (e.g., ", um," or punctuation-only)
                pattern = escaped

            # Use case-insensitive matching for fillers (um, uh, like, you know)
            # Fillers can appear as "Um", "um", "UM" etc.
            flags = re.IGNORECASE if correction_type == "filler" else 0

            new_text, count = re.subn(pattern, corrected, corrected_text, flags=flags)
            if count > 0:
                corrected_text = new_text
                applied_count += 1
            else:
                skipped_corrections.append({**correction, "skip_reason": "no_match"})

        if skipped_corrections:
            logger.debug(f"Skipped {len(skipped_corrections)} corrections that didn't match transcript")

        logger.info(f"Applied {applied_count} corrections to transcript")
        return corrected_text, applied_count, skipped_corrections

    def _apply_speaker_mapping(self, transcript: str, speaker_mapping: Dict[str, str]) -> str:
        """
        Replace speaker placeholders with actual names.

        Args:
            transcript: Markdown transcript with `**SPEAKER_XX:**` placeholders
            speaker_mapping: Dict like {"SPEAKER_00": "Scott Galloway", ...}

        Returns:
            Transcript with speaker names replaced
        """
        if not transcript:
            return ""

        if not speaker_mapping:
            return transcript

        result = transcript
        for placeholder, real_name in speaker_mapping.items():
            if not real_name:
                continue
            escaped_placeholder = re.escape(placeholder)
            pattern = rf"\*\*{escaped_placeholder}:\*\*"
            replacement = f"**{real_name}:**"
            result = re.sub(pattern, replacement, result)

        return result

    def _identify_speakers(
        self,
        formatted_markdown: str,
        podcast_title: str,
        podcast_description: str,
        episode_title: str,
        episode_description: str,
    ) -> Dict[str, str]:
        """Phase 2: Identify who the speakers are"""

        transcript_text = formatted_markdown

        system_prompt = """You are an expert at identifying speakers in podcast transcripts.

Your task is to deduce the real names of speakers from:
1. Self-introductions in the transcript
2. How other speakers address them
3. Context from the podcast/episode titles and descriptions
4. Host information typically in podcast description

The transcript has speaker labels like SPEAKER_00, SPEAKER_01, etc.
Map each speaker label to their real name.

IMPORTANT: Respond with valid JSON only. Return an object mapping speaker labels to real names.

If you cannot identify a speaker with confidence, use:
- "Host" for the main podcast host
- "Guest" for guests
- "Co-host" for additional hosts
- Keep the SPEAKER_XX label if completely unknown

Example:
{
  "speaker_mapping": {
    "SPEAKER_00": "Scott Galloway",
    "SPEAKER_01": "Greg Shove",
    "SPEAKER_02": "Ad Narrator"
  }
}"""

        # For speaker identification, use first and last chunk only (where introductions typically happen)
        chunks = self._chunk_transcript(transcript_text)
        if len(chunks) > 2:
            # Use first and last chunk
            sample_text = chunks[0] + "\n\n[... middle content omitted ...]\n\n" + chunks[-1]
            logger.debug(f"Using first and last chunk of {len(chunks)} chunks for speaker identification")
        elif len(chunks) == 2:
            sample_text = chunks[0] + "\n\n" + chunks[1]
        else:
            sample_text = transcript_text

        context_info = f"""PODCAST CONTEXT:
Podcast: {podcast_title}
About: {podcast_description}

Episode: {episode_title}
Description: {episode_description}

TRANSCRIPT:
{sample_text}"""

        try:
            messages = [{"role": "system", "content": system_prompt}, {"role": "user", "content": context_info}]

            response = self.provider.chat_completion(
                messages=messages,
                temperature=0.1,
                max_tokens=2000,  # Increased for Gemini's larger output capacity
                response_format={"type": "json_object"},
            )

            # Parse JSON response
            response = response.strip()
            if "```json" in response:
                start = response.find("```json") + 7
                end = response.find("```", start)
                if end != -1:
                    response = response[start:end].strip()
            elif "```" in response:
                start = response.find("```") + 3
                end = response.find("```", start)
                if end != -1:
                    response = response[start:end].strip()

            result = json.loads(response)
            return result.get("speaker_mapping", {})

        except Exception as e:
            logger.error(f"Error identifying speakers: {e}")
            return {}

    def _generate_cleaned_transcript(
        self, formatted_markdown: str, corrections: List[Dict], speaker_mapping: Dict[str, str], episode_title: str
    ) -> tuple[str, int]:
        """
        Phase 3: Generate final cleaned transcript using deterministic operations.

        Replaces speaker placeholders with real names.
        Corrections have already been applied in Phase 1.5.

        Args:
            formatted_markdown: Transcript with corrections applied (from Phase 1.5)
            corrections: Original corrections list (unused, kept for API compatibility)
            speaker_mapping: Speaker ID to name mapping from Phase 2
            episode_title: Episode title (unused, kept for API compatibility)

        Returns:
            Tuple of (cleaned transcript, chunks processed - always 0 for deterministic)
        """
        # Apply speaker name replacements
        result = self._apply_speaker_mapping(formatted_markdown, speaker_mapping)

        return result, 0  # 0 chunks processed (no LLM chunking needed)

    def _save_phase_output(self, output_path: str, phase: str, data, episode_id: str = ""):
        """
        Save output from a specific phase immediately after completion.

        Args:
            output_path: Base output path (e.g., data/clean_transcripts/Podcast_Episode_hash_cleaned.md)
            phase: Phase name (original, corrections, corrected, speakers, skipped)
            data: Data to save (list, dict, or string)
            episode_id: Internal episode ID (UUID, unused but kept for API compatibility)
        """
        output_path = Path(output_path)

        # Create debug directory inside clean_transcripts for intermediate files
        debug_dir = output_path.parent / "debug"
        debug_dir.mkdir(parents=True, exist_ok=True)

        # Extract base name from output_path: Podcast_Episode_hash_cleaned.md -> Podcast_Episode_hash
        base_name = output_path.stem.replace("_cleaned", "")

        if phase == "original":
            # Save to debug directory: {base_name}.original.md
            path = debug_dir / f"{base_name}.original.md"
            with open(path, "w", encoding="utf-8") as f:
                f.write(data)
            logger.debug(f"Original transcript saved to: {path}")

        elif phase == "corrections":
            # Save to debug directory: {base_name}.corrections.json
            path = debug_dir / f"{base_name}.corrections.json"
            with open(path, "w", encoding="utf-8") as f:
                json.dump(data, f, indent=2, ensure_ascii=False)
            logger.debug(f"Corrections saved to: {path}")

        elif phase == "corrected":
            # Save to debug directory: {base_name}.corrected.md
            path = debug_dir / f"{base_name}.corrected.md"
            with open(path, "w", encoding="utf-8") as f:
                f.write(data)
            logger.debug(f"Corrected transcript saved to: {path}")

        elif phase == "speakers":
            # Save to debug directory: {base_name}.speakers.json
            path = debug_dir / f"{base_name}.speakers.json"
            with open(path, "w", encoding="utf-8") as f:
                json.dump(data, f, indent=2, ensure_ascii=False)
            logger.debug(f"Speaker mapping saved to: {path}")

        elif phase == "skipped":
            # Save to debug directory: {base_name}.skipped.json
            path = debug_dir / f"{base_name}.skipped.json"
            with open(path, "w", encoding="utf-8") as f:
                json.dump(data, f, indent=2, ensure_ascii=False)
            logger.debug(f"Skipped corrections saved to: {path}")

    def _save_outputs(
        self, result: Dict, output_path: str, save_corrections: bool, save_metrics: bool = True, episode_id: str = ""
    ):
        """
        Save final outputs to standard locations.

        File structure:
        - data/clean_transcripts/{base_name}_cleaned.md - Final cleaned transcript (main output)
        - data/clean_transcripts/debug/{base_name}.metrics.json - Performance metrics
        - data/clean_transcripts/debug/{base_name}.corrections.json - Debug: corrections list
        - data/clean_transcripts/debug/{base_name}.speakers.json - Debug: speaker mapping
        - data/clean_transcripts/debug/{base_name}.corrected.md - Debug: pre-speaker-formatting text

        Args:
            result: Cleaned transcript result dictionary
            output_path: Full path to output file (e.g., Podcast_Episode_hash_cleaned.md)
            save_corrections: Whether to save debug files
            save_metrics: Whether to save performance metrics
            episode_id: Internal episode UUID (used for debug files)
        """
        output_path = Path(output_path)
        output_path.parent.mkdir(parents=True, exist_ok=True)

        # Extract base name from output_path for debug files
        # e.g., "Podcast_Episode_hash_cleaned.md" -> "Podcast_Episode_hash"
        base_name = output_path.stem.replace("_cleaned", "")

        # Save final cleaned markdown using the provided output_path
        # Note: cleaned_markdown already contains header from TranscriptFormatter
        final_path = output_path
        with open(final_path, "w", encoding="utf-8") as f:
            f.write(result["cleaned_markdown"])
        logger.info(f"Final transcript saved to: {final_path}")

        # Save performance metrics to debug folder: debug/{base_name}.metrics.json
        if save_metrics and "metrics" in result:
            debug_dir = output_path.parent / "debug"
            debug_dir.mkdir(parents=True, exist_ok=True)
            metrics_path = debug_dir / f"{base_name}.metrics.json"
            metrics_dict = result["metrics"].model_dump(mode="json")
            # Add computed properties
            metrics_dict["phase_breakdown_percent"] = result["metrics"].phase_breakdown_percent
            metrics_dict["efficiency_metrics"] = result["metrics"].efficiency_metrics
            with open(metrics_path, "w", encoding="utf-8") as f:
                json.dump(metrics_dict, f, indent=2, ensure_ascii=False, default=str)
            logger.debug(f"Performance metrics saved to: {metrics_path}")
