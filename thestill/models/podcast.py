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
    transcript_path: Optional[str] = None
    summary_path: Optional[str] = None


class Podcast(BaseModel):
    title: str
    description: str
    rss_url: HttpUrl
    last_processed: Optional[datetime] = None
    episodes: List[Episode] = []


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