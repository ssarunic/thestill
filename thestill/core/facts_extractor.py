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
Facts extractor for Pass 1 of transcript cleaning.

Analyzes transcript and metadata to extract:
- Speaker mapping (SPEAKER_XX → Name)
- Episode-specific facts (guests, topics, ad sponsors)
- Initial podcast facts (hosts, recurring roles, keywords)

Uses structured output (when supported) for reliable JSON schema validation.
"""

import json
import logging
import re
from typing import Any, Dict, List, Optional, Tuple

from pydantic import BaseModel, Field

from thestill.core.llm_provider import LLMProvider
from thestill.core.post_processor import MODEL_CONFIGS
from thestill.core.transcript_formatter import TranscriptFormatter
from thestill.models.facts import EpisodeFacts, PodcastFacts

logger = logging.getLogger(__name__)


# Response models for structured output
class EpisodeFactsResponse(BaseModel):
    """LLM response schema for episode facts extraction."""

    speaker_mapping: Dict[str, str] = Field(
        default_factory=dict, description="Mapping of SPEAKER_XX to 'Name (Role)' format"
    )
    guests: List[str] = Field(default_factory=list, description="List of guests in 'Name - Role/Company' format")
    topics_keywords: List[str] = Field(default_factory=list, description="Episode-specific proper nouns and terms")
    ad_sponsors: List[str] = Field(default_factory=list, description="Sponsors mentioned in ad segments")


class PodcastFactsResponse(BaseModel):
    """LLM response schema for podcast facts extraction."""

    hosts: List[str] = Field(default_factory=list, description="Regular hosts in 'Name - Description' format")
    recurring_roles: List[str] = Field(default_factory=list, description="Non-host roles that appear regularly")
    known_guests: List[str] = Field(
        default_factory=list, description="Notable guests (usually empty for initial extraction)"
    )
    sponsors: List[str] = Field(
        default_factory=list, description="Long-term sponsors (usually empty for initial extraction)"
    )
    keywords: List[str] = Field(default_factory=list, description="Permanent terms related to the podcast")
    style_notes: List[str] = Field(
        default_factory=lambda: ["Preserve original speaking style"],
        description="General style guidance for the podcast",
    )


# Default max output tokens for facts extraction (fallback for unknown models)
DEFAULT_MAX_OUTPUT_TOKENS = 8192


def get_max_output_tokens(model_name: str) -> int:
    """
    Get the maximum output tokens for a model from MODEL_CONFIGS.

    Args:
        model_name: The model name to look up

    Returns:
        Maximum output tokens for the model, or DEFAULT_MAX_OUTPUT_TOKENS if not found
    """
    # Check for exact match first
    if model_name in MODEL_CONFIGS:
        return MODEL_CONFIGS[model_name].max_output_tokens

    # Check for partial match (model names often have date suffixes)
    for config_model_name, limits in MODEL_CONFIGS.items():
        if model_name.startswith(config_model_name.rsplit("-", 1)[0]):
            return limits.max_output_tokens

    logger.warning(f"Model '{model_name}' not found in MODEL_CONFIGS, using default {DEFAULT_MAX_OUTPUT_TOKENS}")
    return DEFAULT_MAX_OUTPUT_TOKENS


def extract_json_from_response(response: str) -> str:
    """
    Extract JSON from LLM response that might contain markdown code blocks.

    Handles responses like:
    - Pure JSON: {"key": "value"}
    - Markdown wrapped: ```json\n{"key": "value"}\n```
    - Text with JSON: Here is the JSON:\n{"key": "value"}
    """
    if not response:
        return ""

    # Try to parse as-is first
    try:
        json.loads(response)
        return response
    except json.JSONDecodeError:
        pass

    # Try to extract from markdown code blocks
    code_block_pattern = r"```(?:json)?\s*\n?([\s\S]*?)\n?```"
    matches = re.findall(code_block_pattern, response)
    for match in matches:
        try:
            json.loads(match.strip())
            return match.strip()
        except json.JSONDecodeError:
            continue

    # Try to find JSON object pattern
    json_pattern = r"\{[\s\S]*\}"
    matches = re.findall(json_pattern, response)
    for match in matches:
        try:
            json.loads(match)
            return match
        except json.JSONDecodeError:
            continue

    # Return original if nothing worked
    return response


class FactsExtractor:
    """
    Extracts facts from transcripts using LLM analysis.

    Pass 1 of the two-pass transcript cleaning pipeline.
    """

    def __init__(self, provider: LLMProvider, chunk_size: int = 100000):
        """
        Initialize facts extractor.

        Args:
            provider: LLM provider for analysis
            chunk_size: Maximum characters per chunk (for large transcripts)
        """
        self.provider = provider
        self.chunk_size = chunk_size
        self.formatter = TranscriptFormatter()
        # Get max output tokens from model config
        self.max_output_tokens = get_max_output_tokens(provider.get_model_name())
        logger.info(
            f"FactsExtractor using model '{provider.get_model_name()}' with max_output_tokens={self.max_output_tokens}"
        )

    def extract_episode_facts(
        self,
        transcript_data: Dict[str, Any],
        podcast_title: str,
        podcast_description: str,
        episode_title: str,
        episode_description: str,
        podcast_facts: Optional[PodcastFacts] = None,
    ) -> EpisodeFacts:
        """
        Extract episode-specific facts from transcript (Pass 1).

        This includes:
        - Speaker mapping hypothesis (SPEAKER_XX → Name)
        - Guests identified in this episode
        - Topics and keywords specific to this episode
        - Ad sponsors mentioned

        Uses structured output when supported by the provider for guaranteed
        schema compliance.

        Args:
            transcript_data: Raw transcript JSON from transcriber
            podcast_title: Title of the podcast
            podcast_description: Description of the podcast
            episode_title: Title of the episode
            episode_description: Description of the episode
            podcast_facts: Existing podcast facts for context (optional)

        Returns:
            EpisodeFacts with extracted information
        """
        # Format transcript for analysis
        formatted_transcript = self.formatter.format_transcript(transcript_data)

        # Build context from existing podcast facts
        podcast_context = ""
        if podcast_facts:
            podcast_context = self._render_podcast_facts_context(podcast_facts)

        # Build the prompt
        system_prompt = self._build_facts_extraction_system_prompt()
        user_prompt = self._build_facts_extraction_user_prompt(
            formatted_transcript=formatted_transcript,
            podcast_title=podcast_title,
            podcast_description=podcast_description,
            episode_title=episode_title,
            episode_description=episode_description,
            podcast_context=podcast_context,
        )

        messages = [
            {"role": "system", "content": system_prompt},
            {"role": "user", "content": user_prompt},
        ]

        # Call LLM with structured output
        logger.info("Extracting episode facts with LLM (structured output)...")
        try:
            result = self.provider.generate_structured(
                messages=messages,
                response_model=EpisodeFactsResponse,
                temperature=0.1,
                max_tokens=self.max_output_tokens,
            )

            # Convert response model to EpisodeFacts
            return EpisodeFacts(
                episode_title=episode_title,
                speaker_mapping=result.speaker_mapping,
                guests=result.guests,
                topics_keywords=result.topics_keywords,
                ad_sponsors=result.ad_sponsors,
            )

        except Exception as e:
            logger.error(f"Structured output extraction failed: {e}")
            logger.info("Falling back to legacy JSON mode extraction...")
            return self._extract_episode_facts_legacy(
                messages=messages,
                episode_title=episode_title,
            )

    def _extract_episode_facts_legacy(
        self,
        messages: List[Dict[str, str]],
        episode_title: str,
    ) -> EpisodeFacts:
        """
        Legacy extraction method using JSON mode (fallback).

        Used when structured output fails or is not available.
        """
        response = self.provider.chat_completion(
            messages=messages,
            temperature=0.1,
            max_tokens=self.max_output_tokens,
            response_format={"type": "json_object"},
        )

        # Parse response - extract JSON from potential markdown wrapping
        json_str = extract_json_from_response(response)
        try:
            result = json.loads(json_str)
        except json.JSONDecodeError as e:
            logger.error(f"Failed to parse LLM response as JSON: {e}")
            logger.warning(f"Raw response (first 1000 chars): {response[:1000]}")
            # Return minimal facts on parse failure
            return EpisodeFacts(episode_title=episode_title)

        # Build EpisodeFacts from response
        return EpisodeFacts(
            episode_title=episode_title,
            speaker_mapping=result.get("speaker_mapping", {}),
            guests=result.get("guests", []),
            topics_keywords=result.get("topics_keywords", []),
            ad_sponsors=result.get("ad_sponsors", []),
        )

    def extract_initial_podcast_facts(
        self,
        transcript_data: Dict[str, Any],
        podcast_title: str,
        podcast_description: str,
        episode_facts: EpisodeFacts,
    ) -> PodcastFacts:
        """
        Extract initial podcast facts from first episode processed.

        This is called when no podcast facts file exists yet.
        Uses structured output when supported by the provider.

        Args:
            transcript_data: Raw transcript JSON
            podcast_title: Title of the podcast
            podcast_description: Description of the podcast
            episode_facts: Already extracted episode facts

        Returns:
            PodcastFacts with initial podcast-level information
        """
        # Format transcript for analysis
        formatted_transcript = self.formatter.format_transcript(transcript_data)

        # Build the prompt
        system_prompt = self._build_podcast_facts_system_prompt()
        user_prompt = self._build_podcast_facts_user_prompt(
            formatted_transcript=formatted_transcript,
            podcast_title=podcast_title,
            podcast_description=podcast_description,
            episode_facts=episode_facts,
        )

        messages = [
            {"role": "system", "content": system_prompt},
            {"role": "user", "content": user_prompt},
        ]

        # Call LLM with structured output
        logger.info("Extracting initial podcast facts with LLM (structured output)...")
        try:
            result = self.provider.generate_structured(
                messages=messages,
                response_model=PodcastFactsResponse,
                temperature=0.1,
                max_tokens=self.max_output_tokens,
            )

            # Convert response model to PodcastFacts
            return PodcastFacts(
                podcast_title=podcast_title,
                hosts=result.hosts,
                recurring_roles=result.recurring_roles,
                known_guests=result.known_guests,
                sponsors=result.sponsors,
                keywords=result.keywords,
                style_notes=result.style_notes,
            )

        except Exception as e:
            logger.error(f"Structured output extraction failed: {e}")
            logger.info("Falling back to legacy JSON mode extraction...")
            return self._extract_podcast_facts_legacy(
                messages=messages,
                podcast_title=podcast_title,
            )

    def _extract_podcast_facts_legacy(
        self,
        messages: List[Dict[str, str]],
        podcast_title: str,
    ) -> PodcastFacts:
        """
        Legacy extraction method using JSON mode (fallback).

        Used when structured output fails or is not available.
        """
        response = self.provider.chat_completion(
            messages=messages,
            temperature=0.1,
            max_tokens=self.max_output_tokens,
            response_format={"type": "json_object"},
        )

        # Parse response - extract JSON from potential markdown wrapping
        json_str = extract_json_from_response(response)
        try:
            result = json.loads(json_str)
        except json.JSONDecodeError as e:
            logger.error(f"Failed to parse LLM response as JSON: {e}")
            logger.warning(f"Raw response (first 1000 chars): {response[:1000]}")
            # Return minimal facts on parse failure
            return PodcastFacts(podcast_title=podcast_title)

        return PodcastFacts(
            podcast_title=podcast_title,
            hosts=result.get("hosts", []),
            recurring_roles=result.get("recurring_roles", []),
            known_guests=result.get("known_guests", []),
            sponsors=result.get("sponsors", []),
            keywords=result.get("keywords", []),
            style_notes=result.get("style_notes", ["Preserve original speaking style"]),
        )

    def _render_podcast_facts_context(self, facts: PodcastFacts) -> str:
        """Render podcast facts as context for the prompt."""
        lines = ["EXISTING PODCAST FACTS:"]

        if facts.hosts:
            lines.append("\nHosts:")
            for host in facts.hosts:
                lines.append(f"  - {host}")

        if facts.recurring_roles:
            lines.append("\nRecurring Roles:")
            for role in facts.recurring_roles:
                lines.append(f"  - {role}")

        if facts.known_guests:
            lines.append("\nKnown Guests:")
            for guest in facts.known_guests:
                lines.append(f"  - {guest}")

        if facts.sponsors:
            lines.append("\nKnown Sponsors:")
            for sponsor in facts.sponsors:
                lines.append(f"  - {sponsor}")

        if facts.keywords:
            lines.append("\nKeywords & Common Mishearings:")
            for keyword in facts.keywords:
                lines.append(f"  - {keyword}")

        return "\n".join(lines)

    def _build_facts_extraction_system_prompt(self) -> str:
        """Build system prompt for episode facts extraction."""
        return """You are an expert at analyzing podcast transcripts to extract EPISODE-SPECIFIC facts.

Your task is to analyze a raw transcript and extract facts for THIS SPECIFIC EPISODE:

1. SPEAKER MAPPING: Identify who each SPEAKER_XX is based on:
   - Self-introductions ("I'm Scott Galloway", "This is Ed")
   - How others address them ("Thanks Scott", "Ed, what do you think?")
   - Context clues (who asks questions vs gives opinions, host vs guest patterns)
   - Ad narrator patterns (reads sponsor copy, different tone)

2. GUESTS: Identify any guests appearing in THIS episode
   - Include their role/company if mentioned
   - Do NOT include regular hosts

3. TOPICS/KEYWORDS: Extract episode-specific proper nouns and terms:
   - People discussed in this episode (e.g., "Larry David", "Sam Harris")
   - Companies/organizations mentioned (e.g., "Nvidia", "OpenAI")
   - Places relevant to this episode
   - Technical terms or concepts discussed
   - This helps correct speech-to-text errors for these specific terms

4. AD SPONSORS: Identify sponsors mentioned in THIS episode's ad segments
   - These change per episode, so capture them here
   - Include the sponsor name as spoken in the ad read

IMPORTANT GUIDELINES:
- For speaker mapping, use format "Name (Role)" e.g., "Scott Galloway (Host)"
- If you cannot identify a speaker, use "Unknown Speaker" or keep as SPEAKER_XX
- For ad narrators, use "Ad Narrator" as the name
- Look for patterns: hosts usually introduce guests, guests are introduced by title/company
- Keep topics_keywords focused (20-50 items max) - prioritize proper nouns that might be misspelled

Return your analysis as JSON with this structure:
{
  "speaker_mapping": {
    "SPEAKER_00": "Name (Role)",
    "SPEAKER_01": "Name (Role)"
  },
  "guests": ["Name - Role/Company"],
  "topics_keywords": ["keyword1", "keyword2"],
  "ad_sponsors": ["Sponsor1", "Sponsor2"]
}"""

    def _build_facts_extraction_user_prompt(
        self,
        formatted_transcript: str,
        podcast_title: str,
        podcast_description: str,
        episode_title: str,
        episode_description: str,
        podcast_context: str,
    ) -> str:
        """Build user prompt for episode facts extraction."""
        lines = [
            "PODCAST METADATA:",
            f"Podcast: {podcast_title}",
            f"Description: {podcast_description}",
            "",
            "EPISODE METADATA:",
            f"Episode: {episode_title}",
            f"Description: {episode_description}",
        ]

        if podcast_context:
            lines.extend(["", podcast_context])

        lines.extend(
            [
                "",
                "TRANSCRIPT TO ANALYZE:",
                formatted_transcript,
            ]
        )

        return "\n".join(lines)

    def _build_podcast_facts_system_prompt(self) -> str:
        """Build system prompt for initial podcast facts extraction."""
        return """You are an expert at analyzing podcast transcripts to extract PERMANENT facts about the podcast.

CRITICAL: Only extract facts that are TRUE FOR EVERY EPISODE of this podcast, not episode-specific content.

Based on the transcript and metadata provided, extract PODCAST-LEVEL facts:

1. HOSTS: Regular hosts who appear in EVERY or MOST episodes
   - Format: "Name - Role/Description"
   - Only include permanent hosts, NOT one-time guests

2. RECURRING ROLES: Non-host roles that appear regularly (e.g., "Ad Narrator")
   - Only include if they appear across multiple episodes

3. KNOWN GUESTS: Leave this EMPTY for initial extraction
   - Guests are episode-specific and belong in episode facts
   - This field is for manually tracking frequent/notable guests later

4. SPONSORS: Leave this EMPTY for initial extraction
   - Sponsors change per episode and belong in episode facts
   - This field is for manually tracking long-term sponsors later

5. KEYWORDS: Only include PERMANENT terms related to the podcast itself:
   - The podcast name and network (e.g., "Prof G Markets", "Vox Media Podcast Network")
   - Common speech-to-text errors for recurring terms
   - Format: "Term (often misheard as X)" if applicable
   - DO NOT include episode-specific topics, people mentioned, or news items

6. STYLE NOTES: General style guidance for the podcast
   - Do NOT force a specific English variant
   - Note the natural speaking style of the hosts
   - Example: "Preserve original speaking style", "Hosts use first names"

Return your analysis as JSON:
{
  "hosts": ["Name - Description"],
  "recurring_roles": ["Ad Narrator - Reads sponsor segments"],
  "known_guests": [],
  "sponsors": [],
  "keywords": ["Podcast Name (common mishearing)"],
  "style_notes": ["Preserve original speaking style"]
}"""

    def _build_podcast_facts_user_prompt(
        self,
        formatted_transcript: str,
        podcast_title: str,
        podcast_description: str,
        episode_facts: EpisodeFacts,
    ) -> str:
        """Build user prompt for initial podcast facts extraction."""
        lines = [
            "PODCAST METADATA:",
            f"Podcast: {podcast_title}",
            f"Description: {podcast_description}",
            "",
            "EPISODE FACTS ALREADY EXTRACTED:",
            f"Speaker Mapping: {json.dumps(episode_facts.speaker_mapping, indent=2)}",
            f"Guests: {episode_facts.guests}",
            f"Topics: {episode_facts.topics_keywords}",
            f"Ad Sponsors: {episode_facts.ad_sponsors}",
            "",
            "TRANSCRIPT (for additional context):",
            formatted_transcript[:50000],  # Limit transcript size for this call
        ]

        return "\n".join(lines)
