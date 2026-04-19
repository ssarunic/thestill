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
- Pass 2: Transcript cleanup using extracted facts

Two Pass-2 pipelines coexist during the spec #18 transition window:

- ``legacy`` — the original :class:`TranscriptCleaner` that treats the
  transcript as a single blended-Markdown blob. Still required for
  Parakeet fallback until the Parakeet transcriber is fixed.
- ``segmented`` — the structure-preserving pipeline of spec #18
  (``TranscriptSegmenter`` + ``SegmentedTranscriptCleaner``). Produces a
  JSON sidecar of per-segment cleaned text alongside the blended
  Markdown render. The JSON sidecar is canonical and preserves every
  segment kind (including full ad text); the Markdown is an
  ads-stripped projection fed to the summariser. Callers that want the
  "with ads" view render from the JSON on demand.

Which pipeline runs as the **primary** producer of
``clean_transcript_path`` is selected by ``THESTILL_CLEANUP_PIPELINE``
(values ``segmented`` or ``legacy``, default ``segmented``).
``THESTILL_LEGACY_CLEANUP_SHADOW`` (boolean, default truthy) controls
whether the non-primary pipeline also runs and writes its output to a
sibling debug file so the two can be compared side by side. Degenerate
transcripts that fail the capability check are force-routed to the
legacy path and skip the shadow entirely, regardless of either flag.
"""

import json
import os
import time
from pathlib import Path
from typing import Any, Callable, Dict, List, Optional, Tuple

from structlog import get_logger

from ..models.annotated_transcript import AnnotatedTranscript
from ..models.transcript import Transcript
from ..utils.console import ConsoleOutput
from ..utils.path_manager import CleanupPipelineName
from .llm_provider import LLMProvider
from .segmented_transcript_cleaner import SegmentedTranscriptCleaner
from .transcript_cleaner import TranscriptCleaner
from .transcript_formatter import TranscriptFormatter
from .transcript_segmenter import TranscriptSegmenter

logger = get_logger(__name__)

# Recognised routing flag values. The ``CleanupPipelineName`` Literal
# imported from :mod:`thestill.utils.path_manager` is the canonical type;
# these constants give call sites string values that match the Literal.
_PRIMARY_SEGMENTED: CleanupPipelineName = "segmented"
_PRIMARY_LEGACY: CleanupPipelineName = "legacy"


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
        transcript_cleaner = TranscriptCleaner(
            self.provider,
            chunk_size=self.chunk_size,
            on_stream_chunk=on_stream_chunk,
        )

        # Get podcast slug for facts lookup (use provided or generate from title)
        effective_podcast_slug = podcast_slug or (generate_slug(podcast_title) if podcast_title else "unknown-podcast")
        effective_episode_slug = episode_slug or (generate_slug(episode_title) if episode_title else "unknown-episode")

        # Capability check + routing inputs. A successful parse + non-None
        # degeneracy reason means the transcript cannot feed the segmented
        # path; we force-route to legacy and skip the shadow in that case
        # (spec #18 Phase C). Parse failures also force-route to legacy
        # — the processor must never let the capability check block
        # cleanup when the transcript schema shifts unexpectedly.
        transcript_model: Optional[Transcript]
        try:
            transcript_model = Transcript.model_validate(transcript_data)
        except Exception as parse_error:  # pylint: disable=broad-except
            logger.debug(
                "transcript_capability_check_skipped",
                podcast_slug=effective_podcast_slug,
                episode_slug=effective_episode_slug,
                error=str(parse_error),
            )
            transcript_model = None
            degeneracy_reason: Optional[str] = "parse_error"
        else:
            degeneracy_reason = classify_transcript_degeneracy(transcript_model)
            if degeneracy_reason is not None:
                logger.warning(
                    "segmented_cleanup_unavailable",
                    reason=degeneracy_reason,
                    podcast_slug=effective_podcast_slug,
                    episode_slug=effective_episode_slug,
                    segment_count=len(transcript_model.segments),
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

        # Format JSON to markdown (much smaller than raw JSON for LLM).
        # Always produced — both the legacy Pass 2 consumes it directly
        # and the "original" debug artefact snapshots it pre-cleanup.
        logger.info("Formatting transcript JSON to markdown...")
        formatted_markdown = self.formatter.format_transcript(transcript_data)

        # Save debug artifacts if output_path provided
        if output_path:
            # Save original formatted markdown (before LLM cleaning)
            self._save_phase_output(output_path, "original", formatted_markdown)

            # Save speaker mapping from episode facts
            if episode_facts and episode_facts.speaker_mapping:
                self._save_phase_output(output_path, "speakers", episode_facts.speaker_mapping)

        # Degeneracy has authority over the routing flag: when the
        # transcript cannot feed segmented cleanup, primary is forced to
        # legacy and the shadow is skipped, regardless of either env var.
        degenerate = degeneracy_reason is not None
        primary_pipeline = _resolve_primary_pipeline(
            env_value=os.environ.get("THESTILL_CLEANUP_PIPELINE"),
            force_legacy=degenerate,
        )
        shadow_pipeline = _resolve_shadow_pipeline(
            env_value=os.environ.get("THESTILL_LEGACY_CLEANUP_SHADOW"),
            primary=primary_pipeline,
            force_disable=degenerate,
        )

        logger.info(
            "cleanup_routing_resolved",
            primary=primary_pipeline,
            shadow=shadow_pipeline,
            degeneracy_reason=degeneracy_reason,
        )

        # Run the primary. It produces blended Markdown always; the
        # segmented path additionally produces an AnnotatedTranscript
        # which we persist as a JSON sidecar alongside the Markdown.
        primary_markdown, primary_annotated = self._run_pipeline(
            pipeline=primary_pipeline,
            transcript_model=transcript_model,
            formatted_markdown=formatted_markdown,
            transcript_cleaner=transcript_cleaner,
            podcast_facts=podcast_facts,
            episode_facts=episode_facts,
            episode_title=episode_title,
            language=language,
            prompt_save_callback=prompt_save_callback,
        )

        # Write primary output.
        cleaned_json_path: Optional[str] = None
        if output_path:
            output_file = Path(output_path)
            output_file.parent.mkdir(parents=True, exist_ok=True)
            output_file.write_text(primary_markdown, encoding="utf-8")
            logger.info(f"Saved cleaned transcript: {output_path}")

            # The segmented pipeline additionally persists its
            # structured artefact as a JSON sidecar next to the Markdown.
            if primary_annotated is not None:
                json_sidecar = output_file.with_suffix(".json")
                json_sidecar.write_text(primary_annotated.model_dump_json(indent=2), encoding="utf-8")
                cleaned_json_path = str(json_sidecar)
                logger.info(f"Saved annotated transcript JSON: {json_sidecar}")

        # Run the shadow (if any) and write it to the sibling debug file.
        shadow_output_path: Optional[str] = None
        if shadow_pipeline is not None:
            try:
                shadow_markdown, _ = self._run_pipeline(
                    pipeline=shadow_pipeline,
                    transcript_model=transcript_model,
                    formatted_markdown=formatted_markdown,
                    transcript_cleaner=transcript_cleaner,
                    podcast_facts=podcast_facts,
                    episode_facts=episode_facts,
                    episode_title=episode_title,
                    language=language,
                    prompt_save_callback=None,  # Don't duplicate prompt dumps.
                )
                if output_path:
                    shadow_output_path = _write_shadow_output(
                        primary_output_path=output_path,
                        pipeline=shadow_pipeline,
                        content=shadow_markdown,
                    )
            except Exception as shadow_error:  # pylint: disable=broad-except
                # The shadow is diagnostic; a failure here must never
                # mask the primary's success. Log loudly and continue.
                logger.warning(
                    "cleanup_shadow_failed",
                    shadow_pipeline=shadow_pipeline,
                    error=str(shadow_error),
                    exc_info=True,
                )

        processing_time = time.time() - start_time
        logger.info(f"Transcript cleaning completed in {processing_time:.1f} seconds")

        return {
            "cleaned_markdown": primary_markdown,
            "cleaned_json_path": cleaned_json_path,
            "primary_pipeline": primary_pipeline,
            "shadow_pipeline": shadow_pipeline,
            "shadow_path": shadow_output_path,
            "podcast_facts": podcast_facts,
            "episode_facts": episode_facts,
            "processing_time": processing_time,
        }

    def _run_pipeline(
        self,
        *,
        pipeline: CleanupPipelineName,
        transcript_model: Optional[Transcript],
        formatted_markdown: str,
        transcript_cleaner: TranscriptCleaner,
        podcast_facts: Any,
        episode_facts: Any,
        episode_title: str,
        language: str,
        prompt_save_callback: Optional[Callable[[Dict[str, Any]], None]],
    ) -> Tuple[str, Optional[AnnotatedTranscript]]:
        """Dispatch to the named cleanup pipeline.

        Returns ``(blended_markdown, annotated_or_none)``. The annotated
        transcript is populated only for the segmented pipeline — the
        legacy path returns ``None`` there and the caller treats that as
        "no JSON sidecar to persist". When the caller requests the
        segmented pipeline but ``transcript_model`` is ``None`` (e.g.
        the raw-JSON parse failed upstream), we log and recurse into
        the legacy arm rather than crashing.
        """
        if pipeline == _PRIMARY_SEGMENTED and transcript_model is None:
            logger.error(
                "segmented_pipeline_requested_without_transcript_model",
                hint="routing should force legacy when parse fails",
            )
            pipeline = _PRIMARY_LEGACY

        if pipeline == _PRIMARY_LEGACY:
            markdown = transcript_cleaner.clean_transcript(
                formatted_markdown=formatted_markdown,
                podcast_facts=podcast_facts,
                episode_facts=episode_facts,
                episode_title=episode_title,
                language=language,
                on_prompt_ready=prompt_save_callback,
            )
            return markdown, None

        if pipeline == _PRIMARY_SEGMENTED:
            assert transcript_model is not None  # ruled out above
            segmenter = TranscriptSegmenter()
            annotated = segmenter.repair(transcript_model)
            cleaner = SegmentedTranscriptCleaner(self.provider)
            cleaned_annotated = cleaner.clean(
                annotated=annotated,
                podcast_facts=podcast_facts,
                episode_facts=episode_facts,
                language=language,
            )
            # Ads are tagged on the JSON sidecar (the canonical artefact)
            # and stripped from the Markdown projection — the summariser
            # has always read ads-free Markdown and continues to. The
            # web viewer renders from the JSON when it wants the full
            # transcript with ads visible.
            markdown = cleaned_annotated.to_blended_markdown(exclude_kinds={"ad_break"})
            return markdown, cleaned_annotated

        raise ValueError(f"unknown pipeline: {pipeline!r}")

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


# ---------------------------------------------------------------------------
# Module-level routing helpers. Kept outside the class so they have a single
# self-evident signature each and are easy to exercise from the routing
# matrix test (``tests/unit/core/test_cleanup_processor_routing.py``).
# ---------------------------------------------------------------------------


def _resolve_primary_pipeline(*, env_value: Optional[str], force_legacy: bool) -> CleanupPipelineName:
    """Return the primary cleanup pipeline name for this invocation.

    Routing order:

    1. ``force_legacy`` wins unconditionally — used when the transcript
       fails the capability check. Segmented cleanup simply cannot run
       on Parakeet-style stub input regardless of the operator's intent.
    2. Otherwise, the ``THESTILL_CLEANUP_PIPELINE`` env var selects the
       pipeline. ``"segmented"`` (default) or ``"legacy"``; unknown
       values log a warning and default to segmented.
    """
    if force_legacy:
        return _PRIMARY_LEGACY

    resolved = (env_value or _PRIMARY_SEGMENTED).strip().lower()
    if resolved == _PRIMARY_LEGACY:
        return _PRIMARY_LEGACY
    if resolved == _PRIMARY_SEGMENTED:
        return _PRIMARY_SEGMENTED
    logger.warning(
        "unknown_cleanup_pipeline_flag",
        got=resolved,
        defaulting_to=_PRIMARY_SEGMENTED,
    )
    return _PRIMARY_SEGMENTED


def _resolve_shadow_pipeline(
    *,
    env_value: Optional[str],
    primary: CleanupPipelineName,
    force_disable: bool,
) -> Optional[CleanupPipelineName]:
    """Return the shadow pipeline name, or ``None`` if the shadow is disabled.

    ``force_disable`` overrides the env var — degenerate-input transcripts
    force both the pipeline and the shadow decision. Otherwise the env
    var is interpreted as a permissive boolean (default truthy for dev),
    and the shadow is always the pipeline the primary is *not*.
    """
    if force_disable:
        return None
    if env_value is None:
        enabled = True
    else:
        enabled = env_value.strip().lower() in ("1", "true", "yes", "on", "y", "t")
    if not enabled:
        return None
    return _PRIMARY_LEGACY if primary == _PRIMARY_SEGMENTED else _PRIMARY_SEGMENTED


def _write_shadow_output(
    *,
    primary_output_path: str,
    pipeline: str,
    content: str,
) -> str:
    """Write shadow cleanup output to the sibling debug file and return its path.

    Mirrors the naming convention in
    :meth:`PathManager.clean_transcript_shadow_file`:
    ``{parent}/debug/{stem}.shadow_{pipeline}.md``.
    """
    primary_path = Path(primary_output_path)
    debug_dir = primary_path.parent / "debug"
    debug_dir.mkdir(parents=True, exist_ok=True)
    shadow_path = debug_dir / f"{primary_path.stem}.shadow_{pipeline}.md"
    shadow_path.write_text(content, encoding="utf-8")
    logger.info(
        "cleanup_shadow_written",
        shadow_pipeline=pipeline,
        shadow_path=str(shadow_path),
    )
    return str(shadow_path)
