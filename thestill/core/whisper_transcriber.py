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
Whisper and WhisperX transcribers for local speech-to-text.

WhisperTranscriber: Standard OpenAI Whisper with hallucination filtering.
WhisperXTranscriber: Enhanced Whisper with speaker diarization via pyannote.audio.
"""

import os
import shutil
import threading
import time
from pathlib import Path
from typing import Dict, List, Optional

import torch
import whisper
from pydub import AudioSegment
from pydub.effects import normalize

from thestill.models.transcript import Segment, Transcript, Word
from thestill.utils.device import resolve_hybrid_devices
from thestill.utils.duration import get_audio_duration_float
from thestill.utils.stdout_capture import WHISPERX_PROGRESS_PATTERN, StdoutProgressCapture

from .progress import ProgressCallback, ProgressUpdate, TranscriptionStage
from .transcriber import Transcriber

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

    Supports both console output (for CLI) and callback-based progress
    reporting (for web UI).
    """

    def __init__(
        self,
        audio_duration_seconds: float,
        device: str = "cpu",
        progress_callback: Optional[ProgressCallback] = None,
        progress_base_pct: int = 10,
        progress_range_pct: int = 80,
    ):
        """
        Initialize the progress monitor.

        Args:
            audio_duration_seconds: Total duration of audio being processed
            device: Device type (cuda, cpu, mps) for time estimation
            progress_callback: Optional callback for progress updates
            progress_base_pct: Base percentage for diarization stage (default 10%)
            progress_range_pct: Range of percentage for diarization (default 80%, so 10-90%)
        """
        self.audio_duration = audio_duration_seconds
        self.device = device
        self.progress_callback = progress_callback
        self.progress_base_pct = progress_base_pct
        self.progress_range_pct = progress_range_pct
        self.start_time: Optional[float] = None
        self.stop_event = threading.Event()
        self.monitor_thread: Optional[threading.Thread] = None
        self.estimated_duration: Optional[float] = None

        # Initial empirical ratios: diarization_time = audio_duration * ratio
        self.processing_ratios = {
            "cuda": 0.5,  # GPU: ~0.5x audio duration (very fast)
            "cpu": 1.0,  # CPU: ~1.0x audio duration (real-time)
            "mps": 0.08,  # Apple Silicon MPS: ~0.08x audio duration (measured: 32min audio → 2.5min)
        }

    def _get_estimated_duration(self) -> float:
        """Calculate estimated processing time in seconds"""
        if self.estimated_duration is not None:
            return self.estimated_duration
        ratio = self.processing_ratios.get(self.device, 1.0)
        return self.audio_duration * ratio

    def _format_time(self, seconds: float) -> str:
        """Format seconds into readable time string"""
        if seconds < 60:
            return f"{int(seconds)}s"
        if seconds < 3600:
            mins = int(seconds // 60)
            secs = int(seconds % 60)
            return f"{mins}m {secs}s"
        hours = int(seconds // 3600)
        mins = int((seconds % 3600) // 60)
        return f"{hours}h {mins}m"

    def _update_progress(self) -> None:
        """Background thread that updates progress display"""
        while not self.stop_event.is_set():
            if self.start_time is None:
                self.stop_event.wait(0.1)
                continue

            elapsed = time.time() - self.start_time
            initial_estimate = self._get_estimated_duration()

            # Use initial estimate for progress calculation
            # Progress is simply elapsed/estimated, capped at 99%
            diarization_progress_pct = min(99, int((elapsed / initial_estimate) * 100))

            # For remaining time, we have two modes:
            # 1. Before we exceed initial estimate: show (initial_estimate - elapsed)
            # 2. After we exceed initial estimate: show "finishing up" with 0 remaining
            if elapsed < initial_estimate:
                remaining_seconds = max(0, initial_estimate - elapsed)
            else:
                # We've exceeded the estimate - diarization is taking longer than expected
                # Show 0 remaining but cap progress at 95% until actually complete
                remaining_seconds = 0
                diarization_progress_pct = min(95, diarization_progress_pct)

            # Report via callback if available
            if self.progress_callback:
                # Scale to overall progress range (e.g., 50-95%)
                overall_pct = self.progress_base_pct + int(diarization_progress_pct * self.progress_range_pct / 100)
                self.progress_callback(
                    ProgressUpdate(
                        stage=TranscriptionStage.DIARIZING,
                        progress_pct=overall_pct,
                        message=f"Identifying speakers: {diarization_progress_pct}%",
                        estimated_remaining_seconds=remaining_seconds,
                    )
                )

            # Also print to console for CLI usage
            elapsed_str = self._format_time(elapsed)
            estimated_str = self._format_time(initial_estimate)
            remaining_str = self._format_time(remaining_seconds) if remaining_seconds > 0 else "finishing..."

            bar_width = 30
            filled = int(bar_width * diarization_progress_pct / 100)
            bar = "█" * filled + "░" * (bar_width - filled)

            print(
                f"\r  Progress: [{bar}] {diarization_progress_pct}% | {elapsed_str} / ~{estimated_str} | ~{remaining_str}",
                end="",
                flush=True,
            )
            self.stop_event.wait(1.0)
        print()

    def start(self) -> None:
        """Start the progress monitor"""
        self.start_time = time.time()
        self.stop_event.clear()
        self.monitor_thread = threading.Thread(target=self._update_progress, daemon=True)
        self.monitor_thread.start()

    def stop(self) -> None:
        """Stop the progress monitor"""
        if self.monitor_thread:
            self.stop_event.set()
            self.monitor_thread.join(timeout=2.0)
            if self.start_time:
                elapsed = time.time() - self.start_time
                print(f"  ✓ Completed in {self._format_time(elapsed)}")


class WhisperTranscriber(Transcriber):
    """
    Standard OpenAI Whisper transcriber with hallucination filtering.

    Features:
    - Lazy model loading
    - Custom prompt support for better accuracy
    - Hallucination detection and filtering
    - Optional audio preprocessing
    - Optional LLM-based transcript cleaning
    """

    def __init__(self, model_name: str = "base", device: str = "auto"):
        self.model_name = model_name
        self.device = self._resolve_device(device)
        self._model = None

    def load_model(self) -> None:
        """Lazy load the Whisper model"""
        if self._model is not None:
            return

        print(f"Loading Whisper model: {self.model_name}")
        try:
            self._model = whisper.load_model(self.model_name, device=self.device)
            print("Model loaded successfully")
        except Exception as e:
            print(f"Error loading model: {e}")
            if "don't know how to restore data location" in str(e):
                print("Model cache corruption detected, clearing cache and retrying...")
                self._clear_model_cache()
                self._model = whisper.load_model(self.model_name, device=self.device)
                print("Model loaded successfully after cache clear")
            elif "SparseMPS" in str(e) or "_sparse_coo_tensor_with_dims_and_tensors" in str(e):
                print("MPS sparse tensor issue detected, falling back to CPU...")
                self.device = "cpu"
                self._model = whisper.load_model(self.model_name, device="cpu")
                print("Model loaded successfully on CPU")
            else:
                raise

    def _clear_model_cache(self) -> None:
        """Clear Whisper model cache to fix compatibility issues"""
        cache_dir = Path.home() / ".cache" / "whisper"
        if cache_dir.exists():
            shutil.rmtree(cache_dir)
            print(f"Cleared Whisper cache: {cache_dir}")

    def transcribe_audio(
        self,
        audio_path: str,
        output_path: Optional[str] = None,
        *,
        language: str,
        custom_prompt: Optional[str] = None,
        preprocess_audio: bool = False,
        clean_transcript: bool = False,
        cleaning_config: Optional[Dict] = None,
        podcast_title: Optional[str] = None,
        episode_id: Optional[str] = None,
        podcast_slug: Optional[str] = None,
        episode_slug: Optional[str] = None,
        progress_callback=None,  # Accepted for API compatibility, not used
    ) -> Optional[Transcript]:
        """
        Transcribe audio file with optional custom prompt for better accuracy.

        Args:
            audio_path: Path to audio file
            output_path: Path to save transcript JSON
            language: Language code (ISO 639-1, e.g., 'en', 'hr')
            custom_prompt: Custom prompt to improve transcription accuracy
            preprocess_audio: Whether to preprocess audio before transcription
                WARNING: Causes timestamp drift - transcripts won't align with original
            clean_transcript: Whether to clean transcript with LLM
            cleaning_config: Configuration dict for transcript cleaning
            podcast_title: Not used for local transcription (API compatibility)
            episode_id: Not used for local transcription (API compatibility)
            podcast_slug: Not used for local transcription (API compatibility)
            episode_slug: Not used for local transcription (API compatibility)
        """
        # Note: podcast_title, episode_id, podcast_slug, episode_slug are unused
        # They exist for API compatibility with cloud transcribers (Google, ElevenLabs)
        try:
            self.load_model()

            print(f"Starting transcription of: {Path(audio_path).name}")
            start_time = time.time()

            processed_audio_path = audio_path
            if preprocess_audio:
                processed_audio_path = self._preprocess_audio(audio_path)

            audio_duration = self._get_audio_duration_minutes(processed_audio_path)
            print(f"Audio duration: {audio_duration:.1f} minutes")

            transcribe_options = {
                "language": language,
                "task": "transcribe",
                "verbose": True,
                "word_timestamps": True,
                "temperature": 0.0,
                "no_speech_threshold": 0.6,
                "logprob_threshold": -1.0,
                "compression_ratio_threshold": 2.4,
                "condition_on_previous_text": False,
            }

            if custom_prompt:
                transcribe_options["initial_prompt"] = custom_prompt
                print(f"Using custom prompt: {custom_prompt[:100]}...")

            result = self._model.transcribe(processed_audio_path, **transcribe_options)

            if preprocess_audio and processed_audio_path != audio_path:
                try:
                    os.remove(processed_audio_path)
                except OSError:
                    pass

            processing_time = time.time() - start_time
            print(f"Transcription completed in {processing_time:.1f} seconds")

            transcript = self._format_transcript(result, processing_time, audio_path)

            # Note: clean_transcript parameter is deprecated - use 'thestill clean-transcript' instead

            if output_path:
                self._save_transcript(transcript, output_path)

            return transcript

        except Exception as e:
            print(f"Error transcribing {audio_path}: {e}")
            return None

    def _format_transcript(self, whisper_result: Dict, processing_time: float, audio_path: str) -> Transcript:
        """Format Whisper output into structured Transcript"""
        segments = []

        for seg in whisper_result.get("segments", []):
            words = [
                Word(
                    word=w.get("word", "").strip(),
                    start=w.get("start"),
                    end=w.get("end"),
                    probability=w.get("probability", 0.0),
                )
                for w in seg.get("words", [])
            ]

            segments.append(
                Segment(
                    id=seg.get("id", len(segments)),
                    start=seg.get("start", 0.0),
                    end=seg.get("end", 0.0),
                    text=seg.get("text", "").strip(),
                    words=words,
                )
            )

        filtered_segments = self._filter_hallucinations(segments)

        return Transcript(
            audio_file=audio_path,
            language=whisper_result.get("language", "en"),
            text=whisper_result.get("text", ""),
            segments=filtered_segments,
            processing_time=processing_time,
            model_used=self.model_name,
            timestamp=time.time(),
        )

    def estimate_processing_time(self, audio_duration_minutes: float) -> float:
        """Estimate transcription time based on audio duration"""
        base_ratio = {
            "tiny": 0.05,
            "base": 0.1,
            "small": 0.15,
            "medium": 0.25,
            "large": 0.4,
        }
        ratio = base_ratio.get(self.model_name, 0.15)
        return audio_duration_minutes * ratio

    def generate_prompt_from_podcast_info(self, podcast_title: str, episode_title: str = "") -> str:
        """Generate a custom prompt based on podcast information"""
        prompt_parts = []

        if podcast_title:
            prompt_parts.append(f"This is from the podcast '{podcast_title}'.")

        if episode_title:
            prompt_parts.append(f"Episode title: '{episode_title}'.")

        if "politics" in podcast_title.lower():
            prompt_parts.append(
                "Topics include British politics, international relations, "
                "Conservative Party, Labour Party, Parliament, Westminster, Ukraine, Gaza, Nigeria."
            )

        if "rest is politics" in podcast_title.lower():
            prompt_parts.append("Speakers may include Rory Stewart, Alastair Campbell.")

        full_prompt = " ".join(prompt_parts)
        if len(full_prompt) > 1000:
            full_prompt = full_prompt[:1000]

        return full_prompt

    def _filter_hallucinations(self, segments: List[Segment]) -> List[Segment]:
        """Filter out hallucinated segments with repetitive patterns"""
        if not segments:
            return segments

        filtered_segments = []
        hallucination_count = 0

        for i, segment in enumerate(segments):
            text = segment.text.strip()

            if not text:
                continue

            if self._is_hallucination(segment):
                hallucination_count += 1
                print(f"Detected hallucination in segment {segment.id}: {text[:100]}...")
                continue

            filtered_segments.append(segment)

        if hallucination_count > 0:
            print(f"Filtered out {hallucination_count} hallucinated segments")

        return filtered_segments

    def _is_hallucination(self, segment: Segment) -> bool:
        """Detect if a segment is likely a hallucination"""
        text = segment.text.strip()

        if self._has_repetitive_pattern(text):
            return True
        if self._has_low_confidence_words(segment):
            return True
        if self._is_abnormally_long_repetitive(segment):
            return True

        return False

    def _has_repetitive_pattern(self, text: str) -> bool:
        """Check if text contains repetitive patterns indicating hallucination"""
        if not text:
            return False

        words = text.split()
        if len(words) < 10:
            return False

        word_counts: Dict[str, int] = {}
        for word in words:
            word_lower = word.lower().strip(".,!?;:")
            word_counts[word_lower] = word_counts.get(word_lower, 0) + 1

        for count in word_counts.values():
            if count / len(words) > 0.4 and count > 5:
                return True

        for phrase_len in range(2, 5):
            if self._has_repeated_phrases(words, phrase_len):
                return True

        return False

    def _has_repeated_phrases(self, words: List[str], phrase_length: int) -> bool:
        """Check for repeated phrases of given length"""
        if len(words) < phrase_length * 3:
            return False

        phrase_counts: Dict[str, int] = {}
        for i in range(len(words) - phrase_length + 1):
            phrase = " ".join(words[i : i + phrase_length]).lower()
            phrase_counts[phrase] = phrase_counts.get(phrase, 0) + 1

        for count in phrase_counts.values():
            if count >= 3:
                return True

        return False

    def _has_low_confidence_words(self, segment: Segment) -> bool:
        """Check if segment has many low-confidence words"""
        if len(segment.words) < 5:
            return False

        low_confidence_count = sum(1 for word in segment.words if (word.probability or 1.0) < 0.3)
        return (low_confidence_count / len(segment.words)) > 0.6

    def _is_abnormally_long_repetitive(self, segment: Segment) -> bool:
        """Check if segment is abnormally long with repetitive content"""
        text = segment.text.strip()
        duration = segment.end - segment.start

        if duration > 20:
            words = text.split()
            if len(words) > 50:
                unique_words = set(word.lower().strip(".,!?;:") for word in words)
                if len(unique_words) / len(words) < 0.3:
                    return True

        return False

    def _preprocess_audio(self, audio_path: str) -> str:
        """Preprocess audio to reduce conditions that cause hallucinations"""
        try:
            print("Preprocessing audio to reduce noise and normalize volume...")

            audio = AudioSegment.from_file(audio_path)
            audio = normalize(audio)

            silence_threshold = -50
            chunks = self._split_on_silence(audio, silence_threshold)

            if chunks:
                processed_audio = chunks[0]
                for chunk in chunks[1:]:
                    gap = AudioSegment.silent(duration=100)
                    processed_audio += gap + chunk
            else:
                processed_audio = audio

            input_ext = Path(audio_path).suffix
            temp_path = audio_path.replace(input_ext, "_processed.mp3")
            processed_audio.export(temp_path, format="mp3", bitrate="128k")

            print(f"Audio preprocessed and saved to: {temp_path}")
            return temp_path

        except Exception as e:
            print(f"Error preprocessing audio: {e}")
            return audio_path

    def _split_on_silence(self, audio: AudioSegment, silence_thresh: int) -> List[AudioSegment]:
        """Split audio on silence periods to remove long quiet sections"""
        chunks = []
        current_chunk = AudioSegment.empty()

        segment_length = 1000  # 1 second
        for i in range(0, len(audio), segment_length):
            segment = audio[i : i + segment_length]

            if segment.dBFS < silence_thresh:
                if len(current_chunk) > 0:
                    chunks.append(current_chunk)
                    current_chunk = AudioSegment.empty()
            else:
                current_chunk += segment

        if len(current_chunk) > 0:
            chunks.append(current_chunk)

        return chunks


class WhisperXTranscriber(Transcriber):
    """
    WhisperX-based transcriber with speaker diarization support.

    Features:
    - Enhanced word-level timestamp alignment
    - Speaker diarization via pyannote.audio
    - Fallback to standard Whisper if WhisperX fails
    - Progress callback support for real-time progress tracking
    - Hybrid device support: uses optimal device per stage on Mac (CPU for
      transcription, MPS for alignment/diarization)
    """

    def __init__(
        self,
        model_name: str = "base",
        device: str = "auto",
        enable_diarization: bool = False,
        hf_token: str = "",
        min_speakers: Optional[int] = None,
        max_speakers: Optional[int] = None,
        diarization_model: str = "pyannote/speaker-diarization-3.1",
        progress_callback: Optional[ProgressCallback] = None,
    ):
        self.model_name = model_name
        # Resolve devices for each stage (hybrid approach for Mac)
        self.transcription_device, self.alignment_device, self.diarization_device = self._resolve_hybrid_devices(device)
        # Keep self.device for backward compatibility (used by fallback and progress monitor)
        self.device = self.transcription_device
        self.enable_diarization = enable_diarization and WHISPERX_AVAILABLE
        self.hf_token = hf_token
        self.min_speakers = min_speakers
        self.max_speakers = max_speakers
        self.diarization_model = diarization_model
        self.progress_callback = progress_callback
        self._model = None
        self._whisper_fallback: Optional[WhisperTranscriber] = None

        if enable_diarization and not WHISPERX_AVAILABLE:
            print("WARNING: Diarization requested but WhisperX not available. Falling back to standard Whisper.")
            self.enable_diarization = False

        if enable_diarization and not hf_token:
            print("WARNING: Diarization enabled but no HuggingFace token provided. Diarization will be disabled.")
            self.enable_diarization = False

    def _resolve_hybrid_devices(self, device: str) -> tuple:
        """
        Resolve device setting into per-stage devices for optimal performance.

        Delegates to shared utility function in thestill.utils.device.

        Returns:
            tuple: (transcription_device, alignment_device, diarization_device)
        """
        return resolve_hybrid_devices(device, verbose=True)

    def load_model(self) -> None:
        """Load WhisperX model on transcription device"""
        if self._model is not None or not WHISPERX_AVAILABLE:
            return

        print(f"Loading WhisperX model: {self.model_name} (device: {self.transcription_device})")
        try:
            self._model = whisperx.load_model(
                self.model_name,
                device=self.transcription_device,
                compute_type="float16" if self.transcription_device == "cuda" else "int8",
            )
            print("WhisperX model loaded successfully")
        except Exception as e:
            print(f"Error loading WhisperX model: {e}")
            print("Falling back to standard Whisper")
            self._load_whisper_fallback()

    def _load_whisper_fallback(self) -> None:
        """Load standard Whisper as fallback"""
        if self._whisper_fallback is None:
            self._whisper_fallback = WhisperTranscriber(model_name=self.model_name, device=self.device)

    def _report_progress(
        self,
        stage: TranscriptionStage,
        progress_pct: int,
        message: str,
        estimated_remaining_seconds: Optional[float] = None,
    ) -> None:
        """Report progress via callback if available."""
        if self.progress_callback:
            self.progress_callback(
                ProgressUpdate(
                    stage=stage,
                    progress_pct=progress_pct,
                    message=message,
                    estimated_remaining_seconds=estimated_remaining_seconds,
                )
            )

    def transcribe_audio(
        self,
        audio_path: str,
        output_path: Optional[str] = None,
        *,
        language: str,
        custom_prompt: Optional[str] = None,
        preprocess_audio: bool = False,
        clean_transcript: bool = False,
        cleaning_config: Optional[Dict] = None,
        podcast_title: Optional[str] = None,
        episode_id: Optional[str] = None,
        podcast_slug: Optional[str] = None,
        episode_slug: Optional[str] = None,
        progress_callback=None,  # Accepted for API compatibility, not used
    ) -> Optional[Transcript]:
        """
        Transcribe audio with optional speaker diarization.

        Args:
            audio_path: Path to audio file
            output_path: Path to save transcript JSON
            language: Language code (ISO 639-1, e.g., 'en', 'hr')
            custom_prompt: Custom prompt (not used in WhisperX, for API compatibility)
            preprocess_audio: Whether to preprocess audio
            clean_transcript: Whether to clean transcript with LLM
            cleaning_config: Configuration for transcript cleaning
            podcast_title: Not used for local transcription (API compatibility)
            episode_id: Not used for local transcription (API compatibility)
            podcast_slug: Not used for local transcription (API compatibility)
            episode_slug: Not used for local transcription (API compatibility)
        """
        # Note: podcast_title, episode_id, podcast_slug, episode_slug are unused
        # They exist for API compatibility with cloud transcribers (Google, ElevenLabs)
        try:
            if not WHISPERX_AVAILABLE:
                self._load_whisper_fallback()
                return self._whisper_fallback.transcribe_audio(
                    audio_path,
                    output_path,
                    language=language,
                    custom_prompt=custom_prompt,
                    preprocess_audio=preprocess_audio,
                    clean_transcript=clean_transcript,
                    cleaning_config=cleaning_config,
                    podcast_title=podcast_title,
                    episode_id=episode_id,
                    podcast_slug=podcast_slug,
                    episode_slug=episode_slug,
                )

            # Progress allocation depends on whether diarization is enabled:
            # With diarization: load=0-5%, transcribe=5-25%, align=25-30%, diarize=30-95%, format=95-100%
            # Without diarization: load=0-5%, transcribe=5-85%, align=85-95%, format=95-100%
            if self.enable_diarization:
                transcribe_base, transcribe_range = 5, 20  # 5-25%
                align_pct = 25
                diarize_base, diarize_range = 30, 65  # 30-95%
            else:
                transcribe_base, transcribe_range = 5, 80  # 5-85%
                align_pct = 85
                diarize_base, diarize_range = 0, 0  # unused

            self._report_progress(
                TranscriptionStage.LOADING_MODEL,
                2,
                "Loading WhisperX model...",
            )

            self.load_model()

            if self._model is None:
                self._load_whisper_fallback()
                return self._whisper_fallback.transcribe_audio(
                    audio_path,
                    output_path,
                    language=language,
                    custom_prompt=custom_prompt,
                    preprocess_audio=preprocess_audio,
                    clean_transcript=clean_transcript,
                    cleaning_config=cleaning_config,
                    podcast_title=podcast_title,
                    episode_id=episode_id,
                    podcast_slug=podcast_slug,
                    episode_slug=episode_slug,
                )

            print(f"Starting transcription of: {Path(audio_path).name}")
            start_time = time.time()

            processed_audio_path = audio_path
            if preprocess_audio:
                self._load_whisper_fallback()
                processed_audio_path = self._whisper_fallback._preprocess_audio(audio_path)

            # Step 1: Transcribe with WhisperX (with chunk-level progress capture)
            self._report_progress(
                TranscriptionStage.TRANSCRIBING,
                transcribe_base,
                "Transcribing audio with WhisperX...",
            )
            print("Step 1: Transcribing audio with WhisperX...")

            def on_transcribe_progress(whisperx_pct: float) -> None:
                """Scale WhisperX's 0-100% to our allocated range."""
                scaled_pct = transcribe_base + int(whisperx_pct * transcribe_range / 100)
                self._report_progress(
                    TranscriptionStage.TRANSCRIBING,
                    scaled_pct,
                    f"Transcribing: {whisperx_pct:.0f}%",
                )

            with StdoutProgressCapture(WHISPERX_PROGRESS_PATTERN, on_transcribe_progress):
                result = self._model.transcribe(
                    processed_audio_path,
                    batch_size=16,
                    language=language,
                    print_progress=True,
                )

            # Step 2: Align for word-level timestamps
            self._report_progress(
                TranscriptionStage.ALIGNING,
                align_pct,
                f"Aligning timestamps for {len(result.get('segments', []))} segments...",
            )
            print("Step 2: Aligning timestamps for word-level accuracy...")
            print(f"  - Loading alignment model for {result['language']} (device: {self.alignment_device})...")
            model_a, metadata = whisperx.load_align_model(
                language_code=result["language"], device=self.alignment_device
            )
            print(f"  - Running alignment on {len(result.get('segments', []))} segments...")
            result = whisperx.align(
                result["segments"],
                model_a,
                metadata,
                processed_audio_path,
                self.alignment_device,
                return_char_alignments=False,
            )
            print("  ✓ Alignment complete")

            # Step 3: Speaker diarization
            speakers_detected = None
            if self.enable_diarization:
                self._report_progress(
                    TranscriptionStage.DIARIZING,
                    diarize_base,
                    "Starting speaker diarization...",
                )
                speakers_detected = self._perform_diarization(result, processed_audio_path, diarize_base, diarize_range)

            if preprocess_audio and processed_audio_path != audio_path:
                try:
                    os.remove(processed_audio_path)
                except OSError:
                    pass

            # Step 4: Format transcript
            self._report_progress(
                TranscriptionStage.FORMATTING,
                95,
                "Formatting transcript output...",
            )

            processing_time = time.time() - start_time
            print(f"\n✓ Transcription completed in {processing_time:.1f} seconds")
            if speakers_detected:
                print(f"✓ Speaker diarization: {speakers_detected} speakers identified")
            print(f"✓ Generated {len(result.get('segments', []))} transcript segments")

            transcript = self._format_transcript(result, processing_time, audio_path, speakers_detected)

            # Note: clean_transcript parameter is deprecated - use 'thestill clean-transcript' instead

            # Report completion
            self._report_progress(
                TranscriptionStage.COMPLETED,
                100,
                f"Transcription complete ({len(result.get('segments', []))} segments)",
            )

            if output_path:
                self._save_transcript(transcript, output_path)

            return transcript

        except Exception as e:
            print(f"WhisperX transcription failed: {e}")
            print("Falling back to standard Whisper")
            self._load_whisper_fallback()
            return self._whisper_fallback.transcribe_audio(
                audio_path,
                output_path,
                language=language,
                custom_prompt=custom_prompt,
                preprocess_audio=preprocess_audio,
                clean_transcript=clean_transcript,
                cleaning_config=cleaning_config,
            )

    def _perform_diarization(
        self,
        result: Dict,
        audio_path: str,
        progress_base_pct: int = 30,
        progress_range_pct: int = 65,
    ) -> Optional[int]:
        """Perform speaker diarization and update result using diarization_device.

        Args:
            result: WhisperX result dict to update with speaker labels
            audio_path: Path to audio file
            progress_base_pct: Base percentage for diarization progress (default 30%)
            progress_range_pct: Range of percentage for diarization (default 65%, so 30-95%)

        Returns:
            Number of speakers detected, or None if diarization failed
        """
        try:
            print("Step 3: Performing speaker diarization...")
            print(f"  - Loading diarization model ({self.diarization_model}) on {self.diarization_device}...")

            if not PYANNOTE_AVAILABLE:
                raise ImportError("pyannote.audio is required for speaker diarization")

            diarize_model = Pipeline.from_pretrained(self.diarization_model, use_auth_token=self.hf_token)

            if self.diarization_device != "cpu":
                diarize_model.to(torch.device(self.diarization_device))

            print("  - Analyzing audio for speaker patterns...")
            if self.min_speakers or self.max_speakers:
                constraint_msg = f"min={self.min_speakers or 'auto'}, max={self.max_speakers or 'auto'}"
                print(f"  - Speaker constraints: {constraint_msg}")

            audio_duration_seconds = self._get_audio_duration_seconds(audio_path)

            progress_monitor = DiarizationProgressMonitor(
                audio_duration_seconds=audio_duration_seconds,
                device=self.diarization_device,
                progress_callback=self.progress_callback,
                progress_base_pct=progress_base_pct,
                progress_range_pct=progress_range_pct,
            )
            progress_monitor.start()

            try:
                diarize_segments = diarize_model(
                    audio_path,
                    min_speakers=self.min_speakers,
                    max_speakers=self.max_speakers,
                )
            finally:
                progress_monitor.stop()

            print("  - Assigning speakers to transcript segments...")

            import pandas as pd

            diarize_df = pd.DataFrame(
                [
                    {"start": segment.start, "end": segment.end, "speaker": speaker}
                    for segment, _, speaker in diarize_segments.itertracks(yield_label=True)
                ]
            )

            print(f"  - Converted {len(diarize_df)} diarization segments")
            whisperx.assign_word_speakers(diarize_df, result)

            speakers = set()
            for segment in result.get("segments", []):
                if "speaker" in segment and segment["speaker"]:
                    speakers.add(segment["speaker"])

            speakers_detected = len(speakers)
            print(f"  ✓ Detected {speakers_detected} unique speaker(s): {', '.join(sorted(speakers))}")
            return speakers_detected

        except Exception as e:
            print(f"  ✗ Diarization failed: {type(e).__name__}: {e}")
            import traceback

            traceback.print_exc()
            print("  → Continuing without speaker identification")
            return None

    def _get_audio_duration_seconds(self, audio_path: str) -> float:
        """Get audio duration in seconds using ffprobe."""
        return get_audio_duration_float(audio_path)

    def _format_transcript(
        self,
        whisperx_result: Dict,
        processing_time: float,
        audio_path: str,
        speakers_detected: Optional[int],
    ) -> Transcript:
        """Format WhisperX output into structured Transcript"""
        segments = []

        for seg in whisperx_result.get("segments", []):
            words = [
                Word(
                    word=w.get("word", "").strip(),
                    start=w.get("start"),
                    end=w.get("end"),
                    probability=w.get("score", 0.0),
                    speaker=w.get("speaker"),
                )
                for w in seg.get("words", [])
            ]

            segments.append(
                Segment(
                    id=seg.get("id", len(segments)),
                    start=seg.get("start", 0.0),
                    end=seg.get("end", 0.0),
                    text=seg.get("text", "").strip(),
                    speaker=seg.get("speaker"),
                    words=words,
                )
            )

        full_text = " ".join(seg.text for seg in segments)

        return Transcript(
            audio_file=audio_path,
            language=whisperx_result.get("language", "en"),
            text=full_text,
            segments=segments,
            processing_time=processing_time,
            model_used=f"whisperx-{self.model_name}",
            timestamp=time.time(),
            diarization_enabled=self.enable_diarization,
            speakers_detected=speakers_detected,
        )
