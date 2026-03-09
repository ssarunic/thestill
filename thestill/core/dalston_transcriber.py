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
- Transcript export (SRT, VTT, TXT, JSON)
"""

import time
from pathlib import Path
from typing import Any, Dict, List, Optional

from structlog import get_logger

from thestill.models.transcript import Segment, Transcript, Word
from thestill.models.transcription import TranscribeOptions
from thestill.utils.path_manager import PathManager

from .progress import ProgressUpdate, TranscriptionStage
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
    - Multiple backend engines (Faster Whisper, WhisperX, Parakeet, NeMo)

    This transcriber uses the Dalston Python SDK (>=0.1.0) for integration.
    """

    def __init__(
        self,
        base_url: Optional[str] = None,
        api_key: Optional[str] = None,
        model: Optional[str] = None,
        timeout: float = DEFAULT_TIMEOUT,
        enable_diarization: bool = True,
        num_speakers: Optional[int] = None,
        min_speakers: Optional[int] = None,
        max_speakers: Optional[int] = None,
        language: Optional[str] = None,
        path_manager: Optional[PathManager] = None,
        retention: int = 30,
    ):
        """
        Initialize Dalston transcriber.

        Args:
            base_url: Dalston server URL. Defaults to http://localhost:8000.
            api_key: Optional API key for authentication.
            model: Transcription model/engine (e.g., "faster-whisper-large-v3")
                   or "auto" for automatic selection.
            timeout: Request timeout in seconds.
            enable_diarization: Enable speaker diarization (default: True).
            num_speakers: Exact number of speakers (None = auto-detect).
            min_speakers: Minimum speakers for diarization auto-detection.
            max_speakers: Maximum speakers for diarization auto-detection.
            language: Language code (e.g., "en"). None = auto-detect.
            path_manager: PathManager for storing pending operations.
            retention: Retention in days. 0=transient, -1=permanent, N=days.
        """
        import os

        self.base_url = base_url or os.getenv("DALSTON_BASE_URL") or DEFAULT_BASE_URL
        self.api_key = api_key or os.getenv("DALSTON_API_KEY") or None
        self.model = model or os.getenv("DALSTON_MODEL") or None
        self.timeout = timeout
        self.enable_diarization = enable_diarization
        self.num_speakers = num_speakers
        self.min_speakers = min_speakers
        self.max_speakers = max_speakers
        self.language = language
        self.path_manager = path_manager
        self.retention = retention

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
                    "pip install dalston-sdk@git+https://github.com/ssarunic/dalston.git#subdirectory=sdk"
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

        # Use SDK enums for speaker detection
        from dalston_sdk import SpeakerDetection, TimestampGranularity

        speaker_detection = SpeakerDetection.DIARIZE if self.enable_diarization else SpeakerDetection.NONE

        # Use instance language or option language
        effective_language = self.language or options.language or "auto"

        try:
            # Build transcribe kwargs using the latest SDK API
            transcribe_kwargs: Dict[str, Any] = {
                "file": audio_path,
                "language": effective_language,
                "speaker_detection": speaker_detection,
                "timestamps_granularity": TimestampGranularity.WORD,
                "retention": self.retention,
            }

            if self.model:
                transcribe_kwargs["model"] = self.model

            if self.num_speakers is not None:
                transcribe_kwargs["num_speakers"] = self.num_speakers
            if self.min_speakers is not None:
                transcribe_kwargs["min_speakers"] = self.min_speakers
            if self.max_speakers is not None:
                transcribe_kwargs["max_speakers"] = self.max_speakers

            # Submit transcription job - SDK handles file opening
            job = self._client.transcribe(**transcribe_kwargs)

            logger.info("Transcription job submitted", job_id=str(job.id))

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
            # SDK callback signature: (progress: int, stage: str | None)
            def on_progress(progress: int, stage: Optional[str] = None):
                if options.progress_callback:
                    stage_label = stage or "processing"
                    options.progress_callback(
                        ProgressUpdate(
                            stage=TranscriptionStage.TRANSCRIBING,
                            progress_pct=progress,
                            message=f"Transcribing: {stage_label}",
                        )
                    )

            completed_job = self._client.wait_for_completion(
                job.id,
                poll_interval=POLL_INTERVAL,
                timeout=MAX_POLL_DURATION,
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

        except ImportError:
            raise
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

        The SDK returns typed dataclass objects (dalston_sdk.types.Job,
        dalston_sdk.types.Transcript, etc.) so we access fields directly.

        Args:
            job: Completed Dalston Job object.
            audio_path: Original audio file path.
            start_time: Transcription start time for processing_time calculation.
            requested_language: Explicitly requested language.

        Returns:
            Formatted Transcript object.
        """
        processing_time = time.time() - start_time

        # Get transcript data from SDK Job object
        transcript_data = job.transcript

        # Use explicitly requested language if provided, otherwise use SDK's detected language
        if requested_language and requested_language != "auto":
            language = requested_language
        else:
            language = getattr(transcript_data, "language_code", "en") or "en"

        # Extract full text
        full_text = transcript_data.text if transcript_data else ""

        # Build words and segments from SDK response
        words = self._parse_words(transcript_data)
        segments = self._build_segments(transcript_data)

        # Count unique speakers from SDK's speakers list or from segments
        speakers_detected = 0
        if transcript_data and transcript_data.speakers:
            speakers_detected = len(transcript_data.speakers)
        elif segments:
            speakers_detected = len(set(s.speaker for s in segments if s.speaker))

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
                "job_id": str(job.id),
                "base_url": self.base_url,
                "current_stage": getattr(job, "current_stage", None),
            },
        )

    def _parse_words(self, transcript_data: Any) -> List[Word]:
        """
        Parse words from Dalston SDK Transcript object.

        The SDK returns Word dataclasses with fields:
        - text: str
        - start: float
        - end: float
        - confidence: float | None
        - speaker_id: str | None

        Args:
            transcript_data: SDK Transcript dataclass.

        Returns:
            List of thestill Word objects.
        """
        if not transcript_data or not transcript_data.words:
            return []

        words = []
        speaker_mapping: Dict[str, str] = {}

        for sdk_word in transcript_data.words:
            # Map speaker_id to standard SPEAKER_NN format
            speaker = None
            if sdk_word.speaker_id:
                if sdk_word.speaker_id not in speaker_mapping:
                    speaker_num = len(speaker_mapping) + 1
                    speaker_mapping[sdk_word.speaker_id] = f"SPEAKER_{speaker_num:02d}"
                speaker = speaker_mapping[sdk_word.speaker_id]

            words.append(
                Word(
                    word=sdk_word.text,
                    start=sdk_word.start,
                    end=sdk_word.end,
                    probability=sdk_word.confidence,
                    speaker=speaker,
                )
            )

        return words

    def _build_segments(self, transcript_data: Any) -> List[Segment]:
        """
        Build segments from Dalston SDK Transcript object.

        The SDK returns Segment dataclasses with fields:
        - id: int
        - text: str
        - start: float
        - end: float
        - speaker_id: str | None
        - words: list[Word] | None

        Args:
            transcript_data: SDK Transcript dataclass.

        Returns:
            List of thestill Segment objects.
        """
        if not transcript_data or not transcript_data.segments:
            return []

        segments = []
        speaker_mapping: Dict[str, str] = {}

        for sdk_seg in transcript_data.segments:
            # Map speaker_id to standard SPEAKER_NN format
            speaker = None
            if sdk_seg.speaker_id:
                if sdk_seg.speaker_id not in speaker_mapping:
                    speaker_num = len(speaker_mapping) + 1
                    speaker_mapping[sdk_seg.speaker_id] = f"SPEAKER_{speaker_num:02d}"
                speaker = speaker_mapping[sdk_seg.speaker_id]

            # Convert SDK words to thestill Words
            seg_words = None
            if sdk_seg.words:
                seg_words = [
                    Word(
                        word=w.text,
                        start=w.start,
                        end=w.end,
                        probability=w.confidence,
                        speaker=speaker,
                    )
                    for w in sdk_seg.words
                ]

            segments.append(
                Segment(
                    id=sdk_seg.id,
                    start=sdk_seg.start,
                    end=sdk_seg.end,
                    text=sdk_seg.text.strip(),
                    speaker=speaker,
                    words=seg_words,
                )
            )

        return segments

    def export_transcript(self, job_id: str, format: str = "srt") -> str:
        """
        Export a completed transcript in the specified format.

        Args:
            job_id: The Dalston job ID.
            format: Export format - "srt", "vtt", "txt", or "json".

        Returns:
            Exported transcript as string.
        """
        self.load_model()

        from dalston_sdk import ExportFormat

        format_map = {
            "srt": ExportFormat.SRT,
            "vtt": ExportFormat.VTT,
            "txt": ExportFormat.TXT,
            "json": ExportFormat.JSON,
        }
        export_format = format_map.get(format.lower(), ExportFormat.SRT)

        return self._client.export(job_id, format=export_format)

    def health_check(self) -> bool:
        """Check if the Dalston server is healthy."""
        self.load_model()
        try:
            status = self._client.health()
            return status.status == "ok"
        except Exception as e:
            logger.warning("Dalston health check failed", error=str(e))
            return False

    def _save_transcript(self, transcript: Transcript, output_path: str) -> None:
        """Save transcript to JSON file."""
        import json

        output_file = Path(output_path)
        output_file.parent.mkdir(parents=True, exist_ok=True)

        with open(output_file, "w", encoding="utf-8") as f:
            json.dump(transcript.model_dump(), f, indent=2, ensure_ascii=False, default=str)

        logger.info("Transcript saved", output_path=output_path)
