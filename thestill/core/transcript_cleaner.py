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
Transcript cleaner with overlapping chunking for handling long texts.
Uses small LLM models to fix spelling, remove filler words, and improve readability.
"""

import json
import re
import time
from pathlib import Path
from typing import Dict, List, Optional, Tuple

try:
    import tiktoken

    TIKTOKEN_AVAILABLE = True
except ImportError:
    TIKTOKEN_AVAILABLE = False

try:
    import nltk

    NLTK_AVAILABLE = True
    # Try to use punkt tokenizer, download if needed
    try:
        nltk.data.find("tokenizers/punkt")
    except LookupError:
        print("Downloading NLTK punkt tokenizer...")
        nltk.download("punkt", quiet=True)
except ImportError:
    NLTK_AVAILABLE = False

from .llm_provider import LLMProvider


class TranscriptCleanerConfig:
    """Configuration for transcript cleaning"""

    def __init__(
        self,
        chunk_size: int = 20000,  # tokens
        overlap_pct: float = 0.15,  # 15% overlap
        extract_entities: bool = True,  # First pass for entity extraction
        remove_filler_words: bool = True,
        fix_spelling: bool = True,
        fix_grammar: bool = True,
        preserve_timestamps: bool = True,
        filler_words: Optional[List[str]] = None,
    ):
        self.chunk_size = chunk_size
        self.overlap_pct = overlap_pct
        self.extract_entities = extract_entities
        self.remove_filler_words = remove_filler_words
        self.fix_spelling = fix_spelling
        self.fix_grammar = fix_grammar
        self.preserve_timestamps = preserve_timestamps
        self.filler_words = filler_words or [
            "um",
            "uh",
            "ah",
            "hmm",
            "mmm",
            "er",
            "erm",
            "like",
            "you know",
            "sort of",
            "kind of",
            "I mean",
            "right",
            "okay",
            "yeah",
        ]


class TranscriptCleaner:
    """
    Clean transcripts using overlapping chunking strategy.
    Handles long texts that exceed model context windows.
    """

    ENTITY_EXTRACTION_PROMPT = """You are an entity extraction specialist for podcast transcripts.

Your task: Extract all important entities from this transcript for consistency in cleaning.

Extract:
1. **Names**: People, hosts, guests (e.g., "Dr. Sarah Johnson", "Elon Musk")
2. **Companies/Organizations**: (e.g., "OpenAI", "NASA", "MIT")
3. **Acronyms**: (e.g., "AI", "CEO", "FOMO", "LLM")
4. **Technical terms**: Field-specific jargon (e.g., "blockchain", "quantum computing")
5. **Products/Brands**: (e.g., "ChatGPT", "Tesla Model S")

For each entity provide:
- "term": The correct spelling/format
- "type": One of: name, company, acronym, technical_term, product
- "context": Brief context if needed for disambiguation

IMPORTANT: Return ONLY valid JSON. Format:
{
  "entities": [
    {"term": "Dr. Sarah Johnson", "type": "name", "context": "AI researcher"},
    {"term": "OpenAI", "type": "company", "context": "AI research lab"},
    {"term": "LLM", "type": "acronym", "context": "Large Language Model"}
  ]
}

Transcript excerpt:
"""

    CLEANING_PROMPT = """You are a transcript cleaning specialist. Fix this transcript section while maintaining ALL content.

Your tasks:
1. Fix spelling errors and obvious transcription mistakes
2. Remove filler words: {filler_words}
3. Fix grammar while keeping conversational tone
4. Use the entity glossary below for consistent spelling
5. Keep ALL timestamps in [MM:SS] or [HH:MM:SS] format
6. Do NOT summarize or remove content
7. Do NOT add information not in the original

ENTITY GLOSSARY (use these exact spellings):
{entities_json}

Instructions:
- Output ONLY the cleaned text
- Preserve paragraph structure
- Keep the natural flow of conversation
- Fix obvious errors but don't over-edit

Text to clean:
"""

    def __init__(self, provider: LLMProvider, config: Optional[TranscriptCleanerConfig] = None):
        """
        Initialize transcript cleaner.

        Args:
            provider: LLMProvider instance (OpenAI or Ollama)
            config: Cleaning configuration options
        """
        self.provider = provider
        self.config = config or TranscriptCleanerConfig()
        self.model_name = provider.get_model_name()

        # Initialize tokenizer
        if TIKTOKEN_AVAILABLE and "gpt" in self.model_name.lower():
            try:
                self.tokenizer = tiktoken.encoding_for_model(self.model_name)
                print(f"Using tiktoken for {self.model_name}")
            except KeyError:
                self.tokenizer = tiktoken.get_encoding("cl100k_base")
                print(f"Using cl100k_base encoding (model {self.model_name} not found)")
        else:
            self.tokenizer = None
            print("Using character-based token estimation (~4 chars per token)")

    def _count_tokens(self, text: str) -> int:
        """Count tokens in text"""
        if self.tokenizer:
            return len(self.tokenizer.encode(text))
        # Rough estimation: 1 token ≈ 4 characters
        return len(text) // 4

    def _split_into_sentences(self, text: str) -> List[str]:
        """Split text into sentences"""
        if NLTK_AVAILABLE:
            try:
                return nltk.sent_tokenize(text)
            except Exception as e:
                print(f"Warning: NLTK sentence tokenization failed: {e}")

        # Fallback: simple regex-based splitting
        sentences = re.split(r"(?<=[.!?])\s+", text)
        return [s.strip() for s in sentences if s.strip()]

    def _extract_entities(self, text: str) -> List[Dict]:
        """
        First pass: Extract entities for consistent cleaning.
        Uses only first portion of text if too long.
        """
        print("Extracting entities for consistency...")

        # Use only first ~10K tokens for entity extraction
        max_extraction_tokens = 10000
        tokens = self._count_tokens(text)

        if tokens > max_extraction_tokens:
            # Estimate characters to keep
            chars_to_keep = max_extraction_tokens * 4
            text_excerpt = text[:chars_to_keep] + "\n\n[... transcript continues ...]"
        else:
            text_excerpt = text

        try:
            messages = [
                {"role": "system", "content": self.ENTITY_EXTRACTION_PROMPT},
                {"role": "user", "content": text_excerpt},
            ]

            response = self.provider.chat_completion(
                messages=messages, temperature=0.1, max_tokens=2000, response_format={"type": "json_object"}
            )

            # Parse JSON response
            response = response.strip()
            if "```json" in response:
                start = response.find("```json") + 7
                end = response.find("```", start)
                if end != -1:
                    response = response[start:end].strip()

            result = json.loads(response)
            entities = result.get("entities", [])

            print(f"Extracted {len(entities)} entities")
            return entities

        except Exception as e:
            print(f"Warning: Entity extraction failed: {e}")
            return []

    def _create_overlapping_chunks(self, text: str) -> List[Tuple[str, int, int]]:
        """
        Split text into overlapping chunks.

        Returns:
            List of (chunk_text, overlap_start_chars, overlap_end_chars) tuples
            overlap_start_chars: where overlap begins in this chunk (0 for first chunk)
            overlap_end_chars: where overlap ends (0 for last chunk)
        """
        sentences = self._split_into_sentences(text)
        if not sentences:
            return [(text, 0, 0)]

        chunks = []
        current_chunk_sentences = []
        current_tokens = 0
        overlap_size = int(self.config.chunk_size * self.config.overlap_pct)

        # Track overlap sentences for next chunk
        overlap_sentences = []

        for sentence in sentences:
            sentence_tokens = self._count_tokens(sentence)

            # Check if adding this sentence exceeds chunk size
            if current_tokens + sentence_tokens > self.config.chunk_size and current_chunk_sentences:
                # Save current chunk
                chunk_text = " ".join(current_chunk_sentences)

                # Calculate overlap boundaries
                # For first chunk, no overlap at start
                overlap_start = 0 if not chunks else len(" ".join(overlap_sentences)) + 1

                # Calculate overlap for next chunk
                overlap_tokens = 0
                next_overlap_sentences = []
                for sent in reversed(current_chunk_sentences):
                    sent_tokens = self._count_tokens(sent)
                    if overlap_tokens + sent_tokens <= overlap_size:
                        next_overlap_sentences.insert(0, sent)
                        overlap_tokens += sent_tokens
                    else:
                        break

                overlap_end = len(" ".join(next_overlap_sentences)) if next_overlap_sentences else 0

                chunks.append((chunk_text, overlap_start, overlap_end))

                # Start new chunk with overlap from previous
                overlap_sentences = next_overlap_sentences.copy()
                current_chunk_sentences = next_overlap_sentences + [sentence]
                current_tokens = overlap_tokens + sentence_tokens
            else:
                current_chunk_sentences.append(sentence)
                current_tokens += sentence_tokens

        # Add final chunk
        if current_chunk_sentences:
            chunk_text = " ".join(current_chunk_sentences)
            overlap_start = 0 if not chunks else len(" ".join(overlap_sentences)) + 1
            chunks.append((chunk_text, overlap_start, 0))  # No overlap at end

        return chunks if chunks else [(text, 0, 0)]

    def _clean_chunk(self, chunk_text: str, entities: List[Dict], chunk_num: int, total_chunks: int) -> str:
        """Clean a single chunk using LLM"""
        # Build entity glossary string
        if entities:
            entities_json = json.dumps(entities, indent=2)
        else:
            entities_json = "[]"

        # Build filler words list
        filler_words_str = ", ".join(self.config.filler_words)

        # Build prompt
        system_prompt = self.CLEANING_PROMPT.format(filler_words=filler_words_str, entities_json=entities_json)

        # Add chunk info for multi-chunk processing
        user_message = chunk_text
        if total_chunks > 1:
            user_message = f"[CHUNK {chunk_num}/{total_chunks}]\n\n{chunk_text}"

        try:
            messages = [{"role": "system", "content": system_prompt}, {"role": "user", "content": user_message}]

            response = self.provider.chat_completion(
                messages=messages, temperature=0.2, max_tokens=int(self.config.chunk_size * 1.2)  # Allow some expansion
            )

            return response.strip()

        except Exception as e:
            print(f"Error cleaning chunk {chunk_num}/{total_chunks}: {e}")
            # Return original on error
            return chunk_text

    def _stitch_chunks(self, cleaned_chunks: List[Tuple[str, int, int]]) -> str:
        """
        Stitch cleaned chunks together, removing overlapping portions.

        Args:
            cleaned_chunks: List of (cleaned_text, overlap_start_chars, overlap_end_chars)
        """
        if len(cleaned_chunks) == 1:
            return cleaned_chunks[0][0]

        # Start with first chunk (keep everything)
        result = cleaned_chunks[0][0]

        # For subsequent chunks, remove the overlapping start portion
        for i in range(1, len(cleaned_chunks)):
            cleaned_text, overlap_start, _ = cleaned_chunks[i]

            # Skip the overlapping portion at the beginning
            if overlap_start > 0 and overlap_start < len(cleaned_text):
                unique_portion = cleaned_text[overlap_start:].lstrip()
                result += " " + unique_portion
            else:
                # If we can't find overlap, just append with space
                result += " " + cleaned_text

        return result

    def clean_transcript(self, text: str, output_path: Optional[str] = None) -> Dict:
        """
        Clean transcript using overlapping chunking strategy.

        Args:
            text: Raw transcript text to clean
            output_path: Optional path to save cleaned transcript

        Returns:
            Dict with keys: cleaned_text, entities, processing_time, chunks_processed
        """
        start_time = time.time()

        # Count tokens in original
        original_tokens = self._count_tokens(text)
        print(f"\nCleaning transcript with {self.model_name}...")
        print(f"Original length: {len(text):,} chars, ~{original_tokens:,} tokens")

        # Step 1: Extract entities (optional)
        entities = []
        if self.config.extract_entities:
            entities = self._extract_entities(text)

        # Step 2: Create overlapping chunks
        print("Creating overlapping chunks...")
        chunk_tuples = self._create_overlapping_chunks(text)
        total_chunks = len(chunk_tuples)

        print(f"Split into {total_chunks} chunk(s) with {int(self.config.overlap_pct * 100)}% overlap")

        # Step 3: Clean each chunk
        cleaned_chunks = []
        for i, (chunk_text, overlap_start, overlap_end) in enumerate(chunk_tuples, 1):
            chunk_tokens = self._count_tokens(chunk_text)
            print(f"Processing chunk {i}/{total_chunks} (~{chunk_tokens:,} tokens)...")

            cleaned_text = self._clean_chunk(chunk_text, entities, i, total_chunks)
            cleaned_chunks.append((cleaned_text, overlap_start, overlap_end))

            # Note: No artificial delay needed - LLM providers (Anthropic, OpenAI, etc.)
            # have built-in rate limit handling with automatic retry logic

        # Step 4: Stitch chunks together
        print("Stitching chunks together...")
        final_text = self._stitch_chunks(cleaned_chunks)

        processing_time = time.time() - start_time
        final_tokens = self._count_tokens(final_text)

        print(f"\nCleaning completed in {processing_time:.1f}s")
        print(f"Final length: {len(final_text):,} chars, ~{final_tokens:,} tokens")
        print(f"Token change: {((final_tokens - original_tokens) / original_tokens * 100):+.1f}%")

        result = {
            "cleaned_text": final_text,
            "entities": entities,
            "processing_time": processing_time,
            "chunks_processed": total_chunks,
            "original_tokens": original_tokens,
            "final_tokens": final_tokens,
        }

        # Save if output path provided
        if output_path:
            self._save_cleaned_transcript(result, output_path)

        return result

    def _save_cleaned_transcript(self, result: Dict, output_path: str):
        """Save cleaned transcript to file"""
        output_path = Path(output_path)
        output_path.parent.mkdir(parents=True, exist_ok=True)

        # Save as text file
        text_path = output_path.with_suffix(".txt")
        with open(text_path, "w", encoding="utf-8") as f:
            f.write(result["cleaned_text"])

        # Save metadata as JSON
        json_path = output_path.with_suffix(".json")
        metadata = {
            "entities": result["entities"],
            "processing_time": result["processing_time"],
            "chunks_processed": result["chunks_processed"],
            "original_tokens": result["original_tokens"],
            "final_tokens": result["final_tokens"],
        }
        with open(json_path, "w", encoding="utf-8") as f:
            json.dump(metadata, f, indent=2, ensure_ascii=False)

        print(f"Cleaned transcript saved to {text_path}")
        print(f"Metadata saved to {json_path}")
