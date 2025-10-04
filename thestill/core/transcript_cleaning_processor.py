"""
Transcript cleaning processor focused on accuracy and readability.
Acts as a copywriter to fix spelling, grammar, remove filler words, and identify speakers.
"""

import json
import time
from pathlib import Path
from typing import Dict, List, Optional
from .llm_provider import LLMProvider
from .transcript_formatter import TranscriptFormatter


class TranscriptCleaningProcessor:
    """LLM-based transcript cleaner with copywriting focus"""

    def __init__(self, provider: LLMProvider, chunk_size: int = 200000):
        """
        Initialize transcript cleaning processor with an LLM provider.

        Args:
            provider: LLMProvider instance (OpenAI, Ollama, or Gemini)
            chunk_size: Maximum characters per chunk for processing (default: 200000)
                       Gemini Flash 2.5: 1M input tokens (~4M chars), so 200K is safe
                       OpenAI/Ollama: May need lower values (15K-30K)
        """
        self.provider = provider
        self.formatter = TranscriptFormatter()
        self.chunk_size = chunk_size  # Characters, not tokens (rough estimate: 4 chars = 1 token)

    def clean_transcript(
        self,
        transcript_data: Dict,
        podcast_title: str = "",
        podcast_description: str = "",
        episode_title: str = "",
        episode_description: str = "",
        output_path: Optional[str] = None,
        save_corrections: bool = True
    ) -> Dict:
        """
        Clean a transcript with focus on accuracy and readability.

        Args:
            transcript_data: Raw transcript JSON from transcriber
            podcast_title: Title of the podcast
            podcast_description: Description of the podcast
            episode_title: Title of the episode
            episode_description: Description of the episode
            output_path: Optional path to save outputs
            save_corrections: Whether to save corrections list for debugging

        Returns:
            Dict with keys: corrections, speaker_mapping, cleaned_markdown, processing_time
        """
        start_time = time.time()

        try:
            # Phase 0: Format JSON to clean Markdown (efficient for LLM)
            print("Phase 0: Formatting transcript to clean Markdown...")
            formatted_markdown = self.formatter.format_transcript(transcript_data, episode_title)

            # Save formatted markdown for inspection
            if output_path:
                formatted_path = Path(output_path).parent / f"{Path(output_path).stem}_formatted.md"
                with open(formatted_path, 'w', encoding='utf-8') as f:
                    f.write(formatted_markdown)
                print(f"  Formatted markdown saved to: {formatted_path}")

            # Phase 1: Analyze and create corrections list
            print("Phase 1: Analyzing transcript and identifying corrections...")
            corrections = self._analyze_and_correct(
                formatted_markdown,
                podcast_title,
                podcast_description,
                episode_title,
                episode_description
            )

            # Save corrections immediately
            if output_path and save_corrections:
                self._save_phase_output(output_path, "corrections", corrections)

            # Phase 1.5: Apply corrections before speaker identification
            print("Phase 1.5: Applying corrections to improve speaker name accuracy...")
            corrected_markdown = self._apply_corrections(formatted_markdown, corrections)

            # Save corrected markdown immediately
            if output_path:
                self._save_phase_output(output_path, "corrected", corrected_markdown)

            # Phase 2: Identify speakers (using corrected transcript)
            print("Phase 2: Identifying speakers...")
            speaker_mapping = self._identify_speakers(
                corrected_markdown,
                podcast_title,
                podcast_description,
                episode_title,
                episode_description
            )

            # Save speaker mapping immediately
            if output_path:
                self._save_phase_output(output_path, "speakers", speaker_mapping)

            # Phase 3: Generate final cleaned transcript
            print("Phase 3: Generating final cleaned transcript...")
            cleaned_markdown = self._generate_cleaned_transcript(
                formatted_markdown,
                corrections,
                speaker_mapping,
                episode_title
            )

            # Save final cleaned transcript immediately
            if output_path:
                self._save_phase_output(output_path, "cleaned", cleaned_markdown)

            processing_time = time.time() - start_time

            result = {
                "corrections": corrections,
                "speaker_mapping": speaker_mapping,
                "cleaned_markdown": cleaned_markdown,
                "processing_time": processing_time,
                "episode_title": episode_title,
                "podcast_title": podcast_title
            }

            # Save outputs if path provided
            if output_path:
                self._save_outputs(result, output_path, save_corrections)

            print(f"Transcript cleaning completed in {processing_time:.1f} seconds")
            return result

        except Exception as e:
            print(f"Error cleaning transcript: {e}")
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
        lines = text.split('\n')
        current_chunk = []
        current_size = 0

        for line in lines:
            line_size = len(line) + 1  # +1 for newline

            if current_size + line_size > self.chunk_size and current_chunk:
                # Save current chunk and start new one
                chunks.append('\n'.join(current_chunk))
                current_chunk = [line]
                current_size = line_size
            else:
                current_chunk.append(line)
                current_size += line_size

        # Add remaining chunk
        if current_chunk:
            chunks.append('\n'.join(current_chunk))

        return chunks

    def _analyze_and_correct(
        self,
        formatted_markdown: str,
        podcast_title: str,
        podcast_description: str,
        episode_title: str,
        episode_description: str
    ) -> List[Dict]:
        """Phase 1: Analyze transcript and identify all corrections needed"""

        # Markdown is already clean and ready for LLM
        transcript_text = formatted_markdown

        system_prompt = """You are an expert copywriter and editor specialising in podcast transcripts.

Your task is to analyze the transcript and identify ALL corrections needed for:
1. Spelling errors (especially technical terms, names, brands)
2. Grammar mistakes
3. Filler words to remove (um, uh, like, you know, etc.) - only when they don't add meaning
4. Punctuation improvements
5. Advertisement segments to mark

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
          "type": {"type": "string", "enum": ["spelling", "grammar", "filler", "punctuation", "ad_segment"]},
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
    },
    {
      "type": "ad_segment",
      "original": "This episode is brought to you by ExpressVPN",
      "corrected": "[AD]"
    }
  ]
}

If no corrections are needed, return: {"corrections": []}"""

        # Split transcript into chunks if needed
        chunks = self._chunk_transcript(transcript_text)
        all_corrections = []

        for i, chunk in enumerate(chunks):
            chunk_info = f" (chunk {i+1}/{len(chunks)})" if len(chunks) > 1 else ""
            print(f"  Processing{chunk_info}...")

            context_info = f"""PODCAST CONTEXT:
Podcast: {podcast_title}
About: {podcast_description}

Episode: {episode_title}
Description: {episode_description}

TRANSCRIPT TO ANALYZE{chunk_info}:
{chunk}"""

            try:
                messages = [
                    {"role": "system", "content": system_prompt},
                    {"role": "user", "content": context_info}
                ]

                response = self.provider.chat_completion(
                    messages=messages,
                    temperature=0.1,
                    max_tokens=65000,  # Gemini Flash 2.5 supports up to 65K output tokens
                    response_format={"type": "json_object"}
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
                chunk_corrections = result.get("corrections", [])
                all_corrections.extend(chunk_corrections)

            except Exception as e:
                print(f"  Error analyzing chunk {i+1}: {e}")
                continue

        print(f"  Found {len(all_corrections)} corrections across {len(chunks)} chunk(s)")
        return all_corrections

    def _apply_corrections(self, transcript_text: str, corrections: List[Dict]) -> str:
        """
        Apply corrections from Phase 1 to the transcript text.
        This ensures speaker names are properly spelled before speaker identification.

        Args:
            transcript_text: Original transcript markdown
            corrections: List of correction objects from Phase 1

        Returns:
            Corrected transcript text
        """
        corrected_text = transcript_text

        # Sort corrections by type priority (spelling first, then grammar, then fillers)
        priority_order = {"spelling": 1, "grammar": 2, "punctuation": 3, "filler": 4, "ad_segment": 5}
        sorted_corrections = sorted(
            corrections,
            key=lambda c: priority_order.get(c.get("type", ""), 99)
        )

        applied_count = 0
        for correction in sorted_corrections:
            original = correction.get("original", "")
            corrected = correction.get("corrected", "")

            if not original:
                continue

            # Apply the correction (simple string replacement)
            # For more sophisticated replacement, we could use regex with word boundaries
            if original in corrected_text:
                corrected_text = corrected_text.replace(original, corrected)
                applied_count += 1

        print(f"  Applied {applied_count} corrections to transcript")
        return corrected_text

    def _identify_speakers(
        self,
        formatted_markdown: str,
        podcast_title: str,
        podcast_description: str,
        episode_title: str,
        episode_description: str
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
            print(f"  Using first and last chunk of {len(chunks)} chunks for speaker identification")
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
            messages = [
                {"role": "system", "content": system_prompt},
                {"role": "user", "content": context_info}
            ]

            response = self.provider.chat_completion(
                messages=messages,
                temperature=0.1,
                max_tokens=2000,  # Increased for Gemini's larger output capacity
                response_format={"type": "json_object"}
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
            print(f"Error identifying speakers: {e}")
            return {}

    def _generate_cleaned_transcript(
        self,
        formatted_markdown: str,
        corrections: List[Dict],
        speaker_mapping: Dict[str, str],
        episode_title: str
    ) -> str:
        """Phase 3: Generate final cleaned markdown transcript"""

        transcript_text = formatted_markdown

        # Build corrections summary for the LLM
        corrections_summary = "\n".join([
            f"- {c['type']}: '{c['original']}' → '{c['corrected']}'"
            for c in corrections[:100]  # Increased limit since we removed 'reason' field
        ])

        speaker_mapping_str = json.dumps(speaker_mapping, indent=2)

        system_prompt = """You are an expert copywriter specialising in podcast transcripts.

Your task is to produce a final, clean, readable Markdown transcript.

Apply these transformations:
1. Apply all spelling, grammar, and punctuation corrections provided
2. Remove filler words as indicated
3. Replace speaker labels (SPEAKER_00, SPEAKER_01, etc.) with real names from the mapping
4. Mark advertisement segments clearly with [AD] tag
5. Format as readable Markdown with proper paragraphs
6. Use British English spelling
7. Add section breaks for topic changes
8. Maintain conversational tone

STRICT FORMATTING RULES:
1. Each speaker turn MUST start on a new line with format: **Speaker Name:** followed by their dialogue
2. Do NOT use additional formatting like > blockquotes or bullet points for dialogue
3. Speaker name MUST be in bold using **Name:** format (not _Name:_ or other variations)
4. Separate different speaker turns with a single blank line
5. Group consecutive statements by the same speaker into a single paragraph
6. Use ## Heading for major topic changes (use sparingly, only for clear topic shifts)
7. Advertisement sections use format: **[ADVERTISEMENT]** followed by the ad content or summary
8. Do NOT add metadata, timestamps, or editorial comments - only the spoken content
9. Do NOT add a title or episode name at the top - start directly with the dialogue

EXAMPLE OUTPUT FORMAT:

## Introduction

**Rory Stewart:** Welcome back to The Rest Is Politics. I'm Rory Stewart, and I'm here with Alastair Campbell.

**Alastair Campbell:** Thanks, Rory. Today we're going to discuss the latest developments in British politics, particularly the upcoming general election and what it means for the Conservative Party.

**Rory Stewart:** Absolutely. Before we dive in, I think it's worth noting that the polls have been showing some really interesting trends over the past few weeks.

## General Election Discussion

**Alastair Campbell:** The key thing to understand is that Labour's lead has been remarkably stable. We're seeing about a 20-point gap, which is extraordinary by historical standards.

**Rory Stewart:** I agree. When I was in Parliament, even a 10-point lead would have been considered massive.

**[ADVERTISEMENT]**

This episode is brought to you by ExpressVPN. Protect your online privacy with military-grade encryption.

**Rory Stewart:** Right, let's get back to the election. What do you think about the regional variations we're seeing?

Focus on making it read smoothly while staying accurate to what was said. Output ONLY the formatted transcript with no preamble or postamble."""

        # Process in chunks if needed
        chunks = self._chunk_transcript(transcript_text)
        cleaned_chunks = []

        for i, chunk in enumerate(chunks):
            chunk_info = f" (chunk {i+1}/{len(chunks)})" if len(chunks) > 1 else ""
            print(f"  Generating cleaned transcript{chunk_info}...")

            user_message = f"""EPISODE: {episode_title}

SPEAKER MAPPING:
{speaker_mapping_str}

CORRECTIONS TO APPLY:
{corrections_summary}

ORIGINAL TRANSCRIPT{chunk_info}:
{chunk}

Please produce the final cleaned Markdown transcript."""

            try:
                messages = [
                    {"role": "system", "content": system_prompt},
                    {"role": "user", "content": user_message}
                ]

                response = self.provider.chat_completion(
                    messages=messages,
                    temperature=0.3,
                    max_tokens=32000  # Gemini Flash 2.5 can output up to 65K tokens
                )

                cleaned_chunks.append(response.strip())

            except Exception as e:
                print(f"  Error generating chunk {i+1}: {e}")
                # Fallback to original chunk
                cleaned_chunks.append(chunk)

        # Combine chunks with proper spacing
        final_transcript = "\n\n".join(cleaned_chunks)
        return final_transcript

    def _save_phase_output(self, output_path: str, phase: str, data):
        """
        Save output from a specific phase immediately after completion.

        Args:
            output_path: Base output path
            phase: Phase name (corrections, corrected, speakers, cleaned)
            data: Data to save (list, dict, or string)
        """
        output_path = Path(output_path)
        output_path.parent.mkdir(parents=True, exist_ok=True)

        if phase == "corrections":
            # Save corrections list as JSON
            path = output_path.parent / f"{output_path.stem}_corrections.json"
            with open(path, 'w', encoding='utf-8') as f:
                json.dump(data, f, indent=2, ensure_ascii=False)
            print(f"  → Corrections saved to: {path}")

        elif phase == "corrected":
            # Save corrected markdown
            path = output_path.parent / f"{output_path.stem}_corrected.md"
            with open(path, 'w', encoding='utf-8') as f:
                f.write(data)
            print(f"  → Corrected transcript saved to: {path}")

        elif phase == "speakers":
            # Save speaker mapping as JSON
            path = output_path.parent / f"{output_path.stem}_speakers.json"
            with open(path, 'w', encoding='utf-8') as f:
                json.dump(data, f, indent=2, ensure_ascii=False)
            print(f"  → Speaker mapping saved to: {path}")

        elif phase == "cleaned":
            # Save final cleaned markdown (without metadata header at this stage)
            path = output_path.parent / f"{output_path.stem}_cleaned.md"
            with open(path, 'w', encoding='utf-8') as f:
                f.write(data)
            print(f"  → Cleaned transcript saved to: {path}")

    def _save_outputs(self, result: Dict, output_path: str, save_corrections: bool):
        """Save final outputs with metadata to standard locations"""
        output_path = Path(output_path)
        output_path.parent.mkdir(parents=True, exist_ok=True)

        # Save final cleaned markdown with metadata header
        md_path = output_path.with_suffix('.md')
        with open(md_path, 'w', encoding='utf-8') as f:
            # Add metadata header
            f.write(f"# {result['episode_title']}\n\n")
            f.write(f"**Podcast:** {result['podcast_title']}\n\n")
            f.write("---\n\n")
            f.write(result['cleaned_markdown'])
        print(f"Final transcript saved to: {md_path}")

        # Save summary JSON
        summary = {
            "episode_title": result['episode_title'],
            "podcast_title": result['podcast_title'],
            "processing_time": result['processing_time'],
            "corrections_count": len(result['corrections']),
            "speakers_identified": len(result['speaker_mapping'])
        }
        summary_path = output_path.with_suffix('.json')
        with open(summary_path, 'w', encoding='utf-8') as f:
            json.dump(summary, f, indent=2, ensure_ascii=False)
        print(f"Summary saved to: {summary_path}")
