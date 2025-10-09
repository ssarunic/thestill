from datetime import datetime
from typing import List, Optional
from pydantic import BaseModel, HttpUrl


class Episode(BaseModel):
    title: str
    description: str
    pub_date: Optional[datetime] = None
    audio_url: HttpUrl
    duration: Optional[str] = None
    guid: str
    processed: bool = False
    audio_path: Optional[str] = None              # Filename of the downloaded audio file
    raw_transcript_path: Optional[str] = None     # Filename of the raw transcript JSON (Whisper output)
    clean_transcript_path: Optional[str] = None   # Filename of the cleaned transcript MD (corrected, formatted)
    summary_path: Optional[str] = None            # Filename of the summary (future use)


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