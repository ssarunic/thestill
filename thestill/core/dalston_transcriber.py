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
Dalston Speech-to-Text transcriber using the Dalston SDK.

Dalston is a self-hosted transcription server with an ElevenLabs-compatible API.
This transcriber uses the official Dalston Python SDK for clean integration.

Features:
- Speaker diarization
- Word-level timestamps
- Async transcription with polling
- Local/self-hosted deployment
"""

import time
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Dict, List, Optional

from structlog import get_logger

from thestill.models.transcript import Segment, Transcript, Word
from thestill.models.transcription import TranscribeOptions
from thestill.utils.path_manager import PathManager

from .progress import ProgressCallback, ProgressUpdate, TranscriptionStage
from .transcriber import Transcriber

logger = get_logger(__name__)

# Default configuration
DEFAULT_BASE_URL = "http://localhost:8000"
DEFAULT_TIMEOUT = 120.0

# Polling configuration
POLL_INTERVAL = 5.0  # seconds between polls
MAX_POLL_DURATION = 7200  # 2 hours max wait


class DalstonTranscriber(Transcriber):
    """
    Dalston Speech-to-Text transcriber using the official SDK.

    Dalston is a self-hosted transcription server that provides:
    - Batch and real-time transcription
    - Speaker diarization
    - Word-level timestamps
    - Multiple backend engines (Faster Whisper, WhisperX, Pyannote)

    This transcriber uses the Dalston Python SDK for integration.
    """

    def __init__(
        self,
        base_url: Optional[str] = None,
        api_key: Optional[str] = None,
        model: Optional[str] = None,
        timeout: float = DEFAULT_TIMEOUT,
        enable_diarization: bool = True,
        num_speakers: Optional[int] = None,
        language: Optional[str] = None,
        path_manager: Optional[PathManager] = None,
    ):
        """
        Initialize Dalston transcriber.

        Args:
            base_url: Dalston server URL. Defaults to http://localhost:8000.
            api_key: Optional API key for authentication.
            model: Transcription model/engine (e.g., "whisper-large-v3", "faster-whisper-large-v3").
            timeout: Request timeout in seconds.
            enable_diarization: Enable speaker diarization (default: True).
            num_speakers: Expected number of speakers (None = auto-detect).
            language: Language code (e.g., "en"). None = auto-detect.
            path_manager: PathManager for storing pending operations.
        """
        import os

        self.base_url = base_url or os.getenv("DALSTON_BASE_URL") or DEFAULT_BASE_URL
        self.api_key = api_key or os.getenv("DALSTON_API_KEY") or None
        self.model = model or os.getenv("DALSTON_MODEL") or None
        self.timeout = timeout
        self.enable_diarization = enable_diarization
        self.num_speakers = num_speakers
        self.language = language
        self.path_manager = path_manager

        self._client = None

        logger.info(
            "Dalston transcriber initialized",
            base_url=self.base_url,
            model=self.model,
            diarization_enabled=self.enable_diarization,
            num_speakers=self.num_speakers if self.enable_diarization else None,
        )

    def load_model(self) -> None:
        """Initialize the Dalston client (lazy loading)."""
        if self._client is None:
            try:
                from dalston_sdk import Dalston

                self._client = Dalston(
                    base_url=self.base_url,
                    api_key=self.api_key,
                    timeout=self.timeout,
                )
                logger.debug("Dalston client initialized", base_url=self.base_url)
            except ImportError as e:
                raise ImportError(
                    "Dalston SDK not installed. Install with: "
                    "pip install git+https://github.com/ssarunic/dalston.git#subdirectory=sdk"
                ) from e

    def transcribe_audio(
        self,
        audio_path: str,
        output_path: Optional[str] = None,
        *,
        options: TranscribeOptions,
    ) -> Optional[Transcript]:
        """
        Transcribe audio file using Dalston server.

        Args:
            audio_path: Path to audio file.
            output_path: Optional path to save transcript JSON.
            options: Transcription options including language and episode context.

        Returns:
            Transcript object with segments and metadata.
        """
        self.load_model()

        audio_file = Path(audio_path)
        if not audio_file.exists():
            logger.error("Audio file not found", audio_path=audio_path)
            return None

        file_size_mb = audio_file.stat().st_size / (1024 * 1024)
        logger.info(
            "Starting Dalston transcription",
            file_name=audio_file.name,
            file_size_mb=round(file_size_mb, 1),
        )

        start_time = time.time()

        # Report upload progress
        if options.progress_callback:
            options.progress_callback(
                ProgressUpdate(
                    stage=TranscriptionStage.UPLOADING,
                    progress_pct=0,
                    message="Uploading to Dalston server...",
                )
            )

        # Determine speaker detection mode
        speaker_detection = "diarize" if self.enable_diarization else "none"

        # Use instance language or option language
        effective_language = self.language or options.language or "auto"

        try:
            # Build transcribe kwargs
            transcribe_kwargs = {
                "file": None,  # Set below with open()
                "language": effective_language,
                "speaker_detection": speaker_detection,
                "num_speakers": self.num_speakers,
                "timestamps_granularity": "word",
            }
            if self.model:
                transcribe_kwargs["model"] = self.model

            # Submit transcription job
            with open(audio_path, "rb") as f:
                transcribe_kwargs["file"] = f
                job = self._client.transcribe(**transcribe_kwargs)

            logger.info("Transcription job submitted", job_id=job.id)

            # Report transcription in progress
            if options.progress_callback:
                options.progress_callback(
                    ProgressUpdate(
                        stage=TranscriptionStage.TRANSCRIBING,
                        progress_pct=0,
                        message="Waiting for Dalston to transcribe...",
                    )
                )

            # Wait for completion with progress callback
            def on_progress(status: str, progress: Optional[float] = None):
                if options.progress_callback and progress is not None:
                    options.progress_callback(
                        ProgressUpdate(
                            stage=TranscriptionStage.TRANSCRIBING,
                            progress_pct=int(progress * 100),
                            message=f"Transcribing: {status}",
                        )
                    )

            completed_job = self._client.wait_for_completion(
                job.id,
                poll_interval=POLL_INTERVAL,
                on_progress=on_progress,
            )

            # Format the response
            transcript = self._format_response(
                completed_job,
                audio_path,
                start_time,
                effective_language,
            )

            if output_path:
                self._save_transcript(transcript, output_path)

            return transcript

        except Exception as e:
            logger.error(
                "Dalston transcription failed",
                error=str(e),
                audio_path=audio_path,
                exc_info=True,
            )
            raise

    def _format_response(
        self,
        job: Any,
        audio_path: str,
        start_time: float,
        requested_language: Optional[str] = None,
    ) -> Transcript:
        """
        Convert Dalston job response to Transcript model.

        Args:
            job: Completed Dalston job object.
            audio_path: Original audio file path.
            start_time: Transcription start time for processing_time calculation.
            requested_language: Explicitly requested language.

        Returns:
            Formatted Transcript object.
        """
        processing_time = time.time() - start_time

        # Get transcript data
        transcript_data = job.transcript

        # Use explicitly requested language if provided, otherwise use auto-detected
        if requested_language and requested_language != "auto":
            language = requested_language
        else:
            language = getattr(transcript_data, "language", "en") or "en"

        # Extract full text
        full_text = transcript_data.text if hasattr(transcript_data, "text") else ""

        # Build words and segments from response
        words = self._parse_words(transcript_data)
        segments = self._build_segments(words, transcript_data)

        # Count unique speakers
        speakers_detected = len(set(w.speaker for w in words if w.speaker))

        return Transcript(
            audio_file=audio_path,
            language=language,
            text=full_text,
            segments=segments,
            processing_time=processing_time,
            model_used="dalston",
            timestamp=time.time(),
            diarization_enabled=self.enable_diarization,
            speakers_detected=speakers_detected if speakers_detected > 0 else None,
            provider_metadata={
                "provider": "dalston",
                "job_id": job.id,
                "base_url": self.base_url,
            },
        )

    def _parse_words(self, transcript_data: Any) -> List[Word]:
        """
        Parse words from Dalston transcript response.

        Args:
            transcript_data: Transcript data from Dalston job.

        Returns:
            List of Word objects.
        """
        words = []
        speaker_mapping: Dict[str, str] = {}

        # Get segments from transcript
        segments = getattr(transcript_data, "segments", []) or []

        for segment in segments:
            segment_speaker = getattr(segment, "speaker", None) or getattr(segment, "speaker_id", None)

            # Map speaker to standard format
            speaker = None
            if segment_speaker:
                if segment_speaker not in speaker_mapping:
                    speaker_num = len(speaker_mapping) + 1
                    speaker_mapping[segment_speaker] = f"SPEAKER_{speaker_num:02d}"
                speaker = speaker_mapping[segment_speaker]

            # Check if segment has word-level data
            segment_words = getattr(segment, "words", None)
            if segment_words:
                for word_data in segment_words:
                    words.append(
                        Word(
                            word=getattr(word_data, "text", "") or getattr(word_data, "word", ""),
                            start=getattr(word_data, "start_time", None) or getattr(word_data, "start", None),
                            end=getattr(word_data, "end_time", None) or getattr(word_data, "end", None),
                            probability=getattr(word_data, "confidence", None),
                            speaker=speaker,
                        )
                    )
            else:
                # No word-level data, create a single word from segment text
                segment_text = getattr(segment, "text", "")
                if segment_text:
                    words.append(
                        Word(
                            word=segment_text,
                            start=getattr(segment, "start_time", None) or getattr(segment, "start", None),
                            end=getattr(segment, "end_time", None) or getattr(segment, "end", None),
                            probability=None,
                            speaker=speaker,
                        )
                    )

        return words

    def _build_segments(self, words: List[Word], transcript_data: Any) -> List[Segment]:
        """
        Build segments from words or directly from transcript segments.

        Args:
            words: List of Word objects with timestamps and speakers.
            transcript_data: Original transcript data for segment boundaries.

        Returns:
            List of Segment objects.
        """
        # If transcript has explicit segments, use those
        raw_segments = getattr(transcript_data, "segments", []) or []

        if raw_segments:
            segments = []
            speaker_mapping: Dict[str, str] = {}
            word_idx = 0

            for seg_idx, raw_seg in enumerate(raw_segments):
                seg_speaker = getattr(raw_seg, "speaker", None) or getattr(raw_seg, "speaker_id", None)

                # Map speaker to standard format
                speaker = None
                if seg_speaker:
                    if seg_speaker not in speaker_mapping:
                        speaker_num = len(speaker_mapping) + 1
                        speaker_mapping[seg_speaker] = f"SPEAKER_{speaker_num:02d}"
                    speaker = speaker_mapping[seg_speaker]

                seg_text = getattr(raw_seg, "text", "")
                seg_start = getattr(raw_seg, "start_time", None) or getattr(raw_seg, "start", 0.0)
                seg_end = getattr(raw_seg, "end_time", None) or getattr(raw_seg, "end", seg_start)

                # Collect words for this segment
                seg_words = []
                raw_words = getattr(raw_seg, "words", None)
                if raw_words:
                    # Use words from segment
                    for _ in raw_words:
                        if word_idx < len(words):
                            seg_words.append(words[word_idx])
                            word_idx += 1
                elif word_idx < len(words):
                    # Assign next word to this segment
                    seg_words.append(words[word_idx])
                    word_idx += 1

                segments.append(
                    Segment(
                        id=seg_idx,
                        start=seg_start,
                        end=seg_end,
                        text=seg_text.strip(),
                        speaker=speaker,
                        words=seg_words if seg_words else None,
                    )
                )

            return segments

        # Fallback: build segments from words by speaker changes
        if not words:
            return []

        segments = []
        segment_id = 0
        current_speaker = words[0].speaker
        current_words: List[Word] = []
        segment_start = words[0].start or 0.0

        for word in words:
            if word.speaker != current_speaker and current_words:
                # Save current segment
                segment_text = " ".join(w.word for w in current_words if w.word)
                segment_end = current_words[-1].end or current_words[-1].start or segment_start

                segments.append(
                    Segment(
                        id=segment_id,
                        start=segment_start,
                        end=segment_end,
                        text=segment_text.strip(),
                        speaker=current_speaker,
                        words=current_words,
                    )
                )
                segment_id += 1

                # Start new segment
                current_speaker = word.speaker
                current_words = []
                segment_start = word.start or segment_end

            current_words.append(word)

        # Save final segment
        if current_words:
            segment_text = " ".join(w.word for w in current_words if w.word)
            segment_end = current_words[-1].end or current_words[-1].start or segment_start

            segments.append(
                Segment(
                    id=segment_id,
                    start=segment_start,
                    end=segment_end,
                    text=segment_text.strip(),
                    speaker=current_speaker,
                    words=current_words,
                )
            )

        return segments

    def _save_transcript(self, transcript: Transcript, output_path: str) -> None:
        """Save transcript to JSON file."""
        import json

        output_file = Path(output_path)
        output_file.parent.mkdir(parents=True, exist_ok=True)

        with open(output_file, "w", encoding="utf-8") as f:
            json.dump(transcript.model_dump(), f, indent=2, ensure_ascii=False, default=str)

        logger.info("Transcript saved", output_path=output_path)
