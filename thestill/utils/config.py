import os
from pathlib import Path
from typing import Optional

from pydantic import BaseModel
from dotenv import load_dotenv


class Config(BaseModel):
    # API Configuration
    openai_api_key: str

    # Storage Paths
    storage_path: Path = Path("./data")
    audio_path: Path = Path("./data/audio")
    transcripts_path: Path = Path("./data/transcripts")
    summaries_path: Path = Path("./data/summaries")
    processed_path: Path = Path("./data/processed")
    evaluations_path: Path = Path("./data/evaluations")

    # Processing Configuration
    max_workers: int = 3
    chunk_duration_minutes: int = 30

    # Transcription Configuration
    transcription_model: str = "whisper"  # whisper or parakeet
    whisper_model: str = "base"
    whisper_device: str = "auto"

    # LLM Configuration
    llm_model: str = "gpt-4o"

    # Cleanup Configuration
    cleanup_days: int = 30

    def __init__(self, **kwargs):
        super().__init__(**kwargs)
        self._ensure_directories()

    def _ensure_directories(self):
        """Create necessary directories if they don't exist"""
        directories = [
            self.storage_path,
            self.audio_path,
            self.transcripts_path,
            self.summaries_path,
            self.processed_path,
            self.evaluations_path
        ]

        for directory in directories:
            directory.mkdir(parents=True, exist_ok=True)


def load_config(env_file: Optional[str] = None) -> Config:
    """Load configuration from environment variables and .env file"""
    if env_file:
        load_dotenv(env_file)
    else:
        load_dotenv()

    # Required environment variable
    api_key = os.getenv("OPENAI_API_KEY")
    if not api_key:
        raise ValueError(
            "OPENAI_API_KEY environment variable is required. "
            "Please set it in your .env file or environment."
        )

    # Optional configurations with defaults
    config_data = {
        "openai_api_key": api_key,
        "storage_path": Path(os.getenv("STORAGE_PATH", "./data")),
        "audio_path": Path(os.getenv("AUDIO_PATH", "./data/audio")),
        "transcripts_path": Path(os.getenv("TRANSCRIPTS_PATH", "./data/transcripts")),
        "summaries_path": Path(os.getenv("SUMMARIES_PATH", "./data/summaries")),
        "processed_path": Path(os.getenv("PROCESSED_PATH", "./data/processed")),
        "evaluations_path": Path(os.getenv("EVALUATIONS_PATH", "./data/evaluations")),
        "max_workers": int(os.getenv("MAX_WORKERS", "3")),
        "chunk_duration_minutes": int(os.getenv("CHUNK_DURATION_MINUTES", "30")),
        "transcription_model": os.getenv("TRANSCRIPTION_MODEL", "whisper"),
        "whisper_model": os.getenv("WHISPER_MODEL", "base"),
        "whisper_device": os.getenv("WHISPER_DEVICE", "auto"),
        "llm_model": os.getenv("LLM_MODEL", "gpt-4o"),
        "cleanup_days": int(os.getenv("CLEANUP_DAYS", "30"))
    }

    return Config(**config_data)


def get_default_config_path() -> Path:
    """Get the default configuration file path"""
    return Path.cwd() / ".env"