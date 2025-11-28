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
from typing import Any, Dict, List, Optional

from ..models.podcast import TranscriptCleaningMetrics
from ..utils.exceptions import TranscriptCleaningError

logger = logging.getLogger(__name__)

# Threshold for failing the run (if more than this % of chunks fail)
PHASE1_FAILURE_THRESHOLD = 0.5  # 50%

# Threshold for warning about low correction success rate
CORRECTION_SUCCESS_WARNING_THRESHOLD = 0.5  # 50%
CORRECTION_SUCCESS_MIN_CORRECTIONS = 5  # Only warn if at least this many corrections found

# Diarization validation settings
DIARIZATION_VALIDATION_ENABLED_DEFAULT = True  # Enable by default
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

    def __init__(
        self,
        provider: LLMProvider,
        chunk_size: Optional[int] = None,
        validate_diarization: bool = DIARIZATION_VALIDATION_ENABLED_DEFAULT,
    ):
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
            validate_diarization: Whether to run LLM-based diarization validation (default: True)
                                 This detects and fixes speaker assignment errors like:
                                 - Ad segments mixed with host speech
                                 - Speaker identity drift
                                 - Multiple people assigned same speaker ID
        """
        self.provider = provider
        self.formatter = TranscriptFormatter()
        self.validate_diarization = validate_diarization

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

        prompt_logs: Optional[List[Dict[str, Any]]] = [] if output_path and save_corrections else None

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
            # Phase 0.5: Validate and fix diarization errors (on raw JSON)
            if self.validate_diarization:
                logger.info("Phase 0.5: Validating speaker diarization...")
                phase0_5_start = time.time()
                transcript_data, diarization_fixes = self._validate_and_fix_diarization(
                    transcript_data,
                    podcast_title,
                    podcast_description,
                    episode_title,
                    episode_description,
                    prompt_logs=prompt_logs,
                )
                metrics_data["phase0_5_diarization_duration_seconds"] = time.time() - phase0_5_start
                metrics_data["phase0_5_diarization_fixes"] = len(diarization_fixes)

                # Save diarization fixes to debug folder if requested
                if output_path and save_corrections and diarization_fixes:
                    self._save_phase_output(output_path, "diarization_fixes", diarization_fixes, episode_id)

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
                formatted_markdown,
                podcast_title,
                podcast_description,
                episode_title,
                episode_description,
                prompt_logs=prompt_logs,
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
                corrected_markdown,
                podcast_title,
                podcast_description,
                episode_title,
                episode_description,
                prompt_logs=prompt_logs,
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
                if save_corrections and prompt_logs:
                    self._save_prompts(output_path, prompt_logs, episode_id)
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

    def clean_transcript_v2(
        self,
        transcript_data: Dict,
        podcast_title: str = "",
        podcast_description: str = "",
        episode_title: str = "",
        episode_description: str = "",
        episode_id: str = "",
        output_path: Optional[str] = None,
        path_manager: Optional[Any] = None,
        save_prompts: bool = True,
        on_stream_chunk: Optional[callable] = None,
    ) -> Dict:
        """
        Clean transcript using the two-pass facts-based approach (v2).

        This method uses:
        - Pass 1: Facts extraction (speaker mapping, guests, keywords, ad sponsors)
        - Pass 2: Transcript cleanup using extracted facts

        Facts are stored as human-editable Markdown files:
        - Podcast facts: data/podcast_facts/{slug}.facts.md
        - Episode facts: data/episode_facts/{episode_id}.facts.md

        Debug artifacts (when output_path provided):
        - data/clean_transcripts/debug/{base_name}.original.md - Formatted transcript before LLM cleaning
        - data/clean_transcripts/debug/{base_name}.speakers.json - Speaker mapping from episode facts
        - data/clean_transcripts/debug/prompts/{base_name}.prompt_*.md - LLM prompts (if save_prompts=True)

        Args:
            transcript_data: Raw transcript JSON from transcriber
            podcast_title: Title of the podcast
            podcast_description: Description of the podcast
            episode_title: Title of the episode
            episode_description: Description of the episode
            episode_id: Internal episode ID (UUID) for facts file naming
            output_path: Optional path to save final cleaned transcript
            path_manager: PathManager instance for facts file paths (required)
            save_prompts: Whether to save prompts to debug folder (default: True)
            on_stream_chunk: Optional callback for streaming LLM output chunks

        Returns:
            Dict with keys: cleaned_markdown, podcast_facts, episode_facts, processing_time
        """
        import time
        from pathlib import Path

        from thestill.core.facts_extractor import FactsExtractor
        from thestill.core.facts_manager import FactsManager, slugify
        from thestill.core.transcript_cleaner_v2 import TranscriptCleanerV2

        if path_manager is None:
            raise ValueError("path_manager is required for clean_transcript_v2")

        start_time = time.time()

        # Create prompt save callback if saving is enabled
        prompt_save_callback = None
        if output_path and save_prompts:
            prompt_save_callback = self._create_prompt_save_callback(output_path)

        # Initialize components
        facts_manager = FactsManager(path_manager)
        facts_extractor = FactsExtractor(self.provider, chunk_size=self.chunk_size)
        transcript_cleaner = TranscriptCleanerV2(
            self.provider,
            chunk_size=self.chunk_size,
            on_stream_chunk=on_stream_chunk,
        )

        # Get podcast slug for facts lookup
        podcast_slug = slugify(podcast_title) if podcast_title else "unknown-podcast"

        # Load existing facts
        podcast_facts = facts_manager.load_podcast_facts(podcast_slug)
        episode_facts = facts_manager.load_episode_facts(episode_id) if episode_id else None

        # Pass 1: Extract facts if not already present
        if not episode_facts:
            logger.info("Pass 1: Extracting episode facts...")
            episode_facts = facts_extractor.extract_episode_facts(
                transcript_data=transcript_data,
                podcast_title=podcast_title,
                podcast_description=podcast_description,
                episode_title=episode_title,
                episode_description=episode_description,
                podcast_facts=podcast_facts,
            )
            # Save episode facts
            if episode_id:
                facts_manager.save_episode_facts(episode_id, episode_facts)
                logger.info(f"Saved episode facts: {facts_manager.get_episode_facts_path(episode_id)}")

        # Initialize podcast facts if first episode
        if not podcast_facts:
            logger.info("Pass 1: Extracting initial podcast facts...")
            podcast_facts = facts_extractor.extract_initial_podcast_facts(
                transcript_data=transcript_data,
                podcast_title=podcast_title,
                podcast_description=podcast_description,
                episode_facts=episode_facts,
            )
            # Save podcast facts
            facts_manager.save_podcast_facts(podcast_slug, podcast_facts)
            logger.info(f"Saved podcast facts: {facts_manager.get_podcast_facts_path(podcast_slug)}")

        # Format JSON to markdown (much smaller than raw JSON for LLM)
        logger.info("Formatting transcript JSON to markdown...")
        formatted_markdown = self.formatter.format_transcript(transcript_data, episode_title)

        # Save debug artifacts if output_path provided
        if output_path:
            # Save original formatted markdown (before LLM cleaning)
            self._save_phase_output(output_path, "original", formatted_markdown, episode_id)

            # Save speaker mapping from episode facts
            if episode_facts and episode_facts.speaker_mapping:
                self._save_phase_output(output_path, "speakers", episode_facts.speaker_mapping, episode_id)

        # Pass 2: Clean transcript using facts
        logger.info("Pass 2: Cleaning transcript with facts context...")
        cleaned_markdown = transcript_cleaner.clean_transcript(
            formatted_markdown=formatted_markdown,
            podcast_facts=podcast_facts,
            episode_facts=episode_facts,
            episode_title=episode_title,
            on_prompt_ready=prompt_save_callback,
        )

        processing_time = time.time() - start_time

        # Save output if path provided
        if output_path:
            output_file = Path(output_path)
            output_file.parent.mkdir(parents=True, exist_ok=True)
            output_file.write_text(cleaned_markdown, encoding="utf-8")
            logger.info(f"Saved cleaned transcript: {output_path}")

        logger.info(f"Transcript cleaning (v2) completed in {processing_time:.1f} seconds")

        return {
            "cleaned_markdown": cleaned_markdown,
            "podcast_facts": podcast_facts,
            "episode_facts": episode_facts,
            "processing_time": processing_time,
        }

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

    def _validate_and_fix_diarization(
        self,
        transcript_data: Dict,
        podcast_title: str,
        podcast_description: str,
        episode_title: str,
        episode_description: str,
        prompt_logs: Optional[List[Dict[str, Any]]] = None,
    ) -> tuple[Dict, List[Dict]]:
        """
        Phase 0.5: Validate and fix speaker diarization errors using LLM.

        This phase detects and corrects common diarization problems:
        1. Ad segments incorrectly assigned to host speakers
        2. Speaker identity drift (same person split across multiple IDs)
        3. Speaker merging (different people assigned same speaker ID)

        The LLM analyzes segment content to identify which segments are ads vs main content,
        and suggests speaker reassignments based on content patterns.

        Args:
            transcript_data: Raw transcript JSON with segments
            podcast_title: Title of the podcast
            podcast_description: Description of the podcast
            episode_title: Title of the episode
            episode_description: Description of the episode
            prompt_logs: Optional list to capture prompts for debugging

        Returns:
            Tuple of (corrected transcript_data, list of fixes applied)
        """
        segments = transcript_data.get("segments", [])
        if not segments:
            return transcript_data, []

        # Build a summary of segments for the LLM
        segment_summary = self._build_segment_summary_for_diarization(segments)

        system_prompt = """You are an expert at analyzing podcast transcripts to detect speaker diarization errors.

Your task is to analyze the transcript segments and identify diarization problems:

1. **Ad segments**: Identify segments that are clearly advertisements (sponsor reads, product promotions).
   - Look for phrases like "support for the show comes from", "brought to you by", product names/URLs
   - Ads should be assigned to a separate speaker (e.g., "AD_NARRATOR") unless read by the host

2. **Speaker identity drift**: Detect when the same person appears under multiple speaker IDs
   - The host's voice shouldn't suddenly become a different speaker mid-sentence
   - Look for content continuity that suggests same person

3. **Speaker merging**: Detect when different people are incorrectly assigned the same speaker ID
   - A guest expert shouldn't share a speaker ID with an ad narrator
   - Look for dramatic content shifts within the same speaker

4. **CRITICAL: Mid-sentence speaker misattribution**: Detect when a response or continuation is wrongly attributed
   - When someone says "I'm gonna do you the service of..." or similar response phrases, check if it makes sense for the current speaker
   - Look for conversational turn-taking cues: "Yeah", "Right", "No", "So", "Well" at the start of a segment
   - If the segment sounds like a response to the previous speaker, it may be misattributed
   - Pay attention to context: if Host A asks a question, the answer is likely from Host B or Guest, not Host A continuing
   - Short interjections ("Right", "Exactly", "Yeah") are often misattributed to the previous speaker

IMPORTANT: Respond with valid JSON only.

JSON Schema:
{
  "analysis": {
    "ad_segments": [
      {"segment_indices": [0, 1, 2], "reason": "Sponsor read for Blueair", "suggested_speaker": "AD_NARRATOR"}
    ],
    "speaker_drift": [
      {"from_speaker": "SPEAKER_01", "to_speaker": "SPEAKER_03", "segment_index": 33, "reason": "Same guest continues speaking"}
    ],
    "speaker_merge": [
      {"speaker": "SPEAKER_03", "issue": "Contains both ad narrator and guest Michael Cembalest", "fix": "Split at segment 33"}
    ],
    "misattributed_responses": [
      {"segment_index": 15, "issue": "Response 'I'm gonna do you the service...' wrongly attributed to SPEAKER_01 who just asked a question", "suggested_speaker": "SPEAKER_04"}
    ]
  },
  "fixes": [
    {"segment_index": 0, "old_speaker": "SPEAKER_01", "new_speaker": "AD_NARRATOR", "reason": "Blueair ad read"},
    {"segment_index": 15, "old_speaker": "SPEAKER_01", "new_speaker": "SPEAKER_04", "reason": "Response to question should be different speaker"},
    {"segment_index": 33, "old_speaker": "SPEAKER_03", "new_speaker": "GUEST_MICHAEL_CEMBALEST", "reason": "Guest interview begins"}
  ]
}

If no fixes are needed, return: {"analysis": {}, "fixes": []}"""

        context_info = f"""PODCAST CONTEXT:
Podcast: {podcast_title}
About: {podcast_description}

Episode: {episode_title}
Description: {episode_description}

TRANSCRIPT SEGMENTS TO ANALYZE:
{segment_summary}

Analyze the segments above and identify any diarization errors that need fixing."""

        try:
            messages = [{"role": "system", "content": system_prompt}, {"role": "user", "content": context_info}]

            if prompt_logs is not None:
                prompt_logs.append(
                    {
                        "phase": "diarization_validation",
                        "messages": messages,
                        "temperature": 0.1,
                        "max_tokens": 4000,
                        "segments_analyzed": len(segments),
                    }
                )

            response = self.provider.chat_completion(
                messages=messages,
                temperature=0.1,
                max_tokens=4000,
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
            fixes = result.get("fixes", [])

            if not fixes:
                logger.info("Diarization validation: No fixes needed")
                return transcript_data, []

            # Apply fixes to transcript_data
            corrected_data = self._apply_diarization_fixes(transcript_data, fixes)
            logger.info(f"Diarization validation: Applied {len(fixes)} speaker reassignments")

            return corrected_data, fixes

        except json.JSONDecodeError as e:
            logger.warning(f"Diarization validation JSON error: {e}. Skipping fixes.")
            return transcript_data, []
        except Exception as e:
            logger.warning(f"Diarization validation error: {e}. Skipping fixes.")
            return transcript_data, []

    def _build_segment_summary_for_diarization(self, segments: List[Dict], max_segments: int = 100) -> str:
        """
        Build a condensed summary of segments for diarization analysis.

        For long transcripts, we sample strategically:
        - First 20 segments (usually contains ads)
        - Middle 20 segments (main content)
        - Last 20 segments (often contains ads/outro)
        - Any segments with speaker changes

        Args:
            segments: List of transcript segments
            max_segments: Maximum segments to include in summary

        Returns:
            Formatted string summary of segments
        """
        if len(segments) <= max_segments:
            selected_indices = list(range(len(segments)))
        else:
            # Strategic sampling
            selected_indices = set()

            # First 25 segments (ads often at start)
            selected_indices.update(range(min(25, len(segments))))

            # Last 25 segments (ads often at end)
            selected_indices.update(range(max(0, len(segments) - 25), len(segments)))

            # Middle section
            mid = len(segments) // 2
            selected_indices.update(range(max(0, mid - 12), min(len(segments), mid + 13)))

            # Speaker change points (important for detecting drift)
            prev_speaker = None
            for i, seg in enumerate(segments):
                speaker = seg.get("speaker")
                if speaker != prev_speaker:
                    selected_indices.add(i)
                    if i > 0:
                        selected_indices.add(i - 1)
                    if i < len(segments) - 1:
                        selected_indices.add(i + 1)
                prev_speaker = speaker

            selected_indices = sorted(selected_indices)[:max_segments]

        lines = []
        for i in selected_indices:
            seg = segments[i]
            speaker = seg.get("speaker", "UNKNOWN")
            start = seg.get("start", 0)
            text = seg.get("text", "")[:150]  # Truncate long text
            if len(seg.get("text", "")) > 150:
                text += "..."
            lines.append(f"[{i}] t={start:.0f}s {speaker}: {text}")

        return "\n".join(lines)

    def _apply_diarization_fixes(self, transcript_data: Dict, fixes: List[Dict]) -> Dict:
        """
        Apply diarization fixes to transcript data.

        Args:
            transcript_data: Original transcript data
            fixes: List of fixes from LLM analysis

        Returns:
            Corrected transcript data (deep copy with fixes applied)
        """
        import copy

        corrected = copy.deepcopy(transcript_data)
        segments = corrected.get("segments", [])

        for fix in fixes:
            segment_index = fix.get("segment_index")
            new_speaker = fix.get("new_speaker")

            if segment_index is not None and new_speaker and 0 <= segment_index < len(segments):
                old_speaker = segments[segment_index].get("speaker")
                segments[segment_index]["speaker"] = new_speaker
                logger.debug(f"Fixed segment {segment_index}: {old_speaker} -> {new_speaker}")

        return corrected

    def _analyze_and_correct(
        self,
        formatted_markdown: str,
        podcast_title: str,
        podcast_description: str,
        episode_title: str,
        episode_description: str,
        prompt_logs: Optional[List[Dict[str, Any]]] = None,
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
5. **CRITICAL: Homophone and mishearing errors** - Speech-to-text often produces wrong words that sound similar:
   - "know" vs "No" (especially at sentence starts or as responses)
   - "clear" when someone says a name like "Claire" or "Clare"
   - "there/their/they're", "your/you're", "its/it's"
   - Common names misheard as regular words (e.g., "mark" → "Marc", "will" → "Will")
   - Pay special attention when context suggests a person's name (e.g., "Thanks, clear" → "Thanks, Claire")

Context will help you make better corrections:
- Use the podcast/episode titles and descriptions to understand the domain
- Technical podcasts may have jargon that looks wrong but is correct
- Names of people, companies, products should be spelled correctly based on context
- **Production staff**: Podcasts often mention producers, editors, and crew by first name
- **Common podcast roles**: "Senior Producer", "Executive Producer", "Editor" - if these roles are mentioned, the word before is likely a person's name

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
          "type": {"type": "string", "enum": ["spelling", "grammar", "filler", "punctuation", "homophone"]},
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
      "type": "homophone",
      "original": "know?",
      "corrected": "No?"
    },
    {
      "type": "homophone",
      "original": "clear is the Senior Producer",
      "corrected": "Claire is the Senior Producer"
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

                if prompt_logs is not None:
                    prompt_logs.append(
                        {
                            "phase": "analysis",
                            "chunk": i + 1,
                            "total_chunks": len(chunks),
                            "messages": messages,
                            "temperature": 0.1,
                            "max_tokens": provider_max_tokens,
                        }
                    )

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
        prompt_logs: Optional[List[Dict[str, Any]]] = None,
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

        # For speaker identification, sample multiple windows for better coverage
        # - First chunk: Often contains introductions
        # - Middle chunk: May reveal speaker patterns not seen at start/end
        # - Last chunk: Often contains sign-offs and name mentions
        chunks = self._chunk_transcript(transcript_text)
        if len(chunks) >= 3:
            # Use first, middle, and last chunk
            middle_idx = len(chunks) // 2
            sample_text = (
                chunks[0]
                + "\n\n[... content omitted ...]\n\n"
                + chunks[middle_idx]
                + "\n\n[... content omitted ...]\n\n"
                + chunks[-1]
            )
            logger.debug(
                f"Using first, middle (chunk {middle_idx + 1}), and last of {len(chunks)} chunks for speaker identification"
            )
        elif len(chunks) == 2:
            sample_text = chunks[0] + "\n\n" + chunks[1]
            logger.debug("Using both chunks for speaker identification")
        else:
            sample_text = transcript_text
            logger.debug("Using full transcript for speaker identification (single chunk)")

        context_info = f"""PODCAST CONTEXT:
Podcast: {podcast_title}
About: {podcast_description}

Episode: {episode_title}
Description: {episode_description}

TRANSCRIPT:
{sample_text}"""

        try:
            messages = [{"role": "system", "content": system_prompt}, {"role": "user", "content": context_info}]

            if prompt_logs is not None:
                prompt_logs.append(
                    {
                        "phase": "speaker_identification",
                        "messages": messages,
                        "temperature": 0.1,
                        "max_tokens": 2000,
                        "sampled_chunks": len(chunks),
                    }
                )

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
            speaker_mapping = result.get("speaker_mapping", {})

            # Validate speaker mapping - guard against degenerate mappings
            speaker_mapping = self._validate_speaker_mapping(speaker_mapping)
            return speaker_mapping

        except Exception as e:
            logger.error(f"Error identifying speakers: {e}")
            return {}

    def _validate_speaker_mapping(self, speaker_mapping: Dict[str, str]) -> Dict[str, str]:
        """
        Validate and sanitize speaker mapping to prevent degenerate results.

        Guards against:
        - All speakers mapped to the same name (e.g., all "Host")
        - Empty or whitespace-only names

        Args:
            speaker_mapping: Raw speaker mapping from LLM

        Returns:
            Validated speaker mapping (may be empty if degenerate)
        """
        if not speaker_mapping:
            return {}

        # Filter out empty/whitespace names
        cleaned_mapping = {k: v.strip() for k, v in speaker_mapping.items() if v and v.strip()}

        if len(cleaned_mapping) < 2:
            # Single speaker or empty - nothing to validate
            return cleaned_mapping

        # Check for degenerate mapping: all speakers mapped to same name
        unique_names = set(cleaned_mapping.values())
        if len(unique_names) == 1:
            single_name = next(iter(unique_names))
            logger.warning(
                f"Degenerate speaker mapping detected: all {len(cleaned_mapping)} speakers "
                f"mapped to '{single_name}'. Returning empty mapping to preserve SPEAKER_XX labels."
            )
            return {}

        return cleaned_mapping

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

        elif phase == "diarization_fixes":
            # Save to debug directory: {base_name}.diarization_fixes.json
            path = debug_dir / f"{base_name}.diarization_fixes.json"
            with open(path, "w", encoding="utf-8") as f:
                json.dump(data, f, indent=2, ensure_ascii=False)
            logger.debug(f"Diarization fixes saved to: {path}")

    def _save_prompts(self, output_path: str, prompts: List[Dict[str, Any]], episode_id: str = ""):
        """
        Save the exact prompts sent to the LLM during cleaning as separate .md files for easier debugging.

        Each prompt is saved as a separate markdown file with clear formatting:
        - debug/prompts/{base_name}.prompt_1_analysis_chunk1.md
        - debug/prompts/{base_name}.prompt_2_analysis_chunk2.md
        - debug/prompts/{base_name}.prompt_3_speaker_identification.md

        Args:
            output_path: Base output path (e.g., data/clean_transcripts/Podcast_Episode_hash_cleaned.md)
            prompts: List of prompt records captured during processing
            episode_id: Internal episode ID (unused, kept for API compatibility)
        """
        if not prompts:
            return

        output_path = Path(output_path)
        prompts_dir = output_path.parent / "debug" / "prompts"
        prompts_dir.mkdir(parents=True, exist_ok=True)

        base_name = output_path.stem.replace("_cleaned", "")

        for idx, prompt_record in enumerate(prompts, start=1):
            phase = prompt_record.get("phase", "unknown")
            chunk = prompt_record.get("chunk")
            total_chunks = prompt_record.get("total_chunks")

            # Build descriptive filename
            if chunk and total_chunks:
                filename = f"{base_name}.prompt_{idx}_{phase}_chunk{chunk}of{total_chunks}.md"
            else:
                filename = f"{base_name}.prompt_{idx}_{phase}.md"

            path = prompts_dir / filename

            # Format prompt as readable markdown
            md_content = self._format_prompt_as_markdown(prompt_record, idx)

            with open(path, "w", encoding="utf-8") as f:
                f.write(md_content)

        logger.debug(f"Saved {len(prompts)} prompts to: {prompts_dir}")

    def _create_prompt_save_callback(self, output_path: str):
        """
        Create a callback function that saves prompts immediately when called.

        This is used by v2 cleaner to save prompts BEFORE each LLM call,
        ensuring prompts are preserved even if the LLM call fails.

        Args:
            output_path: Base output path for determining debug directory

        Returns:
            Callback function that takes a prompt_record dict and saves it to disk
        """
        output_path_obj = Path(output_path)
        prompts_dir = output_path_obj.parent / "debug" / "prompts"
        prompts_dir.mkdir(parents=True, exist_ok=True)
        base_name = output_path_obj.stem.replace("_cleaned", "")

        # Use a mutable container to track prompt count across calls
        prompt_counter = [0]

        def save_prompt(prompt_record: Dict[str, Any]) -> None:
            """Save a single prompt record immediately."""
            prompt_counter[0] += 1
            idx = prompt_counter[0]

            phase = prompt_record.get("phase", "unknown")
            chunk = prompt_record.get("chunk")
            total_chunks = prompt_record.get("total_chunks")

            # Build descriptive filename
            if chunk and total_chunks:
                filename = f"{base_name}.prompt_{idx}_{phase}_chunk{chunk}of{total_chunks}.md"
            else:
                filename = f"{base_name}.prompt_{idx}_{phase}.md"

            path = prompts_dir / filename

            # Format and save immediately
            md_content = self._format_prompt_as_markdown(prompt_record, idx)
            with open(path, "w", encoding="utf-8") as f:
                f.write(md_content)

            logger.debug(f"Saved prompt to: {path}")

        return save_prompt

    def _format_prompt_as_markdown(self, prompt_record: Dict[str, Any], idx: int) -> str:
        """
        Format a prompt record as readable markdown for debugging.

        Args:
            prompt_record: Dict containing phase, messages, temperature, max_tokens, etc.
            idx: Prompt index (1-based)

        Returns:
            Formatted markdown string
        """
        lines = []

        # Header with metadata
        phase = prompt_record.get("phase", "unknown")
        chunk = prompt_record.get("chunk")
        total_chunks = prompt_record.get("total_chunks")
        temperature = prompt_record.get("temperature", "N/A")
        max_tokens = prompt_record.get("max_tokens", "N/A")

        lines.append(f"# Prompt {idx}: {phase.replace('_', ' ').title()}")
        lines.append("")

        if chunk and total_chunks:
            lines.append(f"**Chunk:** {chunk} of {total_chunks}")
        lines.append(f"**Temperature:** {temperature}")
        lines.append(f"**Max Tokens:** {max_tokens}")

        # Add any extra metadata
        extra_keys = [
            k
            for k in prompt_record.keys()
            if k not in ("phase", "chunk", "total_chunks", "messages", "temperature", "max_tokens")
        ]
        for key in extra_keys:
            lines.append(f"**{key.replace('_', ' ').title()}:** {prompt_record[key]}")

        lines.append("")
        lines.append("---")
        lines.append("")

        # Messages
        messages = prompt_record.get("messages", [])
        for msg in messages:
            role = msg.get("role", "unknown").upper()
            content = msg.get("content", "")

            lines.append(f"## {role}")
            lines.append("")
            lines.append(content)
            lines.append("")
            lines.append("---")
            lines.append("")

        return "\n".join(lines)

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
        - data/clean_transcripts/debug/prompts/{base_name}.prompt_*.md - Debug: individual prompts as markdown

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
