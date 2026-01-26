# Copyright 2025 thestill.me
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
Google Cloud Speech-to-Text V2 transcriber with Chirp 3 model and speaker diarization.
"""

import json
import math
import os
import re
import tempfile
import threading
import time
from concurrent.futures import ThreadPoolExecutor, as_completed
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Dict, List, Optional, Tuple

from pydub import AudioSegment
from structlog import get_logger

from thestill.models.podcast import TranscriptionOperation, TranscriptionOperationState
from thestill.models.transcript import Segment, Transcript, Word
from thestill.models.transcription import TranscribeOptions
from thestill.utils.console import ConsoleOutput
from thestill.utils.path_manager import PathManager

from .transcriber import Transcriber

logger = get_logger(__name__)

# Suppress gRPC ALTS warnings that clutter output
# These appear because we're not running on GCP infrastructure
# Must be set before importing Google Cloud libraries
# The E0000 ALTS warning comes from gRPC's C++ core via abseil logging
os.environ.setdefault("GRPC_VERBOSITY", "NONE")
os.environ.setdefault("GLOG_minloglevel", "2")  # Suppress INFO and WARNING from glog

# pylint: disable=wrong-import-position
# These imports MUST come after os.environ settings above to suppress gRPC warnings
from google.api_core.client_options import ClientOptions
from google.cloud import storage
from google.cloud.speech_v2 import SpeechClient
from google.cloud.speech_v2.types import cloud_speech
from google.oauth2 import service_account

# pylint: enable=wrong-import-position


# Default region for Speech-to-Text V2 API
# Chirp 3 supported regions (GA): us, eu, asia-northeast1, asia-southeast1
# Chirp 3 supported regions (Preview): asia-south1, europe-west2, europe-west3, northamerica-northeast1
DEFAULT_REGION = "europe-west2"  # London - matches GCS bucket location

# Chunk size for BatchRecognize transcription
# Using 12 min chunks balances parallelization with fewer merge operations
# The 1 minute overlap ensures seamless merging with speaker reconciliation
MAX_CHUNK_DURATION_MS = 12 * 60 * 1000  # 12 minutes in milliseconds
OVERLAP_DURATION_MS = 60 * 1000  # 1 minute overlap for merging

# Timeout for BatchRecognize is now handled by OPERATION_MAX_WAIT_HOURS
# This legacy constant is kept for compatibility but not used with GCS output
BATCH_RECOGNIZE_TIMEOUT_SECONDS = 12 * 60 * 60  # 12 hours (not used with GCS output)
MAX_CHUNK_RETRIES = 2  # Number of retries for stuck chunks

# Speaker reconciliation settings
OVERLAP_MATCH_WINDOW_MS = 500  # Window for matching words across chunks (ms)
MIN_SPEAKER_VOTES = 3  # Minimum matched words to establish speaker mapping

# GCS upload settings for reliability on slow/unstable connections
GCS_UPLOAD_CHUNK_SIZE = 8 * 1024 * 1024  # 8MB chunks (default is 100MB)
GCS_UPLOAD_TIMEOUT = 180  # seconds per chunk

# GCS output settings for transcript storage
GCS_TRANSCRIPT_OUTPUT_PREFIX = "transcripts/"  # Folder for transcript output in GCS
GCS_TRANSCRIPT_LIFECYCLE_DAYS = 7  # Auto-delete transcripts after N days

# Operation polling settings
OPERATION_POLL_INTERVAL_SECONDS = 30  # How often to check operation status
OPERATION_MAX_WAIT_HOURS = 12  # Maximum hours to wait for an operation


class _ProgressTracker:
    """
    Thread-safe progress tracker for parallel chunk transcription.

    Provides coordinated output to prevent interleaved messages from multiple workers.
    """

    def __init__(self, total_chunks: int, total_duration_ms: int, console: ConsoleOutput):
        self.total_chunks = total_chunks
        self.total_duration_ms = total_duration_ms
        self.completed_chunks = 0
        self.failed_chunks = 0
        self.start_time = time.time()
        self.console = console
        self._lock = threading.Lock()
        self._chunk_status: Dict[int, str] = {}  # chunk_index -> status

    def chunk_started(self, chunk_index: int) -> None:
        """Mark a chunk as started (for logging purposes)."""
        with self._lock:
            self._chunk_status[chunk_index] = "in_progress"

    def chunk_completed(self, chunk_index: int, success: bool = True) -> None:
        """Mark a chunk as completed and print progress update."""
        with self._lock:
            if success:
                self.completed_chunks += 1
                self._chunk_status[chunk_index] = "completed"
            else:
                self.failed_chunks += 1
                self._chunk_status[chunk_index] = "failed"

            self._print_progress()

    def _print_progress(self) -> None:
        """Print a single-line progress update (called with lock held)."""
        if self.console.quiet:
            return

        done = self.completed_chunks + self.failed_chunks
        elapsed = time.time() - self.start_time

        # Build progress bar
        bar_width = 20
        filled = int(bar_width * done / self.total_chunks)
        bar = "█" * filled + "░" * (bar_width - filled)

        # Estimate remaining time
        if done > 0 and done < self.total_chunks:
            avg_time = elapsed / done
            remaining = avg_time * (self.total_chunks - done)
            eta_str = f", ~{self._format_time(remaining)} remaining"
        else:
            eta_str = ""

        status = f"Transcribing: [{bar}] {done}/{self.total_chunks} chunks"
        if self.failed_chunks > 0:
            status += f" ({self.failed_chunks} failed)"
        status += f" ({self._format_time(elapsed)}{eta_str})"

        # Use carriage return to overwrite the line (but print newline when done)
        import sys

        if done < self.total_chunks:
            sys.stdout.write(f"\r{status}")
            sys.stdout.flush()
        else:
            sys.stdout.write(f"\r{status}\n")
            sys.stdout.flush()

    def _format_time(self, seconds: float) -> str:
        """Format seconds into human-readable time string."""
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


@dataclass
class _ChunkTask:
    """
    Represents a chunk transcription task for parallel processing.

    Each task contains all the information needed to transcribe a single chunk
    independently, including timestamps for timestamp adjustment after transcription.
    """

    chunk_index: int
    start_ms: int
    end_ms: int
    audio_segment: AudioSegment


@dataclass
class _ChunkResult:
    """
    Result of a chunk transcription task.

    Contains the transcript data and metadata needed for merging.
    On error, transcript will be None and error will contain the message.
    """

    chunk_index: int
    start_ms: int
    end_ms: int
    transcript: Optional[Transcript] = None
    error: Optional[str] = None


class _SubChunkResult:
    """
    Wrapper for pre-formatted transcript from sub-chunk splitting.

    When a chunk times out and is split into smaller sub-chunks, the results
    are merged into a Transcript. This wrapper allows the main loop to
    detect that the result is already formatted (not a raw BatchRecognizeResults).
    """

    def __init__(self, transcript: Transcript):
        self.transcript = transcript


class GoogleCloudTranscriber(Transcriber):
    """
    Google Cloud Speech-to-Text V2 transcriber with Chirp 3 model.

    Features:
    - Uses Chirp 3 model for best-in-class accuracy
    - BatchRecognize for all transcriptions (required for diarization)
    - Built-in speaker diarization
    - Word-level timestamps
    - Automatic punctuation

    Output format matches WhisperTranscriber for compatibility with existing pipeline.

    Note: Chirp 3 and diarization require the V2 API with BatchRecognize.
    """

    def __init__(
        self,
        credentials_path: Optional[str] = None,
        project_id: Optional[str] = None,
        storage_bucket: Optional[str] = None,
        enable_diarization: bool = True,
        min_speakers: Optional[int] = None,
        max_speakers: Optional[int] = None,
        region: str = DEFAULT_REGION,
        parallel_chunks: int = 1,
        path_manager: Optional[PathManager] = None,
        console: Optional[ConsoleOutput] = None,
    ):
        """
        Initialize Google Cloud Speech V2 transcriber with Chirp 3.

        Args:
            credentials_path: Path to service account JSON key file
            project_id: Google Cloud project ID (required)
            storage_bucket: GCS bucket name for audio files (optional, will auto-create)
            enable_diarization: Enable speaker diarization (only available with BatchRecognize)
            min_speakers: Minimum number of speakers (None = auto-detect)
            max_speakers: Maximum number of speakers (None = auto-detect)
            region: Google Cloud region for Speech API (default: "eu")
            parallel_chunks: Number of chunks to transcribe in parallel (1 = sequential)
            path_manager: PathManager instance for storing operation state files
            console: ConsoleOutput instance for user-facing messages (optional)
        """
        if not project_id:
            raise ValueError("project_id is required for Speech-to-Text V2 API")

        self.credentials_path = credentials_path
        self.project_id = project_id
        self.storage_bucket = storage_bucket
        self.enable_diarization = enable_diarization
        self.min_speakers = min_speakers
        self.max_speakers = max_speakers
        self.region = region
        self.parallel_chunks = max(1, parallel_chunks)  # At least 1
        self.path_manager = path_manager or PathManager()
        self.console = console or ConsoleOutput()

        self.speech_client = None
        self.storage_client = None
        self._initialize_clients()

    def _initialize_clients(self):
        """Initialize Google Cloud clients for V2 API."""
        try:
            # Set up credentials
            credentials = None
            if self.credentials_path:
                credentials = service_account.Credentials.from_service_account_file(self.credentials_path)

            # Initialize Speech V2 client with regional endpoint
            client_options = ClientOptions(api_endpoint=f"{self.region}-speech.googleapis.com")

            if credentials:
                self.speech_client = SpeechClient(credentials=credentials, client_options=client_options)
                self.storage_client = storage.Client(credentials=credentials, project=self.project_id)
            else:
                # Use default credentials (e.g., from GOOGLE_APPLICATION_CREDENTIALS env var)
                self.speech_client = SpeechClient(client_options=client_options)
                try:
                    self.storage_client = storage.Client(project=self.project_id)
                except Exception:
                    self.console.warning(
                        "Google Cloud Storage client not available - transcription requires GCS for BatchRecognize"
                    )
        except Exception as e:
            raise RuntimeError(f"Failed to initialize Google Cloud clients: {e}")

    def load_model(self) -> None:
        """
        Load/initialize the transcription model.

        For GoogleCloudTranscriber, clients are initialized in __init__,
        so this method is a no-op. Provided for Transcriber interface compliance.
        """
        pass

    def _print_transcription_header(
        self,
        audio_path: str,
        duration_minutes: float,
        num_chunks: int,
        chunk_minutes: Optional[int] = None,
        overlap_seconds: Optional[int] = None,
    ) -> None:
        """
        Print a consolidated header with transcription settings.

        Args:
            audio_path: Path to the audio file
            duration_minutes: Total audio duration in minutes
            num_chunks: Number of chunks to transcribe
            chunk_minutes: Duration of each chunk in minutes (optional)
            overlap_seconds: Overlap between chunks in seconds (optional)
        """
        filename = Path(audio_path).name
        self.console.info(f"📝 Transcribing: {filename}")
        self.console.info(f"   Provider: Google Cloud Speech V2 (chirp_3, {self.region} region)")

        if num_chunks > 1:
            self.console.info(
                f"   Audio: {duration_minutes:.1f} min → {num_chunks} chunks ({chunk_minutes} min each, {overlap_seconds}s overlap)"
            )
            if self.parallel_chunks > 1:
                workers = min(self.parallel_chunks, num_chunks)
                self.console.info(f"   Workers: {workers} parallel")
        else:
            self.console.info(f"   Audio: {duration_minutes:.1f} min")

        if self.enable_diarization:
            min_spk = self.min_speakers if self.min_speakers else 1
            max_spk = self.max_speakers if self.max_speakers else 6
            self.console.info(f"   Diarization: enabled ({min_spk}-{max_spk} speakers)")

    def _configure_bucket_lifecycle(self, bucket) -> None:
        """
        Configure bucket lifecycle to auto-delete temp files after N days.

        This prevents orphaned files if the app crashes before cleanup.
        Applies to:
        - temp-audio/: Audio files uploaded for transcription (7 days)
        - transcripts/: Transcript output files from BatchRecognize (configurable)
        """
        bucket.lifecycle_rules = [
            {
                "action": {"type": "Delete"},
                "condition": {"age": 7, "matchesPrefix": ["temp-audio/"]},
            },
            {
                "action": {"type": "Delete"},
                "condition": {
                    "age": GCS_TRANSCRIPT_LIFECYCLE_DAYS,
                    "matchesPrefix": [GCS_TRANSCRIPT_OUTPUT_PREFIX],
                },
            },
        ]
        bucket.patch()

    def _sanitize_filename(self, text: str) -> str:
        """
        Sanitize text for use in file names.

        Replaces spaces with underscores, removes special characters,
        and collapses multiple underscores.
        """
        if not text:
            return ""
        # Replace spaces with underscores, remove non-alphanumeric chars except underscores/hyphens
        sanitized = "".join(c if c.isalnum() or c in "-_" else "_" for c in text)
        # Collapse multiple underscores and trim
        while "__" in sanitized:
            sanitized = sanitized.replace("__", "_")
        return sanitized.strip("_")

    def _calculate_equal_chunks(self, duration_ms: int) -> List[Tuple[int, int]]:
        """
        Calculate equal-length chunk boundaries with consistent overlap.

        Instead of fixed-size chunks with a variable-length last chunk,
        this calculates the number of chunks needed and distributes the
        audio equally among them, ensuring:
        1. All chunks are approximately equal length
        2. All overlaps are approximately equal (target: OVERLAP_DURATION_MS)
        3. Last chunk ends exactly at duration_ms (no audio cut off)

        Algorithm:
        - effective_advance = MAX_CHUNK_DURATION_MS - OVERLAP_DURATION_MS (8 min)
        - num_chunks = ceil((duration_ms - OVERLAP_DURATION_MS) / effective_advance)
        - Then calculate start positions to distribute evenly

        Args:
            duration_ms: Total audio duration in milliseconds

        Returns:
            List of (start_ms, end_ms) tuples for each chunk
        """
        if duration_ms <= MAX_CHUNK_DURATION_MS:
            # Single chunk covers everything
            return [(0, duration_ms)]

        # Calculate number of chunks needed
        # Each chunk after the first adds (chunk_duration - overlap) of new content
        effective_advance = MAX_CHUNK_DURATION_MS - OVERLAP_DURATION_MS

        # We need enough chunks so that:
        # chunk_duration + (num_chunks - 1) * effective_advance >= duration_ms
        # Solving: num_chunks >= (duration_ms - OVERLAP_DURATION_MS) / effective_advance
        num_chunks = math.ceil((duration_ms - OVERLAP_DURATION_MS) / effective_advance)

        # Ensure at least 2 chunks (we already know duration > MAX_CHUNK_DURATION_MS)
        num_chunks = max(2, num_chunks)

        # Calculate the actual advance between chunk starts to distribute evenly
        # With N chunks, we have N-1 advances, and the last chunk must end at duration_ms
        # start[i] = i * actual_advance
        # end[N-1] = duration_ms
        # start[N-1] + chunk_length = duration_ms
        # (N-1) * actual_advance + chunk_length = duration_ms
        #
        # We want consistent chunk lengths, so:
        # chunk_length = (duration_ms + (num_chunks - 1) * overlap) / num_chunks
        total_with_overlaps = duration_ms + (num_chunks - 1) * OVERLAP_DURATION_MS
        chunk_length = total_with_overlaps // num_chunks

        # Ensure chunk_length doesn't exceed MAX_CHUNK_DURATION_MS
        chunk_length = min(chunk_length, MAX_CHUNK_DURATION_MS)

        # Calculate actual advance (distance between chunk starts)
        # actual_advance = chunk_length - overlap
        actual_advance = chunk_length - OVERLAP_DURATION_MS

        chunks = []
        for i in range(num_chunks):
            start_ms = i * actual_advance

            if i == num_chunks - 1:
                # Last chunk: ensure it ends exactly at duration_ms
                end_ms = duration_ms
            else:
                end_ms = start_ms + chunk_length

            chunks.append((start_ms, end_ms))

        return chunks

    def transcribe_audio(
        self,
        audio_path: str,
        output_path: Optional[str] = None,
        *,
        options: TranscribeOptions,
    ) -> Optional[Transcript]:
        """
        Transcribe audio file using Chirp 3 with optional speaker diarization.

        For files longer than 30 minutes, the audio is automatically split into
        chunks with 1-minute overlap, transcribed separately, and merged.

        Args:
            audio_path: Path to audio file (must be downsampled 16kHz WAV)
            output_path: Path to save transcript JSON
            options: Transcription options including language and episode context.

        Returns:
            Transcript object, or None on error
        """
        try:
            start_time = time.time()

            # Load audio to check duration
            audio = AudioSegment.from_file(audio_path)
            duration_ms = len(audio)
            duration_minutes = duration_ms / 1000 / 60

            if duration_ms > MAX_CHUNK_DURATION_MS:
                # Calculate chunks for header display
                chunks = self._calculate_equal_chunks(duration_ms)
                num_chunks = len(chunks)

                # Print consolidated header
                self._print_transcription_header(
                    audio_path=audio_path,
                    duration_minutes=duration_minutes,
                    num_chunks=num_chunks,
                    chunk_minutes=MAX_CHUNK_DURATION_MS // 60000,
                    overlap_seconds=OVERLAP_DURATION_MS // 1000,
                )

                transcript_data = self._transcribe_chunked(
                    audio,
                    audio_path,
                    options.language,
                    output_path,
                    None,  # podcast_title no longer passed
                    episode_id=options.episode_id,
                    podcast_slug=options.podcast_slug,
                    episode_slug=options.episode_slug,
                )
            else:
                # Short audio - transcribe directly without chunking
                self._print_transcription_header(
                    audio_path=audio_path,
                    duration_minutes=duration_minutes,
                    num_chunks=1,
                )
                result = self._transcribe_batch(
                    audio_path,
                    options.language,
                    None,  # podcast_title no longer passed
                    episode_id=options.episode_id,
                    podcast_slug=options.podcast_slug,
                    episode_slug=options.episode_slug,
                )
                transcript_data = self._format_transcript(
                    result, time.time() - start_time, audio_path, options.language
                )

            processing_time = time.time() - start_time
            transcript_data.processing_time = processing_time
            self.console.success(f"Transcription completed in {self._format_time(processing_time)}")

            if output_path:
                self._save_transcript(transcript_data, output_path)

            return transcript_data

        except Exception as e:
            self.console.error(f"Error transcribing {audio_path}: {e}")
            import traceback

            traceback.print_exc()
            return None

    def _transcribe_chunked(
        self,
        audio: AudioSegment,
        original_path: str,
        language: str,
        output_path: Optional[str] = None,
        podcast_title: Optional[str] = None,
        episode_id: Optional[str] = None,
        podcast_slug: Optional[str] = None,
        episode_slug: Optional[str] = None,
    ) -> Transcript:
        """
        Split audio into chunks, transcribe each (optionally in parallel), and merge results.

        Args:
            audio: Loaded AudioSegment
            original_path: Original audio file path (for metadata)
            language: Language code
            output_path: Path for final transcript (used for debug chunk files)
            podcast_title: Optional podcast title used as prefix for temp files in GCS
            episode_id: Optional episode UUID for operation persistence
            podcast_slug: Optional podcast slug for operation persistence
            episode_slug: Optional episode slug for operation persistence

        Returns:
            Merged Transcript object
        """
        duration_ms = len(audio)
        chunks = self._calculate_equal_chunks(duration_ms)

        # Determine debug output directory (same as final transcript location)
        debug_dir = None
        if output_path:
            debug_dir = Path(output_path).parent / "chunks"
            debug_dir.mkdir(parents=True, exist_ok=True)

        # Create filename prefix from podcast title
        sanitized_title = self._sanitize_filename(podcast_title) if podcast_title else ""
        file_prefix = f"{sanitized_title}_" if sanitized_title else ""

        # Check for pending operations from previous run (resumption support)
        resumed_results: Dict[int, _ChunkResult] = {}
        chunks_to_transcribe: List[int] = list(range(len(chunks)))
        chunks_in_progress: List[int] = []  # Chunks still running on GCS

        if episode_id:
            pending_ops = self.get_pending_operations_for_episode(episode_id)
            if pending_ops:
                logger.info("Found pending chunk operations", chunk_count=len(pending_ops), episode_id=episode_id)
                self.console.info(f"   Resuming: Found {len(pending_ops)} pending chunk operation(s)")

                for op in pending_ops:
                    if op.chunk_index is not None:
                        try:
                            # Check if operation completed
                            updated_op = self.check_pending_operation(op)

                            if updated_op.state == TranscriptionOperationState.COMPLETED:
                                # Download the transcript
                                transcript_data = self.download_completed_operation(updated_op)
                                resumed_results[op.chunk_index] = _ChunkResult(
                                    chunk_index=op.chunk_index,
                                    start_ms=op.chunk_start_ms or 0,
                                    end_ms=op.chunk_end_ms or 0,
                                    transcript=transcript_data,
                                )
                                # Remove from chunks to transcribe
                                if op.chunk_index in chunks_to_transcribe:
                                    chunks_to_transcribe.remove(op.chunk_index)
                                self.console.success(f"Chunk {op.chunk_index + 1}/{len(chunks)} resumed from GCS")
                            elif updated_op.state == TranscriptionOperationState.FAILED:
                                logger.warning("Chunk failed", chunk_index=op.chunk_index, error=updated_op.error)
                                self._delete_operation(op.operation_id)
                                # Keep in chunks_to_transcribe to retry
                            else:
                                # Still pending - track it and remove from chunks_to_transcribe
                                chunks_in_progress.append(op.chunk_index)
                                if op.chunk_index in chunks_to_transcribe:
                                    chunks_to_transcribe.remove(op.chunk_index)
                                logger.info("Chunk still in progress on GCS", chunk_index=op.chunk_index)
                                self.console.progress(
                                    f"Chunk {op.chunk_index + 1}/{len(chunks)} still in progress on GCS"
                                )

                        except Exception as e:
                            logger.warning("Failed to resume chunk", chunk_index=op.chunk_index, error=str(e))
                            self._delete_operation(op.operation_id)
                            # Keep in chunks_to_transcribe to retry

        # Respect parallel_chunks limit including in-progress operations on GCS
        # If we have 3 chunks in progress and parallel_chunks=3, don't start any new ones
        available_slots = max(0, self.parallel_chunks - len(chunks_in_progress))
        if chunks_in_progress and available_slots < len(chunks_to_transcribe):
            logger.info(
                "Limiting new chunks",
                available_slots=available_slots,
                parallel_chunks=self.parallel_chunks,
                in_progress=len(chunks_in_progress),
            )
            self.console.info(
                f"   ℹ️ {len(chunks_in_progress)} chunk(s) running on GCS, "
                f"limiting new uploads to {available_slots} (max parallel={self.parallel_chunks})"
            )
            chunks_to_transcribe = chunks_to_transcribe[:available_slots]

        # If all slots are taken by in-progress chunks, wait for them to complete
        if chunks_in_progress and not chunks_to_transcribe and not resumed_results:
            self.console.progress(f"All {self.parallel_chunks} slots in use, waiting for chunks to complete...")
            self.console.info(f"   (Checking every 30 seconds. Press Ctrl+C to cancel)")

            # We need to wait for in-progress chunks and collect their results
            # Re-fetch pending ops and poll until some complete
            # Note: chunks_in_progress can only be populated when episode_id is set
            assert episode_id is not None
            while chunks_in_progress:
                time.sleep(30)
                pending_ops = self.get_pending_operations_for_episode(episode_id)

                newly_completed = []
                still_pending = []

                for op in pending_ops:
                    if op.chunk_index in chunks_in_progress:
                        try:
                            updated_op = self.check_pending_operation(op)
                            if updated_op.state == TranscriptionOperationState.COMPLETED:
                                transcript_data = self.download_completed_operation(updated_op)
                                resumed_results[op.chunk_index] = _ChunkResult(
                                    chunk_index=op.chunk_index,
                                    start_ms=op.chunk_start_ms or 0,
                                    end_ms=op.chunk_end_ms or 0,
                                    transcript=transcript_data,
                                )
                                newly_completed.append(op.chunk_index)
                                self.console.success(f"Chunk {op.chunk_index + 1}/{len(chunks)} completed")
                            elif updated_op.state == TranscriptionOperationState.FAILED:
                                logger.warning("Chunk failed", chunk_index=op.chunk_index, error=updated_op.error)
                                self._delete_operation(op.operation_id)
                                # Add back to chunks_to_transcribe for retry
                                chunks_to_transcribe.append(op.chunk_index)
                                newly_completed.append(op.chunk_index)
                            else:
                                still_pending.append(op.chunk_index)
                        except Exception as e:
                            logger.warning("Error checking chunk", chunk_index=op.chunk_index, error=str(e))
                            still_pending.append(op.chunk_index)

                # Update in-progress list
                chunks_in_progress = still_pending

                if newly_completed:
                    # Some chunks completed, we can now start more
                    remaining_chunks = [
                        i for i in range(len(chunks)) if i not in resumed_results and i not in chunks_in_progress
                    ]
                    available_slots = max(0, self.parallel_chunks - len(chunks_in_progress))
                    chunks_to_transcribe = remaining_chunks[:available_slots]

                    if chunks_to_transcribe:
                        self.console.info(f"   ➡️ Starting {len(chunks_to_transcribe)} more chunk(s)")
                        break  # Exit wait loop to start new chunks
                    elif not chunks_in_progress:
                        break  # All done

                if chunks_in_progress:
                    self.console.progress(f"Still waiting for {len(chunks_in_progress)} chunk(s)...")

        # Create progress tracker for coordinated output
        total_to_transcribe = len(chunks_to_transcribe)
        progress_tracker = _ProgressTracker(
            total_chunks=total_to_transcribe if total_to_transcribe > 0 else len(chunks),
            total_duration_ms=duration_ms,
            console=self.console,
        )

        # Create chunk tasks only for chunks that need transcription
        chunk_tasks = [
            _ChunkTask(
                chunk_index=i,
                start_ms=chunks[i][0],
                end_ms=chunks[i][1],
                audio_segment=audio[chunks[i][0] : chunks[i][1]],
            )
            for i in chunks_to_transcribe
        ]

        # Total chunks is the ORIGINAL count, not len(chunk_tasks) which may be fewer after resumption
        total_chunks = len(chunks)

        # Log what we're about to transcribe for debugging
        if chunk_tasks:
            chunk_indices = [t.chunk_index for t in chunk_tasks]
            logger.info(
                "Will transcribe chunks",
                chunk_count=len(chunk_tasks),
                chunk_indices=chunk_indices,
                total_chunks=total_chunks,
                resumed_count=len(resumed_results),
            )
            if resumed_results:
                self.console.info(f"   Will transcribe chunks: {[i+1 for i in chunk_indices]} of {total_chunks}")

        # Transcribe remaining chunks (parallel or sequential based on configuration)
        if chunk_tasks:
            if self.parallel_chunks > 1 and len(chunk_tasks) > 1:
                chunk_results = self._transcribe_chunks_parallel(
                    chunk_tasks,
                    language,
                    podcast_title,
                    debug_dir,
                    file_prefix,
                    progress_tracker=progress_tracker,
                    episode_id=episode_id,
                    podcast_slug=podcast_slug,
                    episode_slug=episode_slug,
                    total_chunks=total_chunks,
                )
            else:
                chunk_results = self._transcribe_chunks_sequential(
                    chunk_tasks,
                    language,
                    podcast_title,
                    debug_dir,
                    file_prefix,
                    progress_tracker=progress_tracker,
                    episode_id=episode_id,
                    podcast_slug=podcast_slug,
                    episode_slug=episode_slug,
                    total_chunks=total_chunks,
                )
        else:
            chunk_results = []

        # Combine resumed results with newly transcribed results
        all_results = list(resumed_results.values()) + chunk_results

        # Check for errors in newly transcribed chunks
        errors = [r for r in chunk_results if r.error]
        if errors:
            error_msgs = "; ".join(f"chunk {r.chunk_index + 1}: {r.error}" for r in errors)
            raise RuntimeError(f"Failed to transcribe chunks: {error_msgs}")

        # Sort results by chunk index for sequential processing
        sorted_results = sorted(all_results, key=lambda x: x.chunk_index)

        if not sorted_results:
            return self._empty_transcript(original_path)

        if len(sorted_results) == 1:
            return sorted_results[0].transcript

        logger.info("Merging chunk transcripts", chunk_count=len(sorted_results))

        # Reconcile speakers across chunks using overlap regions, then merge
        # Each chunk has overlap with the next, so we process sequentially
        merged_transcript = sorted_results[0].transcript

        for i in range(1, len(sorted_results)):
            prev_result = sorted_results[i - 1]
            curr_result = sorted_results[i]

            # Calculate overlap region (in absolute time)
            overlap_start = curr_result.start_ms / 1000  # Convert to seconds
            overlap_end = prev_result.end_ms / 1000

            if overlap_start < overlap_end:
                # Build speaker mapping from current chunk to merged transcript
                speaker_mapping = curr_result.transcript.build_speaker_mapping(
                    other=merged_transcript,
                    overlap_start=overlap_start,
                    overlap_end=overlap_end,
                    match_window_sec=OVERLAP_MATCH_WINDOW_MS / 1000,
                    min_votes=MIN_SPEAKER_VOTES,
                )

                if speaker_mapping:
                    # Apply mapping to current chunk before merging
                    curr_transcript = curr_result.transcript.apply_speaker_mapping(speaker_mapping)
                    logger.info(
                        "Speaker mapping applied",
                        from_chunk=i,
                        to_chunk=i + 1,
                        mapping=speaker_mapping,
                    )
                else:
                    curr_transcript = curr_result.transcript
                    logger.debug(
                        "No speaker mapping", from_chunk=i, to_chunk=i + 1, reason="insufficient matches in overlap"
                    )
            else:
                curr_transcript = curr_result.transcript
                logger.debug("No speaker mapping", from_chunk=i, to_chunk=i + 1, reason="no overlap")

            # Merge current chunk into accumulated transcript
            merged_transcript = merged_transcript.merge(curr_transcript)

        # Update metadata for merged transcript
        speakers = merged_transcript.get_speakers()
        return Transcript(
            audio_file=original_path,
            language=language,
            text=merged_transcript.text,
            segments=merged_transcript.segments,
            processing_time=0,  # Will be updated by caller
            model_used="google-cloud-speech-v2-chirp_3",
            timestamp=time.time(),
            diarization_enabled=self.enable_diarization,
            speakers_detected=len(speakers) if speakers else None,
            provider_metadata={"chunks_processed": len(sorted_results)},
        )

    def _transcribe_chunks_sequential(
        self,
        tasks: List[_ChunkTask],
        language: str,
        podcast_title: Optional[str],
        debug_dir: Optional[Path],
        file_prefix: str,
        progress_tracker: Optional[_ProgressTracker] = None,
        episode_id: Optional[str] = None,
        podcast_slug: Optional[str] = None,
        episode_slug: Optional[str] = None,
        total_chunks: Optional[int] = None,
    ) -> List[_ChunkResult]:
        """
        Transcribe chunks sequentially (original behavior).

        Args:
            tasks: List of chunk tasks to process
            language: Language code
            podcast_title: Optional podcast title for GCS naming
            debug_dir: Directory for debug output
            file_prefix: Prefix for debug files
            progress_tracker: Optional progress tracker for coordinated output
            episode_id: Optional episode UUID for operation persistence
            podcast_slug: Optional podcast slug for operation persistence
            episode_slug: Optional episode slug for operation persistence
            total_chunks: Total number of chunks (for naming, may differ from len(tasks) when resuming)

        Returns:
            List of chunk results
        """
        results = []
        # Use provided total_chunks or fall back to len(tasks)
        actual_total_chunks = total_chunks if total_chunks is not None else len(tasks)
        for task in tasks:
            result = self._transcribe_single_chunk(
                task,
                language,
                podcast_title,
                debug_dir,
                file_prefix,
                progress_tracker=progress_tracker,
                episode_id=episode_id,
                podcast_slug=podcast_slug,
                episode_slug=episode_slug,
                total_chunks=actual_total_chunks,
            )
            results.append(result)
        return results

    def _transcribe_chunks_parallel(
        self,
        tasks: List[_ChunkTask],
        language: str,
        podcast_title: Optional[str],
        debug_dir: Optional[Path],
        file_prefix: str,
        progress_tracker: Optional[_ProgressTracker] = None,
        episode_id: Optional[str] = None,
        podcast_slug: Optional[str] = None,
        episode_slug: Optional[str] = None,
        total_chunks: Optional[int] = None,
    ) -> List[_ChunkResult]:
        """
        Transcribe chunks in parallel using ThreadPoolExecutor.

        Each worker creates its own Google Cloud clients for thread safety.

        Args:
            tasks: List of chunk tasks to process
            language: Language code
            podcast_title: Optional podcast title for GCS naming
            debug_dir: Directory for debug output
            file_prefix: Prefix for debug files
            progress_tracker: Optional progress tracker for coordinated output
            episode_id: Optional episode UUID for operation persistence
            podcast_slug: Optional podcast slug for operation persistence
            episode_slug: Optional episode slug for operation persistence
            total_chunks: Total number of chunks (for naming, may differ from len(tasks) when resuming)

        Returns:
            List of chunk results
        """
        num_workers = min(self.parallel_chunks, len(tasks))
        results: List[_ChunkResult] = []
        # Use provided total_chunks or fall back to len(tasks)
        actual_total_chunks = total_chunks if total_chunks is not None else len(tasks)

        with ThreadPoolExecutor(max_workers=num_workers) as executor:
            # Submit all tasks
            future_to_task = {
                executor.submit(
                    self._transcribe_single_chunk_worker,
                    task,
                    language,
                    podcast_title,
                    debug_dir,
                    file_prefix,
                    progress_tracker,
                    episode_id,
                    podcast_slug,
                    episode_slug,
                    actual_total_chunks,
                ): task
                for task in tasks
            }

            # Collect results as they complete
            for future in as_completed(future_to_task):
                task = future_to_task[future]
                try:
                    result = future.result()
                    results.append(result)
                    # Progress updates happen inside _transcribe_single_chunk via progress_tracker
                except Exception as e:
                    results.append(
                        _ChunkResult(
                            chunk_index=task.chunk_index,
                            start_ms=task.start_ms,
                            end_ms=task.end_ms,
                            transcript=None,
                            error=str(e),
                        )
                    )
                    if progress_tracker:
                        progress_tracker.chunk_completed(task.chunk_index, success=False)

        return results

    def _transcribe_single_chunk_worker(
        self,
        task: _ChunkTask,
        language: str,
        podcast_title: Optional[str],
        debug_dir: Optional[Path],
        file_prefix: str,
        progress_tracker: Optional[_ProgressTracker] = None,
        episode_id: Optional[str] = None,
        podcast_slug: Optional[str] = None,
        episode_slug: Optional[str] = None,
        total_chunks: Optional[int] = None,
    ) -> _ChunkResult:
        """
        Worker function for parallel chunk transcription.

        Creates its own Google Cloud clients for thread safety.

        Args:
            task: Chunk task to process
            language: Language code
            podcast_title: Optional podcast title for GCS naming
            debug_dir: Directory for debug output
            file_prefix: Prefix for debug files
            progress_tracker: Optional progress tracker for coordinated output

        Returns:
            Chunk result
        """
        # Create per-worker Google Cloud clients for thread safety
        # Use _quiet=True to suppress duplicate initialization messages
        worker_transcriber = GoogleCloudTranscriber(
            credentials_path=self.credentials_path,
            project_id=self.project_id,
            storage_bucket=self.storage_bucket,
            enable_diarization=self.enable_diarization,
            min_speakers=self.min_speakers,
            max_speakers=self.max_speakers,
            region=self.region,
            parallel_chunks=1,  # Workers don't spawn more workers
            path_manager=self.path_manager,
            _quiet=True,
        )

        return worker_transcriber._transcribe_single_chunk(
            task,
            language,
            podcast_title,
            debug_dir,
            file_prefix,
            progress_tracker=progress_tracker,
            episode_id=episode_id,
            podcast_slug=podcast_slug,
            episode_slug=episode_slug,
            total_chunks=total_chunks,
        )

    def _transcribe_single_chunk(
        self,
        task: _ChunkTask,
        language: str,
        podcast_title: Optional[str],
        debug_dir: Optional[Path],
        file_prefix: str,
        progress_tracker: Optional[_ProgressTracker] = None,
        episode_id: Optional[str] = None,
        podcast_slug: Optional[str] = None,
        episode_slug: Optional[str] = None,
        total_chunks: Optional[int] = None,
    ) -> _ChunkResult:
        """
        Transcribe a single chunk with retry logic.

        Args:
            task: Chunk task containing audio segment and metadata
            language: Language code
            podcast_title: Optional podcast title for GCS naming
            debug_dir: Directory for debug output
            file_prefix: Prefix for debug files
            progress_tracker: Optional progress tracker for coordinated output
            episode_id: Optional episode UUID for operation persistence
            podcast_slug: Optional podcast slug for operation persistence
            episode_slug: Optional episode slug for operation persistence
            total_chunks: Optional total number of chunks for this episode

        Returns:
            Chunk result with transcript or error
        """
        i = task.chunk_index
        start_ms = task.start_ms
        end_ms = task.end_ms
        chunk_audio = task.audio_segment

        logger.debug(
            "Starting chunk transcription",
            chunk_num=i + 1,
            start_seconds=round(start_ms / 1000, 1),
            end_seconds=round(end_ms / 1000, 1),
        )

        if progress_tracker:
            progress_tracker.chunk_started(i)

        # Save to temporary file
        with tempfile.NamedTemporaryFile(suffix=".wav", delete=False) as tmp_file:
            tmp_path = tmp_file.name
            chunk_audio.export(tmp_path, format="wav")

        try:
            # Transcribe chunk with retry logic
            result = None
            last_error = None

            for attempt in range(MAX_CHUNK_RETRIES + 1):
                try:
                    logger.debug(
                        "Calling _transcribe_batch",
                        chunk_num=i + 1,
                        attempt=attempt + 1,
                        max_attempts=MAX_CHUNK_RETRIES + 1,
                    )
                    result = self._transcribe_batch(
                        tmp_path,
                        language,
                        podcast_title,
                        episode_id=episode_id,
                        podcast_slug=podcast_slug,
                        episode_slug=episode_slug,
                        chunk_index=i,
                        chunk_start_ms=start_ms,
                        chunk_end_ms=end_ms,
                        total_chunks=total_chunks,
                    )
                    logger.debug("Chunk transcribe_batch completed successfully", chunk_num=i + 1)
                    break  # Success
                except TimeoutError as e:
                    last_error = e
                    logger.warning(
                        "Chunk timeout, will retry with split",
                        chunk_num=i + 1,
                        attempt=attempt + 1,
                    )
                    # Timeout retry is silent - progress tracker will show overall progress
                    if attempt < MAX_CHUNK_RETRIES:
                        # Retry by splitting this chunk in half
                        result = self._transcribe_chunk_split(
                            chunk_audio,
                            start_ms,
                            language,
                            podcast_title,
                            debug_dir,
                            file_prefix,
                            i,
                            episode_id=episode_id,
                            podcast_slug=podcast_slug,
                            episode_slug=episode_slug,
                            total_chunks=total_chunks,
                        )
                        if result:
                            break
                    continue

            if result is None:
                if progress_tracker:
                    progress_tracker.chunk_completed(i, success=False)
                return _ChunkResult(
                    chunk_index=i,
                    start_ms=start_ms,
                    end_ms=end_ms,
                    transcript=self._empty_transcript(tmp_path),
                    error=f"Failed after {MAX_CHUNK_RETRIES + 1} attempts: {last_error}",
                )

            # Check if result is from sub-chunk splitting (already formatted)
            if isinstance(result, _SubChunkResult):
                # Already formatted and timestamps adjusted in _transcribe_chunk_split
                transcript = result.transcript
            else:
                # Normal result - format and adjust timestamps using typed methods
                transcript = self._format_transcript(result, 0, tmp_path, language)

                # Adjust timestamps by chunk offset
                offset_seconds = start_ms / 1000
                transcript = transcript.adjust_timestamps(offset_seconds)

                # Save chunk transcript for debugging (silent)
                if debug_dir:
                    chunk_path = debug_dir / f"{file_prefix}chunk_{i+1:02d}.json"
                    with open(chunk_path, "w", encoding="utf-8") as f:
                        json.dump(transcript.model_dump(), f, indent=2, ensure_ascii=False)

            if progress_tracker:
                progress_tracker.chunk_completed(i, success=True)

            return _ChunkResult(
                chunk_index=i,
                start_ms=start_ms,
                end_ms=end_ms,
                transcript=transcript,
            )
        finally:
            # Clean up temp file
            Path(tmp_path).unlink(missing_ok=True)

    def _transcribe_chunk_split(
        self,
        chunk_audio: AudioSegment,
        chunk_start_ms: int,
        language: str,
        podcast_title: Optional[str],
        debug_dir: Optional[Path],
        file_prefix: str,
        chunk_index: int,
        episode_id: Optional[str] = None,
        podcast_slug: Optional[str] = None,
        episode_slug: Optional[str] = None,
        total_chunks: Optional[int] = None,
    ) -> Optional[Any]:
        """
        Split a stuck chunk in half and transcribe both halves.

        This is a fallback for chunks that time out - smaller chunks often
        complete faster on Google Cloud Speech-to-Text.

        Args:
            chunk_audio: The audio segment that timed out
            chunk_start_ms: Start time of this chunk in the original audio
            language: Language code
            podcast_title: Optional podcast title for GCS naming
            debug_dir: Directory for debug output
            file_prefix: Prefix for debug files
            chunk_index: Original chunk index (for logging)
            episode_id: Optional episode UUID for operation persistence
            podcast_slug: Optional podcast slug for GCS naming
            episode_slug: Optional episode slug for GCS naming
            total_chunks: Optional total number of chunks

        Returns:
            Raw transcription result (BatchRecognizeResults) or None on failure
        """
        duration_ms = len(chunk_audio)
        half_duration_ms = duration_ms // 2
        sub_overlap_ms = 30 * 1000  # 30 second overlap for sub-chunks

        # First sub-chunk: 0 to half + overlap
        # Second sub-chunk: half - overlap to end
        # This creates overlap in the middle for seamless merging
        sub_chunks = [
            (0, min(half_duration_ms + sub_overlap_ms, duration_ms)),
            (max(0, half_duration_ms - sub_overlap_ms), duration_ms),
        ]

        # Sub-chunk splitting is silent - progress tracker shows overall progress
        sub_transcripts: List[Transcript] = []
        for sub_idx, (sub_start, sub_end) in enumerate(sub_chunks):
            sub_audio = chunk_audio[sub_start:sub_end]

            # Save to temp file
            with tempfile.NamedTemporaryFile(suffix=".wav", delete=False) as tmp_file:
                tmp_path = tmp_file.name
                sub_audio.export(tmp_path, format="wav")

            try:
                # For sub-chunks, use naming like chunk-01a-of-05, chunk-01b-of-05
                result = self._transcribe_batch(
                    tmp_path,
                    language,
                    podcast_title,
                    episode_id=episode_id,
                    podcast_slug=podcast_slug,
                    episode_slug=episode_slug,
                    chunk_index=chunk_index,
                    chunk_start_ms=chunk_start_ms + sub_start,
                    chunk_end_ms=chunk_start_ms + sub_end,
                    total_chunks=total_chunks,
                )

                # Format and adjust timestamps using typed methods
                transcript = self._format_transcript(result, 0, tmp_path, language)
                offset_seconds = (chunk_start_ms + sub_start) / 1000
                transcript = transcript.adjust_timestamps(offset_seconds)

                # Save debug output (silent)
                if debug_dir:
                    sub_chunk_path = debug_dir / f"{file_prefix}chunk_{chunk_index + 1:02d}_{sub_idx + 1}_adjusted.json"
                    with open(sub_chunk_path, "w", encoding="utf-8") as f:
                        json.dump(transcript.model_dump(), f, indent=2, ensure_ascii=False)

                sub_transcripts.append(transcript)

            except Exception:
                # Sub-chunk failure is silent - main chunk will be marked as failed
                return None
            finally:
                Path(tmp_path).unlink(missing_ok=True)

        # Merge sub-chunk transcripts using typed method
        if len(sub_transcripts) == 2:
            merged = sub_transcripts[0].merge(sub_transcripts[1])
            return _SubChunkResult(merged)

        return None

    def _empty_transcript(self, audio_path: str) -> Transcript:
        """Return an empty transcript structure."""
        return Transcript(
            audio_file=audio_path,
            language="en-US",
            text="",
            segments=[],
            processing_time=0,
            model_used="google-cloud-speech-v2-chirp_3",
            timestamp=time.time(),
            diarization_enabled=self.enable_diarization,
            speakers_detected=0,
        )

    def _transcribe_batch(
        self,
        audio_path: str,
        language: str,
        podcast_title: Optional[str] = None,
        disable_diarization: bool = False,
        episode_id: Optional[str] = None,
        podcast_slug: Optional[str] = None,
        episode_slug: Optional[str] = None,
        chunk_index: Optional[int] = None,
        chunk_start_ms: Optional[int] = None,
        chunk_end_ms: Optional[int] = None,
        total_chunks: Optional[int] = None,
    ) -> Any:
        """
        Transcribe using BatchRecognize with GCS output (supports audio up to 8 hours).

        BatchRecognize is the only method that supports both Chirp 3 and diarization.
        Audio is uploaded to GCS, and transcripts are written to GCS then downloaded.

        This approach:
        - Supports audio files up to 8 hours (480 minutes)
        - Writes transcripts to GCS (auto-deleted after N days via lifecycle rule)
        - Downloads and parses the transcript after completion
        - Cleans up both audio and transcript files from GCS
        - Persists operation state for resumption if app restarts during transcription

        Args:
            audio_path: Path to audio file
            language: Language code
            podcast_title: Optional podcast title used as prefix for temp files in GCS
            disable_diarization: Force disable diarization for this request (for retry fallback)
            episode_id: Optional episode UUID for operation persistence
            podcast_slug: Optional podcast slug for operation persistence
            episode_slug: Optional episode slug for operation persistence
            chunk_index: Optional chunk index for multi-chunk transcriptions
            chunk_start_ms: Optional chunk start time offset
            chunk_end_ms: Optional chunk end time offset
            total_chunks: Optional total number of chunks

        Returns:
            BatchRecognizeResults protobuf with transcript data
        """
        if not self.storage_client:
            raise RuntimeError(
                "Google Cloud Storage client not available. GCS is required for BatchRecognize with Chirp 3."
            )

        # Determine bucket name
        bucket_name = self.storage_bucket
        if not bucket_name:
            bucket_name = f"thestill-transcription-{self.project_id}"

        # Create blob names using slugs for human-readable GCS paths
        # Format: temp-audio/{podcast-slug}/{episode-slug}/chunk-01-of-05.wav
        # Or for non-chunked: temp-audio/{podcast-slug}/{episode-slug}/full.wav
        if podcast_slug and episode_slug:
            # Use slugs for organized, human-readable paths
            if chunk_index is not None and total_chunks is not None:
                chunk_name = f"chunk-{chunk_index + 1:02d}-of-{total_chunks:02d}"
            else:
                chunk_name = "full"

            audio_blob_name = f"temp-audio/{podcast_slug}/{episode_slug}/{chunk_name}.wav"
            output_prefix = f"{GCS_TRANSCRIPT_OUTPUT_PREFIX}{podcast_slug}/{episode_slug}/{chunk_name}/"
        else:
            # Fallback to timestamp-based naming for standalone files
            sanitized_title = self._sanitize_filename(podcast_title) if podcast_title else ""
            prefix = f"{sanitized_title}_" if sanitized_title else ""
            timestamp = datetime.now().strftime("%Y%m%d-%H%M%S")
            audio_filename = Path(audio_path).stem

            audio_blob_name = f"temp-audio/{prefix}{timestamp}-{Path(audio_path).name}"
            output_prefix = f"{GCS_TRANSCRIPT_OUTPUT_PREFIX}{prefix}{timestamp}_{audio_filename}/"

        audio_gcs_uri = f"gs://{bucket_name}/{audio_blob_name}"
        output_gcs_uri = f"gs://{bucket_name}/{output_prefix}"

        audio_blob = None
        try:
            # Get or create bucket
            bucket = self.storage_client.bucket(bucket_name)
            if not bucket.exists():
                self.console.info(f"Creating GCS bucket: {bucket_name}")
                bucket.create(location="US")
                # Add lifecycle rules for both temp-audio/ and transcripts/ folders
                self._configure_bucket_lifecycle(bucket)

            # Upload audio file with smaller chunks and extended timeout for reliability
            audio_blob = bucket.blob(audio_blob_name)
            audio_blob.chunk_size = GCS_UPLOAD_CHUNK_SIZE
            audio_blob.upload_from_filename(audio_path, timeout=GCS_UPLOAD_TIMEOUT)

            # Build recognition config
            use_diarization = self.enable_diarization and not disable_diarization
            config = self._build_recognition_config(language, enable_diarization=use_diarization)

            # Build request with GCS output (instead of inline)
            file_metadata = cloud_speech.BatchRecognizeFileMetadata(uri=audio_gcs_uri)

            request = cloud_speech.BatchRecognizeRequest(
                recognizer=f"projects/{self.project_id}/locations/{self.region}/recognizers/_",
                config=config,
                files=[file_metadata],
                recognition_output_config=cloud_speech.RecognitionOutputConfig(
                    gcs_output_config=cloud_speech.GcsOutputConfig(
                        uri=output_gcs_uri,
                    ),
                ),
            )

            # Start batch transcription
            operation = self.speech_client.batch_recognize(request=request)

            # Get operation name for debugging and potential resumption
            operation_name = getattr(operation.operation, "name", None) or str(operation.operation)
            logger.debug("Started BatchRecognize operation", operation_name=operation_name)

            # Extract short operation ID from full name (last segment)
            # Format: projects/xxx/locations/yyy/operations/OPERATION_ID
            operation_id = operation_name.split("/")[-1] if "/" in operation_name else operation_name

            # Save operation state for resumption if app restarts
            # Only persist if we have episode context (batch mode, not standalone file)
            persisted_operation = None
            if episode_id and podcast_slug and episode_slug:
                persisted_operation = TranscriptionOperation(
                    operation_id=operation_id,
                    operation_name=operation_name,
                    episode_id=episode_id,
                    podcast_slug=podcast_slug,
                    episode_slug=episode_slug,
                    audio_gcs_uri=audio_gcs_uri,
                    output_gcs_uri=output_gcs_uri,
                    language=language,
                    chunk_index=chunk_index,
                    chunk_start_ms=chunk_start_ms,
                    chunk_end_ms=chunk_end_ms,
                    total_chunks=total_chunks,
                    state=TranscriptionOperationState.PENDING,
                )
                self._save_operation(persisted_operation)
                logger.info(
                    "Saved operation state",
                    operation_id=operation_id,
                    podcast_slug=podcast_slug,
                    episode_slug=episode_slug,
                )

            # Wait for completion
            op_start_time = time.time()
            max_wait_seconds = OPERATION_MAX_WAIT_HOURS * 3600
            last_progress = -1

            while not operation.done():
                elapsed = time.time() - op_start_time

                # Try to get progress from operation metadata
                # The metadata is OperationMetadata which has batch_recognize_metadata
                try:
                    metadata = operation.metadata
                    if metadata:
                        # Check for batch_recognize_metadata field (OperationMetadata structure)
                        batch_meta = getattr(metadata, "batch_recognize_metadata", None)
                        if batch_meta and batch_meta.transcription_metadata:
                            for uri, file_metadata in batch_meta.transcription_metadata.items():
                                progress = file_metadata.progress_percent
                                if progress != last_progress:
                                    self.console.progress(f"Progress: {progress}%")
                                    last_progress = progress
                except Exception as e:
                    logger.debug("Could not get progress", error=str(e))

                time.sleep(OPERATION_POLL_INTERVAL_SECONDS)

                # Timeout check
                if elapsed > max_wait_seconds:
                    try:
                        operation.cancel()
                    except Exception:
                        pass
                    raise TimeoutError(
                        f"Transcription timed out after {self._format_time(elapsed)}. "
                        f"Operation ID: {operation_name}"
                    )

            response = operation.result()

            # Clean up audio file immediately (transcript stays for lifecycle cleanup)
            try:
                audio_blob.delete()
                audio_blob = None
            except Exception:
                pass

            # Extract transcript URI from response
            # Response structure with GCS output:
            # BatchRecognizeResponse.results[audio_uri] -> BatchRecognizeFileResult
            # BatchRecognizeFileResult.uri -> GCS URI where transcript was written
            if audio_gcs_uri in response.results:
                file_result = response.results[audio_gcs_uri]
            else:
                # Try to find result by iterating (in case URI format differs)
                file_result = None
                for uri, fr in response.results.items():
                    file_result = fr
                    break
                if file_result is None:
                    raise RuntimeError("No transcript found in batch response")

            # Check for errors
            if file_result.error and file_result.error.code != 0:
                raise RuntimeError(f"Transcription error: {file_result.error.message}")

            # Get the transcript URI from the response
            transcript_gcs_uri = file_result.uri
            if not transcript_gcs_uri:
                raise RuntimeError("No transcript URI in response")

            logger.debug("Transcript written to GCS", transcript_gcs_uri=transcript_gcs_uri)

            # Download and parse the transcript from GCS
            transcript = self._download_transcript_from_gcs(transcript_gcs_uri)

            # Clean up transcript file from GCS (explicit cleanup, don't wait for lifecycle)
            try:
                self._delete_gcs_file(transcript_gcs_uri)
            except Exception as e:
                logger.warning("Failed to delete transcript from GCS", error=str(e))

            # Delete the persisted operation file since we completed successfully
            if persisted_operation:
                self._delete_operation(persisted_operation.operation_id)
                logger.debug("Deleted operation state", operation_id=persisted_operation.operation_id)

            return transcript

        except TimeoutError:
            # Re-raise TimeoutError as-is so retry logic can handle it
            if audio_blob:
                try:
                    audio_blob.delete()
                except Exception:
                    pass
            raise
        except Exception as e:
            # Clean up on error
            if audio_blob:
                try:
                    audio_blob.delete()
                except Exception:
                    pass
            raise RuntimeError(f"BatchRecognize failed: {e}")

    def _download_transcript_from_gcs(self, gcs_uri: str) -> Any:
        """
        Download and parse transcript from GCS.

        The transcript is stored as a JSON-formatted BatchRecognizeResults protobuf.

        Args:
            gcs_uri: GCS URI of the transcript file (gs://bucket/path/to/file.json)

        Returns:
            Parsed BatchRecognizeResults protobuf
        """
        # Parse GCS URI
        match = re.match(r"gs://([^/]+)/(.*)", gcs_uri)
        if not match:
            raise ValueError(f"Invalid GCS URI: {gcs_uri}")

        bucket_name, blob_path = match.groups()

        # Download the transcript
        bucket = self.storage_client.bucket(bucket_name)
        blob = bucket.blob(blob_path)
        transcript_json = blob.download_as_bytes()

        # Parse as BatchRecognizeResults protobuf
        transcript = cloud_speech.BatchRecognizeResults.from_json(
            transcript_json,
            ignore_unknown_fields=True,
        )

        return transcript

    def _delete_gcs_file(self, gcs_uri: str) -> None:
        """
        Delete a file from GCS.

        Args:
            gcs_uri: GCS URI of the file to delete (gs://bucket/path/to/file)
        """
        match = re.match(r"gs://([^/]+)/(.*)", gcs_uri)
        if not match:
            logger.warning("Invalid GCS URI, cannot delete", gcs_uri=gcs_uri)
            return

        bucket_name, blob_path = match.groups()
        bucket = self.storage_client.bucket(bucket_name)
        blob = bucket.blob(blob_path)

        try:
            blob.delete()
            logger.debug("Deleted GCS file", gcs_uri=gcs_uri)
        except Exception as e:
            logger.warning("Failed to delete GCS file", gcs_uri=gcs_uri, error=str(e))

    # =========================================================================
    # Operation persistence methods for resuming after app restart
    # =========================================================================

    def _save_operation(self, operation: TranscriptionOperation) -> None:
        """
        Save operation state to local JSON file.

        This allows resuming pending operations if the app is restarted.

        Args:
            operation: TranscriptionOperation to persist
        """
        self.path_manager.pending_operations_dir().mkdir(parents=True, exist_ok=True)
        operation_file = self.path_manager.pending_operation_file(operation.operation_id)

        with open(operation_file, "w", encoding="utf-8") as f:
            f.write(operation.model_dump_json(indent=2))

        logger.debug("Saved operation state", operation_file=str(operation_file))

    def _load_operation(self, operation_id: str) -> Optional[TranscriptionOperation]:
        """
        Load operation state from local JSON file.

        Args:
            operation_id: Operation ID to load

        Returns:
            TranscriptionOperation if found, None otherwise
        """
        operation_file = self.path_manager.pending_operation_file(operation_id)

        if not operation_file.exists():
            return None

        try:
            with open(operation_file, "r", encoding="utf-8") as f:
                data = json.load(f)
            return TranscriptionOperation.model_validate(data)
        except Exception as e:
            logger.warning("Failed to load operation", operation_id=operation_id, error=str(e))
            return None

    def _delete_operation(self, operation_id: str) -> None:
        """
        Delete operation state file after successful completion.

        Args:
            operation_id: Operation ID to delete
        """
        operation_file = self.path_manager.pending_operation_file(operation_id)

        try:
            if operation_file.exists():
                operation_file.unlink()
                logger.debug("Deleted operation state", operation_file=str(operation_file))
        except Exception as e:
            logger.warning("Failed to delete operation file", operation_id=operation_id, error=str(e))

    def list_pending_operations(self) -> List[TranscriptionOperation]:
        """
        List all pending transcription operations.

        Returns:
            List of TranscriptionOperation objects in PENDING state
        """
        operations = []
        pending_dir = self.path_manager.pending_operations_dir()

        if not pending_dir.exists():
            return operations

        for op_file in pending_dir.glob("*.json"):
            try:
                with open(op_file, "r", encoding="utf-8") as f:
                    data = json.load(f)
                op = TranscriptionOperation.model_validate(data)
                if op.state == TranscriptionOperationState.PENDING:
                    operations.append(op)
            except Exception as e:
                logger.warning("Failed to load operation from file", operation_file=str(op_file), error=str(e))

        return operations

    def get_pending_operations_for_episode(self, episode_id: str) -> List[TranscriptionOperation]:
        """
        Get all pending operations for a specific episode.

        This is used to resume chunked transcriptions - if some chunks completed
        before the app was stopped, we can resume from where we left off.

        Args:
            episode_id: Episode UUID to filter by

        Returns:
            List of TranscriptionOperation objects for this episode, sorted by chunk_index
        """
        all_pending = self.list_pending_operations()
        episode_ops = [op for op in all_pending if op.episode_id == episode_id]

        # Sort by chunk_index (None values last)
        episode_ops.sort(key=lambda op: op.chunk_index if op.chunk_index is not None else 9999)

        return episode_ops

    def reset_pending_operations(self) -> List[Tuple[TranscriptionOperation, Optional[Dict]]]:
        """
        Check pending operations once: download completed ones, CANCEL still-running ones.

        This is useful when you want to:
        1. Recover any transcripts that finished while the app was stopped
        2. Cancel operations that are stuck or taking too long
        3. Start fresh without waiting for pending operations

        Unlike wait_for_pending_operations(), this does NOT poll/wait - it checks once
        and immediately returns. Operations still running on GCP are CANCELLED via API.

        Returns:
            List of (operation, transcript_data) tuples.
            - transcript_data is the transcript dict if operation was completed
            - transcript_data is None if operation was cancelled or failed
        """
        results = []
        pending_ops = self.list_pending_operations()

        if not pending_ops:
            return results

        logger.info("Resetting pending operations", operation_count=len(pending_ops))

        for op in pending_ops:
            try:
                # Check operation status (single check, no polling)
                updated_op = self.check_pending_operation(op)

                if updated_op.state == TranscriptionOperationState.COMPLETED:
                    # Download the transcript
                    transcript_data = self.download_completed_operation(updated_op)
                    results.append((updated_op, transcript_data))
                    self.console.success(f"{op.podcast_slug}/{op.episode_slug} - completed, downloaded")
                elif updated_op.state == TranscriptionOperationState.FAILED:
                    # Already failed - just report it
                    results.append((updated_op, None))
                    self._delete_operation(op.operation_id)
                    self.console.error(f"{op.podcast_slug}/{op.episode_slug} - failed: {updated_op.error}")
                else:
                    # Still running - cancel it on GCP and delete local tracking
                    cancelled = self._cancel_operation(op)
                    self._delete_operation(op.operation_id)
                    results.append((op, None))
                    if cancelled:
                        self.console.info(f"⏹ {op.podcast_slug}/{op.episode_slug} - cancelled")
                    else:
                        self.console.info(
                            f"⏹ {op.podcast_slug}/{op.episode_slug} - removed (cancel failed, op may have expired)"
                        )

            except Exception as e:
                logger.error("Error checking operation", operation_id=op.operation_id, error=str(e))
                # Try to cancel anyway, then delete tracking file
                try:
                    self._cancel_operation(op)
                except Exception:
                    pass
                self._delete_operation(op.operation_id)
                results.append((op, None))
                self.console.error(f"{op.podcast_slug}/{op.episode_slug} - error: {e}")

        return results

    def _cancel_operation(self, operation: TranscriptionOperation) -> bool:
        """
        Cancel a running GCP operation.

        Args:
            operation: TranscriptionOperation to cancel

        Returns:
            True if cancelled successfully, False if operation was already done/failed/expired
        """
        try:
            operations_client = self.speech_client._transport.operations_client
            operations_client.cancel_operation(name=operation.operation_name)
            logger.info("Cancelled GCP operation", operation_id=operation.operation_id)
            return True
        except Exception as e:
            # Common reasons for cancel to fail:
            # - Operation already completed (but output may have been cleaned up)
            # - Operation already failed/expired on GCP side
            # - Operation was already cancelled
            # - Operation doesn't exist (wrong ID or already deleted)
            logger.warning(
                f"Could not cancel operation {operation.operation_id}: {e}. "
                "Operation may have already completed, failed, or expired on GCP."
            )
            return False

    def check_pending_operation(self, operation: TranscriptionOperation) -> TranscriptionOperation:
        """
        Check the status of a pending operation and download transcript if complete.

        This method is used to resume operations after app restart. It:
        1. Queries GCP for operation status
        2. If complete, downloads the transcript from GCS
        3. Updates the operation state
        4. Cleans up GCS files

        Args:
            operation: TranscriptionOperation to check

        Returns:
            Updated TranscriptionOperation with new state

        Raises:
            RuntimeError: If operation check fails
        """
        try:
            # Get the operations client
            # Note: We need to use the transport from the speech client
            operations_client = self.speech_client._transport.operations_client

            # Get operation status
            grpc_operation = operations_client.get_operation(name=operation.operation_name)

            # Note: The get_operation API returns OperationInfo metadata which contains
            # createTime, updateTime, resource, method, batchRecognizeRequest - but NOT progress.
            # Progress information is only available during the initial batch_recognize call's
            # operation polling, not when resuming via get_operation.
            if grpc_operation.metadata:
                try:
                    from google.protobuf import json_format

                    metadata_dict = json_format.MessageToDict(grpc_operation.metadata)

                    # Extract timing info for display
                    create_time = metadata_dict.get("createTime")
                    update_time = metadata_dict.get("updateTime")
                    if create_time and update_time:
                        # Just log that we have timing info (progress not available via this API)
                        logger.debug("Operation metadata", create_time=create_time, update_time=update_time)
                except Exception as e:
                    logger.debug("Could not extract metadata", error=str(e))

            if not grpc_operation.done:
                # Still running
                logger.debug("Operation still in progress", operation_id=operation.operation_id)
                return operation

            # Operation completed - check for errors
            if grpc_operation.error.code != 0:
                operation.state = TranscriptionOperationState.FAILED
                operation.error = grpc_operation.error.message
                operation.completed_at = datetime.now(timezone.utc)
                self._save_operation(operation)
                return operation

            # Parse the response from the Any type
            # Note: Using Unpack() directly can fail with "Unknown field: DESCRIPTOR" error
            # due to protobuf version mismatches. We use multiple strategies.
            response = None
            parse_error = None

            # Strategy 1: Parse the Any's value directly as BatchRecognizeResponse
            try:
                response = cloud_speech.BatchRecognizeResponse.deserialize(grpc_operation.response.value)
            except Exception as e:
                parse_error = e
                logger.debug("Strategy 1 (deserialize) failed", error=str(e))

            # Strategy 2: Try from_json if the response has a type_url we can use
            if response is None:
                try:
                    from google.protobuf import json_format

                    # First convert Any to dict, extract the value
                    any_dict = json_format.MessageToDict(grpc_operation.response)
                    # The 'value' in Any is base64 encoded, but MessageToDict might give us the parsed content
                    if "value" in any_dict or any_dict:
                        # Try parsing as BatchRecognizeResponse
                        response = cloud_speech.BatchRecognizeResponse.from_json(
                            json_format.MessageToJson(grpc_operation.response),
                            ignore_unknown_fields=True,
                        )
                except Exception as e:
                    logger.debug("Strategy 2 (json) failed", error=str(e))

            # Strategy 3: Direct Unpack (may work with some protobuf versions)
            if response is None:
                try:
                    response = cloud_speech.BatchRecognizeResponse()
                    grpc_operation.response.Unpack(response)
                except Exception as e:
                    logger.debug("Strategy 3 (Unpack) failed", error=str(e))

            if response is None:
                raise RuntimeError(f"Failed to parse operation response after all strategies: {parse_error}")

            # Find the file result
            file_result = None
            for _, fr in response.results.items():  # pylint: disable=no-member
                file_result = fr
                break

            if file_result is None:
                operation.state = TranscriptionOperationState.FAILED
                operation.error = "No file result in response"
                operation.completed_at = datetime.now(timezone.utc)
                self._save_operation(operation)
                return operation

            # Check for transcription errors
            if file_result.error and file_result.error.code != 0:
                operation.state = TranscriptionOperationState.FAILED
                operation.error = file_result.error.message
                operation.completed_at = datetime.now(timezone.utc)
                self._save_operation(operation)
                return operation

            # Get transcript URI
            transcript_gcs_uri = file_result.uri
            if not transcript_gcs_uri:
                operation.state = TranscriptionOperationState.FAILED
                operation.error = "No transcript URI in response"
                operation.completed_at = datetime.now(timezone.utc)
                self._save_operation(operation)
                return operation

            operation.transcript_gcs_uri = transcript_gcs_uri
            operation.state = TranscriptionOperationState.COMPLETED
            operation.completed_at = datetime.now(timezone.utc)
            self._save_operation(operation)

            logger.info(
                "Operation completed",
                operation_id=operation.operation_id,
                transcript_gcs_uri=transcript_gcs_uri,
            )
            return operation

        except Exception as e:
            logger.error("Failed to check operation", operation_id=operation.operation_id, error=str(e))
            raise RuntimeError(f"Failed to check operation: {e}")

    def download_completed_operation(self, operation: TranscriptionOperation) -> Transcript:
        """
        Download transcript for a completed operation and clean up GCS.

        Args:
            operation: TranscriptionOperation in COMPLETED state

        Returns:
            Formatted Transcript object

        Raises:
            ValueError: If operation is not in COMPLETED state
            RuntimeError: If download fails
        """
        if operation.state != TranscriptionOperationState.COMPLETED:
            raise ValueError(f"Operation must be in COMPLETED state, got {operation.state}")

        if not operation.transcript_gcs_uri:
            raise ValueError("Operation has no transcript URI")

        try:
            # Download and parse the transcript
            transcript_result = self._download_transcript_from_gcs(operation.transcript_gcs_uri)

            # Format the transcript
            transcript = self._format_transcript(transcript_result, 0, "", operation.language)

            # Adjust timestamps if this is a chunk
            if operation.chunk_start_ms is not None:
                offset_seconds = operation.chunk_start_ms / 1000
                transcript = transcript.adjust_timestamps(offset_seconds)

            # Clean up GCS files
            try:
                self._delete_gcs_file(operation.transcript_gcs_uri)
            except Exception as e:
                logger.warning("Failed to delete transcript from GCS", error=str(e))

            # Try to delete audio file if it still exists
            try:
                self._delete_gcs_file(operation.audio_gcs_uri)
            except Exception:
                pass  # Audio may already be deleted

            # Update operation state
            operation.state = TranscriptionOperationState.DOWNLOADED
            self._save_operation(operation)

            # Delete the operation file since we're done
            self._delete_operation(operation.operation_id)

            return transcript

        except Exception as e:
            logger.error(
                "Failed to download transcript for operation", operation_id=operation.operation_id, error=str(e)
            )
            raise RuntimeError(f"Failed to download transcript: {e}")

    def resume_pending_operations(self) -> List[Tuple[TranscriptionOperation, Optional[Transcript]]]:
        """
        Resume all pending operations from previous app run.

        This method:
        1. Lists all pending operations from local storage
        2. Checks each operation's status with GCP
        3. Downloads transcripts for completed operations
        4. Returns results for integration into the pipeline

        Returns:
            List of (operation, transcript) tuples.
            transcript is None if operation is still pending or failed.
        """
        results = []
        pending_ops = self.list_pending_operations()

        if not pending_ops:
            return results

        logger.info("Found pending operations to resume", operation_count=len(pending_ops))

        for op in pending_ops:
            try:
                # Check operation status
                updated_op = self.check_pending_operation(op)

                if updated_op.state == TranscriptionOperationState.COMPLETED:
                    # Download the transcript
                    transcript_data = self.download_completed_operation(updated_op)
                    results.append((updated_op, transcript_data))
                elif updated_op.state == TranscriptionOperationState.FAILED:
                    logger.warning("Operation failed", operation_id=op.operation_id, error=updated_op.error)
                    results.append((updated_op, None))
                else:
                    # Still pending
                    results.append((updated_op, None))

            except Exception as e:
                logger.error("Error resuming operation", operation_id=op.operation_id, error=str(e))
                op.state = TranscriptionOperationState.FAILED
                op.error = str(e)
                self._save_operation(op)
                results.append((op, None))

        return results

    def wait_for_pending_operations(
        self,
        timeout_minutes: int = 60,
        poll_interval_seconds: int = OPERATION_POLL_INTERVAL_SECONDS,
    ) -> List[Tuple[TranscriptionOperation, Optional[Dict]]]:
        """
        Wait for all pending operations to complete, with timeout.

        This method polls all pending operations until they complete, fail, or timeout.
        Use this when you want to wait for in-progress transcriptions before starting
        new ones (e.g., to avoid re-transcribing the same episodes).

        Args:
            timeout_minutes: Maximum time to wait for all operations (default: 60 minutes)
            poll_interval_seconds: How often to check status (default: 30 seconds)

        Returns:
            List of (operation, transcript_data) tuples.
            transcript_data is None if operation failed or timed out.
        """
        results = []
        pending_ops = self.list_pending_operations()

        if not pending_ops:
            return results

        logger.info(
            "Waiting for pending operations",
            operation_count=len(pending_ops),
            timeout_minutes=timeout_minutes,
        )
        start_time = time.time()
        timeout_seconds = timeout_minutes * 60

        # Track which operations are still pending
        remaining_ops = {op.operation_id: op for op in pending_ops}

        while remaining_ops and (time.time() - start_time) < timeout_seconds:
            # Check each remaining operation
            for op_id, op in list(remaining_ops.items()):
                try:
                    updated_op = self.check_pending_operation(op)

                    if updated_op.state == TranscriptionOperationState.COMPLETED:
                        # Download the transcript
                        transcript_data = self.download_completed_operation(updated_op)
                        results.append((updated_op, transcript_data))
                        del remaining_ops[op_id]
                        self.console.success(f"{op.podcast_slug}/{op.episode_slug} completed")
                    elif updated_op.state == TranscriptionOperationState.FAILED:
                        results.append((updated_op, None))
                        del remaining_ops[op_id]
                        self.console.error(f"{op.podcast_slug}/{op.episode_slug} failed: {updated_op.error}")
                    else:
                        # Update reference for next iteration
                        remaining_ops[op_id] = updated_op

                except Exception as e:
                    logger.error("Error checking operation", operation_id=op_id, error=str(e))
                    op.state = TranscriptionOperationState.FAILED
                    op.error = str(e)
                    self._save_operation(op)
                    results.append((op, None))
                    del remaining_ops[op_id]

            # If still have pending operations, wait before next poll
            if remaining_ops:
                elapsed = time.time() - start_time
                remaining_time = timeout_seconds - elapsed
                if remaining_time > poll_interval_seconds:
                    elapsed_mins = int(elapsed / 60)
                    if not self.console.quiet:
                        import sys

                        sys.stdout.write(f"\r⏳ Waiting... ({len(remaining_ops)} pending, {elapsed_mins}m elapsed)")
                        sys.stdout.flush()
                    time.sleep(poll_interval_seconds)

        # Handle timed-out operations
        for op_id, op in remaining_ops.items():
            logger.warning("Operation timed out", operation_id=op_id, timeout_minutes=timeout_minutes)
            op.error = f"Timed out waiting after {timeout_minutes} minutes"
            # Don't mark as failed - it might still complete later
            results.append((op, None))

        return results

    def _build_recognition_config(
        self, language: str, enable_diarization: Optional[bool] = None
    ) -> cloud_speech.RecognitionConfig:
        """Build V2 recognition configuration with Chirp 3 and optional diarization."""
        # Build features configuration
        # Note: Chirp 3 does not support enable_word_confidence
        features_kwargs = {
            "enable_automatic_punctuation": True,
            "enable_word_time_offsets": True,
        }

        # Use instance setting if not explicitly overridden
        use_diarization = enable_diarization if enable_diarization is not None else self.enable_diarization

        # Add diarization if enabled
        if use_diarization:
            diarization_config = cloud_speech.SpeakerDiarizationConfig(
                min_speaker_count=self.min_speakers if self.min_speakers else 1,
                max_speaker_count=self.max_speakers if self.max_speakers else 6,
            )
            features_kwargs["diarization_config"] = diarization_config

        features = cloud_speech.RecognitionFeatures(**features_kwargs)

        # Build main config
        config = cloud_speech.RecognitionConfig(
            auto_decoding_config=cloud_speech.AutoDetectDecodingConfig(),
            language_codes=[language],
            model="chirp_3",
            features=features,
        )

        return config

    def _format_time(self, seconds: float) -> str:
        """
        Format seconds into human-readable time string.

        Examples:
            45 seconds → "45s"
            125 seconds → "2m 5s"
            3725 seconds → "1h 2m 5s"
        """
        if seconds < 60:
            return f"{int(seconds)}s"
        elif seconds < 3600:
            mins = int(seconds // 60)
            secs = int(seconds % 60)
            return f"{mins}m {secs}s"
        else:
            hours = int(seconds // 3600)
            mins = int((seconds % 3600) // 60)
            secs = int(seconds % 60)
            return f"{hours}h {mins}m {secs}s"

    def _format_timestamp_mmss(self, ms: int) -> str:
        """
        Format milliseconds into MM:SS or H:MM:SS timestamp.

        Examples:
            45000 ms → "0:45"
            125000 ms → "2:05"
            3725000 ms → "1:02:05"
        """
        total_seconds = ms // 1000
        if total_seconds < 3600:
            mins = total_seconds // 60
            secs = total_seconds % 60
            return f"{mins}:{secs:02d}"
        else:
            hours = total_seconds // 3600
            mins = (total_seconds % 3600) // 60
            secs = total_seconds % 60
            return f"{hours}:{mins:02d}:{secs:02d}"

    def _format_transcript(
        self,
        transcript: Any,
        processing_time: float,
        audio_path: str,
        requested_language: Optional[str] = None,
    ) -> Transcript:
        """
        Format Google Cloud V2 response to match Whisper transcript structure.

        Output format:
        {
            "audio_file": str,
            "language": str,
            "text": str,  # Full transcript text
            "segments": [
                {
                    "id": int,
                    "start": float,
                    "end": float,
                    "text": str,
                    "speaker": str,  # "SPEAKER_01", "SPEAKER_02", etc.
                    "confidence": float,  # Alternative-level confidence (0.0-1.0)
                    "language_code": str,  # Per-segment detected language
                    "result_end_offset": float,  # End offset from Google result
                    "words": [
                        {
                            "word": str,
                            "start": float,
                            "end": float,
                            "probability": float,
                            "speaker": str
                        }
                    ]
                }
            ],
            "processing_time": float,
            "model_used": str,
            "timestamp": float,
            "diarization_enabled": bool,
            "speakers_detected": int,
            "google_metadata": {  # NEW: Metadata from Google API
                "request_id": str,  # UUID for debugging/audit
                "total_billed_duration_seconds": float  # For cost tracking
            }
        }
        """
        segments = []
        full_text_parts = []
        speakers_detected = set()

        segment_id = 0
        detected_language = "en-US"

        # Extract metadata from Google response
        google_metadata = {}
        if hasattr(transcript, "metadata") and transcript.metadata:
            metadata = transcript.metadata
            if hasattr(metadata, "request_id") and metadata.request_id:
                google_metadata["request_id"] = metadata.request_id
            if hasattr(metadata, "total_billed_duration") and metadata.total_billed_duration:
                google_metadata["total_billed_duration_seconds"] = self._get_seconds(metadata.total_billed_duration)

        for result in transcript.results:
            if not result.alternatives:
                continue

            alternative = result.alternatives[0]

            # Get detected language if available (per-result)
            result_language = None
            if hasattr(result, "language_code") and result.language_code:
                result_language = result.language_code
                detected_language = result.language_code  # Also update global for backward compat

            # Get result end offset for alignment verification
            result_end_offset = None
            if hasattr(result, "result_end_offset") and result.result_end_offset:
                result_end_offset = self._get_seconds(result.result_end_offset)

            # Get alternative-level confidence (overall confidence for this result)
            alternative_confidence = None
            if hasattr(alternative, "confidence"):
                alternative_confidence = alternative.confidence

            # Check if we have word-level information
            if hasattr(alternative, "words") and alternative.words:
                if self.enable_diarization:
                    # Group words by speaker to create segments
                    current_speaker = None
                    current_words = []
                    current_start = 0.0
                    current_end = 0.0

                    for word_info in alternative.words:
                        # V2 API uses different time format
                        word_start = self._get_seconds(word_info.start_offset)
                        word_end = self._get_seconds(word_info.end_offset)

                        # Get speaker tag (V2 uses speaker_label)
                        speaker_label = getattr(word_info, "speaker_label", None)
                        if speaker_label:
                            speaker_id = f"SPEAKER_{speaker_label}"
                        else:
                            speaker_id = "SPEAKER_01"
                        speakers_detected.add(speaker_id)

                        # Start new segment if speaker changed
                        if current_speaker != speaker_id:
                            # Save previous segment
                            if current_words:
                                segment_text = " ".join(w["word"] for w in current_words)
                                segment_data = {
                                    "id": segment_id,
                                    "start": current_start,
                                    "end": current_end,
                                    "text": segment_text,
                                    "speaker": current_speaker,
                                    "words": current_words,
                                }
                                # Add enhanced metadata if available
                                if alternative_confidence is not None:
                                    segment_data["confidence"] = alternative_confidence
                                if result_language:
                                    segment_data["language_code"] = result_language
                                if result_end_offset is not None:
                                    segment_data["result_end_offset"] = result_end_offset
                                segments.append(segment_data)
                                full_text_parts.append(segment_text)
                                segment_id += 1

                            # Start new segment
                            current_speaker = speaker_id
                            current_words = []
                            current_start = word_start

                        # Add word to current segment
                        confidence = getattr(word_info, "confidence", 0.0)
                        current_words.append(
                            {
                                "word": word_info.word,
                                "start": word_start,
                                "end": word_end,
                                "probability": confidence,
                                "speaker": speaker_id,
                            }
                        )
                        current_end = word_end

                    # Save final segment
                    if current_words:
                        segment_text = " ".join(w["word"] for w in current_words)
                        segment_data = {
                            "id": segment_id,
                            "start": current_start,
                            "end": current_end,
                            "text": segment_text,
                            "speaker": current_speaker,
                            "words": current_words,
                        }
                        # Add enhanced metadata if available
                        if alternative_confidence is not None:
                            segment_data["confidence"] = alternative_confidence
                        if result_language:
                            segment_data["language_code"] = result_language
                        if result_end_offset is not None:
                            segment_data["result_end_offset"] = result_end_offset
                        segments.append(segment_data)
                        full_text_parts.append(segment_text)
                        segment_id += 1
                else:
                    # No diarization - create segment from words
                    words = []
                    for word_info in alternative.words:
                        word_start = self._get_seconds(word_info.start_offset)
                        word_end = self._get_seconds(word_info.end_offset)
                        confidence = getattr(word_info, "confidence", 0.0)
                        words.append(
                            {
                                "word": word_info.word,
                                "start": word_start,
                                "end": word_end,
                                "probability": confidence,
                                "speaker": None,
                            }
                        )

                    if words:
                        segment_text = " ".join(w["word"] for w in words)
                        segment_data = {
                            "id": segment_id,
                            "start": words[0]["start"],
                            "end": words[-1]["end"],
                            "text": segment_text,
                            "speaker": None,
                            "words": words,
                        }
                        # Add enhanced metadata if available
                        if alternative_confidence is not None:
                            segment_data["confidence"] = alternative_confidence
                        if result_language:
                            segment_data["language_code"] = result_language
                        if result_end_offset is not None:
                            segment_data["result_end_offset"] = result_end_offset
                        segments.append(segment_data)
                        full_text_parts.append(segment_text)
                        segment_id += 1

            elif hasattr(alternative, "transcript") and alternative.transcript:
                # Fallback: use transcript text without word-level info
                segment_text = alternative.transcript.strip()
                if segment_text:
                    segment_data = {
                        "id": segment_id,
                        "start": 0.0,
                        "end": 0.0,
                        "text": segment_text,
                        "speaker": None,
                        "words": [],
                    }
                    # Add enhanced metadata if available
                    if alternative_confidence is not None:
                        segment_data["confidence"] = alternative_confidence
                    if result_language:
                        segment_data["language_code"] = result_language
                    if result_end_offset is not None:
                        segment_data["result_end_offset"] = result_end_offset
                    segments.append(segment_data)
                    full_text_parts.append(segment_text)
                    segment_id += 1

        full_text = " ".join(full_text_parts)

        # Convert dict segments to Segment objects
        transcript_segments = []
        for seg in segments:
            words = [
                Word(
                    word=w["word"],
                    start=w.get("start"),
                    end=w.get("end"),
                    probability=w.get("probability"),
                    speaker=w.get("speaker"),
                )
                for w in seg.get("words", [])
            ]
            transcript_segments.append(
                Segment(
                    id=seg["id"],
                    start=seg.get("start", 0.0),
                    end=seg.get("end", 0.0),
                    text=seg.get("text", ""),
                    speaker=seg.get("speaker"),
                    words=words,
                    confidence=seg.get("confidence"),
                )
            )

        # Use explicitly requested language if provided, otherwise use auto-detected
        if requested_language:
            language = requested_language
        else:
            language = detected_language

        return Transcript(
            audio_file=audio_path,
            language=language,
            text=full_text,
            segments=transcript_segments,
            processing_time=processing_time,
            model_used="google-cloud-speech-v2-chirp_3",
            timestamp=time.time(),
            diarization_enabled=self.enable_diarization,
            speakers_detected=len(speakers_detected) if self.enable_diarization else None,
            provider_metadata=google_metadata if google_metadata else None,
        )

    def _get_seconds(self, duration) -> float:
        """Convert protobuf Duration to seconds."""
        if duration is None:
            return 0.0
        if hasattr(duration, "total_seconds"):
            return duration.total_seconds()
        # Handle protobuf Duration type
        if hasattr(duration, "seconds") and hasattr(duration, "nanos"):
            return duration.seconds + duration.nanos / 1e9
        return 0.0
