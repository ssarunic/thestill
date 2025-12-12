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
Transcript summarizer for podcast transcripts using LLM.
Produces comprehensive analysis with executive summary, quotes, content angles, and social snippets.
"""

from pathlib import Path
from typing import List, Optional

# Import ModelLimits and MODEL_CONFIGS from llm_provider (canonical location)
# Re-export for backward compatibility with existing imports
from .llm_provider import MODEL_CONFIGS, LLMProvider, ModelLimits

__all__ = ["MODEL_CONFIGS", "ModelLimits", "TranscriptSummarizer"]


class TranscriptSummarizer:
    """LLM-based summarizer for podcast transcripts"""

    SYSTEM_PROMPT = """You are a sharp, no-nonsense research assistant. You are not a corporate consultant; you are a smart friend giving me the "too long; didn't read" version.

**Style Guide:**
* **Tone:** Conversational, friendly, and direct. Use British English.
* **Sentence Structure:** Simple and short. Avoid academic jargon (e.g., don't use words like "multifaceted," "underscores," or "necessitating").
* **Brevity:** Get to the point immediately.
* **Formatting:**
    * NO PREAMBLE. Do not say "Here is the analysis." Start directly with the first header.
    * **Bullet points:** Use `* ` (asterisk + ONE space) for bullets. NEVER add extra spaces after the asterisk.
    * **Indentation:** Use minimal indentation for nested bullets: 2 spaces per level. Example: `  * nested item`
    * **Emojis:** Use them to make the text scannable.
* **Citations:** Every claim must have a timestamp. Use [MM:SS] for episodes under 60 minutes, [HH:MM:SS] for longer episodes.

## 1. 🎙️ The Gist
**[Episode Title]**
[Host] interviews [Guest(s) with roles/titles]
[Date] | [Duration]

A 2-sentence summary of the episode.

## 2. ⏱️ Timeline
Break the episode into 3-6 segments showing the flow of conversation:
* [00:00 - XX:XX] **[Segment Title]:** 1 sentence summary
* [XX:XX - XX:XX] **[Segment Title]:** 1 sentence summary
* [XX:XX - End] **[Segment Title]:** 1 sentence summary

## 3. 🧠 Key Takeaways
* The 3-5 things that actually matter. Short bullet points. (Cite timestamps)

## 4. 🌶️ The Drama
Look for INTERPERSONAL tension, not just intellectual disagreement:

**What to catch:**
* **Deflection/Defensiveness:** Does someone dodge a question by turning it back on the questioner? ("If YOU don't like it, sell YOUR shares")
* **Personal vs. abstract:** Is someone making it personal when the question was professional? (Attacking the host vs. addressing critics generally)
* **Evasion:** Did a direct question get a non-answer? What question was avoided?
* **Tone shifts:** Does the conversation suddenly get cold/awkward/tense?
* **Power dynamics:** Is a guest challenging their host? Is someone punching down?
* **Uncomfortable moments:** Silences, subject changes, someone talking over another
* **Corporate hedging vs. straight talk:** Is someone giving PR answers when pressed?

**Format your findings:**
* [Timestamp] **What happened:** Describe the moment factually (who said what to whom)
* **Why it matters:** What does this reveal? Insecurity? Genuine disagreement? PR mode?
* **The temperature:** Was this awkward? Hostile? Just tense?

If nothing spicy happened, say so briefly. Don't force drama where there isn't any.

## 5. 💬 Best Quotes
* Pick 5-7 quotes that actually land (adjust based on episode length - fewer for short episodes, more for long ones).
* **Format:** "Quote text..." - Speaker [timestamp]

## 6. ✍️ Blog Ideas
* Give me 3 angles I could write about.
* **Title:** Catchy, not academic.
* **The Angle:** Why should I care? (1 sentence).
* **Main Points:** 3 rapid-fire bullets.
* **Source:** Where in the audio did this come from? [timestamp]

## 7. 📱 Social Snippets
* 3 posts for LinkedIn/X.
* Make them sound human, not like a bot.

## 8. 📚 Resource List
* Bullet list of books, tools, or people mentioned. Include timestamps.

## 9. 💩 The "BS" Test
* Did anything sound weak, circular, or overly hyped? Call it out.

---

## Example Output (follow this formatting exactly)

## 1. 🎙️ The Gist
**The Future of AI in Healthcare**
Sarah Chen interviews Dr. James Miller, Chief AI Officer at Stanford Medicine
15 Nov 2024 | 45 min

A deep dive into how machine learning is transforming diagnostics and why doctors shouldn't fear the robots just yet.

## 2. ⏱️ Timeline
* [00:00 - 08:30] **Introductions:** Background on Dr. Miller's journey from radiologist to AI researcher.
* [08:30 - 22:15] **AI in Diagnostics:** How neural networks now spot tumours faster than humans.
* [22:15 - 35:00] **The Human Element:** Why AI won't replace doctors, but augment them.
* [35:00 - End] **Future Predictions:** What's coming in the next 5 years.

## 3. 🧠 Key Takeaways
* AI catches 94% of early-stage cancers vs 88% for human radiologists. [12:45]
* The FDA has approved 500+ AI medical devices since 2020. [18:30]
* Biggest barrier isn't tech—it's getting doctors to trust the black box. [28:15]

## 6. ✍️ Blog Ideas
* **Title:** Why Your Next Diagnosis Might Come From a Machine
  * **The Angle:** AI is already better than doctors at spotting certain diseases.
  * **Main Points:**
    * Radiology AI now matches expert-level accuracy.
    * Early detection rates have jumped 15% in pilot programmes.
    * Patient outcomes improve when AI assists (not replaces) doctors.
  * **Source:** [12:45, 15:20, 28:15]"""

    def __init__(self, provider: LLMProvider, max_tokens: Optional[int] = None):
        """
        Initialize transcript summarizer with an LLM provider.

        Args:
            provider: LLMProvider instance (OpenAI, Anthropic, Gemini, or Ollama)
            max_tokens: Maximum tokens per chunk (optional, auto-calculated if not provided)
        """
        self.provider = provider
        model = provider.get_model_name()

        # Get model limits and calculate optimal chunk size
        self.model_limits = MODEL_CONFIGS.get(model)
        if self.model_limits is None:
            print(f"Warning: Model '{model}' not in config table. Using conservative defaults.")
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

        print(f"Model: {model}")
        print(f"   TPM Limit: {self.model_limits.tpm:,} | Max chunk size: {self.max_tokens:,} tokens")

    def _estimate_tokens(self, text: str) -> int:
        """Rough token estimation: ~4 chars per token"""
        return len(text) // 4

    def _chunk_transcript(self, transcript_text: str) -> List[str]:
        """Split large transcripts into processable chunks"""
        overhead_tokens = self._estimate_tokens(self.SYSTEM_PROMPT)
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

    def _process_single_chunk(self, chunk_text: str, chunk_num: int, total_chunks: int) -> str:
        """Process a single transcript chunk"""
        user_message = "TRANSCRIPT:\n\n"

        if total_chunks > 1:
            user_message += f"[CHUNK {chunk_num}/{total_chunks}]\n\n"

        user_message += chunk_text

        try:
            messages = [
                {"role": "system", "content": self.SYSTEM_PROMPT},
                {"role": "user", "content": user_message},
            ]

            # Use temperature if model supports it
            temperature = 0.3 if self.model_limits.supports_temperature else None

            response = self.provider.chat_completion(messages=messages, temperature=temperature)
            return response

        except Exception as e:
            print(f"Error processing chunk {chunk_num}/{total_chunks}: {e}")
            raise

    def summarize(self, transcript_text: str, output_path: Optional[Path] = None) -> str:
        """
        Summarize a transcript with comprehensive analysis.

        Args:
            transcript_text: The transcript text (from cleaned transcript markdown)
            output_path: Optional path to save the summary markdown

        Returns:
            The summary as markdown text
        """
        estimated_tokens = self._estimate_tokens(transcript_text)

        print(f"Summarizing transcript with {self.provider.get_model_name()}...")
        print(f"Estimated tokens: ~{estimated_tokens:,}")

        chunks = self._chunk_transcript(transcript_text)

        if len(chunks) > 1:
            print(f"Large transcript detected. Splitting into {len(chunks)} chunks...")

        try:
            chunk_outputs = []

            for i, chunk in enumerate(chunks, 1):
                if len(chunks) > 1:
                    print(f"Processing chunk {i}/{len(chunks)}...")

                output_text = self._process_single_chunk(chunk, i, len(chunks))
                chunk_outputs.append(output_text)

            # Combine results if multiple chunks
            if len(chunk_outputs) == 1:
                final_output = chunk_outputs[0]
            else:
                final_output = "\n\n---\n\n".join(chunk_outputs)

            # Save if output path provided
            if output_path:
                output_path.parent.mkdir(parents=True, exist_ok=True)
                with open(output_path, "w", encoding="utf-8") as f:
                    f.write(final_output)
                print(f"Summary saved to {output_path}")

            print("Summarization completed successfully")
            return final_output

        except Exception as e:
            print(f"Error during summarization: {e}")
            raise
