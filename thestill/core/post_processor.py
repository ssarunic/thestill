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
Enhanced post-processor for podcast transcripts using LLM.
Produces cleaned Markdown transcripts with notable quotes and social snippets.
"""

import json
import time
from pathlib import Path
from typing import Dict, List, NamedTuple, Optional

from .llm_provider import LLMProvider


class ModelLimits(NamedTuple):
    """Rate limits for OpenAI models"""

    tpm: int  # Tokens per minute
    rpm: int  # Requests per minute
    tpd: int  # Tokens per day
    context_window: int  # Maximum context window size
    supports_temperature: bool = True  # Whether model supports custom temperature


# Model rate limits and context windows
MODEL_CONFIGS = {
    # OpenAI models
    "gpt-5": ModelLimits(tpm=500000, rpm=500, tpd=1500000, context_window=128000, supports_temperature=False),
    "gpt-5-mini": ModelLimits(tpm=500000, rpm=500, tpd=5000000, context_window=128000, supports_temperature=False),
    "gpt-5-nano": ModelLimits(tpm=200000, rpm=500, tpd=2000000, context_window=128000, supports_temperature=False),
    "gpt-4.1": ModelLimits(tpm=30000, rpm=500, tpd=900000, context_window=128000, supports_temperature=True),
    "gpt-4.1-mini": ModelLimits(tpm=200000, rpm=500, tpd=2000000, context_window=128000, supports_temperature=True),
    "gpt-4.1-nano": ModelLimits(tpm=200000, rpm=500, tpd=2000000, context_window=128000, supports_temperature=True),
    "o3": ModelLimits(tpm=30000, rpm=500, tpd=90000, context_window=128000, supports_temperature=True),
    "o4-mini": ModelLimits(tpm=200000, rpm=500, tpd=2000000, context_window=128000, supports_temperature=True),
    "gpt-4o": ModelLimits(tpm=30000, rpm=500, tpd=90000, context_window=128000, supports_temperature=True),
    "gpt-4o-mini": ModelLimits(tpm=200000, rpm=500, tpd=2000000, context_window=128000, supports_temperature=True),
    "gpt-4-turbo": ModelLimits(tpm=30000, rpm=500, tpd=90000, context_window=128000, supports_temperature=True),
    "gpt-4-turbo-preview": ModelLimits(tpm=30000, rpm=500, tpd=90000, context_window=128000, supports_temperature=True),
    # Ollama/Gemma 3 models (no rate limits for local inference)
    # Using very high tpm/rpm/tpd since there are no actual limits
    "gemma3:270m": ModelLimits(tpm=1000000, rpm=10000, tpd=100000000, context_window=32000, supports_temperature=True),
    "gemma3:1b": ModelLimits(tpm=1000000, rpm=10000, tpd=100000000, context_window=32000, supports_temperature=True),
    "gemma3:4b": ModelLimits(tpm=1000000, rpm=10000, tpd=100000000, context_window=128000, supports_temperature=True),
    "gemma3:12b": ModelLimits(tpm=1000000, rpm=10000, tpd=100000000, context_window=128000, supports_temperature=True),
    "gemma3:27b": ModelLimits(tpm=1000000, rpm=10000, tpd=100000000, context_window=128000, supports_temperature=True),
}


class PostProcessorConfig:
    """Configuration for post-processing options"""

    def __init__(
        self,
        add_timestamps: bool = True,
        make_audio_links: bool = False,
        audio_base_url: str = "",
        speaker_map: Optional[Dict[str, str]] = None,
        filler_words: Optional[List[str]] = None,
        ad_detect_patterns: Optional[List[str]] = None,
        table_layout_for_snappy_sections: bool = True,
    ):
        self.add_timestamps = add_timestamps
        self.make_audio_links = make_audio_links
        self.audio_base_url = audio_base_url
        self.speaker_map = speaker_map or {}
        self.filler_words = filler_words or [
            "ah",
            "uh",
            "um",
            "erm",
            "mmm",
            "like",
            "you know",
            "sort of",
            "kind of",
            "I mean",
            "right",
            "okay",
            "yeah",
        ]
        self.ad_detect_patterns = ad_detect_patterns or []
        self.table_layout_for_snappy_sections = table_layout_for_snappy_sections


class EnhancedPostProcessor:
    """Enhanced LLM-based post-processor for podcast transcripts"""

    SYSTEM_PROMPT = """You are a transcript post-processor. Write in British English. Use simple, conversational sentences. Do not use the em dash character.

Goal
Given a podcast transcript in JSON, produce a clean, readable Markdown article of the episode with ads marked, intro and outro separated, obvious transcription errors fixed, noise words removed, section-level timestamps if requested, highlight notable quotes, and generate ready-to-use social media snippets.

Input format
You will receive one JSON object. It may have either text as a single string or segments as a list of timecoded chunks.

Processing rules
1. Normalise speakers using SPEAKER_MAP and bold their names.
2. Remove FILLER_WORDS when they don't carry meaning.
3. Add punctuation and correct obvious grammar slips.
4. Detect and label [AD], [INTRO], [OUTRO].
5. Add section headings and optional timestamps.
6. Keep the conversational tone.
7. Use British English spelling.
8. For notable quotes and snippets, focus on the guest but include hosts if they say something impactful.

Outputs
Return three blocks:
1. # Cleaned transcript (Markdown)
   • Ads marked with [AD]
   • Intro/outro clearly separated
   • Headings with timestamps if enabled
   • Paragraph format for long dialogue; table layout for snappy ads if enabled
2. # Notable quotes
   • 5–8 of the best quotes, in blockquote format (>) with attribution
3. # Suggested social snippets
   • 5–7 short posts
   • Half styled for Twitter/X (≤280 characters)
   • Half styled for LinkedIn (1–3 sentences, more context allowed)
   • Use relevant hashtags
   • Add audio link if AUDIO_BASE_URL is provided"""

    def __init__(self, provider: LLMProvider, max_tokens: Optional[int] = None):
        """
        Initialize enhanced post-processor with an LLM provider.

        Args:
            provider: LLMProvider instance (OpenAI or Ollama)
            max_tokens: Maximum tokens per chunk (optional, auto-calculated if not provided)
        """
        self.provider = provider
        model = provider.get_model_name()

        # Get model limits and calculate optimal chunk size
        self.model_limits = MODEL_CONFIGS.get(model)
        if self.model_limits is None:
            print(f"⚠️  Warning: Model '{model}' not in config table. Using conservative defaults.")
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

        # Calculate delay between chunks based on provider type
        # Ollama has no rate limits, so no delay needed
        from .llm_provider import OllamaProvider

        if isinstance(provider, OllamaProvider):
            self.chunk_delay = 0
        else:
            # For cloud providers, respect TPM limits
            self.chunk_delay = 60 if self.model_limits.tpm < 100000 else 10

        print(f"📊 Model: {model}")
        print(f"   TPM Limit: {self.model_limits.tpm:,} | Max chunk size: {self.max_tokens:,} tokens")
        if self.chunk_delay > 0:
            print(f"   Delay between chunks: {self.chunk_delay}s")
        else:
            print("   No delay between chunks (local inference)")

    def _build_options_string(self, config: PostProcessorConfig) -> str:
        """Build the OPTIONS section for the prompt"""
        speaker_map_str = json.dumps(config.speaker_map, indent=2)
        filler_words_str = json.dumps(config.filler_words)

        options = f"""OPTIONS TO SET BEFORE THE JSON

ADD_TIMESTAMPS={str(config.add_timestamps).lower()}
MAKE_AUDIO_LINKS={str(config.make_audio_links).lower()}
AUDIO_BASE_URL={config.audio_base_url}
TABLE_LAYOUT_FOR_SNAPPY_SECTIONS={str(config.table_layout_for_snappy_sections).lower()}
SPEAKER_MAP = {speaker_map_str}
FILLER_WORDS = {filler_words_str}"""

        if config.ad_detect_patterns:
            options += f"\nAD_DETECT_PATTERNS = {json.dumps(config.ad_detect_patterns)}"

        return options

    def _estimate_tokens(self, text: str) -> int:
        """Rough token estimation: ~4 chars per token"""
        return len(text) // 4

    def _chunk_transcript(self, transcript_data: Dict, config: PostProcessorConfig) -> List[Dict]:
        """Split large transcripts into processable chunks"""
        # Check if we have segments (timestamped) or plain text
        if "segments" in transcript_data:
            return self._chunk_by_segments(transcript_data, config)
        return self._chunk_by_text(transcript_data, config)

    def _chunk_by_segments(self, transcript_data: Dict, config: PostProcessorConfig) -> List[Dict]:
        """Chunk transcript by segments for timestamped data"""
        segments = transcript_data.get("segments", [])
        if not segments:
            return [transcript_data]

        chunks = []
        current_chunk = {"text": transcript_data.get("text", ""), "segments": []}
        current_tokens = 0
        options_str = self._build_options_string(config)
        overhead_tokens = self._estimate_tokens(options_str + self.SYSTEM_PROMPT)

        for segment in segments:
            segment_json = json.dumps(segment)
            segment_tokens = self._estimate_tokens(segment_json)

            if current_tokens + segment_tokens + overhead_tokens > self.max_tokens and current_chunk["segments"]:
                # Save current chunk and start new one
                chunks.append(current_chunk.copy())
                current_chunk = {"text": "", "segments": []}
                current_tokens = 0

            current_chunk["segments"].append(segment)
            current_tokens += segment_tokens

        # Add final chunk
        if current_chunk["segments"]:
            chunks.append(current_chunk)

        if len(chunks) > 1:
            return chunks
        return [transcript_data]

    def _chunk_by_text(self, transcript_data: Dict, config: PostProcessorConfig) -> List[Dict]:
        """Chunk transcript by text for non-timestamped data"""
        text = transcript_data.get("text", "")
        options_str = self._build_options_string(config)
        overhead_tokens = self._estimate_tokens(options_str + self.SYSTEM_PROMPT)

        # Calculate available tokens for text
        available_tokens = self.max_tokens - overhead_tokens
        max_chars = available_tokens * 4  # Rough estimation

        if len(text) <= max_chars:
            return [transcript_data]

        # Split by paragraphs/sentences
        chunks = []
        sentences = text.split(". ")
        current_chunk_text = ""

        for sentence in sentences:
            if len(current_chunk_text) + len(sentence) > max_chars and current_chunk_text:
                chunks.append({"text": current_chunk_text.strip()})
                current_chunk_text = sentence + ". "
            else:
                current_chunk_text += sentence + ". "

        if current_chunk_text:
            chunks.append({"text": current_chunk_text.strip()})

        return chunks

    def _process_single_chunk(
        self, chunk_data: Dict, config: PostProcessorConfig, chunk_num: int, total_chunks: int
    ) -> str:
        """Process a single transcript chunk"""
        options_str = self._build_options_string(config)
        transcript_json = json.dumps(chunk_data, indent=2)

        user_message = f"""{options_str}

━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━

JSON TRANSCRIPT STARTS BELOW THIS LINE"""

        if total_chunks > 1:
            user_message += f"\n\n[CHUNK {chunk_num}/{total_chunks}]\n"

        user_message += f"\n{transcript_json}"

        try:
            messages = [{"role": "system", "content": self.SYSTEM_PROMPT}, {"role": "user", "content": user_message}]

            # Use temperature if model supports it
            temperature = 0.3 if self.model_limits.supports_temperature else None

            response = self.provider.chat_completion(messages=messages, temperature=temperature)
            return response

        except Exception as e:
            print(f"Error processing chunk {chunk_num}/{total_chunks}: {e}")
            raise

    def _combine_chunk_results(self, chunk_outputs: List[str]) -> str:
        """Combine multiple chunk outputs into a single coherent result"""
        if len(chunk_outputs) == 1:
            return chunk_outputs[0]

        # Parse each chunk
        all_transcripts = []
        all_quotes = []
        all_snippets = []

        for output in chunk_outputs:
            parsed = self._parse_output(output)
            if parsed["cleaned_transcript"]:
                all_transcripts.append(parsed["cleaned_transcript"])
            if parsed["notable_quotes"]:
                all_quotes.append(parsed["notable_quotes"])
            if parsed["social_snippets"]:
                all_snippets.append(parsed["social_snippets"])

        # Combine into single markdown document
        combined = "# Cleaned transcript\n\n"
        combined += "\n\n".join(all_transcripts)
        combined += "\n\n# Notable quotes\n\n"
        combined += "\n\n".join(all_quotes)
        combined += "\n\n# Suggested social snippets\n\n"
        combined += "\n\n".join(all_snippets)

        return combined

    def process_transcript(
        self, transcript_data: Dict, config: Optional[PostProcessorConfig] = None, output_path: Optional[str] = None
    ) -> Dict:
        """
        Process a transcript with enhanced LLM post-processing.
        Automatically chunks large transcripts to avoid rate limits.

        Args:
            transcript_data: The raw transcript JSON from transcriber
            config: Post-processing configuration options
            output_path: Optional path to save the processed output

        Returns:
            Dict with keys: cleaned_transcript, notable_quotes, social_snippets
        """
        if config is None:
            config = PostProcessorConfig()

        # Estimate tokens and chunk if necessary
        transcript_json = json.dumps(transcript_data)
        estimated_tokens = self._estimate_tokens(transcript_json)

        print(f"Processing transcript with {self.provider.get_model_name()}...")
        print(f"Estimated tokens: ~{estimated_tokens:,}")

        chunks = self._chunk_transcript(transcript_data, config)

        if len(chunks) > 1:
            print(f"⚠️  Large transcript detected. Splitting into {len(chunks)} chunks...")

        try:
            chunk_outputs = []

            for i, chunk in enumerate(chunks, 1):
                if len(chunks) > 1:
                    print(f"Processing chunk {i}/{len(chunks)}...")

                output_text = self._process_single_chunk(chunk, config, i, len(chunks))
                chunk_outputs.append(output_text)

                # Add delay between chunks to respect rate limits (only for cloud providers)
                if i < len(chunks) and self.chunk_delay > 0:
                    print(f"Waiting {self.chunk_delay}s before next chunk...")
                    time.sleep(self.chunk_delay)

            # Combine results if multiple chunks
            final_output = self._combine_chunk_results(chunk_outputs)

            # Parse the three sections from the output
            result = self._parse_output(final_output)

            # Save if output path provided
            if output_path:
                self._save_processed_output(result, output_path)

            print("Post-processing completed successfully")
            return result

        except Exception as e:
            print(f"Error during post-processing: {e}")
            raise

    def _parse_output(self, output_text: str) -> Dict:
        """Parse the LLM output into structured sections"""
        sections = {"cleaned_transcript": "", "notable_quotes": "", "social_snippets": "", "full_output": output_text}

        # Simple section splitting based on markdown headers
        lines = output_text.split("\n")
        current_section = None
        current_content = []

        for line in lines:
            lower_line = line.lower().strip()

            if lower_line.startswith("# cleaned transcript"):
                if current_section:
                    sections[current_section] = "\n".join(current_content).strip()
                current_section = "cleaned_transcript"
                current_content = []
            elif lower_line.startswith("# notable quotes"):
                if current_section:
                    sections[current_section] = "\n".join(current_content).strip()
                current_section = "notable_quotes"
                current_content = []
            elif lower_line.startswith("# suggested social snippets") or lower_line.startswith("# social snippets"):
                if current_section:
                    sections[current_section] = "\n".join(current_content).strip()
                current_section = "social_snippets"
                current_content = []
            else:
                current_content.append(line)

        # Don't forget the last section
        if current_section:
            sections[current_section] = "\n".join(current_content).strip()

        return sections

    def _save_processed_output(self, result: Dict, output_path: str):
        """Save the processed output to files"""
        output_path = Path(output_path)
        output_path.parent.mkdir(parents=True, exist_ok=True)

        # Save full output as markdown
        full_md_path = output_path.with_suffix(".md")
        with open(full_md_path, "w", encoding="utf-8") as f:
            f.write(result["full_output"])

        # Save structured JSON
        json_path = output_path.with_suffix(".json")
        with open(json_path, "w", encoding="utf-8") as f:
            json.dump(
                {
                    "cleaned_transcript": result["cleaned_transcript"],
                    "notable_quotes": result["notable_quotes"],
                    "social_snippets": result["social_snippets"],
                },
                f,
                indent=2,
                ensure_ascii=False,
            )

        print(f"Processed output saved to {full_md_path} and {json_path}")
