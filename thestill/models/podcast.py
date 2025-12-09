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

import uuid
from datetime import datetime
from enum import Enum
from typing import List, Optional

from pydantic import BaseModel, Field, HttpUrl, computed_field, model_validator


class EpisodeState(str, Enum):
    """
    Episode processing state enum.

    States represent the progression through the pipeline:
    - DISCOVERED: Episode found in feed (has audio_url)
    - DOWNLOADED: Audio file downloaded (has audio_path)
    - DOWNSAMPLED: Audio converted to 16kHz WAV (has downsampled_audio_path)
    - TRANSCRIBED: Transcript generated (has raw_transcript_path)
    - CLEANED: Final cleaned transcript created (has clean_transcript_path)
    - SUMMARIZED: Summary generated (has summary_path)
    """

    DISCOVERED = "discovered"
    DOWNLOADED = "downloaded"
    DOWNSAMPLED = "downsampled"
    TRANSCRIBED = "transcribed"
    CLEANED = "cleaned"
    SUMMARIZED = "summarized"


class Episode(BaseModel):
    # Internal identifiers (auto-generated)
    id: str = Field(default_factory=lambda: str(uuid.uuid4()))  # Internal UUID
    created_at: datetime = Field(default_factory=datetime.utcnow)  # When episode was first added to database

    # External identifiers
    external_id: str  # External ID from RSS feed (publisher's GUID)

    # Episode metadata
    title: str
    slug: str = ""  # URL/filesystem-safe identifier (auto-generated from title if empty)
    description: str
    pub_date: Optional[datetime] = None
    audio_url: HttpUrl
    duration: Optional[str] = None

    # File paths (filenames only, relative to storage directories)
    audio_path: Optional[str] = None  # Filename of the original downloaded audio file (in original_audio/)
    downsampled_audio_path: Optional[str] = None  # Filename of the downsampled WAV file (in downsampled_audio/)
    raw_transcript_path: Optional[str] = None  # Filename of the raw transcript JSON (Whisper output)
    clean_transcript_path: Optional[str] = None  # Filename of the cleaned transcript MD (corrected, formatted)
    summary_path: Optional[str] = None  # Filename of the summary (future use)

    @model_validator(mode="after")
    def ensure_slug(self) -> "Episode":
        """Auto-generate slug from title if not provided."""
        if not self.slug and self.title:
            from thestill.utils.slug import generate_slug

            self.slug = generate_slug(self.title)
        return self

    @computed_field  # type: ignore[misc]
    @property
    def state(self) -> EpisodeState:
        """
        Compute current episode state from file paths.

        The state is determined by checking which paths are set, from most
        progressed to least progressed. This ensures we always return the
        furthest state reached.

        This is a computed property that dynamically reflects the current
        processing state based on which file paths are set.

        Returns:
            EpisodeState: Current processing state
        """
        if self.summary_path:
            return EpisodeState.SUMMARIZED
        if self.clean_transcript_path:
            return EpisodeState.CLEANED
        if self.raw_transcript_path:
            return EpisodeState.TRANSCRIBED
        if self.downsampled_audio_path:
            return EpisodeState.DOWNSAMPLED
        if self.audio_path:
            return EpisodeState.DOWNLOADED
        return EpisodeState.DISCOVERED


class Podcast(BaseModel):
    # Internal identifiers (auto-generated)
    id: str = Field(default_factory=lambda: str(uuid.uuid4()))  # Internal UUID
    created_at: datetime = Field(default_factory=datetime.utcnow)  # When podcast was first added to database

    # External identifiers
    rss_url: HttpUrl  # RSS feed URL (external identifier)

    # Podcast metadata
    title: str
    slug: str = ""  # URL/filesystem-safe identifier (auto-generated from title if empty)
    description: str

    # Processing status
    last_processed: Optional[datetime] = None

    # Episodes
    episodes: List[Episode] = []

    @model_validator(mode="after")
    def ensure_slug(self) -> "Podcast":
        """Auto-generate slug from title if not provided."""
        if not self.slug and self.title:
            from thestill.utils.slug import generate_slug

            self.slug = generate_slug(self.title)
        return self


class Word(BaseModel):
    word: str
    start: float
    end: float
    probability: float
    speaker: Optional[str] = None


class Segment(BaseModel):
    id: int
    start: float
    end: float
    text: str
    words: List[Word] = []
    speaker: Optional[str] = None


class TranscriptMetadata(BaseModel):
    audio_file: str
    language: str
    processing_time: float
    model_used: str
    timestamp: float
    diarization_enabled: bool = False
    speakers_detected: Optional[int] = None


class Quote(BaseModel):
    text: str
    speaker: Optional[str] = None
    timestamp: Optional[str] = None
    significance: str


class ProcessedContent(BaseModel):
    episode_external_id: str  # External episode ID (from RSS feed)
    cleaned_transcript: str
    summary: str
    quotes: List[Quote]
    processing_time: float
    created_at: datetime


class CleanedTranscript(BaseModel):
    episode_external_id: str  # External episode ID (from RSS feed)
    episode_title: str
    podcast_title: str
    corrections: List[dict] = []
    speaker_mapping: dict = {}
    cleaned_markdown: str
    processing_time: float
    created_at: datetime


class TranscriptionOperationState(str, Enum):
    """
    State of a Google Cloud transcription operation.

    States represent the lifecycle of a BatchRecognize operation:
    - PENDING: Operation submitted, waiting for completion
    - COMPLETED: Operation finished successfully, transcript available in GCS
    - DOWNLOADED: Transcript downloaded from GCS to local storage
    - FAILED: Operation failed (see error field for details)
    - CANCELLED: Operation was cancelled
    """

    PENDING = "pending"
    COMPLETED = "completed"
    DOWNLOADED = "downloaded"
    FAILED = "failed"
    CANCELLED = "cancelled"


class TranscriptionOperation(BaseModel):
    """
    Tracks the state of a Google Cloud BatchRecognize operation.

    This model is persisted to disk so that pending operations can be
    resumed if the application is restarted. It stores all information
    needed to:
    1. Check if the operation has completed
    2. Download the transcript from GCS when ready
    3. Clean up GCS resources after download

    Stored in: data/pending_operations/{operation_id}.json
    """

    # Operation identifiers
    operation_id: str  # Short ID for local tracking (extracted from operation_name)
    operation_name: str  # Full GCP operation name (projects/.../operations/...)

    # Context for resuming
    episode_id: str  # Episode UUID for updating database after completion
    podcast_slug: str  # For organizing output files
    episode_slug: str  # For naming output files
    audio_gcs_uri: str  # GCS URI of the uploaded audio file
    output_gcs_uri: str  # GCS URI prefix where transcript will be written
    language: str  # Language code used for transcription

    # Chunk info (for multi-chunk transcriptions)
    chunk_index: Optional[int] = None  # None for single-file transcription
    chunk_start_ms: Optional[int] = None  # Start time offset for this chunk
    chunk_end_ms: Optional[int] = None  # End time offset for this chunk
    total_chunks: Optional[int] = None  # Total number of chunks

    # State tracking
    state: TranscriptionOperationState = TranscriptionOperationState.PENDING
    created_at: datetime = Field(default_factory=datetime.utcnow)
    completed_at: Optional[datetime] = None
    error: Optional[str] = None

    # Result info (populated after completion)
    transcript_gcs_uri: Optional[str] = None  # Actual transcript file URI (from response)
    local_transcript_path: Optional[str] = None  # Path where transcript was downloaded


class TranscriptCleaningMetrics(BaseModel):
    """
    Performance metrics for transcript cleaning pipeline.

    Tracks timing and resource usage for each phase to enable:
    - Performance optimization analysis
    - Cost estimation (LLM API usage)
    - Bottleneck identification
    - Trend analysis across episodes
    """

    episode_external_id: str  # External episode ID (from RSS feed)
    episode_title: str
    podcast_title: str

    # Overall metrics
    total_duration_seconds: float
    total_transcript_chars: int
    total_chunks_processed: int

    # Run status: "success", "degraded", or "failed"
    # - success: All chunks processed, corrections applied as expected
    # - degraded: Some chunks failed or low correction success rate
    # - failed: Critical failure (should not normally be saved)
    run_status: str = "success"

    # Phase 0: Formatting
    phase0_format_duration_seconds: float

    # Phase 1: Corrections analysis
    phase1_analysis_duration_seconds: float
    phase1_chunks_processed: int
    phase1_chunks_failed: int = 0  # Chunks that failed to process (JSON errors, etc.)
    phase1_corrections_found: int
    phase1_llm_calls: int

    # Phase 1.5: Apply corrections
    phase1_5_apply_duration_seconds: float
    phase1_5_corrections_applied: int

    # Phase 2: Speaker identification
    phase2_speaker_duration_seconds: float
    phase2_speakers_identified: int
    phase2_llm_calls: int

    # Phase 3: Generate cleaned transcript
    phase3_generation_duration_seconds: float
    phase3_chunks_processed: int
    phase3_llm_calls: int

    # LLM provider info
    llm_provider: str
    llm_model: str
    chunk_size: int

    # Metadata
    timestamp: datetime

    @property
    def total_llm_calls(self) -> int:
        """Total number of LLM API calls made across all phases"""
        return self.phase1_llm_calls + self.phase2_llm_calls + self.phase3_llm_calls

    @property
    def phase_breakdown_percent(self) -> dict:
        """Percentage of time spent in each phase"""
        if self.total_duration_seconds == 0:
            return {}

        return {
            "phase0_format": round(100 * self.phase0_format_duration_seconds / self.total_duration_seconds, 1),
            "phase1_analysis": round(100 * self.phase1_analysis_duration_seconds / self.total_duration_seconds, 1),
            "phase1_5_apply": round(100 * self.phase1_5_apply_duration_seconds / self.total_duration_seconds, 1),
            "phase2_speaker": round(100 * self.phase2_speaker_duration_seconds / self.total_duration_seconds, 1),
            "phase3_generation": round(100 * self.phase3_generation_duration_seconds / self.total_duration_seconds, 1),
        }

    @property
    def corrections_success_rate(self) -> float:
        """
        Percentage of corrections that were successfully applied.

        Returns 1.0 (100%) if no corrections were found (nothing to apply).
        """
        if self.phase1_corrections_found == 0:
            return 1.0
        return self.phase1_5_corrections_applied / self.phase1_corrections_found

    @property
    def corrections_skipped(self) -> int:
        """Number of corrections that failed to apply (found - applied)."""
        return max(0, self.phase1_corrections_found - self.phase1_5_corrections_applied)

    @property
    def phase1_failure_rate(self) -> float:
        """Percentage of chunks that failed in Phase 1."""
        total = self.phase1_chunks_processed + self.phase1_chunks_failed
        if total == 0:
            return 0.0
        return self.phase1_chunks_failed / total

    @property
    def efficiency_metrics(self) -> dict:
        """Derived efficiency metrics"""
        return {
            "chars_per_second": (
                round(self.total_transcript_chars / self.total_duration_seconds, 1)
                if self.total_duration_seconds > 0
                else 0
            ),
            "seconds_per_llm_call": (
                round(self.total_duration_seconds / self.total_llm_calls, 2) if self.total_llm_calls > 0 else 0
            ),
            "corrections_per_1000_chars": (
                round(1000 * self.phase1_corrections_found / self.total_transcript_chars, 1)
                if self.total_transcript_chars > 0
                else 0
            ),
            "corrections_success_rate": round(self.corrections_success_rate * 100, 1),
            "phase1_failure_rate": round(self.phase1_failure_rate * 100, 1),
        }
