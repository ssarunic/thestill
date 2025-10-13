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

from datetime import datetime
from enum import Enum
from typing import List, Optional

from pydantic import BaseModel, HttpUrl


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
    title: str
    description: str
    pub_date: Optional[datetime] = None
    audio_url: HttpUrl
    duration: Optional[str] = None
    guid: str
    processed: bool = False
    audio_path: Optional[str] = None  # Filename of the original downloaded audio file (in original_audio/)
    downsampled_audio_path: Optional[str] = None  # Filename of the downsampled WAV file (in downsampled_audio/)
    raw_transcript_path: Optional[str] = None  # Filename of the raw transcript JSON (Whisper output)
    clean_transcript_path: Optional[str] = None  # Filename of the cleaned transcript MD (corrected, formatted)
    summary_path: Optional[str] = None  # Filename of the summary (future use)

    @property
    def state(self) -> EpisodeState:
        """
        Compute current episode state from file paths.

        The state is determined by checking which paths are set, from most
        progressed to least progressed. This ensures we always return the
        furthest state reached.

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
    title: str
    description: str
    rss_url: HttpUrl
    last_processed: Optional[datetime] = None
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
    episode_guid: str
    cleaned_transcript: str
    summary: str
    quotes: List[Quote]
    ad_segments: List[dict] = []
    processing_time: float
    created_at: datetime


class CleanedTranscript(BaseModel):
    episode_guid: str
    episode_title: str
    podcast_title: str
    corrections: List[dict] = []
    speaker_mapping: dict = {}
    cleaned_markdown: str
    processing_time: float
    created_at: datetime
