import os
from pathlib import Path
from typing import Optional

from pydantic import BaseModel
from dotenv import load_dotenv


class Config(BaseModel):
    # API Configuration
    openai_api_key: str = ""
    gemini_api_key: str = ""
    anthropic_api_key: str = ""

    # Storage Paths
    storage_path: Path = Path("./data")
    audio_path: Path = Path("./data/audio")
    raw_transcripts_path: Path = Path("./data/raw_transcripts")  # Raw Whisper JSON transcripts
    clean_transcripts_path: Path = Path("./data/clean_transcripts")  # Cleaned/formatted transcripts
    summaries_path: Path = Path("./data/summaries")
    evaluations_path: Path = Path("./data/evaluations")

    # Processing Configuration
    max_workers: int = 3
    chunk_duration_minutes: int = 30

    # Transcription Configuration
    transcription_model: str = "whisper"  # whisper or parakeet
    whisper_model: str = "base"
    whisper_device: str = "auto"

    # Speaker Diarization Configuration
    enable_diarization: bool = False
    diarization_model: str = "pyannote/speaker-diarization-3.1"
    huggingface_token: str = ""
    min_speakers: Optional[int] = None
    max_speakers: Optional[int] = None

    # LLM Configuration
    llm_provider: str = "openai"  # openai, ollama, gemini, or anthropic
    llm_model: str = "gpt-4o"

    # Ollama Configuration
    ollama_base_url: str = "http://localhost:11434"
    ollama_model: str = "gemma3:4b"

    # Gemini Configuration
    gemini_model: str = "gemini-2.0-flash-exp"

    # Anthropic Configuration
    anthropic_model: str = "claude-3-5-sonnet-20241022"

    # Transcript Cleaning Configuration
    enable_transcript_cleaning: bool = False  # Enable LLM-based transcript cleaning
    cleaning_provider: str = "ollama"  # Provider for cleaning (openai, ollama, gemini, or anthropic)
    cleaning_model: str = "gemma3:4b"  # Model for cleaning (small models recommended)
    cleaning_chunk_size: int = 20000  # Max tokens per chunk
    cleaning_overlap_pct: float = 0.15  # Overlap percentage (0.10 = 10%)
    cleaning_extract_entities: bool = True  # Extract entities for consistency

    # Cleanup Configuration
    cleanup_days: int = 30

    # Debug/Testing Configuration
    debug_clip_duration: Optional[int] = None  # Clip audio to N seconds for testing

    def __init__(self, **kwargs):
        super().__init__(**kwargs)
        self._ensure_directories()

    def _ensure_directories(self):
        """Create necessary directories if they don't exist"""
        directories = [
            self.storage_path,
            self.audio_path,
            self.raw_transcripts_path,
            self.clean_transcripts_path,
            self.summaries_path,
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

    # Get LLM provider
    llm_provider = os.getenv("LLM_PROVIDER", "openai").lower()

    # Validate required API keys based on provider
    openai_api_key = os.getenv("OPENAI_API_KEY", "")
    gemini_api_key = os.getenv("GEMINI_API_KEY", "")
    anthropic_api_key = os.getenv("ANTHROPIC_API_KEY", "")

    if llm_provider == "openai" and not openai_api_key:
        raise ValueError(
            "OPENAI_API_KEY environment variable is required when using OpenAI provider. "
            "Please set it in your .env file or environment, or switch to another provider."
        )
    if llm_provider == "gemini" and not gemini_api_key:
        raise ValueError(
            "GEMINI_API_KEY environment variable is required when using Gemini provider. "
            "Please set it in your .env file or environment, or switch to another provider."
        )
    if llm_provider == "anthropic" and not anthropic_api_key:
        raise ValueError(
            "ANTHROPIC_API_KEY environment variable is required when using Anthropic provider. "
            "Please set it in your .env file or environment, or switch to another provider."
        )

    # Optional configurations with defaults
    # All paths derived from storage_path for cross-platform compatibility
    storage_path = Path(os.getenv("STORAGE_PATH", "./data"))

    config_data = {
        "openai_api_key": openai_api_key,
        "gemini_api_key": gemini_api_key,
        "anthropic_api_key": anthropic_api_key,
        "storage_path": storage_path,
        "audio_path": storage_path / "audio",
        "raw_transcripts_path": storage_path / "raw_transcripts",
        "clean_transcripts_path": storage_path / "clean_transcripts",
        "summaries_path": storage_path / "summaries",
        "evaluations_path": storage_path / "evaluations",
        "max_workers": int(os.getenv("MAX_WORKERS", "3")),
        "chunk_duration_minutes": int(os.getenv("CHUNK_DURATION_MINUTES", "30")),
        "transcription_model": os.getenv("TRANSCRIPTION_MODEL", "whisper"),
        "whisper_model": os.getenv("WHISPER_MODEL", "base"),
        "whisper_device": os.getenv("WHISPER_DEVICE", "auto"),
        "enable_diarization": os.getenv("ENABLE_DIARIZATION", "false").lower() == "true",
        "diarization_model": os.getenv("DIARIZATION_MODEL", "pyannote/speaker-diarization-3.1"),
        "huggingface_token": os.getenv("HUGGINGFACE_TOKEN", ""),
        "min_speakers": int(os.getenv("MIN_SPEAKERS")) if os.getenv("MIN_SPEAKERS") else None,
        "max_speakers": int(os.getenv("MAX_SPEAKERS")) if os.getenv("MAX_SPEAKERS") else None,
        "llm_provider": llm_provider,
        "llm_model": os.getenv("LLM_MODEL", "gpt-4o" if llm_provider == "openai" else ("gemini-2.0-flash-exp" if llm_provider == "gemini" else ("claude-3-5-sonnet-20241022" if llm_provider == "anthropic" else "gemma3:4b"))),
        "ollama_base_url": os.getenv("OLLAMA_BASE_URL", "http://localhost:11434"),
        "ollama_model": os.getenv("OLLAMA_MODEL", "gemma3:4b"),
        "gemini_model": os.getenv("GEMINI_MODEL", "gemini-2.0-flash-exp"),
        "anthropic_model": os.getenv("ANTHROPIC_MODEL", "claude-3-5-sonnet-20241022"),
        "enable_transcript_cleaning": os.getenv("ENABLE_TRANSCRIPT_CLEANING", "false").lower() == "true",
        "cleaning_provider": os.getenv("CLEANING_PROVIDER", "ollama"),
        "cleaning_model": os.getenv("CLEANING_MODEL", "gemma3:4b"),
        "cleaning_chunk_size": int(os.getenv("CLEANING_CHUNK_SIZE", "20000")),
        "cleaning_overlap_pct": float(os.getenv("CLEANING_OVERLAP_PCT", "0.15")),
        "cleaning_extract_entities": os.getenv("CLEANING_EXTRACT_ENTITIES", "true").lower() == "true",
        "cleanup_days": int(os.getenv("CLEANUP_DAYS", "30")),
        "debug_clip_duration": int(os.getenv("DEBUG_CLIP_DURATION")) if os.getenv("DEBUG_CLIP_DURATION") else None
    }

    return Config(**config_data)


def get_default_config_path() -> Path:
    """Get the default configuration file path"""
    return Path.cwd() / ".env"