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

from pydantic import BaseModel, Field, HttpUrl, computed_field


class EpisodeState(str, Enum):
    """
    Episode processing state enum.

    States represent the progression through the pipeline:
    - DISCOVERED: Episode found in feed (has audio_url)
    - DOWNLOADED: Audio file downloaded (has audio_path)
    - DOWNSAMPLED: Audio converted to 16kHz WAV (has downsampled_audio_path)
    - TRANSCRIBED: Transcript generated (has raw_transcript_path)
    - CLEANED: Final cleaned transcript created (has clean_transcript_path)
    """

    DISCOVERED = "discovered"
    DOWNLOADED = "downloaded"
    DOWNSAMPLED = "downsampled"
    TRANSCRIBED = "transcribed"
    CLEANED = "cleaned"


class Episode(BaseModel):
    # Internal identifiers (auto-generated)
    id: str = Field(default_factory=lambda: str(uuid.uuid4()))  # Internal UUID
    created_at: datetime = Field(default_factory=datetime.utcnow)  # When episode was first added to database

    # External identifiers
    external_id: str  # External ID from RSS feed (publisher's GUID)

    # Episode metadata
    title: str
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
    description: str

    # Processing status
    last_processed: Optional[datetime] = None

    # Episodes
    episodes: List[Episode] = []


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
    ad_segments: List[dict] = []
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

    # Phase 0: Formatting
    phase0_format_duration_seconds: float

    # Phase 1: Corrections analysis
    phase1_analysis_duration_seconds: float
    phase1_chunks_processed: int
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
        }
