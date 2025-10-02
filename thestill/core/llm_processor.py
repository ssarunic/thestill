import json
import time
from datetime import datetime
from pathlib import Path
from typing import Dict, List, Optional

from ..models.podcast import Quote, ProcessedContent
from .transcript_compactor import TranscriptCompactor
from .llm_provider import LLMProvider


class LLMProcessor:
    def __init__(self, provider: LLMProvider):
        """
        Initialize LLM processor with a provider.

        Args:
            provider: LLMProvider instance (OpenAI or Ollama)
        """
        self.provider = provider
        self.compactor = TranscriptCompactor()

    def process_transcript(self, transcript_text: str, episode_guid: str,
                          output_path: str = None, transcript_json_path: str = None) -> Optional[ProcessedContent]:
        """Process raw transcript through LLM pipeline using compacted Markdown"""
        try:
            start_time = time.time()

            # If we have the JSON path, compact it first for token savings
            markdown_text = transcript_text
            if transcript_json_path and Path(transcript_json_path).exists():
                print("Compacting transcript to Markdown for token efficiency...")

                # Generate output paths for pruned JSON and markdown
                base_path = Path(transcript_json_path).parent.parent
                transcript_name = Path(transcript_json_path).stem

                pruned_json_path = base_path / "transcripts" / f"{transcript_name}_pruned.json"
                markdown_path = base_path / "transcripts" / f"{transcript_name}.md"

                compact_result = self.compactor.compact_transcript(
                    transcript_json_path,
                    output_md_path=str(markdown_path),
                    output_json_path=str(pruned_json_path)
                )

                markdown_text = compact_result["markdown"]
                print(f"Token savings: ~{compact_result['token_savings_estimate']}% "
                      f"({compact_result['original_chars']} → {compact_result['markdown_chars']} chars)")

            print("Step 1: Cleaning transcript and detecting ads...")
            cleaned_result = self._clean_and_detect_ads(markdown_text)

            print("Step 2: Generating summary...")
            summary = self._generate_summary(cleaned_result["cleaned_transcript"])

            print("Step 3: Extracting quotes...")
            quotes = self._extract_quotes(cleaned_result["cleaned_transcript"])

            processing_time = time.time() - start_time

            processed_content = ProcessedContent(
                episode_guid=episode_guid,
                cleaned_transcript=cleaned_result["cleaned_transcript"],
                summary=summary,
                quotes=quotes,
                ad_segments=cleaned_result["ad_segments"],
                processing_time=processing_time,
                created_at=datetime.now()
            )

            if output_path:
                self._save_processed_content(processed_content, output_path)

            print(f"LLM processing completed in {processing_time:.1f} seconds")
            return processed_content

        except Exception as e:
            print(f"Error processing transcript: {e}")
            return None

    def _clean_and_detect_ads(self, transcript: str) -> Dict:
        """Clean transcript and detect advertisement segments"""
        prompt = """
You are a transcript cleaning specialist. Your task is to:

1. Remove filler words like "um", "uh", "you know", "like" (when used as filler)
2. Fix obvious transcription errors based on context
3. Identify advertisement segments and mark them clearly
4. Preserve the natural flow and meaning of the conversation
5. Keep timestamps intact

Advertisement segments typically include:
- Product endorsements or sponsorship mentions
- Discount codes or special offers
- "This episode is brought to you by..."
- Clear promotional language

IMPORTANT: You MUST respond with ONLY valid JSON. Do not include any explanatory text before or after the JSON.

JSON Schema:
{
  "type": "object",
  "properties": {
    "cleaned_transcript": {"type": "string"},
    "ad_segments": {
      "type": "array",
      "items": {
        "type": "object",
        "properties": {
          "start_marker": {"type": "string"},
          "end_marker": {"type": "string"},
          "content": {"type": "string"},
          "type": {"type": "string", "enum": ["sponsorship", "product_placement", "promotion"]}
        },
        "required": ["start_marker", "end_marker", "content", "type"]
      }
    }
  },
  "required": ["cleaned_transcript", "ad_segments"]
}

Example response:
{
  "cleaned_transcript": "[00:00] Welcome to the podcast. Today we discuss AI and its impact on society. The technology is rapidly evolving and changing how we work.",
  "ad_segments": [
    {
      "start_marker": "[00:15]",
      "end_marker": "[00:45]",
      "content": "This episode is brought to you by TechCorp. Use code PODCAST20 for 20% off your first order.",
      "type": "sponsorship"
    }
  ]
}

Here's the transcript to process:
"""

        try:
            messages = [
                {"role": "system", "content": prompt},
                {"role": "user", "content": transcript}
            ]

            content = self.provider.chat_completion(
                messages=messages,
                temperature=0.1,
                max_tokens=4000
            )

            # Check for empty content
            if not content:
                print("Warning: Provider returned empty content")
                return {
                    "cleaned_transcript": transcript,
                    "ad_segments": []
                }

            content = content.strip()

            # Try to extract JSON from the response if it's wrapped in code blocks
            if "```json" in content:
                start = content.find("```json") + 7
                end = content.find("```", start)
                if end != -1:
                    content = content[start:end].strip()
            elif "```" in content:
                start = content.find("```") + 3
                end = content.find("```", start)
                if end != -1:
                    content = content[start:end].strip()

            result = json.loads(content)
            return result

        except json.JSONDecodeError as e:
            print(f"Warning: Failed to parse LLM response as JSON: {e}")
            print(f"Raw response: {content[:500] if content else '(empty)'}...")
            return {
                "cleaned_transcript": transcript,
                "ad_segments": []
            }
        except Exception as e:
            print(f"Error in transcript cleaning: {e}")
            return {
                "cleaned_transcript": transcript,
                "ad_segments": []
            }

    def _generate_summary(self, cleaned_transcript: str) -> str:
        """Generate comprehensive episode summary"""
        prompt = """
You are an expert podcast summarizer. Create a comprehensive but concise summary of this podcast episode.

Your summary should:
1. Start with a brief one-sentence overview
2. Cover the main topics and key points discussed
3. Highlight important insights, conclusions, or takeaways
4. Mention any notable guests or experts featured
5. Be well-structured and easy to read
6. Be approximately 200-400 words
7. Ignore any advertisement segments

Focus on substance and insights that would help someone decide if they want to listen to the full episode.

Here's the transcript:
"""

        try:
            messages = [
                {"role": "system", "content": prompt},
                {"role": "user", "content": cleaned_transcript}
            ]

            response = self.provider.chat_completion(
                messages=messages,
                temperature=0.4,
                max_tokens=600
            )

            return response.strip()

        except Exception as e:
            print(f"Error generating summary: {e}")
            return "Summary generation failed."

    def _extract_quotes(self, cleaned_transcript: str) -> List[Quote]:
        """Extract and analyze notable quotes"""
        prompt = """
You are a quote extraction specialist. From this podcast transcript, identify 3-5 of the most notable, impactful, or insightful quotes.

For each quote, provide:
1. The exact quote text
2. The speaker (if identifiable from context)
3. Why this quote is significant or impactful

Focus on quotes that:
- Contain key insights or wisdom
- Are memorable or thought-provoking
- Represent important conclusions or perspectives
- Could stand alone as valuable takeaways
- Avoid advertisement content

IMPORTANT: You MUST respond with ONLY valid JSON. Do not include any explanatory text before or after the JSON.

JSON Schema:
{
  "type": "object",
  "properties": {
    "quotes": {
      "type": "array",
      "items": {
        "type": "object",
        "properties": {
          "text": {"type": "string"},
          "speaker": {"type": ["string", "null"]},
          "significance": {"type": "string"}
        },
        "required": ["text", "speaker", "significance"]
      }
    }
  },
  "required": ["quotes"]
}

Example response:
{
  "quotes": [
    {
      "text": "The future belongs to those who believe in the beauty of their dreams.",
      "speaker": "Eleanor Roosevelt",
      "significance": "This quote emphasizes the power of having vision and believing in oneself to achieve meaningful goals."
    },
    {
      "text": "Innovation distinguishes between a leader and a follower.",
      "speaker": null,
      "significance": "Highlights how creative thinking and willingness to try new approaches separates successful leaders from those who merely react."
    }
  ]
}

Here's the transcript:
"""

        try:
            messages = [
                {"role": "system", "content": prompt},
                {"role": "user", "content": cleaned_transcript}
            ]

            content = self.provider.chat_completion(
                messages=messages,
                temperature=0.3,
                max_tokens=1000
            ).strip()

            # Try to extract JSON from the response if it's wrapped in code blocks
            if "```json" in content:
                start = content.find("```json") + 7
                end = content.find("```", start)
                if end != -1:
                    content = content[start:end].strip()
            elif "```" in content:
                start = content.find("```") + 3
                end = content.find("```", start)
                if end != -1:
                    content = content[start:end].strip()

            result = json.loads(content)
            quotes = []

            for quote_data in result.get("quotes", []):
                quote = Quote(
                    text=quote_data.get("text", ""),
                    speaker=quote_data.get("speaker"),
                    significance=quote_data.get("significance", "")
                )
                quotes.append(quote)

            return quotes

        except json.JSONDecodeError as e:
            print(f"Warning: Failed to parse quotes response as JSON: {e}")
            print(f"Raw response: {content[:500]}...")
            return []
        except Exception as e:
            print(f"Error extracting quotes: {e}")
            return []

    def _save_processed_content(self, content: ProcessedContent, output_path: str):
        """Save processed content to JSON file"""
        try:
            Path(output_path).parent.mkdir(parents=True, exist_ok=True)
            with open(output_path, 'w', encoding='utf-8') as f:
                json.dump(content.model_dump(mode='json'), f, indent=2, ensure_ascii=False)
            print(f"Processed content saved to: {output_path}")
        except Exception as e:
            print(f"Error saving processed content: {e}")

    def estimate_cost(self, transcript_length: int) -> float:
        """Estimate processing cost based on transcript length"""
        # Rough estimates for GPT-4 pricing (per 1K tokens)
        # Input: $0.01, Output: $0.03

        estimated_input_tokens = transcript_length // 4  # ~4 chars per token
        estimated_output_tokens = 2000  # Summary + quotes + cleaned transcript

        input_cost = (estimated_input_tokens / 1000) * 0.01
        output_cost = (estimated_output_tokens / 1000) * 0.03

        return input_cost + output_cost