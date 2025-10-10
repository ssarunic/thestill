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

import whisper
import json
import time
import os
import torch
import re
import threading
from pathlib import Path
from typing import Dict, List, Optional, Tuple
from pydub import AudioSegment
from pydub.effects import normalize

try:
    import whisperx
    WHISPERX_AVAILABLE = True
except ImportError:
    WHISPERX_AVAILABLE = False

try:
    from pyannote.audio import Pipeline
    PYANNOTE_AVAILABLE = True
except ImportError:
    PYANNOTE_AVAILABLE = False


class DiarizationProgressMonitor:
    """
    Monitor and display progress for speaker diarization process.
    Uses audio duration and empirical ratios to estimate completion time.
    Adapts estimate based on actual performance.
    """

    def __init__(self, audio_duration_seconds: float, device: str = "cpu"):
        self.audio_duration = audio_duration_seconds
        self.device = device
        self.start_time = None
        self.stop_event = threading.Event()
        self.monitor_thread = None
        self.estimated_duration = None
        self.last_update_time = None

        # Initial empirical ratios: diarization_time = audio_duration * ratio
        # Based on typical pyannote.audio performance with modern hardware
        self.processing_ratios = {
            "cuda": 0.5,   # GPU: ~0.5x audio duration (very fast)
            "cpu": 1.0,    # CPU: ~1.0x audio duration (real-time)
            "mps": 0.7     # Apple Silicon: ~0.7x audio duration
        }

    def _get_estimated_duration(self) -> float:
        """Calculate estimated processing time in seconds, adapting based on actual progress"""
        if self.estimated_duration is not None:
            # Use adapted estimate
            return self.estimated_duration

        # Initial estimate
        ratio = self.processing_ratios.get(self.device, 1.0)
        return self.audio_duration * ratio

    def _format_time(self, seconds: float) -> str:
        """Format seconds into readable time string"""
        if seconds < 60:
            return f"{int(seconds)}s"
        elif seconds < 3600:
            mins = int(seconds // 60)
            secs = int(seconds % 60)
            return f"{mins}m {secs}s"
        else:
            hours = int(seconds // 3600)
            mins = int((seconds % 3600) // 60)
            return f"{hours}h {mins}m"

    def _update_progress(self):
        """Background thread that updates progress display with adaptive estimation"""
        while not self.stop_event.is_set():
            elapsed = time.time() - self.start_time
            estimated_duration = self._get_estimated_duration()

            # Adaptive estimation: after 20% elapsed time, recalculate based on actual progress
            # This helps correct overly optimistic or pessimistic initial estimates
            if elapsed > 10 and self.estimated_duration is None:
                # Assume we're making steady progress
                # Use current rate to predict total time
                current_ratio = elapsed / self.audio_duration
                if current_ratio > 0.1:  # Only adapt after we have some data
                    # Add 20% buffer to be conservative
                    self.estimated_duration = (elapsed / 0.2) * 1.2

            progress_pct = min(99, int((elapsed / estimated_duration) * 100))

            elapsed_str = self._format_time(elapsed)
            estimated_str = self._format_time(estimated_duration)

            # Create progress bar
            bar_width = 30
            filled = int(bar_width * progress_pct / 100)
            bar = "█" * filled + "░" * (bar_width - filled)

            # Print progress (carriage return to overwrite line)
            print(f"\r  Progress: [{bar}] {progress_pct}% | {elapsed_str} / ~{estimated_str}",
                  end="", flush=True)

            # Update every second
            self.stop_event.wait(1.0)

        # Final newline after completion
        print()

    def start(self):
        """Start the progress monitor"""
        self.start_time = time.time()
        self.stop_event.clear()
        self.monitor_thread = threading.Thread(target=self._update_progress, daemon=True)
        self.monitor_thread.start()

    def stop(self):
        """Stop the progress monitor"""
        if self.monitor_thread:
            self.stop_event.set()
            self.monitor_thread.join(timeout=2.0)
            elapsed = time.time() - self.start_time
            print(f"  ✓ Completed in {self._format_time(elapsed)}")


class WhisperTranscriber:
    def __init__(self, model_name: str = "base", device: str = "auto"):
        self.model_name = model_name
        self.device = self._resolve_device(device)
        self._model = None

    def _resolve_device(self, device: str) -> str:
        """Resolve 'auto' device to actual device"""
        if device == "auto":
            if torch.cuda.is_available():
                return "cuda"
            elif hasattr(torch.backends, 'mps') and torch.backends.mps.is_available():
                # MPS has issues with Whisper in PyTorch 2.8.0, fall back to CPU
                return "cpu"
            else:
                return "cpu"
        return device

    def load_model(self):
        """Lazy load the Whisper model"""
        if self._model is None:
            print(f"Loading Whisper model: {self.model_name}")

            original_load = torch.load
            def safe_load(*args, **kwargs):
                kwargs['weights_only'] = False
                kwargs['map_location'] = 'cpu'  # Force CPU loading first
                return original_load(*args, **kwargs)

            torch.load = safe_load
            try:
                self._model = whisper.load_model(self.model_name, device=self.device)
                print("Model loaded successfully")
            except Exception as e:
                print(f"Error loading model: {e}")
                if "don't know how to restore data location" in str(e):
                    print("Model cache corruption detected, clearing cache and retrying...")
                    self._clear_model_cache()
                    # Retry loading after cache clear
                    self._model = whisper.load_model(self.model_name, device=self.device)
                    print("Model loaded successfully after cache clear")
                elif "SparseMPS" in str(e) or "_sparse_coo_tensor_with_dims_and_tensors" in str(e):
                    print("MPS sparse tensor issue detected, falling back to CPU...")
                    self.device = "cpu"
                    self._model = whisper.load_model(self.model_name, device="cpu")
                    print("Model loaded successfully on CPU")
                else:
                    raise
            finally:
                torch.load = original_load

    def _clear_model_cache(self):
        """Clear Whisper model cache to fix compatibility issues"""
        import shutil
        from pathlib import Path

        cache_dir = Path.home() / ".cache" / "whisper"
        if cache_dir.exists():
            shutil.rmtree(cache_dir)
            print(f"Cleared Whisper cache: {cache_dir}")

    def transcribe_audio(self, audio_path: str, output_path: str = None,
                         custom_prompt: str = None, preprocess_audio: bool = False,
                         clean_transcript: bool = False, cleaning_config: Dict = None) -> Optional[Dict]:
        """
        Transcribe audio file with optional custom prompt for better accuracy.

        Args:
            audio_path: Path to audio file
            output_path: Path to save transcript JSON
            custom_prompt: Custom prompt to improve transcription accuracy
            preprocess_audio: Whether to preprocess audio before transcription
                WARNING: Enabling preprocessing will modify the audio (remove silence, normalize)
                and cause timestamp drift - transcripts won't align with original audio file
            clean_transcript: Whether to clean transcript with LLM (for fixing errors, removing fillers)
            cleaning_config: Configuration dict for transcript cleaning (provider, model, etc.)
        """
        try:
            self.load_model()

            print(f"Starting transcription of: {Path(audio_path).name}")
            start_time = time.time()

            # Preprocess audio if requested
            processed_audio_path = audio_path
            if preprocess_audio:
                processed_audio_path = self._preprocess_audio(audio_path)

            audio_duration = self._get_audio_duration(processed_audio_path)
            print(f"Audio duration: {audio_duration:.1f} minutes")

            # Build transcription options
            transcribe_options = {
                "language": "en",
                "task": "transcribe",
                "verbose": True,
                "word_timestamps": True,
                "temperature": 0.0,
                "no_speech_threshold": 0.6,  # Higher threshold to detect silence
                "logprob_threshold": -1.0,   # Filter out low-probability segments
                "compression_ratio_threshold": 2.4,  # Detect repetitive content
                "condition_on_previous_text": False  # Reduce hallucination propagation
            }

            # Add custom prompt if provided (helps with proper nouns, technical terms)
            if custom_prompt:
                transcribe_options["initial_prompt"] = custom_prompt
                print(f"Using custom prompt: {custom_prompt[:100]}...")

            result = self._model.transcribe(processed_audio_path, **transcribe_options)

            # Clean up temporary processed audio file if it was created
            if preprocess_audio and processed_audio_path != audio_path:
                try:
                    os.remove(processed_audio_path)
                except:
                    pass

            processing_time = time.time() - start_time
            print(f"Transcription completed in {processing_time:.1f} seconds")

            transcript_data = self._format_transcript(result, processing_time, audio_path)

            # Optional: Clean transcript with LLM
            if clean_transcript and cleaning_config:
                transcript_data = self._clean_transcript_with_llm(transcript_data, cleaning_config)

            if output_path:
                self._save_transcript(transcript_data, output_path)

            return transcript_data

        except Exception as e:
            print(f"Error transcribing {audio_path}: {e}")
            return None

    def _get_audio_duration(self, audio_path: str) -> float:
        """Get audio duration in minutes"""
        try:
            audio = AudioSegment.from_file(audio_path)
            return len(audio) / (1000 * 60)  # Convert ms to minutes
        except:
            return 0.0

    def _format_transcript(self, whisper_result: Dict, processing_time: float, audio_path: str) -> Dict:
        """Format Whisper output into structured transcript"""
        segments = []

        for segment in whisper_result.get("segments", []):
            segment_data = {
                "id": segment.get("id"),
                "start": segment.get("start"),
                "end": segment.get("end"),
                "text": segment.get("text", "").strip(),
                "words": []
            }

            for word in segment.get("words", []):
                word_data = {
                    "word": word.get("word", "").strip(),
                    "start": word.get("start"),
                    "end": word.get("end"),
                    "probability": word.get("probability", 0.0)
                }
                segment_data["words"].append(word_data)

            segments.append(segment_data)

        # Filter out hallucinated segments
        filtered_segments = self._filter_hallucinations(segments)

        return {
            "audio_file": audio_path,
            "language": whisper_result.get("language", "en"),
            "text": whisper_result.get("text", ""),
            "segments": filtered_segments,
            "processing_time": processing_time,
            "model_used": self.model_name,
            "timestamp": time.time()
        }

    def _save_transcript(self, transcript_data: Dict, output_path: str):
        """Save transcript to JSON file"""
        try:
            Path(output_path).parent.mkdir(parents=True, exist_ok=True)
            with open(output_path, 'w', encoding='utf-8') as f:
                json.dump(transcript_data, f, indent=2, ensure_ascii=False)
            print(f"Transcript saved to: {output_path}")
        except Exception as e:
            print(f"Error saving transcript: {e}")

    def get_transcript_text(self, transcript_data: Dict) -> str:
        """Extract plain text from transcript data"""
        if not transcript_data or "segments" not in transcript_data:
            return ""

        text_parts = []
        for segment in transcript_data["segments"]:
            text = segment.get("text", "").strip()
            if text:
                start_time = segment.get("start", 0)
                minutes = int(start_time // 60)
                seconds = int(start_time % 60)
                timestamp = f"[{minutes:02d}:{seconds:02d}]"
                text_parts.append(f"{timestamp} {text}")

        return "\n".join(text_parts)

    def estimate_processing_time(self, audio_duration_minutes: float) -> float:
        """Estimate transcription time based on audio duration"""
        base_ratio = {
            "tiny": 0.05,    # 5% of audio length
            "base": 0.1,     # 10% of audio length
            "small": 0.15,   # 15% of audio length
            "medium": 0.25,  # 25% of audio length
            "large": 0.4     # 40% of audio length
        }

        ratio = base_ratio.get(self.model_name, 0.15)
        return audio_duration_minutes * ratio

    def generate_prompt_from_podcast_info(self, podcast_title: str, episode_title: str = "") -> str:
        """Generate a custom prompt based on podcast information to improve transcription accuracy"""
        prompt_parts = []

        # Add podcast context
        if podcast_title:
            prompt_parts.append(f"This is from the podcast '{podcast_title}'.")

        if episode_title:
            prompt_parts.append(f"Episode title: '{episode_title}'.")

        # Add common proper nouns based on podcast type
        if "politics" in podcast_title.lower():
            prompt_parts.append("Topics include British politics, international relations, Conservative Party, Labour Party, Parliament, Westminster, Ukraine, Gaza, Nigeria.")

        if "rest is politics" in podcast_title.lower():
            prompt_parts.append("Speakers may include Rory Stewart, Alastair Campbell.")

        # Keep under 244 tokens (Whisper's limit)
        full_prompt = " ".join(prompt_parts)
        if len(full_prompt) > 1000:  # Rough token estimate (4 chars per token)
            full_prompt = full_prompt[:1000]

        return full_prompt

    def _filter_hallucinations(self, segments: List[Dict]) -> List[Dict]:
        """Filter out hallucinated segments with repetitive patterns"""
        if not segments:
            return segments

        filtered_segments = []
        hallucination_count = 0

        for i, segment in enumerate(segments):
            text = segment.get("text", "").strip()

            # Skip empty segments
            if not text:
                continue

            # Check if this segment is a hallucination
            if self._is_hallucination(segment, segments, i):
                hallucination_count += 1
                print(f"Detected hallucination in segment {segment.get('id', i)}: {text[:100]}...")
                continue

            filtered_segments.append(segment)

        if hallucination_count > 0:
            print(f"Filtered out {hallucination_count} hallucinated segments")

        return filtered_segments

    def _is_hallucination(self, segment: Dict, all_segments: List[Dict], index: int) -> bool:
        """Detect if a segment is likely a hallucination"""
        text = segment.get("text", "").strip()

        # Check for repetitive word patterns
        if self._has_repetitive_pattern(text):
            return True

        # Check for very low confidence words
        if self._has_low_confidence_words(segment):
            return True

        # Check for abnormally long segments with repetitive content
        if self._is_abnormally_long_repetitive(segment):
            return True

        return False

    def _has_repetitive_pattern(self, text: str) -> bool:
        """Check if text contains repetitive patterns indicating hallucination"""
        if not text:
            return False

        # Split into words
        words = text.split()
        if len(words) < 10:
            return False

        # Check for exact word repetition (like "no, no, no, no...")
        word_counts = {}
        for word in words:
            word_lower = word.lower().strip('.,!?;:')
            word_counts[word_lower] = word_counts.get(word_lower, 0) + 1

        # If any word appears more than 40% of the time, it's likely a hallucination
        for word, count in word_counts.items():
            if count / len(words) > 0.4 and count > 5:
                return True

        # Check for phrase repetition (like "and the country and the country...")
        # Look for patterns of 2-4 words repeated
        for phrase_len in range(2, 5):
            if self._has_repeated_phrases(words, phrase_len):
                return True

        return False

    def _has_repeated_phrases(self, words: List[str], phrase_length: int) -> bool:
        """Check for repeated phrases of given length"""
        if len(words) < phrase_length * 3:  # Need at least 3 repetitions
            return False

        phrase_counts = {}
        for i in range(len(words) - phrase_length + 1):
            phrase = ' '.join(words[i:i + phrase_length]).lower()
            phrase_counts[phrase] = phrase_counts.get(phrase, 0) + 1

        # If any phrase appears more than 3 times, it's likely repetitive
        for phrase, count in phrase_counts.items():
            if count >= 3:
                return True

        return False

    def _has_low_confidence_words(self, segment: Dict) -> bool:
        """Check if segment has many low-confidence words"""
        words = segment.get("words", [])
        if len(words) < 5:
            return False

        low_confidence_count = 0
        for word in words:
            probability = word.get("probability", 1.0)
            if probability < 0.3:  # Very low confidence threshold
                low_confidence_count += 1

        # If more than 60% of words have low confidence, likely hallucination
        return (low_confidence_count / len(words)) > 0.6

    def _is_abnormally_long_repetitive(self, segment: Dict) -> bool:
        """Check if segment is abnormally long with repetitive content"""
        text = segment.get("text", "").strip()
        start = segment.get("start", 0)
        end = segment.get("end", 0)
        duration = end - start

        # If segment is longer than 20 seconds and has repetitive content
        if duration > 20:
            words = text.split()
            if len(words) > 50:  # Very long segment
                # Check if it's mostly repetitive
                unique_words = set(word.lower().strip('.,!?;:') for word in words)
                if len(unique_words) / len(words) < 0.3:  # Less than 30% unique words
                    return True

        return False

    def _preprocess_audio(self, audio_path: str) -> str:
        """Preprocess audio to reduce conditions that cause hallucinations"""
        try:
            print("Preprocessing audio to reduce noise and normalize volume...")

            # Load audio
            audio = AudioSegment.from_file(audio_path)

            # Normalize volume to reduce extreme quiet/loud sections
            audio = normalize(audio)

            # Apply noise gate - remove very quiet sections that might cause hallucinations
            # Convert to dBFS for analysis
            silence_threshold = -50  # dB threshold for silence
            min_silence_len = 1000   # 1 second minimum silence to remove

            # Split audio on silence
            chunks = self._split_on_silence(audio, silence_threshold, min_silence_len)

            if chunks:
                # Recombine chunks with short gaps to prevent abrupt cuts
                processed_audio = chunks[0]
                for chunk in chunks[1:]:
                    # Add a small gap between chunks
                    gap = AudioSegment.silent(duration=100)  # 100ms gap
                    processed_audio += gap + chunk
            else:
                processed_audio = audio

            # Create temporary file for processed audio
            # Use MP3 to avoid format compatibility issues and huge WAV files
            from pathlib import Path
            input_ext = Path(audio_path).suffix
            temp_path = audio_path.replace(input_ext, f'_processed.mp3')
            processed_audio.export(temp_path, format="mp3", bitrate="128k")

            print(f"Audio preprocessed and saved to: {temp_path}")
            return temp_path

        except Exception as e:
            print(f"Error preprocessing audio: {e}")
            return audio_path  # Return original path if preprocessing fails

    def _split_on_silence(self, audio: AudioSegment, silence_thresh: int, min_silence_len: int) -> List[AudioSegment]:
        """Split audio on silence periods to remove long quiet sections"""
        chunks = []
        current_chunk = AudioSegment.empty()

        # Process audio in 1-second segments
        segment_length = 1000  # 1 second
        for i in range(0, len(audio), segment_length):
            segment = audio[i:i + segment_length]

            # Check if segment is mostly silent
            if segment.dBFS < silence_thresh:
                # This is silence - if we have a current chunk, save it
                if len(current_chunk) > 0:
                    chunks.append(current_chunk)
                    current_chunk = AudioSegment.empty()
            else:
                # This is not silence - add to current chunk
                current_chunk += segment

        # Add final chunk if it exists
        if len(current_chunk) > 0:
            chunks.append(current_chunk)

        return chunks

    def _clean_transcript_with_llm(self, transcript_data: Dict, cleaning_config: Dict) -> Dict:
        """
        Clean transcript text using LLM with overlapping chunking strategy.

        Args:
            transcript_data: Transcript dictionary with segments and text
            cleaning_config: Configuration dict with provider, model, etc.
        """
        print("\n" + "=" * 60)
        print("CLEANING TRANSCRIPT WITH LLM")
        print("=" * 60)

        try:
            from .transcript_cleaner import TranscriptCleaner, TranscriptCleanerConfig
            from .llm_provider import OpenAIProvider, OllamaProvider

            # Extract config
            provider_type = cleaning_config.get("provider", "ollama")
            model = cleaning_config.get("model", "gemma3:4b")
            chunk_size = cleaning_config.get("chunk_size", 20000)
            overlap_pct = cleaning_config.get("overlap_pct", 0.15)
            extract_entities = cleaning_config.get("extract_entities", True)

            # Create provider
            if provider_type == "openai":
                api_key = cleaning_config.get("api_key")
                if not api_key:
                    raise ValueError("OpenAI API key required for cleaning")
                provider = OpenAIProvider(api_key=api_key, model=model)
            else:  # ollama
                base_url = cleaning_config.get("base_url", "http://localhost:11434")
                provider = OllamaProvider(base_url=base_url, model=model)

            # Create cleaner config
            cleaner_config = TranscriptCleanerConfig(
                chunk_size=chunk_size,
                overlap_pct=overlap_pct,
                extract_entities=extract_entities
            )

            # Create cleaner
            cleaner = TranscriptCleaner(provider=provider, config=cleaner_config)

            # Get plain text from transcript
            original_text = self.get_transcript_text(transcript_data)

            # Clean the text
            cleaned_result = cleaner.clean_transcript(original_text)

            # Update transcript data with cleaned text
            # For now, we'll add the cleaned text as a new field
            # and keep the original segments intact
            transcript_data["cleaned_text"] = cleaned_result["cleaned_text"]
            transcript_data["cleaning_metadata"] = {
                "entities": cleaned_result["entities"],
                "processing_time": cleaned_result["processing_time"],
                "chunks_processed": cleaned_result["chunks_processed"],
                "original_tokens": cleaned_result["original_tokens"],
                "final_tokens": cleaned_result["final_tokens"]
            }

            print("=" * 60)
            return transcript_data

        except Exception as e:
            print(f"Error cleaning transcript: {e}")
            print("Returning original transcript without cleaning")
            return transcript_data


class WhisperXTranscriber:
    """
    WhisperX-based transcriber with speaker diarization support.
    Fallback to standard Whisper if WhisperX or diarization fails.
    """

    def __init__(
        self,
        model_name: str = "base",
        device: str = "auto",
        enable_diarization: bool = False,
        hf_token: str = "",
        min_speakers: Optional[int] = None,
        max_speakers: Optional[int] = None,
        diarization_model: str = "pyannote/speaker-diarization-3.1"
    ):
        self.model_name = model_name
        self.device = self._resolve_device(device)
        self.enable_diarization = enable_diarization and WHISPERX_AVAILABLE
        self.hf_token = hf_token
        self.min_speakers = min_speakers
        self.max_speakers = max_speakers
        self.diarization_model = diarization_model
        self._model = None
        self._whisper_fallback = None

        if enable_diarization and not WHISPERX_AVAILABLE:
            print("WARNING: Diarization requested but WhisperX not available. Falling back to standard Whisper.")
            self.enable_diarization = False

        if enable_diarization and not hf_token:
            print("WARNING: Diarization enabled but no HuggingFace token provided. Diarization will be disabled.")
            self.enable_diarization = False

    def _resolve_device(self, device: str) -> str:
        """Resolve 'auto' device to actual device"""
        if device == "auto":
            if torch.cuda.is_available():
                return "cuda"
            elif hasattr(torch.backends, 'mps') and torch.backends.mps.is_available():
                return "cpu"  # MPS has issues, use CPU
            else:
                return "cpu"
        return device

    def load_model(self):
        """Load WhisperX model"""
        if self._model is None and WHISPERX_AVAILABLE:
            print(f"Loading WhisperX model: {self.model_name}")
            try:
                self._model = whisperx.load_model(
                    self.model_name,
                    device=self.device,
                    compute_type="float16" if self.device == "cuda" else "int8"
                )
                print("WhisperX model loaded successfully")
            except Exception as e:
                print(f"Error loading WhisperX model: {e}")
                print("Falling back to standard Whisper")
                self._load_whisper_fallback()

    def _load_whisper_fallback(self):
        """Load standard Whisper as fallback"""
        if self._whisper_fallback is None:
            self._whisper_fallback = WhisperTranscriber(
                model_name=self.model_name,
                device=self.device
            )

    def transcribe_audio(
        self,
        audio_path: str,
        output_path: str = None,
        custom_prompt: str = None,
        preprocess_audio: bool = False,
        clean_transcript: bool = False,
        cleaning_config: Dict = None
    ) -> Optional[Dict]:
        """
        Transcribe audio with optional speaker diarization.

        Args:
            audio_path: Path to audio file
            output_path: Path to save transcript JSON
            custom_prompt: Custom prompt for better accuracy (not used in WhisperX)
            preprocess_audio: Whether to preprocess audio
                WARNING: Enabling preprocessing will modify the audio (remove silence, normalize)
                and cause timestamp drift - transcripts won't align with original audio file
            clean_transcript: Whether to clean transcript with LLM
            cleaning_config: Configuration for transcript cleaning
        """
        try:
            # If WhisperX not available, use fallback
            if not WHISPERX_AVAILABLE:
                self._load_whisper_fallback()
                return self._whisper_fallback.transcribe_audio(
                    audio_path, output_path, custom_prompt,
                    preprocess_audio, clean_transcript, cleaning_config
                )

            self.load_model()

            # If model failed to load, use fallback
            if self._model is None:
                self._load_whisper_fallback()
                return self._whisper_fallback.transcribe_audio(
                    audio_path, output_path, custom_prompt,
                    preprocess_audio, clean_transcript, cleaning_config
                )

            print(f"Starting transcription of: {Path(audio_path).name}")
            start_time = time.time()

            # Preprocess audio if requested
            processed_audio_path = audio_path
            if preprocess_audio:
                processed_audio_path = self._preprocess_audio(audio_path)

            # Step 1: Transcribe with WhisperX
            print("Step 1: Transcribing audio with WhisperX...")
            result = self._model.transcribe(
                processed_audio_path,
                batch_size=16,
                language="en",
                print_progress=True  # Enable progress bar
            )

            # Step 2: Align whisper output for accurate word-level timestamps
            print("Step 2: Aligning timestamps for word-level accuracy...")
            print(f"  - Loading alignment model for {result['language']}...")
            model_a, metadata = whisperx.load_align_model(
                language_code=result["language"],
                device=self.device
            )
            print(f"  - Running alignment on {len(result.get('segments', []))} segments...")
            result = whisperx.align(
                result["segments"],
                model_a,
                metadata,
                processed_audio_path,
                self.device,
                return_char_alignments=False
            )
            print("  ✓ Alignment complete")

            # Step 3: Speaker diarization (if enabled)
            speakers_detected = None
            if self.enable_diarization:
                try:
                    print("Step 3: Performing speaker diarization...")
                    print(f"  - Loading diarization model ({self.diarization_model})...")

                    if not PYANNOTE_AVAILABLE:
                        raise ImportError("pyannote.audio is required for speaker diarization")

                    diarize_model = Pipeline.from_pretrained(
                        self.diarization_model,
                        use_auth_token=self.hf_token
                    )

                    # Move model to appropriate device
                    if self.device != "cpu":
                        diarize_model.to(torch.device(self.device))

                    print("  - Analyzing audio for speaker patterns...")
                    if self.min_speakers or self.max_speakers:
                        constraint_msg = f"min={self.min_speakers or 'auto'}, max={self.max_speakers or 'auto'}"
                        print(f"  - Speaker constraints: {constraint_msg}")

                    # Get audio duration for progress estimation
                    audio_duration_seconds = self._get_audio_duration_seconds(processed_audio_path)

                    # Start progress monitor
                    progress_monitor = DiarizationProgressMonitor(
                        audio_duration_seconds=audio_duration_seconds,
                        device=self.device
                    )
                    progress_monitor.start()

                    try:
                        diarize_segments = diarize_model(
                            processed_audio_path,
                            min_speakers=self.min_speakers,
                            max_speakers=self.max_speakers
                        )
                    finally:
                        # Stop progress monitor whether diarization succeeds or fails
                        progress_monitor.stop()

                    print("  - Assigning speakers to transcript segments...")

                    # Convert pyannote Annotation to the format whisperx expects
                    # whisperx expects a pandas DataFrame with 'start', 'end', 'speaker' columns
                    import pandas as pd

                    diarize_df = pd.DataFrame([
                        {
                            'start': segment.start,
                            'end': segment.end,
                            'speaker': speaker
                        }
                        for segment, _, speaker in diarize_segments.itertracks(yield_label=True)
                    ])

                    print(f"  - Converted {len(diarize_df)} diarization segments")
                    result = whisperx.assign_word_speakers(diarize_df, result)

                    # Count unique speakers
                    speakers = set()
                    for segment in result.get("segments", []):
                        if "speaker" in segment and segment["speaker"]:
                            speakers.add(segment["speaker"])
                    speakers_detected = len(speakers)
                    print(f"  ✓ Detected {speakers_detected} unique speaker(s): {', '.join(sorted(speakers))}")

                except Exception as e:
                    print(f"  ✗ Diarization failed: {type(e).__name__}: {e}")
                    import traceback
                    traceback.print_exc()
                    print("  → Continuing without speaker identification")

            # Clean up temporary processed audio
            if preprocess_audio and processed_audio_path != audio_path:
                try:
                    os.remove(processed_audio_path)
                except:
                    pass

            processing_time = time.time() - start_time
            print(f"\n✓ Transcription completed in {processing_time:.1f} seconds")
            if speakers_detected:
                print(f"✓ Speaker diarization: {speakers_detected} speakers identified")
            print(f"✓ Generated {len(result.get('segments', []))} transcript segments")

            # Format transcript
            transcript_data = self._format_transcript(
                result,
                processing_time,
                audio_path,
                speakers_detected
            )

            # Optional: Clean transcript with LLM
            if clean_transcript and cleaning_config:
                transcript_data = self._clean_transcript_with_llm(
                    transcript_data,
                    cleaning_config
                )

            if output_path:
                self._save_transcript(transcript_data, output_path)

            return transcript_data

        except Exception as e:
            print(f"WhisperX transcription failed: {e}")
            print("Falling back to standard Whisper")
            self._load_whisper_fallback()
            return self._whisper_fallback.transcribe_audio(
                audio_path, output_path, custom_prompt,
                preprocess_audio, clean_transcript, cleaning_config
            )

    def _format_transcript(
        self,
        whisperx_result: Dict,
        processing_time: float,
        audio_path: str,
        speakers_detected: Optional[int]
    ) -> Dict:
        """Format WhisperX output into structured transcript"""
        segments = []

        for segment in whisperx_result.get("segments", []):
            segment_data = {
                "id": segment.get("id", len(segments)),
                "start": segment.get("start"),
                "end": segment.get("end"),
                "text": segment.get("text", "").strip(),
                "speaker": segment.get("speaker"),
                "words": []
            }

            for word in segment.get("words", []):
                word_data = {
                    "word": word.get("word", "").strip(),
                    "start": word.get("start"),
                    "end": word.get("end"),
                    "probability": word.get("score", 0.0),
                    "speaker": word.get("speaker")
                }
                segment_data["words"].append(word_data)

            segments.append(segment_data)

        # Build full text
        full_text = " ".join(seg.get("text", "") for seg in segments)

        return {
            "audio_file": audio_path,
            "language": whisperx_result.get("language", "en"),
            "text": full_text,
            "segments": segments,
            "processing_time": processing_time,
            "model_used": f"whisperx-{self.model_name}",
            "timestamp": time.time(),
            "diarization_enabled": self.enable_diarization,
            "speakers_detected": speakers_detected
        }

    def _save_transcript(self, transcript_data: Dict, output_path: str):
        """Save transcript to JSON file"""
        try:
            Path(output_path).parent.mkdir(parents=True, exist_ok=True)
            with open(output_path, 'w', encoding='utf-8') as f:
                json.dump(transcript_data, f, indent=2, ensure_ascii=False)
            print(f"Transcript saved to: {output_path}")
        except Exception as e:
            print(f"Error saving transcript: {e}")

    def _get_audio_duration_seconds(self, audio_path: str) -> float:
        """Get audio duration in seconds"""
        try:
            audio = AudioSegment.from_file(audio_path)
            return len(audio) / 1000.0  # Convert milliseconds to seconds
        except Exception as e:
            print(f"Warning: Could not determine audio duration: {e}")
            return 0.0

    def _preprocess_audio(self, audio_path: str) -> str:
        """Preprocess audio using WhisperTranscriber method"""
        # Reuse preprocessing from WhisperTranscriber
        if self._whisper_fallback is None:
            self._load_whisper_fallback()
        return self._whisper_fallback._preprocess_audio(audio_path)

    def _clean_transcript_with_llm(self, transcript_data: Dict, cleaning_config: Dict) -> Dict:
        """Clean transcript using WhisperTranscriber method"""
        if self._whisper_fallback is None:
            self._load_whisper_fallback()
        return self._whisper_fallback._clean_transcript_with_llm(
            transcript_data,
            cleaning_config
        )

    def get_transcript_text(self, transcript_data: Dict) -> str:
        """Extract plain text from transcript data with speaker labels"""
        if not transcript_data or "segments" not in transcript_data:
            return ""

        text_parts = []
        for segment in transcript_data["segments"]:
            text = segment.get("text", "").strip()
            if text:
                start_time = segment.get("start", 0)
                minutes = int(start_time // 60)
                seconds = int(start_time % 60)
                timestamp = f"[{minutes:02d}:{seconds:02d}]"

                # Add speaker label if available
                speaker = segment.get("speaker")
                if speaker:
                    text_parts.append(f"{timestamp} [{speaker}] {text}")
                else:
                    text_parts.append(f"{timestamp} {text}")

        return "\n".join(text_parts)
