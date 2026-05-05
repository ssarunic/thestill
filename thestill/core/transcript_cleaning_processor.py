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
Transcript cleaning processor using facts-based two-pass approach.

This processor orchestrates:
- Pass 1: Facts extraction (speaker mapping, guests, keywords, ad sponsors)
- Pass 2: Segmented transcript cleanup using extracted facts

The Pass-2 pipeline is the structure-preserving spec #18 implementation
(``TranscriptSegmenter`` + ``SegmentedTranscriptCleaner``). It produces
a JSON sidecar of per-segment cleaned text alongside the blended
Markdown render. The JSON sidecar is canonical and preserves every
segment kind (including full ad text); the Markdown is an
ads-stripped projection fed to the summariser. Callers that want the
"with ads" view render from the JSON on demand.

Transcripts that can't feed the segmented path (parse-failed JSON or
``classify_transcript_degeneracy`` flag) raise a clear error rather
than silently degrading to a markdown-only legacy path. This forces
upstream issues — schema drift, broken provider output — to surface
instead of producing an episode that's invisible to corpus search.
"""

import json
import time
from pathlib import Path
from typing import Any, Callable, Dict, Optional

from structlog import get_logger

from ..models.annotated_transcript import SegmentKind
from ..utils.console import ConsoleOutput
from .llm_provider import LLMProvider
from .segmented_transcript_cleaner import SegmentedTranscriptCleaner
from .transcript_formatter import TranscriptFormatter
from .transcript_segmenter import TranscriptSegmenter

logger = get_logger(__name__)


class DegenerateTranscriptError(RuntimeError):
    """Raised when an input transcript can't feed the segmented cleaner.

    Carries the ``reason`` from ``classify_transcript_degeneracy`` (or
    ``"parse_error"`` when the raw JSON failed to validate against the
    ``Transcript`` schema). Surfaces to the queue worker, which marks
    the clean task as failed with this message — keeping the bug visible
    rather than silently producing a sidecarless episode.
    """


# Kinds stripped from the canonical blended-Markdown projection that
# feeds the summariser. The JSON sidecar always carries every kind
# (including full ad text) — the web viewer filters for display
# instead. Adding a kind here is the one edit required when a new
# kind should be hidden from summarisation.
_ADS_STRIPPED_KINDS: frozenset[SegmentKind] = frozenset({"ad_break"})


class TranscriptCleaningProcessor:
    """
    Transcript cleaning processor using facts-based approach.

    Orchestrates the two-pass cleaning pipeline:
    - Pass 1: Extract facts from transcript (speaker mapping, guests, keywords)
    - Pass 2: Clean transcript using extracted facts context
    """

    def __init__(
        self,
        provider: LLMProvider,
        chunk_size: Optional[int] = None,
        console: Optional[ConsoleOutput] = None,
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
            console: ConsoleOutput instance for user-facing messages (optional)
        """
        self.provider = provider
        self.formatter = TranscriptFormatter(console=console)

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
        podcast_slug: str = "",
        episode_slug: str = "",
        output_path: Optional[str] = None,
        path_manager: Optional[Any] = None,
        save_prompts: bool = True,
        on_stream_chunk: Optional[Callable[[str], None]] = None,
        *,
        language: str,
    ) -> Dict:
        """
        Clean transcript using the two-pass facts-based approach.

        This method uses:
        - Pass 1: Facts extraction (speaker mapping, guests, keywords, ad sponsors)
        - Pass 2: Transcript cleanup using extracted facts

        Facts are stored as human-editable Markdown files:
        - Podcast facts: data/podcast_facts/{podcast_slug}.facts.md
        - Episode facts: data/episode_facts/{podcast_slug}/{episode_slug}.facts.md

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
            podcast_slug: Slugified podcast title for facts file naming
            episode_slug: Slugified episode title for facts file naming
            output_path: Optional path to save final cleaned transcript
            path_manager: PathManager instance for facts file paths (required)
            save_prompts: Whether to save prompts to debug folder (default: True)
            on_stream_chunk: Optional callback for streaming LLM output chunks
            language: ISO 639-1 language code (e.g., "en", "hr", "de") for language-aware cleaning

        Returns:
            Dict with keys: cleaned_markdown, podcast_facts, episode_facts, processing_time
        """
        from thestill.core.facts_extractor import FactsExtractor
        from thestill.core.facts_manager import FactsManager
        from thestill.models.transcript import Transcript
        from thestill.utils.slug import generate_slug
        from thestill.utils.transcript_capabilities import classify_transcript_degeneracy

        if path_manager is None:
            raise ValueError("path_manager is required for clean_transcript")

        start_time = time.time()

        # Create prompt save callback if saving is enabled
        prompt_save_callback = None
        if output_path and save_prompts:
            prompt_save_callback = self._create_prompt_save_callback(output_path)

        # Initialize components
        facts_manager = FactsManager(path_manager)
        facts_extractor = FactsExtractor(self.provider, chunk_size=self.chunk_size)
        # The streaming callback is currently only honoured by the legacy
        # cleaner. The segmented path emits per-batch progress through
        # structured logs instead — drop the callback rather than carry
        # a no-op parameter through.
        del on_stream_chunk  # reserved for future segmented-streaming hook

        # Get podcast slug for facts lookup (use provided or generate from title)
        effective_podcast_slug = podcast_slug or (generate_slug(podcast_title) if podcast_title else "unknown-podcast")
        effective_episode_slug = episode_slug or (generate_slug(episode_title) if episode_title else "unknown-episode")

        # Capability check. The segmented pipeline is the only Pass-2
        # implementation: a parse failure or a degeneracy verdict means
        # the input cannot be cleaned. Raise so the queue worker marks
        # the task failed instead of silently producing a markdown-only
        # episode that wouldn't be searchable.
        try:
            transcript_model = Transcript.model_validate(transcript_data)
        except Exception as parse_error:
            logger.error(
                "transcript_parse_failed",
                podcast_slug=effective_podcast_slug,
                episode_slug=effective_episode_slug,
                error=str(parse_error),
            )
            raise DegenerateTranscriptError(
                f"raw transcript JSON failed Transcript.model_validate: {parse_error}"
            ) from parse_error

        degeneracy_reason = classify_transcript_degeneracy(transcript_model)
        if degeneracy_reason is not None:
            logger.error(
                "segmented_cleanup_unavailable",
                reason=degeneracy_reason,
                podcast_slug=effective_podcast_slug,
                episode_slug=effective_episode_slug,
                segment_count=len(transcript_model.segments),
            )
            raise DegenerateTranscriptError(
                f"transcript cannot feed segmented cleaner ({degeneracy_reason}); "
                "re-transcribe with a provider that produces real per-segment timing"
            )

        # Load existing facts
        podcast_facts = facts_manager.load_podcast_facts(effective_podcast_slug)
        episode_facts = (
            facts_manager.load_episode_facts(effective_podcast_slug, effective_episode_slug)
            if effective_episode_slug
            else None
        )

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
                language=language,
            )
            # Save episode facts
            if effective_episode_slug:
                facts_manager.save_episode_facts(effective_podcast_slug, effective_episode_slug, episode_facts)
                logger.info(
                    f"Saved episode facts: {facts_manager.get_episode_facts_path(effective_podcast_slug, effective_episode_slug)}"
                )

        # Initialize podcast facts if first episode
        if not podcast_facts:
            logger.info("Pass 1: Extracting initial podcast facts...")
            podcast_facts = facts_extractor.extract_initial_podcast_facts(
                transcript_data=transcript_data,
                podcast_title=podcast_title,
                podcast_description=podcast_description,
                episode_facts=episode_facts,
                language=language,
            )
            # Save podcast facts
            facts_manager.save_podcast_facts(effective_podcast_slug, podcast_facts)
            logger.info(f"Saved podcast facts: {facts_manager.get_podcast_facts_path(effective_podcast_slug)}")

        # Save debug artifacts if output_path provided. The "original"
        # snapshot is the formatter's projection of the raw JSON before
        # any LLM cleaning runs — useful for diffing against the cleaned
        # blended-Markdown output.
        if output_path:
            formatted_markdown = self.formatter.format_transcript(transcript_data)
            self._save_phase_output(output_path, "original", formatted_markdown)
            if episode_facts and episode_facts.speaker_mapping:
                self._save_phase_output(output_path, "speakers", episode_facts.speaker_mapping)

        # The prompt-save callback is wired through the segmented cleaner
        # via the LLM provider's ``on_prompt_ready`` mechanism. Currently
        # SegmentedTranscriptCleaner doesn't accept the callback directly
        # — saving prompts there is a follow-up. For now the parameter is
        # accepted for parity but unused for the segmented path.
        del prompt_save_callback

        # Run the segmented cleaner. Returns an ``AnnotatedTranscript``
        # which we persist as the canonical JSON sidecar; the blended
        # Markdown is derived from it for the summariser.
        segmenter = TranscriptSegmenter()
        annotated = segmenter.repair(transcript_model)
        cleaner = SegmentedTranscriptCleaner(self.provider)
        cleaned_annotated = cleaner.clean(
            annotated=annotated,
            podcast_facts=podcast_facts,
            episode_facts=episode_facts,
            language=language,
        )
        # Record the duration of the audio we just transcribed. The
        # viewer uses this to detect drift when the live URL later
        # returns a DAI-mutated file of a different length.
        cleaned_annotated.transcript_source_duration_s = transcript_model.get_duration()
        # Ads are tagged on the JSON sidecar (the canonical artefact)
        # and stripped from the Markdown projection — the summariser
        # has always read ads-free Markdown and continues to. The web
        # viewer renders from the JSON when it wants the full transcript
        # with ads visible.
        cleaned_markdown = cleaned_annotated.to_blended_markdown(exclude_kinds=_ADS_STRIPPED_KINDS)

        # Write primary output and JSON sidecar.
        cleaned_json_path: Optional[str] = None
        if output_path:
            output_file = Path(output_path)
            output_file.parent.mkdir(parents=True, exist_ok=True)
            output_file.write_text(cleaned_markdown, encoding="utf-8")
            logger.info(f"Saved cleaned transcript: {output_path}")

            json_sidecar = output_file.with_suffix(".json")
            json_sidecar.write_text(cleaned_annotated.model_dump_json(indent=2), encoding="utf-8")
            cleaned_json_path = str(json_sidecar)
            logger.info(f"Saved annotated transcript JSON: {json_sidecar}")

        processing_time = time.time() - start_time
        logger.info(f"Transcript cleaning completed in {processing_time:.1f} seconds")

        return {
            "cleaned_markdown": cleaned_markdown,
            "cleaned_json_path": cleaned_json_path,
            "podcast_facts": podcast_facts,
            "episode_facts": episode_facts,
            "processing_time": processing_time,
        }

    def _save_phase_output(self, output_path: str, phase: str, data, episode_id: str = ""):
        """
        Save output from a specific phase immediately after completion.

        Args:
            output_path: Base output path (e.g., data/clean_transcripts/Podcast_Episode_hash_cleaned.md)
            phase: Phase name (original, speakers)
            data: Data to save (dict or string)
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

        elif phase == "speakers":
            # Save to debug directory: {base_name}.speakers.json
            path = debug_dir / f"{base_name}.speakers.json"
            with open(path, "w", encoding="utf-8") as f:
                json.dump(data, f, indent=2, ensure_ascii=False)
            logger.debug(f"Speaker mapping saved to: {path}")

    def _create_prompt_save_callback(self, output_path: str):
        """
        Create a callback function that saves prompts immediately when called.

        This saves prompts BEFORE each LLM call, ensuring prompts are preserved
        even if the LLM call fails.

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
