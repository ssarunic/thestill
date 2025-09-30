import whisper
import json
import time
import os
import torch
import re
from pathlib import Path
from typing import Dict, List, Optional, Tuple
from pydub import AudioSegment
from pydub.effects import normalize


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
                         custom_prompt: str = None, preprocess_audio: bool = True) -> Optional[Dict]:
        """Transcribe audio file with optional custom prompt for better accuracy"""
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