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

import os
from pathlib import Path
from typing import Optional

from dotenv import load_dotenv
from pydantic import BaseModel, Field
from pydantic.config import ConfigDict

from .path_manager import PathManager


class Config(BaseModel):
    model_config = ConfigDict(arbitrary_types_allowed=True)

    # API Configuration
    openai_api_key: str = ""
    gemini_api_key: str = ""
    anthropic_api_key: str = ""

    # Google Cloud Configuration (for Google Speech-to-Text)
    google_app_credentials: str = ""
    google_cloud_project_id: str = ""
    google_storage_bucket: str = ""

    # ElevenLabs Configuration (for ElevenLabs Speech-to-Text)
    elevenlabs_api_key: str = ""
    elevenlabs_model: str = "scribe_v1"  # scribe_v1 or scribe_v1_experimental
    elevenlabs_webhook_secret: str = ""  # HMAC secret for webhook signature verification
    elevenlabs_webhook_require_metadata: bool = True  # Require episode_id in webhook callbacks
    elevenlabs_async_threshold_mb: int = 0  # Use async mode for files > N MB (0 = always async)
    webhook_server_port: int = 8000  # Port for background webhook server during transcription

    # Storage Paths
    storage_path: Path = Path("./data")
    database_path: str = ""  # SQLite database path (default: storage_path/podcasts.db)

    # Path Manager (initialized after model creation)
    # All path operations should use path_manager methods instead of direct path attributes
    path_manager: Optional[PathManager] = Field(default=None, exclude=True)

    # Processing Configuration
    max_workers: int = 3
    chunk_duration_minutes: int = 30
    max_episodes_per_podcast: Optional[int] = None  # Limit episodes per podcast during discovery

    # Transcription Configuration
    transcription_provider: str = "whisper"  # whisper, parakeet, google, or elevenlabs
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

    # OpenAI Configuration
    openai_model: str = "gpt-5.2"
    openai_reasoning_effort: Optional[str] = None  # Reasoning effort for GPT-5.x models (none/low/medium/high/xhigh)

    # Ollama Configuration
    ollama_base_url: str = "http://localhost:11434"
    ollama_model: str = "gemma3:4b"

    # Gemini Configuration
    gemini_model: str = "gemini-3-pro-preview"
    gemini_thinking_level: Optional[str] = (
        None  # Thinking level for Gemini 3 models (low/high for Pro, minimal/low/medium/high for Flash)
    )

    # Anthropic Configuration
    anthropic_model: str = "claude-sonnet-4-5-20250929"

    # Mistral Configuration
    mistral_api_key: str = ""
    mistral_model: str = "mistral-large-latest"

    # Transcript Cleaning Configuration (legacy - used during transcription step only)
    enable_transcript_cleaning: bool = False  # Enable LLM-based transcript cleaning
    cleaning_provider: str = "gemini"  # Provider for cleaning (openai, ollama, gemini, or anthropic)
    cleaning_model: str = "gemini-3-flash-preview"  # Model for cleaning (Gemini 3 Flash is fast and cost-effective)
    cleaning_chunk_size: int = 20000  # Max tokens per chunk
    cleaning_overlap_pct: float = 0.15  # Overlap percentage (0.10 = 10%)
    cleaning_extract_entities: bool = True  # Extract entities for consistency

    # Cleanup Configuration
    cleanup_days: int = 30
    delete_audio_after_processing: bool = False  # Delete audio files after each pipeline stage completes

    # Debug/Testing Configuration
    debug_clip_duration: Optional[int] = None  # Clip audio to N seconds for testing

    def __init__(self, **kwargs):
        super().__init__(**kwargs)
        # Initialize PathManager for centralized path management
        self.path_manager = PathManager(str(self.storage_path))

        # Set default database path if not provided
        if not self.database_path:
            self.database_path = str(self.storage_path / "podcasts.db")

        self._ensure_directories()

    def _ensure_directories(self):
        """Create necessary directories if they don't exist"""
        # Use PathManager to ensure all directories exist
        self.path_manager.ensure_directories_exist()


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
    mistral_api_key = os.getenv("MISTRAL_API_KEY", "")

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
    if llm_provider == "mistral" and not mistral_api_key:
        raise ValueError(
            "MISTRAL_API_KEY environment variable is required when using Mistral provider. "
            "Please set it in your .env file or environment, or switch to another provider."
        )

    # Optional configurations with defaults
    # All paths derived from storage_path for cross-platform compatibility
    storage_path = Path(os.getenv("STORAGE_PATH", "./data"))
    database_path = os.getenv("DATABASE_PATH", "")  # Empty string = use default

    config_data = {
        "openai_api_key": openai_api_key,
        "gemini_api_key": gemini_api_key,
        "anthropic_api_key": anthropic_api_key,
        "google_app_credentials": os.getenv("GOOGLE_APP_CREDENTIALS", ""),
        "google_cloud_project_id": os.getenv("GOOGLE_CLOUD_PROJECT_ID", ""),
        "google_storage_bucket": os.getenv("GOOGLE_STORAGE_BUCKET", ""),
        "elevenlabs_api_key": os.getenv("ELEVENLABS_API_KEY", ""),
        "elevenlabs_model": os.getenv("ELEVENLABS_MODEL", "scribe_v1"),
        "elevenlabs_webhook_secret": os.getenv("ELEVENLABS_WEBHOOK_SECRET", ""),
        "elevenlabs_webhook_require_metadata": os.getenv("ELEVENLABS_WEBHOOK_REQUIRE_METADATA", "true").lower()
        == "true",
        "webhook_server_port": int(os.getenv("WEBHOOK_SERVER_PORT", "8000")),
        "elevenlabs_async_threshold_mb": int(os.getenv("ELEVENLABS_ASYNC_THRESHOLD_MB", "0")),
        "storage_path": storage_path,
        "database_path": database_path,
        # Note: Path operations should use config.path_manager methods
        # Removed: audio_path, downsampled_audio_path, raw_transcripts_path,
        # clean_transcripts_path, summaries_path, evaluations_path
        "max_workers": int(os.getenv("MAX_WORKERS", "3")),
        "chunk_duration_minutes": int(os.getenv("CHUNK_DURATION_MINUTES", "30")),
        "max_episodes_per_podcast": (
            int(os.getenv("MAX_EPISODES_PER_PODCAST")) if os.getenv("MAX_EPISODES_PER_PODCAST") else None
        ),
        "transcription_provider": os.getenv("TRANSCRIPTION_PROVIDER", "whisper"),
        "whisper_model": os.getenv("WHISPER_MODEL", "base"),
        "whisper_device": os.getenv("WHISPER_DEVICE", "auto"),
        "enable_diarization": os.getenv("ENABLE_DIARIZATION", "false").lower() == "true",
        "diarization_model": os.getenv("DIARIZATION_MODEL", "pyannote/speaker-diarization-3.1"),
        "huggingface_token": os.getenv("HUGGINGFACE_TOKEN", ""),
        "min_speakers": int(os.getenv("MIN_SPEAKERS")) if os.getenv("MIN_SPEAKERS") else None,
        "max_speakers": int(os.getenv("MAX_SPEAKERS")) if os.getenv("MAX_SPEAKERS") else None,
        "llm_provider": llm_provider,
        "openai_model": os.getenv("OPENAI_MODEL", "gpt-5.2"),
        "openai_reasoning_effort": os.getenv("OPENAI_REASONING_EFFORT")
        or None,  # none/low/medium/high/xhigh for GPT-5.x
        "ollama_base_url": os.getenv("OLLAMA_BASE_URL", "http://localhost:11434"),
        "ollama_model": os.getenv("OLLAMA_MODEL", "gemma3:4b"),
        "gemini_model": os.getenv("GEMINI_MODEL", "gemini-3-pro-preview"),
        "gemini_thinking_level": os.getenv("GEMINI_THINKING_LEVEL")
        or None,  # low/high for Pro, minimal/low/medium/high for Flash
        "anthropic_model": os.getenv("ANTHROPIC_MODEL", "claude-sonnet-4-5-20250929"),
        "mistral_api_key": mistral_api_key,
        "mistral_model": os.getenv("MISTRAL_MODEL", "mistral-large-latest"),
        "enable_transcript_cleaning": os.getenv("ENABLE_TRANSCRIPT_CLEANING", "false").lower() == "true",
        "cleaning_provider": os.getenv("CLEANING_PROVIDER", "gemini"),
        "cleaning_model": os.getenv("CLEANING_MODEL", "gemini-3-flash-preview"),
        "cleaning_chunk_size": int(os.getenv("CLEANING_CHUNK_SIZE", "20000")),
        "cleaning_overlap_pct": float(os.getenv("CLEANING_OVERLAP_PCT", "0.15")),
        "cleaning_extract_entities": os.getenv("CLEANING_EXTRACT_ENTITIES", "true").lower() == "true",
        "cleanup_days": int(os.getenv("CLEANUP_DAYS", "30")),
        "delete_audio_after_processing": os.getenv("DELETE_AUDIO_AFTER_PROCESSING", "false").lower() == "true",
        "debug_clip_duration": int(os.getenv("DEBUG_CLIP_DURATION")) if os.getenv("DEBUG_CLIP_DURATION") else None,
    }

    return Config(**config_data)


def get_default_config_path() -> Path:
    """Get the default configuration file path"""
    return Path.cwd() / ".env"
