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

import os
from pathlib import Path
from typing import Dict, Optional

import structlog
from dotenv import load_dotenv
from pydantic import BaseModel, Field
from pydantic.config import ConfigDict

from .path_manager import PathManager

logger = structlog.get_logger(__name__)


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
    elevenlabs_base_url: str = ""  # Override API base URL (for ElevenLabs-compatible servers like Dalston)
    elevenlabs_model: str = "scribe_v1"  # scribe_v1 or scribe_v1_experimental
    elevenlabs_webhook_secret: str = ""  # HMAC secret for webhook signature verification
    elevenlabs_webhook_require_metadata: bool = True  # Require episode_id in webhook callbacks
    elevenlabs_async_threshold_mb: int = 0  # Use async mode for files > N MB (0 = always async)
    webhook_server_port: int = 8000  # Port for background webhook server during transcription

    # Dalston Configuration (self-hosted transcription server)
    dalston_base_url: str = ""  # Dalston server URL (e.g., http://localhost:8000)
    dalston_api_key: str = ""  # Optional API key for Dalston authentication
    dalston_model: str = ""  # Transcription model/engine (e.g., whisper-large-v3)

    # Storage Paths
    storage_path: Path = Path("./data")
    database_path: str = ""  # SQLite database path (default: storage_path/podcasts.db)

    # Path Manager (initialized after model creation)
    # All path operations should use path_manager methods instead of direct path attributes
    path_manager: Optional[PathManager] = Field(default=None, exclude=True)

    # Processing Configuration
    max_workers: int = 3
    parallel_jobs: int = 1  # Default per-stage capacity; stages below override it individually.
    # Per-stage worker capacity. Each TaskStage is polled by its own loop with
    # its own semaphore, so tuning these lets independent hosts run in
    # parallel (e.g. transcribe on Dalston while clean hits Gemini).
    # A value of 0 or missing falls back to ``parallel_jobs``.
    download_parallel_jobs: Optional[int] = None
    downsample_parallel_jobs: Optional[int] = None
    transcribe_parallel_jobs: Optional[int] = None
    clean_parallel_jobs: Optional[int] = None
    summarize_parallel_jobs: Optional[int] = None
    # Spec #28 entity-branch stages (run async off ``clean``).
    extract_entities_parallel_jobs: Optional[int] = None
    resolve_entities_parallel_jobs: Optional[int] = None
    write_corpus_parallel_jobs: Optional[int] = None
    reindex_parallel_jobs: Optional[int] = None
    chunk_duration_minutes: int = 30
    max_episodes_per_podcast: Optional[int] = None  # Limit episodes per podcast during discovery

    # Refresh Configuration (spec #19)
    # 1 = serial (historical behavior); raise to enable ThreadPoolExecutor over feeds.
    refresh_max_workers: int = 1
    # Per-host concurrency cap so bursts don't hammer Megaphone/Libsyn/Transistor.
    refresh_max_per_host: int = 2

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

    # Digest Configuration
    digest_default_since_days: int = 7  # Default time window for digest (days)
    digest_default_max_episodes: int = 10  # Default max episodes per digest

    # Authentication Configuration
    multi_user: bool = False  # False = single-user (local), True = multi-user (hosted)
    google_client_id: str = ""  # Google OAuth client ID (required for multi-user)
    google_client_secret: str = ""  # Google OAuth client secret (required for multi-user)
    jwt_secret_key: str = ""  # Secret key for signing JWTs
    jwt_algorithm: str = "HS256"  # JWT signing algorithm
    jwt_expire_days: int = 30  # JWT token expiration in days

    # Deployment / Web Surface Configuration
    # "production" locks down cookies, docs endpoints, CORS and verbose errors.
    # "development" keeps the ergonomic defaults for local dev.
    environment: str = "production"  # production | development
    # Auth cookie transport. Defaults to True; only flip via COOKIE_SECURE=false
    # explicitly for local http dev.
    cookie_secure: bool = True
    # CORS origins. Comma-separated in env (ALLOWED_ORIGINS=https://a,https://b).
    # Empty list = no cross-origin access allowed.
    allowed_origins: list[str] = Field(default_factory=list)
    # IPs of reverse proxies we trust to set X-Forwarded-* headers. Anything
    # else is ignored, so an attacker-controlled Host header cannot influence
    # OAuth redirect construction. Comma-separated in env.
    trusted_proxies: list[str] = Field(default_factory=list)
    # Canonical public URL — used to build OAuth redirects when the request
    # does NOT come from a trusted proxy. Must include scheme.
    public_base_url: str = ""
    # OpenAPI docs / redoc. On in dev; on in prod only if explicitly enabled.
    enable_docs: bool = False
    # Hard cap on any user-triggered audio download. Attackers controlling an
    # RSS feed can otherwise point us at a 100 GB file. Default 2 GiB.
    max_audio_bytes: int = 2 * 1024 * 1024 * 1024
    # Request body cap for the webhook endpoint (bytes). Default 1 MiB.
    max_webhook_body_bytes: int = 1 * 1024 * 1024

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

    def get_parallel_jobs_per_stage(self) -> Dict["TaskStage", int]:  # noqa: F821
        """
        Resolve per-stage worker capacity.

        Stages without an explicit override fall back to ``parallel_jobs``.
        """
        # Imported lazily to avoid utils -> core coupling at module load.
        from ..core.queue_manager import TaskStage

        overrides = {
            TaskStage.DOWNLOAD: self.download_parallel_jobs,
            TaskStage.DOWNSAMPLE: self.downsample_parallel_jobs,
            TaskStage.TRANSCRIBE: self.transcribe_parallel_jobs,
            TaskStage.CLEAN: self.clean_parallel_jobs,
            TaskStage.SUMMARIZE: self.summarize_parallel_jobs,
            TaskStage.EXTRACT_ENTITIES: self.extract_entities_parallel_jobs,
            TaskStage.RESOLVE_ENTITIES: self.resolve_entities_parallel_jobs,
            TaskStage.WRITE_CORPUS: self.write_corpus_parallel_jobs,
            TaskStage.REINDEX: self.reindex_parallel_jobs,
        }
        return {stage: (value if value and value > 0 else self.parallel_jobs) for stage, value in overrides.items()}


def _find_dotenv_from_package() -> Optional[str]:
    """
    Walk upward from this module's location looking for a ``.env``.

    In editable installs (``pip install -e .``) this finds the
    repo-root ``.env`` even when the process was launched from an
    unrelated CWD (e.g. an MCP client spawning ``thestill-mcp`` with
    CWD=``$HOME``).

    Using ``__file__`` directly rather than ``dotenv.find_dotenv`` —
    find_dotenv walks the call stack, which misbehaves under ``python
    -c`` and frozen interpreters where the top frame's filename is
    synthetic. Walking ``__file__`` gives deterministic behaviour.
    """
    current = Path(__file__).resolve().parent
    for candidate_dir in (current, *current.parents):
        candidate = candidate_dir / ".env"
        if candidate.is_file():
            return str(candidate)
    return None


def _find_dotenv_from_cwd() -> Optional[str]:
    """Walk upward from the current working directory looking for ``.env``."""
    try:
        current = Path.cwd().resolve()
    except (FileNotFoundError, OSError):
        return None
    for candidate_dir in (current, *current.parents):
        candidate = candidate_dir / ".env"
        if candidate.is_file():
            return str(candidate)
    return None


def _resolve_env_file(explicit: Optional[str]) -> Optional[str]:
    """
    Resolve which ``.env`` to load, in this priority order:

    1. Explicit ``env_file`` argument (CLI ``--config`` flag).
    2. ``THESTILL_ENV_FILE`` environment variable. Escape hatch for
       launchers whose CWD is unpredictable — notably MCP clients like
       Claude Desktop, which typically spawn servers with CWD=``$HOME``
       rather than the project root.
    3. Walk upward from this module's location (catches editable
       installs where the .env sits at the repo root).
    4. Walk upward from the CWD (matches the historical default for
       interactive CLI use from the repo root).

    Returns the resolved path (for logging), or ``None`` when no
    ``.env`` was found and only actual environment variables apply.
    """
    if explicit:
        return explicit

    from_env_var = os.getenv("THESTILL_ENV_FILE")
    if from_env_var:
        return from_env_var

    return _find_dotenv_from_package() or _find_dotenv_from_cwd()


def load_config(env_file: Optional[str] = None) -> Config:
    """Load configuration from environment variables and .env file"""
    resolved_env_file = _resolve_env_file(env_file)
    if resolved_env_file:
        load_dotenv(resolved_env_file)
        logger.info("config_env_file_loaded", path=resolved_env_file)
    else:
        logger.info("config_env_file_not_found", note="using process environment only")

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
        "elevenlabs_base_url": os.getenv("ELEVENLABS_BASE_URL", ""),
        "elevenlabs_model": os.getenv("ELEVENLABS_MODEL", "scribe_v1"),
        "elevenlabs_webhook_secret": os.getenv("ELEVENLABS_WEBHOOK_SECRET", ""),
        "elevenlabs_webhook_require_metadata": os.getenv("ELEVENLABS_WEBHOOK_REQUIRE_METADATA", "true").lower()
        == "true",
        "webhook_server_port": int(os.getenv("WEBHOOK_SERVER_PORT", "8000")),
        "elevenlabs_async_threshold_mb": int(os.getenv("ELEVENLABS_ASYNC_THRESHOLD_MB", "0")),
        # Dalston
        "dalston_base_url": os.getenv("DALSTON_BASE_URL", ""),
        "dalston_api_key": os.getenv("DALSTON_API_KEY", ""),
        "dalston_model": os.getenv("DALSTON_MODEL", ""),
        "storage_path": storage_path,
        "database_path": database_path,
        # Note: Path operations should use config.path_manager methods
        # Removed: audio_path, downsampled_audio_path, raw_transcripts_path,
        # clean_transcripts_path, summaries_path, evaluations_path
        "max_workers": int(os.getenv("MAX_WORKERS", "3")),
        "parallel_jobs": int(os.getenv("PARALLEL_JOBS", "1")),
        **{
            # Spec #28: TaskStage values use hyphens (e.g. "extract-entities"),
            # but Python field names and env var keys use underscores. The
            # ``replace`` keeps both halves consistent without a separate
            # mapping table.
            f"{stage.replace('-', '_')}_parallel_jobs": (
                int(os.getenv(f"{stage.replace('-', '_').upper()}_PARALLEL_JOBS")) or None
            )
            for stage in (
                "download",
                "downsample",
                "transcribe",
                "clean",
                "summarize",
                "extract-entities",
                "resolve-entities",
                "write-corpus",
                "reindex",
            )
            if os.getenv(f"{stage.replace('-', '_').upper()}_PARALLEL_JOBS")
        },
        "chunk_duration_minutes": int(os.getenv("CHUNK_DURATION_MINUTES", "30")),
        "max_episodes_per_podcast": (
            int(os.getenv("MAX_EPISODES_PER_PODCAST")) if os.getenv("MAX_EPISODES_PER_PODCAST") else None
        ),
        "refresh_max_workers": int(os.getenv("REFRESH_MAX_WORKERS", "1")),
        "refresh_max_per_host": int(os.getenv("REFRESH_MAX_PER_HOST", "2")),
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
        # Digest
        "digest_default_since_days": int(os.getenv("DIGEST_DEFAULT_SINCE_DAYS", "7")),
        "digest_default_max_episodes": int(os.getenv("DIGEST_DEFAULT_MAX_EPISODES", "10")),
        # Authentication
        "multi_user": os.getenv("MULTI_USER", "false").lower() == "true",
        "google_client_id": os.getenv("GOOGLE_CLIENT_ID", ""),
        "google_client_secret": os.getenv("GOOGLE_CLIENT_SECRET", ""),
        "jwt_secret_key": os.getenv("JWT_SECRET_KEY", ""),
        "jwt_algorithm": os.getenv("JWT_ALGORITHM", "HS256"),
        "jwt_expire_days": int(os.getenv("JWT_EXPIRE_DAYS", "30")),
        # Deployment / Web Surface
        "environment": os.getenv("ENVIRONMENT", "production").lower(),
        "cookie_secure": os.getenv("COOKIE_SECURE", "true").lower() == "true",
        "allowed_origins": [origin.strip() for origin in os.getenv("ALLOWED_ORIGINS", "").split(",") if origin.strip()],
        "trusted_proxies": [proxy.strip() for proxy in os.getenv("TRUSTED_PROXIES", "").split(",") if proxy.strip()],
        "public_base_url": os.getenv("PUBLIC_BASE_URL", "").rstrip("/"),
        "enable_docs": os.getenv("ENABLE_DOCS", "false").lower() == "true",
        "max_audio_bytes": int(os.getenv("MAX_AUDIO_BYTES", str(2 * 1024 * 1024 * 1024))),
        "max_webhook_body_bytes": int(os.getenv("MAX_WEBHOOK_BODY_BYTES", str(1 * 1024 * 1024))),
    }

    # Production must not emit
    # non-secure auth cookies. The footgun is COOKIE_SECURE=false slipping
    # through from a shared .env — refuse it loudly. Development can still
    # opt out.
    if config_data["environment"] == "production" and not config_data["cookie_secure"]:
        raise ValueError(
            "COOKIE_SECURE=false is not permitted when ENVIRONMENT=production. "
            "Set COOKIE_SECURE=true (the default) or switch ENVIRONMENT=development."
        )

    # Multi-user mode runs OAuth, which must build a non-spoofable
    # callback URL. Require PUBLIC_BASE_URL unconditionally — TRUSTED_PROXIES
    # alone is not sufficient, because a misconfigured proxy that omits
    # X-Forwarded-Host would silently let the attacker-controllable Host
    # header re-enter the callback. PUBLIC_BASE_URL is the operator-declared
    # ground truth; forwarded headers may override it per-request, but the
    # baseline must always exist.
    if config_data.get("multi_user") and not config_data["public_base_url"]:
        raise ValueError(
            "MULTI_USER=true requires PUBLIC_BASE_URL. Without it the OAuth "
            "redirect URI could be derived from the attacker-controllable "
            "Host header when trusted proxies omit X-Forwarded-Host."
        )

    return Config(**config_data)


def get_default_config_path() -> Path:
    """Get the default configuration file path"""
    return Path.cwd() / ".env"
